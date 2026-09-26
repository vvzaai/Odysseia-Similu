"""
播放引擎 - 音乐播放的核心控制器

协调音频提供者、队列管理器和语音管理器，提供统一的播放控制接口。
负责播放流程的编排和状态管理。
"""

import logging
import asyncio
import os
import time
from typing import Optional, Dict, Any, Tuple
import discord
from discord.ext import commands

from similubot.core.interfaces import IPlaybackEngine, IQueueManager, IVoiceManager, IAudioProvider, SongInfo
from similubot.progress.base import ProgressCallback
from similubot.provider import AudioProviderFactory
from similubot.queue import QueueManager, PersistenceManager
from .voice_manager import VoiceManager
from .seek_manager import SeekManager


class PlaybackEngine(IPlaybackEngine):
    """
    播放引擎实现
    
    协调各个模块，提供完整的音乐播放功能。
    管理播放状态、队列操作和用户交互。
    """
    
    def __init__(
        self, 
        bot: commands.Bot, 
        temp_dir: str = "./temp", 
        config=None
    ):
        """
        初始化播放引擎
        
        Args:
            bot: Discord机器人实例
            temp_dir: 临时文件目录
            config: 配置管理器
        """
        self.bot = bot
        self.temp_dir = temp_dir
        self.config = config
        self.logger = logging.getLogger("similubot.playback.engine")
        
        # 初始化组件
        self.audio_provider_factory = AudioProviderFactory(temp_dir, config)
        self.voice_manager = VoiceManager(bot)
        self.seek_manager = SeekManager()
        
        # 初始化持久化管理器
        self.persistence_manager = PersistenceManager() if config else None
        
        # 服务器特定的队列管理器
        self._queue_managers: Dict[int, IQueueManager] = {}
        
        # 播放状态跟踪
        self._playback_tasks: Dict[int, asyncio.Task] = {}
        self._current_audio_files: Dict[int, str] = {}

        # 播放控制标记（guild_id -> "skip"/"stop"）：区分主动控制与自然结束/播放出错。
        # discord.py 的 stop() 与自然结束都会触发 after(error=None)，无标记无法区分，
        # 而重试逻辑只在真正出错时才应触发
        self._control_actions: Dict[int, str] = {}

        # 自动断开任务跟踪（队列空闲超时后断开语音，music.auto_disconnect_timeout）
        self._disconnect_tasks: Dict[int, asyncio.Task] = {}

        # 播放失败重试退避（秒）：下载成功但 ffmpeg 播放失败时的重试间隔
        self._play_retry_delays: Tuple[float, ...] = (1.0, 3.0)

        # 播放时间跟踪
        self._playback_start_times: Dict[int, float] = {}
        self._playback_paused_times: Dict[int, float] = {}
        self._total_paused_duration: Dict[int, float] = {}

        # 文本频道跟踪（用于发送通知消息）
        self._text_channels: Dict[int, int] = {}  # guild_id -> text_channel_id

        # 事件处理器表（实例级——类属性的可变 dict 会被所有实例共享，属 bug）
        self._event_handlers = {
            "song_requester_absent_skip": [],  # 跳过点歌人不在语音频道的歌曲
            "show_song_info": [],  # 歌曲信息
            "your_song_notification": [],  # 要轮到你的歌了！
            "song_added_notification": [],  # 歌曲添加到队列的公共通知
        }

        self.logger.info("🎵 播放引擎初始化完成")
    
    def get_queue_manager(self, guild_id: int) -> IQueueManager:
        """
        获取或创建服务器的队列管理器
        
        Args:
            guild_id: Discord服务器ID
            
        Returns:
            队列管理器实例
        """
        if guild_id not in self._queue_managers:
            queue_manager = QueueManager(guild_id, self.persistence_manager, self.config)
            self._queue_managers[guild_id] = queue_manager
            self.logger.debug(f"为服务器 {guild_id} 创建队列管理器")
        
        return self._queue_managers[guild_id]

    def set_text_channel(self, guild_id: int, channel_id: int) -> None:
        """
        设置服务器的文本频道ID（用于发送通知消息）

        Args:
            guild_id: Discord服务器ID
            channel_id: 文本频道ID
        """
        self._text_channels[guild_id] = channel_id
        self.logger.debug(f"设置服务器 {guild_id} 的文本频道: {channel_id}")

    def get_text_channel_id(self, guild_id: int) -> Optional[int]:
        """
        获取服务器的文本频道ID

        Args:
            guild_id: Discord服务器ID

        Returns:
            文本频道ID，如果未设置则返回None
        """
        return self._text_channels.get(guild_id)

    # 事件处理器表在 __init__ 中实例化（见上）
    
    def add_event_handler(self, event_type: str, handler: callable) -> None:
        """
        添加播放事件处理器
        
        Args:
            event_type: 事件类型
            handler: 事件处理函数
        """

        if event_type in self._event_handlers:
            self._event_handlers[event_type].append(handler)
            self.logger.debug(f"添加事件处理器: {event_type}")
        else:
            self.logger.warning(f"未知事件类型: {event_type}")

    async def _trigger_event(self, event_type: str, **kwargs) -> None:
        """
        触发播放事件
        
        Args:
            event_type: 事件类型
            *args: 事件参数
            **kwargs: 事件关键字参数
        """
        if event_type in self._event_handlers:
            for handler in self._event_handlers[event_type]:
                try:
                    kwargs['bot'] = self.bot  # 确保bot实例传递给处理器
                    await handler(**kwargs)
                except Exception as e:
                    self.logger.error(f"事件处理器 {handler.__name__} 处理 {event_type} 时出错: {e}")
        else:
            self.logger.warning(f"未知事件类型: {event_type}")

    async def _trigger_song_added_notification(self, guild_id: int, audio_info, position: int, source_type: str = "点歌", requester=None) -> None:
        """
        触发歌曲添加通知事件

        Args:
            guild_id: 服务器ID
            audio_info: 音频信息
            position: 队列位置
            source_type: 添加来源类型 ("点歌" 或 "抽卡")
            requester: 请求用户 (discord.Member)
        """
        try:
            channel_id = self.get_text_channel_id(guild_id)
            if not channel_id:
                self.logger.debug(f"服务器 {guild_id} 未设置文本频道，跳过歌曲添加通知")
                return

            # 触发歌曲添加通知事件
            await self._trigger_event(
                "song_added_notification",
                guild_id=guild_id,
                channel_id=channel_id,
                song=audio_info,
                position=position,
                source_type=source_type,
                requester=requester
            )

        except Exception as e:
            self.logger.error(f"触发歌曲添加通知事件失败: {e}", exc_info=True)

    async def add_song_to_queue(
        self, 
        url: str, 
        requester: discord.Member, 
        progress_callback: Optional[ProgressCallback] = None
    ) -> Tuple[bool, Optional[int], Optional[str]]:
        """
        添加歌曲到队列
        
        Args:
            url: 音频URL
            requester: 请求用户
            progress_callback: 进度回调
            
        Returns:
            (成功标志, 队列位置, 错误消息)
        """
        try:
            guild_id = requester.guild.id

            # 来点歌说明频道恢复活跃，取消队列空闲的自动断开倒计时
            self._cancel_disconnect_task(guild_id)
            
            # 检查URL是否支持
            if not self.audio_provider_factory.is_supported_url(url):
                return False, None, "不支持的URL格式"
            
            # 提取音频信息
            audio_info = await self.audio_provider_factory.extract_audio_info(url)
            if not audio_info:
                return False, None, "无法获取音频信息"
            
            # 添加到队列
            queue_manager = self.get_queue_manager(guild_id)
            try:
                position = await queue_manager.add_song(audio_info, requester)
            except Exception as e:
                # 检查是否是队列相关错误（重复歌曲、队列公平性或歌曲过长）
                error_msg = str(e)
                if ("已经请求了这首歌曲" in error_msg or
                    "已经有" in error_msg and "首歌曲在队列中" in error_msg or
                    "正在播放中" in error_msg or
                    "歌曲时长" in error_msg and "超过了最大限制" in error_msg):
                    return False, None, error_msg
                else:
                    raise  # 重新抛出其他异常

            self.logger.info(f"歌曲添加到队列 - 服务器 {guild_id}: {audio_info.title} (位置 {position})")

            # 触发歌曲添加通知事件
            await self._trigger_song_added_notification(guild_id, audio_info, position, "点歌", requester)

            # 如果没有正在播放，开始播放
            if not self.is_playing(guild_id):
                await self._start_playback_if_needed(guild_id)

            return True, position, None
            
        except Exception as e:
            error_msg = f"添加歌曲到队列失败: {e}"
            self.logger.error(error_msg)
            return False, None, error_msg
    
    async def skip_song(self, guild_id: int) -> Tuple[bool, Optional[SongInfo], Optional[str]]:
        """
        跳过当前歌曲

        Args:
            guild_id: 服务器ID

        Returns:
            (成功标志, 当前歌曲信息, 错误消息)
        """
        try:
            queue_manager = self.get_queue_manager(guild_id)

            # 获取当前歌曲信息用于返回
            current_song = queue_manager.get_current_song()

            if not current_song:
                return False, None, "当前没有歌曲在播放"

            # 标记为跳歌（区分主动控制与播放出错，重试逻辑不应对主动操作生效）
            self._control_actions[guild_id] = "skip"

            # 停止当前播放 - 这会触发 after_playing 回调，playback loop 会自然地继续到下一首歌
            self.logger.debug(f"停止当前播放 - 服务器 {guild_id}")
            self.voice_manager.stop_audio(guild_id)

            # 清理当前音频文件
            self.logger.debug(f"清理当前音频文件 - 服务器 {guild_id}")
            await self._cleanup_current_audio(guild_id)

            self.logger.info(f"跳过歌曲 - 服务器 {guild_id}: {current_song.title}")

            return True, current_song, None

        except Exception as e:
            error_msg = f"跳过歌曲失败: {e}"
            self.logger.error(error_msg)
            return False, None, error_msg

    async def jump_to_position(self, guild_id: int, position: int) -> Tuple[bool, Optional[SongInfo], Optional[str]]:
        """
        跳转到队列中的指定位置

        Args:
            guild_id: 服务器ID
            position: 队列位置（从1开始）

        Returns:
            (成功标志, 目标歌曲信息, 错误消息)
        """
        try:
            queue_manager = self.get_queue_manager(guild_id)

            # 标记为跳歌（跳转本质上是跳过当前歌曲，不应触发播放失败重试）
            self._control_actions[guild_id] = "skip"

            # 停止当前播放 - 这会触发 after_playing 回调
            self.logger.debug(f"停止当前播放以跳转 - 服务器 {guild_id}")
            self.voice_manager.stop_audio(guild_id)

            # 清理当前音频文件
            self.logger.debug(f"清理当前音频文件以跳转 - 服务器 {guild_id}")
            await self._cleanup_current_audio(guild_id)

            # 跳转到指定位置
            self.logger.debug(f"跳转到队列位置 {position} - 服务器 {guild_id}")
            target_song = await queue_manager.jump_to_position(position)

            if not target_song:
                return False, None, f"无效的队列位置: {position}"

            self.logger.info(f"跳转到位置 {position} - 服务器 {guild_id}: {target_song.title}")

            return True, target_song, None

        except Exception as e:
            error_msg = f"跳转到位置失败: {e}"
            self.logger.error(error_msg)
            return False, None, error_msg

    async def stop_playback(self, guild_id: int) -> Tuple[bool, Optional[str]]:
        """
        停止播放并清空队列

        Args:
            guild_id: 服务器ID

        Returns:
            (成功标志, 错误消息)
        """
        try:
            # 标记为手动停止（重试逻辑不应对主动停止生效）
            self._control_actions[guild_id] = "stop"
            self._cancel_disconnect_task(guild_id)

            # 停止播放
            self.voice_manager.stop_audio(guild_id)

            # 清理播放时间跟踪
            self._cleanup_playback_tracking(guild_id)

            # 清空队列
            queue_manager = self.get_queue_manager(guild_id)
            cleared_count = await queue_manager.clear_queue()

            # 清理播放任务（取消后等待其收尾，避免与循环的 finally 清理竞态）
            task = self._playback_tasks.pop(guild_id, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # 清理音频文件
            await self._cleanup_current_audio(guild_id)

            self._control_actions.pop(guild_id, None)
            self.logger.info(f"停止播放 - 服务器 {guild_id}: 清空了 {cleared_count} 首歌曲")
            return True, None

        except Exception as e:
            error_msg = f"停止播放失败: {e}"
            self.logger.error(error_msg)
            self._control_actions.pop(guild_id, None)
            return False, error_msg
    
    async def connect_to_user_channel(self, user: discord.Member) -> Tuple[bool, Optional[str]]:
        """
        连接到用户语音频道
        
        Args:
            user: Discord用户
            
        Returns:
            (成功标志, 错误消息)
        """
        success, error = await self.voice_manager.connect_to_user_channel(user)
        if success:
            # 连接成功说明频道恢复活跃，取消自动断开倒计时
            self._cancel_disconnect_task(user.guild.id)
        return success, error
    
    def get_queue_info(self, guild_id: int) -> Dict[str, Any]:
        """
        获取队列信息（IPlaybackEngine 同步接口实现）。

        queue_manager.get_queue_info() 为持锁异步方法，此处用同步原语读取快照。
        同步方法执行期间不会让出事件循环控制权，无锁遍历是安全的。
        调用方请优先使用 MusicPlayerAdapter 的异步 get_queue_info。
        """
        try:
            queue_manager = self.get_queue_manager(guild_id)
            current_song = queue_manager.get_current_song()
            queue_songs = list(queue_manager._queue)
            return {
                'guild_id': guild_id,
                'current_song': current_song.get_display_info() if current_song else None,
                'current_position': queue_manager._current_position,
                'queue_length': len(queue_songs),
                'queue_songs': [song.get_display_info() for song in queue_songs[:10]],
                'total_duration': sum(song.duration for song in queue_songs),
                'has_more_songs': len(queue_songs) > 10
            }
        except Exception as e:
            self.logger.error(f"获取队列信息失败 - 服务器 {guild_id}: {e}")
            return {
                'guild_id': guild_id,
                'current_song': None,
                'current_position': 0.0,
                'queue_length': 0,
                'queue_songs': [],
                'total_duration': 0,
                'has_more_songs': False
            }

    def is_playing(self, guild_id: int) -> bool:
        """
        检查是否正在播放
        
        Args:
            guild_id: 服务器ID
            
        Returns:
            如果正在播放则返回True
        """
        return self.voice_manager.is_playing(guild_id)
    
    def is_paused(self, guild_id: int) -> bool:
        """
        检查是否暂停

        Args:
            guild_id: 服务器ID

        Returns:
            如果暂停则返回True
        """
        return self.voice_manager.is_paused(guild_id)

    def get_current_playback_position(self, guild_id: int) -> Optional[float]:
        """
        获取当前播放位置

        Args:
            guild_id: 服务器ID

        Returns:
            当前播放位置（秒），如果没有播放则返回None
        """
        if guild_id not in self._playback_start_times:
            return None

        start_time = self._playback_start_times[guild_id]
        current_time = time.time()

        # 计算已播放时间
        elapsed = current_time - start_time

        # 减去暂停时间
        total_paused = self._total_paused_duration.get(guild_id, 0.0)

        # 如果当前暂停，添加当前暂停时长
        if guild_id in self._playback_paused_times:
            current_pause_duration = current_time - self._playback_paused_times[guild_id]
            total_paused += current_pause_duration

        return max(0.0, elapsed - total_paused)

    def pause_playback(self, guild_id: int) -> bool:
        """
        暂停播放并记录暂停时间

        Args:
            guild_id: 服务器ID

        Returns:
            暂停是否成功
        """
        success = self.voice_manager.pause_audio(guild_id)
        if success and guild_id in self._playback_start_times:
            # 记录暂停开始时间
            self._playback_paused_times[guild_id] = time.time()
            self.logger.debug(f"记录暂停时间 - 服务器 {guild_id}")
        return success

    def resume_playback(self, guild_id: int) -> bool:
        """
        恢复播放并更新暂停时长

        Args:
            guild_id: 服务器ID

        Returns:
            恢复是否成功
        """
        success = self.voice_manager.resume_audio(guild_id)
        if success and guild_id in self._playback_paused_times:
            # 计算暂停时长并累加
            pause_start = self._playback_paused_times[guild_id]
            pause_duration = time.time() - pause_start

            if guild_id not in self._total_paused_duration:
                self._total_paused_duration[guild_id] = 0.0
            self._total_paused_duration[guild_id] += pause_duration

            # 清除暂停开始时间
            del self._playback_paused_times[guild_id]

            self.logger.debug(f"恢复播放，累计暂停时长: {self._total_paused_duration[guild_id]:.1f}秒 - 服务器 {guild_id}")
        return success
    
    async def _start_playback_if_needed(self, guild_id: int) -> None:
        """如果需要，开始播放下一首歌曲"""
        if guild_id in self._playback_tasks:
            return  # 已经有播放任务在运行

        # 新播放任务启动，取消空闲断开倒计时
        self._cancel_disconnect_task(guild_id)

        # 创建播放任务
        task = asyncio.create_task(self._playback_loop(guild_id))
        self._playback_tasks[guild_id] = task
    
    async def _playback_loop(self, guild_id: int) -> None:
        """播放循环"""
        try:
            queue_manager = self.get_queue_manager(guild_id)
            
            while True:
                # 获取下一首歌曲 - 这里正确使用 get_next_song 来实际推进队列
                # 注意：只有在这里才应该调用 get_next_song，其他地方应该使用 peek_next_song
                song = await queue_manager.get_next_song()
                if not song:
                    break  # 队列为空
                
                # 检查添加歌曲至队列的用户是否仍在语音频道，不在则跳过
                # 处理 MockMember（已离开服务器的用户）和真实用户
                requester_name = getattr(song.requester, 'name', song.requester.display_name)
                is_mock_member = not hasattr(song.requester, 'guild') or song.requester.__class__.__name__ == 'MockMember'

                if is_mock_member or not song.requester.voice or not song.requester.voice.channel:
                    if is_mock_member:
                        self.logger.info(f"点歌人 {requester_name} 已离开服务器，跳过歌曲: {song.title}")
                    else:
                        self.logger.info(f"点歌人 {requester_name} 不在语音频道，跳过歌曲: {song.title}")

                    # 获取文本频道ID用于发送通知
                    text_channel_id = self.get_text_channel_id(guild_id)
                    if text_channel_id:
                        asyncio.create_task(
                            self._trigger_event("song_requester_absent_skip", guild_id=guild_id, channel_id=text_channel_id, song=song)
                        )
                    else:
                        self.logger.warning(f"⚠️ 服务器 {guild_id} 没有设置文本频道，无法发送跳过通知")

                    # 取歌时已设为当前歌曲并通知重复检测器，跳过需成对清理，避免状态残留
                    await queue_manager.clear_current_song(song)
                    continue

                # 下载并播放（下载成功后的播放失败会有限重试，内部统一清理状态与临时文件）
                await self._play_song_with_retry(guild_id, song)

                # 主动停止后退出循环
                if self._control_actions.get(guild_id) == "stop":
                    break

        except asyncio.CancelledError:
            self.logger.info(f"服务器 {guild_id} 播放循环被取消")
            raise
        except Exception as e:
            self.logger.error(f"播放循环出错 - 服务器 {guild_id}: {e}", exc_info=True)
        finally:
            # 清理播放任务
            if guild_id in self._playback_tasks:
                del self._playback_tasks[guild_id]
            self._control_actions.pop(guild_id, None)
            # 队列播完但语音仍连接：启动空闲自动断开倒计时（来点歌会取消）
            if self.voice_manager.is_connected(guild_id):
                self._schedule_disconnect(guild_id)
    
    async def _play_audio_file(self, guild_id: int, file_path: str, song: SongInfo) -> Tuple[bool, bool]:
        """
        播放本地音频文件。

        Returns:
            (播放是否完成, 失败时是否值得重试)
        """
        # 本地文件不需要重连参数，仅应用输出选项（如 -vn）
        ffmpeg_options = self.config.get_ffmpeg_options() if self.config else '-vn'
        audio_source = discord.FFmpegPCMAudio(file_path, options=ffmpeg_options)
        return await self._play_audio_source(guild_id, audio_source, song)

    async def _play_audio_source(self, guild_id: int, audio_source: discord.AudioSource, song: SongInfo) -> Tuple[bool, bool]:
        """
        播放已创建的音频源（统一核心，两个播放入口共享）。

        结束语义判定：主动停止/跳歌（_control_actions）> 播放错误 > 自然完成。
        discord.py 的 stop() 与自然结束都以 error=None 触发 after 回调，
        必须靠显式控制标记区分，否则跳歌会被误判为播放失败。

        Returns:
            (播放是否完成, 失败时是否值得重试)
        """
        playback_finished = asyncio.Event()
        playback_error: Dict[str, Optional[Exception]] = {"error": None}
        loop = asyncio.get_running_loop()

        def after_playing(error):
            # 本回调由 Discord 音频播放线程触发；Event 只能在事件循环线程操作。
            # 状态清理移到本方法的 finally（事件循环线程），回调只负责传递错误
            def _finalize():
                playback_error["error"] = error
                playback_finished.set()
            try:
                loop.call_soon_threadsafe(_finalize)
            except RuntimeError:
                # 事件循环已关闭（机器人关闭中），直接在当前线程收尾
                _finalize()

        try:
            if not await self.voice_manager.play_audio(guild_id, audio_source, after_playing):
                return False, True  # 播放启动失败（如语音连接中断），值得重试

            # 记录播放开始时间
            self._playback_start_times[guild_id] = time.time()
            self._total_paused_duration[guild_id] = 0.0
            self._playback_paused_times.pop(guild_id, None)

            self.logger.info(f"正在播放: {song.title}")

            # 触发歌曲信息显示事件
            text_channel_id = self.get_text_channel_id(guild_id)
            if text_channel_id:
                asyncio.create_task(
                    self._trigger_event("show_song_info", guild_id=guild_id, channel_id=text_channel_id, song=song)
                )
            else:
                self.logger.warning(f"⚠️ 服务器 {guild_id} 没有设置文本频道，无法显示歌曲信息")

            # 检查下一首歌曲的点歌人状态并发送通知（如果配置启用）
            await self._check_and_notify_next_song(guild_id)

            # 等待播放完成
            await playback_finished.wait()

            # 结束语义判定并消费控制标记（主动停止 > 主动跳歌 > 播放错误 > 自然完成）。
            # 必须 pop 消费：检查后残留会污染下一首歌曲的播放
            action = self._control_actions.pop(guild_id, None)
            if action == "stop":
                return False, False
            if action == "skip":
                return True, False  # 跳歌视为完成，不重试
            # 播放出错：值得重试
            if playback_error["error"] is not None:
                self.logger.error(f"播放出错: {playback_error['error']}")
                return False, True
            # 自然完成
            return True, False

        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.error(f"播放音频失败: {e}", exc_info=True)
            return False, True
        finally:
            # 时间跟踪收尾（事件循环线程）。重复检测/当前歌曲状态的清理由
            # _play_song_with_retry 的 finally 统一负责，避免双重通知
            self._cleanup_playback_tracking(guild_id)

    async def _play_song_with_retry(self, guild_id: int, song: SongInfo) -> None:
        """
        下载并播放歌曲，播放失败时按指数退避有限重试。

        失败分层处理（从根本上区分可自愈与不可自愈）：
        - 流级断流：ffmpeg -reconnect 参数在传输层自愈，不进入重试
        - 下载失败：不重试（源站 4xx/5xx 重试无意义，网络超时已由 aiohttp 超时控制）
        - 播放启动/播放中出错：重试；URL 源每次重试重新解析直链，规避网易云链接过期
        - 主动跳歌/停止：通过 _control_actions 标记识别，绝不触发重试
        """
        queue_manager = self.get_queue_manager(guild_id)
        audio_path: Optional[str] = None

        try:
            # 下载（仅一次；Catbox 等流式源不下载，直接返回直链信息）
            success, audio_info, error = await self.audio_provider_factory.download_audio(song.url)
            if not success or not audio_info:
                self.logger.error(f"下载音频失败 - {song.title}: {error}")
                return

            if audio_info.file_path and os.path.exists(audio_info.file_path):
                audio_path = audio_info.file_path

            attempts = len(self._play_retry_delays) + 1
            for attempt in range(attempts):
                # 主动控制检查（消费标记：跳歌/停止绝不重试）
                action = self._control_actions.pop(guild_id, None)
                if action == "stop":
                    return
                if action == "skip":
                    self.logger.debug(f"歌曲被跳过，不重试 - {song.title}")
                    return

                if audio_path:
                    played, retriable = await self._play_audio_file(guild_id, audio_path, song)
                else:
                    played, retriable = await self._play_audio_url(guild_id, song.url, song)

                if played or not retriable:
                    return

                if attempt < attempts - 1:
                    delay = self._play_retry_delays[attempt]
                    self.logger.warning(
                        f"播放失败，{delay:.0f} 秒后重试 - 服务器 {guild_id}: {song.title} "
                        f"（第 {attempt + 1}/{attempts} 次）"
                    )
                    await asyncio.sleep(delay)

            self.logger.error(f"播放多次失败，放弃当前歌曲 - 服务器 {guild_id}: {song.title}")

        finally:
            # 统一收尾：清理当前歌曲状态（含重复检测跟踪）与本次临时文件
            try:
                await queue_manager.clear_current_song(song)
            except Exception as e:
                self.logger.warning(f"清理当前歌曲状态失败: {e}")
            if audio_path:
                self._current_audio_files[guild_id] = audio_path
                await self._cleanup_current_audio(guild_id)

    def _schedule_disconnect(self, guild_id: int) -> None:
        """队列空闲后安排自动断开倒计时（music.auto_disconnect_timeout 的实际实现）"""
        self._cancel_disconnect_task(guild_id)
        timeout = self.config.get_music_auto_disconnect_timeout() if self.config else 300
        if timeout <= 0:
            return
        self.logger.info(f"服务器 {guild_id} 队列已空，{timeout} 秒后自动断开语音")
        task = asyncio.create_task(self._disconnect_after_timeout(guild_id, timeout))
        self._disconnect_tasks[guild_id] = task
        task.add_done_callback(lambda t: self._disconnect_tasks.pop(guild_id, None))

    def _cancel_disconnect_task(self, guild_id: int) -> None:
        """取消自动断开倒计时（来点歌/连接频道等活跃信号时调用）"""
        task = self._disconnect_tasks.pop(guild_id, None)
        if task and not task.done():
            task.cancel()
            self.logger.debug(f"服务器 {guild_id} 的自动断开倒计时已取消")

    async def _disconnect_after_timeout(self, guild_id: int, timeout: int) -> None:
        """倒计时结束后断开空闲语音连接（期间恢复活跃则不动作）"""
        try:
            await asyncio.sleep(timeout)
            queue_manager = self.get_queue_manager(guild_id)
            if (
                self.voice_manager.is_connected(guild_id)
                and queue_manager.get_queue_length() == 0
                and not self.is_playing(guild_id)
            ):
                self.logger.info(f"服务器 {guild_id} 空闲超时，断开语音连接")
                await self.voice_manager.disconnect_from_guild(guild_id)
                # 告别消息
                text_channel_id = self.get_text_channel_id(guild_id)
                if text_channel_id:
                    try:
                        channel = self.bot.get_channel(text_channel_id) or await self.bot.fetch_channel(text_channel_id)
                        if channel:
                            await channel.send("🎵 队列已空，我先离开啦~ 下次见！")
                    except (discord.NotFound, discord.Forbidden):
                        self.logger.warning(f"无法在服务器 {guild_id} 的频道 {text_channel_id} 发送告别消息")
            else:
                self.logger.debug(f"服务器 {guild_id} 倒计时期间恢复活跃，取消断开")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"自动断开任务出错 - 服务器 {guild_id}: {e}", exc_info=True)


    async def _resolve_playable_url(self, url: str) -> Optional[str]:
        """
        解析URL为可播放的直链

        对于网易云音乐的规范化URL，需要调用 resolve_playable_url 方法
        对于其他URL，直接返回原始URL

        Args:
            url: 原始URL（可能是规范化URL）

        Returns:
            可播放的直链URL，解析失败时返回None
        """
        try:
            # 检测URL对应的提供者
            provider = self.audio_provider_factory.detect_provider_for_url(url)

            if provider and provider.name.lower() == 'netease':
                # 对于网易云音乐，需要解析规范化URL为可播放直链
                self.logger.debug(f"解析网易云规范化URL: {url}")

                # 检查是否有 resolve_playable_url 方法（新版本的 NetEaseProvider）
                if hasattr(provider, 'resolve_playable_url'):
                    playable_url = await provider.resolve_playable_url(url)
                    if playable_url:
                        self.logger.debug(f"成功解析网易云播放链接: {url} -> {playable_url[:100]}...")
                        return playable_url
                    else:
                        self.logger.error(f"网易云播放链接解析失败: {url}")
                        return None
                else:
                    # 兼容旧版本的 NetEaseProvider，直接使用原始URL
                    self.logger.debug(f"使用兼容模式，直接返回原始URL: {url}")
                    return url
            else:
                # 对于其他提供者（YouTube、Catbox等），直接使用原始URL
                self.logger.debug(f"非网易云URL，直接使用原始URL: {url}")
                return url

        except Exception as e:
            self.logger.error(f"解析播放URL时出错: {e}", exc_info=True)
            return None

    async def _play_audio_url(self, guild_id: int, url: str, song: SongInfo) -> Tuple[bool, bool]:
        """
        播放音频URL。

        网易云规范化URL每次播放前重新解析为直链（规避直链短时效过期）；
        URL 流式播放应用 ffmpeg 重连参数，流级断流在传输层自愈。

        Returns:
            (播放是否完成, 失败时是否值得重试)
        """
        playable_url = await self._resolve_playable_url(url)
        if not playable_url:
            self.logger.error(f"无法解析播放链接: {url}")
            return False, False  # 解析失败多为源站问题，重试无意义

        self.logger.debug(f"使用播放链接: {playable_url}")

        if self.config:
            before_options = self.config.get_ffmpeg_before_options()
            ffmpeg_options = self.config.get_ffmpeg_options()
        else:
            before_options = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
            ffmpeg_options = '-vn'

        audio_source = discord.FFmpegPCMAudio(
            playable_url,
            before_options=before_options,
            options=ffmpeg_options
        )
        return await self._play_audio_source(guild_id, audio_source, song)
    
    def _cleanup_playback_tracking(self, guild_id: int) -> None:
        """清理播放时间跟踪"""
        if guild_id in self._playback_start_times:
            del self._playback_start_times[guild_id]
        if guild_id in self._playback_paused_times:
            del self._playback_paused_times[guild_id]
        if guild_id in self._total_paused_duration:
            del self._total_paused_duration[guild_id]

    async def _cleanup_current_audio(self, guild_id: int) -> None:
        """清理当前音频文件"""
        if guild_id in self._current_audio_files:
            file_path = self._current_audio_files[guild_id]
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    self.logger.debug(f"清理音频文件: {file_path}")
            except Exception as e:
                self.logger.warning(f"清理音频文件失败: {e}")
            finally:
                del self._current_audio_files[guild_id]

    async def _check_and_notify_next_song(self, guild_id: int) -> None:
        """
        检查下首和下下首歌曲的点歌人状态并发送通知（如果配置启用）

        这是一个可配置的功能，允许服务器管理员控制是否向缺席用户发送
        "轮到你的歌了"的提醒通知。

        Args:
            guild_id: 服务器ID
        """
        try:
            # 检查配置是否启用缺席用户通知
            notify_absent_users = True  # 默认启用
            if self.config:
                notify_absent_users = self.config.is_notify_absent_users_enabled()

            if not notify_absent_users:
                self.logger.debug(f"🔕 缺席用户通知已禁用 - 服务器 {guild_id}")
                return

            # 查看下下首歌曲（不从队列中移除）- 修复队列同步问题
            # 如果是原来的下首歌曲会导致没有通知的连锁反应
            queue_manager = self.get_queue_manager(guild_id)
            next_song = queue_manager.peek_next_song() # 获取下首
            next_2_song = queue_manager.peek_next_song(2)  # 获取下下首歌曲

            if not next_song:
                self.logger.debug(f"📭 没有下一首歌曲 - 服务器 {guild_id}")
                return

            # 处理 MockMember（已离开服务器的用户）和真实用户
            requester_name = getattr(next_song.requester, 'name', next_song.requester.display_name)
            is_mock_member = not hasattr(next_song.requester, 'guild') or next_song.requester.__class__.__name__ == 'MockMember'
            requester2_name = getattr(next_2_song.requester, 'name', next_2_song.requester.display_name) if next_2_song else "未知"
            is_mock_member2 = not hasattr(next_2_song.requester, 'guild') or next_2_song.requester.__class__.__name__ == 'MockMember'

            self.logger.debug(f"🔍 检查下一首歌曲的点歌人状态: {next_song.title} - {requester_name}")
            self.logger.debug(f"🔍 检查下下首歌曲的点歌人状态: {next_2_song.title} - {requester2_name}")

            # 检查下一首歌曲的点歌人是否在语音频道
            if is_mock_member or not next_song.requester.voice or not next_song.requester.voice.channel:
                if is_mock_member:
                    self.logger.debug(f"📢 下一首歌曲的点歌人 {requester_name} 已离开服务器，发送提醒通知")
                else:
                    self.logger.debug(f"📢 下一首歌曲的点歌人 {requester_name} 不在语音频道，发送提醒通知")

                # 获取文本频道ID用于发送通知
                text_channel_id = self.get_text_channel_id(guild_id)
                if text_channel_id:
                    asyncio.create_task(
                        self._trigger_event(
                            "your_song_notification",
                            guild_id=guild_id,
                            channel_id=text_channel_id,
                            song=next_song,
                            interval=1
                        )
                    )
                else:
                    self.logger.warning(f"⚠️ 服务器 {guild_id} 没有设置文本频道，无法发送提醒通知")
            else:
                self.logger.debug(f"✅ 下一首歌曲的点歌人 {next_song.requester.name} 在语音频道中")

            if is_mock_member2 or not next_2_song.requester.voice or not next_2_song.requester.voice.channel:
                if is_mock_member:
                    self.logger.debug(f"📢 下下首歌曲的点歌人 {requester2_name} 已离开服务器，发送提醒通知")
                else:
                    self.logger.debug(f"📢 下下首歌曲的点歌人 {requester2_name} 不在语音频道，发送提醒通知")
                
                text_channel_id = self.get_text_channel_id(guild_id)
                if text_channel_id:
                    asyncio.create_task(
                        self._trigger_event(
                            "your_song_notification",
                            guild_id=guild_id,
                            channel_id=text_channel_id,
                            song=next_2_song,
                            interval=2
                        )
                    )
            
        except Exception as e:
            self.logger.error(f"❌ 检查下一首歌曲通知时出错 - 服务器 {guild_id}: {e}", exc_info=True)

    async def initialize_persistence(self) -> None:
        """初始化持久化系统并恢复所有队列状态"""
        if not self.persistence_manager:
            self.logger.info("队列持久化未启用")
            return

        try:
            self.logger.info("🔄 开始恢复队列状态...")
            
            # 获取所有有保存状态的服务器
            guild_ids = await self.persistence_manager.get_all_guild_ids()
            if not guild_ids:
                self.logger.info("没有找到需要恢复的队列状态")
                return

            restored_count = 0
            for guild_id in guild_ids:
                try:
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        self.logger.warning(f"无法找到服务器 {guild_id}，跳过恢复")
                        continue

                    # 获取队列管理器并恢复状态
                    queue_manager = self.get_queue_manager(guild_id)
                    success = await queue_manager.restore_from_persistence(guild)
                    
                    if success:
                        restored_count += 1
                        self.logger.info(f"✅ 服务器 {guild_id} 队列状态恢复成功")

                except Exception as e:
                    self.logger.error(f"恢复服务器 {guild_id} 队列状态时出错: {e}")

            self.logger.info(f"队列恢复完成: {restored_count}/{len(guild_ids)} 个服务器成功恢复")

        except Exception as e:
            self.logger.error(f"初始化持久化系统时出错: {e}")

    async def manual_save(self, guild_id: int) -> None:
        """
        手动保存当前服务器的队列状态到持久化存储

        Args:
            guild_id: 服务器ID
        """
        if not self.persistence_manager:
            self.logger.info("队列持久化未启用，无法手动保存")
            return

        try:
            queue_manager = self.get_queue_manager(guild_id)
            await queue_manager._save_state()
            self.logger.info(f"手动保存服务器 {guild_id} 的队列状态成功")
        except Exception as e:
            self.logger.error(f"手动保存服务器 {guild_id} 队列状态失败: {e}")