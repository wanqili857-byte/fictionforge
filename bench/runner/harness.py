#!/usr/bin/env python3
"""bench.runner.harness — 三档 harness（BENCH_PLAN §三），纯函数、零 LLM。

  bare  最小 system + 本章 spec                    → 模型裸能力
  mid   + 世界设定 + 前文 + POV 已知事实（知识边界注入）→ 上下文工程的贡献
  full  = mid 的 prompt + 确定性后处理门禁（删禁词 / 切超长段）→ 门禁的贡献

**知识边界纪律**：mid/full 注入的「已知事实」严格限定为该 POV 角色在**本章及之前**
已学到的事实；终局真相在其学习章之前绝不注入，false 行（错误认识）永不注入。
harness 自己不许泄底——否则测不出模型的边界能力。

prompt 本身与「门禁后处理」分离：三档的 prompt 只有 bare/mid 两种，
full 复用 mid 的 prompt，差异在后处理（跑批时同时记录修前/修后违反率）。
"""

import re
from typing import Optional

TIERS = ("bare", "mid", "full")

SYSTEM_BARE = "你是小说写作者。"
SYSTEM_MID = ("你是小说写作者。保持与既有设定、前文内容一致；"
              "角色的言行必须是其此刻已经知道的信息所能支撑的。")

# 输出格式要求：三档**完全一致**（否则档位对比不公平）。首次真实跑批发现模型会
# 输出 markdown 标题（"## 第1章"/"### 一、…"），污染字数统计与段落判据。
OUTPUT_RULES = ("\n## 输出要求\n"
                "只写正文。不要章节标题、不要小节标题、不要任何标记符号"
                "（#、*、---）。短段落，1-2 句换行。"
                "承接上文，写到本章结束为止，不要写本章之后的内容。")

_PARA_SPLIT = re.compile(r"(?<=[。！？])")


def world_facts(u, chapter: int = 1) -> list:
    """「客观设定」段落只注入**常识型**事实（common=True）且已到 reveal 章。

    发现型事实（主角要一章一章学到的）**不走这里**——它们经 `known_facts()` 按
    知识边界注入：主角学到的那一刻起才出现。两类混在一起就会泄底：
    读者/模型提前知道主角还没查到的事，边界违反就测不出来了。
    """
    return [f["statement"] for f in u.truth_table
            if not f["is_false"] and f.get("common")
            and f.get("reveal_chapter", 1) <= chapter]


def known_facts(u, character: str, chapter: int) -> list:
    """该角色在本章及之前已学到的事实命题（知识边界的内侧）。"""
    by_id = {f["id"]: f for f in u.truth_table}
    out = []
    for entry in u.knowledge.get(character, []):
        if entry["learned_chapter"] <= chapter:
            f = by_id.get(entry["fact_id"])
            if f and not f["is_false"]:
                out.append(f["statement"])
    return out


def chapter_spec_text(spec: dict) -> str:
    lines = [f"## {spec['title']}", ""]
    if spec.get("mood"):
        lines += [f"情绪线：{spec['mood']}", ""]
    for sec in spec.get("sections", []):
        lines.append(f"### {sec['id']}、{sec['subject']}")
        if sec.get("scene_anchor"):
            lines.append(f"场景：{sec['scene_anchor']}")
        lines.append(sec.get("description", ""))
        if sec.get("tension_direction"):
            lines.append(f"张力方向：{sec['tension_direction']}")
        lines.append("")
    lines.append(f"总篇幅：约 {spec.get('target_chars', 1800)} 字。短段落，1-2 句换行。")
    return "\n".join(lines)


def build_prompt(tier: str, u, spec: dict, prior_text: Optional[str] = None,
                 prior_tail: int = 1200):
    """返回 (system, user)。tier ∈ TIERS。full 与 mid 的 prompt 相同（差异在后处理）。"""
    if tier not in TIERS:
        raise ValueError(f"未知 harness 档位: {tier}（可选 {TIERS}）")

    if tier == "bare":
        return SYSTEM_BARE, chapter_spec_text(spec) + OUTPUT_RULES

    parts = []
    chapter = spec.get("chapter", 1)
    facts = world_facts(u, chapter)
    if facts:
        parts.append("## 世界设定（客观事实）")
        parts += [f"- {s}" for s in facts]
        parts.append("")

    known = known_facts(u, u.protagonist, chapter)
    if known:
        parts.append(f"## {u.protagonist}此刻已知（不得越出此范围，也不得凭空得知）")
        parts += [f"- {s}" for s in known]
        parts.append("")

    if prior_text:
        tail = prior_text[-prior_tail:]
        parts.append("## 前文末尾（紧接下文继续，勿重复）")
        parts.append(tail)
        parts.append("")

    parts.append(chapter_spec_text(spec))
    parts.append(OUTPUT_RULES)
    return SYSTEM_MID, "\n".join(parts)


# ── full 档的后处理门禁（确定性，可单测）──────────────────────────────

def _split_long_paragraph(para: str, para_max: int) -> list:
    if len(para) <= para_max:
        return [para]
    out, cur = [], ""
    for sent in _PARA_SPLIT.split(para):
        if not sent:
            continue
        # 单句本身就超长（无标点长串）→ 硬切，否则永远切不开
        while len(sent) > para_max:
            if cur:
                out.append(cur)
                cur = ""
            out.append(sent[:para_max])
            sent = sent[para_max:]
        if cur and len(cur) + len(sent) > para_max:
            out.append(cur)
            cur = sent
        else:
            cur += sent
    if cur:
        out.append(cur)
    return out


def apply_gate_fix(text: str, forbidden_words=(), para_max: int = 100) -> str:
    """确定性后处理：删禁词 / 切超长段落。无 LLM 调用。

    删词是粗暴但确定的做法（不做同义替换，避免引入新的风格偏差）——
    基准要的是「门禁擦掉了多少违反」这个可量化的量，不是文笔。
    """
    out = text
    for w in forbidden_words:
        if w:
            out = out.replace(w, "")
    paras = []
    for para in out.split("\n"):
        if len(para) > para_max:
            paras.extend(_split_long_paragraph(para, para_max))
        else:
            paras.append(para)
    return "\n".join(paras)