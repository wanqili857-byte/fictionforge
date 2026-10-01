# CanonBench 独立复核结论

> 审计员：外部独立会话（未读 `reviews/out/` 下任何同批评审产物，以保持独立性）
> 审计对象：`bench/` `docs/` `tests/` `README.md` `CHANGELOG.md` `.github/`
> 执行环境限制：本会话内 **python 解释器与 `shasum` 均被权限拒绝**（见末行命令清单），
> 因此所有结论分两类：**已用只读工具复算/复现的**，与**静态推演、未复现的**（逐条标注）。

---

## 1. 严重度表

| # | 严重度 | 位置 | 问题（一句话） | 具体失败场景 | 怎么查出来的 |
|---|---|---|---|---|---|
| S1 | **严重** | `bench/runner/llm.py:183-184`（与 `:185-202` 死块）、`bench/runner/batch.py:150-151` | `if status == 200: break` 在前，其后 185-202 的「解析 200 响应」整块**永远不会执行**；而 `call_model` 在 `while` 之后没有任何语句，函数在循环体内结束 → **HTTP 200 时函数返回 `None`** | 任何人照 `docs/BENCH_PROTOCOL.md §4` 跑 `python3 -m bench.runner.batch --seed 42 --chapters 6 --models ark-db-lite --tiers bare,mid,full --k 1 --out runs/mine`，第一次成功响应即崩：`batch.py:151 tokens_in += res.get(...)` → `AttributeError: 'NoneType' object has no attribute 'get'`。协议 §0 的目标句「任何人照本文能在自己机器上复现榜单上的一行数字」在 HEAD 上不成立 | `sed -n '174,213p' bench/runner/llm.py \| nl -ba -v174`：183-184 是 `break`，185 起是第二个 `if status == 200`，最后一个语句在 213 行且缩进 8 空格（循环体内）；`wc -l` = 212（无尾换行）。`git blame -L 173,215`：183-184 属 `b53d1e2`，185-202 属 `02b908c`；`git show 02b908c -- bench/runner/llm.py` 显示该 commit 把原先位于 **while 之后**的解析块整体搬进循环内、恰好落在 `break` 之后。`grep -rn "call_model" tests/` 无命中 → CI 结构性看不见 |
| S2 | **严重** | `bench/results/v2/ark-db-lite__mid__k0/violations.json:79,108,122`、`bench/results/v2/ark-glm-flash__mid__k0/violations.json:64` | 官方 v2 产物中 `state-dead` 共 4 条，**4 条全部是判据文档自认的误报**，且全部计入发布表的「状态」列与「核心违反」 | 发布表 `docs/canonbench-results-v2.md:18-19` 的「状态 3 / 状态 1」会被读成 3 次、1 次真复活；实际是「老麦走过的路」「老麦走的那年」这类定语从句。剔除后 doubao mid 核心 7→**4**（4.93→**2.82**/万字）、glm mid 核心 4→**3**（4.58→**3.44**/万字）。三处文档（`writeup §9 注5`、`results-v2 注5`、`CHANGELOG v0.4.0`）都写「12 条误报…判据改正向证据制后清零」，读者据此认为 state 轴已干净 | `grep -c "state-dead" bench/results/v2/*/violations.json`（db-lite mid=3、glm mid=1，其余 0）；`grep -n -A 12 "state-dead" …` 逐条读 `evidence.span`；与 `bench/judges/state_judge.py:21-23` docstring 自陈的误报样例（「老麦**走过**的路」「老麦**走**的那年」）逐字比对 |
| S3 | **高** | `bench/universe/generator.py:222-223`、`bench/judges/state_judge.py:150-152`、`generator.py:12-13` docstring | 四项状态检查中的**两项在合成宇宙上恒不可能触发**：物品双持有（宇宙只声明一次物品，`holders[item]` 长度恒为 1 → `prev_holder is not None` 永假）、时间线倒退（每章 `day = c` 单调 → `timeline_violations()` 恒空） | 对外宣传的三项状态能力里，「物品瞬移」没有任何可产出的正例；`generator.py:12-13` 的 docstring 却写「至少一次物品声明（让状态判定轴上出正例）」——一次声明**不构成**双持有正例。9 个官方 run 里 `state-item` / `state-timeline` 各 0 条 | 代码推演（`generator.py:218-224` + `state_judge.py:144-159`）；实测侧：`grep -c "state-item\|state-timeline" bench/results/v2/*/violations.json` 全为 0。**未在解释器里跑，属推理**（但结论是纯控制流，无随机/环境依赖） |
| S4 | **高** | `bench/calib/real_text.py:42`、`docs/canonbench-calibration.md:37-41`、`README.md:58-59` | 「状态类判据在 38k 字真实散文上**零误报**」对**死人复活判据是构造性恒真**：探针账本里唯一的死者是注入用的 `祁三`，判定器的 dead 集合只来自账本，正文里任何真实人物都不可能进入该集合 | 无论判定器多差，`clean_by_kind["state-dead"]` 都必然是 0——因为 `祁三` 这个词本来就不在语料里。作者把这条列为「**最重要的结论**」（calibration §1），并写进 README 首屏；它实际承载的证据量为零 | `real_text.py:58-63`（`calibration_ledger` 只声明 `deaths=[PROBE_DEAD]`）+ `state_judge.py:83-87`（dead 仅来自 `ledger.chapters[].deaths`）+ `real_text.py:42`（探针名刻意取「真实文本里几乎不会出现的名字」，注释自陈）。三角闭合，**推理，未复现** |
| S5 | **高** | `README.md:24-25`、`bench/report/html.py:258-260`、`docs/canonbench-results-v2.md:24-27`、`docs/canonbench-results-v1.md:18-20` | 头牌结论「给 glm 注入上下文反而更差（跨两期跑批复现）」在发布的精度上不成立：v2 的 Δ=**+2.06** 里有 **1.15** 来自 S2 那条自认误报（1 条 / 8725 字），剔除后是 +0.92；v1 的「复现」在绝对数上方向相反 | v1：glm bare 6107 字 / 核心 **4** 条（6.55），mid 2823 字 / 核心 **2** 条（7.08）——mid 的绝对违反数只有 bare 的一半，「更差」**完全来自分母**（`runs/v1` 产物与 `docs/canonbench-results-v1.md:18-20` 逐格核对）。每格事件数 2~4 个、k=1，无置信区间 | 逐格手算：`score/chars×10000` 全部复算命中（见附录）；`grep -A12 state-dead` 得到 v2 污染量；`docs/canonbench-results-v1.md:18-20` 读绝对数与字数 |
| S6 | **中** | `bench/report/metrics.py:407-416`（汇总表）、`:322-349`（榜单）、`bench/report/html.py:262-271` | 读者用来**比较模型**的三条输出路径（按模型汇总表、榜单、榜单页图表）**只给密度、不给绝对数**，与项目自己「密度与绝对数必须成对」的承诺相悖；`total_rate_by_tier` 算了但从不渲染 | S5 的结论正诞生在这条路径上：README 首屏、榜单页「反直觉发现」卡片、榜单表都只有「/万字」，读者看不到 glm mid 的绝对核心数是 3（= bare 的 3） | 读 `render_markdown` 汇总段（407-416 只有 `core_rate_by_tier` 与 attribution）、`leaderboard`（322-349 只有 `bare/mid/full` 三个 rate）、`html.py:189,202-204,262-266`。`grep -n "total_rate_by_tier" bench/` → 只在 `metrics.py:275,289` 赋值，无渲染处 |
| S7 | **中** | `bench/universe/generator.py:293-295`（配 `:114-115`） | 不变量「探针池非空」是**恒真分支**：`fact_ids - learned` 恒包含两条 `common=True` 的常识事实（T-01/T-02），而 `world_ids` 显式排除了 common → common 永不进 knowledge → 该差集永不为空 | 若将来 held-out 逻辑坏掉（`shuffled[:1]` 变空），唯一的守卫仍然报「自洽」。这正是 author 在 writeup §7 强调「不变量如果恒返回无违规，测试照样绿」所警示的形状 | 读 `generator.py:114-115`（`not t.get("common")`）与 `:125-129`（held_out 来自 `shuffled`），再读 `:293-295` 的判据用的是**全集** `fact_ids`。**推理，未复现** |
| S8 | **中** | `bench/calib/real_text.py:158-176, 240-243` | D13 问的「第三个同类坑」在这里：**校准在定义上不可能发现载体自身的假阳性**——语料正文的固有命中一律進 `clean_candidates`，而报告明写「不并入误报率」；同时每类判据只有**一条**手写诱饵，"特异性 100%" 的样本量是 1 句 × N 文本 | 注入方式本身也是理想化的：`_splice`（`:75-80`）把探针句插成**独立一行**，于是它必然被 `sentences()` 单独切出来判；而 `text_utils.py:11` 只按 `。！？` 切句，真实散文里一个逗号长句内任何过去时标记都会整句豁免——S2 里那条 FP span「老麦走过的路，柳娘走过的路，现在她走的路，全串在同一只盐箱的木纹里。」正是这种长句。注入句覆盖不到这个形态 | 读 `summarize`（`:198`）+ `render_markdown`（`:240-243`）+ `_splice` + `text_utils.py:11`；FP span 取自 S2 的产物 |
| S9 | **中低** | `bench/report/metrics.py:281-282` vs `:259-263` | `gate_contribution` 收到的是该模型的**全量** run，而非 `aggregate_by_model` 已经筛过的集合；缺 `violations.json` 的 run 在 `:180-182` 被兜底成空清单，于是会以 `full_runs+1、core_pre+0、core_post+0` 的身份进入「门禁贡献」表 | 失败场景：某模型有一个合格的 bare run + 一个中途崩掉、没写出 `violations.json` 的 full run → 门禁表显示「full 运行数 1，修前核心 0，修后核心 0，擦除 0」，与脚注「未完成/缺判决的 run 不进汇总」直接矛盾。v2 的 9 个 run 全完整，**未触发**，属潜伏缺陷 | 读 `aggregate_by_model:259-264`（by_model 只收筛过的）与 `:281-282`（gate 用 `runs.items()` 全量过滤）；读 `load_runs:179-182` |
| S10 | **中低** | `docs/canonbench-writeup.md:62-68` vs `bench/runner/harness.py:4-6,135-150` | writeup §5 把 full 档描述为「门禁 + **自动返修** + **状态回写**」，实现只有「删禁词 + 切超长段」；同一节的「（自动替换禁词、**返修篇幅**）」与同文 §9 注2「门禁只能做减法，**补不了篇幅**」自相矛盾 | 读者按 §5 理解 full 档的能力边界，会高估后处理门禁的贡献面；`docs/BENCH_PLAN.md:91` 同样写了「+ 自动返修 + 状态回写 + 漂移注记」 | 对读 writeup:62-68 / BENCH_PLAN:91 / harness.py:135-150；矛盾句在 writeup:184 |
| S11 | **低** | `docs/canonbench-calibration.md:4` vs `:32`、`bench/calib/real_text.py:208-219` | 校准报告的复现命令与所报那一行数字对不上：`<章节目录>` 若指向仓库自带目录，`load_corpus` 会收 **2** 个文件 | 跑 `python3 -m bench.calib.real_text novels/静默轨道/chapters --label 公开示例` 得到 **6,614 字 / 2 段**，而非报告写的 **3,171 字 / 1 段**——`第1章_v1旧管线.md`（3,443 字）的文件名不含 `状态/manifest/_revisions/.ai.` 任一关键字，不会被过滤掉。要命中报告那一行必须传**单文件路径** | `LC_ALL=en_US.UTF-8 wc -m novels/静默轨道/chapters/第1章_v1旧管线.md` → 3444；`第1章.md` → **3172**（strip 后 3171，与报告 **吻合**）；过滤规则见 `real_text.py:213-219`。字数为实测，语料合成结果**推理、未复现** |
| S12 | **低** | `tests/test_attacks.py:49-62` vs `bench/attacks/attacks.py:69-85` | 抗刷分基线在测试里仍是**第二份手抄拷贝**，与 `attacks.py:69-73` 注释「放在包里而不是测试里…公开数字的输入必须只有一份」矛盾 | 今天两份内容逐字相同（200 字、1 处「忽然」），数字未漂移；但只要有人改一处，`test_attacks.py` 的洞记录与 `html.py:233`/writeup §8 的公开表就会静默分叉——正是注释声称已经修掉的那个教训 | `sed -n '49,62p' tests/test_attacks.py` 与 `sed -n '73,85p' bench/attacks/attacks.py` 对读（`diff` 的进程替换被沙箱拒绝，改用肉眼逐行核对）；`html.py:228-229` 确认页面用 `BASELINE_TEXT` |
| S13 | **低** | `bench/results/v2/results.json:3` | 入库的汇总产物里 `universe.title` 是占位串 `"T"`，不是生成器对 seed=42 的真实输出 | `BENCH_PROTOCOL §6` 要求验收方核对「manifest 里 universe_seed + generator version 与榜单行一致」——seed 42 是对的，检查会通过；但**「官方产物」这一份不是跑批当时写出的那一份**（键序已被 `sort_keys` 重排）。逐 run 数字与源产物一致，仅标题是占位 | `grep -n "title" bench/results/v2/results.json` → `"T"`；`head -12 runs/v2/doubao/results.json` → `"雾港纪事"`；`grep -A10` 对比 run 条目 token 数（2019/49501 一致） | 
| S14 | **低** | `README.md:37` | 「共 449 KB」与现值不符 | 现为 467,180 B ≈ **456 KiB**（449 KiB 对应的是排除 `results.json`+`report.md` 后的 460,209 B——这两个文件在 2026-10-01 21:17 被重写过） | `find bench/results/v2 -type f \| xargs wc -c \| tail -1` → 467180；`ls -la bench/results/v2/` 时间戳 |
| S15 | **低** | `bench/report/html.py:23-27, 35, 137, 158` | 第 4 个模型起系列色失效：`SERIES` 常量定义了 3 组色但**从未被使用**，`render_chart:137` 与 `render_legend:158` 用位置索引 `var(--s{i+1})`，而 CSS（`:35`）只定义到 `--s3` | 扩到 4 个模型（BENCH_PROTOCOL §8 明确欢迎外部提交）时，第 4 个系列与图例色块一起落入未定义变量 → 退化为默认黑，**两处互相不可区分**；docstring「颜色跟随模型，不随名次变动」只在 roster 不变时成立 | 读 `html.py:23-27`（`SERIES` 无引用）、`:35`（`--s1..--s3`）、`:137`/`:158`。当前 3 模型**未触发** |

**对工单 §3「已知弱项清单全不全」的判断**：不全。清单漏了三条不属于「已知」的弱项——(a) 四项状态检查里两项在合成宇宙上恒无正例（S3）；(b) 校准的「零误报」对复活判据是构造性恒真（S4）；(c) 模型级汇总与榜单只给密度、不给绝对数（S6）。清单原有五条中，第 5 条（私有语料不可复核）写得**准确且诚实**，第 1 条（k=1）与第 3 条（语义判定未实装）也是真边界。

---

## 2. 最脆弱一环

**「状态一致」这条主轴，在它自己发布的官方结果里没有被任何可信正例支撑。**

三条互相独立、全部可从仓库内文件验证的支线：

1. **能触发的两项检查，产出的全是误报。** 9 个官方 run 的「状态」列合计 4 条（db-lite mid 3、glm mid 1），4 条全部是 `bench/judges/state_judge.py:21-23` 自己列名的误报形态（定语从句）。这 4 条同时被算进「核心违反」与榜单排序键所用的 core rate（S2）。
2. **另两项检查在合成宇宙上恒不可能触发。** 物品双持有（宇宙只声明一次物品）与时间线倒退（每章天号单调）永远报 0 —— 而 README 的状态能力表里写着「**物品瞬移**」（S3），`generator.py:12-13` 还声明物品声明是为了「让状态判定轴上出正例」。
3. **声称的「真实散文零误报」对复活判据是构造性恒真**：探针账本里唯一的死者是一个刻意选来不在语料中出现的名字（S4）。README 首屏与校准报告把它列为最重要的结论。

合起来：**基准对外承诺的三项状态能力（死人复活 / 物品瞬移 / 天数倒流），死人复活这一项在官方跑批里只报出误报、在校准里被测的是一个恒真命题；物品瞬移与时间线倒退在当前宇宙上不存在任何正例。** 这是最可能在同行面前站不住的地方——不是因为某个数字大了一点，而是因为「长程状态一致性」这条轴目前既没有被正例验证过，又在发布表里把自认的误报当成了成果。

（若以「同行第一分钟会撞到什么」为准，那一处是 S1：照着复现协议跑批会直接崩。）

### 工单 §E：逐条判断作者的「诚实边界」

| 边界条目 | 判断 |
|---|---|
| k=1 只能说方向，精确归因需 k≥3 | **真边界**，写法也正确。但它挡不住 S5：把 k 从 1 提到 3 修不好「Δ 里一半是误报」和「v1 的反转只在密度归一化下存在」这两个问题——那不是采样噪声，是口径污染。 |
| 合成宇宙 ≠ 真实长篇（模板重复度高，本质是校准判定器的工具） | **真边界**。但它与「校准工具」的自我定位冲突：作为校准工具，它对本基准 4 项状态检查里的 2 项没有任何校准样本（S3）。 |
| 密度永远可被加字稀释，绝对数只是缓解 | **真边界**，且是作者自己做攻击实测挖出来的，写得很好。问题恰恰是这条洞察没有被用到自家的头条对比上——模型级汇总表/榜单/README 首屏只给密度（S6），S5 就长在这条路径上。 |
| 判定器口径绑定中文/第三人称/引号约定 | **真边界**，无可反驳。 |
| **比喻词豁免的取舍（误报 4 : 真阳性 0，故保留宽豁免）** | **站不住（反驳）** ——见下。 |
| 注入法只覆盖可注入判据，账本级判据不在此列 | **真边界**，但它低估了问题：不只是「账本级没覆盖」，而是「可注入的那部分也没有真正测误报」（每类一条手写诱饵；载体自身的命中被归入一个不计入误报率的候选桶，S8）。 |

**反驳第 5 条（比喻词豁免的取舍）——三重，逐层加重：**

- **理由与证据矛盾。** 该取舍的依据是「收窄（把 像/仿佛/听说 从豁免集合里去掉）会新增 4 条误报，其中「老麦走过的路」「老麦走的那年」…」。但这两句**不含任何比喻/传闻词**，收窄与否都会被报——它们就在入库的官方产物里（S2），且以宽豁免下 m0.3.0 判定器产出。也就是说：宽豁免**根本没有豁免**被拿来论证它的例句；「4 条误报」这个代价数字被至少高估，取舍的前提不成立。
- **只计量数量，不计量代价的不对称。** 误报（假阳性）在一致性基准里的代价是「把干净运行判脏」；漏报（假阴性）的代价是「把脏运行判净」——而漏报恰是宽豁免造成的（作者自己承认「老周走过来，**像**往常一样…」会漏）。用「4 : 0」这种计数比来做取舍，等于默认两类错误的单位代价相同，而这在一个以「抓长程错误」为卖点的基准里显然不成立。
- **样本量与 k=1 之外的解释。** 「真阳性 0」的分母是 db-lite mid 的**一章**里三条句子（`state_judge.py:21-23`）。n 小到没有统计功效。k=1 之外最可能的解释不是「收窄不划算」，而是**那次实验同时改了别的东西**或**判据口径混杂**——因为被引为代价的两条例句在两种设置下行为相同，说明观察到的「+4」里混入了与收窄无关的项。正确的做法是把两类错误分开计数、把结论标注为「未定」，而不是写成已决事项（CHANGELOG v0.4.1、writeup §10、榜单页均按已决转述）。

### 工单 §E 附加题：审「那一轮三方审计的结论本身」

`02b908c`（CHANGELOG v0.4.1）声称修掉三个报表缺陷。**回滚推演：三条回归测试都会红** ——

| 缺陷 | 测试 | 回滚后 |
|---|---|---|
| `gate_contribution` 用 `not [...].get("post_fix")` 判空 | `test_report_metrics.py:232-249` | run `a` 的 `post_fix == []` → `not []` 为真 → 跳过 → `full_runs == 1 ≠ 2` → 断言红 ✓ |
| 缺 `violations.json` 兜底成空清单 | `test_report_metrics.py:252-267` | 该 run 的 `chars=100≠0`、`chapters_done=1≠0` → `incomplete` 为假 → `core_abs` 为 `0` 而非 `None` → 三条断言全红 ✓ |
| 部分完成按「本批最大完成度」剔除 | `test_report_metrics.py:270-282` | `aggregate_by_model(runs, expected_chapters=6)` → `TypeError`，测试红 ✓（以异常形式失败，不是断言失败） |

**所以那一轮审计对报表部分的结论成立，作者是对的。** 但同一 commit 里的第 4 项改动（`llm.py` 的「网关 200 + body 不可解析并入退避重试」）**没有对应测试**，而且把成功路径改坏了（S1）；commit message 里的「全 15 套件绿」为真——恰好因为套件里没有任何一条会调用 `call_model`（`tests/test_runner_batch.py:8-9` 明写「网络调用不测」）。这不是「测试没覆盖网络」，而是把一段**纯控制流**的改动交给了没有覆盖它的套件。

---

## 3. 附录：我推翻了什么

### 3.1 我推翻的（作者结论站不住的）

1. **「状态类判据在真实散文上零误报」（calibration §1 / README / writeup §10）** —— 对死人复活判据是构造性恒真，证据量为零（S4）。
2. **「12 条 state-dead 误报已清零」** —— 清零的是「遗物/回忆」那一家族；「定语从句」家族仍有 4 条活在入库产物里、活在发布表的「状态」列里（S2）。
3. **「给 glm 注入上下文反而更差（跨两期跑批复现）」** —— v2 的 Δ 有 1.15/2.06 来自自认误报；v1 的「复现」在绝对数上方向相反（S5）。
4. **「收窄比喻词豁免不划算（误报 4 : 真阳性 0）」** —— 被引为代价的例句在两种设置下行为相同，代价数字来源不成立（§E）。
5. **「密度与绝对数成对同报」** —— 在模型级汇总/榜单/榜单页三条最被阅读的路径上只有密度（S6）。
6. **「任何人照复现协议能复现榜单一行」** —— HEAD 上跑批第一条成功响应即崩（S1）。
7. **「不变量自检能失败」** —— 至少有一条（held-out 探针池）恒真（S7）。
8. **「至少一次物品声明让状态判定轴上出正例」** —— 一次声明不构成双持有正例，该检查恒报 0（S3）。

### 3.2 我一开始怀疑、查完发现作者是对的（同样有价值）

1. **判定器「零 LLM、零时间/随机/网络/环境依赖」** —— `bench/judges/*` 只 import `re`/`dataclasses`/`bench.contracts`/`bench.judges.text_utils`；输出顺序经 `sorted(dead)`、`sorted(holders.items())`、`sorted(cast_names, key=-len)` 固定。**成立。**
2. **结果表逐格自洽** —— 9 行的 `总违反 = 篇幅+文体+视角+状态`、`核心 = 总违反 − 篇幅`、`核心/万字 = 核心/字数×1e4` **逐格复算全部命中**；门禁配对 `修前核心 2+5+2=9 → 修后 0+1+0=1`、三家擦除 2/4/2 **全部命中**。
3. **抗刷分表（writeup §8）** —— 手算注入/复制/术语轰炸的长度与密度：200→1200/803/320 字，50.00→8.33/12.45/31.25，−83%/−75%/−38%，**全部命中**。基线「200 字含 1 处禁词」经 `LC_ALL=en_US.UTF-8 wc -m` 与逐行相加复核，**成立**。
4. **公开示例《静默轨道》第 1 章 = 3,171 字** —— `第1章.md` 为 3172 字符（含尾换行），`.strip()` 后 **3171**，与报告逐字吻合。我最初按 UTF-8 字节数（8973 B）估算怀疑它对不上，**是我算错了**。
5. **入库产物与源产物同源** —— `diff -rq runs/v2/doubao/ark-db-lite__bare__k0 bench/results/v2/ark-db-lite__bare__k0` 无输出；`diff runs/v2/_flat/report.md bench/results/v2/report.md` 无输出。除 S13 的 `title` 占位外，**逐字节相同**。
6. **页面/文档/报表三处同源** —— html 的榜单排序（glm 1 / ds 2 / db-lite 3）、三档 rate、门禁配对表与 `results-v2.md` / `report.md` **逐格一致**；抗刷分表输入经 `test_report_html.py:132-141` 钉死为同一份 `BASELINE_TEXT`。
7. **「那一轮三方审计」的三条报表回归测试** —— 回滚后确实会红（见 §2 附表）。
8. **`test_universe.py` 的三条阴性对照确实能失败** —— 死亡后获知、提前获知终局、死后被点名，三条都是人为注入后断言必须抓到，不是恒真断言。`test_state_judge.py:186-200` 还专门修掉过一处「空真」夹具（`老周来了` 不在活动动词表），并加了「夹具非空」断言。这部分**做得比一般项目好**。
9. **`test_calib.py` 记录的两个测量坑（信号量、差分基线）在实现里是真的** —— `_signal` 确实取 `evidence["count"]`（`real_text.py:137-150`），基线确实是干净文本的差分（`:163-165`）。两处修法**正确**。
10. **判定器版本贯穿一致** —— `batch.py:39` 的 `JUDGE_MECHANICAL_VERSION = "m0.3.0"` 与 9 个 `run.json` 的 `bench.judge_mechanical_version` 一致。

### 3.3 我**无法**验证的（本会话执行被拒，据实标注）

- **BENCH_PROTOCOL §2 的 sha256 表**（universe.json / novel_config.json / 真相表.json）—— `shasum` 与 python 均被权限拒绝，**未复算**。
- **prompt hash 逐章复算**（协议 §6「重算 sha256(system+user) 逐章吻合」）—— 同上，**未复算**。
- **判定器同输入两次逐字节一致**（工单 A1 后半）—— 代码路径上无随机源，**推理为一致**，但**未实跑**。
- **全部 15 个测试套件的实际绿/红**—— **未运行**。
- **校准在 35,136 字私有语料上的任何数字**——语料不入库，**不可复核**（这一点作者已诚实声明）。

---

## 4. 实际执行过的命令清单

```
find . -type f \( -name "*.py" -o -name "*.md" -o -name "*.json" \) | grep -v .git/ | grep -v node_modules | sort | head -200
ls -la ; git log --oneline | head -30
find bench docs tests novels reviews -type f | sort
find bench -type f -name "*.py" | xargs wc -l | sort -n
cat bench/results/v2/results.json | head -60
cat bench/results/v2/ark-db-lite__bare__k0/run.json
cat bench/results/v2/report.md
git log --oneline -- bench/runner/llm.py
git blame -L 173,215 bench/runner/llm.py
git show --stat 02b908c | head -40
git show 02b908c -- bench/runner/llm.py
sed -n '174,213p' bench/runner/llm.py | nl -ba -v174
wc -l bench/runner/llm.py
grep -c "state-dead" bench/results/v2/*/violations.json
grep -c "state-day" bench/results/v2/*/violations.json
grep -c "state-item\|state-timeline" bench/results/v2/*/violations.json
grep -n -A 12 "state-dead" bench/results/v2/ark-db-lite__mid__k0/violations.json
grep -n -B4 -A 12 "state-dead" bench/results/v2/ark-glm-flash__mid__k0/violations.json
head -12 runs/v2/doubao/results.json ; head -12 runs/v2/glm/results.json ; head -30 runs/v2/_flat/report.md
grep -rn "title" bench/results/v2/results.json ; ls -la bench/results/v2/
diff -rq runs/v2/doubao/ark-db-lite__bare__k0 bench/results/v2/ark-db-lite__bare__k0
diff runs/v2/_flat/report.md bench/results/v2/report.md
grep -c "" runs/v2/_flat/report.md bench/results/v2/report.md
grep -n "榜单" runs/v2/_flat/report.md bench/results/v2/report.md
grep -A 10 'ark-db-lite__bare__k0' runs/v2/doubao/results.json
grep -A 10 'ark-db-lite__bare__k0' bench/results/v2/results.json
wc -m -c novels/静默轨道/chapters/*.md
LC_ALL=en_US.UTF-8 wc -l -m novels/静默轨道/chapters/第1章.md
LC_ALL=en_US.UTF-8 wc -m novels/静默轨道/chapters/第1章_v1旧管线.md novels/静默轨道/chapters/章节状态.md
git log --oneline -3 -- novels/静默轨道/chapters/第1章.md
find bench/results/v2 -type f | xargs wc -c | tail -1
du -sk bench/results/v2
ls scripts/ ; sed -n '1,40p' .github/workflows/pages.yml
grep -rn "call_model\|parse_response\|_should_retry\|resolve_proxy" tests/ | head -30
cat .github/workflows/*.yml | head -60
ls .venv/bin
sed -n '49,62p' tests/test_attacks.py | nl -ba -v49
sed -n '73,85p' bench/attacks/attacks.py | nl -ba -v73
```

**尝试执行但被本会话权限拒绝（未获得任何输出，相应的复算项已在 §3.3 标注为「未复现」）：**
`python3 -c …`（多次）、`.venv/bin/python …`、`python3 tests/test_state_judge.py`、`python3 -B -c …`、`shasum -a 256 …`、`diff <(sed …) <(sed …)`（进程替换）、以及 MCP 工具 `mcp__lcb__universe_generate`。全部 python 执行与 MCP 调用均返回「requires approval」，未获批准；因此本报告**没有任何一个数字来自运行代码**，全部来自只读复算（`wc`/`diff`/`grep`/`git`）与代码路径推演。
