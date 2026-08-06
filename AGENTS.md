# AI Novel Generation Framework

框架与小说解耦：`framework/` 是通用引擎，`novels/<小说>/` 是可替换的内容包。换一本小说只改内容，不改框架。

## 目录结构

- `framework/` — 通用引擎（tick_runner / agent_base / agent_lite / scene_director / narrator / spec_builder / world_sim / vault_sync / percept_filter / env_state / novel_config / theory_of_mind / chapter_coordinator）
- `novels/<小说>/` — 内容包，`novel_config.json` 是框架与小说的唯一接口
- `lib/`、`scripts/gen.py`、`server/` — 生成管线与代理
- `tests/` — 框架单元测试（无 LLM / 无 I/O 依赖）
- `templates/novel/` — 新小说脚手架

## 小说生成工作流（双方约束）

每章流程，不可跳过：

1. **Design spec** — 设计章节 spec
   - 用 tension-only 格式：3 节，每节 100-150 字描述功能/情绪走向/结束状态，不写具体意象
   - 标注本章的反转类型（A/B）和位置
   - 至少包含一种反转/信息更新，连续两章不能都是纯推进
2. **User reviews spec** — 审 spec 逻辑和物理合理性，修改后确认
3. **Generate chapter** — gen.py 调用模型生成正文
4. **User reviews + rewrites** — 读生成结果，人工复核后手动修改
5. **Confirm finalized** — 确认定稿
6. **Update 章节状态** — 按最终版重写章节状态，确保下一章加载的是正确信息
7. → 回到 1，下一章

### Spec 设计约束

每章 spec 必须包含至少一种反转/信息更新：

- **A — 新确证信息**：读者确认了之前只猜到的事
- **B — 认知反转**：之前以为的某个事实是错的

反转类型和位置在 step 1 标出，不写入 gen.py。

## 模型生成约束

system prompt 注入内容统一在 `bible/` 目录维护，gen.py 自动读取。改约束只改 bible 文件，不动代码。内容包通过 `novel_config.json` 声明 bible 注入清单。

gen.py 以外的调用（手动润色/测试）必须手动注入 bible 约束到 system prompt。润色指令禁止用宽泛描述（"润色一下"）。改用精确指令（如"在不增加比喻密度的前提下，增强感官密度和身体信号"），否则模型会自由发挥其默认"好文笔"模式。

## 认知反转引擎（v0.2.0）

- **真相表**：`bible/真相表.md`，作者维护的权威事实表，`novel_config.json` 的 `theory_of_mind.truth_table` 声明。**不进 bible_files、不 verbatim 注入**——只有推导出的 `info_gaps` 进 prompt（模型全知会泄谜底）。`false` 行 = 广泛流传但错误的认识（B 型素材）。
- **知识 vs 真相**：每角色 `knowledge`（已确认事实）/ `tom`（「我认为 X 知道什么」）/ `unknown_to_character`。A 型反转 = 读者确认之前只敢猜的事；B 型 = 之前以为对的事实是错的。
- **顶层协调器**：每章走 gen（手写 spec → info_gaps 标注 → 推进知识 → 生成）/ engine（tick JSON 落盘，不生成正文）/ hybrid（tick → 机械 spec → 标注 → 生成）。`scripts/run_chapter.py` 调用。配置分层：`cli > arc.pipeline_mode > chapter_overrides > default_mode > gen`。

## 写作技法参考（悬疑/恐怖章节专用，其他章节不适用。持续补充）

1. **恐怖信号渐强阶梯** — 同一场景内按强度递增排列感知信号（声音延迟→目光异常→身体变异），不堆在同一段。
2. **假释放机制** — 让读者以为危险解除，下一场景立即升级。两章用一次即可，避免疲劳。
3. **幻觉帧手法** — 一个无法解释的视觉异常置于主角主观视角，随后"眨眼消失"。不确定性制造下一章期待。
4. **具象比喻法则** — 每个恐怖信号绑一个日常参照物，不写抽象形容词。
5. **身体信号代替心理描写** — 恐惧不写"觉得害怕"，写呼吸消失、喉咙锁住、闻到气味、指尖发凉。
6. **深三 POV 赦免权** — 深三不写表情，但每章情绪峰值处破例一次：写主角的脸。不看前面用过什么信号，用新的。

## 角色人格写作指南（通用模板）

每章写作时注入以下三层：

**A 层（吐槽视角）— 每章 2-3 次：** 内心独白：对眼前的事有即时评价，锋利但不恶毒。通常不说出来，读者通过"心想"看到。字数 1-3 句不拖节奏。吐槽自己不比吐槽别人少。

**C 层（外冷内温）— 每章 1-2 次暴露动作：** 说不出"在乎"但会做。嘴上没反应，下一幕做一个无关但有关的小动作。不做解释，读者自己领会。

**B 层（数据偏执）— 世界观节点备用：** 相信数据和数学不会撒谎——这是角色的信仰。当数据开始撒谎时信仰崩塌。

**缺陷（固定）：** 计算强迫（数一切能数的东西）、情感表达障碍（行动十分嘴上零分）、对蠢话零容忍。

**关系摩擦：** 配角可以吐槽主角，主角不生气。重要配角可以戳穿主角。这些场景不要删除或弱化——它们是读者看到"主角是活人"的机会。

## 扩展

- **新小说**：复制 `templates/novel/`，填 `novel_config.json` + bible + agents（Tier1 继承 `framework.agent_base.Agent`；Tier2 用 `framework.agent_lite` 免写代码）。见 `templates/novel/README.md`。
- **新注入维度**：`bible/` 下加文件，`novel_config.json` 的 `bible_files` 加一行。
- **新质量检查**：gen.py 的 Phase 4-5 加函数。
- **参考示例**：`novels/静默轨道/`（科幻，适配实证——换小说不改框架）。
