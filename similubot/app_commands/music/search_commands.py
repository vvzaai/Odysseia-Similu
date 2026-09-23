"""
音乐搜索命令实现

处理音乐搜索和添加相关的Slash命令：
- NetEase音乐搜索
- URL检测和处理
- 交互式歌曲选择
- 队列公平性检查
"""

import logging
from typing import Any, Optional
import discord
from discord import app_commands

from ..core.base_command import BaseSlashCommand
from ..ui.message_visibility import MessageVisibility, MessageType
from similubot.utils.config_manager import ConfigManager
from similubot.utils.netease_search import search_songs, get_playback_url
from similubot.core.interfaces import AudioInfo, NetEaseSearchResult
from similubot.progress.discord_updater import DiscordProgressUpdater
from similubot.queue.user_queue_status import UserQueueStatusService


class MusicSearchCommands(BaseSlashCommand):
    """
    音乐搜索命令处理器

    负责处理音乐搜索、URL检测和歌曲添加功能
    """

    def __init__(self, config: ConfigManager, music_player: Any):
        """
        初始化音乐搜索命令

        Args:
            config: 配置管理器
            music_player: 音乐播放器实例
        """
        super().__init__(config, music_player)

        # 初始化消息可见性控制器
        self.message_visibility = MessageVisibility()

        # 初始化交互管理器
        from similubot.ui.button_interactions import InteractionManager
        self.interaction_manager = InteractionManager()

        self.logger.debug("音乐搜索命令已初始化")

    async def execute(self, interaction: discord.Interaction, **kwargs) -> None:
        """
        执行音乐搜索命令

        Args:
            interaction: Discord交互对象
            **kwargs: 命令参数，应包含 'query' 参数
        """
        query = kwargs.get('query', '')
        if not query:
            await interaction.response.send_message(
                "❌ 请提供搜索关键词或音乐链接",
                ephemeral=True
            )
            return

        await self.handle_song_request(interaction, query)

    async def handle_song_request(self, interaction: discord.Interaction, query: str) -> None:
        """
        处理点歌请求

        Args:
            interaction: Discord交互对象
            query: 搜索查询或URL
        """
        try:
            # 检查前置条件
            if not await self.check_prerequisites(interaction):
                return

            if not await self.check_voice_channel(interaction):
                return

            self.logger.debug(f"处理点歌请求 - 用户: {interaction.user.display_name}, 查询: {query}")

            # 立即 defer，占用 interaction token（15分钟有效）。
            # 语音握手等耗时操作可能超过 Discord 3 秒初始响应窗口，
            # 不 defer 会导致后续响应抛 404，用户看到"应用未响应"但 bot 已进频道
            await interaction.response.defer(ephemeral=True)

            # 连接到用户的语音频道
            success, error = await self.music_player.connect_to_user_channel(interaction.user)
            if not success:
                await self.send_error_response(interaction, f"无法连接到语音频道: {error}")
                return

            # 设置文本频道用于事件通知
            if hasattr(self.music_player, '_playback_engine') and interaction.guild:
                self.music_player._playback_engine.set_text_channel(
                    interaction.guild.id,
                    interaction.channel.id
                )
                self.logger.debug(f"设置服务器 {interaction.guild.id} 的文本频道为 {interaction.channel.id}")

            # 检查是否为支持的URL
            if self.music_player.is_supported_url(query):
                await self._handle_url_request(interaction, query)
            else:
                # 默认行为：NetEase搜索
                await self._handle_netease_search(interaction, query)

        except Exception as e:
            self.logger.error(f"处理点歌请求失败: {e}", exc_info=True)
            await self.handle_command_error(interaction, e)

    async def _handle_url_request(self, interaction: discord.Interaction, url: str) -> None:
        """
        处理URL点歌请求

        Args:
            interaction: Discord交互对象
            url: 音频URL
        """
        try:
            # 检测音频源类型
            source_type = self.music_player.detect_audio_source_type(url)
            source_name = source_type.value.title() if source_type else "Audio"

            # 发送初始响应
            embed = discord.Embed(
                title="🔄 处理中...",
                description=f"正在处理 {source_name} 链接...",
                color=discord.Color.blue()
            )
            # handle_song_request 已 defer，此处走 followup
            await interaction.followup.send(embed=embed, ephemeral=True)

            # 创建进度更新器
            progress_updater = DiscordProgressUpdater(interaction)
            progress_callback = progress_updater.create_callback()

            # 添加歌曲到队列
            success, position, error = await self.music_player.add_song_to_queue(
                url, interaction.user, progress_callback
            )

            if not success:
                # 检查是否为队列公平性错误
                if error and ("已经有" in error and "首歌曲在队列中" in error):
                    await self._handle_queue_fairness_error(interaction, url, source_type)
                    return

                # 其他错误
                await self._send_queue_error_response(interaction, error)
                return

            # 获取音频信息
            audio_info = await self._get_audio_info_by_source(url, source_type)
            if not audio_info:
                await self.send_error_response(interaction, "获取歌曲信息失败")
                return

            # 发送成功响应
            await self._send_song_added_response(interaction, audio_info, position)

        except Exception as e:
            self.logger.error(f"处理URL请求失败: {e}", exc_info=True)
            await self.handle_command_error(interaction, e)

    async def _handle_netease_search(self, interaction: discord.Interaction, query: str) -> None:
        """
        处理NetEase音乐搜索

        Args:
            interaction: Discord交互对象
            query: 搜索关键词
        """
        try:
            self.logger.debug(f"NetEase搜索: {query}")

            # 发送搜索中的消息（ephemeral）
            # handle_song_request 已 defer，此处走 followup
            embed = discord.Embed(
                title="🔍 搜索中...",
                description=f"正在网易云音乐中搜索: **{query}**",
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            # 执行搜索
            search_results = await search_songs(query, limit=5)

            if not search_results:
                embed = discord.Embed(
                    title="❌ 未找到结果",
                    description=f"未找到与 **{query}** 相关的歌曲",
                    color=discord.Color.orange()
                )
                await interaction.edit_original_response(embed=embed)
                return

            # 显示搜索结果并处理用户选择
            await self._handle_search_results(interaction, search_results)

        except Exception as e:
            self.logger.error(f"NetEase搜索失败: {e}", exc_info=True)
            await self.handle_command_error(interaction, e)

    async def _handle_search_results(
        self,
        interaction: discord.Interaction,
        search_results: list[NetEaseSearchResult]
    ) -> None:
        """
        处理搜索结果的用户选择

        Args:
            interaction: Discord交互对象
            search_results: 搜索结果列表
        """
        try:
            # 显示第一个结果的确认界面
            first_result = search_results[0]

            # 创建确认消息
            embed = discord.Embed(
                title="🎵 找到歌曲",
                description="是否添加这首歌曲到队列？",
                color=discord.Color.blue()
            )

            embed.add_field(
                name="歌曲信息",
                value=f"**{first_result.get_display_name()}**\n专辑: {first_result.album}",
                inline=False
            )

            if first_result.duration:
                embed.add_field(
                    name="时长",
                    value=first_result.format_duration(),
                    inline=True
                )

            if first_result.cover_url:
                embed.set_thumbnail(url=first_result.cover_url)

            embed.set_footer(text=f"请在60秒内选择 • 只有 {interaction.user.display_name} 可以操作此界面")

            # 使用交互管理器处理用户选择
            from similubot.ui.button_interactions import InteractionResult

            # 创建临时的Context对象用于兼容现有的交互管理器
            class TempContext:
                def __init__(self, interaction):
                    self.author = interaction.user
                    self.send = interaction.followup.send

            temp_ctx = TempContext(interaction)

            # 显示确认界面
            interaction_result, selected_result = await self.interaction_manager.show_search_confirmation(
                temp_ctx, first_result, timeout=60.0
            )

            if interaction_result == InteractionResult.CONFIRMED and selected_result:
                # 用户确认了第一个结果
                await self._add_netease_song_to_queue(interaction, selected_result)
            elif interaction_result == InteractionResult.DENIED:
                # 用户拒绝了第一个结果，显示更多选择
                if len(search_results) > 1:
                    interaction_result, selected_result = await self.interaction_manager.show_search_selection(
                        temp_ctx, search_results, timeout=60.0
                    )

                    if interaction_result == InteractionResult.SELECTED and selected_result:
                        await self._add_netease_song_to_queue(interaction, selected_result)
                    elif interaction_result == InteractionResult.CANCELLED:
                        self.logger.debug(f"用户 {interaction.user.display_name} 取消了搜索选择")
                    elif interaction_result == InteractionResult.TIMEOUT:
                        self.logger.debug(f"用户 {interaction.user.display_name} 的搜索选择超时")
                else:
                    # 只有一个结果但被拒绝
                    embed = discord.Embed(
                        title="❌ 已取消",
                        description="搜索已取消",
                        color=discord.Color.light_grey()
                    )
                    await interaction.edit_original_response(embed=embed)
            elif interaction_result == InteractionResult.TIMEOUT:
                self.logger.debug(f"用户 {interaction.user.display_name} 的搜索确认超时")

        except Exception as e:
            self.logger.error(f"处理搜索结果失败: {e}", exc_info=True)
            await self.handle_command_error(interaction, e)

    async def _add_netease_song_to_queue(
        self,
        interaction: discord.Interaction,
        search_result: NetEaseSearchResult
    ) -> None:
        """
        将NetEase歌曲添加到队列

        Args:
            interaction: Discord交互对象
            search_result: 搜索结果
        """
        try:
            # 构建播放URL
            playback_url = get_playback_url(search_result.song_id, use_api=True)

            self.logger.debug(f"添加NetEase歌曲: {search_result.get_display_name()} - URL: {playback_url}")

            # 创建进度更新器
            progress_updater = DiscordProgressUpdater(interaction)
            progress_callback = progress_updater.create_callback()

            # 添加歌曲到队列
            success, position, error = await self.music_player.add_song_to_queue(
                playback_url, interaction.user, progress_callback
            )

            if not success:
                # 检查是否为队列公平性错误
                if error and ("已经有" in error and "首歌曲在队列中" in error):
                    await self._handle_netease_queue_fairness_error(interaction, search_result)
                    return

                # 其他错误
                await self._send_queue_error_response(interaction, error)
                return

            # 发送成功响应
            embed = discord.Embed(
                title="🎵 歌曲已添加到队列",
                color=discord.Color.green()
            )

            embed.add_field(
                name="歌曲信息",
                value=f"**{search_result.get_display_name()}**\n专辑: {search_result.album}",
                inline=False
            )

            if search_result.duration:
                embed.add_field(
                    name="时长",
                    value=search_result.format_duration(),
                    inline=True
                )

            embed.add_field(
                name="队列位置",
                value=f"#{position}",
                inline=True
            )

            embed.add_field(
                name="点歌人",
                value=interaction.user.display_name,
                inline=True
            )

            if search_result.cover_url:
                embed.set_thumbnail(url=search_result.cover_url)

            # 这是成功添加歌曲的通知，应该是public消息
            await interaction.edit_original_response(embed=embed)

        except Exception as e:
            self.logger.error(f"添加NetEase歌曲失败: {e}", exc_info=True)
            await self.handle_command_error(interaction, e)

    async def _get_audio_info_by_source(self, url: str, source_type) -> Optional[AudioInfo]:
        """
        根据音频源类型获取音频信息

        Args:
            url: 音频URL
            source_type: 音频源类型

        Returns:
            音频信息或None
        """
        try:
            if source_type and source_type.value == "youtube":
                return await self.music_player.youtube_client.extract_audio_info(url)
            elif source_type and source_type.value == "catbox":
                return await self.music_player.catbox_client.extract_audio_info(url)
            elif source_type and source_type.value == "bilibili":
                return await self.music_player.bilibili_client.extract_audio_info(url)
            elif source_type and source_type.value == "soundcloud":
                return await self.music_player.soundcloud_client.extract_audio_info(url)
            elif source_type and source_type.value == "netease":
                return await self.music_player.netease_client.extract_audio_info(url)

            return None

        except Exception as e:
            self.logger.error(f"获取音频信息失败: {e}")
            return None

    async def _send_song_added_response(
        self,
        interaction: discord.Interaction,
        audio_info: AudioInfo,
        position: int
    ) -> None:
        """
        发送歌曲添加成功响应

        Args:
            interaction: Discord交互对象
            audio_info: 音频信息
            position: 队列位置
        """
        embed = discord.Embed(
            title="🎵 歌曲已添加到队列",
            color=discord.Color.green()
        )

        embed.add_field(
            name="歌曲标题",
            value=audio_info.title,
            inline=False
        )

        # 格式化时长
        if hasattr(audio_info, 'duration') and audio_info.duration > 0:
            duration_str = self._format_duration(audio_info.duration)
        else:
            duration_str = "未知"

        embed.add_field(
            name="时长",
            value=duration_str,
            inline=True
        )

        embed.add_field(
            name="来源",
            value=audio_info.uploader,
            inline=True
        )

        embed.add_field(
            name="队列位置",
            value=f"#{position}",
            inline=True
        )

        embed.add_field(
            name="点歌人",
            value=interaction.user.display_name,
            inline=True
        )

        if hasattr(audio_info, 'thumbnail_url') and audio_info.thumbnail_url:
            embed.set_thumbnail(url=audio_info.thumbnail_url)

        # 成功添加歌曲的通知应该是public消息
        await interaction.edit_original_response(embed=embed)

    async def _send_queue_error_response(self, interaction: discord.Interaction, error: str) -> None:
        """
        发送队列错误响应

        Args:
            interaction: Discord交互对象
            error: 错误消息
        """
        if "重复" in error or "duplicate" in error.lower():
            embed = discord.Embed(
                title="🔄 重复歌曲",
                description=error,
                color=discord.Color.orange()
            )
            embed.add_field(
                name="💡 提示",
                value="等待当前歌曲播放完成后，您就可以再次请求这首歌曲了。",
                inline=False
            )
        elif "时长" in error or "too long" in error.lower():
            embed = discord.Embed(
                title="⏱️ 歌曲时长超限",
                description=error,
                color=discord.Color.red()
            )
            embed.add_field(
                name="💡 建议",
                value="请尝试寻找该歌曲的较短版本，或选择其他歌曲。",
                inline=False
            )
        else:
            embed = discord.Embed(
                title="❌ 添加失败",
                description=error,
                color=discord.Color.red()
            )

        await interaction.edit_original_response(embed=embed)

    async def _handle_queue_fairness_error(
        self,
        interaction: discord.Interaction,
        url: str,
        source_type
    ) -> None:
        """
        处理队列公平性错误（URL版本）

        Args:
            interaction: Discord交互对象
            url: 音频URL
            source_type: 音频源类型
        """
        try:
            # 获取音频信息用于交互式替换
            audio_info = await self._get_audio_info_by_source(url, source_type)
            if audio_info:
                await self._handle_interactive_queue_fairness(interaction, audio_info)
            else:
                # 回退到基本错误处理
                await self._send_basic_queue_fairness_error(interaction)

        except Exception as e:
            self.logger.error(f"处理队列公平性错误失败: {e}")
            await self._send_basic_queue_fairness_error(interaction)

    async def _handle_netease_queue_fairness_error(
        self,
        interaction: discord.Interaction,
        search_result: NetEaseSearchResult
    ) -> None:
        """
        处理NetEase队列公平性错误

        Args:
            interaction: Discord交互对象
            search_result: 搜索结果
        """
        try:
            # 创建AudioInfo对象
            audio_info = AudioInfo(
                title=search_result.title,
                duration=search_result.duration or 0,
                url=get_playback_url(search_result.song_id, use_api=True),
                uploader=search_result.artist
            )

            await self._handle_interactive_queue_fairness(interaction, audio_info)

        except Exception as e:
            self.logger.error(f"处理NetEase队列公平性错误失败: {e}")
            await self._send_basic_queue_fairness_error(interaction)

    async def _handle_interactive_queue_fairness(
        self,
        interaction: discord.Interaction,
        audio_info: AudioInfo
    ) -> None:
        """
        处理交互式队列公平性替换

        Args:
            interaction: Discord交互对象
            audio_info: 音频信息
        """
        try:
            # 获取用户队列状态
            if not hasattr(self.music_player, '_playback_engine'):
                await self._send_basic_queue_fairness_error(interaction)
                return

            user_queue_service = UserQueueStatusService(self.music_player._playback_engine)
            user_info = user_queue_service.get_user_queue_info(interaction.user, interaction.guild.id)

            if not user_info.has_queued_song:
                await self._send_basic_queue_fairness_error(interaction)
                return

            # 显示替换确认界面
            from similubot.ui.button_interactions import InteractionResult

            # 创建临时Context对象
            class TempContext:
                def __init__(self, interaction):
                    self.author = interaction.user
                    self.send = interaction.followup.send

            temp_ctx = TempContext(interaction)

            result, _ = await self.interaction_manager.show_queue_fairness_replacement(
                temp_ctx,
                audio_info.title,
                user_info.queued_song_title or "未知歌曲",
                user_info.queue_position or 1
            )

            if result == InteractionResult.REPLACED:
                # 执行歌曲替换
                queue_manager = self.music_player.get_queue_manager(interaction.guild.id)
                success, position, error_msg = await queue_manager.replace_user_song(
                    interaction.user, audio_info
                )

                if success:
                    embed = discord.Embed(
                        title="✅ 歌曲替换成功",
                        description=f"已将您的歌曲替换为 **{audio_info.title}**",
                        color=discord.Color.green()
                    )
                    embed.add_field(
                        name="📍 队列位置",
                        value=f"第 {position} 位",
                        inline=True
                    )
                    embed.add_field(
                        name="点歌人",
                        value=interaction.user.display_name,
                        inline=True
                    )
                    await interaction.followup.send(embed=embed) # 呃啊居然不能用respond而是followup吗
                else:
                    await self.send_error_response(interaction, error_msg or "替换歌曲失败")
            else:
                # 用户拒绝或超时
                await self._send_basic_queue_fairness_error(interaction)

        except Exception as e:
            self.logger.error(f"处理交互式队列公平性失败: {e}")
            await self._send_basic_queue_fairness_error(interaction)

    async def _send_basic_queue_fairness_error(self, interaction: discord.Interaction) -> None:
        """
        发送基本的队列公平性错误消息

        Args:
            interaction: Discord交互对象
        """
        embed = discord.Embed(
            title="⚖️ 队列公平性限制",
            description="您已经有歌曲在队列中，请等待播放完成后再添加新歌曲。",
            color=discord.Color.orange()
        )

        embed.add_field(
            name="📋 队列规则",
            value="为了保证所有用户的公平使用，每位用户同时只能有一首歌曲在队列中等待播放。",
            inline=False
        )

        embed.add_field(
            name="💡 建议",
            value="使用 `/我的队列` 命令查看您当前的队列状态和预计播放时间。",
            inline=False
        )

        await interaction.edit_original_response(embed=embed)

    def _format_duration(self, duration_seconds: int) -> str:
        """
        格式化时长显示

        Args:
            duration_seconds: 时长（秒）

        Returns:
            格式化的时长字符串
        """
        if duration_seconds < 60:
            return f"{duration_seconds}秒"
        elif duration_seconds < 3600:
            minutes = duration_seconds // 60
            seconds = duration_seconds % 60
            return f"{minutes}:{seconds:02d}"
        else:
            hours = duration_seconds // 3600
            minutes = (duration_seconds % 3600) // 60
            seconds = duration_seconds % 60
            return f"{hours}:{minutes:02d}:{seconds:02d}"