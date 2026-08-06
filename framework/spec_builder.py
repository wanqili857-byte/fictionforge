"""
spec_builder.py — LLM-driven chapter spec designer.

Bridge between engine tick output and gen.py chapter spec.
TickResult → structured LLM call → gen.py-compatible spec JSON.

Key quality difference from mechanical build_spec_from_tick():
  - Reads raw events and designs narrative structure (tension curve, section purpose)
  - Writes descriptions as narrative prose, not event lists
  - tension_direction is writing instruction (how to write), not day number
  - Knows which section is expanded based on reversal position
"""

from __future__ import annotations
import json
import os
import re
from typing import Optional

from .api import call_structured
from . import novel_config as novel_config_mod


SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "mood": {
            "type": "string",
            "description": "单行描述本章情绪基调。例：'发现式恐怖——不是被入侵，是走进一个仍在运行但正在被替换的空间'",
        },
        "sections": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "enum": ["一", "二", "三"]},
                    "subject": {
                        "type": "string",
                        "description": "2-6字简洁标题，概括本节核心场景或动作",
                    },
                    "scene_anchor": {
                        "type": "string",
                        "description": "本节绑定的具体场景，格式：'第X天 @地点'（如'第11天 @城东旧工业区咖啡馆'）。三节必须互不相同，且对应下方场景列表中的真实场景。description 只写此场景里发生的事，不要混入其他场景。",
                    },
                    "description": {
                        "type": "string",
                        "description": "80-150字叙事性段落。写场景、写动作、写感官变化。不是事件列表。",
                    },
                    "tension_direction": {
                        "type": "string",
                        "description": "写作指令——告诉模型这一节怎么写（节奏/视角切换/感官密度/动作/禁忌），不是写什么内容。",
                    },
                    "weight": {
                        "type": "string",
                        "enum": ["normal", "expanded"],
                    },
                    "expanded_direction": {
                        "type": "string",
                        "description": "仅weight=expanded需要。展开方向——重点刻画哪几个具体场景/动作/感官细节。",
                    },
                    "target_words": {
                        "type": "integer",
                        "description": "仅weight=expanded需要。目标字数，通常600-800。",
                    },
                },
                "required": ["id", "subject", "description", "tension_direction", "weight", "scene_anchor"],
            },
        },
        "target_chars": {
            "type": "integer",
            "description": "全章目标字数，通常2500-3000",
        },
    },
    "required": ["mood", "sections", "target_chars"],
}


SYSTEM_PROMPT = """你是一个悬疑小说的章节结构设计师。

## 你的任务
根据引擎提供的完整故事弧数据（事件线、角色状态、世界变化），设计一章小说的写作 spec。
这个 spec 将被生成模型用来写正文。你必须设计出能让模型直接写出高质量章节的骨架。

## 三节结构规则
每章固定3节，功能和 tension 各不相同：

### 一（铺垫 & 积累）
功能：让读者进入场景。通过日常动作和感官细节积累异常信号。
tension：低位起，渐近。不写恐怖，写"不对"。
weight: normal

### 二（核心 & 转折）
功能：本章的核心事件。三段式——前1/3推进发现，中1/3认知反转或揭示，后1/3困境升级。
tension：从积累到释放到新困境。全章最高点。
weight: expanded（需 expanded_direction + target_words）

### 三（收尾 & 扩展）
功能：从核心事件中抽离/撤离。生理上脱离危险了，情绪上没有。
tension：下降后反弹——松弛感 → 新信息出现 → 更大的问题在窗外。
weight: normal

三节 tension 曲线：一<二<三。三的结尾要打开下一章的窗口。

## 场景绑定规则（三节必须三场景）
1. 每节在 scene_anchor 字段绑定一个具体场景（格式：第X天 @地点），对应下方"本章场景"列表中的真实场景。
2. 三节 scene_anchor 必须互不相同——同一场景禁止复用，材料不够也不准把两节写成同一场景。
3. 核心节（二）场景优先级：对手戏/信息交接（见面、递交资料、分享发现）> 直接发现异常 > 独自思考/日常。列表中标了（对手戏）或（隐藏信息）的场景是核心节首选。
4. 反转在本章时（看下方"反转"行），核心节必须落在反转所在场景。
5. 收尾节（三）如果本章只剩两个场景可选，允许绑定"核心场景的时间延伸"——主角带着核心事件里的物品/信息回到某处继续，scene_anchor 写'第X天 @地点（延伸）'。这不是复用，是推进，description 必须写延伸后的新进展。
6. description 只写绑定场景内发生的事——不要混入其他场景的事件。

## 写作指引
1. description 必须写叙事性文字（场景/动作/感官），不是 bullet point 或事件列表。
2. tension_direction 是写作指令，要具体：
   - 节奏（慢/渐快/碎片/长时间静止）
   - 主导感官（视觉/听觉/嗅觉/触觉）
   - 视角控制（主角主观 vs 上帝视角 vs 与其他角色的信息差）
   - 禁忌（不要什么）
3. expanded_direction 比 description 更具体——告诉模型"展开时重点刻画哪几个画面"。
4. 不要插入具体时间标记（如"凌晨三点"），gen.py 会从 vault 安排时间线。
5. mood 要让人读完就知道这章是哪类恐惧——心理压迫型/身体恐怖型/信息确认型/认知颠覆型。
6. 角色锚点在"角色锚点"段落注入（来自该小说 bible/人物锚点.md），按它写人物，不自行发明人设。"""


# 对手戏判定词：场景里有当面交流/信息交接才算，打电话/发语音不算
MEETING_VERBS = (
    "见面", "碰头", "汇合", "会合", "交接", "递给", "交给",
    "推过去", "同桌", "遇到",
)
# 非当面渠道——命中则排除（如"发语音说我们见一面"没真见面）
PHONE_WORDS = (
    "语音", "电话", "短信", "发消息", "发了一条", "微信", "QQ", "邮件", "留言",
)


class SpecBuilder:
    """LLM-driven chapter spec designer.

    Usage:
        builder = SpecBuilder()
        spec = builder.build(tick_data, chapter_num=8)
        if spec:
            with open("spec.json", "w") as f:
                json.dump(spec, f, ensure_ascii=False, indent=2)
    """

    def __init__(self, vault_reader=None, novel_dir=None):
        self.vault_reader = vault_reader
        # pov 显示名从 novel_config 读——框架不绑定具体小说
        self.novel_dir = novel_dir or novel_config_mod.novel_dir_from_vault(vault_reader)
        self.novel_config = (novel_config_mod.load(self.novel_dir)
                             if self.novel_dir else {})
        self.pov_labels = novel_config_mod.get_pov_labels(self.novel_config)

    def build(
        self,
        tick_data: dict,
        chapter_num: int,
        arc_config: Optional[dict] = None,
        novel: str = "",
    ) -> Optional[dict]:
        """Build chapter spec from tick data.

        Args:
            tick_data: Full tick result dict from tick_runner
            chapter_num: Which chapter to generate spec for
            novel: 小说目录名。默认从 tick 元数据取，取不到用空（调用方兜底）

        Returns:
            spec dict compatible with gen.py, or None on failure
        """
        context_text = self._build_context_text(tick_data, chapter_num)
        user_prompt = self._build_user_prompt(tick_data, chapter_num)

        full_system = SYSTEM_PROMPT + "\n\n" + context_text
        anchors = self._load_character_anchors(novel)
        if anchors:
            full_system += "\n\n" + anchors

        result = call_structured(
            full_system,
            user_prompt,
            SPEC_SCHEMA,
            route_key="expanded",
            temperature=0.75,
        )

        if not result or not result.get("sections"):
            return None

        # Fill defaults for expanded sections
        for sec in result["sections"]:
            if sec.get("weight") == "expanded":
                sec.setdefault("expanded_direction", sec.get("tension_direction", ""))
                sec.setdefault("target_words", 700)

        return {
            "from_engine": True,
            "tick_arc_id": tick_data.get("arc_id", ""),
            "novel": novel or tick_data.get("novel", ""),
            "chapter": chapter_num,
            "title": f"第{chapter_num}章",
            "mood": result.get("mood", ""),
            "sections": result["sections"],
            "target_chars": result.get("target_chars", 2800),
        }

    # ── Context extraction ──────────────────────────────────────────────

    def _build_context_text(self, tick_data: dict, chapter_num: int) -> str:
        """Build story context section: world stage, character states, etc."""
        parts = ["## 当前故事上下文"]

        sc = tick_data.get("state_changes", {})
        if not sc:
            return "\n".join(parts)

        stage = sc.get("world_stage", "")
        if stage:
            parts.append(f"故事阶段：{stage}")

        # Character states
        for char_name, changes in sc.get("characters", {}).items():
            label = self.pov_labels.get(char_name, char_name)
            ch_parts = []
            if changes.get("perception_stage"):
                ch_parts.append(f"感知阶段：{changes['perception_stage']}")
            if changes.get("goal"):
                ch_parts.append(f"目标：{changes['goal']}")
            beliefs = changes.get("new_beliefs", [])
            if beliefs:
                ch_parts.append(f"核心信念：{'；'.join(beliefs[:2])}")
            if ch_parts:
                parts.append(f"\n{label}：")
                for p in ch_parts:
                    parts.append(f"  - {p}")

        # Public events (de-dupe)
        seen_events = set()
        for ev in sc.get("public_events", []):
            if isinstance(ev, str) and ev and ev not in seen_events:
                seen_events.add(ev)
                parts.append(f"\n公众事件：{ev}")

        # Infrastructure anomalies
        infra = sc.get("infrastructure", {})
        if isinstance(infra, dict):
            anomalies = []
            for region, status in infra.items():
                if isinstance(status, dict):
                    for k, v in status.items():
                        if v not in ("normal", "正常"):
                            anomalies.append(f"{region}/{k}: {v}")
                elif isinstance(status, str) and status not in ("normal", "正常"):
                    anomalies.append(f"{region}: {status}")
            if anomalies:
                parts.append("\n基础设施异常：")
                for a in anomalies:
                    parts.append(f"  - {a}")

        return "\n".join(parts)

    def _load_character_anchors(self, novel: str) -> str:
        """从该小说 bible/人物锚点.md 注入角色锚点——框架不硬编码人物。"""
        novel_dir = self.novel_dir
        if not novel_dir and novel:
            novel_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "novels", novel)
        if not novel_dir:
            return ""
        try:
            from lib.bible_utils import load_bible_file
            return load_bible_file(novel_dir, "人物锚点.md")
        except Exception:
            return ""

    def _build_user_prompt(self, tick_data: dict, chapter_num: int) -> str:
        """Build user prompt with this chapter's events grouped into scenes."""
        lines = [f"请设计第{chapter_num}章的写作 spec。\n"]

        # ── Arc info ──
        arc_id = tick_data.get("arc_id", "?")
        ch_covered = tick_data.get("chapters_covered", "?")
        lines.append("## 故事弧信息")
        lines.append(f"弧：{arc_id}（覆盖章节 {ch_covered}）")

        rp = tick_data.get("reversal_plan", {})
        rev_type = rp.get("type", "")
        rev_desc = rp.get("description", "")
        rev_pos = rp.get("position", "")
        if rev_type:
            lines.append(f"\n反转类型：{rev_type} — {rev_desc}")
            if rev_pos:
                lines.append(f"反转位置：{rev_pos}")
            m = re.search(r"(\d+)", str(rev_pos))
            rev_in_ch = bool(m and int(m.group(1)) == chapter_num)
            if rev_in_ch:
                lines.append("→ 反转在本章：核心节（二）必须落在反转所在场景。")
            else:
                lines.append("→ 反转不在本章：核心节（二）选信息交接/异常发现密度最高的场景（对手戏优先）。")

        # ── This chapter's events, grouped into scenes ──
        ch_events = self._get_chapter_events(tick_data, chapter_num)
        scenes = self._group_scenes(ch_events)

        if scenes:
            lines.append(f"\n## 本章场景（{len(scenes)} 场）")
            lines.append("三节必须绑定三个不同的场景（scene_anchor 互斥），核心节优先选带标记的场景。")
            for i, sc in enumerate(scenes, 1):
                tags = []
                if sc["interactive"]:
                    tags.append("对手戏")
                if sc["has_private"]:
                    tags.append("隐藏信息")
                tag_str = f"　（{'/'.join(tags)}）" if tags else ""
                lines.append(f"\n### 场景{i}｜第{sc['day']}天 @{sc['location']}{tag_str}")
                for ev in sc["events"]:
                    label = self.pov_labels.get(ev.get("pov", ""), ev.get("pov", ""))
                    time_s = ev.get("time", "")
                    brief = ev.get("brief", "")
                    lines.append(f"- {time_s} [{label}] {brief}")
        else:
            lines.append("\n## 本章场景\n（无事件数据，从弧设定推断）")

        # ── Hooks available ──
        hooks = tick_data.get("new_hooks", [])
        if hooks:
            lines.append("\n## 可用伏笔")
            for h in hooks:
                desc = h.get("description", "") if isinstance(h, dict) else str(h)
                if desc:
                    lines.append(f"- {desc[:150]}")

        lines.append(f"\n请输出第{chapter_num}章的章节 spec。遵循三节结构，tension 曲线一<二<三。")
        return "\n".join(lines)

    def _get_chapter_events(self, tick_data: dict, chapter_num: int) -> list[dict]:
        """取本章事件：优先用拆章建议里填好的 events，否则按天范围兜底。"""
        split = tick_data.get("suggested_chapter_split", [])
        scope = next((s for s in split if s.get("chapter_num") == chapter_num), None)
        if scope and scope.get("events"):
            events = list(scope["events"])
            events.sort(key=lambda e: (e.get("day", 0), e.get("time", "")))
            return events
        return self._chapter_events_by_day_range(tick_data, chapter_num)

    def _chapter_events_by_day_range(self, tick_data: dict,
                                     chapter_num: int) -> list[dict]:
        """兜底：按天范围过滤事件（旧 tick 文件无 scope.events）。"""
        trajectories = tick_data.get("character_trajectories", {})
        all_days = sorted(
            set(ev.get("day", 0) for events in trajectories.values() for ev in events)
        )
        split = tick_data.get("suggested_chapter_split", [])
        n_chapters = len(split) if split else 1

        ch_event_days: set[int] = set()
        if n_chapters > 0 and all_days:
            ch_idx = next(
                (i for i, s in enumerate(split) if s.get("chapter_num") == chapter_num),
                chapter_num - min(s.get("chapter_num", 1) for s in split) if split else 0,
            )
            ch_idx = max(0, min(ch_idx, n_chapters - 1))
            days_per = max(1, len(all_days) // n_chapters)
            start = ch_idx * days_per
            end = (ch_idx + 1) * days_per if ch_idx < n_chapters - 1 else len(all_days)
            ch_event_days = set(all_days[start:end])

        events = []
        for pov, evs in trajectories.items():
            for ev in evs:
                if ch_event_days and ev.get("day", 0) not in ch_event_days:
                    continue
                events.append(ev)
        events.sort(key=lambda e: (e.get("day", 0), e.get("time", "")))
        return events

    def _group_scenes(self, events: list[dict]) -> list[dict]:
        """按 (天, 地点) 聚类事件成场景，标对手戏/隐藏信息。"""
        scene_map: dict[tuple, dict] = {}
        for ev in events:
            key = (ev.get("day", 0), ev.get("location", ""))
            sc = scene_map.setdefault(key, {
                "day": key[0],
                "location": key[1],
                "events": [],
                "interactive": False,
                "has_private": False,
            })
            sc["events"].append(ev)
            if str(ev.get("pov", "")).endswith("_private"):
                sc["has_private"] = True

        scenes = list(scene_map.values())
        scenes.sort(key=lambda s: (s["day"], s["events"][0].get("time", "")))

        for sc in scenes:
            for ev in sc["events"]:
                brief = ev.get("brief", "")
                if any(v in brief for v in PHONE_WORDS):
                    continue
                if any(v in brief for v in MEETING_VERBS):
                    sc["interactive"] = True
                    break
        return scenes


# ── 机械 spec 构建（无 LLM）──────────────────────────────────────────────

def build_spec_mechanical(tick_data: dict, chapter_num: int, novel: str) -> dict:
    """从 tick 引擎输出自动构建章节 spec（确定性，无 LLM）。

    与 LLM-driven SpecBuilder.build 不同：不设计叙事结构，只按天把事件
    分组填进三节。作为 SpecBuilder 失败/不可用时的兜底。
    返回 gen.py 兼容的 spec dict。
    """
    scopes = tick_data.get("suggested_chapter_split", [])
    scope = None
    for s in scopes:
        if s.get("chapter_num") == chapter_num:
            scope = s
            break
    if not scope and scopes:
        scope = scopes[0]
    n_sections = scope.get("sections", 3) if scope else 3

    # Map chapters to day ranges
    all_events = []
    for pov, events in tick_data.get("character_trajectories", {}).items():
        for ev in events:
            all_events.append((ev.get("day", 0), ev.get("time", ""), pov, ev))
    all_events.sort(key=lambda x: (x[0], x[1]))

    total_days = sorted(set(e[0] for e in all_events))
    n = len(scopes) if scopes else 1
    days_per = max(1, len(total_days) // n) if total_days else 1

    ch_indices = [s["chapter_num"] for s in scopes] if scopes else [chapter_num]
    try:
        idx = ch_indices.index(chapter_num)
    except ValueError:
        idx = 0
    start_idx = idx * days_per
    end_idx = (idx + 1) * days_per if idx < n - 1 else len(total_days)
    ch_days = set(total_days[start_idx:end_idx])

    ch_events = [e for e in all_events if e[0] in ch_days]

    # Build sections from events
    sec_ids = ["一", "二", "三", "四", "五", "六"]
    sections = []
    if ch_events:
        events_per = max(1, len(ch_events) // n_sections)
        for i in range(n_sections):
            group = ch_events[i * events_per : (i + 1) * events_per]
            if not group and sections:
                group = ch_events[-events_per:] if ch_events else []

            locs = list(set(e[3].get("location", "?") for e in group)) if group else ["?"]
            loc_str = " / ".join(locs[:3])
            briefs = "\n".join(
                f"- {e[3].get('pov', '?')}: {e[3].get('brief', '')[:60]}"
                for e in group[:5]
            ) if group else "（无具体事件）"

            is_reversal = scope.get("reversal_at_section", 0) == i + 1
            sections.append({
                "id": sec_ids[i],
                "subject": loc_str,
                "description": f"（引擎输出）{loc_str}。{briefs}",
                "tension_direction": scope.get("tension_direction", "") if scope else "",
                "weight": "expanded" if is_reversal else "normal",
            })
            if is_reversal:
                sections[-1]["expanded_direction"] = scope.get("tension_direction",
                     "反转场景，重点展开本章核心事件")
    else:
        for i in range(n_sections):
            sections.append({
                "id": sec_ids[i],
                "subject": f"场景{i+1}",
                "description": f"（引擎无具体事件，请手动补充本章内容）",
                "tension_direction": scope.get("tension_direction", "") if scope else "",
                "weight": "normal",
            })

    return {
        "novel": novel,
        "chapter": chapter_num,
        "title": f"第{chapter_num}章",
        "sections": sections,
        "target_chars": max(2000, n_sections * 800),
        "from_engine": True,
        "tick_arc_id": tick_data.get("arc_id", ""),
    }
