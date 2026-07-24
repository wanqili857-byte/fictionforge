"""
_api_client.py — SSE 流式客户端（gen.py + engine/ 共享）。

与本地 gen_proxy（端口 3002）通信，解析 SSE 流。
提供纯文本返回和流式消费两种模式。
"""

import json
import sys
import urllib.request
from typing import Optional

API_URL = "http://localhost:3002/api/chat"

# ── 模型路由表（唯一权威来源） ──────────────────────────────────────────
# gen.py 和 engine/api.py 都从本模块导入
# 如需增删模型/路线，只改此处
MODEL_ROUTES = {
    "outline":  {"provider": "deepseek", "model": "deepseek-v4-flash",
                 "temperature": 0.7, "maxTokens": 2048},
    "normal":   {"provider": "openrouter", "model": "moonshotai/kimi-k2.6",
                 "temperature": 0.85, "maxTokens": 32768},
    "expanded": {"provider": "openrouter", "model": "moonshotai/kimi-k2.6",
                 "temperature": 0.85, "maxTokens": 32768},
    "agent":    {"provider": "deepseek", "model": "deepseek-v4-flash",
                 "temperature": 0.7, "maxTokens": 8192},
    "long":     {"provider": "deepseek", "model": "deepseek-v4-flash",
                 "temperature": 0.7, "maxTokens": 16384},
    "state":    {"provider": "deepseek", "model": "deepseek-v4-flash",
                 "temperature": 0.3, "maxTokens": 2048},
    # Qwen 3.7 Max — 百炼 DashScope
    "qwen-agent": {"provider": "dashscope", "model": "qwen-max",
                 "temperature": 0.7, "maxTokens": 8192},
    "qwen-long":  {"provider": "dashscope", "model": "qwen-max",
                 "temperature": 0.7, "maxTokens": 16384},
}


def sse_request(body: dict, stream_callback=None) -> Optional[str]:
    """发送 SSE 请求并返回完整响应文本。

    stream_callback(content_chunk): 如果提供，每收到一个 chunk 就调用一次。
    用于 gen.py 的实时流式输出。

    返回完整响应文本，出错时返回 None。
    """
    req_data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=req_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    full_content = ""
    try:
        response = urllib.request.urlopen(req, timeout=600)
        buffer = b""
        while True:
            chunk = response.read(1)
            if not chunk:
                break
            buffer += chunk
            if buffer.endswith(b"\n"):
                line = buffer.decode("utf-8", errors="replace").strip()
                buffer = b""
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                t = data.get("type", "")
                if t == "chunk":
                    c = data.get("content", "")
                    full_content += c
                    if stream_callback:
                        stream_callback(c)
                elif t == "done":
                    full_content = data.get("fullContent", full_content)
                elif t == "error":
                    print(f"\n[SSE ERROR] {data.get('error', '')}", file=sys.stderr)
                    return None
    except Exception as e:
        print(f"\n[SSE NETWORK ERROR] {e}", file=sys.stderr)
        return None

    return full_content.strip()
