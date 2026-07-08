"""
跳过歌曲投票系统UI组件

提供民主投票跳过当前歌曲的Discord界面，包括：
- 投票嵌入消息显示
- 反应监听和计数
- 实时投票状态更新
- 投票结果处理
"""

import logging
import asyncio
import inspect
from typing import Optional, List, Set, Callable, Any, Dict, Union, Awaitable
from enum import Enum
import discord
from discord.ext import commands

from similubot.core.interfaces import SongInfo


class VoteResult(Enum):
    """投票结果枚举"""
    PASSED = "passed"           # 投票通过，跳过歌曲
    FAILED = "failed"           # 投票失败，继续播放
    TIMEOUT = "timeout"         # 投票超时
    CANCELLED = "cancelled"     # 投票被取消


class SkipVotePoll:
    """
    跳过歌曲投票轮询器
    
    管理单次投票的完整生命周期，包括：
    - 创建和显示投票消息
    - 监听和处理用户反应
    - 计算投票阈值和结果
    - 更新投票状态显示
    """
    
    def __init__(
        self,
        ctx: commands.Context,
        current_song: SongInfo,
        voice_channel_members: List[discord.Member],
        threshold: Union[int, str],
        timeout: int = 60,
        min_voters: int = 2
    ):
        """
        初始化投票轮询器
        
        Args:
            ctx: Discord命令上下文
            current_song: 当前播放的歌曲信息
            voice_channel_members: 语音频道中的成员列表
            threshold: 投票阈值（数字或百分比字符串）
            timeout: 投票超时时间（秒）
            min_voters: 最小投票人数要求
        """
        self.ctx = ctx
        self.current_song = current_song
        self.voice_channel_members = voice_channel_members
        self.threshold = threshold
        self.timeout = timeout
        self.min_voters = min_voters
        
        # 投票状态
        self.voters: Set[int] = set()  # 已投票用户ID集合
        self.voice_channel_member_ids = {member.id for member in voice_channel_members}
        self.vote_message: Optional[discord.Message] = None
        self.result: Optional[VoteResult] = None
        self.is_active = False
        
        # 日志记录器
        self.logger = logging.getLogger("similubot.ui.skip_vote_poll")

        # 计算投票阈值
        self.required_votes = self._calculate_required_votes()
        
        # 投票完成回调 - 支持同步和异步回调
        self.on_vote_complete: Optional[Union[
            Callable[[VoteResult], None],
            Callable[[VoteResult], Awaitable[None]]
        ]] = None
        
        self.logger.debug(
            f"初始化跳过投票 - 歌曲: {current_song.title}, "
            f"语音频道成员: {len(voice_channel_members)}, "
            f"需要投票数: {self.required_votes}"
        )

    async def _invoke_callback(self, callback: Callable, result: VoteResult) -> None:
        """
        调用回调函数，支持同步和异步回调

        Args:
            callback: 回调函数（可以是同步或异步）
            result: 投票结果
        """
        try:
            # 检查回调是否是协程函数
            if inspect.iscoroutinefunction(callback):
                # 异步回调
                self.logger.debug("调用异步投票完成回调")
                await callback(result)
            else:
                # 同步回调
                self.logger.debug("调用同步投票完成回调")
                callback(result)
        except Exception as e:
            self.logger.error(f"调用投票完成回调时发生错误: {e}", exc_info=True)
    
    def _calculate_required_votes(self) -> int:
        """
        计算所需的投票数量
        
        Returns:
            所需的投票数量
        """
        total_members = len(self.voice_channel_members)
        
        # 如果人数少于最小投票要求，返回1（允许直接跳过）
        if total_members < self.min_voters:
            self.logger.debug(f"语音频道人数({total_members})少于最小投票要求({self.min_voters})，允许直接跳过")
            return 1
        
        # 处理百分比阈值
        if isinstance(self.threshold, str) and self.threshold.endswith('%'):
            try:
                percentage = float(self.threshold[:-1]) / 100.0
                required = max(1, int(total_members * percentage))
                self.logger.debug(f"百分比阈值 {self.threshold}: {total_members} * {percentage} = {required}")
                return required
            except ValueError:
                self.logger.warning(f"无效的百分比阈值: {self.threshold}，使用默认值5")
                return min(5, total_members)
        
        # 处理固定数字阈值
        try:
            fixed_threshold = int(self.threshold)
            # 确保阈值不超过频道总人数
            required = min(fixed_threshold, total_members)
            self.logger.debug(f"固定阈值: {fixed_threshold}, 实际需要: {required}")
            return required
        except ValueError:
            self.logger.warning(f"无效的投票阈值: {self.threshold}，使用默认值5")
            return min(5, total_members)
    
    async def start_poll(self) -> VoteResult:
        """
        启动投票轮询
        
        Returns:
            投票结果
        """
        try:
            self.is_active = True
            self.logger.info(f"启动跳过投票 - 歌曲: {self.current_song.title}")
            
            # 创建投票嵌入消息
            embed = self._create_poll_embed()
            
            # 发送投票消息
            self.vote_message = await self.ctx.send(embed=embed)
            
            # 添加投票反应
            await self.vote_message.add_reaction("✅")
            
            # 启动投票监听任务
            vote_task = asyncio.create_task(self._monitor_votes())
            timeout_task = asyncio.create_task(self._handle_timeout())
            
            # 等待投票完成或超时
            done, pending = await asyncio.wait(
                [vote_task, timeout_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # 取消未完成的任务
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            
            # 获取结果
            for task in done:
                if not task.cancelled():
                    self.result = task.result()
                    break
            
            if self.result is None:
                self.result = VoteResult.TIMEOUT
            
            # 更新最终消息
            await self._update_final_message()
            
            self.logger.info(f"投票结束 - 结果: {self.result.value}")
            
            # 调用完成回调 - 支持同步和异步回调
            if self.on_vote_complete:
                await self._invoke_callback(self.on_vote_complete, self.result)
            
            return self.result
            
        except Exception as e:
            self.logger.error(f"投票过程中发生错误: {e}", exc_info=True)
            self.result = VoteResult.FAILED
            return self.result
        finally:
            self.is_active = False
    
    async def _monitor_votes(self) -> VoteResult:
        """
        监听投票反应
        
        Returns:
            投票结果
        """
        def check_reaction(payload: discord.RawReactionActionEvent) -> bool:
            """检查反应是否有效"""
            return (
                self.vote_message is not None and
                payload.message_id == self.vote_message.id and
                str(payload.emoji) == "✅" and
                payload.user_id != self.ctx.bot.user.id and
                payload.user_id in self.voice_channel_member_ids
            )
        
        while self.is_active:
            try:
                # 等待有效的投票反应
                payload = await self.ctx.bot.wait_for(
                    'raw_reaction_add',
                    check=check_reaction,
                    timeout=1.0  # 短超时，用于定期检查状态
                )
                
                # 记录投票
                if payload.user_id not in self.voters:
                    self.voters.add(payload.user_id)
                    voter = self._get_voice_member(payload.user_id)
                    voter_name = getattr(voter, 'display_name', payload.user_id)
                    self.logger.debug(f"用户 {voter_name} 投票跳过，当前票数: {len(self.voters)}/{self.required_votes}")
                    
                    # 更新投票消息
                    await self._update_poll_message()
                    
                    # 检查是否达到阈值
                    if len(self.voters) >= self.required_votes:
                        self.logger.info(f"投票通过 - 票数: {len(self.voters)}/{self.required_votes}")
                        return VoteResult.PASSED
                
            except asyncio.TimeoutError:
                # 定期检查，继续循环
                continue
            except Exception as e:
                self.logger.error(f"监听投票反应时出错: {e}")
                continue
        
        return VoteResult.FAILED

    def _get_voice_member(self, user_id: int) -> Optional[discord.Member]:
        """Return the voice-channel member with the given user ID."""
        for member in self.voice_channel_members:
            if member.id == user_id:
                return member
        return None
    
    async def _handle_timeout(self) -> VoteResult:
        """
        处理投票超时
        
        Returns:
            超时结果
        """
        await asyncio.sleep(self.timeout)
        self.logger.debug(f"投票超时 - {self.timeout}秒")
        return VoteResult.TIMEOUT
    
    def _create_poll_embed(self) -> discord.Embed:
        """
        创建投票嵌入消息
        
        Returns:
            Discord嵌入消息
        """
        embed = discord.Embed(
            title="🗳️ 跳过歌曲投票",
            description="点击 ✅ 投票跳过当前歌曲",
            color=discord.Color.blue()
        )
        
        # 当前歌曲信息
        embed.add_field(
            name="🎵 当前歌曲",
            value=f"**{self.current_song.title}**\n点歌人: {self.current_song.requester.display_name}",
            inline=False
        )
        
        # 投票状态
        embed.add_field(
            name="📊 投票状态",
            value=f"**{len(self.voters)}/{self.required_votes}** 票\n语音频道人数: {len(self.voice_channel_members)}",
            inline=True
        )
        
        # 投票规则
        embed.add_field(
            name="⏱️ 投票规则",
            value=f"超时时间: {self.timeout}秒\n只有语音频道内的用户可以投票",
            inline=True
        )
        
        embed.set_footer(text="民主投票系统 • 每人只能投一票")

        return embed

    async def _update_poll_message(self) -> None:
        """更新投票消息显示"""
        if not self.vote_message:
            return

        try:
            embed = self._create_poll_embed()
            await self.vote_message.edit(embed=embed)
        except Exception as e:
            self.logger.error(f"更新投票消息失败: {e}")

    async def _update_final_message(self) -> None:
        """更新最终投票结果消息"""
        if not self.vote_message:
            return

        try:
            embed = self._create_final_embed()
            await self.vote_message.edit(embed=embed)
        except Exception as e:
            self.logger.error(f"更新最终消息失败: {e}")

    def _create_final_embed(self) -> discord.Embed:
        """
        创建最终结果嵌入消息

        Returns:
            Discord嵌入消息
        """
        if self.result == VoteResult.PASSED:
            embed = discord.Embed(
                title="✅ 投票通过",
                description="跳过歌曲投票已通过，正在跳过当前歌曲...",
                color=discord.Color.green()
            )
        elif self.result == VoteResult.TIMEOUT:
            embed = discord.Embed(
                title="⏰ 投票超时",
                description="投票时间已结束，继续播放当前歌曲。",
                color=discord.Color.orange()
            )
        else:
            embed = discord.Embed(
                title="❌ 投票失败",
                description="投票未通过，继续播放当前歌曲。",
                color=discord.Color.red()
            )

        # 当前歌曲信息
        embed.add_field(
            name="🎵 歌曲",
            value=f"**{self.current_song.title}**\n点歌人: {self.current_song.requester.display_name}",
            inline=False
        )

        # 最终投票结果
        embed.add_field(
            name="📊 最终结果",
            value=f"**{len(self.voters)}/{self.required_votes}** 票\n参与投票: {len(self.voters)} 人",
            inline=True
        )

        embed.set_footer(text="投票已结束")

        return embed


class VoteManager:
    """
    投票管理器

    提供高级接口来管理跳过歌曲的投票流程，包括：
    - 检查投票前置条件
    - 启动和管理投票
    - 处理投票结果
    """

    def __init__(self, config_manager):
        """
        初始化投票管理器

        Args:
            config_manager: 配置管理器实例
        """
        self.config_manager = config_manager
        self.logger = logging.getLogger("similubot.ui.vote_manager")

        # 活跃投票跟踪（每个服务器只能有一个活跃投票）
        self.active_polls: Dict[int, SkipVotePoll] = {}

    def get_voice_channel_members(self, ctx: commands.Context) -> Optional[List[discord.Member]]:
        """
        获取语音频道中的成员列表

        Args:
            ctx: Discord命令上下文

        Returns:
            语音频道成员列表，如果机器人未连接则返回None
        """
        if not ctx.guild or not ctx.guild.voice_client:
            return None

        voice_client = ctx.guild.voice_client
        if not voice_client.channel:
            return None

        # 获取语音频道中的所有非机器人成员
        try:
            channel = voice_client.channel
            if channel:
                # 直接访问members属性，忽略类型检查
                members = getattr(channel, 'members', [])
                if members:
                    non_bot_members = [member for member in members if not member.bot]

                    channel_name = getattr(channel, 'name', 'Unknown')
                    self.logger.debug(f"语音频道 {channel_name} 中有 {len(non_bot_members)} 个用户")

                    return non_bot_members
        except Exception as e:
            self.logger.error(f"获取语音频道成员失败: {e}")

        return None

    def should_use_voting(self, voice_channel_members: List[discord.Member]) -> bool:
        """
        判断是否应该使用投票系统

        Args:
            voice_channel_members: 语音频道成员列表

        Returns:
            True if voting should be used, False for direct skip
        """
        # 检查投票系统是否启用
        if not self.config_manager.is_skip_voting_enabled():
            self.logger.debug("投票系统已禁用，使用直接跳过")
            return False

        # 检查最小投票人数要求
        min_voters = self.config_manager.get_skip_voting_min_voters()
        if len(voice_channel_members) < min_voters:
            self.logger.debug(f"语音频道人数({len(voice_channel_members)})少于最小投票要求({min_voters})，使用直接跳过")
            return False

        return True

    async def start_skip_vote(
        self,
        ctx: commands.Context,
        current_song: SongInfo,
        on_vote_complete: Optional[Callable[[VoteResult], None]] = None
    ) -> Optional[VoteResult]:
        """
        启动跳过歌曲投票

        Args:
            ctx: Discord命令上下文
            current_song: 当前播放的歌曲
            on_vote_complete: 投票完成回调函数

        Returns:
            投票结果，如果无法启动投票则返回None
        """
        if not ctx.guild:
            self.logger.error("无法获取服务器信息")
            return None

        guild_id = ctx.guild.id

        # 检查是否已有活跃投票
        if guild_id in self.active_polls:
            self.logger.warning(f"服务器 {guild_id} 已有活跃投票")
            return None

        # 获取语音频道成员
        voice_members = self.get_voice_channel_members(ctx)
        if not voice_members:
            self.logger.error("无法获取语音频道成员")
            return None

        # 检查是否应该使用投票
        if not self.should_use_voting(voice_members):
            return None

        try:
            # 创建投票轮询器
            poll = SkipVotePoll(
                ctx=ctx,
                current_song=current_song,
                voice_channel_members=voice_members,
                threshold=self.config_manager.get_skip_voting_threshold(),
                timeout=self.config_manager.get_skip_voting_timeout(),
                min_voters=self.config_manager.get_skip_voting_min_voters()
            )

            # 设置回调
            poll.on_vote_complete = on_vote_complete

            # 注册活跃投票
            self.active_polls[guild_id] = poll

            # 启动投票
            result = await poll.start_poll()

            return result

        except Exception as e:
            self.logger.error(f"启动投票时发生错误: {e}", exc_info=True)
            return None
        finally:
            # 清理活跃投票
            if guild_id in self.active_polls:
                del self.active_polls[guild_id]

    def cancel_active_vote(self, guild_id: int) -> bool:
        """
        取消活跃的投票

        Args:
            guild_id: 服务器ID

        Returns:
            是否成功取消投票
        """
        if guild_id in self.active_polls:
            poll = self.active_polls[guild_id]
            poll.is_active = False
            del self.active_polls[guild_id]
            self.logger.info(f"取消服务器 {guild_id} 的活跃投票")
            return True

        return False
