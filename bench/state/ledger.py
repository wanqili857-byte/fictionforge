#!/usr/bin/env python3
"""bench.state.ledger — W2 状态账本（确定性构建，零 LLM）。

来源（BENCH_PLAN W2：不解析 章节状态.md 散文）：
- **派生**：spec 各节 scene_anchor → 天 / 时段 / 地点（`第N天[时段] @地点[→地点2]`）
- **派生**：cast 基名在 spec 描述中出现 → 本章在场角色（最长名优先掩码，防同名重复计）
- **声明**：spec 可选 `state_delta` → 死亡 / 持有物（作者声明，确定性应用）

提供：时间线单调性检查、alive_at / holder_of / locations_at 查询、可读 diff、JSON 往返。
"""

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# 时段序（同日先后比较用）；未列的时段视为未知，跳过比较
SLOT_ORDER = ["凌晨", "清晨", "上午", "中午", "下午", "黄昏", "傍晚", "夜里", "夜", "深夜"]

_ANCHOR_RE = re.compile(
    r"第\s*(\d+)\s*天\s*(凌晨|清晨|上午|中午|下午|黄昏|傍晚|夜里|夜|深夜)?\s*@\s*(.+)"
)
_PAREN_RE = re.compile(r"[（(].*?[)）]")


def base_name(name: str) -> str:
    """匹配用基名：去掉括号限定（"明处朋友（名待定）" → "明处朋友"）。"""
    return _PAREN_RE.sub("", name).strip()


def cast_base_names(novel_config: dict) -> list[str]:
    """从 novel_config 取声明名列表（显示用，保留括号限定）。

    兼容两种 cast 形态：[{"name": …}, …] 或 ["名字", …]。
    """
    out = []
    for entry in (novel_config or {}).get("cast", []) or []:
        if isinstance(entry, dict):
            nm = entry.get("name")
        else:
            nm = entry
        if nm:
            out.append(nm)
    return out


def parse_anchor(anchor: str):
    """(day, slot, [地点…])；畸形/缺失 → (None, None, [])。"""
    if not anchor:
        return (None, None, [])
    m = _ANCHOR_RE.search(anchor)
    if not m:
        return (None, None, [])
    day = int(m.group(1))
    slot = m.group(2)
    locs = [x.strip() for x in re.split(r"→|->", m.group(3)) if x.strip()]
    return (day, slot, locs)


def _slot_index(slot: Optional[str]) -> Optional[int]:
    if slot in SLOT_ORDER:
        return SLOT_ORDER.index(slot)
    return None


@dataclass
class ChapterState:
    chapter: int
    day: Optional[int] = None
    slots: list = field(default_factory=list)
    locations: list = field(default_factory=list)
    characters: list = field(default_factory=list)
    deaths: list = field(default_factory=list)
    possessions: dict = field(default_factory=dict)
    notes: str = ""


@dataclass
class StateLedger:
    novel: str
    chapters: list = field(default_factory=list)   # ChapterState[]

    # ── 查询 ──
    def chapter(self, n: int) -> Optional[ChapterState]:
        for c in self.chapters:
            if c.chapter == n:
                return c
        return None

    def _upto(self, n: int):
        return [c for c in self.chapters if c.chapter <= n]

    def alive_at(self, n: int) -> list:
        persons = set()
        for c in self.chapters:
            persons.update(c.characters)
            persons.update(c.possessions.values())
            persons.update(c.deaths)
        dead = set()
        for c in self._upto(n):
            dead.update(c.deaths)
        return sorted(persons - dead)

    def holder_of(self, item: str, n: int) -> Optional[str]:
        holder = None
        for c in sorted(self._upto(n), key=lambda x: x.chapter):
            if item in c.possessions:
                holder = c.possessions[item]
        return holder

    def locations_at(self, n: int) -> list:
        c = self.chapter(n)
        return list(c.locations) if c else []

    # ── 时间线单调性 ──
    def timeline_violations(self) -> list:
        out = []
        prev = None
        for c in sorted(self.chapters, key=lambda x: x.chapter):
            cur = (c.day, min((_slot_index(s) for s in c.slots
                               if _slot_index(s) is not None), default=None))
            if prev is not None and c.day is not None and prev[0] is not None:
                if c.day < prev[0]:
                    out.append(f"第{c.chapter}章 (第{c.day}天) 早于上一章 (第{prev[0]}天)——时间线倒退")
                elif (c.day == prev[0] and cur[1] is not None and prev[1] is not None
                      and cur[1] < prev[1]):
                    out.append(f"第{c.chapter}章 (第{c.day}天 {c.slots[0]}) 早于上一章"
                               f"同日时段——时间线倒退")
            prev = cur if c.day is not None else prev
        return out

    # ── diff（可读）──
    def diff(self, other: "StateLedger") -> list:
        out = []
        mine = {c.chapter: c for c in self.chapters}
        theirs = {c.chapter: c for c in other.chapters}
        for ch in sorted(set(mine) | set(theirs)):
            a, b = mine.get(ch), theirs.get(ch)
            if a is None:
                out.append(f"第{ch}章 仅对方账本有")
                continue
            if b is None:
                out.append(f"第{ch}章 仅本账本有")
                continue
            if a.day != b.day:
                out.append(f"第{ch}章 天不同：{a.day} vs {b.day}")
            if a.slots != b.slots:
                out.append(f"第{ch}章 时段不同：{a.slots} vs {b.slots}")
            if a.locations != b.locations:
                out.append(f"第{ch}章 地点不同：{a.locations} vs {b.locations}")
            if a.characters != b.characters:
                out.append(f"第{ch}章 角色不同：{a.characters} vs {b.characters}")
            if a.deaths != b.deaths:
                out.append(f"第{ch}章 死亡不同：{a.deaths} vs {b.deaths}")
            if a.possessions != b.possessions:
                out.append(f"第{ch}章 持有物不同：{a.possessions} vs {b.possessions}")
        return out

    # ── 序列化 ──
    def to_dict(self) -> dict:
        return {"novel": self.novel,
                "chapters": [asdict(c) for c in self.chapters]}

    @classmethod
    def from_dict(cls, d: dict) -> "StateLedger":
        return cls(novel=d.get("novel", ""),
                   chapters=[ChapterState(**c) for c in d.get("chapters", [])])


# ── 构建 ──────────────────────────────────────────────────────────────

def _characters_in(text: str, cast_names: list) -> list:
    """最长基名优先掩码匹配，避免 "林汐" 在 "林汐的朋友" 中被重复计。"""
    masked = text
    found = []
    for name in sorted(cast_names, key=lambda n: -len(base_name(n))):
        bn = base_name(name)
        if bn and bn in masked:
            found.append(name)
            masked = masked.replace(bn, "\u0000" * len(bn))
    # 保持 cast 声明顺序
    return [n for n in cast_names if n in found]


def build_ledger(novel: str, specs: list, cast_names: list,
                 protagonist: Optional[str] = None) -> StateLedger:
    """从 spec 列表构建账本。specs 可为 dict 列表或已加载 JSON。

    protagonist：单 POV 小说里主角每章在场，但 spec 描述常只用「她」不写名字——
    派生漏检时回填主角（有场景锚点即视为在场）。
    """
    chapters = []
    for sp in sorted(specs, key=lambda s: s.get("chapter", 0)):
        ch = sp.get("chapter", 0)
        days, slots, locs, chars = [], [], [], []
        has_scene = False
        for sec in sp.get("sections", []):
            day, slot, ls = parse_anchor(sec.get("scene_anchor", ""))
            if day is not None:
                days.append(day)
            if slot:
                slots.append(slot)
            if day is not None or slot or ls:
                has_scene = True
            for l in ls:
                if l not in locs:
                    locs.append(l)
            for nm in _characters_in(sec.get("description", "") or "", cast_names):
                if nm not in chars:
                    chars.append(nm)
        if protagonist and has_scene and protagonist not in chars:
            chars = [protagonist] + chars
        delta = sp.get("state_delta") or {}
        chapters.append(ChapterState(
            chapter=ch,
            day=days[0] if days else None,
            slots=slots,
            locations=locs,
            characters=chars,
            deaths=list(delta.get("deaths", [])),
            possessions=dict(delta.get("items", {})),
            notes=delta.get("notes", ""),
        ))
    return StateLedger(novel=novel, chapters=chapters)


def load_specs(novel_dir) -> list:
    """读 novel_dir/specs/ch*.json（按章号排序）。"""
    d = Path(novel_dir) / "specs"
    out = []
    for p in sorted(d.glob("ch*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return sorted(out, key=lambda s: s.get("chapter", 0))