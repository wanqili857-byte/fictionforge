#!/usr/bin/env python3
"""bench.judges.text_utils — 判定器共用的文本口径（单一事实源）。

对话行口径与 gen.py 一致：以 「 / 直引号 " / 弯引号 “ 开头。
过去时标记：正文提到已死角色/旧日期时的合法豁免词（回忆、梦、遗物…）。
"""

import re

DIALOG_RE = re.compile(r"^\s*[「\"“]")
SENT_SPLIT = re.compile(r"(?<=[。！？])")

# 过去时/非在场标记——命中则不算「复活/矛盾」（机械层豁免，避免误报）
PAST_MARKERS = (
    "回忆", "记得", "想起", "梦里", "梦到", "梦窗", "闪回", "以前", "当年",
    "曾经", "上回", "那次", "坟", "尸体", "遗物", "死了", "已死", "死前",
    "临死", "生前", "听说", "据说", "传说", "像", "仿佛", "好像",
)


def is_dialogue(line: str) -> bool:
    return bool(DIALOG_RE.match(line))


def sentences(line: str) -> list:
    return [s.strip() for s in SENT_SPLIT.split(line) if s.strip()]


def has_past_marker(sentence: str) -> bool:
    return any(m in sentence for m in PAST_MARKERS)