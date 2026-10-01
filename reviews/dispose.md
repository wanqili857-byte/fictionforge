# CanonBench 独立复核 · 记录与修复计划（第一轮）

> 派单 2026-10-01 · 三审核员独立进程 · 材料快照 `git:4770d0de`（HEAD `ac71277`）
> 工单 `reviews/brief.md` · 配置 `reviews/review.yaml` · 原始结论 `reviews/out/`
>
> 本文档是**记录 + 计划 + 处置台账**三合一：末尾那张表的每行挂一条可失败的断言，
> `quorum verify --config reviews/review.yaml` 会全部跑一遍。
> 判定标准：**把这个修复回滚，这条 check 会不会红？** 不会红的不写进来。

---

## 一、这一轮是谁审的

| 审核员 | harness | 模型 | vendor | role |
|---|---|---|---|---|
| kimi | `claude -p`（方舟 Anthropic 兼容端点） | kimi-k2.7-code | moonshot | primary |
| doubao | `claude -p`（同一端点） | doubao-seed-2-1-pro | bytedance | primary |
| codex | `codex exec -s read-only` | deepseek-v4-flash | deepseek | cross |

**独立性**：vendor 三家不同；harness 只有两个（kimi 与 doubao 同走 claude-cli）。
所以这两家的一致**可能部分来自同一套 loop**，不全是两个独立模型的共识——
这条注记按 quorum 的契约写在配置里，不藏着。

**COI**：CanonBench 的被测名单里就有字节与 deepseek。kimi（moonshot）不在名单里，
故它是最干净的一家；doubao 与 codex 的结论涉及自家模型在榜上的位置时需打折扣。

## 二、三家全部 FAILED——原因不同，且本身就是发现

| 审核员 | 门禁判定 | 真因 |
|---|---|---|
| kimi | FAILED · 疑似卡住 | **权限白名单只放行只读文本工具**：`python3 -c` / `-m` / `shasum` / `awk` 全被拒。工单要求跑的验证（测试套件、sha256 复算、确定性比对、重判一致性）**一条都没跑成**，只能静态审查 + 用 grep/sort 从产物重算 |
| doubao | FAILED · 疑似卡住 | 同上 |
| codex | FAILED · 发现 0 条 | 结论**写成了标题式**（`### F1 · …`）而非严重度表，门禁解析不到行 → 判 0 条发现 |

**两条改进项**（进台账）：① 审核员需要能跑本地只读脚本，否则最强的一类发现
（可执行验证）永远拿不到；② quorum 的门禁只认表格，标题式结论会被判空——
这轮先按现状记录，第二轮的工单里明确要求表格。

**尽管如此**，三家仍打出了 20+ 条实质发现，含 1 条致命、1 条推翻对外主张。

## 三、发现汇总与核验

严重度是我核验后的定级；「来源」按首报与佐证列。

| 编号 | 来源 | 位置 | 问题 | 我的核验 |
|---|---|---|---|---|
| **F1** 🔴 | kimi、codex | `bench/runner/llm.py`（v0.4.1） | `call_model()` 成功时**隐式返回 `None`**：`if status == 200: break` 之后又跟一个 `if status == 200:` 解析块 → 死代码。真跑批从第一次成功调用起崩 | ✅ **成立且更糟**：除死代码外，`ParseError` 也不在可重试集合里，那条「并入退避重试」根本没生效。已修 + 补传输层打桩测试 + 真调验证 |
| **F2** 🔴 | kimi、doubao、codex | `bench/results/v2/*/violations.json` vs `state_judge.py` | 出厂产物里 4 条 `state-dead` **全是误报**（定语从句/比喻框），却计进「状态」族、进了榜单与页面 | ✅ 成立。**这 4 条正是我早先自己判定为误报的那 4 条**——判定它们错、却没从产物剔除 |
| **F3** 🔴 | kimi、doubao、codex | `bench/calib/real_text.py` + `docs/canonbench-calibration.md` | 「真实散文上状态类判据零误报」**是空测**：校准账本里唯一的死者是探针名 `祁三`，它按设计不出现在任何语料里 → 该判据在干净文本上无可命中对象，「零候选」是构造决定的 | ✅ 成立。codex 把它反着做了一遍（把语料里真实出场的角色声明为死者）→ 立刻命中，证明是空测 |
| **F4** 🟠 | kimi | v1 产物 + writeup §9 | 「注入上下文让 glm 更差 · 跨两期复现」**表述过强**：v1 那次是密度口径翻转、绝对数是好的（2 条 < 4 条）；只有 v2 两种口径同向 | ✅ 成立，需订正表述 |
| **F5** 🟠 | codex | `bench/calib/real_text.py` | 「孪生句只差一个词，故差异只可能来自判据」的论证**前提不成立**：注入句同时改变了段落结构/相邻引号 | ✅ 成立（同日我自己的差分测量已修一半，论证本身仍要改） |
| **F6** 🟠 | kimi | `bench/runner/batch.py` / `llm.py` | **截断但非空**的章节被当完整章：`ark-glm-flash__mid__k0/ch5.md` 只有 147 字、句子没写完，仍记 6/6 完成 | ✅ 成立 |
| **F7** 🟠 | kimi、codex | `bench/universe/generator.py` | 至少 3 条不变量**恒真**：如 `reveal_chapter` 由 `hero_learned` 赋值，不变量再从**同一个 dict** 反推比对 | ✅ 成立（同源比较） |
| **F8** 🟠 | doubao、codex | `bench/report/metrics.py` | 门禁配对测量**没套用**汇总的剔除规则（`is_incomplete` / `violations_missing` / 章数不足） | ✅ 成立 |
| **F9** 🟡 | codex | `bench/runner/batch.py` | 续跑 skip 只看 `run.json` **自报**完成，不校验判决文件在不在、章数够不够 | ⬜ 待核 |
| **F10** 🟡 | kimi、codex | `docs/BENCH_PROTOCOL.md`、`canonbench-writeup.md` | 复现协议首条命令 `git checkout feat/canonbench` **分支不存在**（批量改名把分支名也改了）；文档指向 `runs/v2/_flat/report.md`（被 gitignore，读者打不开） | ✅ 成立 |
| **F11** 🟡 | kimi、codex | `bench/report/metrics.py`、`html.py` | 榜单只给密度、不给绝对数，与自家协议 §8-4「密度与绝对数成对」相抵触 | ✅ 成立 |
| **F12** 🟡 | kimi | `bench/calib/real_text.py` | `cons-pov` 的诱饵（行首是「她」）对 m0.2.0 与 m0.3.0 输出相同 → 它测不到它声称守护的那次修复 | ✅ 成立 |
| **F13** 🟡 | kimi、codex | `docs/canonbench-writeup.md` | full 档被描述成「自动返修 + 状态回写 / 返修篇幅」，实现里没有状态回写 | ⬜ 待核 |
| **F14** 🟢 | kimi | `.github/workflows/ci.yml`、`CHANGELOG.md` | CI 纳入计数与 CHANGELOG 所述不一致；`test_mcp_sdk.py` 不在 CI | ⬜ 待核 |
| **F15** 🟢 | kimi | `bench/runner/batch.py` | manifest 的 `generated_at` 是**最后一次写入时间**（重判会刷新），非生成时间 | ✅ 成立 |

**三家共同指认的最脆弱一环**（措辞不同、指向同一处）：

> **基准对外最硬的方法学卖点是空的**——「已在真实长篇上校准、状态类判据零误报」
> 这一条，其探针在语料里不存在（F3）；而出厂产物里该判据开火 4 次、4 次全是误报（F2）。
> 正面证据是构造性的，负面证据是真实的。

这条我接受。它的分量大于其余所有条目之和——**不是因为某个数字大了一点，
而是因为「长程状态一致性」这条轴目前既没有正例支撑，又在发布表里把自认的误报当成了成果。**

## 四、修复计划（顺序即依赖）

排序原则：**先改会改变结论的 → 再改机制 → 再改文档 → 只发一次版 → 再来一轮交叉复核。**

| 步 | 内容 | 为什么在这个位置 |
|---|---|---|
| **0** | F1 致命 bug + 缺失的传输层测试 | ✅ 已完成（含真调验证） |
| **1** | F2 修判据（识别定语从句/比喻框）→ 重判 → 4 条误报消失 → 重生成报表/榜单/页面/文档数字；F6 截断章算未完成；F8 门禁配对套用剔除规则 | 这三条改**往外公布的数字**，必须一次做完再动文档，否则文档改三遍 |
| **2** | F3 + F5 校准的测量设计（去掉恒真部分，改论证）；F12 诱饵要能区分实现；F7 恒真不变量 + 负例 | 改「我们凭什么这么说」——产出新结论，会回流文档 |
| **3** | F10 协议分支名/路径；F4 表述订正；F11 榜单补绝对数；F13/F9/F14/F15 核对与订正 | 读者能否复核与表述准确性 |
| **4** | 发 v0.4.2（含全部修复 + CHANGELOG 记录本轮评审） | 一次发出，别发三次 |
| **5** | 第二轮交叉复核：放行审核员跑本地只读脚本；工单强制表格格式；复核本轮结论本身 | 本轮三家全 FAILED，最强的一类发现（可执行验证）没拿到 |

## 五、处置台账（可执行断言）

> **这份台账被 `quorum verify` 抓过一次**：初版 12 行里有 8 行的断言**当时就通过**——
> 也就是说，把对应修复回滚，断言照样绿，属于装饰性断言。已全部改为「今天必红、
> 修完才绿」的形式。工具抓自己主人的台账，这是它设计时的原话，也确实发生了。


| # | 置信度 | 严重度 | 位置 | 问题 | 处置 | check | status |
|---|---|---|---|---|---|---|---|
| 1 | 跨模型族一致 | 🔴 | `bench/runner/llm.py` | 成功路径隐式返回 `None`（`break` 后死代码），且 `ParseError` 不在可重试集合 | 单一 `if status==200` 块内解析并返回；`ParseError` 入 `RETRYABLE_EXC`；补传输层打桩测试 | `python3 tests/test_runner_batch.py` | ✅ |
| 2 | 跨模型族一致 | 🔴 | `bench/judges/state_judge.py` | 定语从句/比喻框被当成「死者活动」（出厂产物 4 条全是误报） | 加两条豁免：名字前是「的」（定语从句中心语）、活动动词后紧跟「的/过的/了的」；4 条真实误报进夹具 | `grep -q '定语从句' bench/judges/state_judge.py && ! grep -lE '走过的路\|走的那年\|站在跳板中间的老' bench/results/v2/*/violations.json` | ⬜ |
| 3 | 跨模型族一致 | 🔴 | `bench/calib/real_text.py` | 「真实散文零误报」是空测（探针名不在语料中） | 校准账本改用**语料里真实出场的角色**当死者（调用方传入名字表） | `grep -q 'def corpus_dead_names' bench/calib/real_text.py` | ⬜ |
| 4 | 跨模型族一致 | 🟠 | `bench/runner/batch.py` | 截断但非空的章节被当完整章 | 生成结果带 `finish_reason`；`length` 截断章记错误不落盘 | `grep -q 'finish_reason' bench/runner/batch.py && grep -q 'finish_reason' bench/runner/llm.py` | ⬜ |
| 5 | 跨模型族一致 | 🟠 | `bench/report/metrics.py` | 门禁配对测量未套用汇总剔除规则 | `gate_contribution` 接 `expected_chapters` 并跳过未完成/缺判决 | `grep -q 'def gate_contribution(runs: dict, expected_chapters' bench/report/metrics.py` | ⬜ |
| 6 | 跨模型族一致 | 🟠 | `bench/universe/generator.py` | ≥3 条不变量对输出恒真（同源比较） | 反推校验改为对独立重算的期望值比较，并补阴性对照 | `grep -q 'def _expected_reveal_chapter' bench/universe/generator.py` | ⬜ |
| 7 | 跨模型族一致 | 🟠 | `docs/canonbench-results-v2.md`、`README.md` | 「跨两期复现」表述过强（v1 是密度口径翻转、绝对数是好的） | 改为「v2 两口径同向；v1 仅密度口径」 | `! grep -q '跨两期跑批复现' README.md docs/canonbench-results-v2.md` | ⬜ |
| 8 | 跨模型族一致 | 🟡 | `docs/BENCH_PROTOCOL.md`、`canonbench-writeup.md` | 复现协议首条命令分支不存在；报表路径被 gitignore | 分支名改 `main`；报表路径改 `bench/results/v2/report.md` | `grep -q 'bench/results/v2/report.md' docs/canonbench-results-v2.md && ! grep -rq 'checkout feat/canonbench' docs/` | ⬜ |
| 9 | 含交叉 · 中置信 | 🟡 | `bench/report/metrics.py`、`html.py` | 榜单只给密度，与自家协议「绝对数成对」相抵触 | 榜单加绝对违反数列 | `python3 -m bench.report.metrics bench/results/v2 | grep -qE '名次.*绝对'` | ⬜ |
| 10 | 含交叉 · 中置信 | 🟡 | `bench/calib/real_text.py` | `cons-pov` 诱饵分辨不出 m0.2.0/m0.3.0 | 诱饵改为「行首引号 + 其后旁白第一人称」（正是 m0.3.0 修的那类） | `grep -q '“这是我的。”我蹲进凹陷' bench/calib/real_text.py` | ⬜ |
| 11 | 单家独有 | 🟢 | `bench/runner/batch.py` | `generated_at` 是最后写入时间 | 记 `generated_at`（首次）与 `rejudged_at`（重判另记） | `grep -q 'rejudged_at' bench/runner/batch.py` | ⬜ |
| 12 | 单家独有 | 🟢 | `reviews/review.yaml` | 审核员被权限白名单卡住（三家全 FAILED 主因）；codex 标题式结论被判 0 条 | 第二轮配置放行本地只读脚本；工单强制表格格式 | `grep -qE 'allowedTools\|permission' reviews/review.yaml && grep -q '严重度表' reviews/brief.md` | ⬜ |
