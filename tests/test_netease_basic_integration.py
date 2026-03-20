"""
网易云音乐基础集成测试 - 验证核心功能正常工作

简化的集成测试，专注于验证各组件能够正确协作。
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch

from similubot.core.interfaces import NetEaseSearchResult, AudioInfo
from similubot.utils.netease_search import NetEaseSearchClient
from similubot.provider.netease_provider import NetEaseProvider
from similubot.ui.button_interactions import InteractionManager, InteractionResult


class TestBasicNetEaseIntegration:
    """基础NetEase集成测试"""

    def test_netease_search_result_creation(self):
        """测试NetEase搜索结果创建"""
        result = NetEaseSearchResult(
            song_id="517567145",
            title="初登校",
            artist="橋本由香利",
            album="ひなこのーと COMPLETE SOUNDTRACK",
            cover_url="http://example.com/cover.jpg",
            duration=225
        )
        
        assert result.song_id == "517567145"
        assert result.title == "初登校"
        assert result.artist == "橋本由香利"
        assert result.get_display_name() == "初登校 - 橋本由香利"
        assert result.format_duration() == "3:45"

    def test_netease_provider_url_support(self):
        """测试NetEase提供者URL支持"""
        provider = NetEaseProvider("./test_temp")
        
        # 测试支持的URL
        supported_urls = [
            "https://music.163.com/song?id=517567145",
            "http://music.163.com/#/song?id=123456",
            "https://music.163.com/m/song?id=789012"
        ]
        
        for url in supported_urls:
            assert provider.is_supported_url(url), f"应该支持URL: {url}"
        
        # 测试不支持的URL
        unsupported_urls = [
            "https://www.youtube.com/watch?v=123",
            "https://example.com/song",
            ""
        ]
        
        for url in unsupported_urls:
            assert not provider.is_supported_url(url), f"不应该支持URL: {url}"

    def test_netease_provider_song_id_extraction(self):
        """测试歌曲ID提取"""
        provider = NetEaseProvider("./test_temp")
        
        test_cases = [
            ("https://music.163.com/song?id=517567145", "517567145"),
            ("http://music.163.com/#/song?id=123456", "123456"),
            ("invalid_url", None)
        ]
        
        for url, expected_id in test_cases:
            result = provider._extract_song_id(url)
            assert result == expected_id

    @pytest.mark.asyncio
    async def test_interaction_manager_embed_creation(self):
        """测试交互管理器嵌入消息创建"""
        manager = InteractionManager()
        
        search_result = NetEaseSearchResult(
            song_id="517567145",
            title="初登校",
            artist="橋本由香利",
            album="ひなこのート COMPLETE SOUNDTRACK",
            duration=225
        )
        
        # 测试确认嵌入消息
        confirmation_embed = manager._create_confirmation_embed(search_result)
        assert confirmation_embed.title == "🎵 找到歌曲"
        assert "是否添加这首歌曲到队列？" in confirmation_embed.description
        
        # 测试选择嵌入消息
        search_results = [search_result]
        selection_embed = manager._create_selection_embed(search_results)
        assert selection_embed.title == "🎵 搜索结果"
        assert "请选择要添加到队列的歌曲：" in selection_embed.description

    def test_netease_search_client_initialization(self):
        """测试NetEase搜索客户端初始化"""
        client = NetEaseSearchClient()

        assert client.search_api == "http://music.163.com/api/search/get"
        assert client.song_detail_api == "https://api.paugram.com/netease/"
        assert client.timeout.total == 10
        assert client.proxy_manager is not None
        assert client.headers["Accept-Encoding"] == "identity"

    def test_netease_search_client_url_generation(self):
        """测试NetEase搜索客户端URL生成"""
        client = NetEaseSearchClient()

        # 测试API URL（代理功能禁用时应该返回原始URL）
        api_url = client.get_playback_url("517567145", use_api=True)
        assert api_url == "https://api.paugram.com/netease/?id=517567145"

        # 测试直接URL（代理功能禁用时应该返回原始URL）
        direct_url = client.get_playback_url("517567145", use_api=False)
        assert direct_url == "https://music.163.com/song/media/outer/url?id=517567145.mp3"

    def test_netease_search_client_query_cleaning(self):
        """测试搜索查询清理"""
        client = NetEaseSearchClient()
        
        test_cases = [
            ("初音未来", "初音未来"),
            ("  初音未来  ", "初音未来"),
            ("初音@未来#", "初音 未来"),
            ("", ""),
            ("   ", "")
        ]
        
        for input_query, expected_output in test_cases:
            result = client._clean_search_query(input_query)
            assert result == expected_output

    @pytest.mark.asyncio
    async def test_netease_provider_audio_info_creation(self):
        """测试NetEase提供者音频信息创建"""
        provider = NetEaseProvider("./test_temp")
        
        # 模拟歌曲详情
        mock_song_details = {
            "id": 517567145,
            "title": "初登校",
            "artist": "橋本由香利",
            "album": "ひなこのーと COMPLETE SOUNDTRACK",
            "cover": "http://example.com/cover.jpg",
            "link": "http://music.163.com/song/media/outer/url?id=517567145.mp3"
        }
        
        with patch('similubot.utils.netease_search.get_song_details', new_callable=AsyncMock) as mock_get_details:
            mock_get_details.return_value = mock_song_details
            
            url = "https://music.163.com/song?id=517567145"
            audio_info = await provider._extract_audio_info_impl(url)
            
            assert audio_info is not None
            assert audio_info.title == "初登校"
            assert audio_info.uploader == "橋本由香利"
            assert audio_info.url == "http://music.163.com/song/media/outer/url?id=517567145.mp3"
            assert audio_info.thumbnail_url == "http://example.com/cover.jpg"
            assert audio_info.file_format == "mp3"

    def test_audio_info_creation(self):
        """测试音频信息创建"""
        audio_info = AudioInfo(
            title="初登校",
            duration=225,
            url="https://api.paugram.com/netease/?id=517567145",
            uploader="橋本由香利",
            thumbnail_url="http://example.com/cover.jpg",
            file_format="mp3"
        )
        
        assert audio_info.title == "初登校"
        assert audio_info.duration == 225
        assert audio_info.url == "https://api.paugram.com/netease/?id=517567145"
        assert audio_info.uploader == "橋本由香利"
        assert audio_info.thumbnail_url == "http://example.com/cover.jpg"
        assert audio_info.file_format == "mp3"

    def test_netease_search_result_edge_cases(self):
        """测试NetEase搜索结果边界情况"""
        # 测试无时长的情况
        result_no_duration = NetEaseSearchResult(
            song_id="123",
            title="测试歌曲",
            artist="测试艺术家",
            album="测试专辑"
        )
        assert result_no_duration.format_duration() == "未知时长"
        
        # 测试空字符串的情况
        result_empty = NetEaseSearchResult(
            song_id="456",
            title="",
            artist="",
            album=""
        )
        assert result_empty.get_display_name() == " - "
        
        # 测试长时长的情况
        result_long = NetEaseSearchResult(
            song_id="789",
            title="长歌曲",
            artist="艺术家",
            album="专辑",
            duration=3661  # 1小时1分1秒
        )
        assert result_long.format_duration() == "61:01"

    @pytest.mark.asyncio
    async def test_provider_factory_integration(self):
        """测试提供者工厂集成"""
        from similubot.provider.provider_factory import AudioProviderFactory
        
        # 创建工厂实例
        factory = AudioProviderFactory("./test_temp")
        
        # 验证NetEase提供者被包含
        supported_providers = factory.get_supported_providers()
        assert "netease" in supported_providers
        
        # 验证可以获取NetEase提供者
        netease_provider = factory.get_provider_by_name("netease")
        assert netease_provider is not None
        assert isinstance(netease_provider, NetEaseProvider)
        
        # 验证URL检测
        netease_url = "https://music.163.com/song?id=517567145"
        detected_provider = factory.detect_provider_for_url(netease_url)
        assert detected_provider is not None
        assert isinstance(detected_provider, NetEaseProvider)
        
        # 验证URL支持检测
        assert factory.is_supported_url(netease_url)

    def test_interaction_result_enum(self):
        """测试交互结果枚举"""
        assert InteractionResult.CONFIRMED.value == "confirmed"
        assert InteractionResult.DENIED.value == "denied"
        assert InteractionResult.SELECTED.value == "selected"
        assert InteractionResult.CANCELLED.value == "cancelled"
        assert InteractionResult.TIMEOUT.value == "timeout"

    @pytest.mark.asyncio
    async def test_complete_workflow_simulation(self):
        """测试完整工作流程模拟"""
        # 1. 创建搜索结果
        search_result = NetEaseSearchResult(
            song_id="517567145",
            title="初登校",
            artist="橋本由香利",
            album="ひなこのーと COMPLETE SOUNDTRACK",
            cover_url="http://example.com/cover.jpg",
            duration=225
        )
        
        # 2. 验证搜索结果
        assert search_result.get_display_name() == "初登校 - 橋本由香利"
        assert search_result.format_duration() == "3:45"
        
        # 3. 创建提供者并验证URL支持
        provider = NetEaseProvider("./test_temp")
        netease_url = f"https://music.163.com/song?id={search_result.song_id}"
        assert provider.is_supported_url(netease_url)
        
        # 4. 提取歌曲ID
        extracted_id = provider._extract_song_id(netease_url)
        assert extracted_id == search_result.song_id
        
        # 5. 生成播放URL（代理功能禁用时应该返回原始URL）
        client = NetEaseSearchClient()
        playback_url = client.get_playback_url(search_result.song_id, use_api=True)
        assert playback_url == f"https://api.paugram.com/netease/?id={search_result.song_id}"
        
        # 6. 创建音频信息
        audio_info = AudioInfo(
            title=search_result.title,
            duration=search_result.duration,
            url=playback_url,
            uploader=search_result.artist,
            thumbnail_url=search_result.cover_url,
            file_format="mp3"
        )
        
        # 7. 验证音频信息
        assert audio_info.title == search_result.title
        assert audio_info.duration == search_result.duration
        assert audio_info.uploader == search_result.artist
        
        # 8. 创建交互管理器并验证嵌入消息
        manager = InteractionManager()
        embed = manager._create_confirmation_embed(search_result)
        assert embed.title == "🎵 找到歌曲"
        
        # 工作流程验证完成
        assert True  # 如果到达这里，说明整个工作流程都正常
