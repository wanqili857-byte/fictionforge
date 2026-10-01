# CanonBench 独立复核结论

> 审核员：kimi（moonshot，第三方 harness，role=primary）
> 日期 2026-10-01 · 只读复核

## 0. 先说执行环境的硬限制（它决定了下面每条的「复现状态」）

本会话的 Bash 权限白名单只放行了只读文本工具（`ls/find/cat/sed/grep/sort/uniq/cut/wc/head/tail/tr/comm/expr/git/echo`）。**`python3 -c`、`python3 -m`、`node -e`、`openssl`、`shasum`、`awk`、`bc` 全部被拦（"requires approval"）。** 唯一放行的解释器形式是 `python3 --version`。

因此：**工单里所有需要跑代码的验证我都没跑成**——测试套件、宇宙 sha256、逐字节确定性、不变量阴性对照、`--rejudge` 重判一致性、校准脚本，一律未执行。我把这部分改成两条腿走路：(a) 代码/历史静态审查；(b) 从 `bench/results/v2/` 的产物文件直接重算数字（`grep/sort/uniq/wc` 能做到）。

**凡是「推理，未复现」的我都标了。凡是标「已重算」的，数字是我自己从产物文件数出来的，没抄任何文档。**

---

## 1. 严重度表

| # | 文件:行 | 问题 | 具体失败场景 | 严重度 | 怎么查出来的 |
|---|---|---|---|---|---|
| **F1** | `bench/runner/llm.py:183-184`（`break`）、`:185`（死代码） | **`call_model()` 在 HTTP 200 时返回 `None`**。`if status == 200: break` 之后紧跟一个新的 `if status == 200:` 解析块——`break` 先执行，循环退出，函数体到 213 行结束，**函数隐式返回 None**；185-202 行的解析/返回是死代码。 | `python3 -m bench.runner.batch ...` 第一次成功生成 → `batch.py:151` `tokens_in += res.get("tokens_in", 0)` → `AttributeError: 'NoneType' object has no attribute 'get'`。整条真跑批路径从第一次成功调用起就崩。CHANGELOG v0.4.1 声称修好的「网关 200 + 不可解析 body 并入退避重试」是不可达代码。 | **致命** | `git show 02b908c -- bench/runner/llm.py`：该 commit 删掉了 while 循环**之后**的 `try: out = parse_response(r.json()) ... return out` 块（原版可用），把替代实现插到了 `break` **之后**、循环体**之内**。`grep -n "" bench/runner/llm.py \| tail -32` 确认 183/184/185 三行顺序，且文件止于 213 行、全部在循环体内。**静态确认，未执行**（见 §0）。 |
| **F2** | `bench/judges/state_judge.py:96` + `bench/judges/text_utils.py:23-27` | **官方跑批里 4 次「死人复活」告警，4 次全是误报，真阳性 0。** | 三条出现在 db-lite mid（ch4/ch5），一条在 glm-flash mid（ch3），全部计入「状态」族与核心违反率（db-lite mid 核心 7 条里有 3 条是这些）。同时 `state_judge.py:20-23` 的 docstring 用「收窄实测代价更大：+4 条误报、真阳性 0」为宽豁免辩护——但**这 4 条误报在当前的宽豁免版本里就是存在的**。 | **高** | `grep -A6 '"probe_id": "state-dead' bench/results/v2/*/violations.json`，再 `grep -n` 回正文核对：`db-lite__mid__k0/ch4.md:52`「老麦走过的路，柳娘走过的路，现在她走的路，全串在同一只盐箱的木纹里。」、`ch5.md:21`「老麦走的那年，盐场的烟囱就再也没冒过烟。」、`ch5.md:93`、`glm-flash__mid__k0/ch3.md:95`。四句都是定语/隐喻指称，无一句是行动。**已重算（产物文件）** |
| **F3** | `bench/calib/real_text.py:58-63, 164` | **「状态类判据在真实散文上零误报」是构造性恒真，不是测量。** `calibration_ledger()` 的 `dead` 集合只有一个名字 `祁三`（`:42`），而它在任何载体语料里都不出现；`state_judge` 只对账本 `dead` 集合里的名字开火 → `clean_by_kind["state-dead"] ≡ 0` **与判据质量无关**。 | `docs/canonbench-calibration.md:37-41` 把它列为「最重要的结论」，`README.md:58-59` 写进对外首屏。这条结论对「判定器在真实人名上会不会误报」零信息量——而 F2 给出的正是真实人名上的 4/4 误报。 | **高** | 读 `real_text.py` 的账本构造 + `state_judge.py:84-101` 的 `dead` 来源；`grep -o "祁三" bench/results/v2/*/ch*.md` 无命中。**推理，未复现（跑不了校准脚本）** |
| **F4** | `docs/canonbench-results-v1.md`（表）+ `docs/canonbench-writeup.md:185-187`、`docs/canonbench-results-v2.md:40-42` | **头条结论「注入上下文让 glm 更差」在不同指标下符号相反。** v1：glm bare 6107 字 / 核心 4 条 → 密度 6.55；mid 2823 字 / 核心 **2** 条 → 密度 7.08。**按绝对数，mid 是改善（4→2）；按密度，mid 是退步（6.55→7.08）。** | 项目自己规定「密度与绝对数成对同报」，而两把尺子在对头条发现上给出相反答案时，文档只报了密度那一把。更糟的是 v1 的 mid 只写了 bare 的 46% 字数——**这正是「写得少 → 密度虚高」的镜像，而文档只讨论了「加字稀释」这一个方向**。榜单（`metrics.py:342`）又只按密度排序。 | **高** | 从 `docs/canonbench-results-v1.md` 逐行重算：4/6107×1e4=6.55、2/2823×1e4=7.08、2/8061×1e4=2.48。**已重算（文档内算术自洽；v1 原件未入库，无法从产物复算）** |
| **F5** | `bench/report/metrics.py:139-162` | **门禁配对测量没套用汇总的剔除规则。** `gate_contribution` 只查 `tier=="full"` 和 `"post_fix" in ...`，不查 `is_incomplete` / `violations_missing` / `chapters_done < expected`——而 `aggregate_by_model:243-251` 的 docstring 明写这些 run「不进汇总」。 | 一个跑 2/6 章的 full 运行：修前/修后核心数照常计入「门禁贡献」表并参与跨模型合计；同一个 run 在「每次运行」表里被标 ⚠ 部分完成、「不参与汇总」。两张表对同一批 run 用了两套口径。v2 里没触发（9 个 run 全部 `chapters_done=6`），是潜伏缺陷。 | **中** | 读 `metrics.py` 两处剔除条件对比；`grep -A1 chapters_done bench/results/v2/*/run.json` 确认 v2 无部分完成 run。**推理 + 产物核对，未复现失败场景** |
| **F6** | `bench/runner/batch.py:110-112`（finish_reason 只在空正文分支看）、`bench/runner/llm.py:110-114` | **截断但非空的章节被当成完整章。** 「空正文白拿第一」的修复只拦 `not text.strip()`。 | `ark-glm-flash__mid__k0/ch5.md` 正文 147 字、句子断在「差事有人先替她当」无标点，被计为 `chapters_done=6` 的完整运行并进榜、进汇总。完成度校验（BENCH_PROTOCOL §6 `chapters_done == expected`）对这类输出完全无效。 | **中** | `wc -m bench/results/v2/ark-glm-flash__mid__k0/ch5.md` = 147；`cat` 该文件见截断；`grep -h -A1 chapters_done bench/results/v2/*/run.json` 全为 6。**已重算（产物文件）** |
| **F7** | `.github/workflows/ci.yml` vs `tests/` | **「CanonBench 套件已全部纳入 CI」不成立。** | CI 跑 12 个 CanonBench 套件（CHANGELOG v0.4.1 写的是「10 个」，commit message 也写 10），`tests/test_mcp_sdk.py` 不在其中。更关键的是下面 F8：CI 全绿并不覆盖 F1 那条路径。 | **中** | `ls tests/`（17 个文件）对 `cat .github/workflows/ci.yml`。**已重算（文件清点）** |
| **F8** | `tests/`（grep 结果为空） | **没有任何测试调用过 `call_model`。** | `grep -rn "call_model" tests/` 无命中——测试只覆盖 `parse_response` / `_should_retry` / `resolve_proxy` 三个纯函数。v0.4.1 号称「三方审计（代码审查）…全 15 套件绿」，却把 F1 这个「成功路径返回 None」的改动放进了网络边界之外的盲区。**这一轮审计的代码审查结论在它自称修好的那一行上是错的。** | **中** | `grep -rn "call_model" tests/ bench/ scripts/ server/ framework/`（只在 `batch.py:31,243` 命中）。**已重算** |
| **F9** | `docs/BENCH_PROTOCOL.md:25`、`docs/canonbench-writeup.md:219` | **复现协议第一条命令就跑不过。** 两处都写 `git checkout feat/canonbench`，该分支不存在。 | 读者照协议执行 → `fatal: Needed a single revision`。实际分支只有 `main` 与 `feat/lcb-bench`（LCB→CanonBench 改名时漏改，且 `feat/lcb-bench` 本身也未被协议引用）。 | **中** | `git branch -a`；`git rev-parse --verify feat/canonbench` → fatal；`grep -rn "feat/canonbench" README.md docs/ CHANGELOG.md bench/`。**已复现** |
| **F10** | `bench/runner/batch.py:205` + `39` | **manifest 的 `generated_at` 记的是「最后一次写入时间」，不是生成时间；`judge_mechanical_version` 是硬编码常量。** | 9 个 `run.json` 的 `generated_at` 全部是 `2026-10-01T13:16:34.xxx`（相差 40 毫秒），而文档写「跑批日期 2026-09-26/27」——即这批 manifest 是 10-01 一次 `--rejudge` 扫出来的。任何按 manifest 追「这次运行」的读者会被 10-01 这个时间误导；而 `--rejudge` 每次都会覆写它，所以「逐字节复现 manifest」在定义上不可能。 | **中** | `grep -h "generated_at" bench/results/v2/*/run.json \| sort \| uniq -c`（9 条同秒）。**已重算（产物文件）** |
| **F11** | `docs/canonbench-results-v2.md:66` | **文档指的「原始报表」在 clone 里不存在。** 该行写「原始报表：`runs/v2/_flat/report.md`」。 | `.gitignore` 忽略 `runs/`，`git ls-files runs/` 为空 → 读者永远打不开这个路径。仓库里真正的报表是 `bench/results/v2/report.md`。 | **中低** | `cat .gitignore`；`git ls-files runs/`（空）；`ls runs/v2`（本地有、仓库无）。**已复现** |
| **F12** | `bench/report/metrics.py:322-365`、`bench/report/html.py:189,202-204` | **榜单只给密度，不给绝对数**，与 `BENCH_PROTOCOL §8-4`「核心指标 = 密度 + 绝对数成对」相抵触。 | 榜单是该项目最对外的一张表（README 首屏链接、GitHub Pages）。它按 `core_per_10k` 升序排名。项目自己用三个攻击证明了密度可被加字稀释，却不讨论反向：**输出短 → 密度虚高**。把 F4 的 v1 数据套进来，glm 的档位排序会翻转。 | **中** | 读 `leaderboard()` / `render_leaderboard()` / `render_page()` 的取数；对 `render_chart` 只画 `core_by_model_tier`（密度）。**已重算（代码 + 产物）** |
| **F13** | `bench/universe/generator.py:157` vs `:262-268` | **至少 3 条不变量对生成器输出恒真。** 最明显的一条：生成器用 `t["reveal_chapter"] = hero_learned.get(...)` 赋值（157 行），不变量又从**同一个 dict** 反推 `expect` 再比对（262-268）——同源比较，不可能失败。时间线单调（`days` 恒为 `[1..N]`）、每章恰一个 expanded（生成器写死 `s_i==0`）同理。 | 10 条不变量里只有 3 条有阴性对照（`tests/test_universe.py:131-169`：死后获知 / 提前获知终局 / 死后点名）。其余 7 条若恒真，套件照样全绿。 | **中低** | 逐条比对 `generate()` 的赋值与 `invariants()` 的重算来源；`tests/test_universe.py` 对照阴性对照覆盖范围。**已重算（代码）** |
| **F14** | `bench/calib/real_text.py:105` | **`cons-pov` 的诱饵不区分实现，特异性 100% 是空的。** 诱饵是 `她低声说：“我没听说有这回事。”`（行首是「她」）。m0.2.0 的「行首引号整行跳过」和 m0.3.0 的「剥引文后继续判」对这个句子输出**完全一致**（都不报）——它测不到它声称要守的那次修复。 | `docs/canonbench-calibration.md:20` 声称该诱饵测「引语内的第一人称」。真正的判别性孪生句应当把引号放行首（`“我没听说有这回事。”`），那才会在旧实现下被整行跳过、在新实现下被剥出。 | **中低** | `git show 02b908c -- bench/judges/mechanical.py` 拿到旧实现（`if not stripped or _DIALOG_RE.match(stripped): continue`），对两句分别推演。**推理，未复现** |
| **F15** | `docs/canonbench-calibration.md:32-33`、`:4` vs `bench/calib/real_text.py:215` | **公开样本数对不上命令。** 文档写公开样本 3,171 字 / 1 章，复现命令是「章节目录」。 | `load_corpus` 的跳过名单只含 `状态/manifest/_revisions/.ai.`，`第1章_v1旧管线.md`（3,443 字）会被一起收进来 → 目录口径实际是 6,614 字 / 2 段。 | **中低** | `LC_ALL=en_US.UTF-8 wc -m novels/静默轨道/chapters/*.md`（3172 / 3444）；对读 `real_text.py:213-219`。**已重算** |
| **F16** | `docs/canonbench-writeup.md:149` vs `:162` | 同一张表里的同一个数字自相矛盾：表内「术语轰炸」重复率 **0.00**，正文第 162 行写「重复率 **0.09**」。 | 两个数不可能都对；`html.py:244` 渲染的是现算值（与表内 0.00 一路）。 | **低** | `grep -n "0.09\|31.25\|重复率" docs/canonbench-writeup.md`。**已重算（文档内部对账）** |
| **F17** | `bench/contracts.py:165-205`（`RunManifest`/`BenchVersion`） | **manifest 不记录 Python 解释器版本**，而「同 seed 逐字节可复现」依赖它。 | 生成器用 `random.Random(seed)` + `rng.sample(...)`；`random.sample` 的抽样算法在 CPython 各版本间并非冻结契约。协议只钉了 generator 版本（`u0.1.0`），没钉解释器。跨版本 clone 复现 sha256 表存在系统性风险。 | **低** | 读 `contracts.py` 字段清单 + `generator.py:79-81`。**推理，未复现（跑不了两个解释器）** |

---

## 2. 最脆弱一环（只写一条）

**状态一致性这根轴，在它自己的官方产物上，没有任何一次被证明有效的报警。**

理由不是「某个判据有 bug」，而是三条一起看：

1. 官方 9 个 run 里，`state-dead` 一共开火 **4 次，4 次全是误报**（F2，逐句回正文核对过），真阳性 **0**。这 4 条被计进「状态」族，进了 `report.md`、进了 GitHub Pages 的柱状图、进了榜单。
2. 支撑「状态类判据零误报」这个对外结论的唯一证据（F3），其探针人名 `祁三` 在载体语料里根本不出现——**那项测量的期望值恒为 0，与判据好坏无关**。也就是说，这个基准最核心的主张，目前既没有被正面证据支持，也被自己的负面证据部分否定。
3. 而 `state_judge.py:20-23` 用「收窄 +4 误报 : 真阳性 0」为宽豁免辩护——这 4 条误报就是现在这批产物里的这 4 条（F2）。**取舍的依据与它声称要避免的现象同时存在于同一份入库数据里**，说明那次「实测」与入库产物不是同一次运行，或结论抄错了方向。

对一个自称「把长程一致性做成可自动判定的尺子」的项目，同行最容易在这里发难：*你唯一的自动状态判定器，在你公布的批次上精确率是 0/4，召回无已知真阳性；而你对它做的「真实文本零误报」校准是构造性恒真的。* 这不是可以靠 k≥3 或加语料补救的问题，是判据与它的证据链都要重做。

（F1 的致命程度更高——它让「clone 后可跑批」当场断掉；但那是**一行代码**的事，修完不影响已入库结论。F2/F3 修不动结论。）

---

## 3. 附录：我推翻了什么

### 3.1 我推翻了作者的结论

| 作者的说法 | 我的证据 |
|---|---|
| README `:58-59` / calibration `:37-41`：真实长篇上状态类判据**零误报** | 该测量的探针名 `祁三` 不在载体中出现，`clean_by_kind["state-dead"]` 恒为 0（F3）。而真实人名上的误报，官方产物里就有 4 条（F2）。 |
| `state_judge.py:20-23`：宽豁免「误报 4 : 真阳性 0，收窄不划算」 | 那 4 条误报（含 docstring 自己举的「老麦走过的路」「老麦走的那年」）**在当前宽豁免版本里就报出来了**，逐句在 `ch2/ch4/ch5.md` 里找到原文（F2）。前提不成立。 |
| CHANGELOG v0.4.1 / commit 02b908c：网关 200 不可解析 body 已并入重试 | 那段代码在 `break` 之后，不可达（F1）。 |
| CHANGELOG v0.4.1：三方审计（代码审查）→ 全 15 套件绿 | 绿是真的，但没有任何测试碰 `call_model`（F8）。这次代码审查漏掉了一条让真跑批必然崩的改动。 |
| CHANGELOG v0.4.1：CI 已纳入 CanonBench 全部套件 | 12 个在跑、`test_mcp_sdk.py` 不在内；CHANGELOG 自称 10 个（F7）。 |
| meta：门禁贡献漏统计「三家实测未受影响」 | **这条作者是对的**——我重算了三家 full 的 `post_fix`：db-lite 2 条、ds-flash 3 条、glm-flash 4 条，全非空，确实没被旧判空逻辑漏掉。 |
| writeup §10「判定器口径绑定中文/第三人称」 | 对，且比它说的更紧：活动动词表 34 词 + 6 字窗口 + `PAST_MARKERS` 含裸字「像」。构造反例（**推理，未复现**）：真复活被漏——「老麦慢慢地、一步一步地走过来。」（尾窗 6 字「慢慢地、一步」无动词）；「老麦走进货棚，把名册摔在桌上。」（整句含 `_MEMORIAL_RE` 命中词「名册」→ 整句豁免）。非复活被误报——「老麦蹲过的那个位置。」（尾窗含「蹲」、无豁免词，判据是**句级**豁免、不区分从句）。 |

### 3.2 我一开始怀疑、查完发现作者是对的那些

- **v0.4.1 的三条报表回归测试回滚后真会红**。逐个对过实现与断言：`test_gate_contribution_counts_fully_cleaned_runs`（旧写法 `not ...get("post_fix")` 会把全擦干净的 run 跳过 → `full_runs==2` 断言必红）；`test_missing_violations_json_is_not_zero_violations`（回滚后 `incomplete` 为假 → 三条断言全红）；`test_aggregate_excludes_partial_by_expected_chapters`（回滚后 `expected_chapters` 参数不存在 → TypeError）。**这一轮审计的报表修复是真的。**
- **续跑 skip 条件正确**：`batch.py:95` 要求 `not prev.get("errors") and prev.get("chapters_done") == len(wanted)`，两条都满足才跳过；`tests/test_runner_batch.py:280-299` 用 `error_at=2` 造残缺 run，断言 redo、补齐后才 skip。**对。**
- **重试环不吞不可重试错误**：`_should_retry` 对 400/402/404 直接 False，`llm.py:208-213` 立刻返回 error；`RETRY_BACKOFF=(30,90)` + `max_attempts=3` 封顶。**对**（当然，F1 让 200 路径整个断掉，那是另一回事）。
- **三档 prompt 真的是两套**：db-lite `mid` 与 `full` 的 ch1 `prompt_hash` **完全相同**（`9fa539a8…`），`bare` 不同（`f7f12bb3…`）。harness 声称的「full 复用 mid 的 prompt、差异只在后处理」得到产物级验证。**对。**
- **判定器确定性**：`bench/judges/` 只 import `re` 与 `bench.contracts`；死者集合 `sorted(dead)`、持有者 `sorted(holders.items())`、按章排序，无 time/random/os/网络。**推理，未复现（跑不了两次比对）。**
- **文档数字全对得上**：我从产物重算了 9 行的字数、总违反、核心违反、核心/万字、四族分解、修前→修后；模型汇总的 bare/mid/full 与 bare→mid 差值；门禁配对（db 2→0 / ds 5→1 / glm 2→0，合计 9→1）；榜单名次。**与 `docs/canonbench-results-v2.md` 和 `bench/results/v2/report.md` 逐格一致，无一出入。**
- **writeup §8 攻击表算术全对**：`BASELINE_TEXT` 手数 = 12 行 189 字 + 11 换行 = **200** ✓；注水 ×6 → 1200 ✓（密度 8.33、−83%）✓；复制 ×4 → 803 ✓（12.45、−75%）✓；术语轰炸 → 320 ✓（31.25、−38%）✓。**唯一问题是最右侧那列重复率的正文说法（F16）。**
- **README「共 449 KB」**：`wc -c bench/results/v2/*/*` 合计 **460,209 字节 = 449.4 KiB** ✓。
- **用量账本自洽**：db-lite full 的 `usage.jsonl` 按章去重后 tokens = 7880 in / 55856 out，与 manifest 完全一致 ✓（首行有一条 ch1 的 0-token 错误记录，被后写的同章记录覆盖，取值逻辑正确）。
- **`docs/canonbench-writeup.md:113-114` 主动标注「该批原始产物未入库，无法在本仓库复算」**——这条诚实标注是真的，值得肯定。（但 §9 的 v1 表没有加同样的标注，F4。）

### 3.3 对「诚实边界」清单的逐条判定（工单 E）

| 边界条目 | 判定 |
|---|---|
| k=1 只能说方向，精确归因需 k≥3 | **真边界**。且清单不全：k=1 之外还有 F4 那种「同一批数据两个指标符号相反」的问题，跟采样次数无关。 |
| 合成宇宙 ≠ 真实长篇（模板重复度高） | **真边界**，且是全文最诚实的一条。补一句：模板重复度高不只是「不像真实文本」，它还让 `state-dead` 判据几乎无事可做——产物里 老麦 在死后各章被提及上百次，判据只开火 4 次，全错。 |
| 密度指标永远可被加字稀释，绝对数只是缓解 | **半条真边界**。它只说了一个方向。反方向（**输出短 → 密度虚高**）没写，而 F4 显示这个方向已经在改结论了。 |
| 判据口径绑定中文/第三人称/引号约定 | **真边界**。 |
| 比喻词豁免的取舍（误报 4 : 真阳性 0 故保留宽豁免）——这个取舍成立吗？ | **不成立，已反驳**（F2）。k=1 之外的确还有别的解释：**这 4 条误报根本没被豁免掉**，它们是当前判据在宽豁免下产出的。所以「保留宽豁免」既没换来低误报，也没换来真阳性——这个取舍目前在任何数据上都没有正面依据。 |
| 注入法只覆盖可注入判据；账本级（时间线倒退、物品双持有）不在此列 | **真边界**，且这条比它说的更严重：账本级判据在官方 9 个 run 里**一次都没开火**（我数过，`state-timeline` 0 条、`state-item` 0 条），因为在合成宇宙里账本由 spec 声明、正文改不动它。也就是说「长程一致性」的四类里，有一类从未被这批产物检验过。对账本级 + F3 的状态级，合起来是：**这个基准号称测的四个轴，产物只对「约束遵守」轴给出了非平凡证据。** |
| **清单全不全？** | 不全。至少缺：① F2/F3 的状态轴有效性（作者把「12 条误报清零」当作已完成，但那只是误报的一个形状）；② F4 的指标符号分歧；③ F10 的 manifest 时间/判据版本溯源（`--rejudge` 覆写 `generated_at` 与硬编码 `judge_mechanical_version`）；④ F6 的截断章节算完整；⑤ F1 的「clone 后跑不了批」——BENCH_PROTOCOL §7 的「已知不可复现项」列了 4 条，没有一条指向「代码本身跑不起来」。 |

---

## 4. 我实际执行过的命令清单

```
ls -la && find . -path ./.git -prune -o -type f -print | head -200
wc -l bench/judges/*.py bench/universe/*.py bench/runner/*.py bench/report/*.py bench/attacks/*.py bench/calib/*.py bench/state/*.py bench/contracts.py tests/*.py docs/*.md README.md CHANGELOG.md
git log --oneline -- bench/runner/llm.py
git log -p --follow -- bench/runner/llm.py | grep -n "status == 200" -A3 -B3
git show ca5b1a7:bench/runner/llm.py | wc -l ; git show ca5b1a7:bench/runner/llm.py | sed -n '100,160p'
git show b53d1e2 -- bench/runner/llm.py | sed -n '1,120p'
git show 02b908c -- bench/runner/llm.py | sed -n '1,140p'
git show 02b908c -- bench/judges/mechanical.py bench/judges/text_utils.py | sed -n '1,150p'
grep -n "" bench/runner/llm.py | tail -32
sed -n 170,215p bench/runner/llm.py | cat -n
grep -rn "call_model" tests/ bench/ scripts/ server/ framework/
grep -rn "parse_response\|_should_retry\|resolve_proxy" tests/
grep -o -H '"probe_id": "[a-z-]*' bench/results/v2/*/violations.json | sed 's/.*"//' | sort | uniq -c
grep -c '"probe_id"' bench/results/v2/*/violations.json
grep -n '"post_fix"' bench/results/v2/*/violations.json
head -n 241 … | grep -c '"probe_id"'   # 及 mid/full 各 run 的 pre/post 切分（共 12 条同类命令）
grep -o '"probe_id": "[a-z-]*' bench/results/v2/<每个 run>/violations.json | sort | uniq -c
LC_ALL=en_US.UTF-8 wc -m bench/results/v2/*/ch[1-6].md          # 9 个 run 逐个求和
grep -n -A6 '"probe_id": "state-dead' bench/results/v2/ark-db-lite__mid__k0/violations.json bench/results/v2/ark-glm-flash__mid__k0/violations.json
grep -n "老麦走过的路" bench/results/v2/ark-db-lite__mid__k0/ch4.md
grep -n "老麦走的那年" bench/results/v2/ark-db-lite__mid__k0/ch5.md
grep -n "老麦如今问的那些" bench/results/v2/ark-glm-flash__mid__k0/ch3.md
grep -n "和老麦、柳娘走过的" bench/results/v2/ark-db-lite__mid__k0/ch5.md
grep -oh "老麦..." bench/results/v2/*/ch*.md | sort | uniq -c | sort -rn
grep -rh "老麦" bench/results/v2/*/ch*.md | grep "名册\|流水册\|日志\|照片\|碑" | grep -E "老麦(走|站|坐|蹲|拿|抓|推|递|伸手|转身|回头|开口|说话|喊|笑|问|盯着|看着)"
grep -rn "老麦站在这" bench/results/v2/*/ch*.md
head -n 63/150/107/93/48/… <各 violations.json> | grep -c '"probe_id"'   # 全部 9 个 run 的 pre/post 计数
cat bench/results/v2/ark-db-lite__full__k0/run.json
grep -h "generated_at" bench/results/v2/*/run.json | sort | uniq -c
grep -h -A1 "chapters_done\|\"errors\"" bench/results/v2/*/run.json | head -40
grep -h '"1"' bench/results/v2/ark-db-lite__{mid,full,bare}__k0/run.json
cat bench/results/v2/ark-db-lite__full__k0/usage.jsonl
grep -c "prompt_hash" bench/results/v2/*/usage.jsonl
wc -c bench/results/v2/*/* | tail -3
du -sk bench/results/v2
cat bench/results/v2/report.md
cat bench/results/v2/ark-glm-flash__mid__k0/ch5.md ; wc -m bench/results/v2/ark-glm-flash__mid__k0/ch5.md
tail -n 1 bench/results/v2/ark-glm-flash__{mid,full}__k0/ch{2,4,6}.md
grep -h '"probe_id": "cons-length' bench/results/v2/<三个 run>/violations.json | sort | uniq -c
grep -n "忽然\|突然" bench/attacks/attacks.py ; cat bench/attacks/attacks.py（Read 工具）
cat .github/workflows/ci.yml ; find .github -type f ; ls tests/
cat .gitignore ; ls -la runs/ ; ls runs/v2 ; git ls-files runs/ ; git ls-files docs/
git branch -a ; git remote -v ; git rev-parse --verify feat/canonbench
grep -rn "feat/canonbench\|feat/lcb-bench" README.md docs/ CHANGELOG.md bench/
grep -rn "lcb-results\|lcb-writeup\|lcb-" docs/*.md README.md
grep -n "0.09\|31.25\|重复率" docs/canonbench-writeup.md
grep -n "反直觉\|gm 的 mid\|反转" docs/canonbench-writeup.md README.md docs/canonbench-results-v2.md
LC_ALL=en_US.UTF-8 wc -m novels/静默轨道/chapters/*.md
git log --oneline -- "novels/静默轨道/chapters/第1章_v1旧管线.md"
（全部源码/测试/文档文件用 Read 工具逐份读完：bench/judges/*、bench/state/*、bench/universe/generator.py、bench/runner/*、bench/report/*、bench/attacks/attacks.py、bench/calib/real_text.py、bench/contracts.py、tests/test_{universe,state_judge,report_metrics,calib,runner_batch}.py 及相关处、docs/*.md、README.md、CHANGELOG.md）

被权限拦截、未能执行（因此凡涉及它们的结论均标「推理，未复现」）：
  python3 -c / python3 -m pytest / python3 tests/*.py / node -e / openssl dgst / shasum / awk / bc
```
