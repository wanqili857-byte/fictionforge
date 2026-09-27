#!/usr/bin/env python3
"""
test_mcp_sdk.py — 官方 MCP SDK 版实现测试。

需要 Python ≥3.10 且装有官方 mcp SDK（.venv）。未满足时整体 SKIP（退出 0），
这样 3.9 环境的 CI 不会红——手写版（test_mcp_server.py）始终是全量覆盖。

覆盖：
- build_server() 可构造
- 工具清单：5 个、名字正确、描述非空（描述来自 tool_specs() 单一来源）
- 经 SDK 真实调用：universe_generate（确定性）+ novel_gate（含错误路径）
- 错误语义：参数非法 → 结果标记错误，不炸会话

用法:
    .venv/bin/python tests/test_mcp_sdk.py     # 有 SDK
    python3 tests/test_mcp_sdk.py              # 无 SDK → SKIP
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

_PASS, _FAIL = 0, 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  FAIL: {name}")


def _sdk_available():
    if sys.version_info < (3, 10):
        return False
    try:
        import mcp.server.mcpserver  # noqa: F401
        return True
    except Exception:
        return False


def main():
    if not _sdk_available():
        print(f"SKIP: 官方 mcp SDK 不可用（python {sys.version.split()[0]} < 3.10 或未安装）")
        return 0

    from bench.mcpserver import sdk_server

    # ── 构造与工具清单 ──
    server = sdk_server.build_server()
    check("build_server 可构造", server is not None)

    tools = asyncio.run(server.list_tools())
    names = sorted(t.name for t in tools)
    check("SDK 列出 5 个工具", len(tools) == 5)
    check("工具名与手写版一致",
          names == ["novel_gate", "novel_gen", "novel_ledger",
                    "novel_truth", "universe_generate"])
    check("每个工具描述非空", all((t.description or "").strip() for t in tools))

    from bench.mcpserver.server import handle_message
    hand = {t["name"] for t in json.loads(json.dumps(
        handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})))["result"]["tools"]}
    check("两版工具集合一致", hand == set(names))

    # ── 经 SDK 真实调用 ──
    def call(name, args):
        res = asyncio.run(server.call_tool(name, args))
        text = ""
        for c in (getattr(res, "content", None) or []):
            text += getattr(c, "text", "") or ""
        return text, bool(getattr(res, "isError", False))

    t1, e1 = call("universe_generate", {"seed": 21, "chapters": 4})
    t2, e2 = call("universe_generate", {"seed": 21, "chapters": 4})
    check("经 SDK 调用成功", not e1 and not e2 and bool(t1))
    d = json.loads(t1)
    check("返回体含宇宙要素", d["chapters"] == 4 and len(d["cast"]) == 4)
    check("同 seed 确定性（经 SDK）", t1 == t2)

    # 落盘后跑门禁（跨工具流程）
    tmp = Path(tempfile.mkdtemp())
    call("universe_generate", {"seed": 21, "chapters": 4, "out_dir": str(tmp)})
    t3, e3 = call("novel_ledger", {"novel_dir": str(tmp)})
    check("经 SDK 查账本", not e3 and len(json.loads(t3)["chapters"]) == 4)

    # ── 错误路径 ──
    # 注意：直接调 server.call_tool() 时，SDK 把「pydantic 参数校验失败」与
    # 「工具内部异常」都转成抛错；在真实客户端会话里 SDK 会把它包成 isError
    # 结果返回（协议层行为，由 SDK 上游保证）。这里断言「不会静默成功」。
    def raises(fn):
        try:
            fn()
            return False
        except Exception:
            return True

    check("参数类型非法 → 抛错（不静默成功）",
          raises(lambda: call("universe_generate", {"seed": "不是数字"})))
    check("目录不存在 → 抛错",
          raises(lambda: call("novel_ledger", {"novel_dir": "/tmp/绝对不存在-xyz"})))
    check("未知工具 → 抛错",
          raises(lambda: call("no_such_tool", {})))

    print(f"\n结果: {_PASS}/{_PASS + _FAIL} 通过")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())