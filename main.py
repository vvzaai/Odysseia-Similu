# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
Odysseia-Similu 音乐机器人 - 专为类脑/Odysseia Discord 社区打造的音乐播放机器人

主程序入口点，负责配置加载、机器人初始化和优雅的启动/关闭处理。
支持 YouTube 视频和 Catbox 音频文件播放，提供完整的音乐队列管理功能。
"""
import logging

from similubot.bot import SimiluBot
from similubot.utils.config_manager import ConfigManager
from similubot.utils.logger import setup_logger

def main() -> int:
    """
    Odysseia-Similu 音乐机器人主入口函数。

    处理机器人的完整生命周期，包括：
    - 日志系统设置
    - 配置加载和验证
    - 机器人初始化
    - 优雅的启动和关闭

    Returns:
        int: 退出代码（0表示成功，1表示错误）
    """
    # Load configuration first to get logging settings
    config = ConfigManager()

    # Set up logging with configuration values
    setup_logger(
        log_level=config.get_log_level(),
        log_file=config.get_log_file(),
        max_size=config.get_log_max_size(),
        backup_count=config.get_log_backup_count()
    )
    logger = logging.getLogger("similubot")

    logger.info("=" * 60)
    logger.info("🎵 Odysseia-Similu 音乐机器人启动中...")
    logger.info("=" * 60)
    logger.info("✅ 配置文件加载成功")
    logger.debug(f"日志配置完成 - 级别: {config.get_log_level()}, 文件: {config.get_log_file()}")
    
    try:
        # 从配置中获取 Discord 令牌
        logger.info("正在获取 Discord 机器人令牌...")
        try:
            discord_token = config.get_discord_token()
            logger.info("✅ Discord 令牌获取成功")
        except ValueError as e:
            logger.error(f"❌ Discord 令牌配置错误: {e}")
            logger.error("请检查 config/config.yaml 文件并确保 Discord 令牌已正确设置")
            logger.error("示例: discord.token: '你的_实际_机器人_令牌'")
            return 1

        # 初始化机器人
        logger.info("正在初始化音乐机器人...")
        bot = SimiluBot(config)
        logger.info("✅ 音乐机器人初始化成功")

        # 记录机器人配置摘要
        _log_bot_configuration(logger, config, bot)

        # 运行机器人
        logger.info("🚀 启动音乐机器人...")
        logger.info("按 Ctrl+C 停止机器人")
        bot.run(discord_token)

    except KeyboardInterrupt:
        logger.info("🛑 用户停止了机器人 (Ctrl+C)")
        logger.info("再见！")
        return 0
    except FileNotFoundError as e:
        logger.error(f"❌ 配置文件错误: {e}")
        logger.error("请确保 config/config.yaml 文件存在且配置正确")
        return 1
    except Exception as e:
        logger.error(f"❌ 启动音乐机器人时发生意外错误: {e}", exc_info=True)
        logger.error("请检查上面的日志以获取更多详细信息")
        return 1

    return 0


def _log_bot_configuration(logger: logging.Logger, config: ConfigManager, bot: SimiluBot) -> None:
    """
    记录机器人配置摘要，用于调试和监控。

    Args:
        logger: 日志记录器实例
        config: 配置管理器
        bot: SimiluBot 实例
    """
    logger.info("📋 机器人配置摘要:")
    logger.info("   命令入口: Discord Slash Commands (/)")
    logger.info("   Privileged Gateway Intents: 未请求")
    logger.info(f"   音乐功能: {'✅ 已启用' if config.get('music.enabled', True) else '❌ 已禁用'}")
    logger.info(f"   最大队列长度: {config.get('music.max_queue_size', 100)}")
    logger.info(f"   最大歌曲时长: {config.get('music.max_song_duration', 3600)} 秒")
    logger.info(f"   自动断开超时: {config.get('music.auto_disconnect_timeout', 300)} 秒")
    logger.info(f"   默认音量: {config.get('music.volume', 0.5)}")
    logger.info(f"   临时文件目录: {config.get('download.temp_dir', './temp')}")
    logger.info("=" * 60)

if __name__ == "__main__":
    exit(main())
