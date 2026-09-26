#!/usr/bin/env python3
"""bench.state.truth_table — 真相表解析（确定性，零 LLM）。

真相表是作者维护的权威事实表（bible/真相表.md），markdown 表格：
    | id | 类别 | 命题 | [false 标记，可选] |
第 4 列为 false 标记（false / 假 / 错）时，该命题是「广泛流传但错误的认识」——
B 型认知反转素材，**不得进角色的「已知集合」**（W4 探针生成依赖此语义）。
"""

import re
from dataclasses import dataclass
from pathlib import Path

_FALSE_MARKERS = {"false", "假", "错", "错误", "否"}
_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_SEP_RE = re.compile(r"^\s*\|[\s:\-|]+\|\s*$")


@dataclass
class TruthFact:
    id: str
    category: str
    statement: str
    is_false: bool = False


def load_truth_table(path) -> list:
    """解析真相表 markdown 表格 → TruthFact[]。文件不存在/无表 → []。"""
    p = Path(path)
    if not p.exists():
        return []
    facts = []
    for line in p.read_text(encoding="utf-8").split("\n"):
        if _SEP_RE.match(line):
            continue
        m = _ROW_RE.match(line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if len(cells) < 3:
            continue                      # 畸形行容错
        fid, category, statement = cells[0], cells[1], cells[2]
        if not fid or fid.lower() == "id":
            continue                      # 表头
        is_false = len(cells) >= 4 and cells[3].lower() in _FALSE_MARKERS
        facts.append(TruthFact(id=fid, category=category,
                               statement=statement, is_false=is_false))
    return facts