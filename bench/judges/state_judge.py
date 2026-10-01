#!/usr/bin/env python3
"""bench.judges.state_judge — W3 机械状态判定（确定性，零 LLM）。

输入正文 + StateLedger，输出 Violation[]（type=state）。**只判正文**，两检查：

  1. dead_resurrection   已死角色在后续章正文中正常活动（过去时/对话豁免）
  2. day_contradiction   正文提到的天早于本章锚点天（过去时/对话豁免）

**曾经还有两条——`timeline_regression`（账本时间线倒退）与 `item_conflict`
（物品双持有无 transfer 注记）——已移出本模块**（第二轮外部评审 kimi F6）。
理由：那两条**不读正文**，只读 spec 推出的账本，因此对任何模型输出都是常量，
在 9 个官方 run 里恒定不作为；更糟的是 spec 写错会被记成**模型**违规。
它们是宇宙自检，归 `bench/universe/generator.py` 的 `invariants()`
（时间线单调、物品持有变更），那里才是它们能被证伪的地方。

启发式项 confidence < 1.0（机械层无法确证「活动中」vs「被提及」的边界），
证据带 span 供人工复核——Kappa 校准在 W5 统一做。

**已知边界（不修，写明）**：
1. 相对天数只豁免「第二天」这一惯用式（`RELATIVE_DAY`），「第三天」「第四天」按
   绝对天数比对锚点。这是刻意的：中文叙事里「第三天」通常就是故事第 3 天，
   全豁免会把天数倒退检测削掉；「又过了两天，第三天…」这类真相对表达会误报
   一条 conf 0.8、带 span 的候选，交人工复核。
2. 比喻/传闻标记（像/仿佛/听说）会整句豁免死人活动判据——「老周走过来，
   **像**往常一样把水递给她」这类真复活会漏报。试过收窄（把这些标记从豁免
   集合里去掉），实测代价更大：db-lite mid 一章就新增 4 条误报
   （「**就像**三年前站在跳板中间的老麦」「老麦**走过**的路」「老麦**走**的那年」
   ——全是比喻框与定语从句），真阳性 0 条。**误报 4 : 真阳性 0，收窄不划算**，
   故保留宽豁免，把漏报形态记在这里。这就是 Kappa 校准（W5）要量的东西。
"""

import re

from bench.contracts import Violation, ViolationType, DetectorKind, Severity
from bench.judges.text_utils import (narration, sentences, has_past_marker,
                                     hard_past_marker, first_soft_marker)

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
# 名字**前面**的谓语只有「存现/位移」类才算死者在行动（「门口站着老周」
# 「出现老周的身影」）。感知动词不算：「三年前看着老麦被雾吞掉」里老麦是
# 被看的宾语，不是行动者（出厂产物里的另一条误报）。
_PRE_AGENT_RE = re.compile(r"(?:站|坐|蹲|躺|靠|趴)着|出现|走来|跑过来|走进")
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
            # 剥引文而非「行首引号就整行跳过」：行内引号之后的旁白也要判
            # （`“这是我的。”我蹲进凹陷。` 曾整行漏判）
            narr = narration(line)
            if not narr.strip():
                continue
            for sent in sentences(narr):
                if hard_past_marker(sent) or _MEMORIAL_RE.search(sent):
                    continue
                soft_at = first_soft_marker(sent)
                for name in sorted(dead):
                    idx = sent.find(name)
                    if idx < 0:
                        continue
                    # 软标记（时间副词/比喻词）豁免的是**它管辖的那个动作**，不是整句。
                    # 判法：比较软标记位置与**动作动词**位置——
                    #   标记在动作之前 → 它限定这个动作 → 闪回/比喻，豁免
                    #     「**当时**老麦走过来」/「老麦**当年**也走到过这一步」/「就像三年前
                    #     站在跳板中间的**老麦**那样」
                    #   标记在动作之后 → 它管的是别的东西，动作发生在当下 → 不豁免
                    #     「老麦走进来，说起了**当年**的事」/「老麦走进来，**像**往常一样…」
                    # 只比「标记 vs 名字」是不够的：上面三个豁免例里有两个的标记在名字**之后**
                    # （老麦当年…／老麦当时蹲在这），那两条会变成真实语料上的误报
                    # （入库语料实测 3 条，见 bench/calib/corpus_control.py 的 clean_hits）。
                    if 0 <= soft_at < idx:
                        continue
                    pre = sent[max(0, idx - 4):idx]      # 显形动词可在名字前：出现老周
                    tail = sent[idx + len(name): idx + len(name) + 8]
                    # 先截到小句边界：否则窗口会跨进下一小句，把别人的动作算到死者头上
                    # （「老周走过的路，柳娘走过的路」里第二个「走」是柳娘的）。
                    # 顿号不算边界——「老周、柳娘走进来」是真复活，动词属于整个并列主语。
                    _cut = re.search(r"[，。；！？：]", tail)
                    if _cut:
                        tail = tail[:_cut.start()]
                    # 定语从句的**中心语**：名字前是「的」——「站在跳板中间的老麦」
                    if pre.endswith("的"):
                        continue
                    if tail.startswith("的") and "身影" not in tail and "出现" not in pre:
                        continue                          # 领格：老麦的X
                    # 关系从句：动词后面紧跟「的」或「<补语>的」，说明这个动词在修饰
                    # 名词而不是在叙述动作——「老麦走过的路」「老麦蹲着的那块跳板」
                    # 「老麦没走完的路」。出厂产物里 4 条 state-dead 全是这一形态
                    # （评审 F2 揪出）；`着+的`（第二轮 doubao）与 `<补语>的`
                    # （本轮入库语料对照实测）是同一类的漏网形态。
                    hit = False
                    for m in re.finditer("|".join(re.escape(v) for v in _ACTIVITY_VERBS), tail):
                        vpos = idx + len(name) + m.start()
                        if 0 <= soft_at < vpos:
                            continue                      # 软标记管辖这个动作 → 闪回/比喻
                        if re.match(r"(?:[着过了]|[进出到完起开回上下得]{1,2})?的",
                                    tail[m.end():]):
                            continue                      # 关系从句 → 不作数
                        hit = True
                        break
                    # 动作也可以落在名字**前面**的谓语上（「门口站着老周」「出现老周的身影」）。
                    # 这一条必须放在从句豁免之后：「看着老周走过的路」里 看着 是活的，
                    # 但死者仍是关系从句的中心语，不该报。
                    pre_m = _PRE_AGENT_RE.search(pre)
                    if not hit and pre_m:
                        ppos = max(0, idx - 4) + pre_m.start()
                        if not (0 <= soft_at < ppos):
                            hit = True
                    if hit:
                        out.append(V("state-dead", Severity.HIGH,
                                     {"span": sent[:50], "character": name},
                                     confidence=0.7,
                                     note="死者名后出现活动动词"
                                          "（遗物/回忆/关系从句已豁免）"))

    # 3. 日期矛盾：正文提到的天早于本章锚点天
    cur = ledger.chapter(chapter)
    anchor_day = cur.day if cur else None
    if anchor_day is not None and text:
        for line in text.split("\n"):
            # 剥引文而非「行首引号就整行跳过」：行内引号之后的旁白也要判
            # （`“这是我的。”我蹲进凹陷。` 曾整行漏判）
            narr = narration(line)
            if not narr.strip():
                continue
            for sent in sentences(narr):
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

    return out