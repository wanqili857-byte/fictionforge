"""
narrator.py — 叙事合成器。

从各 Agent + 世界模拟器 + 环境状态的输出中，
合成结构化的 TickResult，用于 gen.py 消费。

TickResult 包含：
  - character_trajectories: 多视角事件线
  - state_changes: 状态变更
  - new_hooks: 新伏笔
  - suggested_chapter_split: 拆章建议
  - reversal_plan: 反转规划
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from . import novel_config as novel_config_mod


# ── 数据结构 ────────────────────────────────────────────────────────────

@dataclass
class Event:
    """单个事件。"""
    day: int
    time: str
    location: str
    brief: str
    pov: str = ""  # 哪个角色的视角


@dataclass
class ChapterScope:
    """单个章节的范围。"""
    chapter_num: int
    title_hint: str = ""
    events: list[Event] = field(default_factory=list)
    sections: int = 3
    tension_direction: str = ""
    reversal_at_section: int = 0  # 0 = 无, 1-3 = 在第几节


@dataclass
class Hook:
    """伏笔条目。"""
    id: str = ""
    description: str = ""
    created_at_chapter: int = 0
    status: str = "open"


# 场景异常关键词默认值（可被 novel_config.quality.anomaly_words 覆盖）
DEFAULT_ANOMALY_WORDS = (
    "不对", "异常", "消失", "失灵", "信号", "停电", "错误", "空白", "变化", "神秘",
)

# 非当面渠道——提到名字但只是电话/语音，不算对手戏
PHONE_WORDS = (
    "语音", "电话", "短信", "发消息", "发了一条", "微信", "QQ", "邮件", "留言",
)


@dataclass
class TickResult:
    """一次 tick 的完整输出。"""
    arc_id: str = ""
    novel: str = ""
    chapters_covered: str = ""
    character_trajectories: dict[str, list[Event]] = field(default_factory=dict)
    state_changes: dict = field(default_factory=dict)
    new_hooks: list[Hook] = field(default_factory=list)
    suggested_chapter_split: list[ChapterScope] = field(default_factory=list)
    reversal_plan: dict = field(default_factory=dict)


# ── Narrator ────────────────────────────────────────────────────────────

class Narrator:
    """叙事合成器。将多层输出合并为 TickResult。"""

    def __init__(self, vault_reader=None, novel_dir=None):
        self.vault_reader = vault_reader  # 保留引用，供子类扩展
        # 角色名/pov 标签从 novel_config 读——框架不绑定具体小说
        self.novel_dir = novel_dir or novel_config_mod.novel_dir_from_vault(vault_reader)
        self.novel_config = (novel_config_mod.load(self.novel_dir)
                             if self.novel_dir else {})
        self.pov_labels = novel_config_mod.get_pov_labels(self.novel_config)
        self.cast_names = novel_config_mod.cast_names(self.novel_config)
        # 主角名（私密轨迹前缀用）——从 config 读，不硬编码
        self.protagonist = novel_config_mod.protagonist_name(self.novel_config)
        # 场景异常关键词——config 可覆盖，默认通用词表
        self.anomaly_words = self._load_anomaly_words()

    def _load_anomaly_words(self) -> tuple:
        """场景强度权重用的异常关键词。config 优先，默认通用词表。"""
        words = (self.novel_config or {}).get("quality", {}).get("anomaly_words")
        if words:
            return tuple(words)
        return DEFAULT_ANOMALY_WORDS

    def _pov_label(self, pov: str) -> str:
        """pov 键 → 显示名。config 有 labels 用之；否则通用兜底（去掉 _private 后缀）。"""
        if self.pov_labels:
            return self.pov_labels.get(pov, pov)
        return pov[:-len("_private")] if pov.endswith("_private") else pov

    def synthesize(self, arc_config: dict,
                   agent_outputs: dict[str, dict],
                   world_output: dict,
                   env_state: dict) -> TickResult:
        """合成所有输出为 TickResult。

        agent_outputs: {"protagonist": {...}, "mom": {...}, ...}
        world_output: world_sim 模块的输出
        env_state: env_state.to_dict()
        """
        result = TickResult(
            arc_id=arc_config.get("arc_id", "unnamed_arc"),
            chapters_covered=str(arc_config.get("chapters", [])),
        )

        # 1. 提取各角色事件线
        result.character_trajectories = self._extract_trajectories(agent_outputs)

        # 2. 拆章建议（按天平分）
        split = self._suggest_chapter_split(
            result.character_trajectories,
            arc_config,
        )
        result.suggested_chapter_split = split

        # 3. 提取新钩子
        result.new_hooks = self._extract_hooks(
            agent_outputs, world_output, arc_config
        )

        # 4. 状态变更
        result.state_changes = self._build_state_changes(
            agent_outputs, world_output, env_state
        )

        # 5. 反转规划
        result.reversal_plan = {
            "type": arc_config.get("reversal_type", "A"),
            "description": arc_config.get("reversal_description", ""),
            "position": arc_config.get("reversal_position", ""),
        }

        return result

    def _extract_trajectories(self,
                              agent_outputs: dict) -> dict[str, list[Event]]:
        """从 agent 输出中提取事件线。"""
        trajectories = {}

        private_prefix = f"[{self.protagonist}不知道] " if self.protagonist else "🔒 "

        for agent_name, output in agent_outputs.items():
            events = []
            # 可见性标记：新键 visible_to_protagonist，兼容旧键 visible_to_linmo
            has_visibility = any(
                "visible_to_protagonist" in act or "visible_to_linmo" in act
                for act in output.get("actions", [])
            )
            for act in output.get("actions", []):
                # 有可见性标记且主角不可见 → 进入私密轨迹
                visible = act.get("visible_to_protagonist",
                                  act.get("visible_to_linmo", True))
                if has_visibility and not visible:
                    trajectories.setdefault(f"{agent_name}_private", []).append(Event(
                        day=act.get("day", 0),
                        time=act.get("time", "?"),
                        location=act.get("location", "?"),
                        brief=f"{private_prefix}{self._compose_brief(act)}",
                        pov=f"{agent_name}_private",
                    ))
                    continue
                events.append(Event(
                    day=act.get("day", 0),
                    time=act.get("time", "?"),
                    location=act.get("location", "?"),
                    brief=self._compose_brief(act),
                    pov=agent_name,
                ))
            trajectories[agent_name] = events

        # 第二遍：渲染 private_observations（其他角色观察到的、主角不知道的事）
        for agent_name, output in agent_outputs.items():
            for obs in output.get("private_observations", []):
                observation = obs.get("observation", "")
                if not observation:
                    continue
                about = obs.get("about", "")
                concern = obs.get("concern_level")
                lvl = f"，担忧程度 {concern}/10" if concern not in (None, "") else ""
                trajectories.setdefault(f"{agent_name}_private", []).append(Event(
                    day=obs.get("day", 0),
                    time="?",
                    location=obs.get("location", "?"),
                    brief=f"观察{about}：{observation}{lvl}",
                    pov=f"{agent_name}_private",
                ))

        return trajectories

    @staticmethod
    def _compose_brief(act: dict) -> str:
        """把 action + inner(A层) + warm_action(C层) 合成一行 brief。"""
        brief = act.get("action", "")
        inner = act.get("inner", "")
        warm = act.get("warm_action", "")
        if inner:
            brief += f"　心想：{inner}"
        if warm:
            brief += f"（{warm}）"
        return brief

    def _suggest_chapter_split(self,
                                trajectories: dict[str, list[Event]],
                                arc_config: dict) -> list[ChapterScope]:
        """按场景聚类拆章（规则，非 LLM）。

        场景 = (天, 地点) 聚类的事件组。场景按时间序分配到各章，
        锚点切分尽量让每章含至少一场强场景（对手戏/隐藏信息/异常），
        并把事件填入 scope.events，供 spec_builder 按场景渲染。
        """
        chapters = arc_config.get("chapters", [])
        if not chapters:
            return []

        scenes = self._cluster_scenes(trajectories)
        return self._distribute_scenes(scenes, chapters)

    def _cluster_scenes(self, trajectories: dict) -> list[dict]:
        """把事件按 (天, 地点) 聚类成场景，附戏剧强度权重。"""
        scene_map: dict[tuple, dict] = {}
        for pov, evs in trajectories.items():
            for ev in evs:
                key = (ev.day, ev.location)
                sc = scene_map.setdefault(key, {
                    "day": ev.day,
                    "location": ev.location,
                    "povs": set(),
                    "events": [],
                    "first_time": ev.time or "99",
                })
                sc["povs"].add(pov)
                sc["events"].append(ev)
                if (ev.time or "99") < sc["first_time"]:
                    sc["first_time"] = ev.time or "99"

        scenes = list(scene_map.values())
        scenes.sort(key=lambda s: (s["day"], s["first_time"]))
        for sc in scenes:
            self._scene_weight(sc)
        return scenes

    def _scene_weight(self, scene: dict) -> int:
        """场景戏剧强度：对手戏 > 异常/隐藏信息 > 独处日常。"""
        w = 1
        povs = scene["povs"]
        briefs = [ev.brief for ev in scene["events"]]
        pov_names = {self.pov_labels.get(p, p) for p in povs}
        all_names = self.cast_names or list(self.pov_labels.values())

        interactive = False
        has_private = any(p.endswith("_private") for p in povs)
        for b in briefs:
            if any(v in b for v in PHONE_WORDS):
                continue
            for pn in pov_names:
                for n in all_names:
                    if n and n != pn and n in b:
                        interactive = True
                        break

        if interactive:
            w += 2
        if has_private:
            w += 1
        if len(povs) > 1:
            w += 2
        if any(wd in b for b in briefs for wd in self.anomaly_words):
            w += 1

        scene["interactive"] = interactive
        scene["has_private"] = has_private
        scene["weight"] = w
        return w

    def _distribute_scenes(self, scenes: list[dict],
                           chapters: list[int]) -> list[ChapterScope]:
        """把场景按天分配到各章。天是原子单位——同一天的场景不跨章。

        天序切分：多余天放最后（escalation，恐慌/异常递增），
        再跑强场景修复：某章无强场景时从后章挪一个强天过来。
        """
        n = len(chapters)
        scopes = [ChapterScope(chapter_num=ch, sections=3) for ch in chapters]
        if not scenes:
            return scopes

        day_list = sorted({sc["day"] for sc in scenes})
        day_strong = {
            d: sum(1 for sc in scenes if sc["day"] == d and sc["weight"] >= 3)
            for d in day_list
        }
        day_scenes = {
            d: [sc for sc in scenes if sc["day"] == d] for d in day_list
        }

        if len(day_list) <= n:
            # 每天一章，多出的章给 0 天
            day_cuts = list(range(1, len(day_list) + 1)) + \
                       [len(day_list)] * (n - len(day_list))
        else:
            base, rem = divmod(len(day_list), n)
            day_cuts = []
            acc = 0
            for k in range(n):
                acc += base + (1 if k >= n - rem else 0)
                day_cuts.append(acc)
        day_cuts = self._repair_strong_days(day_list, day_strong, day_cuts)

        for i, ch in enumerate(chapters):
            lo_d = day_cuts[i - 1] if i > 0 else 0
            hi_d = day_cuts[i]
            ch_scenes = [sc for d in day_list[lo_d:hi_d]
                         for sc in day_scenes.get(d, [])]
            scope = scopes[i]
            scope.events = [ev for sc in ch_scenes for ev in sc["events"]]
            if ch_scenes:
                locs = "、".join(dict.fromkeys(s["location"] for s in ch_scenes))
                has_strong = any(s["weight"] >= 3 for s in ch_scenes)
                scope.tension_direction = (
                    f"{len(ch_scenes)} 场场景（{locs}）"
                    + (" · 含强场景" if has_strong else "")
                )
            else:
                scope.tension_direction = "（无事件）"
        return scopes

    @staticmethod
    def _repair_strong_days(day_list: list[int], day_strong: dict,
                            day_cuts: list[int]) -> list[int]:
        """某章无强场景且后章含≥2 个强天 → 把后章第一个强天挪进该章。"""
        n = len(day_cuts)
        for i in range(n - 1):
            lo = day_cuts[i - 1] if i > 0 else 0
            hi = day_cuts[i]
            if any(day_strong.get(d, 0) >= 1 for d in day_list[lo:hi]):
                continue
            for j in range(i + 1, n):
                jlo = day_cuts[j - 1] if j > 0 else 0
                jhi = day_cuts[j]
                j_strong = [d for d in day_list[jlo:jhi]
                            if day_strong.get(d, 0) >= 1]
                if len(j_strong) >= 2:
                    day_cuts[i] = day_list.index(j_strong[0]) + 1
                    break
        return day_cuts

    def _extract_hooks(self, agent_outputs: dict,
                       world_output: dict,
                       arc_config: dict) -> list[Hook]:
        """从各层输出中提取新伏笔。"""
        hooks = []
        start_ch = min(arc_config.get("chapters", [1]))

        # 从世界输出的 hidden_truths 推断伏笔
        for ht in world_output.get("hidden_truths", []):
            hooks.append(Hook(
                description=ht,
                created_at_chapter=start_ch,
                status="open",
            ))

        # 从 public_traces 推断伏笔
        for trace in world_output.get("public_traces", []):
            desc = trace.get("description", "")
            if desc and len(desc) > 5:
                hooks.append(Hook(
                    description=desc,
                    created_at_chapter=start_ch,
                    status="open",
                ))

        return hooks

    def _build_state_changes(self, agent_outputs: dict,
                              world_output: dict,
                              env_state: dict) -> dict:
        """整合状态变更。"""
        changes = {
            "world_stage": env_state.get("story_stage", ""),
            "public_events": world_output.get("public_sentiment", {}).get(
                "collective_behavior", []),
            "hidden_truths": world_output.get("hidden_truths", []),
            "infrastructure": world_output.get("infrastructure", {}),
            "env": {
                "time": env_state.get("time", {}),
                "weather": env_state.get("weather", {}),
                "aggregated_panic": env_state.get("aggregated_panic", 0),
            },
            "characters": {},
        }

        for agent_name, output in agent_outputs.items():
            ch = {}
            if output.get("perception_stage_update"):
                ch["perception_stage"] = output["perception_stage_update"]
            if output.get("goal_update"):
                ch["goal"] = output["goal_update"].get("immediate", "")
            if output.get("belief_updates"):
                ch["new_beliefs"] = [
                    b.get("proposition", "") for b in output["belief_updates"]
                    if isinstance(b, dict) and b.get("proposition")
                ]
                # 认知反转：被 revises 标记的旧信念（断裂痕迹）
                revised = [
                    b.get("revises") for b in output["belief_updates"]
                    if isinstance(b, dict) and b.get("revises")
                ]
                if revised:
                    ch["revised_beliefs"] = revised
            changes["characters"][agent_name] = ch

        return changes

    def to_markdown(self, result: TickResult, arc_config: dict) -> str:
        """输出 Obsidian 可读的 markdown 事件线速览。"""
        lines = []

        arc_name = result.arc_id or arc_config.get("arc_id", "unnamed")
        rev_type = arc_config.get("reversal_type", "?")
        rev_desc = arc_config.get("reversal_description", "")
        lines.append(f"# 引擎输出：{arc_name}")
        lines.append(f"> 弧范围：{result.chapters_covered}  |  反转：{rev_type} — {rev_desc}")
        lines.append("")

        # 角色轨迹（pov 显示名来自 novel_config，无配置时通用兜底）
        private_prefix = f"[{self.protagonist}不知道] " if self.protagonist else "🔒 "
        for pov, events in result.character_trajectories.items():
            label = self._pov_label(pov)
            lines.append(f"## {label} 的行动线")
            lines.append("")
            lines.append("| 天 | 时间 | 地点 | 事件 |")
            lines.append("|----|------|------|------|")
            for ev in events:
                brief = ev.brief
                if brief.startswith(private_prefix):
                    brief = "🔒 " + brief[len(private_prefix):]
                elif brief.startswith("[主角不知道] "):  # 兼容旧 tick 文件
                    brief = "🔒 " + brief[len("[主角不知道] "):]
                lines.append(f"| 第{ev.day}天 | {ev.time} | {ev.location} | {brief} |")
            lines.append("")

        # 世界状态
        sc = result.state_changes
        lines.append("## 世界状态变化")
        lines.append("")
        lines.append(f"- 阶段：{sc.get('world_stage', '?')}")
        panic = sc.get("env", {}).get("aggregated_panic", sc.get("env", {}).get("aggregated_panic", "?"))
        lines.append(f"- 聚合恐慌：{panic}/10")
        for ht in sc.get("hidden_truths", []):
            lines.append(f"- 🔮 隐藏真相：{ht}")
        lines.append("")

        # 新伏笔
        if result.new_hooks:
            lines.append("## 新伏笔")
            lines.append("")
            for h in result.new_hooks:
                lines.append(f"- {h.description}")
            lines.append("")

        # 拆章建议
        if result.suggested_chapter_split:
            lines.append("## 拆章建议")
            lines.append("")
            for s in result.suggested_chapter_split:
                td = s.tension_direction or "?"
                lines.append(f"- **第{s.chapter_num}章**：{s.sections} 节，张力方向「{td}」")
                if s.reversal_at_section:
                    lines.append(f"  - 反转位置：第{s.reversal_at_section}节")
            lines.append("")

        return "\n".join(lines)

    def to_json(self, result: TickResult) -> dict:
        """序列化 TickResult 为 JSON 兼容 dict。"""
        return {
            "arc_id": result.arc_id,
            "novel": result.novel,
            "chapters_covered": result.chapters_covered,
            "character_trajectories": {
                k: [
                    {"day": e.day, "time": e.time, "location": e.location,
                     "brief": e.brief, "pov": e.pov}
                    for e in evs
                ]
                for k, evs in result.character_trajectories.items()
            },
            "state_changes": result.state_changes,
            "new_hooks": [
                {"id": h.id, "description": h.description,
                 "created_at_chapter": h.created_at_chapter, "status": h.status}
                for h in result.new_hooks
            ],
            "suggested_chapter_split": [
                {
                    "chapter_num": s.chapter_num,
                    "title_hint": s.title_hint,
                    "sections": s.sections,
                    "tension_direction": s.tension_direction,
                    "reversal_at_section": s.reversal_at_section,
                    "events": [
                        {"day": e.day, "time": e.time, "location": e.location,
                         "brief": e.brief, "pov": e.pov}
                        for e in s.events
                    ],
                }
                for s in result.suggested_chapter_split
            ],
            "reversal_plan": result.reversal_plan,
        }
