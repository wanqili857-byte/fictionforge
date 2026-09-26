#!/usr/bin/env python3
"""bench.judges.state_judge — W3 机械状态判定（确定性，零 LLM）。

输入正文 + StateLedger，输出 Violation[]（type=state）。四检查：
  1. dead_resurrection   已死角色在后续章正文中正常活动（过去时/对话豁免）
  2. timeline_regression 账本时间线倒退（W2 timeline_violations 转 Violation）
  3. day_contradiction   正文提到的天早于本章锚点天（过去时/对话豁免）
  4. item_conflict       同一物品声明给两个持有者且无 transfer 注记

启发式项 confidence < 1.0（机械层无法确证「活动中」vs「被提及」的边界），
证据带 span 供人工复核——Kappa 校准在 W5 统一做。
"""

import re

from bench.contracts import Violation, ViolationType, DetectorKind, Severity
from bench.judges.text_utils import is_dialogue, sentences, has_past_marker

_DAY_RE = re.compile(r"第\s*([0-9]+|[一二三四五六七八九十]+)\s*天")
_VIOL_CH_RE = re.compile(r"第(\d+)章")

# 死者活动判据 v2 —— 用正赛真实 FP 校准（db-lite 一章 12 条全是遗物/回忆性指称）：
# 「老麦的名字写在册子上」「是老麦的字」「老麦的日志」「老麦当时蹲在这」——
# **死后点名 ≠ 死人复活**。判据改正向证据：
#   豁免：过去时标记 / 遗物回忆语境（名册·字迹·日志·写/刻/记…）/ 领格「老麦的X」
#   命中：死者名后 6 字窗内出现**活动动词**（复活 = 继续行动，不是被提及）
_MEMORIAL_RE = re.compile(
    r"名册|名[字单]|流水册|日志|日记|字迹|笔迹|遗[物言迹]|照片|画像|碑|"
    r"写下|写着|刻着|记着|印着|画着|想起|记得|回忆|生前|尸体|坟")
_ACTIVITY_VERBS = ("走", "跑", "站", "坐", "蹲", "躺", "靠", "拿", "抓", "攥",
                   "握", "推", "拉", "举", "抬", "冲", "退", "追", "递", "塞",
                   "伸手", "开口", "说话", "喊", "笑", "哭", "点头", "摇头",
                   "转身", "回头", "出现", "停下", "盯着", "看着", "问")

_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_to_int(s: str):
    """中文数字 → int（支持 一~九十九；不足则 None）。"""
    if s.isdigit():
        return int(s)
    if "十" not in s:
        return _CN_DIGITS.get(s)
    head, _, tail = s.partition("十")
    tens = _CN_DIGITS.get(head, 1) if head else 1
    ones = _CN_DIGITS.get(tail, 0) if tail else 0
    if tail and tail not in _CN_DIGITS:
        return None
    return tens * 10 + ones


def state_judge(text: str, ledger, chapter: int, run_id: str,
                cast_names=None) -> list:
    """返回 Violation[]（type=state, detector=mechanical）。确定性顺序。"""
    out = []

    def V(pid, sev, evidence, confidence=1.0, note=""):
        return Violation(
            probe_id=f"{pid}-ch{chapter}",
            type=ViolationType.STATE,
            detector=DetectorKind.MECHANICAL,
            chapter=chapter,
            severity=sev,
            evidence=evidence,
            run_id=run_id,
            confidence=confidence,
            note=note,
        )

    # 1. 死人复活：本章之前已声明死亡的角色，在本章正文里「正常活动」
    dead = set()
    for c in ledger.chapters:
        if c.chapter < chapter:
            dead.update(c.deaths)
    if dead and text:
        for line in text.split("\n"):
            if is_dialogue(line):
                continue
            for sent in sentences(line):
                if has_past_marker(sent) or _MEMORIAL_RE.search(sent):
                    continue
                for name in sorted(dead):
                    idx = sent.find(name)
                    if idx < 0:
                        continue
                    pre = sent[max(0, idx - 4):idx]      # 显形动词可在名字前：出现老周
                    tail = sent[idx + len(name): idx + len(name) + 6]
                    if tail.startswith("的") and "身影" not in tail and "出现" not in pre:
                        continue                          # 领格：老麦的X
                    if "出现" in pre or any(v in tail for v in _ACTIVITY_VERBS):
                        out.append(V("state-dead", Severity.HIGH,
                                     {"span": sent[:50], "character": name},
                                     confidence=0.7,
                                     note="死者名后出现活动动词"
                                          "（遗物/回忆语境已豁免）"))

    # 2. 时间倒流：账本自身的时间线违规（只报本章及之前）
    for vtext in ledger.timeline_violations():
        m = _VIOL_CH_RE.search(vtext)
        if m and int(m.group(1)) <= chapter:
            out.append(V("state-timeline", Severity.HIGH,
                         {"span": vtext}, confidence=1.0,
                         note="spec 锚点时间线倒退"))

    # 3. 日期矛盾：正文提到的天早于本章锚点天
    cur = ledger.chapter(chapter)
    anchor_day = cur.day if cur else None
    if anchor_day is not None and text:
        for line in text.split("\n"):
            if is_dialogue(line):
                continue
            for sent in sentences(line):
                if has_past_marker(sent):
                    continue
                for m in _DAY_RE.finditer(sent):
                    d = _cn_to_int(m.group(1))
                    if d is not None and d < anchor_day:
                        out.append(V("state-day", Severity.MEDIUM,
                                     {"span": sent[:50], "mentioned_day": d,
                                      "anchor_day": anchor_day},
                                     confidence=0.8,
                                     note="正文天数早于本章锚点天"))

    # 4. 物品双持有：同物品先后声明给不同持有者，且该章无 transfer 注记
    holders = {}
    for c in sorted(ledger.chapters, key=lambda x: x.chapter):
        for item, holder in c.possessions.items():
            holders.setdefault(item, []).append((c.chapter, holder, c))
    for item, seq in sorted(holders.items()):
        prev_holder = None
        for ch_no, holder, cobj in seq:
            if prev_holder is not None and holder != prev_holder:
                if item not in (cobj.transfers or []) and ch_no <= chapter:
                    out.append(V("state-item", Severity.HIGH,
                                 {"span": f"{item}: {prev_holder} → {holder}",
                                  "item": item, "from": prev_holder,
                                  "to": holder, "chapter": ch_no},
                                 confidence=0.8,
                                 note="持有者变更无 transfer 注记"))
            prev_holder = holder

    return out