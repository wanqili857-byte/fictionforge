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
| 2 | 跨模型族一致 | 🔴 | `bench/judges/state_judge.py` | 定语从句/比喻框被当成「死者活动」（出厂产物 4 条全是误报） | 加两条豁免：名字前是「的」（定语从句中心语）、活动动词后紧跟「的/过的/了的」；4 条真实误报进夹具 | `python3 tests/test_corpus_control.py`（关系从句三类豁免 + 名字前带定语仍报，回滚判据即红） | ⬜ |
| 3 | 跨模型族一致 | 🔴 | `bench/calib/real_text.py` | 「真实散文零误报」是空测（探针名不在语料中） | 补**三项对照**：中性注入（增量必 0）/ 正对照（把语料里真实出场的角色声明为死者，判据必须开火）/ 可测性标注（不可测时标 n/a）；文档把结论降级为「召回与豁免有证据，真实负载误报率未测」 | `grep -q 'def positive_control' bench/calib/real_text.py && python3 tests/test_calib.py` | ✅（第二轮重验） |
| 4 | 跨模型族一致 | 🟠 | `bench/runner/batch.py` | 截断但非空的章节被当完整章（glm mid ch5 仅 146 字） | 生成结果带 `finish_reason`；`length` 截断章记错误不落盘；glm max_tokens → 65536 | `python3 tests/test_runner_batch.py`（截断章记 error 不落盘——回滚守卫即红） | ✅ |
| 5 | 跨模型族一致 | 🟠 | `bench/report/metrics.py` | 门禁配对测量未套用汇总剔除规则 | `gate_contribution` 接 `expected_chapters` 并跳过未完成/缺判决 | `python3 tests/test_report_metrics.py`（半截/未完成 run 不进配对统计） | ✅ |
| 6 | 跨模型族一致 | 🟠 | `bench/universe/generator.py` | ≥3 条不变量对输出恒真（同源比较） | 不变量在 docstring 里**分成两类**（语义检查 / 构造保证回归检查，后者在 generate() 内不可能失败）；为**每条**补阴性对照证明它会红；时间线/物品判据的「无正例」写明是宇宙不产生该情形，召回由直接单测覆盖 | `python3 tests/test_universe.py` | ✅ |
| 7 | 跨模型族一致 | 🟠 | `docs/canonbench-results-v2.md`、`README.md` | 「跨两期复现」表述过强（v1 是密度口径翻转、绝对数是好的） | 三处文档改为订正叙述：初版 −2.06 → 修判定器误报 −0.92 → 修截断章 −0.18（噪声内） | `grep -q '一次公开的订正' README.md && grep -q '大半是缺陷造成的' docs/canonbench-results-v2.md` | ✅ |
| 8 | 跨模型族一致 | 🟡 | `docs/BENCH_PROTOCOL.md`、`canonbench-writeup.md` | 复现协议首条命令分支不存在；报表路径被 gitignore | 分支名改 `main`；报表路径改 `bench/results/v2/report.md` | `grep -q 'bench/results/v2/report.md' docs/canonbench-results-v2.md && ! grep -rq 'checkout feat/canonbench' docs/` | ✅ |
| 9 | 含交叉 · 中置信 | 🟡 | `bench/report/metrics.py`、`html.py` | 榜单只给密度，与自家协议「绝对数成对」相抵触 | 榜单每格改为「密度（绝对）」；报表与页面同步 | `python3 -m bench.report.metrics bench/results/v2 \| grep -qE '名次.*绝对'` | ✅ |
| 10 | 含交叉 · 中置信 | 🟡 | `bench/calib/real_text.py` | `cons-pov` 诱饵分辨不出 m0.2.0/m0.3.0 | 加 `positive_extra`：行内引号之后的旁白第一人称（m0.3.0 修的那类）必须有正例覆盖 | `grep -q 'positive_extra' bench/calib/real_text.py && python3 tests/test_calib.py` | ✅ |
| 11 | 单家独有 | 🟢 | `bench/runner/batch.py` | `generated_at` 是最后写入时间 | 重判时沿用旧 `generated_at`，重判时间入 `notes.rejudged_at` | `grep -q 'rejudged_at' bench/runner/batch.py` | ✅ |
| 13 | 含交叉 · 中置信 | 🔴 | `bench/runner/batch.py` | **续跑只信 run.json 自报**：文件被删后 manifest 仍称完成 → 跳过（本轮实测踩到：删两条截断章后自报 6/6、磁盘只有 4 章） | skip 前校验章文件齐 + 判决文件在 | `python3 tests/test_runner_batch.py` | ✅ |
| 14 | 单家独有 | 🟠 | `bench/calib/real_text.py`（方法论证） | 「孪生句只差一个词，故差异只来自判据」的论证前提不成立（注入也改了段落结构） | 加**中性注入对照**：同位置插一句无害句，增量必须为 0 | `python3 tests/test_calib.py`（中性注入增量必须为 0） | ✅ |
| 16 | 单家独有 | 🟡 | `.github/workflows/ci.yml`、`docs/canonbench-writeup.md` | CI 计数与 CHANGELOG 不符；`test_mcp_sdk` 不在 CI（本地无 SDK 时自我 SKIP → 「绿」掩盖「没跑」）；writeup 把 full 档描述成含「状态回写/返修篇幅」，实现里没有 | MCP SDK 版单列一个 py3.12 job；CHANGELOG 计数改为不写死；writeup 改为「门禁只做减法，不做状态回写」 | `grep -q 'mcp-sdk' .github/workflows/ci.yml` | ✅ |
| 12 | 单家独有 | 🟢 | `reviews/review.yaml` | 审核员被权限白名单卡住（三家全 FAILED 主因）；codex 标题式结论被判 0 条 | 第二轮配置放行本地只读脚本；工单强制表格格式 | `grep -qE 'allowedTools\|permission' reviews/review.yaml && grep -q '严重度表' reviews/brief.md` | ⬜ |

### 第二轮对**这份台账**的审计结果（doubao）

> 第一轮我写「全部改为今天必红、修完才绿的形式」——**这句话本身不成立**。
> 第二轮 doubao 抽查后指出：**#2 / #4 / #5 / #7 / #14 五行的 check 只验证「作者做了那个动作」**：
> `grep -q '定语从句'`、`grep -q 'finish_reason'`、`grep -q 'NEUTRAL_SENTENCE'`……
> 这些字符串在修复之前就存在，回滚修复照样绿。**能红 ≠ 验证了问题**。
> 五行已全部换成指向判别性测试的 check（第二轮重验）。这条比任何单点发现都重要：
> 上一轮学会的是「断言要能红」，这一轮学到的是「红了要红在对的地方」。


## 十、补记：被 🟠 吞掉的那 16 条

第一轮的处置台账是**基于残缺的输入**做的——quorum 的严重度正则不认 🟠，
而我在工单里明写「严重度用 🔴/🟠/🟡/🟢」。实测：46 条发现只解析出 30 条，
其中 doubao 交 17 条只进来 8 条，**门禁照样判 ok**。
（这条本身已修，见 2-20。）下面是把 30 条里没覆盖到的部分补完。

| # | 置信度 | 严重度 | 位置 | 问题 | 处置 | check | status |
|---|---|---|---|---|---|---|---|
| 2-20 | 单家 kimi（工具侧） | 🟠 | `quorum/gates.py` | **严重度 🟠 被静默丢弃**：`SEVERITY_RE` 只认 🔴🟡🟢，而工单写了四级；丢行不报错，门禁照判 ok——46 条只进来 30 条 | 正则补 🟠（`severity_of` 同步分四级）；`split_table_rows` 回填 `dropped_severity`；`evaluate` 无条件提示 | `cd /Users/ayu/ayu/quorum && python3 -m pytest tests/test_row_alias.py -q` | ✅ |
| 2-21 | 单家 kimi（工具侧） | 🟠 | `quorum/gates.py` | **「位置」列认不出**：工单表头叫「文件:行」，解析器只认「位置」→ 交叉表位置全空、零报错，而交叉表正是按位置对齐的 | 加**列名别名表**（位置←文件:行/文件/路径；证据←怎么查出来的/查证…）；认不出时出声 | `cd /Users/ayu/ayu/quorum && python3 -m pytest tests/test_row_alias.py -q` | ✅ |
| 2-22 | 单家 codex | 🟠 | `bench/judges/state_judge.py` | `pre.endswith("的") → 整句豁免` 把**名字后面的动作**一起豁免：「失踪已久的老麦端着水走过来」MISS，而「老麦端着水走过来」HIT | 该豁免只作用于名字**前面**的谓语（`relative_head`）；名字后的动作一律算数 | `python3 tests/test_corpus_control.py` | ✅ |
| 2-23 | 两家（kimi+doubao） | 🟠 | `bench/judges/state_judge.py` | 软标记跨小句豁免：「像往常一样，老麦走过来」里前一句的 像 管住了后一句的 走 → 真复活漏报 | 豁免加**小句下界**（标记须与名字同小句、且在动作之前） | `python3 tests/test_corpus_control.py` | ✅ |
| 2-24 | 两家（doubao+codex） | 🟠 | `bench/judges/state_judge.py`（docstring） | **自认边界的依据已过期**：docstring 写「比喻词收窄 = 误报 4 : 真阳性 0，故保留宽豁免」，而那 4 条误报早被关系从句豁免独立修掉，作者从未重测 | 两家各自实验：去掉比喻/传闻词重判 9 个 run → **新增误报 0**。docstring 改为记录这次推翻，并写明现行规则是「按管辖范围判」 | `grep -q '已被推翻' bench/judges/state_judge.py && grep -q '按管辖范围判' bench/judges/state_judge.py` | ✅ |
| 2-25 | 单家 doubao | 🟠 | `reviews/dispose.md`（第一轮台账 #2/#4/#5/#7/#14） | **第一轮台账里 5 行 ✅ 是装饰性断言**：`grep -q '定语从句'` / `'finish_reason'` / `'NEUTRAL_SENTENCE'` —— 修复前字符串就在，回滚照样绿 | 五行全部换成指向判别性测试的 check，并逐条做回滚实验 | `! grep -qE "grep -q '(NEUTRAL_SENTENCE|finish_reason|定语从句|一次公开的订正)'" reviews/dispose.md` | ✅ |
| 2-26 | 单家 doubao | 🟡 | `reviews/dispose.md` | 台账 check 单元格里的**裸 `|`**（管道）会切断 Markdown 表格 | 转义为 `\|` | `grep -c 'metrics bench/results/v2 \\| grep' reviews/dispose.md` | ✅ |
| 2-27 | 两家（kimi+doubao） | 🟠 | `bench/calib/real_text.py` | 校准的「100%」把**同一条刺激在 N 段载体上重复**当成 N 次独立试验来报比率 | 渲染表加「刺激条数 × 载体」列（`1 × 2` = 一条正例 × 两段载体） | `python3 -m bench.calib.real_text novels/静默轨道/chapters \| grep -q '刺激条数'` | ✅ |
| 2-28 | 三家一致 | 🟠 | 文档 | 文档里的复现命令跑不出文档里的数字 | **部分**：`corpus_control` / `metrics --write` / 各套件已实测可跑；校准文档 §4 那条命令已随 2-27 重写 | ⬜ 仍待逐条跑 |

### 2-25 的回滚实验（第一轮台账重验）

| 回滚什么 | 结果 |
|---|---|
| 关系从句豁免退回 `(过\|了)?的` | 红（`tests/test_corpus_control.py`） |
| 截断章守卫失效 | 红（`tests/test_runner_batch.py`） |
| `gate_contribution` 不接期望章数 | 红（`tests/test_report_metrics.py`） |
| 删掉中性注入对照 | 红（`tests/test_calib.py`） |
| 订正叙述从 README/results 撤掉 | 红（文本断言——这类修复的交付物**就是**措辞，所以查文本是对的；反模式是查修复前就存在的代码符号） |


---

# 第二轮（2026-10-01/02）——从「审计结论」升级到「审计前提」

> 第一轮工单问的是「你自称的保证是不是真的」。第二轮加了三条：
> **① 放行本地只读脚本**（上一轮两家被权限白名单挡住，最强的可执行验证一条没跑成）；
> **② 强制结论表格**（上一轮 codex 用标题式，门禁解析 0 条）；
> **③ 审上一轮那份台账本身**。

## 六、谁审的

| 审核员 | harness | 模型 | vendor | role | 结果 |
|---|---|---|---|---|---|
| kimi | `claude -p`（方舟 Anthropic 兼容端点） | kimi-k2.7-code | moonshot | primary | **ok · 9 条** |
| doubao | `claude -p`（同一端点） | doubao-seed-2-1-pro | bytedance | primary | **ok · 8 条** |
| codex | `codex exec -s read-only` | deepseek-v4-flash | deepseek | cross | **ok · 13 条** |

三家全部过门禁——**上一轮的两处阻断（权限白名单、结论格式）确实是主因**：
上游 quorum 补了 `channels.*.args` 与硬输出契约之后，同样的模型立刻交得出东西。

## 七、quorum 报的「材料快照不一致」是**假警报**——而且它自己就是发现

`quorum plate` 打红旗：kimi 审的是 `git:4ce3d64b`，另两家是 `git:424503f2`，
同一 HEAD、工作区干净、文件数都是 184，却不同指纹。查下去：

| 时刻 | 事件 |
|---|---|
| 23:19:22 | 提交 85ad4f1（三家的 HEAD 都是它） |
| 23:19:2x | kimi 取快照，工作区干净 |
| **23:19:29** | `bench/universe/__pycache__/generator.cpython-312.pyc` 被重写 |
| 23:31 / 23:37 | doubao / codex 取快照 |

**材料一个字没动，动的是一个 `.pyc` 生成物**，而它被算进了材料指纹——
`snapshot_exclude: ["__pycache__"]` 从来没生效过：`_excluded` 对非 glob 模式
只按**仓库根前缀**匹配，`__pycache__` 这种「到处都有」的名字只能命中根目录那一个，
嵌套的 19 个 `.pyc` 全部漏网（旧版修过同一类的第一处，这是第二处）。
**假警报和真警报长得一模一样**，这次纯靠人肉查出来。已提上游补丁 + 判别性测试。

> 教训写在这里：审计工具自己也是被测对象。第一次是它抓出我台账里 8 条装饰性断言，
> 这次是我查出它一个静默失效的配置项。

## 八、发现汇总与核验（25 簇 → 逐条核）

**最重的一条（codex F16 + kimi F6，两家独立，均已在本地复算证实）**：
入库的 9 个官方 run 里，**状态轴零命中**。全部 60 条修前判决 = 禁词 29 / 篇幅 22 /
段落 6 / 视角 3，**状态/知识边界/上下文腐坏 0 条**。也就是说：一个叫「长程一致性」
的基准，榜单排序实际由文体与篇幅决定。而 `core_*` 的定义里明写着
「即叙事一致性（状态/视角/文体/知识边界）」。

处理**不是**改措辞：补**语料级对照**，把「零命中是观测还是判据失效」量出来——
正例注入 333/333 全中、孪生负例 0/297 误报。结论因此变成可站住的两句话：
判据是活的；**这 6 章语料在一致性轴上没有区分度**。

## 九、处置台账（第二轮）

> 判定标准与第一轮相同：**把这个修复回滚，这条 check 会不会红？**
> 本轮 16 行 check **逐条做过回滚实验**（见下），红的一律贴出来。

| # | 置信度 | 严重度 | 位置 | 问题 | 处置 | check | status |
|---|---|---|---|---|---|---|---|
| 2-1 | 跨模型族一致（三家） | 🔴 | `README.md`、`docs/canonbench-writeup.md` | 已被作者公开撤回的「状态类判据零误报」仍写在首屏与方法文诚实清单里 | 两处改写为「召回与豁免有证据、误报率未测」，并指向语料级对照 | `! grep -qE '状态类判据.{0,6}零误报' README.md docs/canonbench-writeup.md` | ✅ |
| 2-2 | 单家 kimi | 🔴 | `bench/report/html.py` | 榜单页**现算**的数字与写死的结论卡自相矛盾：卡片仍渲染已撤回的 glm 反转头条 | 卡片改为**从数据现算**（`_harness_finding`），另加状态轴贡献卡（`_state_axis_card`） | `! grep -q '反直觉发现' bench/report/html.py` | ✅ |
| 2-3 | 单家 kimi | 🔴 | `bench/judges/state_judge.py` | 四条状态探针里两条**不读正文**（只读账本=spec），对任何模型恒定；spec 写错会被记成模型违规 | 移出模型判据，归 `generator.invariants()`；补物品持有不变量 | `python3 tests/test_state_judge.py` | ✅ |
| 2-4 | 单家 codex | 🔴 | `bench/calib/` | 状态轴零命中在产物里**分不出**「判据失效」还是「语料没有」 | 新增 `corpus_control.py`：入库语料上的正/负对照；进 CI、进页面、进文档 | `python3 tests/test_corpus_control.py` | ✅ |
| 2-5 | 跨模型族（kimi+doubao） | 🟡 | `tests/test_universe.py`、`bench/universe/generator.py` | 上一轮台账称「为**每条**不变量补阴性对照」不成立：14 个失败分支只覆盖 10 个 | 补 4 条对照（expanded 节数/无锚点节/锚点时间线/物品 transfer）+ **覆盖计数断言** | `python3 tests/test_universe.py` | ✅ |
| 2-6 | 单家 doubao | 🟡 | `bench/judges/state_judge.py` | 关系从句豁免只认 `(过|了)?的`，漏 `着+的`：「老麦蹲着的那块跳板」被判成复活 | 豁免正则收到 `(?:[着过了]|[进出到完起开回上下得]{1,2})?的`；负对照 36/36 → 0/36 | `python3 tests/test_corpus_control.py` | ✅ |
| 2-7 | 单家 doubao | 🟡 | `bench/judges/text_utils.py` | 过去时豁免过宽：`当时/以前/当年/曾经` 与比喻词同属整句豁免，吞掉真复活 | 标记分**硬/软**两类；软标记只豁免**它管辖的那个动作**；相对时间跨度（三年前）算硬标记 | `python3 tests/test_corpus_control.py` | ✅ |
| 2-8 | 单家 doubao | 🟡 | `docs/canonbench-calibration.md`、`bench/calib/real_text.py` | 「孪生句只差一个词，故差异只来自判据」是假的（原文给的反例自己就不成立），F5 的处置没订正它 | 措辞改为「同载体、同位置、同插入长度」，并写明死人复活那一对差的是**谓语类型** | `grep -q '同载体、同位置、同插入长度' docs/canonbench-calibration.md bench/calib/real_text.py` | ✅ |
| 2-9 | 单家 codex | 🟡 | `bench/report/metrics.py` | 「不参与汇总」脚注循环里 `break`，多个残缺 run 只有一个有说明 | 去掉 `break`；补两残缺 run 的判别性测试 | `python3 tests/test_report_metrics.py` | ✅ |
| 2-10 | 单家 codex | 🟡 | `bench/results/v2/results.json` | 入库的「官方产物」汇总**不是那次跑批写出的那一份**：`universe.title` 是占位串 `"T"`（手工拼的） | 重判重写为真标题；加身份测试（title 必须等于生成器同 seed 的输出） | `python3 tests/test_report_metrics.py` | ✅ |
| 2-11 | 单家 doubao/codex | 🟡 | `tests/test_attacks.py` | 抗刷分基线在测试里是**第二份手抄拷贝**，与包内注释「公开数字的输入必须只有一份」矛盾 | 改为 `BASE = BASELINE_TEXT`（引用，不抄） | `grep -q 'BASE = BASELINE_TEXT' tests/test_attacks.py` | ✅ |
| 2-12 | 单家 codex | 🟡 | `bench/report/metrics.py` | `pass_k` / `bootstrap_ci` 有实现、有单测、**零调用**（与当初 `leaderboard` 同一个病） | 删除；k≥3 落地时按需重引入（记入 `VERSION_PLAN.md`） | `! grep -q 'def pass_k' bench/report/metrics.py` | ✅ |
| 2-13 | 单家 kimi | 🟡 | `bench/calib/real_text.py` | `needs_entity_in_corpus` 声明 5 处、**读取 0 处**：可测性标注从未进结论 | 接线到 `baseline_is_evidence`，渲染表加「基线是证据的文本」列 | `python3 tests/test_calib.py` | ✅ |
| 2-14 | 单家 kimi+codex | 🟡 | `docs/canonbench-writeup.md` | 同一节自相矛盾：表头写判定器 m0.4.0，注解 5 写「本表为 m0.3.0」 | 统一为判决链 m0.1.0→m0.4.0→m0.5.0，并写明 m0.4.0→m0.5.0 对本批产物**逐条零差异** | `! grep -q '本表为 .m0.3.0.' docs/canonbench-writeup.md` | ✅ |
| 2-15 | 单家 kimi | 🟢 | `docs/canonbench-writeup.md` | 正文说术语轰炸「重复率 0.09」，同节表格写 0.00 | 实测复算为 **0.00**（40 个双字词各不相同），改正文 | `! grep -q '重复率 0.09' docs/canonbench-writeup.md` | ✅ |
| 2-16 | 单家 kimi | 🟡 | `bench/report/metrics.py` | F8 修得不完整：`aggregate_by_model` 推导出的期望章数没传给门禁配对，同一份 runs 两套剔除规则 | 调用点改传**推导后**的 `expected`；补走 `aggregate_by_model` 的判别性测试 | `python3 tests/test_report_metrics.py` | ✅ |
| 2-17 | 单家 kimi | 🟡 | `bench/runner/batch.py`、`docs/BENCH_PROTOCOL.md` | `generated_at` 在入库产物里全被刷成同一次合并时刻，与文档写死的跑批日期矛盾 | 原始日期**不可恢复**，故改为写明字段语义：它不是跑批日期，跨版本核对用 judge 版本；协议加字段表 | `grep -q '不能当跑批日期' docs/BENCH_PROTOCOL.md` | ✅ |
| 2-18 | 单家 doubao（**第一轮漏项**） | 🟢 | `bench/report/html.py` | `SERIES` 定义后从未被引用，CSS 只定义 `--s1..--s3`，第 4 个模型起颜色变量不存在 → 柱与图例一起退化 | 颜色改为**由 SERIES 生成**（`_css()`/`series_color()`），颜色跟随模型身份；超槽位归 `--s-over`，不循环配色 | `python3 tests/test_report_html.py` | ✅ |
| 2-19 | 上游 quorum | 🟡 | `quorum/snapshot.py` | `_excluded` 对非 glob 模式只按仓库根前缀匹配，嵌套 `__pycache__` 全部漏网（本轮假警报成因） | 改为**路径段**匹配（相等/前缀/后缀/中间）；补判别性测试 | `cd /Users/ayu/ayu/quorum && python3 -m pytest tests/test_quorum.py -q` | ✅ |

### 回滚实验记录（本轮 check 的判别性证据）

| 回滚什么 | 期望 | 实测 |
|---|---|---|
| 关系从句豁免退回 `(过|了)?的` | 红 | 红（负对照 着+的 转红） |
| 相对时间跨度退回软标记 | 红 | 红 |
| 软标记退回「句内出现即豁免」 | 红 | 红（两个正例转 0） |
| 差分口径退回 probe_id 集合 | 红 | 红 |
| 脚注循环 `break` 回来 | 红 | 红 |
| 删一条不变量阴性对照 | 红 | 红（覆盖计数 14 < 15） |
| 可测性字段写死 `True` | 红 | 红 |
| 颜色退回 `var(--s1)` | 红 | 红 |
| `gate_contribution` 传回原参数 | 红 | 红 |
| `results.json` title 改回 `"T"` | 红 | 红 |
| quorum `_excluded` 退回根前缀 | 红 | 红 |

### 未处置 / 待复核（如实列出，不装作都修了）

| 发现 | 状态 |
|---|---|
| codex F17「至少一条 held-out 事实」恒真 | **未复现**：`_mutate_no_held_out()` 确实能让该分支开火，`test_every_invariant_can_fail` 里已有该用例 |
| codex F18 校准报表层混用两种计数口径 | **未复核**（第二轮新增的 `baseline_evidence_n` 只处理了可测性，口径统一另开） |
| codex F13 死人复活判据漏报远不止比喻词（构造 17 条真复活，7 条漏） | **部分修**：本轮修了软标记管辖问题；封闭动词表（34 词）未动，语料级正对照的 4 种句式全中说明常见形态没问题，边角形态仍是已知漏报 |
| codex F21 台账漏记第一轮 doubao 的 S12/S14/S15 | 已在本轮补记为 2-11（基线）/2-18（配色）；S14（449 KB）随 2-10 一并处理（README 去掉写死体积） |
| codex F3「文档里的复现命令跑不出文档里的数字」 | **部分**：README 的「30 秒跑一遍」与 `bench.calib.corpus_control` 已实测可跑；其余命令待逐条跑一遍 |
| 知识边界 / 上下文腐坏两条轴 | **仍未实装**（W4/W5），第二轮没有新的处置 |

