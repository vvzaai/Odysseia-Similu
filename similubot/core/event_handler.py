"""Odysseia-Similu 音乐机器人事件处理器。"""
import logging
import discord
from discord.ext import commands


class EventHandler:
    """
    Odysseia-Similu 音乐机器人事件处理器。

    管理机器人生命周期事件。
    """

    def __init__(
        self,
        bot: commands.Bot
    ):
        """
        初始化事件处理器。

        Args:
            bot: Discord 机器人实例
        """
        self.logger = logging.getLogger("similubot.events")
        self.bot = bot

        # 注册事件处理器
        self._register_events()

    def _register_events(self) -> None:
        """注册 Discord 事件处理器。"""
        @self.bot.event
        async def on_ready():
            await self._on_ready()

        self.logger.debug("事件处理器注册完成")

    async def _on_ready(self) -> None:
        """处理机器人就绪事件。"""
        if self.bot.user is None:
            self.logger.error("机器人用户在 on_ready 事件中为 None")
            return

        self.logger.info(f"🎵 音乐机器人已就绪。登录为 {self.bot.user.name} ({self.bot.user.id})")

        # 设置机器人状态
        activity = discord.Activity(
            type=discord.ActivityType.listening,
            name="🎵 /点歌 | /music"
        )
        await self.bot.change_presence(activity=activity)

        self.logger.info("✅ Odysseia-Similu 音乐机器人已准备就绪")

    def get_event_stats(self) -> dict:
        """
        Get event handling statistics.

        Returns:
            Dictionary with event statistics
        """
        return {
            "bot_ready": self.bot.is_ready(),
            "bot_user": str(self.bot.user) if self.bot.user else None,
            "guild_count": len(self.bot.guilds),
            "user_count": sum(guild.member_count or 0 for guild in self.bot.guilds)
        }
