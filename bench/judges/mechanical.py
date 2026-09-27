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
from bench.judges.text_utils import narration, sentences

_DIALOG_RE = re.compile(r"^\s*[「\"“]")   # 直角引号 / 直引号 / 弯引号 “
_SENT_SPLIT = re.compile(r"(?<=[。！？])")
# 句首第一人称（真违规形状）；句中「我」多为无引号引语，见下方判定注释
_FIRST_PERSON_HEAD = re.compile(r"^(我们|我)")


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

    def V(pid, sev, evidence, note="", confidence=1.0):
        return Violation(
            probe_id=f"{pid}-ch{chapter}",
            type=ViolationType.CONSTRAINT,
            detector=DetectorKind.MECHANICAL,
            chapter=chapter,
            severity=sev,
            evidence=evidence,
            run_id=run_id,
            confidence=confidence,
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

    # 4. POV 第一人称（旁白）+ 5. 人称混用
    # 真实数据教训（首轮跑批抽查）：
    #  - 引文要**任意位置**剥离，不能只认行首（`他说：“这是我的。”` 同行混排）
    #  - 逐句「主角名+反性别人称+句内无同性别角色名」判据误报率极高
    #    （`苏茜转身看他` 里的他是场上另一个男性角色，名字未必出现）
    #    改为：紧邻判据（无歧义）+ 章节级缺位判据（真 bug 形状：主角人称从不用）
    narr_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or _DIALOG_RE.match(stripped):
            continue
        narr = narration(stripped)
        if not narr.strip():
            continue
        narr_lines.append(narr)
        if rules.pov == "third_limited":
            # 只认**句首**第一人称：真违规的形状是「我蹲进凹陷。」
            # 而句中的「我」多为无引号直接引语（中文小说正当手法）：
            #   `苏茜说我没听说有这回事。` / `抬头说，那你去跟柳娘说，这批箱我查完了再放。`
            # 首轮跑批实测：句中「我」全部是引语，句首判据零误报。
            for sent in sentences(narr):
                m = _FIRST_PERSON_HEAD.match(sent)
                if m:
                    out.append(V("cons-pov", Severity.HIGH,
                                 {"word": m.group(0),
                                  "span": sent[:24]}))

    opp = _opposite(rules.protagonist_pronoun)
    if opp and rules.protagonist:
        joined = "\n".join(narr_lines)
        for m in re.finditer(re.escape(rules.protagonist) + r"\s*" + re.escape(opp), joined):
            out.append(V("cons-pronoun-adj", Severity.HIGH,
                         {"word": opp, "span": joined[max(0, m.start() - 6): m.end() + 8],
                          "note": "主角名后紧跟反性别人称"}))
        name_count = joined.count(rules.protagonist)
        correct = joined.count(rules.protagonist_pronoun) if rules.protagonist_pronoun else 0
        opposite_count = joined.count(opp)
        if name_count >= 3 and correct == 0 and opposite_count >= 3:
            out.append(V("cons-pronoun", Severity.HIGH,
                         {"protagonist": rules.protagonist,
                          "name_count": name_count, "correct_pronoun_count": correct,
                          "opposite_pronoun_count": opposite_count,
                          "span": joined[:60]},
                         confidence=0.7,
                         note="主角名多次出现但主角人称从未使用、反性别人称反复出现"))

    return out
