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

# 过去时/非在场标记——命中则不算「复活/矛盾」（机械层豁免，避免误报）
PAST_MARKERS = (
    "回忆", "记得", "想起", "梦里", "梦到", "梦窗", "闪回", "以前", "当年",
    "曾经", "上回", "那次", "坟", "尸体", "遗物", "死了", "已死", "死前",
    "临死", "生前", "听说", "据说", "传说", "像", "仿佛", "好像",
) + RELATIVE_DAY


def is_dialogue(line: str) -> bool:
    return bool(DIALOG_RE.match(line))


def narration(line: str) -> str:
    """剥离引文后的旁白部分（引文里的「我」不算旁白第一人称）。"""
    return _QUOTED_SPANS.sub("", line)


def sentences(line: str) -> list:
    return [s.strip() for s in SENT_SPLIT.split(line) if s.strip()]


def has_past_marker(sentence: str) -> bool:
    return any(m in sentence for m in PAST_MARKERS)