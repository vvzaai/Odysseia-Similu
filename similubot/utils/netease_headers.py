"""
网易云音乐 HTTP 请求头工具。

为体积很小的 JSON/text API 响应固定使用 identity 编码，
避免某些上游/代理链路返回不可解码的 Brotli 响应体。
"""

from typing import Dict, Optional


def build_netease_api_headers(
    *,
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    referer: str = "http://music.163.com",
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """构建网易云小型 API 请求头。"""
    headers = {
        "User-Agent": user_agent,
        "Referer": referer,
        "Host": "music.163.com",
        "Accept-Encoding": "identity",
    }
    if extra_headers:
        headers.update(extra_headers)
    return headers
