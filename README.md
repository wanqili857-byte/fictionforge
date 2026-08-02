# 🔨 FictionForge · 小说锻造

**把大纲锻造成让人睡不着的小说。**

![python](https://img.shields.io/badge/Python-3.9+-3776AB)
![license](https://img.shields.io/badge/License-MIT-green)
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

## 状态

- ✅ 引擎全链跑通(tick → spec → 正文)
- ✅ 框架/内容包解耦——换小说不动引擎
- 🚧 文档、适配示例、示例小说 Tier1 agent,持续完善中

---

## For English readers

FictionForge is a **multi-agent novel-writing framework**. Character agents each carry memory, beliefs, and a personal view of the world; a narrator synthesizes their event lines into a chapter spec; a generation pipeline writes the prose behind quality gates (banned words, AI-flavor detection, metaphor density, sensory warmth) and auto-rewrites until it passes.

Framework and novels are fully decoupled: `framework/` is generic, `novels/<yours>/` is a swappable content package, and `novel_config.json` is the only door between them. See `novels/静默轨道/` for a working sci-fi example.

**Quick start:** `pip install requests` → put an `OPENROUTER_API_KEY` or `DEEPSEEK_API_KEY` in `~/.env` → `python3 server/gen_proxy.py` → `python3 scripts/gen.py --force novels/静默轨道/specs/ch1.json` (`--force` because chapter 1 already ships with the repo — see the Chinese section above). Unit tests (no LLM, no I/O): `python3 tests/test_split_scenes.py` + `python3 tests/test_engine_core.py`.

**Bring your own novel:** copy `templates/novel/`, fill `novel_config.json` + `bible/`, write a protagonist agent, done.
