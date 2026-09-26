#!/usr/bin/env python3
"""
test_report_metrics.py — W8 指标层单元测试（零 LLM / 零网络）。

覆盖：密度与绝对值成对、按类型计数、pass_k 边界、自助法 CI（固定种子可复现）、
三档归因差值、按模型汇总、表格与 Markdown 渲染、跑批目录装载（含修正稿排除）。

用法:
    python3 tests/test_report_metrics.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bench.report import metrics as M

_PASS, _FAIL = 0, 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  FAIL: {name}")


def vio(t, chapter=1, word="忽然"):
    return {"probe_id": f"x-ch{chapter}", "type": t, "detector": "mechanical",
            "chapter": chapter, "severity": "high",
            "evidence": {"word": word}, "run_id": "r", "confidence": 1.0}


# ── 基础指标 ──────────────────────────────────────────────────────────

def test_rate_and_summary():
    check("密度公式", M.violations_per_10k(5, 2500) == 20.0)
    check("零字数不除零", M.violations_per_10k(3, 0) == 0.0)
    check("零违反为 0", M.violations_per_10k(0, 1000) == 0.0)

    s = M.summarize_run([vio("constraint"), vio("state"), vio("constraint")], 2000)
    check("绝对值与密度成对", s["violations_abs"] == 3 and s["violations_per_10k"] == 15.0)
    check("按类型计数", s["by_type"] == {"constraint": 2, "state": 1})


def test_pass_k():
    check("全过 = 1.0", M.pass_k([0, 0, 0]) == 1.0)
    check("全挂 = 0.0", M.pass_k([1, 2, 3]) == 0.0)
    check("部分 = 比例", M.pass_k([0, 1, 0, 1]) == 0.5)
    check("空输入 = 0.0", M.pass_k([]) == 0.0)


def test_bootstrap_ci():
    data = [10.0, 20.0, 30.0, 20.0, 20.0]
    a = M.bootstrap_ci(data, iters=500, seed=1)
    b = M.bootstrap_ci(data, iters=500, seed=1)
    check("固定种子可复现", a == b)
    check("区间包住均值附近", a[0] <= 20.0 <= a[1] and a[0] < a[1])
    check("空样本 → (0,0)", M.bootstrap_ci([]) == (0.0, 0.0))
    constant = M.bootstrap_ci([5.0] * 10, iters=200, seed=2)
    check("常量样本区间退化", constant == (5.0, 5.0))


# ── 归因 ──────────────────────────────────────────────────────────────

def test_attribution():
    d = M.attribution({"bare": 30.0, "mid": 18.0, "full": 12.0})
    check("上下文工程差值", d["context_engineering"] == 12.0)
    check("门禁差值", d["gate_postprocessing"] == 6.0)
    check("合计差值", d["total"] == 18.0)
    partial = M.attribution({"bare": 30.0, "mid": 18.0})
    check("缺档位不产出对应项", "gate_postprocessing" not in partial)
    check("空输入空输出", M.attribution({}) == {})


# ── 表格与汇总 ────────────────────────────────────────────────────────

def _runs_fixture():
    def run(model, tier, k, pre, post, chars):
        return {"model": model, "tier": tier, "k": k, "chars": chars,
                "cost": 0.01, "summary": {"model": model, "tier": tier, "k": k},
                "violations": {"pre_fix": [vio(t) for t in pre],
                               "post_fix": [vio(t) for t in post]}}
    return {
        "m1__bare__k0": run("m1", "bare", 0, ["constraint"] * 10, [], 1000),
        "m1__mid__k0": run("m1", "mid", 0, ["constraint"] * 4, [], 1000),
        "m1__full__k0": run("m1", "full", 0, ["constraint"] * 4, [], 1000),
        "m2__bare__k0": run("m2", "bare", 0, ["state"] * 2, [], 2000),
    }


def test_build_table_and_full_uses_post():
    rows = {r["run_id"]: r for r in M.build_table(_runs_fixture())}
    check("bare 行用修前数", rows["m1__bare__k0"]["violations_abs"] == 10)
    full = rows["m1__full__k0"]
    check("full 行同时给出修前修后", full["pre_fix_abs"] == 4 and full["post_fix_abs"] == 0)
    check("full 的采用值 = 修后", full["violations_abs"] == 0)
    check("密度按字数算", rows["m2__bare__k0"]["violations_per_10k"] == 10.0)


def test_aggregate_by_model():
    agg = {a["model"]: a for a in M.aggregate_by_model(_runs_fixture())}
    m1 = agg["m1"]
    check("按档位平均", m1["rate_by_tier"] == {"bare": 100.0, "mid": 40.0, "full": 0.0})
    check("归因随汇总给出", m1["attribution"]["total"] == 100.0)
    check("平均成本", m1["avg_cost_per_run"] == 0.01)
    check("单档位模型也可汇总", agg["m2"]["rate_by_tier"] == {"bare": 10.0})


def test_render_markdown():
    runs = _runs_fixture()
    md = M.render_markdown(M.build_table(runs), M.aggregate_by_model(runs))
    check("含每次运行表头", "违反/万字" in md and "修前→修后" in md)
    check("含汇总表头", "上下文工程" in md and "门禁" in md)
    check("含数据行", "m1__bare__k0" in md and "m1" in md)
    check("以换行结尾", md.endswith("\n"))


# ── 装载（I/O 层）────────────────────────────────────────────────────

def test_load_runs():
    out = Path(tempfile.mkdtemp())
    d = out / "m1__full__k0"
    d.mkdir()
    (d / "ch1.md").write_text("正文一百字。" * 5, encoding="utf-8")       # 30 字
    (d / "ch2.md").write_text("第二章正文。" * 5, encoding="utf-8")       # 30 字
    (d / "ch1.fixed.md").write_text("修正稿" * 100, encoding="utf-8")     # 应被排除
    (d / "run.json").write_text(json.dumps({
        "run_id": "m1__full__k0", "cost": {"currency_cost": 0.02},
        "_summary": {"model": "m1", "tier": "full", "k": 0},
    }), encoding="utf-8")
    (d / "violations.json").write_text(json.dumps(
        {"pre_fix": [vio("constraint")], "post_fix": []}), encoding="utf-8")
    runs = M.load_runs(out)
    check("装载一个运行", list(runs) == ["m1__full__k0"])
    r = runs["m1__full__k0"]
    check("字数排除修正稿", r["chars"] == 60)
    check("成本读出", r["cost"] == 0.02)
    check("违反读出", len(r["violations"]["pre_fix"]) == 1)

    empty = Path(tempfile.mkdtemp())
    check("空目录返回空", M.load_runs(empty) == {})


if __name__ == "__main__":
    test_rate_and_summary()
    test_pass_k()
    test_bootstrap_ci()
    test_attribution()
    test_build_table_and_full_uses_post()
    test_aggregate_by_model()
    test_render_markdown()
    test_load_runs()
    print(f"\n结果: {_PASS}/{_PASS + _FAIL} 通过")
    sys.exit(1 if _FAIL else 0)