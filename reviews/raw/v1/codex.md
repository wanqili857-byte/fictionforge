I have completed the audit. One note before the conclusion: the working tree was modified concurrently during my review (someone patched `bench/runner/llm.py` and `tests/test_runner_batch.py` at 22:17). All findings below are pinned to committed **HEAD `f5d4461`**, with the drift called out.

---

# CanonBench 独立复核结论

**范围**：以 git HEAD `f5d4461` 为准；`bench/` 全部实现、`bench/results/v2` 官方产物、`docs/canonbench-*`、`tests/`、`README.md`、`CHANGELOG.md`。全部数字由我用本地脚本重算，命令见末行。只读，未改动任何文件。

**状态说明（重要）**：复核进行中，工作树被并发改动——`bench/runner/llm.py`、`tests/test_runner_batch.py` 于 22:17 出现未提交修改。我的 F1 结论针对**已提交的 HEAD**（bug 真实存在于 `git show HEAD:bench/runner/llm.py`），并在该项内说明工作树的未提交修复。

---

## 1. 严重度表

### F1 · `bench/runner/llm.py:183-184`（HEAD） — 真跑批成功路径返回 `None`，重试块是死代码
- **问题**：`if status == 200: break`（183-184 行）在 while 循环里直接跳出，紧随其后的 `if status == 200:`（185 行，内含 `parse_response` 与 `return out`）**永远不可达**。`call_model` 在 HTTP 200 时落出循环体、无返回值 → 隐式 `return None`。同时 `"ParseError"` 不在 `RETRYABLE_EXC`，即使那条支路可达也不会重试。
- **失败场景**：`batch.run_one` 里 `res = generate_fn(...)` 后紧接 `res.get("tokens_in", 0)` → `AttributeError: 'NoneType' has no attribute 'get'`。**任何人按复现协议 §4 clone 后重跑，第一条成功的模型调用就崩**——「谁都能在自己机器上复现榜单一行」这句的核心承诺在 HEAD 上不成立。（`--rejudge` 路径不调用它，故离线重判仍可用。）
- **怎么查出来的**：(1) `git show HEAD:bench/runner/llm.py` 确认 183/184/185 三行形状；(2) 用桩 `requests`（合法 200+正常 JSON）调 `call_model`，返回 `None`；(3) 证明它由 `02b908c`（“发布后审计修复”）引入——该 commit 删除循环后的解析块、新增行内 `if status==200:`，却漏删已有的 `break`；(4) `grep` 全校：`tests/` 只测 `parse_response` 与 `_should_retry`，**没有任何测试调用 `call_model`**，所以「全 15 套件绿」与这条路径无关。
- **严重度**：**fatal**（阻塞真跑批 / 复现协议）。
- **工作树漂移**：未提交改动已删除 `break`、把 `"ParseError"` 加进 `RETRYABLE_EXC`，并新增 `tests/test_runner_batch.py::test_call_model_transport_with_stub`；我直接对该函数跑了一遍，0 失败，修复是对的。但**已提交状态仍是坏的**。

### F2 · `bench/judges/state_judge.py:42,96,103,106`（+ `text_utils.py:23-31`） — 死人复活判据的漏报/误报面比 docstring 承认的宽得多
- **问题**：文档只承认「比喻/传闻词豁免」一类漏报。实测至少还有两类漏报和一类误报：
  - **漏报（活动动词表外）**：`_ACTIVITY_VERBS` 是封闭词表。`老周咳嗽了一声` / `老周叹了口气` / `老周把门关上` / `老周皱起眉头` 全部 **MISS**（这些动词不在表内）。
  - **漏报（6 字窗口外）**：`老周缓缓地从椅子上站起来，看着她。` → **MISS**（“站”落在名字后第 10 字，超出 `sent[idx+len:idx+len+6]` 窗口）。
  - **误报（相对小句/定语）**：`tail.startswith("的")` 只豁免光杆领格「老X的X」，豁免不了「老X + 动词 + 的 + 名词」。`那条船老周造的，跑得很快。` / `老周造的船，走夜路也不怕。` / `这是老周留下的船，走得快。` 全部 **FP**（动词属于被修饰的名词/小句，不是老周在活动）。
- **失败场景**：真复活写成「把门关上」会漏；正常叙述「老X造的船跑得快」会误报 → 状态轴的数字既低报又高报。
- **怎么查出来的**：构造 ledger（老周 ch2 死）+ 逐句喂 `state_judge`，打印命中/漏掉，见上方实例。
- **严重度**：**high**。

### F3 · `bench/results/v2/*/violations.json` vs `docs/canonbench-writeup.md:192-193` — 出厂产物里 4 条 state-dead **全是**误报，而文档称「清零」
- **问题**：入库的 m0.3.0 判决里共有 4 条 `state-dead`，按作者自己的判定逻辑（遗物/回忆/定语豁免之外才报）这 4 条**全是误报**：
  - `ark-db-lite__mid` ch4「老麦走过的路，柳娘走过的路…」（定语从句）
  - `ark-db-lite__mid` ch5「老麦走的那年，盐场的烟囱…」（定语/时间小句）
  - `ark-db-lite__mid` ch5「…和老麦、柳娘走过的，是同一条。」
  - `ark-glm-flash__mid` ch3「老麦如今问的那些，原来也不是老麦先问的。」
  我先用 `generate(seed=42,chapters=6)` 复核了宇宙：老麦确实 ch2 死亡，故以上均在死后章节被当「复活」。
- **失败场景**：`docs/canonbench-writeup.md:193` 写「判据改正向证据制后**清零**」，`docs/canonbench-results-v2.md:49-51` 也把 12 条 FP 说成已修。但**结果表的「状态」列（db-lite mid=3、glm mid=1）整列都是这 4 条 FP**——读者会以为三家模型有状态一致性问题，其实全是指称误报。另外所谓「12 条 FP 原文进测试夹具」（`tests/test_state_judge.py` 的 12 条 `fp_spans`）用的是 `老周`、且这 4 条残留在夹具里**没有**，并非「原文钉死」。
- **怎么查出来的**：遍历 `bench/results/v2/*/violations.json` 统计 probe_id → `state-dead` 共 4 条，逐条打印 span 判读；再用生成器账本确认老麦的死亡章。
- **严重度**：**high**（对外数字与文档自相矛盾）。

### F4 · `bench/calib/real_text.py:58-63` + `docs/canonbench-calibration.md:37-39`、`README.md:58-59` — 「真实散文上状态类判据零误报」是**空测**，且 92% 语料不可复核
- **问题**：校准账本只把**合成分句里的探针名 `祁三`** 声明为死者（`calibration_ledger`，cast=`[祁三, 苏茜]`）。真实语料里根本没有「祁三」，于是 state-dead 判据**没有任何可命中的名字**——「零候选」与判据质量无关。我实测：把真实语料里确实出场的角色（陆离/零号）声明为死者，同一段公开文本立刻产出 `陆离退后。`、`陆离盯着时间戳重叠图。` 这类命中（即：活人做寻常动作就会被当复活）。
- **失败场景**：README 头条「在真实长篇上校准过…状态类判据零误报」被当作该基准最硬的方法学卖点，但它的头条那半（死人复活）在真实文本上**什么都没测**；38,307 字里 35,136（92%）是私有稿，不入库、不可复核。这条结论的可验证性接近 0。
- **怎么查出来的**：读 `calibration_ledger`；对 `novels/静默轨道/chapters/第1章.md` 分别用「祁三死」与「陆离死」两个账本跑 `state_judge` 对比命中数。
- **严重度**：**high**（方法学最脆弱处，见 §2）。

### F5 · `bench/calib/real_text.py:76-116` + `docs/canonbench-calibration.md:14` — 「孪生句只差一个词」的论证前提**不成立**
- **问题**：文档反复说正例与诱饵是「只差一个词的孪生句」。用 `difflib` 量：`state-day` 相似度 0.93（确实差 1 字），但 `state-dead` 0.28、`cons-pov` 0.12、`cons-para` 0.13——**4 对里 3 对几乎完全不同**（长度、句式、标点都不同）。
- **失败场景**：方法文声称「抓到/没抓到只可能来自判据本身」。真正的对照只控制了「载体与插入位置」，没有控制正例自身的表面特征；因此「100% 召回」无法区分「判据抓的是语义」与「判据抓的是正例独有的表面模式」——而我们已构造出同族的 FP（F2），证明后一种解释真实存在。
- **怎么查出来的**：对 `real_text.PROBES` 逐对算 `SequenceMatcher.ratio()` 与差字符数。
- **严重度**：**medium**（结论方向未必错，但论证被夸大；配合 F4 更弱）。

### F6 · `bench/report/metrics.py:322-377`（`leaderboard`/`render_leaderboard`）与 `bench/report/html.py` 榜单/柱图 — 对外头牌只给密度，不给绝对数
- **问题**：榜单排序键是 `core_rate_by_tier`（纯密度），HTML 柱图和标题卡也是密度；**绝对违反数只出现在明细表**。而基准自己在 writeup §8 实测「注水 ×6 把密度刷低 83%」——也就是说**榜单名次可直接靠注水刷高**。
- **失败场景**：一个模型多写一倍干净填充，`bench/report` 输出的榜单就会往前排；「密度与绝对数成对同报」这条防线在头牌页面上并未生效。
- **怎么查出来的**：读 `metrics.py` 的 `core_rate_by_tier` 定义与 `render_leaderboard` 列；跑 `python3 -m bench.report.html bench/results/v2` 看榜单/柱图只有密度。
- **严重度**：**medium**（与自述抗刷分设计矛盾）。

### F7 · `bench/universe/generator.py:293-295` + `tests/test_universe.py` 「存在 held-out 事实」 — 该不变量**恒真**
- **问题**：`fact_ids - learned` 永远非空，因为 `fact_ids` 含**常识事实**（T-01/02）与 **false 行**（F-01..03），而这些永不进 `knowledge`。所以「无 held-out 事实（探针池为空）」这条**无论宇宙怎样都不会触发**。实测：把 seed=42 唯一的 held-out 发现型事实 T-06 整个删掉，`invariants()` 仍返回 `[]`。`test_universe.py` 的 `len(fact_ids - learned) >= 1`（「存在 held-out 事实」）同病——它数的是常识/false 行，不是发现型事实。
- **失败场景**：生成器的 held-out 机制若哪天坏了（探针池真的空了），自检与单测**都不会红**——docstring/README 把「至少一条 held-out」当已自检保证，实为空保证。
- **怎么查出来的**：`invariants()` 逐条人肉注入违规（10 类里 9 类我复现出 CANAUGHT），唯独 held-out 这条删掉唯一 held-out 发现型事实后仍为 `[]`。
- **严重度**：**medium**（恒真断言/恒真不变量）。

### F8 · `bench/calib/real_text.py:166,198,202`（`summarize`） — 同一张校准报告里两种计数口径混用
- **问题**：`clean_candidates = sum(len(clean))` 用**违反行数**，而 `clean_by_kind` 用 `_signal`（**出现次数**）。含「忽然×2」的载体实测：`clean_candidates=2`，但 `sum(clean_by_kind)=3`——「固有命中」表和「候选 N 条」行不可对账。这正是作者修过的第二坑（条数 vs 出现次数）在**报表层**的残留。
- **失败场景**：读者拿「固有命中 段落39/视角11/禁词3」去核对「候选 53 条」会得到错误结论（禁词那列是出现次数、候选总数是行数）。
- **怎么查出来的**：构造含禁词两次的载体，跑 `calibrate_texts`+`summarize` 对比两处计数。
- **严重度**：**medium**（第三个同类测量坑）。

### F9 · `bench/runner/batch.py:88-95` — 续跑 skip 不看判决文件是否在，「真完成」仅凭 run.json 自报
- **问题**：skip 条件只有 `not prev.get("errors") and prev.get("chapters_done") == len(wanted)`；**不检查 `violations.json`、也不检查 `ch*.md` 是否齐全**。一个 run.json 完整但判决文件缺失（或正文被删）的 run，续跑会静默跳过、永不修复——`metrics.py:load_runs` 的注释自己承认「续跑因 run.json 完整而永不修复」。另外 `wanted = chapters or [...]`：换 `--chapters` 续跑会改变比较基准。
- **失败场景**：复现协议 §4「断点续跑」在判决缺失场景下不会自愈；残缺 run 会长期以「未完成」混在清单里。
- **怎么查出来的**：静态审读 `run_one` skip 分支 + `load_runs` 注释（沙箱只读，无法建目录实跑，标「推理，未复现」）。
- **严重度**：**medium**（「续跑只跳过真完成的 run」只部分成立）。

### F10 · `docs/canonbench-writeup.md:65,68` — full 档被描述成「自动返修 + 状态回写 / 返修篇幅」，实现里没有
- **问题**：`bench/runner/harness.py::apply_gate_fix` 只做两件事：删禁词（直接 `replace(w, "")`）、切超长段。没有同义替换、没有状态回写、**补不了篇幅**（文档别处也承认「门禁只能做减法，补不了篇幅」，与 §5 自相矛盾）。
- **失败场景**：读者据 §5 以为 full 档含篇幅修复与状态回写，从而误读「门禁贡献」能覆盖哪些违反。
- **怎么查出来的**：对照 `apply_gate_fix` 源码与 `docs/canonbench-writeup.md` §5 文字。
- **严重度**：**low**（文档-实现描述不符）。

### F11 · `docs/canonbench-results-v2.md:66` — 复现指针指向被 gitignore 的路径
- **问题**：文档写「原始报表：`runs/v2/_flat/report.md`」，但 `.gitignore` 含 `runs/`（`git ls-files runs` = 0）。clone 后该文件不存在；committed 副本是 `bench/results/v2/`（101 个 tracked 文件）。
- **严重度**：**low**（复现路径指引错误）。

---

## 2. 最脆弱一环（只写一条）

**真实文本校准里「状态类判据零误报」这条结论是空的，而它是基准对外最硬的方法学卖点。** 校准账本只声明了一个真实语料里根本不存在的合成分句名当死者（`祁三`），所以「死人复活」判据在 38k 真实散文上**没有任何可命中的对象**——「零候选」是构造决定的，不是判据准。我把真实语料里出场的角色声明为死者，同一段公开章节立刻产出 `陆离退后。` 这类命中。再叠加 92% 语料是私有稿、不可复核，同行只要照 `README.md:58-59` 的声明去问「你们到底拿哪个死人测的」，这个基准在「已在真实长篇上校准」这一点上就站不住。

（相比之下 F1 的 llm.py bug 更“致命但好修”，且作者已在工作树修掉；F4 是方法学层面的信用问题，更难自证。故最脆弱选 F4。）

---

## 3. 附录：我推翻/确认了什么

**推翻了作者的结论：**
1. 「状态类判据零误报」（README/writeup/calibration）→ 空测（F4）。
2. 「12 条 state-dead FP … **清零**」（writeup:193）→ 出厂 m0.3.0 产物仍有 4 条同类 FP，且结果表「状态」列整列是 FP（F3）。
3. 「clone 后重跑能复现」→ HEAD 上 `call_model` 成功即返回 `None`，真跑批路径不可用（F1）；「网关 200+坏 body 并入退避重试」在 HEAD 双重不成立（死代码 + `ParseError` 不可重试）。
4. 「孪生句只差一个词」→ 4 对里 3 对相似度 ≤0.28（F5）。
5. 「至少一条 held-out 事实」不变量/断言 → 恒真，删掉唯一 held-out 也是绿（F7）。
6. 「比喻词豁免是唯一已知漏报形态」→ 另有词表外动词、6 字窗外两类漏报 + 定语小句误报（F2）；作者对该取舍的「误报 4 : 真阳性 0」证据也站不住（见下）。

**E · 逐条判「诚实边界」：**
- k=1 只能说方向 → **真边界**（有 CI/文档一致表述）。
- 合成宇宙 ≠ 真实长篇 → **真边界**。
- 密度可被加字稀释 → **真边界**，但**榜单按密度排序**与之自相矛盾（F6）——是「说了却没做」。
- 口径绑定中文/第三人称/引号 → **真边界**（还漏了两条：`cons-pronoun*` 人称判据与 `cons-length` 篇幅判据在 5/5 校准里根本没被注入）。
- 「比喻词豁免：误报 4 : 真阳性 0，故保留」→ **站不住的反驳点**。合成宇宙**结构上不产生真复活**（生成器明确让死者不在死后章节当行动者），所以「真阳性 0」在该语料上几乎是必然，不是收窄决策的结果；而放宽豁免后出厂产物里仍留着 4 条定语小句 FP（F3）。这个 4:0 不能支撑取舍。
- 「注入法只覆盖可注入判据；账本级不在此列」→ 真边界，但**不完整**（漏报人称/篇幅两类可注入判据未覆盖）。

**E · 复审「那一轮三方审计的结论本身」：** 它声称修掉的三个**报表**缺陷，回归测试**真的会在回滚时变红**——我把 `gate_contribution` 回滚成 `not post_fix` 判定，`test_gate_contribution_counts_fully_cleaned_runs` 报 2 条 FAIL；把 `aggregate_by_model` 回滚成忽略 `expected_chapters`，`test_aggregate_excludes_partial_by_expected_chapters` 报 1 条 FAIL；`violations_missing` 回滚会让 `runs[..]["violations_missing"]` 直接 KeyError。**这三条审计结论成立。** 但同一轮审计那句「网关 200+坏 body 并入退避重试」**没有对应测试、且引入了一个更严重的回归**（F1）——所以「全 15 套件绿」这一轮审计结论并不整体可信。

**我一开始怀疑、查完发现作者是对的：**
- 同 seed 逐字节可复现：`BENCH_PROTOCOL` 三个 sha256 我全复算命中（`34cc153008c9ca84` / `a3e3b45192205c68` / `bdbf0087774c40d7`）。
- `--rejudge` 判决可重算：用当前 `make_judge` 对 9 个 run 的 `ch*.md`/`ch*.fixed.md` 重判，**逐条**与入库 `violations.json`（pre_fix 与 post_fix）完全一致。
- 判定器纯净：`bench/judges/*` 只 import `re/dataclasses/typing` + 内部模块，无 time/random/os/网络/env；同输入两次 `to_dict` 逐字节相同。
- `gate_contribution` 的 `post_fix == []` 修复有效（配对统计正确计入，removed=2）。
- 抗刷分基线**三处同源**：`attacks.BASELINE_TEXT`（200 字）现算=`writeup §8` 表（1200/1/8.33/−83%/0.76 等），`html.py` 现算同值；`tests/test_attacks.BASE` 与之逐字节相同。
- 非重试错误不吞：`_should_retry(400/402/404/422, …, 1)` 全 False，429/500 True。
- 报表页与文档逐格一致；README 的「30 秒跑一遍」两条命令离线可跑通。

---

## 4. 我实际执行过的命令清单

```
cat reviews/brief.md
find bench -type f ；ls bench/* docs tests
cat bench/judges/{text_utils,mechanical,state_judge}.py
cat bench/universe/generator.py ；cat bench/state/ledger.py
cat bench/report/{metrics,html}.py ；cat bench/attacks/attacks.py
cat bench/calib/real_text.py ；cat bench/runner/{batch,harness,llm,models}.py
cat docs/{BENCH_PROTOCOL,canonbench-results-v2,canonbench-calibration,canonbench-writeup}.md ；sed -n '1,80p' README.md CHANGELOG.md
git log --oneline -- bench/runner/llm.py ；git show b53d1e2 -- bench/runner/llm.py ；git show 02b908c -- bench/runner/llm.py
git show HEAD:bench/runner/llm.py | awk (查看 173-205 行)；git status --porcelain
# 复现 F1（桩 requests，无网络）
python3 -c "stub requests.Session → call_model → repr(...)"        # → None
python3 -c "_should_retry(400/402/404/422/429/500,…)"              # → [F,F,F,F,T,T]
# 复现 A2：宇宙 sha256
python3 -c "generate(42,6) → _dump(...) → sha256"                  # 三个哈希命中协议表
# 复现 A4：死人复活边界
python3 -c "ledger(老周死) → state_judge(咳嗽/叹气/把门关上/缓缓站起/造的船…)"   # 多例 MISS/FP
# 复现 A3：不变量逐条注入违规 + held-out 恒真探针
python3 -c "invariants(u) 逐条 mutation；删 T-06 → invariants()==[]"
# 复现 B5/B6
python3 -c "metrics.gate_contribution(post_fix==[]) ；build_table/aggregate/render_markdown( violations_missing / partial )"
# 复现 B7/B8
python3 -m bench.report.metrics bench/results/v2
python3 -m bench.report.html bench/results/v2        # 抽取榜单/明细/门禁/抗刷分表
python3 -c "attacks.run_attack ×3 on BASELINE_TEXT"  # 与 writeup §8 逐格一致
# 复现 C9：离线重判 vs 入库判决
python3 -c "make_judge(seed42) 重判 9 个 run 的 ch*.md/.fixed.md → 逐条比对 violations.json"
# 复现 D：校准
python3 -m bench.calib.real_text novels/静默轨道/chapters/第1章.md --label 静默轨道
python3 -m bench.calib.real_text novels/静默轨道/chapters --label 静默轨道
python3 -c "calibration_ledger 死者集；声明陆离/零号为死者 → state_judge 命中数"
python3 -c "PROBES 正例/诱饵 difflib.ratio()"
python3 -c "summarize 单位混用探针（忽然×2）"
# 复现 E：三个报表修复的回滚红测（monkeypatch 后跑测试体）
python3 -c "buggy gate/aggregate → tests.test_report_metrics.* FAIL 计数"
# 可离线跑、无需写盘的套件
python3 tests/test_state_judge.py ；tests/test_mechanical.py ；tests/test_contracts.py ；tests/test_runner_harness.py ；tests/test_attacks.py   # 32/32,45/45,29/29,42/42,27/27
python3 -c "导入 tests.test_runner_batch；直接调用其新测试 test_call_model_transport_with_stub（工作树版）"   # 0 失败
grep -rn "call_model|_should_retry|parse_response" tests/ ；grep -n "PAST_MARKERS|_ACTIVITY_VERBS|held-out" bench/...
git ls-files runs ；git ls-files bench/results/v2 ；cat .gitignore
```
（受限说明：沙箱为只读，`tests/` 里依赖 `tempfile.mkdtemp` 的套件无法实跑，相关条目已在正文标「推理，未复现」。）