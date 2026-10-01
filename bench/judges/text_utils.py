#!/usr/bin/env python3
"""bench.judges.text_utils — 判定器共用的文本口径（单一事实源）。

对话行口径与 gen.py 一致：以 「 / 直引号 " / 弯引号 “ 开头。
过去时标记：正文提到已死角色/旧日期时的合法豁免词（回忆、梦、遗物…）。
"""

import re

DIALOG_RE = re.compile(r"^\s*[「\"“]")
SENT_SPLIT = re.compile(r"(?<=[。！？])")

# 引文片段（「…」/“…”/"…"）——**任意位置**都要剥离，不能只认行首。
# 真实数据教训：`他说：“这箱子是我的。”` 这类「旁白+对话」同行，行首不是引号，
# 于是对话里的「我」被当旁白第一人称误报。
_QUOTED_SPANS = re.compile(r"「[^」]*」|“[^”]*”|\"[^\"]*\"")

# 相对时间表达：不是绝对天数（`搬盐的人，第二天就走了` 说的是往事）
RELATIVE_DAY = ("第二天", "次日", "隔天", "头天", "头一天", "前一天", "昨天",
                "前天", "当晚", "当天", "翌日", "后天")

# 过去时/非在场标记。**分两类**——这个区分是第二轮外部评审逼出来的：
#
#   硬标记（HARD）：语义上**就是**在指涉过去/传闻/遗物，出现即豁免。
#     「她想起老麦当时蹲在跳板上」——想起 管辖整句，怎么读都不是复活。
#   软标记（SOFT）：时间副词与比喻词。它们**管辖范围可大可小**：
#     「**当时**老麦走过来」  → 当时 管辖「老麦走过来」，是闪回，不是复活
#     「老麦走进来，说起了**当年**的事」→ 当年 只管辖「事」，走动发生在当下，**是**复活
#     旧实现按「句内是否出现」一刀切，于是第二类被整句豁免——
#     入库语料上的正对照把这条量了出来（比喻框 0/36、过去时 0/36，
#     见 bench/calib/corpus_control.py）。软标记的判法改为**看它出现在死者名之前还是之后**。
HARD_PAST_MARKERS = (
    "回忆", "记得", "想起", "梦里", "梦到", "梦窗", "闪回", "坟", "尸体",
    "遗物", "死了", "已死", "死前", "临死", "生前", "听说", "据说", "传说",
    # 相对时间跨度：「三年前」「两个月前」「几天前」。它们把所叙之事**明确定date到过去**，
    # 因此不是软标记——不能按「管辖哪个小句」切。出厂产物里
    # 「她听见老周在门外喊了一嗓子，那声音三年前就被风带走了」正是这一形态：
    # 三年前 在**后一个小句**里，但它把整句都放进了过去。
    "年前", "个月前", "天前", "年前的事",
)
SOFT_PAST_MARKERS = (
    "以前", "当年", "当时", "曾经", "上回", "那次", "像", "仿佛", "好像",
) + RELATIVE_DAY

# 并集：给「句内出现即豁免」的调用方用（目前只有天数判据——
# 那里没有「管辖范围」问题，句内相对时间表达本来就说明这句在说往事）
PAST_MARKERS = HARD_PAST_MARKERS + SOFT_PAST_MARKERS


def is_dialogue(line: str) -> bool:
    return bool(DIALOG_RE.match(line))


def narration(line: str) -> str:
    """剥离引文后的旁白部分（引文里的「我」不算旁白第一人称）。"""
    return _QUOTED_SPANS.sub("", line)


def sentences(line: str) -> list:
    return [s.strip() for s in SENT_SPLIT.split(line) if s.strip()]


def has_past_marker(sentence: str) -> bool:
    """句内是否含任一类过去时/回忆标记（并集口径）。

    **只给「句内出现即豁免」的调用方用。** 死人活动判据不该用这个——
    它要区分标记管辖的是谁，用 `hard_past_marker` + `first_soft_marker`。
    """
    return any(m in sentence for m in PAST_MARKERS)


def hard_past_marker(sentence: str) -> bool:
    """硬标记：出现即豁免（回想/传闻/遗物/死亡词）。"""
    return any(m in sentence for m in HARD_PAST_MARKERS)


def first_soft_marker(sentence: str) -> int:
    """第一个软标记的字符位置；没有则 -1。

    调用方拿它和死者名的位置比：软标记在名字**之前** → 它管辖的是名字那个小句
    （闪回/比喻框），豁免；在名字**之后** → 它管的是别的东西，动作发生在当下，不豁免。
    """
    pos = [sentence.find(m) for m in SOFT_PAST_MARKERS]
    pos = [p for p in pos if p >= 0]
    return min(pos) if pos else -1
