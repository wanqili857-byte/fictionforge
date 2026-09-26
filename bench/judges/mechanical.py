#!/usr/bin/env python3
"""bench.judges.mechanical — W1 机械判定器（确定性，零 LLM / 零网络）。

检查项（全部可精确单测，docs/BENCH_PLAN.md W1）：
  1. forbidden_word     禁词，逐词计数，fatal
  2. overlength_para    段落过长（非对话行），low
  3. target_length      篇幅 ±20%，medium
  4. pov_first_person   旁白第一人称（对话豁免，仅 third_limited），high
  5. pronoun_switch     人称混用：主角名同句 + 反性别人称 + 句内无同性别角色名，high

规则语义：MechanicalRules() 全空 = 全部检查关闭（默认中性）。
对话行口径与 gen.py 一致：以 「 或 引号 开头。
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from bench.contracts import Violation, ViolationType, DetectorKind, Severity

_DIALOG_RE = re.compile(r"^\s*[「\"“]")   # 直角引号 / 直引号 / 弯引号 “
_SENT_SPLIT = re.compile(r"(?<=[。！？])")
_FIRST_PERSON_RE = re.compile(r"我们|我")   # 我们优先，避免重复报


@dataclass
class MechanicalRules:
    """判定规则，全部来自 spec / novel_config。缺省 = 该项检查关闭。"""
    forbidden_words: list = field(default_factory=list)
    target_chars: int = 0               # 0 = 跳过篇幅检查
    para_max_chars: int = 0             # 0 = 跳过段落检查
    pov: str = ""                       # 仅 "third_limited" 启用第一人称检查
    protagonist: str = ""
    protagonist_pronoun: str = ""       # "她"/"他"；空 = 跳过人称检查
    cast_genders: dict = field(default_factory=dict)  # {name: "f"|"m"}


def _opposite(pronoun: str) -> str:
    if pronoun == "她":
        return "他"
    if pronoun == "他":
        return "她"
    return ""


def mechanical_judge(text: str, rules: MechanicalRules, run_id: str,
                     chapter: int) -> list:
    """返回 Violation[]（type=constraint, detector=mechanical）。确定性顺序。"""
    if not text or not text.strip():
        return []
    out = []

    def V(pid, sev, evidence, note=""):
        return Violation(
            probe_id=f"{pid}-ch{chapter}",
            type=ViolationType.CONSTRAINT,
            detector=DetectorKind.MECHANICAL,
            chapter=chapter,
            severity=sev,
            evidence=evidence,
            run_id=run_id,
            note=note,
        )

    # 1. 禁词（逐词，全文含对话）
    for w in rules.forbidden_words:
        if not w:
            continue
        count = text.count(w)
        if count:
            first = text.find(w)
            span = text[max(0, first - 10): first + len(w) + 10]
            out.append(V("cons-forbidden", Severity.FATAL,
                         {"word": w, "count": count, "first_span": span}))

    lines = text.split("\n")

    # 2. 段落过长（非对话行）
    if rules.para_max_chars > 0:
        for line in lines:
            stripped = line.strip()
            if not stripped or _DIALOG_RE.match(stripped):
                continue
            if len(stripped) > rules.para_max_chars:
                out.append(V("cons-para", Severity.LOW,
                             {"count": 1, "span": stripped[:30],
                              "len": len(stripped)}))

    # 3. 篇幅 ±20%
    if rules.target_chars > 0:
        n = len(text.strip())
        lower, upper = rules.target_chars * 0.8, rules.target_chars * 1.2
        if n < lower or n > upper:
            out.append(V("cons-length", Severity.MEDIUM,
                         {"actual": n, "target": rules.target_chars,
                          "span": f"{int(lower)}-{int(upper)}"}))

    # 4. POV 第一人称（旁白）+ 5. 人称混用（旁白逐句）
    opp = _opposite(rules.protagonist_pronoun)
    same_gender_names = []
    if opp and rules.cast_genders:
        want = "m" if opp == "他" else "f"
        same_gender_names = [n for n, g in rules.cast_genders.items()
                             if g == want and n != rules.protagonist]
    for line in lines:
        stripped = line.strip()
        if not stripped or _DIALOG_RE.match(stripped):
            continue

        if rules.pov == "third_limited":
            for m in _FIRST_PERSON_RE.finditer(stripped):
                out.append(V("cons-pov", Severity.HIGH,
                             {"word": m.group(0),
                              "span": stripped[max(0, m.start() - 8): m.end() + 8]}))

        if opp and same_gender_names:
            for sent in _SENT_SPLIT.split(stripped):
                sent = sent.strip()
                if (rules.protagonist and rules.protagonist in sent
                        and opp in sent
                        and not any(n in sent for n in same_gender_names)):
                    out.append(V("cons-pronoun", Severity.HIGH,
                                 {"word": opp,
                                  "span": sent[:40]}))

    return out
