"""
播放控制命令实现

处理播放控制相关的Slash命令：
- 跳过歌曲（民主投票）
- 显示播放进度
- 停止播放
"""

import logging
from typing import Any
import discord

from ..core.base_command import BaseSlashCommand
from ..ui.message_visibility import MessageVisibility, MessageType
from similubot.utils.config_manager import ConfigManager
from similubot.progress.music_progress import MusicProgressBar
from similubot.ui.skip_vote_poll import VoteManager, VoteResult


class PlaybackControlCommands(BaseSlashCommand):
    """
    播放控制命令处理器

    负责处理播放控制操作，包括跳过、进度显示等
    """

    def __init__(self, config: ConfigManager, music_player: Any):
        """
        初始化播放控制命令

        Args:
            config: 配置管理器
            music_player: 音乐播放器实例
        """
        super().__init__(config, music_player)

        # 初始化消息可见性控制器
        self.message_visibility = MessageVisibility()

        # 初始化进度条
        self.progress_bar = MusicProgressBar(music_player)

        # 初始化投票管理器
        self.vote_manager = VoteManager(config)

        self.logger.debug("播放控制命令已初始化")

    async def execute(self, interaction: discord.Interaction, **kwargs) -> None:
        """
        执行播放控制命令

        Args:
            interaction: Discord交互对象
            **kwargs: 命令参数，应包含 'action' 参数
        """
        action = kwargs.get('action', 'progress')

        if action == 'skip':
            await self.handle_skip_song(interaction)
        elif action == 'progress':
            await self.handle_show_progress(interaction)
        else:
            await interaction.response.send_message(
                f"❌ 未知的播放控制操作: {action}",
                ephemeral=True
            )

    async def handle_skip_song(self, interaction: discord.Interaction) -> None:
        """
        处理跳过歌曲命令

        Args:
            interaction: Discord交互对象
        """
        try:
            # 检查前置条件
            if not await self.check_prerequisites(interaction):
                return

            self.logger.debug(f"处理跳过命令 - 用户: {interaction.user.display_name}")

            # 获取当前歌曲信息
            queue_info = await self.music_player.get_queue_info(interaction.guild.id)
            current_song = queue_info.get("current_song")

            if not current_song:
                await self.send_error_response(
                    interaction,
                    "当前没有歌曲在播放",
                    ephemeral=True
                )
                return

            self.logger.debug(f"当前歌曲: {current_song.title}")

            # 校验发起者与 bot 处于同一语音频道（投票名单取自 bot 所在频道，
            # 不校验会导致发起者无法投票、名单与实际听众不一致）
            voice_client = interaction.guild.voice_client
            user_voice = getattr(interaction.user, 'voice', None)
            if (
                not voice_client or not voice_client.channel
                or not user_voice or user_voice.channel != voice_client.channel
            ):
                await self.send_error_response(
                    interaction,
                    "你需要和我在同一个语音频道才能发起跳过投票",
                    ephemeral=True
                )
                return

            # 获取语音频道成员
            voice_members = self.vote_manager.get_voice_channel_members(
                self._create_temp_context(interaction)
            )

            if not voice_members:
                await self.send_error_response(
                    interaction,
                    "机器人未连接到语音频道或无法获取频道成员",
                    ephemeral=True
                )
                return

            # 检查是否应该使用投票系统
            if not self.vote_manager.should_use_voting(voice_members):
                # 直接跳过
                self.logger.debug("使用直接跳过模式")
                await self._direct_skip_song(interaction, current_song)
                return
            elif current_song.requester == interaction.user:
                # 如果请求者是当前用户，直接跳过
                self.logger.debug("使用直接跳过模式（请求者是当前用户）")
                await self._direct_skip_song(interaction, current_song)
                return

            # 启动民主投票
            self.logger.info(f"启动民主投票跳过 - 歌曲: {current_song.title}, 语音频道人数: {len(voice_members)}")

            # 发送初始响应
            embed = discord.Embed(
                title="🗳️ 启动跳过投票",
                description="正在启动民主投票跳过当前歌曲...",
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)

            # 创建投票完成回调
            async def on_vote_complete(result: VoteResult) -> None:
                """投票完成回调处理"""
                if result == VoteResult.PASSED:
                    # 投票通过，执行跳过
                    self.logger.info(f"投票通过，跳过歌曲: {current_song.title}")
                    await self._execute_skip(interaction.guild.id, current_song.title)
                    await self._update_vote_result_message(
                        interaction, "✅ 投票通过，已跳过当前歌曲", discord.Color.green()
                    )
                else:
                    # 投票失败或超时，继续播放
                    self.logger.info(f"投票未通过 ({result.value})，继续播放: {current_song.title}")
                    await self._update_vote_result_message(
                        interaction, "❎ 投票未通过，继续播放当前歌曲", discord.Color.orange()
                    )

            # 启动投票
            result = await self.vote_manager.start_skip_vote(
                ctx=self._create_temp_context(interaction),
                current_song=current_song,
                on_vote_complete=on_vote_complete
            )

            if result is None:
                # 投票启动失败（已有活跃投票/无法获取成员等）：
                # 报错告知用户，绝不能绕过投票机制直接跳歌
                self.logger.warning("投票启动失败")
                await self._update_vote_result_message(
                    interaction, "⚠️ 投票启动失败，请稍后重试", discord.Color.red()
                )

        except Exception as e:
            self.logger.error(f"处理跳过命令失败: {e}", exc_info=True)
            await self.handle_command_error(interaction, e)

    async def _update_vote_result_message(
        self,
        interaction: discord.Interaction,
        text: str,
        color: discord.Color
    ) -> None:
        """投票结束后更新初始响应消息（interaction token 15 分钟内有效，投票默认 60 秒超时）"""
        try:
            embed = discord.Embed(title="🗳️ 跳过投票", description=text, color=color)
            await interaction.edit_original_response(embed=embed)
        except Exception as e:
            self.logger.debug(f"更新投票结果消息失败（token 可能已过期）: {e}")

    async def handle_show_progress(self, interaction: discord.Interaction) -> None:
        """
        处理显示播放进度命令

        Args:
            interaction: Discord交互对象
        """
        try:
            # 检查前置条件
            if not await self.check_prerequisites(interaction):
                return

            self.logger.debug(f"显示播放进度 - 用户: {interaction.user.display_name}")

            # 获取当前歌曲信息
            queue_info = await self.music_player.get_queue_info(interaction.guild.id)
            current_song = queue_info.get("current_song")

            if not current_song:
                await self.send_error_response(
                    interaction,
                    "当前没有歌曲在播放",
                    ephemeral=True
                )
                return

            # 发送初始响应
            embed = discord.Embed(
                title="🔄 加载进度条...",
                description="正在加载播放进度信息...",
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)

            # 获取消息对象用于进度条更新
            message = await interaction.original_response()

            # 启动实时进度条
            success = await self.progress_bar.show_progress_bar(
                message,
                interaction.guild.id
            )

            if not success:
                # 回退到静态显示
                embed = discord.Embed(
                    title="🎶 正在播放",
                    color=discord.Color.green()
                )

                embed.add_field(
                    name="歌曲标题",
                    value=current_song.title,
                    inline=False
                )

                embed.add_field(
                    name="时长",
                    value=self._format_duration(current_song.duration),
                    inline=True
                )

                embed.add_field(
                    name="上传者",
                    value=current_song.uploader,
                    inline=True
                )

                embed.add_field(
                    name="点歌人",
                    value=current_song.requester.display_name,
                    inline=True
                )

                # 添加静态状态
                if queue_info["playing"]:
                    embed.add_field(
                        name="状态",
                        value="▶️ 播放中",
                        inline=True
                    )
                elif queue_info["paused"]:
                    embed.add_field(
                        name="状态",
                        value="⏸️ 已暂停",
                        inline=True
                    )

                if hasattr(current_song, 'audio_info') and current_song.audio_info.thumbnail_url:
                    embed.set_thumbnail(url=current_song.audio_info.thumbnail_url)

                await interaction.edit_original_response(embed=embed)

        except Exception as e:
            self.logger.error(f"显示播放进度失败: {e}", exc_info=True)
            await self.handle_command_error(interaction, e)

    async def _direct_skip_song(self, interaction: discord.Interaction, current_song) -> None:
        """
        直接跳过歌曲（无投票）

        Args:
            interaction: Discord交互对象
            current_song: 当前歌曲信息
        """
        try:
            # 停止任何活跃的进度条
            self.progress_bar.stop_progress_updates(interaction.guild.id)

            success, skipped_title, error = await self.music_player.skip_current_song(interaction.guild.id)

            if not success:
                await self.send_error_response(interaction, error, ephemeral=True)
                return

            embed = discord.Embed(
                title="⏭️ 歌曲已跳过",
                description=f"已跳过: **{skipped_title}**",
                color=discord.Color.orange()
            )

            # 跳过通知是public消息
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=embed)
            else:
                await interaction.response.send_message(embed=embed)

            self.logger.info(f"直接跳过歌曲: {skipped_title}")

        except Exception as e:
            self.logger.error(f"直接跳过歌曲失败: {e}", exc_info=True)
            await self.handle_command_error(interaction, e)

    async def _execute_skip(self, guild_id: int, song_title: str) -> None:
        """
        执行歌曲跳过操作

        Args:
            guild_id: 服务器ID
            song_title: 歌曲标题（用于日志）
        """
        try:
            success, skipped_title, error = await self.music_player.skip_current_song(guild_id)

            # 停止任何活跃的进度条
            self.progress_bar.stop_progress_updates(guild_id)

            if success:
                self.logger.info(f"成功跳过歌曲: {skipped_title}")
            else:
                self.logger.error(f"跳过歌曲失败: {error}")

        except Exception as e:
            self.logger.error(f"执行跳过操作失败: {e}", exc_info=True)

    def _create_temp_context(self, interaction: discord.Interaction):
        """
        创建临时Context对象用于兼容现有的投票管理器

        Args:
            interaction: Discord交互对象

        Returns:
            临时Context对象
        """
        class TempContext:
            def __init__(self, interaction):
                self.author = interaction.user
                self.guild = interaction.guild
                self.channel = interaction.channel
                self.bot = interaction.client
                self.send = interaction.followup.send

        return TempContext(interaction)

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