# arcs/ — 故事弧配置

每个弧一个 `.md` 文件，用 YAML frontmatter 定义参数（Obsidian 可直接编辑）。
引擎一次 tick = 一个弧。

```yaml
---
arc_id: 弧名
chapters: [5, 6, 7]        # 覆盖的章节号
time_start: 第11天
time_end: 第14天+
tier1: [主角, 配角A]        # 参与 Tier1 的角色名（需在 novel_config cast 里）
waypoints: [家, 酒店, 城市]  # 活动地点
reversal_type: B           # A=新确证信息 / B=认知反转
reversal_description: 反转内容一句话
reversal_position: ch7 结尾 # 反转落在哪
tension_target: 7           # 目标恐慌值 0-10
world_modules: [government, media, infrastructure, public]
protagonist_hint: 主角这个弧里在做什么
---
```

`tier2` 不用在这里列——Tier2 角色由 novel_config cast 决定。
