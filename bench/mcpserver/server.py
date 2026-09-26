#!/usr/bin/env python3
"""bench.mcpserver.server — MCP server（stdio，换行分隔 JSON-RPC 2.0）。

零第三方依赖：手写协议层（不依赖官方 mcp SDK），便于协议级单测与审计。
分三层：
  handle_line(str)     行 → json.loads → handle_message（解析失败 → -32700）
  handle_message(obj)  纯函数，返回响应 dict 或 None（notification）
  serve()              stdin 逐行读 → stdout 逐行写

支持：initialize / notifications/initialized / ping / tools/list / tools/call。
"""

import json
import sys

from bench.mcpserver.tools import call_tool, tool_specs, ToolError

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "lcb-bench"
SERVER_VERSION = "0.1.0"

_PARSE_ERROR = -32700
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602


def _err(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _ok(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def handle_line(line: str):
    """stdio 一行 → 响应（解析失败返回 -32700，不抛异常）。"""
    try:
        msg = json.loads(line)
    except Exception as e:
        return _err(None, _PARSE_ERROR, f"JSON 解析失败: {e}")
    return handle_message(msg)


def handle_message(msg):
    """JSON-RPC 消息 → 响应 dict；notification（无 id）→ None。"""
    if not isinstance(msg, dict):
        return _err(None, _PARSE_ERROR, "消息必须是 JSON 对象")

    method = msg.get("method")
    req_id = msg.get("id")
    is_notification = "id" not in msg

    if not isinstance(method, str):
        return None if is_notification else _err(req_id, _INVALID_PARAMS,
                                                 "缺少 method")

    # ── notification：一律不响应 ──
    if is_notification or method.startswith("notifications/"):
        return None

    if method == "initialize":
        return _ok(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method == "ping":
        return _ok(req_id, {})

    if method == "tools/list":
        return _ok(req_id, {"tools": tool_specs()})

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return _err(req_id, _INVALID_PARAMS, "tools/call 需要 params.name")
        args = params.get("arguments") or {}
        try:
            payload = call_tool(name, args)
            text = json.dumps(payload, ensure_ascii=False, indent=2)
            return _ok(req_id, {"content": [{"type": "text", "text": text}],
                                "isError": False})
        except ToolError as e:
            return _ok(req_id, {"content": [{"type": "text",
                                             "text": f"[工具错误] {e}"}],
                                "isError": True})
        except Exception as e:      # 工具内部异常也转为 isError，不中断会话
            return _ok(req_id, {"content": [{"type": "text",
                                             "text": f"[内部错误] {type(e).__name__}: {e}"}],
                                "isError": True})

    return _err(req_id, _METHOD_NOT_FOUND, f"未知方法: {method}")


def serve(stdin=None, stdout=None) -> None:
    """stdio 循环：逐行读 JSON-RPC，逐行写响应（stderr 留给日志）。"""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        resp = handle_line(line)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()