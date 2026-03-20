"""
网易云音乐 HTTP 请求头工具。

为体积很小的 JSON/text API 响应固定使用 identity 编码，
避免某些上游/代理链路返回不可解码的 Brotli 响应体。
"""

from typing import Dict


def build_netease_api_headers() -> Dict[str, str]:
    """构建网易云 JSON API 请求头。"""
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "http://music.163.com",
        "Host": "music.163.com",
        "Accept-Encoding": "identity",
    }
