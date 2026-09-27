# 🔨 FictionForge · 小说锻造

**把大纲锻造成让人睡不着的小说。**

![python](https://img.shields.io/badge/Python-3.9+-3776AB)
![license](https://img.shields.io/badge/License-MIT-green)
![ci](https://github.com/wanqili857-byte/fictionforge/actions/workflows/ci.yml/badge.svg)
![stars](https://img.shields.io/github/stars/wanqili857-byte/fictionforge)

多智能体小说写作框架——**每个角色都有自己的脑子,每一章都有质量门禁。**

---

## 这不是又一个"ChatGPT 帮我写小说"

普通 AI 写小说是这样:你输入"写个章节",它凭感觉赌一个答案给你。赌对了你开心,赌错了人物越写越歪、剧情越写越散,三十章之后连主角都忘了自己叫什么。

**FictionForge 不一样。它不是让 AI 猜一章,而是让 AI 给你"演"一章。**

- 🧠 **每个角色都是一个 agent** —— 主角配角各有各的记忆、信念、视角。配角在你看不见的地方也在过日子。
- 🌍 **世界是活的** —— 环境、情绪、基础设施都在实时变化,而且**每个角色看到的世界还不一样**(有人的地方才有戏)。
- 🎬 **导演级叙事合成** —— 多视角事件线被合成为结构化的章节 spec,标注好反转落点。不写流水账,写的是有钩子的章节。
- 🚧 **质量门禁,写不完不算完** —— 禁词、AI 腔、比喻密度、感官温度,逐项体检。不合格?自动返工,重写到你过关。
- 📦 **框架和小说彻底解耦** —— 换一本小说 = 换一个内容包。引擎代码一行不用动。

> 一句话:**别人给你一个"写作工具",FictionForge 给你一支"演员剧组"。**

### 台后还有一套铁打的营盘

上面是台前的戏。让**三十万字能写完**的,其实是台后这三件硬功夫:

- 🗂️ **上下文统一** —— 每章都注入同一份权威上下文(人设/世界观/写作法则),设定前后不打架,不靠模型"记性"。
- 👥 **角色统一** —— 每个角色一个 agent,人格、记忆、信念持久化落盘。上一章说的话,下一章还记得;配角不会换了章节就换个脑子。
- 🧠 **不丢人格** —— agent 状态跨章持久化(memories / beliefs / knowledge),断了上下文接着写,角色还是那个人,不是临时捏出来的纸片。

> 台前是演员剧组,台后是铁打的营盘。**剧组演得好是本事,营盘稳,才是长篇能写完的底气。**

---

## 真家伙展示 · 这是引擎生成的,不是人写的

下面这段来自示例小说《静默轨道》第一章(科幻)。全文生成,只过了质量门禁,没经过人手修改:

> 对接完成的震动沿着舱壁传来,一声闷响,精确吻合128小时轨道的接口相位。
>
> 陆离飘在对接通道中央。左手手套摘到一半,裸露的指节敲了敲舷窗边缘。三厘米厚的多层硅酸盐玻璃对面,静默号的外壁占满视野。微陨石撞击坑小而密集地嵌在防辐射涂层上,分布规律与二十年前设计矩阵完全一致。星空贴在外面,没有变化。
>
> "对接舱气压101.3千帕,温度22摄氏度,已预设至标准值。"
>
> 每个音节的长度都被计算过。例行公事,准确到刻薄。

想多看看?`novels/静默轨道/chapters/第1章.md` 还有一整章。这一章只是"适配实证"——证明同一套引擎,换个内容包就能写别的故事。

---

## 它是怎么"演"的

```
        ┌─────────────────────────────┐
        │  novels/<你的小说>/           │  ← 内容包:人设/世界观/写作法则/角色agent
        │  novel_config.json           │     框架和小说之间唯一的一扇门
        └──────────────┬──────────────┘
                       │ 读配置
        ┌──────────────▼──────────────┐
        │  framework/                  │  ← 引擎(通用,不认任何一本小说)
        │  多 agent 并行感知+决策       │
        │  叙事合成 → 章节 spec         │
        │  生成管线 → 质量门禁          │
        └─────────────────────────────┘
```

一个"弧"(好几章)跑一遍:**角色 agent 并行感知世界 → 各自独立做决定 → Narrator 把多视角事件线合成章节 spec → 生成管线产出正文 → 质量门禁体检。**

全程你坐导演椅。每章先审 spec,再改定稿。引擎干活,你拍板。

---

## 你改的每个字,框架都记得(v0.3.0 修订感知管线)

AI 写的稿子你总要改。问题是:**改完之后,那些修改去哪了?**

多数工具里,它们蒸发了——下次生成,模型照样犯同样的毛病。

FictionForge 把「人工修订」做成了一等公民:

- 📝 **canon / drafts 分家** —— AI 只写草稿(`chapters/drafts/`),你改完的定稿才是 canon。生成管线**永不覆盖**你改过的字。
- 🔁 **修订即资产** —— `promote.py` 自动 diff「你的定稿 vs AI 原稿」,逐条标注修订原因(AI味/节奏/素材/逻辑),存进修订记录。
- 📈 **越用越懂你** —— 修订记录蒸馏成素材库(只存人改后的好句,配额新进旧出),注入后续生成。库越准,prompt 不膨胀。
- ✂️ **局部重生成** —— `--resection <节id>`:只重写你不满意的那一节,其余节字节级保留。
- 🧭 **情节差异回流** —— 你推翻的情节写进「漂移注记」,下一章生成以此为准,不再继承已被你否掉的旧设定。

> 你改的每个字,都是下一章的教材。

---

## 快速上手 · 三条命令开写

```bash
# 1. 装依赖(就一个)
pip install requests

# 2. 起 LLM 代理
python3 server/gen_proxy.py

# 3. 生成示例小说第一章,看引擎出手
python3 scripts/gen.py --force novels/静默轨道/specs/ch1.json
```

> 需要 LLM key:`OPENROUTER_API_KEY` 或 `DEEPSEEK_API_KEY`,放进 `~/.env`(不在项目里,不会被提交)。详见 `server/README.md`。
>
> 为什么加 `--force`:《静默轨道》第1章已经随仓库提交,直接跑会被章节状态校验拦下(报 expected ch2)。`--force` 是演示重跑;你新接小说时从新 spec 起章,不需要它。

---

## 接你自己的小说

复制 `templates/novel/` → 填 `novel_config.json`(cast 是谁、bible 注入哪些、质量参数) → 写 `bible/` 里你的写作法则/人设/世界观 → 给主角写个 agent(配角免写)→ 建 vault + arcs。分步指南在 `templates/novel/README.md`。

**每一章的人类 + AI 工作流:**

```
1. Design spec   → 你定章节走向(反转类型/位置,三节功能)
2. User reviews  → AI 审逻辑和物理合理性
3. Generate      → 引擎生成正文
4. User reviews  → 复核 + 你手动改定稿
5. Confirm       → 定稿
6. Update state  → 更新章节状态,下一章接着演
```

每章反转二选一:**A** = 读者确认了之前只敢猜的事;**B** = 之前以为的事实是错的。连续两章纯推进?不允许,读者会走。

---

## 顺带造的一把尺子 · LCB 长程一致性基准

写长篇最难的不是文笔,是**一致性**:第 2 章死的人第 4 章又活了、第 5 章才该知道的事第 3 章就说了出来。这是 agent 的**长程状态追踪**问题——和记忆、上下文腐坏同一类。

于是把框架里的一致性判定器抽出来,做成可自动判定的基准:

**已实现**

- 🔍 **判定器** —— 约束遵守(禁词/篇幅/POV/人称)、状态一致(死人复活/物品瞬移/时间倒流/物品双持有),纯函数、零 LLM、可精确单测
- 🧪 **合成宇宙** —— `seed → 一部可跑的小说包`,零 LLM、**逐字节可复现**,ground truth 自带不变量自检(含阴性对照)
- 🏃 **跑批与指标** —— 模型 × 三档 harness(裸/注入/满) × k 次;逐章用量账本、可续跑;密度与绝对违反数**成对**、自助法 CI、pass^k、三档归因
- 🛡️ **抗刷分自测** —— 注水/复制/术语轰炸三攻击 + 重复率判据(实测三个攻击都能刷低密度指标,防线是绝对数成对 + 重复率 + 篇幅门禁)
- 🔌 **MCP server** —— 五个工具可直接被 agent 调用(手写协议层 + 官方 SDK 双实现)

- 🏆 **榜单与复现协议** —— 静态榜单(bare 档排序/并列同名次) + `docs/BENCH_PROTOCOL.md`:宇宙 sha256 校准、manifest 校验清单、榜单准入规则

**首期跑批结果**(三厂商 × 三档 × 6 章,判定器 m0.2.0,见 `docs/lcb-results-v2.md`):裸写 glm-5.3-flash 最优(2.52 核心违反/万字),deepseek 最吃上下文工程红利,doubao 被 harness 拉到修后 0;**glm 家族对上下文注入负贡献,跨两期跑批复现**。

**设计中(见 `docs/BENCH_PLAN.md`)**

- 🧠 **语义判定** —— 知识边界(谁在第几章知道什么)与上下文腐坏:清单式 LLM 判定 + Kappa 准入门槛

细节见 `bench/mcpserver/README.md`;设计与发现见 `docs/lcb-writeup.md`。

> 有意思的是:判定器抓出的第一批违规,来自我自己的数据生成器——ground truth 被自己的工具查出矛盾。详见 writeup §7。

---

## 状态

- ✅ 引擎全链跑通(tick → spec → 正文)
- ✅ 框架/内容包解耦——换小说不动引擎
- ✅ **v0.3.0 修订感知管线**——canon/drafts 分家、修订回灌(素材库)、局部重生成、情节差异回流
- ✅ **LCB 长程一致性基准**(v0.4.0)——判定器/合成宇宙/跑批/指标/抗刷分/榜单/复现协议全链完成;两期受控跑批出数;语义判定(Kappa)进行中
- 🚧 文档、适配示例、示例小说 Tier1 agent,持续完善中

---

## 版本历史

| 版本 | 内容 |
|---|---|
| **v0.4.0** | **LCB 长程一致性基准**:判定器(m0.2.0)/合成宇宙(逐字节可复现)/三档 harness 跑批/指标(密度+绝对数成对、门禁配对)/抗刷分自测/榜单/复现协议/MCP server;两期受控跑批出数([结果](docs/lcb-results-v2.md)) |
| v0.3.0 | **修订感知管线**:canon/drafts 分家、修订 diff 打标→素材库回灌、`--resection` 局部重生成、情节漂移回流 |
| v0.2.0 | 理论心智层:真相表 + 知识 vs 真相 + A/B 反转素材、顶层协调器(gen/engine/hybrid) |
| v0.1.0 | 引擎全链:TickRunner→角色 agents→Narrator 合成,框架/内容包解耦 |

详细更新内容见 [CHANGELOG.md](CHANGELOG.md) 与 [GitHub Releases](https://github.com/wanqili857-byte/fictionforge/releases);规划见 `docs/VERSION_PLAN.md`。

---

## For English readers

FictionForge is a **multi-agent novel-writing framework**. Character agents each carry memory, beliefs, and a personal view of the world; a narrator synthesizes their event lines into a chapter spec; a generation pipeline writes the prose behind quality gates (banned words, AI-flavor detection, metaphor density, sensory warmth) and auto-rewrites until it passes.

Framework and novels are fully decoupled: `framework/` is generic, `novels/<yours>/` is a swappable content package, and `novel_config.json` is the only door between them. See `novels/静默轨道/` for a working sci-fi example.

Under the hood it's built for the long haul: **unified context** (every chapter gets the same authoritative settings, no drift), **consistent characters** (each character is an agent with persisted memory/beliefs), and **persona persistence** (agent state survives across chapters — write 300k words and the protagonist is still the same person).

**v0.3.0 — revision-aware pipeline.** AI writes drafts into `chapters/drafts/`; your edited version is promoted to canon and the generation pipeline never overwrites it. `promote.py` diffs your edits against the AI original and labels each change (AI-flavor / rhythm / material / logic); those labels distill into an example library that feeds later chapters. Section-level regeneration (`--resection`) rewrites one section and leaves the rest byte-identical.

**Quick start:** `pip install requests` → put an `OPENROUTER_API_KEY` or `DEEPSEEK_API_KEY` in `~/.env` → `python3 server/gen_proxy.py` → `python3 scripts/gen.py --force novels/静默轨道/specs/ch1.json` (`--force` because chapter 1 already ships with the repo — see the Chinese section above). Unit tests (no LLM, no I/O): `python3 tests/test_split_scenes.py` + `python3 tests/test_engine_core.py`.

**Bring your own novel:** copy `templates/novel/`, fill `novel_config.json` + `bible/`, write a protagonist agent, done.

**LCB (v0.4.0) — a benchmark for long-horizon consistency.** The same consistency checkers the framework uses for quality gates, extracted into a benchmark: constraint adherence (banned words, length, POV, pronouns) and state consistency (dead characters walking, teleporting items, time going backwards) — pure functions, zero LLM, exactly unit-testable. Inputs come from a **fully deterministic synthetic-universe generator** (`seed → a runnable novel package`, byte-identical across runs, with self-checked invariants and negative-control tests), so ground truth never wobbles. Batch runs with a **three-tier harness ablation** (bare / context-injected / full-gate), paired gate attribution, anti-gaming self-tests, a static leaderboard, and a reproduction protocol all shipped in v0.4.0 — first controlled runs across three vendors are in `docs/lcb-results-v2.md`. Still being built: semantic judging of knowledge boundaries (checklist-style LLM judging behind a Kappa gate). Also ships as an **MCP server** — five tools an agent can call directly. See `bench/mcpserver/README.md`, `docs/lcb-writeup.md` and `docs/BENCH_PROTOCOL.md`.
