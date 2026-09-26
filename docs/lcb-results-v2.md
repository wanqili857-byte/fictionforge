# LCB 跑批结果 v2（方舟通道 · 三厂商 · 三档 harness）

> 生成条件：合成宇宙 seed=42 × 6 章，k=1，harness 三档（bare/mid/full），
> 判定器 `m0.2.0`（死人复活判据 v2），生成通道 = 火山方舟 coding plan（订阅制）。
> 跑批日期 2026-09-26/27。复现步骤见 `docs/BENCH_PROTOCOL.md`。

## 每次运行

| run | 模型 | 档位 | 字数 | 总违反 | 核心违反 | 核心/万字 | 篇幅 | 文体 | 视角 | 状态 | 修前→修后 | 成本 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ark-glm-flash__bare__k0 | glm-5.3-flash | bare | 11905 | 3 | 3 | **2.52** | 0 | 3 | 0 | 0 | — | 订阅 |
| ark-glm-flash__mid__k0 | glm-5.3-flash | mid | 8725 | 8 | 4 | 4.58 | 4 | 3 | 0 | 1 | — | 订阅 |
| ark-glm-flash__full__k0 | glm-5.3-flash | full | 7241 | 4 | **0** | 0.0 | 4 | 0 | 0 | 0 | 6→4 | 订阅 |
| ark-ds-flash__bare__k0 | deepseek-v4.1-flash | bare | 11692 | 8 | 5 | 4.28 | 3 | 5 | 0 | 0 | — | 订阅 |
| ark-ds-flash__mid__k0 | deepseek-v4.1-flash | mid | 12072 | 3 | 2 | 1.66 | 1 | 2 | 0 | 0 | — | 订阅 |
| ark-ds-flash__full__k0 | deepseek-v4.1-flash | full | 13565 | 3 | **0.74** | 0.74 | 2 | 0 | 1 | 0 | 7→3 | 订阅 |
| ark-db-lite__bare__k0 | doubao-seed-2-1-lite | bare | 14410 | 16 | 12 | 8.33 | 4 | 10 | 2 | 0 | — | 订阅 |
| ark-db-lite__mid__k0 | doubao-seed-2-1-lite | mid | 14186 | 10 | 7 | 4.93 | 3 | 4 | 0 | 3 | — | 订阅 |
| ark-db-lite__full__k0 | doubao-seed-2-1-lite | full | 12928 | 2 | **0** | 0.0 | 2 | 0 | 0 | 0 | 4→2 | 订阅 |

## 按模型汇总

| 模型 | bare | mid | full(修后) | 上下文工程(bare→mid) | 门禁(配对:修前核心→修后) |
|---|---|---|---|---|---|
| glm-5.3-flash | 2.52 | 4.58 | 0.0 | **−2.06** | 2→0 |
| deepseek-v4.1-flash | 4.28 | 1.66 | 0.74 | +2.62 | 5→1 |
| doubao-seed-2-1-lite | 8.33 | 4.93 | 0.0 | +3.40 | 2→0 |

## 榜单（排序键 = bare 档核心违反率）

| 名次 | 模型 | bare | mid | full |
|---|---|---|---|---|
| 1 | glm-5.3-flash | 2.52 | 4.58 | 0.0 |
| 2 | deepseek-v4.1-flash | 4.28 | 1.66 | 0.74 |
| 3 | doubao-seed-2-1-lite | 8.33 | 4.93 | 0.0 |

## 注解（缺一条就会被误读）

1. **k=1**：档位差值混着采样噪声，只能看方向。归因结论需 k≥3（复现协议 §8）。
2. **glm 的 mid 反转**（2.52 → 4.58）在两期跑批中重复出现（v1 的 glm-flash
   6.55 → 7.08 同向）：对 glm 家族，注入上下文反而吵到它——「上下文工程是
   万能药」在这个家族上不成立。而 deepseek/doubao 的 mid 都是正贡献。
3. **门禁配对测量**：三家修前核心 9 → 修后 1，门禁只做减法（删禁词、切长段），
   补不了篇幅——glm full 残留 4 条全是篇幅（它写不满 1900 字目标）。
4. **成本口径**：订阅通道成本列标「订阅」，可比量是 token。实测思考型模型
   单章 output 1 万~2.3 万 token（其中 reasoning 占大头；glm 的 reasoning
   计入 max_tokens、doubao 的不计）——同一张表里的「一个章节」在不同模型
   手里的算力成本差 3 倍以上。
5. **判定器版本 m0.2.0**：首轮判决后人工抽查发现 12 条 state-dead 全是误报
   （死者名字写在流水册上、「是他的字」「当时蹲在这」——遗物/回忆性指称），
   判据改为正向证据制（活动动词窗口），12 条 FP 原文进测试夹具钉死。
   判决可重算：`--rejudge` 零 LLM 调用重出全部判决。
6. **运维现实**：本期跑批真处理了四类打断——方舟账号级 429（退避重试）、
   glm 思考超时 420s 不够（per-spec timeout 1200s）、空正文（reasoning 烧满
   max_tokens → 判 error 不入库）、残缺 run 冒充完成（续跑校验完成度）。
   评测系统的鲁棒性是跑出来的，不是设计出来的。

## 通道史（覆盖了哪些厂商，为什么）

| 通道 | 状态 | 厂商 |
|---|---|---|
| 火山方舟 coding plan | ✅ 本期 | 字节(doubao)/智谱(glm)/DeepSeek |
| 阿里云百炼 | ⛔ 账号欠费墙 | 阿里(qwen)/Moonshot(kimi)——待账务处理后补跑 |
| OpenRouter | ⛔ 额度耗尽停用 | （v1 历史数据仍有效） |

原始报表：`runs/v2/_flat/report.md`（由 `python3 -m bench.report.metrics runs/v2/_flat --write` 生成）。
