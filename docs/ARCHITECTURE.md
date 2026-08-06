# 架构

## 双管线

```
管线A: 多 agent 引擎（弧级）              管线B: 生成管线（单章）
────────────────────────────           ────────────────────────────
TickRunner 编排一个弧（数章）            gen.py 消费章节 spec
  │                                      │
  ├─ 加载 vault 起始状态                  ├─ 组装 system prompt
  ├─ 按 novel_config 实例化 agents       │   (bible 注入: 写作法则/人设/世界观)
  ├─ WorldSim 模拟世界状态               ├─ Phase1: 逐节顺序生成（一次调用=一场景，
  ├─ PerceptionFilter: 各角色看不同世界   │            上一节真实文本串接为下一节上文；
  ├─ agents 并行 perceive + decide       │            expanded 核心节独立调用+展开方向）
  ├─ SceneDirector: 共享场景对手戏解析    ├─ Phase3: 顺序拼接 + 字数检查
  │   （同天同地场景 LLM 交互合成）       ├─ Phase3b: 章节状态回写
  ├─ Narrator 合成 TickResult            ├─ Phase4: anti-AI 检查 + 自动修复
  │   └─ 场景聚类拆章(天×地点)           ├─ Phase5: 质量检查 + 人味审
  │   └─ 三节三场景 spec (LLM)          └─ 输出章节正文
  ├─ 弧末反思：Tier1 记忆压缩为高阶洞察
  └─ tick JSON / markdown
```

`gen.py --use-engine <tick.json> --chapter N` 把两条管线接起来：引擎 tick → SpecBuilder(LLM) 设计章节 spec → 生成管线出正文。

## 顶层协调器（v0.2.0）

每章经 `ChapterCoordinator`（`framework/chapter_coordinator.py`）选管线路径：

- **gen**：手写 spec → 理论心智层标注（`info_gaps` 注入 prompt）→ 推进知识 → gen.py
- **engine**：TickRunner tick → JSON 落盘，不生成正文
- **hybrid**：engine tick → 机械 spec → 理论心智层标注 → gen.py

配置分层（novel_config.pipeline + arc + CLI）：`cli > arc.pipeline_mode > chapter_overrides > default_mode > "gen"`。
CLI：`python3 scripts/run_chapter.py novels/<小说> --chapter N [--mode ...]`。

理论心智层数据源是 `bible/真相表.md`（作者维护的权威事实表，声明于 `theory_of_mind.truth_table`，
**不进** bible_files、不 verbatim 注入——只有推导出的 `info_gaps` 进 prompt）。真相表缺失/禁用时静默降级，行为与 v0.1.x 一致。

## 组件职责

| 组件 | 职责 | 与小说耦合 |
|---|---|---|
| `tick_runner.py` | 弧级编排；按 cast 配置 importlib 实例化 agents | 无（读 novel_config） |
| `agent_base.py` | Agent 基类、AgentState、记忆 Memory/Retriever | 无 |
| `agent_lite.py` | Tier2 配置式配角（信念+目标+行动线，无持久记忆） | 无 |
| `percept_filter.py` | 感知过滤：角色感知阶段 → 看到哪些世界痕迹 | 无 |
| `env_state.py` | 环境状态：时间/天气/恐慌聚合/基础设施/变异等级 | 无 |
| `world_sim.py` | 世界模拟：政府/媒体/公众/基础设施对事件的反应 | 无 |
| `narrator.py` | 多视角事件合成；场景聚类拆章；对手戏/异常强度权重 | 读 novel_config（pov_labels/cast_names） |
| `spec_builder.py` | LLM 章节结构设计：三节功能、三场景绑定、反转落点锚；`build_spec_mechanical`（机械 spec，hybrid 路径用） | 读 novel_config + bible/人物锚点.md |
| `theory_of_mind.py` | 理论心智层：真相表解析、知识vs真相、跨角色 ToM、A型反转追踪、`info_gaps` 标注 | 读 novel_config + bible/真相表.md |
| `chapter_coordinator.py` | 顶层协调器：每章选管线路径（gen/engine/hybrid），依赖注入可单测 | 无（读 novel_config） |
| `vault_sync.py` | vault 回写、MemoryStore 序列化（含 beliefs/knowledge/tom） | 无 |
| `gen.py` | 生成管线 + 质量门禁；主角名/温情角色从 novel_config 读 | 读 novel_config |
| `novels/<n>/agents/*.py` | 小说专属角色 agent（Tier1 继承 Agent；Tier2 用 LiteAgent） | 全部（这是内容包） |
| `novels/<n>/bible/*.md` | system prompt 注入内容（写作法则/人设/世界观/人物锚点） | 全部（这是内容包） |

## 引擎数据流（一个 tick）

```
arc_config (arcs/*.md frontmatter)
    │
    ▼
┌───────────────────────────────┐
│ TickRunner                    │
│  1. _load_vault_state(chN)     │
│  2. _init_agents               │  ← 按 novel_config.cast importlib 实例化
│     Tier1: Cls.from_vault_state │     小说专属 agent (novels/<n>/agents)
│     Tier2: LiteAgent(name, ...) │
│  2.5 _restore_agent_epistemics │  ← 恢复 beliefs/knowledge/tom（vault）
│                                  │     knowledge=已确认事实, tom=跨角色认知
│  3. EnvState.from_vault_reader  │
│  4. WorldSim.run → world_output │
│  5. _build_world_structure     │  → public/traces/hidden 三层
│  6. agents 并行 perceive+decide│  → agent_outputs (actions/beliefs/...)
│  7. env 更新（恐慌/基础设施）    │
│  8. Narrator.synthesize        │
│     └─ 轨迹提取(含 private)     │
│     └─ 场景聚类(天×地点)拆章     │  → suggested_chapter_split(含 events)
│     └─ hooks/state/reversal    │  → TickResult
│  8.1 理论心智层                │  ← sync_knowledge_from_beliefs
│                                  │   → propagate_tom_all（同场共现/告知/信任衰减）
│                                  │   → sync_unknowns
│                                  │   → detect_type_a → TickResult.type_a_events
│  9. MemoryStore 持久化记忆      │  ← 含 beliefs/knowledge/tom
│ 10. VaultSync / 输出 tick JSON  │
└───────────────────────────────┘
```

## 拆章与 spec 设计（质量核心）

- **场景聚类**：事件按 `(天, 地点)` 聚类成场景；天是原子单位，同一天不跨章。
- **场景强度**：对手戏(+2) / 隐藏信息(+1) / 多人同场(+2) / 异常关键词(+1)。权重 ≥3 = 强场景。
- **按天切分**：多余天放最后（escalation），无强场景章从后章挪强天。
- **三节三场景**：SpecBuilder 强制每节绑定不同 `scene_anchor`（`第X天 @地点`）；核心节优先对手戏场景；反转在本章时核心节锚到反转场景。
- **对手戏判定**：见面/交接动词 + 电话/语音排除（发语音说"见一面"≠当面）。

## 质量门禁（gen.py Phase 4-5）

| 检查 | 逻辑 |
|---|---|
| anti-AI | 命中禁词（`某种`/`显得`/`仿佛`…）→ 自动改写修复 |
| 比喻密度 | 比喻标记词计数 ≤ 上限（默认 5，novel_config.quality.simile_max） |
| 篇幅 | 目标字数 ±20%（LONG/SHORT 警告） |
| 人味审 (verve) | 主角内心独白密度、温情角色互动质感、C层动作、温度触觉、自嘲、感官密度 |
| 一致性 | 章节状态回写 + vault 连续性检查 |

## 扩展点

- **新小说**：复制 `templates/novel/`，填 `novel_config.json` + bible + agents。见 `templates/novel/README.md`。
- **新注入维度**：`bible/` 下加文件，`novel_config.json` 的 `bible_files` 加一行（gen.py 自动读）。
- **新角色**：cast 加条目。Tier1 写 agent；Tier2 用 `framework.agent_lite`。
- **新质量检查**：gen.py Phase 4-5 加函数。
