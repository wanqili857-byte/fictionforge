# 版本规划

框架版本 → release 宣传记录对照。发布时照抄到 GitHub release。

## v0.1.1 — 框架可用性优化（≈2026-08-05）

**定位：** 第二版·框架可用性优化

- 外挂内容包（Option B）—— spec 用 `novel_dir` 指向外部包，换小说第二种姿势
- `divergence_vibe` 接线 —— 每节注入「发散方向」，normal/expanded 两处 prompt 都读
- CI：双测试套件入库、去 pip cache
- README：quickstart 修正

## v0.2.0 — 认知反转引擎

**定位：** 信息差正式建模，反转从碰运气变可设计

- 理论心智层 —— 每角色「知识状态 vs 世界真相」对照表 + 「我认为 X 知道什么」，type-A 反转的正式信息差建模
- 顶层协调器 —— 每章选管线路径（gen.py pipeline / engine tick / 混合）

## v0.3.0 — 引擎落地

**定位：** 引擎跑通示例小说，双管线都有活样本

- 引擎全链路真跑 —— `--use-engine` 真 LLM 跑通（不只过数据流）
- 静默轨道 agents —— 示例小说 Tier1 主角 agent

## Release 流程

1. 该版本 TODO 项全完成 + 测试全过
2. `git push origin main`
3. `gh release create vX.Y.Z --title "<定位>" --notes "<从本文件对应段复制>"` + tag
4. 私有仓库同步：`cd ~/novels/灰色频段 && git pull upstream main`

## 版本历史

- **v0.1.0**（2026-08-02）— FictionForge 第一版：双管线（gen.py + engine）、引擎 Agent 强化、框架/内容解耦、公开 sanitize
