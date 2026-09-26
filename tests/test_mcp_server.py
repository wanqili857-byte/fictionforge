#!/usr/bin/env python3
"""
test_mcp_server.py — W12 MCP server 单元测试（协议级 + 工具级，零 LLM / 零网络）。

覆盖（BENCH_PLAN W12 验收）：
- 协议：initialize 握手 / notifications/initialized 无响应 / tools/list /
  tools/call / 未知方法与未知工具报错 / 畸形 JSON 不崩
- 工具（≥5 个，每个至少 happy path + 参数校验）：
  universe_generate / novel_ledger / novel_truth / novel_gate / novel_gen
- 确定性：universe_generate 同 seed 两次结果一致
- 端到端离线流程：universe_generate → novel_ledger → novel_gate（查状态→门禁）

用法:
    python3 tests/test_mcp_server.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bench.mcpserver.server import (
    handle_message, handle_line, PROTOCOL_VERSION, SERVER_NAME,
)
from bench.mcpserver.tools import call_tool, tool_specs, ToolError

_PASS, _FAIL = 0, 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  FAIL: {name}")


def call(name, args):
    """tools/call 便捷封装 → (文本, isError)。"""
    resp = handle_message({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                           "params": {"name": name, "arguments": args}})
    res = resp["result"]
    text = res["content"][0]["text"]
    return text, res.get("isError", False)


# ── 协议层 ────────────────────────────────────────────────────────────

def test_initialize():
    resp = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-06-18",
                                      "capabilities": {}}})
    r = resp["result"]
    check("initialize 返回协议版本", r["protocolVersion"] == PROTOCOL_VERSION)
    check("initialize serverInfo 名字", r["serverInfo"]["name"] == SERVER_NAME)
    check("声明 tools 能力", "tools" in r["capabilities"])
    check("id 回填", resp["id"] == 1 and resp["jsonrpc"] == "2.0")


def test_notification_no_response():
    check("notifications/initialized 无响应",
          handle_message({"jsonrpc": "2.0",
                          "method": "notifications/initialized"}) is None)
    check("未知 notification 无响应",
          handle_message({"jsonrpc": "2.0", "method": "notifications/whatever"}) is None)


def test_tools_list():
    resp = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = resp["result"]["tools"]
    names = {t["name"] for t in tools}
    check("工具 ≥5 个", len(tools) >= 5)
    check("含五个必需工具",
          {"universe_generate", "novel_ledger", "novel_truth",
           "novel_gate", "novel_gen"} <= names)
    ok = True
    for t in tools:
        if not (t.get("name") and t.get("description") and
                isinstance(t.get("inputSchema"), dict) and
                t["inputSchema"].get("type") == "object"):
            ok = False
            print(f"      · 工具 {t.get('name')} schema 不合格")
    check("每个工具带 name/description/inputSchema", ok)


def test_protocol_errors():
    r1 = handle_message({"jsonrpc": "2.0", "id": 3, "method": "no/such/method"})
    check("未知方法 → -32601", r1["error"]["code"] == -32601)
    t, err = call("no_such_tool", {})
    check("未知工具 → isError", err and "未知工具" in t)
    resp = handle_message({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                           "params": {"name": "novel_truth"}})   # 缺 arguments
    check("tools/call 缺 arguments 不崩", "result" in resp or "error" in resp)


def test_malformed_json():
    r = handle_message(None)
    check("None 输入 → -32700 而非崩溃",
          isinstance(r, dict) and r["error"]["code"] == -32700)
    r2 = handle_message("这不是对象")
    check("非对象输入 → -32700", isinstance(r2, dict) and r2["error"]["code"] == -32700)
    r3 = handle_line("{ 坏 JSON")
    check("畸形行 → -32700", isinstance(r3, dict) and r3["error"]["code"] == -32700)
    r4 = handle_line('{"jsonrpc":"2.0","id":5,"method":"ping"}')
    check("正常行可解析", isinstance(r4, dict) and r4.get("id") == 5)


# ── 工具层 ────────────────────────────────────────────────────────────

def test_universe_generate_deterministic():
    t1, e1 = call("universe_generate", {"seed": 42, "chapters": 6})
    t2, e2 = call("universe_generate", {"seed": 42, "chapters": 6})
    check("universe_generate 成功", not e1 and not e2)
    check("同 seed 结果一致（确定性）", t1 == t2)
    d = json.loads(t1)
    check("返回 invariants 空", d["invariants"] == [])
    check("返回章数与 cast", d["chapters"] == 6 and len(d["cast"]) == 4)
    t3, _ = call("universe_generate", {"seed": 43, "chapters": 6})
    check("不同 seed 不同", t1 != t3)


def test_universe_generate_validation():
    for bad in ({}, {"seed": "x"}, {"seed": 1, "chapters": 0}):
        t, err = call("universe_generate", bad)
        check(f"参数校验: {bad}", err)


def test_tool_offline_flow():
    """端到端离线流程：生成宇宙 → 落盘 → 查账本 → 跑门禁。"""
    tmp = Path(tempfile.mkdtemp())
    t, err = call("universe_generate", {"seed": 7, "chapters": 6,
                                       "out_dir": str(tmp)})
    check("生成并落盘", not err and (tmp / "specs" / "ch1.json").exists())

    t2, err2 = call("novel_ledger", {"novel_dir": str(tmp)})
    check("查账本成功", not err2)
    led = json.loads(t2)
    check("账本 6 章", len(led["chapters"]) == 6)
    check("账本含声明死亡", any(c["deaths"] for c in led["chapters"]))
    check("时间线无违规", led["timeline_violations"] == [])

    t3, err3 = call("novel_truth", {"novel_dir": str(tmp)})
    check("查真相表成功", not err3 and len(json.loads(t3)["facts"]) >= 8)
    t4, _ = call("novel_truth", {"novel_dir": str(tmp), "only_false": True})
    check("only_false 过滤", all(f["is_false"] for f in json.loads(t4)["facts"]))

    # 门禁：对给定文本跑机械 + 状态判定
    dead = led["chapters"]
    dead_name = next(c["deaths"][0] for c in dead if c["deaths"])
    dead_ch = next(c["chapter"] for c in dead if c["deaths"])
    text = f"{dead_name}从码头尽头走过来，把缆绳甩在桩上。他忽然停住。"
    t5, err5 = call("novel_gate", {"novel_dir": str(tmp), "chapter": dead_ch + 1,
                                   "text": text})
    check("门禁成功", not err5)
    gate = json.loads(t5)
    types = {v["type"] for v in gate["violations"]}
    check("门禁含 state 违反", "state" in types)
    check("门禁含 constraint 违反", "constraint" in types)
    check("门禁给出每万字违反率", gate["rate_per_10k"] > 0)


def test_ledger_validation():
    t, err = call("novel_ledger", {})
    check("缺 novel_dir → 报错", err)
    t2, err2 = call("novel_ledger", {"novel_dir": "/tmp/绝对不存在的目录-xyz"})
    check("不存在的目录 → 报错", err2)


def test_novel_gen_validation():
    # 只测参数校验与错误路径（真实生成需要 LLM 通道，不在离线单测里跑）
    t, err = call("novel_gen", {})
    check("novel_gen 缺参数 → 报错", err and "spec" in t.lower())
    t2, err2 = call("novel_gen", {"spec": "/tmp/不存在-spec.json"})
    check("novel_gen spec 不存在 → 报错", err2)


def test_tool_error_direct():
    try:
        call_tool("universe_generate", {"seed": "bad"})
        check("call_tool 直接抛 ToolError", False)
    except ToolError:
        check("call_tool 直接抛 ToolError", True)


if __name__ == "__main__":
    test_initialize()
    test_notification_no_response()
    test_tools_list()
    test_protocol_errors()
    test_malformed_json()
    test_universe_generate_deterministic()
    test_universe_generate_validation()
    test_tool_offline_flow()
    test_ledger_validation()
    test_novel_gen_validation()
    test_tool_error_direct()
    print(f"\n结果: {_PASS}/{_PASS + _FAIL} 通过")
    sys.exit(1 if _FAIL else 0)