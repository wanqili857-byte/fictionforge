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

## ⬜ 待办

- [ ] **理论心智层** — 每角色「知识状态 vs 世界真相」对照表 + 「我认为 X 知道什么」，type-A 反转的正式信息差建模
- [ ] **`divergence_vibe` 接线或删除** — spec JSON 里有字段，gen.py 的 prompt 构建从未读它
- [ ] **顶层协调器** — 判断每章走 gen.py pipeline、engine tick、还是混合模式
- [ ] **引擎 → spec_builder → gen.py 全链路真跑** — `--use-engine` 只验证过数据流，未真 LLM 跑
- [ ] **静默轨道 agents** — 示例小说 Tier1 主角 agent 待写（见 `novels/静默轨道/agents/README.md`）

## 参考

- 双管线：引擎（弧级）→ spec_builder → gen.py 生成管线（质量门禁）
- 示例小说：`novels/静默轨道/`
- 架构文档：`docs/ARCHITECTURE.md`
