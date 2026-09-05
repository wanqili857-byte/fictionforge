# 版本规划

框架版本 → release 宣传记录对照。发布时照抄到 GitHub release。

## v0.3.0 — 修订感知管线

**定位：** 从「日更流水线」转「精品生产线」——AI 草稿 + 人工修订 + 修订回灌的完整循环（2026-08-28 转向，原「引擎落地」项降级至 v0.4+）

- canon 层 —— 章节状态机 `ai_draft → human_revised → canon`；gen.py 只写 `chapters/drafts/`，canon（`chapters/`）只由人工晋升，生成管线永不覆盖；context_before 只从 canon 拼接
- 修订回灌 —— diff(AI 草稿, 定稿) 落盘；**原因标签由 AI 事后标注**（逐块分类：AI味/节奏/素材/逻辑），蒸馏时人工审校纠偏；蒸馏产物 = 素材库（**只存人改好句**——v5 教训：AI 原句是坏例，进 prompt 会被模型照抄；坏例对留在修订记录人审）
- prompt 预算配额 —— 回灌产物只增不减，必须设蒸馏配额（每类上限 N 条、新挤旧、定期人工归并），防 system prompt 膨胀致质量倒退（v0 教训：~2755 字即机械）
- 状态重提取 —— 修订后重跑 `update_chapter_state`（章节状态.md / 角色状态.json），杜绝「状态基于 AI 草稿、正文已人改」的 context 复利偏差
- 局部重生成 —— 段级粒度：canon 锁死 + 目标段重生成，上文 = 前后 canon 实稿；spec 校验的章节顺序锁（gen.py:521-534）只对「新章生成」生效，不挡「旧章修订」
- canon 前门禁闭环 —— 晋升 canon 前对定稿重跑 anti-AI / verve 门禁（人改引入的问题也能抓）；句长方差等统计指标降级为内部参考，不追外部检测器
- 试点：作者私有内容包——ch1 全链已在本地验证（AI 草稿→人修→promote→distill→回灌）；**小说内容不入公开 repo**，公开示例一律走 `novels/静默轨道`
- spec-canon 漂移注记 —— canon 晋升时记差异（改了什么/为什么），下一章 spec 撰写可见

## v0.4.0+ — 引擎落地（降级顺延）

原 v0.3.0 内容，精品转向后顺延：

- 引擎全链路真跑 —— `--use-engine` 真 LLM 跑通（不只过数据流）
- 静默轨道 agents —— 示例小说 Tier1 主角 agent
- 真相对照的 B 型反转素材 —— 真相表加 `false` 行

## v0.1.1 — 框架可用性优化（≈2026-08-05）

**定位：** 第二版·框架可用性优化

- 外挂内容包（Option B）—— spec 用 `novel_dir` 指向外部包，换小说第二种姿势
- `divergence_vibe` 接线 —— 每节注入「发散方向」，normal/expanded 两处 prompt 都读
- CI：双测试套件入库、去 pip cache
- README：quickstart 修正

## v0.2.0 — 认知反转引擎

**定位：** 信息差正式建模，反转从碰运气变可设计

- 理论心智层 —— `bible/真相表.md`（作者维护权威事实）→ 每角色「知识 vs 真相」对照 + 跨角色 ToM + type-A 反转追踪；`info_gaps` 标注注入 spec（不进 bible_files，不泄谜底）
- 顶层协调器 —— 每章选管线路径：gen / engine / hybrid，配置分层（cli > arc > 章节覆盖 > 默认 > gen）
- 认知持久化 —— MemoryStore 落盘 beliefs/knowledge/tom，补「信念不落盘」缺口
- 测试 4 套件 —— engine_core + split_scenes + theory_of_mind（30）+ chapter_coordinator（12）
- CLI —— `scripts/run_chapter.py`，零新增 LLM 调用，全部确定性逻辑（引擎真 LLM 跑留 v0.3.0）

## Release 流程

1. 该版本 TODO 项全完成 + 测试全过
2. `git push origin main`
3. `gh release create vX.Y.Z --title "<定位>" --notes "<从本文件对应段复制>"` + tag
4. 私有仓库同步：作者私有内容包自行 `git pull` 框架更新（私有小说永不进公开 repo）

## 版本历史

- **v0.3.0**（2026-08-30）— 修订感知管线：canon/drafts 分家、promote/distill 修订回灌、M4 局部重生成、spec-canon 漂移注记、DESIGN 五铁律
- **v0.1.1**（2026-08-06）— 第二版·框架可用性优化：外挂内容包、divergence_vibe 接线、CI 双套件、README 修正
- **v0.1.0**（2026-08-02）— FictionForge 第一版：双管线（gen.py + engine）、引擎 Agent 强化、框架/内容解耦、公开 sanitize
