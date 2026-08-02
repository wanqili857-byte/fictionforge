# AI 小说叙事框架

一套 **AI 智能体小说写作框架**：多 agent 引擎模拟角色行为线，把事件合成章节 spec，再经生成管线产出带质量门禁的正文。

**框架在根，小说是内容包。** 框架代码（`framework/`）与具体小说解耦——提示词、人设、世界观、人物 agent 都是可替换的"内容包"。换一本小说，只改内容，不改框架。

`novels/静默轨道/` 是参考示例（科幻）——用来实证"换小说不改框架"：同一个框架，换一套内容包即换一本小说。

## 核心范式

```
                    ┌─────────────────────────────┐
                    │  novels/<你的小说>/           │  ← 内容包（全部可替换）
                    │  novel_config.json           │      框架与小说的唯一接口
                    │  bible/  写作法则·人设·世界观   │      (system prompt 注入)
                    │  agents/ 小说专属角色实现       │
                    │  arcs/   vault/  chapters/   │
                    └──────────────┬──────────────┘
                                   │ 读 novel_config
                    ┌──────────────▼──────────────┐
                    │  framework/                  │  ← 通用框架（不动）
                    │  引擎: tick_runner + agents   │
                    │  spec_builder  叙事合成       │
                    │  gen.py 生成管线 + 质量门禁    │
                    └─────────────────────────────┘
```

三个关键设计：

1. **内容包驱动** — `novel_config.json` 声明 cast（谁是什么层级的 agent）、pov 显示名、主角人称代词、bible 注入清单、质量参数。框架据此实例化角色、组装 prompt。
2. **bible 注入** — 写作法则、人设、世界观、人物锚点都是 `bible/` 下的 markdown 文件，gen.py 自动读取注入 system prompt。调约束改文件，不动代码。
3. **多 agent 引擎** — 一个弧（数章）内，各角色 agent 并行感知世界、独立决策，Narrator 把多视角事件线合成结构化的章节 spec（按场景拆分、标注反转落点），再交给生成管线。

## 目录结构

```
writing/
├── framework/            # 通用框架（与小说解耦）
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
├── server/               # LLM 代理（SSE 客户端 + 本地代理）
├── novels/
│   └── 静默轨道/          # 参考小说（示例内容包，适配实证）
│       ├── novel_config.json
│       ├── bible/  agents/  specs/  chapters/
│       └── agents/README.md  # Tier1 agent 待写说明
├── tests/                # 框架单元测试（无 LLM / 无 I/O 依赖）
└── templates/novel/      # 空内容包骨架 + 接入指南
```

## 快速开始

```bash
# 1. 跑参考小说一章（需要 LLM 代理在跑，见 server/README）
python scripts/gen.py novels/静默轨道/specs/ch1.json

# 2. 单元测试（无 LLM / 无 I/O 依赖）
python tests/test_split_scenes.py
python tests/test_engine_core.py
```

## 接入自己的小说

复制 `templates/novel/` → 填 `novel_config.json` → 写 `bible/` 注入文件 → 写主角 agent（配角免写）→ 建 vault + arcs。详见 `templates/novel/README.md`。

## 每章工作流（人类 + AI 协作）

```
1. Design spec    → 你设计章节 spec（反转类型/位置、三节功能）
2. User reviews   → AI 审逻辑和物理合理性
3. Generate       → gen.py 生成正文
4. User reviews   → AI 复核 + 手动改定稿版
5. Confirm        → 定稿
6. Update state   → 更新章节状态（下一章加载正确信息）
```

反转类型二选一：**A** = 读者确认了之前只猜到的事；**B** = 之前以为的事实是错的。连续两章不能都是纯推进。

## 状态

- ✅ 引擎全链（tick → spec → 正文）跑通，输出到 temp 不污染真章
- ✅ 框架/内容包解耦（novel_config 驱动）
- 🚧 文档与适配示例持续完善中
