# LCB MCP Server

把长程一致性基准的能力暴露成 MCP 工具，任何 MCP 客户端（Claude Code / Cursor / …）可直接调用。

## 两个实现（自动择一）

| 实现 | 文件 | 依赖 | 需要 | 特点 |
|---|---|---|---|---|
| **SDK 版** | `sdk_server.py` | 官方 `mcp`（v2） | Python ≥3.10 | 标准、功能全（resources/prompts/HTTP 传输/并发）、依赖重（pydantic/starlette/uvicorn） |
| **手写版** | `server.py` | 无（零第三方） | Python 3.9 即可 | 协议层自实现，可审计、协议级可单测，只实现 tools 子集 |

工具逻辑不分叉——两版都调 `tools.py` 的同一批纯函数，描述文本同取自 `tool_specs()`。
`scripts/lcb_mcp.py` 自动选择：SDK 可用则用 SDK，否则回落手写；也可 `--sdk` / `--hand-rolled` 强制。

## 工具

| 工具 | 作用 | 需 LLM |
|---|---|---|
| `universe_generate` | 合成宇宙生成（seed → cast/真相表/知识表/spec），确定性、可落盘为内容包 | 否 |
| `novel_ledger` | 状态账本：逐章天/时段/地点/在场/死亡/持有物 + 时间线违规 | 否 |
| `novel_truth` | 真相表读取（`only_false` 可只取 B 型反转素材） | 否 |
| `novel_gate` | 门禁：机械判定（禁词/段落/篇幅/POV/人称）+ 状态判定（死人复活/时间倒流/日期矛盾/物品双持有）→ 违反清单 + 每万字违反率 | 否 |
| `novel_gen` | 调 `gen.py` 生成章节草稿（支持 `resection` 局部重生成） | 是 |

## 注册

SDK 版（推荐，Python ≥3.10 环境）：

```bash
claude mcp add lcb -- /Users/ayu/ayu/写作/fictionforge/.venv/bin/python \
    /Users/ayu/ayu/写作/fictionforge/scripts/lcb_mcp.py
```

手写版（零依赖 / Python 3.9）：

```bash
claude mcp add lcb -- python3 /Users/ayu/ayu/写作/fictionforge/scripts/lcb_mcp.py
```

项目级 `.mcp.json`：

```json
{
  "mcpServers": {
    "lcb": {
      "command": "/Users/ayu/ayu/写作/fictionforge/.venv/bin/python",
      "args": ["/Users/ayu/ayu/写作/fictionforge/scripts/lcb_mcp.py"]
    }
  }
}
```

## 环境准备（仅 SDK 版需要）

```bash
~/.local/bin/python3.12 -m venv .venv
.venv/bin/pip install mcp
```

框架其余部分仍是 Python 3.9 兼容写法，**不需要升级**——3.12 上测试同样全绿。

## 典型流程（全离线，零成本）

```
universe_generate(seed=99, chapters=6, out_dir=/tmp/u)
  → novel_ledger(novel_dir=/tmp/u)        查状态：谁死了、时间线是否倒退
  → novel_truth(novel_dir=/tmp/u, only_false=true)   取 B 型反转素材
  → novel_gate(novel_dir=/tmp/u, chapter=4, text="…")  跑门禁拿违反率
```

## 手工验证（不经客户端）

```bash
# SDK 版（stdin 保持打开，否则服务端 EOF 退出、响应来不及回写）
{ printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
 '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'; sleep 3; } \
 | .venv/bin/python scripts/lcb_mcp.py --sdk

# 手写版（写完即关 stdin 也可以，同步处理）
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
 | python3 scripts/lcb_mcp.py --hand-rolled
```

## 已知差异与限制

- **SDK 版并发执行工具调用**：SDK v2 会并行处理同一批请求；若客户端把
  `universe_generate` 与 `novel_ledger` 背靠背发出（不等结果），ledger 可能
  读到只写了一半的目录。正常 agent 会等结果再调下一步，不受影响；
  手写版是单线程顺序处理，无此问题。
- **手写版仅 tools 子集**：未实现 resources / prompts / 取消 / HTTP 传输。
- **stdin 必须保持打开**：手工用管道测试时若写完立即关闭 stdin，stdio 服务端
  会 EOF 退出，异步工具来不及回写响应（表现为「响应丢失」）。

## 测试

```bash
python3 tests/test_mcp_server.py     # 手写版：协议级 + 工具级，零 LLM/零网络（40 项）
.venv/bin/python tests/test_mcp_sdk.py   # SDK 版：12 项（无 SDK 时自动 SKIP）
```