#!/usr/bin/env python3
"""
test_runner_harness.py — W7 模型目录 + 三档 harness 单元测试。

零 LLM / 零网络。覆盖：
- 模型目录：完整性 / 未知别名 / 家族 / 成本公式（真实价格）
- bare 档：只有 spec，无设定/前文/已知事实
- mid 档：含世界设定 + 前文 + **限定范围内的**已知事实
- **知识边界纪律**：终局真相未学不得注入、false 行永不注入、学习章之后的才注入
- full 档：prompt 与 mid 相同（差异在后处理）
- 后处理门禁：删禁词 / 切超长段 / 幂等
- 确定性

用法:
    python3 tests/test_runner_harness.py
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bench.runner import models
from bench.runner.harness import (
    build_prompt, apply_gate_fix, world_facts, known_facts,
    SYSTEM_BARE, SYSTEM_MID, TIERS,
)
from bench.universe.generator import generate

_PASS, _FAIL = 0, 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  FAIL: {name}")


# ── 模型目录 ──────────────────────────────────────────────────────────

def test_models_catalog():
    check("目录非空", len(models.CATALOG) >= 5)
    ok = True
    for alias, m in models.CATALOG.items():
        if alias != m.alias or not m.provider or not m.model or not m.family:
            ok = False
            print(f"      · {alias} 字段不完整")
        if m.price_in <= 0 or m.price_out <= 0:
            ok = False
            print(f"      · {alias} 价格应为正")
        if m.max_tokens <= 0 or not (0 <= m.temperature <= 2):
            ok = False
    check("每个 ModelSpec 字段完整且价格为正", ok)
    check("跨家族 ≥3", len({m.family for m in models.CATALOG.values()}) >= 3)
    check("价格快照带日期", bool(models.PRICES_FETCHED))
    try:
        models.get("不存在的模型")
        check("未知别名抛 KeyError", False)
    except KeyError:
        check("未知别名抛 KeyError", True)
    check("resolve 顺序保持", [m.alias for m in models.resolve(["kimi", "glm"])]
          == ["kimi", "glm"])
    check("families 去重", models.families(["kimi", "glm", "glm-flash"]) == {"moonshot", "glm"})


def test_cost_math():
    m = models.get("kimi")          # 0.95 / 4.00 每百万
    check("成本公式：0 token = 0", m.cost(0, 0) == 0.0)
    check("成本公式：1M in", abs(m.cost(1_000_000, 0) - 0.95) < 1e-9)
    check("成本公式：1M out", abs(m.cost(0, 1_000_000) - 4.00) < 1e-9)
    check("成本公式：混合", abs(m.cost(1_000_000, 1_000_000) - 4.95) < 1e-9)
    cheap = models.get("ds-flash")
    check("便宜模型更便宜", cheap.cost(10_000, 10_000) < m.cost(10_000, 10_000))


# ── harness 三档 ──────────────────────────────────────────────────────

def _u():
    return generate(seed=42, chapters=8)


def _spec(u, chapter=1):
    return next(s for s in u.specs if s["chapter"] == chapter)


def test_bare_tier():
    u = _u()
    sp = _spec(u)
    system, user = build_prompt("bare", u, sp, prior_text="前文内容XYZ")
    check("bare system 最小", system == SYSTEM_BARE)
    check("bare 含本章描述", sp["sections"][0]["description"][:20] in user)
    check("bare 无世界设定", not any(f[:10] in user for f in world_facts(u, 3)))
    check("bare 无前文", "前文内容XYZ" not in user)
    check("bare 无已知事实段", "此刻已知" not in user)


def test_mid_tier():
    u = _u()
    sp = _spec(u, 3)
    system, user = build_prompt("mid", u, sp, prior_text="前文尾巴ABC")
    check("mid system 含一致性要求", "一致" in system and system == SYSTEM_MID)
    check("mid 含世界设定", any(f[:12] in user for f in world_facts(u, 3)))
    check("mid 含前文尾巴", "前文尾巴ABC" in user)
    check("mid 含已知事实段", "此刻已知" in user)
    # 已知事实必须是「本章及之前学到」的子集
    kf = known_facts(u, u.protagonist, 3)
    check("mid 只注入已学到的事实", all(s in user for s in kf))
    later = known_facts(u, u.protagonist, u.chapters)
    check("mid 不注入未学到的事实",
          all(s not in user for s in later if s not in kf))


def test_knowledge_boundary_discipline():
    u = _u()
    by_id = {f["id"]: f for f in u.truth_table}
    terminal = by_id[u.terminal_fact_id]["statement"]
    _, user1 = build_prompt("mid", u, _spec(u, 1), prior_text=None)
    check("终局真相在第 1 章 prompt 中不出现", terminal not in user1)
    _, user_last = build_prompt("mid", u, _spec(u, u.chapters), prior_text=None)
    check("终局真相在其学习章出现", terminal in user_last)
    # false 行永不注入
    falses = [f["statement"] for f in u.truth_table if f["is_false"]]
    check("false 行永不注入", all(s not in user_last for s in falses))


def test_prior_tail_truncation():
    u = _u()
    long_prior = "甲" * 5000 + "尾巴标记"
    _, user = build_prompt("mid", u, _spec(u, 2), prior_text=long_prior,
                           prior_tail=1200)
    check("前文只取尾巴", "尾巴标记" in user and user.count("甲") <= 1200)


def test_full_equals_mid_prompt():
    u = _u()
    sp = _spec(u, 2)
    a = build_prompt("mid", u, sp, prior_text="X")
    b = build_prompt("full", u, sp, prior_text="X")
    check("full 与 mid 的 prompt 相同（差异在后处理）", a == b)
    try:
        build_prompt("nope", u, sp)
        check("未知档位抛错", False)
    except ValueError:
        check("未知档位抛错", True)
    check("TIERS 三档", TIERS == ("bare", "mid", "full"))


def test_output_rules_identical_across_tiers():
    """输出格式要求在三档必须一致——否则档位对比不公平（首次真实跑批踩到：
    模型输出 markdown 标题污染字数统计）。"""
    u = _u()
    sp = _spec(u, 2)
    _, bare_user = build_prompt("bare", u, sp)
    _, mid_user = build_prompt("mid", u, sp, prior_text="X")
    check("bare 含输出要求", "只写正文" in bare_user and "不要章节标题" in bare_user)
    check("mid 含输出要求", "只写正文" in mid_user)
    from bench.runner.harness import OUTPUT_RULES
    check("三档输出要求字面一致",
          bare_user.endswith(OUTPUT_RULES) and mid_user.endswith(OUTPUT_RULES))


def test_determinism():
    u = _u()
    sp = _spec(u, 2)
    check("确定性：两次构造相同",
          build_prompt("mid", u, sp, prior_text="Z") == build_prompt("mid", u, sp, prior_text="Z"))


# ── 后处理门禁 ────────────────────────────────────────────────────────

def test_apply_gate_fix():
    text = "他忽然停住。"
    fixed = apply_gate_fix(text, forbidden_words=["忽然"])
    check("删禁词", "忽然" not in fixed and "他停住" in fixed)

    long_para = "字" * 260
    fixed2 = apply_gate_fix(long_para, forbidden_words=[], para_max=100)
    lines = fixed2.split("\n")
    check("切超长段：行数 >1", len(lines) > 1)
    check("切超长段：每段 ≤100", all(len(l) <= 100 for l in lines))
    check("切超长段：不丢字", sum(len(l) for l in lines) == 260)

    once = apply_gate_fix(long_para, forbidden_words=["忽然"], para_max=100)
    twice = apply_gate_fix(once, forbidden_words=["忽然"], para_max=100)
    check("幂等", once == twice)
    check("短文本不变", apply_gate_fix("一句话。", [], 100) == "一句话。")
    check("空禁词表不崩", apply_gate_fix("文本", [], 100) == "文本")


if __name__ == "__main__":
    test_models_catalog()
    test_cost_math()
    test_bare_tier()
    test_mid_tier()
    test_knowledge_boundary_discipline()
    test_prior_tail_truncation()
    test_full_equals_mid_prompt()
    test_output_rules_identical_across_tiers()
    test_determinism()
    test_apply_gate_fix()
    print(f"\n结果: {_PASS}/{_PASS + _FAIL} 通过")
    sys.exit(1 if _FAIL else 0)