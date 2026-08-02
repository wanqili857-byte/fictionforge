# AI 小说叙事框架 / AI Novel Writing Framework

一套 **AI 智能体小说写作框架**：多 agent 引擎模拟角色行为线，把事件合成章节 spec，再经生成管线产出带质量门禁的正文。

An **AI-agent framework for writing serialized fiction**: a multi-agent engine simulates character behavior, synthesizes events into a chapter spec, then a generation pipeline produces prose with automated quality gates.

**框架在根，小说是内容包。** 框架代码（`framework/`）与具体小说解耦——提示词、人设、世界观、人物 agent 都是可替换的"内容包"。换一本小说，只改内容，不改框架。

**Framework lives at the root; novels are content packages.** The engine in `framework/` is fully decoupled from any specific novel — prompts, character settings, worldbuilding, and character agents are all swappable "content packages". Change novels by changing content, not code.

`novels/静默轨道/`（科幻）是参考示例——用来实证"换小说不改框架"：同一个框架，换一套内容包即换一本小说。

`novels/静默轨道/` (sci-fi) is the reference example — proof that swapping novels doesn't touch the framework: same engine, new content package, new novel.

---

## 核心范式 / Core Paradigm

```
                    ┌─────────────────────────────┐
                    │  novels/<你的小说>/          │  ← 内容包 Content package（全部可替换）
                    │  novel_config.json          │      框架与小说的唯一接口 / sole interface
                    │  bible/  写作法则·人设·世界观 │      (system prompt 注入)
                    │  agents/ 小说专属角色实现     │
                    │  arcs/   vault/  chapters/   │
                    └──────────────┬──────────────┘
                                   │ 读 novel_config
                    ┌──────────────▼──────────────┐
                    │  framework/                  │  ← 通用框架 / generic engine（不动）
                    │  引擎: tick_runner + agents   │
                    │  spec_builder  叙事合成       │
                    │  gen.py 生成管线 + 质量门禁    │
                    └─────────────────────────────┘
```

三个关键设计 / Three key design decisions：

1. **内容包驱动** — `novel_config.json` 声明 cast（谁是什么层级的 agent）、pov 显示名、主角人称代词、bible 注入清单、质量参数。框架据此实例化角色、组装 prompt。
   **Content-package driven** — `novel_config.json` declares the cast (who is what tier of agent), POV display names, protagonist pronouns, the bible injection list, and quality parameters. The framework instantiates characters and assembles prompts from it.
2. **bible 注入** — 写作法则、人设、世界观、人物锚点都是 `bible/` 下的 markdown 文件，gen.py 自动读取注入 system prompt。调约束改文件，不动代码。
   **bible injection** — writing rules, character settings, worldbuilding, and character anchors are markdown files under `bible/`, auto-read by gen.py and injected into the system prompt. Tune constraints by editing files, not code.
3. **多 agent 引擎** — 一个弧（数章）内，各角色 agent 并行感知世界、独立决策，Narrator 把多视角事件线合成结构化的章节 spec（按场景拆分、标注反转落点），再交给生成管线。
   **Multi-agent engine** — within an arc (several chapters), each character agent perceives the world in parallel and decides independently; the Narrator synthesizes the multi-POV event lines into a structured chapter spec (scene-split, reversal anchors marked), which is handed to the generation pipeline.

---

## 目录结构 / Directory Structure

```
writing/
├── framework/            # 通用框架 / generic engine（与小说解耦）
│   ├── tick_runner.py    # 弧级编排器（按 novel_config 实例化 agents）
│   ├── agent_base.py     # Agent 基类 / 状态 / 记忆
│   ├── agent_lite.py     # Tier2 配置式配角 agent
│   ├── narrator.py       # 叙事合成（场景聚类拆章 → 章节范围）
│   ├── scene_director.py # 两阶段对手戏（共享场景 LLM 交互解析）
│   ├── spec_builder.py   # LLM 章节结构设计（三节三场景 + 反转锚）
│   ├── world_sim.py      # 世界模拟器
│   ├── env_state.py      # 环境状态（恐慌/天气/基础设施）
│   ├── vault_sync.py     # vault 回写 / 记忆存储
│   ├── percept_filter.py # 感知过滤（不同角色看到不同世界）
│   └── api.py            # LLM 调用层（路由/重试）
├── scripts/gen.py        # 生成管线：spec → 正文 → 质量门禁
├── lib/                  # 共享库（vault_reader / bible_utils / log）
├── server/               # LLM 代理（SSE 客户端 + 本地代理）→ 见 server/README.md
├── novels/
│   └── 静默轨道/          # 参考小说（示例内容包，适配实证）
│       ├── novel_config.json
│       ├── bible/  agents/  specs/  chapters/
│       └── agents/README.md  # Tier1 agent 待写说明
├── tests/                # 框架单元测试（无 LLM / 无 I/O 依赖）
└── templates/novel/      # 空内容包骨架 + 接入指南
```

---

## 环境要求 / Requirements

- **Python 3.9+**
- 唯一第三方依赖：**`requests`**（其余全用标准库）
- 至少一个 LLM 供应商的 API key（见下节）

Only one third-party dependency (`requests`); the rest is stdlib. You need an API key from at least one LLM provider (see below).

## 安装 / Installation

```bash
pip install requests
```

## LLM 配置 / LLM API Setup

框架通过本地小代理 `server/gen_proxy.py` 与上游 LLM 供应商通信（SSE 流式，端口 **3002**）。好处：API key 不留在项目里，路由统一管理。

The framework talks to upstream LLM providers through a small local proxy, `server/gen_proxy.py` (SSE streaming on port **3002**). This keeps API keys out of the project and centralizes routing.

**1. 配置 key** — 代理自动读取 `~/.env`（项目目录外，别提交进 git），或直接导出环境变量：

Set your keys — the proxy auto-loads `~/.env` (outside the repo, don't commit it), or export env vars:

```bash
# ~/.env
OPENROUTER_API_KEY=sk-or-...
DEEPSEEK_API_KEY=sk-...
DASHSCOPE_API_KEY=sk-...        # 可选，仅 qwen 路由用 / optional, only for qwen routes
```

**2. 启动代理 / start the proxy:**

```bash
python server/gen_proxy.py      # listening on 0.0.0.0:3002
```

**3. 模型路由表** — 各场景用什么模型、走哪个供应商，统一在 `server/_api_client.py` 的 `MODEL_ROUTES` 维护（唯一权威来源）。默认：

The model route table — which model each scene uses and through which provider — lives in `MODEL_ROUTES` in `server/_api_client.py` (single source of truth). Defaults:

| 用途 / Use                  | provider    | model                 |
|-----------------------------|-------------|-----------------------|
| 正文生成 normal/expanded     | OpenRouter  | `moonshotai/kimi-k2.6` |
| outline / agent / state     | DeepSeek    | `deepseek-v4-flash`   |
| qwen-agent / qwen-long      | DashScope   | `qwen-max`            |

> 注意：默认快速开始同时需要 **OpenRouter + DeepSeek** 两个 key。如果你只有一个供应商，直接改 `MODEL_ROUTES`，把全部路由指到同一个 provider/model 即可。
>
> Note: the default quick start needs **both OpenRouter and DeepSeek** keys. If you only have one provider, edit `MODEL_ROUTES` and point every route at it.

## 快速开始 / Quick Start

```bash
# 1. 启动 LLM 代理（另一个终端）/ start the LLM proxy (another terminal)
python server/gen_proxy.py

# 2. 生成参考小说第一章 / generate chapter 1 of the example novel
python scripts/gen.py novels/静默轨道/specs/ch1.json

# 3. 单元测试（无 LLM / 无 I/O 依赖）/ unit tests (no LLM, no I/O)
python tests/test_split_scenes.py
python tests/test_engine_core.py
```

生成结果默认写到 `novels/<novel>/chapters/<章名>.md`；可用 spec 的 `output` 字段覆盖。`python scripts/gen.py --help` 查看全部参数（`--validate` 只校验 / `--force` 跳过校验 / `--use-engine` tick→gen 模式）。

Output defaults to `novels/<novel>/chapters/<title>.md`, overridable via the spec's `output` field. Run `python scripts/gen.py --help` for all flags (`--validate` validate-only, `--force` skip validation, `--use-engine` tick→gen mode).

## 接入自己的小说 / Bring Your Own Novel

复制 `templates/novel/` → 填 `novel_config.json` → 写 `bible/` 注入文件 → 写主角 agent（配角免写）→ 建 vault + arcs。

Copy `templates/novel/` → fill in `novel_config.json` → write your `bible/` injection files → write the protagonist agent (side characters need no code) → set up vault + arcs.

完整分步指南（cast 分级、bible_files、quality 参数）见 / Full step-by-step guide: `templates/novel/README.md`

## 每章工作流（人类 + AI 协作）/ Per-Chapter Workflow (Human + AI)

```
1. Design spec    → 你设计章节 spec（反转类型/位置、三节功能）
                    You design the chapter spec (reversal type/position, section functions)
2. User reviews   → AI 审逻辑和物理合理性
                    AI reviews logic and physical plausibility
3. Generate       → gen.py 生成正文
                    gen.py generates the prose
4. User reviews   → AI 复核 + 手动改定稿版
                    AI re-checks + you hand-edit the final draft
5. Confirm        → 定稿 / finalize
6. Update state   → 更新章节状态（下一章加载正确信息）
                    Update chapter state (next chapter loads correct info)
```

反转类型二选一 / Reversal types (pick one per chapter): **A** = 读者确认了之前只猜到的事 / readers confirm what they'd only guessed; **B** = 之前以为的事实是错的 / a previously-held fact is wrong. 连续两章不能都是纯推进 / two consecutive chapters can't both be pure progression.

## 状态 / Status

- ✅ 引擎全链（tick → spec → 正文）跑通，输出到 temp 不污染真章 / engine pipeline runs end-to-end
- ✅ 框架/内容包解耦（novel_config 驱动）/ framework/content decoupling
- 🚧 文档与适配示例持续完善中 / docs & adaptation examples ongoing
