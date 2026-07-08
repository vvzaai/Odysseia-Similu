# Odysseia-Similu 音乐机器人

专为类脑/Odysseia Discord 社区打造的音乐播放机器人，基于 Discord Slash Commands 提供点歌、队列和播放控制体验。支持 YouTube、网易云音乐、Bilibili、Catbox 和 SoundCloud 音频来源，具备智能重复检测系统。

## 功能特性

### 🎵 核心音乐功能
- 🎵 支持 YouTube、网易云音乐、Bilibili、Catbox 和 SoundCloud 音频播放
- 🧭 使用 Discord Slash Commands，不需要 Message Content 等 Privileged Gateway Intents
- 📋 智能音乐队列管理
- ⏭️ 跳过投票和播放进度显示
- 📊 实时播放进度显示
- 🔊 语音频道自动连接
- 🎧 支持多种音频格式（MP3、WAV、OGG、M4A、FLAC、AAC、OPUS、WMA）

### 🚫 智能重复检测系统 ⭐ **新功能**
- 🎯 **用户特定重复检测**: 防止同一用户重复添加相同歌曲
- 🧠 **智能歌曲识别**: 自动识别歌曲变体（官方版本、HD版本、歌词版本等）
- 👥 **多用户友好**: 不同用户可以添加相同歌曲
- 📏 **队列长度阈值**: 短队列时允许重复，长队列时保持保护
- 🔄 **自动清理**: 歌曲播放完成后自动清理跟踪数据
- 💬 **清晰反馈**: 提供中文用户友好的错误提示

## 系统要求

- Python 3.8 或更高版本
- FFmpeg（必须安装并添加到系统 PATH）
- Discord 机器人令牌
- 稳定的网络连接

## 安装步骤

1. 克隆仓库：
   ```bash
   git clone https://github.com/Darkatse/Odysseia-Similu.git
   cd Odysseia-Similu
   ```

2. 安装 Python 依赖包：
   ```bash
   pip install -r requirements.txt
   ```

3. 创建配置文件：
   ```bash
   cp config/config.yaml.example config/config.yaml
   ```

4. 编辑配置文件并添加你的 Discord 机器人令牌：
   ```yaml
   discord:
     token: "你的_DISCORD_机器人_令牌"
   ```

## 配置说明

`config/config.yaml` 文件包含机器人的所有配置选项：

### 基础配置
- `discord.token`: Discord 机器人令牌
- `download.temp_dir`: 临时文件存储目录

### 音乐播放配置
- `music.enabled`: 是否启用音乐功能（默认：true）
- `music.max_queue_size`: 每个服务器的最大队列长度（默认：100）
- `music.max_song_duration`: 单首歌曲最大时长（秒，默认：3600）
- `music.auto_disconnect_timeout`: 无活动自动断开时间（秒，默认：300）
- `music.volume`: 默认播放音量（0.0-1.0，默认：0.5）

### 重复检测配置 ⭐ **新功能**
- `duplicate_detection.queue_length_threshold`: 队列长度阈值（默认：5）
  - 当队列长度小于此值时，允许用户重复添加歌曲
  - 当队列长度大于等于此值时，保持重复检测保护
  - **小型服务器** (< 20用户): 推荐设置为 3
  - **中型服务器** (20-100用户): 推荐设置为 5（默认）
  - **大型服务器** (> 100用户): 推荐设置为 7

### 日志配置
- `logging.level`: 日志级别（DEBUG、INFO、WARNING、ERROR、CRITICAL）
- `logging.file`: 日志文件路径
- `logging.max_size`: 日志文件最大大小（字节）
- `logging.backup_count`: 保留的备份日志文件数量

## 使用方法

1. 启动机器人：
   ```bash
   python main.py
   ```

2. 在 Discord 中使用以下命令：
   - `/点歌 链接或名字:<链接或关键词>`: 搜索并添加歌曲到播放队列
   - `/music link_or_name:<link or keywords>`: 英文点歌入口
   - `/歌曲队列`: 显示当前播放队列
   - `/我的队列`: 查看自己的队列状态和预计播放时间
   - `/歌曲跳过`: 投票跳过当前歌曲
   - `/歌曲进度`: 显示当前歌曲和实时播放进度
   - `/随机抽卡`: 从歌曲历史中随机抽取歌曲
   - `/设置抽卡来源`: 设置随机抽卡的歌曲来源
   - `/延迟`: 检查机器人延迟和连接质量
   - `/帮助`: 显示机器人信息和使用指南

## 项目结构

```
Odysseia-Similu/
├── config/
│   └── config.yaml.example    # 配置文件模板
├── docs/                      # 项目文档
│   ├── architecture.md        # 技术架构文档
│   ├── api.md                # API 文档
│   ├── configuration.md       # 配置指南
│   ├── development.md         # 开发指南
│   └── prd.md                # 产品需求文档
├── similubot/
│   ├── bot.py                 # 主要机器人实现
│   ├── app_commands/          # Discord Slash Commands
│   │   ├── core/              # 命令注册和基础设施
│   │   ├── music/             # 音乐搜索、队列、播放控制命令
│   │   ├── card_draw/         # 随机抽卡功能
│   │   └── general/           # 通用命令
│   ├── core/
│   │   ├── interfaces.py      # 核心接口定义
│   │   └── event_handler.py   # 生命周期事件处理器
│   ├── playback/              # 播放引擎
│   │   ├── playback_engine.py # 播放引擎核心
│   │   ├── voice_manager.py   # 语音连接管理
│   │   └── seek_manager.py    # 时间定位管理
│   ├── provider/              # 音频提供者
│   │   ├── provider_factory.py # 音频提供者工厂
│   │   ├── youtube_provider.py # YouTube 提供者
│   │   ├── bilibili_provider.py # Bilibili 提供者
│   │   ├── netease_provider.py # 网易云音乐提供者
│   │   ├── soundcloud_provider.py # SoundCloud 提供者
│   │   └── catbox_provider.py # Catbox 提供者
│   ├── queue/                 # 队列管理
│   │   ├── duplicate_detector.py # 重复检测和公平性限制
│   │   ├── queue_manager.py   # 队列管理器
│   │   ├── song.py           # 歌曲数据模型
│   │   └── persistence_manager.py # 持久化管理
│   ├── progress/
│   │   └── base.py           # 进度显示基类
│   └── utils/
│       ├── config_manager.py  # 配置管理
│       └── logger.py          # 日志功能
├── tests/                     # 单元测试
├── .gitignore
├── README.md
├── requirements.txt
└── main.py                    # 程序入口
```

## 开发相关

### 运行测试

```bash
pytest
```

### 音乐命令示例

```bash
# 播放 YouTube 视频
/点歌 链接或名字:https://www.youtube.com/watch?v=dQw4w9WgXcQ

# 播放 Catbox 音频文件
/点歌 链接或名字:https://files.catbox.moe/example.mp3

# 搜索网易云音乐
/点歌 链接或名字:周杰伦 稻香

# 查看播放队列
/歌曲队列

# 显示当前播放进度
/歌曲进度

# 跳过当前歌曲
/歌曲跳过
```

### 重复检测系统示例 ⭐ **新功能**

```bash
# 场景1: 短队列时允许重复添加
用户A: /点歌 链接或名字:https://www.youtube.com/watch?v=example
机器人: ✅ 已添加到队列位置 #1: 示例歌曲

用户A: /点歌 链接或名字:https://www.youtube.com/watch?v=example  # 队列仍然较短
机器人: ✅ 已添加到队列位置 #2: 示例歌曲  # 允许重复添加

# 场景2: 长队列时的重复保护
用户A: /点歌 链接或名字:https://www.youtube.com/watch?v=example
机器人: ✅ 已添加到队列位置 #6: 示例歌曲

用户A: /点歌 链接或名字:https://www.youtube.com/watch?v=example  # 队列已较长
机器人: ❌ 你已经请求过这首歌曲了！**示例歌曲** 已在队列中，请等待播放完成后再次请求。

# 场景3: 不同用户可以添加相同歌曲
用户A: /点歌 链接或名字:https://www.youtube.com/watch?v=example
机器人: ✅ 已添加到队列位置 #1: 示例歌曲

用户B: /点歌 链接或名字:https://www.youtube.com/watch?v=example  # 不同用户
机器人: ✅ 已添加到队列位置 #2: 示例歌曲  # 始终允许

# 场景4: 智能识别歌曲变体
用户A: /点歌 链接或名字:https://www.youtube.com/watch?v=example
机器人: ✅ 已添加到队列位置 #1: 示例歌曲

用户A: /点歌 链接或名字:https://www.youtube.com/watch?v=example_hd  # HD版本
机器人: ❌ 你已经请求过相似的歌曲了！**示例歌曲** 已在队列中，请等待播放完成后再次请求。
```

## 许可证

本项目采用 Apache 2.0 许可证 - 详情请参阅 LICENSE 文件。

## 致谢

### 核心依赖库
- [discord.py](https://github.com/Rapptz/discord.py) - Python Discord API 封装库
- [pytubefix](https://github.com/JuanBindez/pytubefix) - YouTube 视频下载库
- [FFmpeg](https://ffmpeg.org/) - 音频/视频处理工具
- [bilibili-api-python](https://github.com/nemo2011/bilibili-api) Python Bilibili API库

### 网易云音乐支持
- [保罗 API](https://api.paugram.com/help/netease) - 提供网易云音乐 API 代理服务，解决海外访问限制
- [pyncm](https://github.com/mos9527/pyncm) - Python 网易云音乐 API 库，为会员认证和加密算法提供参考实现

### SoundCloud下载支持
- [Soundcloud-lib](https://github.com/3jackdaws/soundcloud-lib) - Python SoundCloud API库，帮助实现下载功能

## 支持

如果在使用过程中遇到问题，请在 GitHub 上提交 Issue 或联系开发者。

---

**为类脑/Odysseia Discord 社区专门定制** 🎵
