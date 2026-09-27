#!/usr/bin/env python3
"""bench.attacks — W9 抗刷分攻击器（纯函数 + 度量）。

三个攻击，目的不是"防住"，而是**量出指标能被怎么刷**：
  1. inflate     干净注水：塞无违反的填充文本，稀释「每万字违反率」
  2. duplicate   重复：复制段落，分子分母同乘（对密度指标无效，需重复率判定器）
  3. jargon_bomb 术语轰炸：堆设定词但无实质，看指标是否被骗着变好

写攻击清单的第一天就抓到自己指标的洞（详见 writeup §8）：
`rate = 违反数 / 字数 × 10000`，干净注水让分子不变、分母翻倍 → 指标减半。
所以指标必须**绝对数与密度成对同报**，且密度只在篇幅门禁通过时有效。

攻击器只做文本变换（可单测），度量走注入的判定器（离线用假件）。
"""

import hashlib


# ── 攻击：文本变换 ────────────────────────────────────────────────────

def inflate(text: str, factor: float = 2.0, filler: str = None) -> str:
    """干净注水到约 factor 倍字数。

    filler 刻意不含禁词、段落短（不触发段落判据）——模拟"刷分者"
    用最无害的文本拉长篇幅。默认填充为不含任何判据敏感词的叙述。
    """
    if factor <= 1.0:
        raise ValueError("factor 必须 > 1")
    target = int(len(text) * factor)
    pool = filler or (
        "雾贴着水面走，一层一层。她没有动。\n"
        "远处有人在卸货，声音断断续续。她数了数缆绳上的结。\n"
        "盐霜落在木板上，白得发亮。她把手插回袖子里。\n"
    )
    out = text
    while len(out) < target:
        out += pool
    return out[:max(target, len(text))]


def duplicate(text: str, times: int = 2) -> str:
    """整段复制 times 次（内容零新增——模拟"注水"手法）。"""
    if times < 1:
        raise ValueError("times 必须 ≥ 1")
    return "\n".join([text] * times)


def jargon_bomb(text: str, terms, per_line: int = 4) -> str:
    """在文末堆设定术语（无实质内容），看指标是否被"看起来更专业"骗到。"""
    terms = list(terms)
    if not terms:
        return text
    lines = []
    for i in range(0, len(terms), per_line):
        lines.append("、".join(terms[i:i + per_line]))
    return text + "\n" + "\n".join(lines)


def fingerprint(text: str) -> str:
    """文本指纹（重复率判据的原料，W9 待建的判定器用）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def duplicate_ratio(text: str, k: int = 80, step: int = 1) -> float:
    """内容重复率：重复的 k 字窗口占比（去空白后**逐字**滑动取样）。

    为什么逐字滑动：固定步长会与重复周期错位而漏检（实测踩过：
    周期 112 字、步长 20 时重复窗一个都撞不上）。逐字滑动对任意
    重复周期都成立，章节规模（数千字）下开销可忽略。
      · duplicate 整段复制   → 重复窗口比例飙升
      · inflate 反复追加填充 → 同样飙升（注水本质就是复制）
    只统计 ≥k 字的窗口，避免对话短句的正常重复误报。
    诚实标注：对"改写式"复制（同义替换后再重复）无效——那需要语义级判据。
    """
    s = "".join(text.split())          # 去所有空白，避免换行差异干扰
    if len(s) < k * 2:
        return 0.0
    step = max(1, step)
    windows = [s[i:i + k] for i in range(0, len(s) - k + 1, step)]
    if not windows:
        return 0.0
    seen, dup = set(), 0
    for w in windows:
        if w in seen:
            dup += 1
        seen.add(w)
    return round(dup / len(windows), 4)


# ── 度量：攻击前后的指标对照 ──────────────────────────────────────────

def measure(text: str, judge_fn, chapter: int = 1) -> dict:
    """对一段文本跑判定，返回绝对数与密度（成对）。"""
    vs = judge_fn(text, chapter)
    chars = len(text.strip())
    return {"violations_abs": len(vs), "chars": chars,
            "per_10k": round(len(vs) / chars * 10000, 2) if chars else 0.0,
            "types": sorted({v.type.value for v in vs})}


def run_attack(name: str, text: str, judge_fn, chapter: int = 1, **kw) -> dict:
    """执行一个攻击并给出前后对照。返回结果 dict（可直接进报告）。"""
    before = measure(text, judge_fn, chapter)
    if name == "inflate":
        attacked = inflate(text, **kw)
    elif name == "duplicate":
        attacked = duplicate(text, **kw)
    elif name == "jargon_bomb":
        attacked = jargon_bomb(text, **kw)
    else:
        raise ValueError(f"未知攻击: {name}")
    after = measure(attacked, judge_fn, chapter)
    return {
        "attack": name, "kwargs": {k: v for k, v in kw.items() if k != "terms"},
        "chars_before": before["chars"], "chars_after": after["chars"],
        "abs_before": before["violations_abs"], "abs_after": after["violations_abs"],
        "rate_before": before["per_10k"], "rate_after": after["per_10k"],
        "rate_delta": round(after["per_10k"] - before["per_10k"], 2),
        "dup_ratio_after": duplicate_ratio(attacked),
    }