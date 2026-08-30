# 框架路线图

## ✅ 已完成

### P0-P3 架构修复（2026-07）

- [x] `_api_client.py` sse_request() 加 timeout + 错误返回 None
- [x] `gen_proxy.py` Content-Length 上限检查
- [x] 世界观解析统一（bible_utils.py，gen.py 与 world_sim 共用）
- [x] gen.py main() 拆 Pipeline 类、日志统一、engine 并行 API、sys.path 统一
- [x] env_state 恐慌上限、percept_filter 配置化、vault_sync 插入标记、输出路径常量
- [x] 测试补充：`test_engine_core.py` 纯逻辑单元测试

### 引擎 Agent 强化（2026-08）

- [x] **framework 去小说耦合** — world_sim/narrator/agent_lite/spec_builder/gen.py 全 config 化；世界观注入 config 优先（world_summary + world_core_principle）→ 通用蒸馏兜底
- [x] **两阶段对手戏**（`framework/scene_director.py`）— 共享场景（同天同地）LLM 交互解析，替换独立提案；失败回退
- [x] **信念修正（AGM 式）** — `Belief.status`(active/revised) + `apply_belief_updates`（revises 标记旧命题，修正不删除）
- [x] **弧末反思巩固** — `Agent.reflect()` 记忆压缩为高阶洞察，tick_runner 阶段 8.25（仅 Tier1，失败不中断）
- [x] 回归：test_engine_core 24/24、test_split_scenes 25/25、dry-run OK

### 认知反转引擎（v0.2.0，2026-08-06）

- [x] **理论心智层** — `framework/theory_of_mind.py`：真相表解析 + 知识vs真相 + 跨角色 ToM + A型反转追踪 + `info_gaps` 标注；测试 30/30
- [x] **认知持久化** — MemoryStore 落盘 beliefs/knowledge/tom（`load_agent_state` 返回全量）
- [x] **顶层协调器** — `framework/chapter_coordinator.py` + `scripts/run_chapter.py`：gen/engine/hybrid 三路分发，配置分层（cli > arc > override > default > gen）；测试 12/12
- [x] 回归：engine_core 24/24、split_scenes 26/26

## ⬜ 待办

### v0.3.0 修订感知管线（2026-08-28 转向，见 docs/VERSION_PLAN.md）

- [ ] **canon 层** — 章节状态机 `ai_draft → human_revised → canon`；gen.py 输出改 `chapters/drafts/`，canon 只由人工晋升；context_before 只从 canon 拼接
- [ ] **修订回灌** — diff(AI 草稿, 定稿) 落盘 + AI 事后标注原因标签（AI味/节奏/素材/逻辑）；蒸馏产物 = 素材库（只存人改好句，AI 原句对留修订记录人审，v5 教训）
- [ ] **prompt 预算配额** — 回灌产物设上限（每类 N 条、新挤旧、定期人工归并），防 system prompt 膨胀
- [ ] **状态重提取** — 修订后重跑章节状态/角色状态提取，消除草稿-定稿偏差
- [x] **局部重生成** — `gen.py --resection <节id>`：目标节重写、其余节复用；节清单 `drafts/X.md.sections.json`；草稿被人工改过时守卫拦截（需 `--force` 显式覆盖）
- [x] **canon 前门禁** — promote 晋升前对定稿重跑 anti-AI / verve（只报告不自动改）；统计指标按决策不追外部检测器，仅内部参考
- [ ] **spec-canon 漂移注记** — canon 晋升时记差异，下一章 spec 可见
- [ ] **试点跑通** — 私有试点包 ch1 全循环：AI 草稿 → 人修 → promote → distill → ch2 带回灌产物生成（作者本地验证；公开 repo 只留示例 `novels/静默轨道`）

### v0.4.0+ 引擎落地（降级顺延）

- [ ] **引擎 → spec_builder → gen.py 全链路真跑** — `--use-engine` 只验证过数据流，未真 LLM 跑　`[v0.4.0]`
- [ ] **静默轨道 agents** — 示例小说 Tier1 主角 agent 待写（见 `novels/静默轨道/agents/README.md`）　`[v0.4.0]`
- [ ] **真相对照的 B 型反转素材** — 真相表加 `false` 行（作者在认知反转落点埋错误认识）　`[v0.4.0]`

## 参考

- 双管线：引擎（弧级）→ spec_builder → gen.py 生成管线（质量门禁）
- 示例小说：`novels/静默轨道/`
- 架构文档：`docs/ARCHITECTURE.md`
