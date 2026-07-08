"""重构后的 Odysseia-Similu 音乐机器人主实现"""
import logging
import os
from typing import Optional
import discord
from discord.ext import commands

# 核心模块
from similubot.core.event_handler import EventHandler
from similubot.core.dependency_container import DependencyContainer

# 新的 Slash Commands 架构
from similubot.app_commands.integration_example import setup_app_commands

# 新架构模块 - 使用重构后的播放引擎
from similubot.playback.playback_engine import PlaybackEngine
from similubot.adapters.music_player_adapter import MusicPlayerAdapter
from similubot.utils.config_manager import ConfigManager


def build_gateway_intents() -> discord.Intents:
    """Return the non-privileged gateway intents required by the bot."""
    intents = discord.Intents.none()
    intents.guilds = True
    intents.voice_states = True
    intents.reactions = True
    return intents


def disabled_text_command_prefix(_bot: commands.Bot, _message: discord.Message) -> tuple:
    """Disable the legacy prefix-command surface for the commands.Bot base class."""
    return ()


class SimiluBot:
    """
    Odysseia-Similu 音乐机器人主实现类。

    专为类脑/Odysseia Discord 社区打造的音乐播放机器人：
    - 支持 YouTube 视频和 Catbox 音频文件播放
    - 完整的音乐队列管理系统
    - 实时播放进度显示
    - 精确的时间定位功能
    - 模块化架构，易于维护和扩展
    """

    def __init__(self, config: ConfigManager):
        """
        Initialize the Discord bot with modern Slash Commands architecture.

        Args:
            config: Configuration manager
        """
        self.logger = logging.getLogger("similubot.bot")
        self.config = config

        # Initialize dependency injection container
        self.container = DependencyContainer()

        # Set up Discord bot with only non-privileged gateway data.
        self.bot = commands.Bot(
            command_prefix=disabled_text_command_prefix,
            intents=build_gateway_intents(),
            help_command=None  # We'll use our custom slash commands
        )

        # 存储对自身的引用，供事件处理器使用
        self.bot._similu_bot = self

        # Register dependencies and initialize core components
        self._register_dependencies()
        self._init_core_modules()

        # Setup event handlers
        self._setup_event_handlers()

        # 设置机器人启动时的初始化任务
        self.bot.add_listener(self._on_ready, 'on_ready')

        self.logger.info("🎵 音乐机器人初始化成功")

    def _register_dependencies(self) -> None:
        """
        注册依赖项到依赖注入容器

        定义组件间的依赖关系，确保按正确顺序初始化。
        """
        # 创建临时目录工厂函数
        def create_temp_dir() -> str:
            temp_dir = self.config.get('download.temp_dir', './temp')
            if not os.path.exists(temp_dir):
                os.makedirs(temp_dir)
                self.logger.debug(f"创建临时目录: {temp_dir}")
            return temp_dir

        # 播放引擎工厂函数
        def create_playback_engine(temp_dir: str) -> PlaybackEngine:
            return PlaybackEngine(
                bot=self.bot,
                temp_dir=temp_dir,
                config=self.config
            )

        # 音乐播放器适配器工厂函数
        def create_music_player_adapter(playback_engine: PlaybackEngine) -> MusicPlayerAdapter:
            return MusicPlayerAdapter(playback_engine)

        # 播放事件处理器工厂函数
        def create_playback_event(music_player_adapter: MusicPlayerAdapter):
            from similubot.playback.playback_event import PlaybackEvent
            return PlaybackEvent(music_player_adapter=music_player_adapter)

        # 注册依赖项（按依赖顺序）
        self.container.register_singleton("temp_dir", create_temp_dir)
        self.container.register_singleton("playback_engine", create_playback_engine, ["temp_dir"])
        self.container.register_singleton("music_player_adapter", create_music_player_adapter, ["playback_engine"])
        self.container.register_singleton("playback_event", create_playback_event, ["music_player_adapter"])

        # 验证依赖关系
        self.container.validate_dependencies()
        self.logger.debug("📝 依赖项注册完成")

    def _init_core_modules(self) -> None:
        """
        使用依赖注入容器初始化核心机器人模块

        通过依赖注入容器自动解析和初始化所有组件，
        确保依赖关系正确且避免初始化顺序问题。
        """
        try:
            # 解析所有核心依赖项
            self.logger.debug("🔧 开始解析核心依赖项...")

            # 按依赖顺序解析组件
            self.playback_engine = self.container.resolve("playback_engine")
            self.music_player = self.container.resolve("music_player_adapter")
            self.playback_event = self.container.resolve("playback_event")

            # 注册播放事件处理器到播放引擎
            self._register_playback_events_to_engine()

            self.logger.info("✅ 核心模块初始化完成")

        except Exception as e:
            self.logger.error(f"❌ 核心模块初始化失败: {e}", exc_info=True)
            raise RuntimeError(f"核心模块初始化失败: {e}") from e

    def _register_playback_events_to_engine(self) -> None:
        """
        将播放事件处理器注册到播放引擎

        此时所有依赖项都已通过依赖注入容器正确初始化，
        只需要将事件处理器的方法注册到播放引擎即可。
        """
        try:
            # 验证必要的组件已初始化
            if not self.playback_engine:
                raise RuntimeError("播放引擎未初始化")
            if not self.playback_event:
                raise RuntimeError("播放事件处理器未创建")

            # 注册事件处理器到播放引擎
            event_mappings = {
                "show_song_info": self.playback_event.show_song_info,
                "song_requester_absent_skip": self.playback_event.song_requester_absent_skip,
                "your_song_notification": self.playback_event.your_song_notification,
                "song_added_notification": self.playback_event.song_added_notification
            }

            for event_type, handler in event_mappings.items():
                self.playback_engine.add_event_handler(event_type, handler)
                self.logger.debug(f"📝 注册事件处理器: {event_type}")

            self.logger.info("✅ 播放事件处理器注册到引擎完成")

        except Exception as e:
            self.logger.error(f"❌ 播放事件处理器注册失败: {e}", exc_info=True)
            raise RuntimeError(f"播放事件处理器注册失败: {e}") from e

    async def _init_slash_commands(self) -> None:
        """初始化 Slash Commands 系统"""
        try:
            self.logger.debug("🔧 开始初始化 Slash Commands...")

            # 使用新的 app_commands 架构设置 Slash Commands
            self.app_commands_integration = await setup_app_commands(
                bot=self.bot,
                config=self.config,
                music_player=self.music_player
            )

            self.logger.info("✅ Slash Commands 初始化完成")

        except Exception as e:
            self.logger.error(f"❌ Slash Commands 初始化失败: {e}", exc_info=True)
            raise RuntimeError(f"Slash Commands 初始化失败: {e}") from e

    def _setup_event_handlers(self) -> None:
        """设置 Discord 事件处理器。"""
        # 初始化事件处理器（简化版，只处理基本事件）
        self.event_handler = EventHandler(bot=self.bot,)

        self.logger.debug("事件处理器设置完成")

    async def _on_ready(self) -> None:
        """机器人就绪时的初始化任务"""
        try:
            self.logger.info(f"🤖 机器人已就绪: {self.bot.user}")

            # 初始化 Slash Commands
            await self._init_slash_commands()

            # 同步 Slash Commands 到 Discord
            await self.app_commands_integration.sync_commands()
            self.logger.info("✅ Slash Commands 已同步到 Discord")

            # 初始化持久化系统并恢复队列状态
            if hasattr(self.music_player, 'initialize_persistence'):
                await self.music_player.initialize_persistence()
                self.logger.info("✅ 队列持久化系统初始化完成")

            # 初始化歌曲历史数据库（抽卡功能）
            await self._init_song_history_database()
            self.logger.info("✅ 歌曲历史数据库初始化完成")

        except Exception as e:
            self.logger.error(f"机器人就绪初始化失败: {e}", exc_info=True)

    async def _init_song_history_database(self) -> None:
        """初始化歌曲历史数据库"""
        try:
            from similubot.app_commands.card_draw.database import SongHistoryDatabase

            # 检查是否启用抽卡功能
            card_draw_config = self.config.get('card_draw', {})
            if not card_draw_config.get('enabled', True):
                self.logger.info("抽卡功能已禁用，跳过歌曲历史数据库初始化")
                return

            # 初始化数据库
            self.song_history_db = SongHistoryDatabase()
            success = await self.song_history_db.initialize()

            if success:
                self.logger.debug("歌曲历史数据库表结构创建成功")

                # 将数据库实例存储到容器中供其他组件使用
                if hasattr(self, 'container'):
                    self.container.register_singleton("song_history_database", lambda: self.song_history_db)
            else:
                self.logger.error("歌曲历史数据库初始化失败")

        except Exception as e:
            self.logger.error(f"初始化歌曲历史数据库时发生异常: {e}", exc_info=True)

    async def start(self, token: str) -> None:
        """
        Start the Discord bot.

        Args:
            token: Discord bot token
        """
        try:
            self.logger.info("🚀 启动音乐机器人...")
            await self.bot.start(token)
        except Exception as e:
            self.logger.error(f"启动机器人失败: {e}", exc_info=True)
            raise

    async def close(self) -> None:
        """关闭 Discord 机器人并清理资源。"""
        try:
            self.logger.info("🛑 正在关闭音乐机器人...")

            # 清理音乐播放器（如果可用）
            if hasattr(self, 'music_player'):
                await self.music_player.cleanup_all()

            # 清理 Slash Commands 集成（如果可用）
            if hasattr(self, 'app_commands_integration'):
                await self.app_commands_integration.cleanup()

            await self.bot.close()
            self.logger.info("✅ 音乐机器人关闭成功")
        except Exception as e:
            self.logger.error(f"关闭过程中发生错误: {e}", exc_info=True)

    def run(self, token: str) -> None:
        """
        运行 Discord 机器人（阻塞式）。

        Args:
            token: Discord 机器人令牌
        """
        try:
            self.bot.run(token)
        except KeyboardInterrupt:
            self.logger.info("用户停止了机器人")
        except Exception as e:
            self.logger.error(f"机器人崩溃: {e}", exc_info=True)
            raise

    def get_stats(self) -> dict:
        """
        获取机器人统计信息。

        Returns:
            包含机器人统计信息的字典
        """
        stats = {
            "bot_ready": self.bot.is_ready(),
            "guild_count": len(self.bot.guilds),
            "user_count": sum(guild.member_count or 0 for guild in self.bot.guilds),
            "slash_commands_enabled": hasattr(self, 'app_commands_integration'),
            "music_enabled": hasattr(self, 'music_player') and self.music_player is not None
        }

        return stats

    def get_registered_commands(self) -> dict:
        """
        获取已注册的 Slash Commands 列表。

        Returns:
            已注册命令的字典
        """
        if hasattr(self, 'app_commands_integration'):
            return {
                "slash_commands": [cmd.name for cmd in self.bot.tree.get_commands()],
                "command_count": len(self.bot.tree.get_commands())
            }
        return {"slash_commands": [], "command_count": 0}

    def is_ready(self) -> bool:
        """
        检查机器人是否就绪。

        Returns:
            如果机器人就绪返回 True，否则返回 False
        """
        return self.bot.is_ready()

    @property
    def user(self) -> Optional[discord.ClientUser]:
        """获取机器人用户。"""
        return self.bot.user

    @property
    def latency(self) -> float:
        """获取机器人延迟。"""
        return self.bot.latency

    @property
    def guilds(self) -> list:
        """获取机器人所在的服务器列表。"""
        return list(self.bot.guilds)
