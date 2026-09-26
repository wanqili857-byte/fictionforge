# LCB MCP Server

把长程一致性基准的能力暴露成 MCP 工具，任何 MCP 客户端（Claude Code / Cursor / …）可直接调用。

零第三方依赖——协议层（JSON-RPC 2.0 over stdio，换行分隔）自行实现，便于审计与单测。

## 工具

| 工具 | 作用 | 需 LLM |
|---|---|---|
| `universe_generate` | 合成宇宙生成（seed → cast/真相表/知识表/spec），确定性、可落盘为内容包 | 否 |
| `novel_ledger` | 状态账本：逐章天/时段/地点/在场/死亡/持有物 + 时间线违规 | 否 |
| `novel_truth` | 真相表读取（`only_false` 可只取 B 型反转素材） | 否 |
| `novel_gate` | 门禁：机械判定（禁词/段落/篇幅/POV/人称）+ 状态判定（死人复活/时间倒流/日期矛盾/物品双持有）→ 违反清单 + 每万字违反率 | 否 |
| `novel_gen` | 调 `gen.py` 生成章节草稿（支持 `resection` 局部重生成） | 是 |

## 注册

```bash
claude mcp add lcb -- python3 /Users/ayu/ayu/写作/fictionforge/scripts/lcb_mcp.py
```

或项目级 `.mcp.json`：

```json
{
  "mcpServers": {
    "lcb": {
      "command": "python3",
      "args": ["/Users/ayu/ayu/写作/fictionforge/scripts/lcb_mcp.py"]
    }
  }
}
```

## 典型流程（全离线，零成本）

```
universe_generate(seed=99, chapters=6, out_dir=/tmp/u)
  → novel_ledger(novel_dir=/tmp/u)        查状态：谁死了、时间线是否倒退
  → novel_truth(novel_dir=/tmp/u, only_false=true)   取 B 型反转素材
  → novel_gate(novel_dir=/tmp/u, chapter=4, text="…")  跑门禁拿违反率
```

## 手工验证（不经客户端）

```bash
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 | python3 scripts/lcb_mcp.py
```

## 测试

```bash
python3 tests/test_mcp_server.py     # 协议级 + 工具级，零 LLM / 零网络（40 项）
```