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


def vio(t, chapter=1, word="忽然", probe=None):
    return {"probe_id": probe or f"cons-forbidden-ch{chapter}", "type": t,
            "detector": "mechanical", "chapter": chapter, "severity": "high",
            "evidence": {"word": word}, "run_id": "r", "confidence": 1.0}


# ── 基础指标 ──────────────────────────────────────────────────────────

def test_rate_and_summary():
    check("密度公式", M.violations_per_10k(5, 2500) == 20.0)
    check("零字数不除零", M.violations_per_10k(3, 0) == 0.0)
    check("零违反为 0", M.violations_per_10k(0, 1000) == 0.0)

    s = M.summarize_run([vio("constraint"), vio("state"), vio("constraint")], 2000)
    check("绝对值与密度成对", s["violations_abs"] == 3 and s["violations_per_10k"] == 15.0)
    check("按类型计数", s["by_type"] == {"constraint": 2, "state": 1})


def test_family_split():
    """聚合数字会把叙事一致性与篇幅合规混在一起——必须分族、并给出剔篇幅的核心数。"""
    check("篇幅族", M.family_of("cons-length-ch1") == "length")
    check("文体族", M.family_of("cons-forbidden-ch1") == "style"
          and M.family_of("cons-para-ch2") == "style")
    check("视角族", M.family_of("cons-pov-ch1") == "pov"
          and M.family_of("cons-pronoun-adj-ch3") == "pov")
    check("状态族（前缀）", M.family_of("state-dead-ch3") == "state")
    check("未知归 other", M.family_of("weird-ch1") == "other")

    vs = [vio("constraint", probe="cons-length-ch1"),
          vio("constraint", probe="cons-length-ch2"),
          vio("constraint", probe="cons-forbidden-ch3"),
          vio("state", probe="state-dead-ch3")]
    s = M.summarize_run(vs, 1000)
    check("分族计数", s["by_family"] == {"length": 2, "style": 1, "state": 1})
    check("核心数剔除篇幅", s["core_abs"] == 2)
    check("核心密度按字数", s["core_per_10k"] == 20.0)


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
    check("按档位平均（核心率）", m1["core_rate_by_tier"] == {"bare": 100.0, "mid": 40.0, "full": 0.0})
    check("归因随汇总给出", m1["attribution"]["total"] == 100.0)
    check("平均成本", m1["avg_cost_per_run"] == 0.01)
    check("单档位模型也可汇总", agg["m2"]["core_rate_by_tier"] == {"bare": 10.0})


def test_incomplete_and_paired_gate():
    """未完成 run 不能显示为 0 违反；门禁贡献用配对测量。"""
    def mk(probe):
        return vio("constraint", probe=probe)

    def run(model, tier, pre, post, chars, done=6):
        return {"model": model, "tier": tier, "k": 0, "chars": chars, "cost": 0.01,
                "summary": {"model": model, "tier": tier, "k": 0, "chapters_done": done},
                "violations": {"pre_fix": [mk(p) for p in pre],
                               "post_fix": [mk(p) for p in post]}}
    runs = {
        # full：修前核心 3（style 2 + state 1）+ 篇幅 1 → 修后只剩篇幅
        "m1__full__k0": run("m1", "full",
                            ["cons-forbidden-ch1", "cons-para-ch2",
                             "state-dead-ch3", "cons-length-ch4"],
                            ["cons-length-ch4"], 1000),
        "m1__bare__k0": run("m1", "bare", ["cons-forbidden-ch1"] * 4, [], 1000),
        # 未完成：0 章 0 字
        "m2__bare__k0": run("m2", "bare", [], [], 0, done=0),
    }
    rows = {r["run_id"]: r for r in M.build_table(runs)}
    check("未完成 run 标 incomplete", rows["m2__bare__k0"]["incomplete"] is True)
    check("未完成 run 核心数为 None（不显示 0）",
          rows["m2__bare__k0"]["core_abs"] is None)
    check("未完成 run 在 Markdown 中标明",
          "**未完成**" in M.render_markdown(M.build_table(runs),
                                           M.aggregate_by_model(runs)))
    agg = {a["model"]: a for a in M.aggregate_by_model(runs)}
    check("未完成模型不进汇总", "m2" not in agg)
    g = agg["m1"]["gate_paired"]
    check("配对门禁：修前核心 3 → 修后 0，擦除 3",
          g["core_pre"] == 3 and g["core_post"] == 0 and g["removed"] == 3)
    check("汇总不再给 mid↔full 门禁差值",
          "gate_postprocessing" not in agg["m1"]["attribution"])


def test_render_markdown():
    runs = _runs_fixture()
    md = M.render_markdown(M.build_table(runs), M.aggregate_by_model(runs))
    check("含每次运行表头", "核心/万字" in md and "修前→修后" in md and "篇幅" in md)
    check("含汇总表头", "上下文工程" in md and "门禁" in md)
    check("含数据行", "m1__bare__k0" in md and "m1" in md)
    check("以换行结尾", md.endswith("\n"))


# ── 装载（I/O 层）────────────────────────────────────────────────────

def test_subscription_cost_not_rendered_as_zero():
    """订阅通道成本列不能显示 $0——会被读成「免费」，与「边际成本为 0」不是一回事。"""
    check("订阅标为订阅", M._cost_cell(0.0, "subscription") == "订阅")
    check("免费额度标为免费额度", M._cost_cell(0.0, "free_quota") == "免费额度")
    check("按量照常显示数字", M._cost_cell(0.0013, "per_token") == "0.0013")

    def run(model, tier, billing):
        return {"model": model, "tier": tier, "k": 0, "chars": 1000, "cost": 0.0,
                "billing": billing,
                "summary": {"model": model, "tier": tier, "k": 0,
                            "billing": billing, "chapters_done": 6},
                "violations": {"pre_fix": [], "post_fix": []}}
    runs = {"ark__bare__k0": run("ark", "bare", "subscription"),
            "paid__bare__k0": run("paid", "bare", "per_token")}
    md = M.render_markdown(M.build_table(runs), M.aggregate_by_model(runs))
    check("订阅行渲染为订阅", "| ark | bare | 0 | 1000 | 0 | 0 | 0.0 | 0 | 0 | 0 | 0 | — | 订阅 |" in md)
    check("按量行仍显示美元", "| paid | bare | 0 | 1000 | 0 | 0 | 0.0 | 0 | 0 | 0 | 0 | — | 0.0 |" in md)
    check("口径说明进表头", "成本口径" in md and "零边际成本通道" in md)


def test_leaderboard():
    """榜单：升序、并列同名次、缺档标注、未完成不进榜。"""
    ranked = M.leaderboard([
        {"model": "b", "core_rate_by_tier": {"bare": 3.3, "mid": 1.1, "full": 0.0},
         "billing": "subscription"},
        {"model": "a", "core_rate_by_tier": {"bare": 1.1, "full": 0.5},
         "billing": "per_token"},          # 缺 mid
        {"model": "c", "core_rate_by_tier": {"bare": 1.1}},   # 与 a 并列，缺两档
        {"model": "d", "core_rate_by_tier": {}},              # 无 bare → 不进榜
    ])
    check("缺 bare 不进榜", [e["model"] for e in ranked] == ["a", "c", "b"])
    check("升序", ranked[0]["bare"] <= ranked[-1]["bare"])
    check("并列同名次", ranked[0]["rank"] == ranked[1]["rank"] == 1
          and ranked[2]["rank"] == 3)
    check("缺档列出", ranked[0]["missing"] == ["mid"] and ranked[1]["missing"] == ["mid", "full"])
    check("计费口径带上", ranked[2]["billing"] == "subscription")
    md = M.render_leaderboard(ranked)
    check("榜单渲染含口径说明", "排序键" in md and "零边际成本" in md)
    check("缺档渲染为 —", "| 3 | b | 3.3 | 1.1 | 0.0 | 订阅 | — |" in md
          and "| 1 | a | 1.1 | — | 0.5 | 按量 | mid |" in md)
    # 计费列只标口径：按量模型曾渲染成字面量 "0.0"（读起来是「免费」）
    check("按量模型计费列标「按量」而非 0.0",
          M._billing_label("per_token") == "按量"
          and M._billing_label("subscription") == "订阅"
          and M._billing_label("free_quota") == "免费额度")


def test_gate_contribution_counts_fully_cleaned_runs():
    """门禁把违反擦干净的 run（post_fix == []）正是最好情形，不能被跳过。

    缺陷形态：`not r["violations"].get("post_fix")` —— 空列表为假 → 整 run 跳过
    → removed 系统性低估（全擦干净的模型直接从表里消失）。"""
    def run(model, pre, post):
        return {"model": model, "tier": "full", "k": 0, "chars": 1000, "cost": 0.0,
                "summary": {"model": model, "tier": "full", "k": 0, "chapters_done": 6},
                "violations": {"pre_fix": [vio("constraint", probe=p) for p in pre],
                               "post_fix": [vio("constraint", probe=p) for p in post]}}
    runs = {
        "a__full__k0": run("a", ["cons-forbidden-ch1", "cons-para-ch2"], []),   # 全擦干净
        "b__full__k0": run("b", ["cons-forbidden-ch1"], ["cons-length-ch1"]),   # 残留篇幅
    }
    g = M.gate_contribution(runs)
    check("全擦干净的 run 计入", g["full_runs"] == 2)
    check("擦除数含全清 run", g["core_pre"] == 3 and g["core_post"] == 0
          and g["removed"] == 3)


def test_gate_contribution_applies_exclusion_rules():
    """门禁配对测量必须和汇总用同一套剔除规则：半截运行（章数不足）不进配对统计，
    否则「门禁贡献」里会混进一次残跑的修前修后差值（评审 F8）。"""
    def run(model, pre, post, done):
        return {"model": model, "tier": "full", "k": 0, "chars": 2000, "cost": 0.0,
                "summary": {"model": model, "tier": "full", "k": 0,
                            "chapters_done": done},
                "violations": {"pre_fix": [vio("constraint", probe=p) for p in pre],
                               "post_fix": [vio("constraint", probe=p) for p in post]}}
    runs = {
        "full6__full__k0": run("full6", ["cons-forbidden-ch1"] * 3, [], 6),
        "half__full__k0": run("half", ["cons-forbidden-ch1"] * 5, [], 2),   # 半截
        "empty__full__k0": run("empty", ["cons-forbidden-ch1"] * 4, [], 0),  # 未完成
    }
    g = M.gate_contribution(runs, expected_chapters=6)
    check("半截与未完成不进配对统计", g["full_runs"] == 1 and g["core_pre"] == 3)
    g2 = M.gate_contribution(runs)
    check("不给期望章数时退回旧行为（仅排未完成）",
          g2["full_runs"] == 2 and g2["core_pre"] == 8)


def test_missing_violations_json_is_not_zero_violations():
    """缺 violations.json 的残缺 run 不能以「零违反」身份上榜。"""
    out = Path(tempfile.mkdtemp())
    d = out / "m1__bare__k0"
    d.mkdir()
    (d / "ch1.md").write_text("正文" * 50, encoding="utf-8")
    (d / "run.json").write_text(json.dumps({
        "run_id": "m1__bare__k0", "cost": {"currency_cost": 0.0},
        "_summary": {"model": "m1", "tier": "bare", "k": 0, "chapters_done": 1},
    }), encoding="utf-8")   # 故意不写 violations.json
    runs = M.load_runs(out)
    check("标记缺判决", runs["m1__bare__k0"]["violations_missing"] is True)
    row = M.build_table(runs)[0]
    check("缺判决按未完成处理", row["incomplete"] is True and row["core_abs"] is None)
    check("缺判决不进汇总", M.aggregate_by_model(runs) == [])
    check("缺判决不进榜单", M.leaderboard(M.aggregate_by_model(runs)) == [])


def test_aggregate_excludes_partial_by_expected_chapters():
    """部分完成必须按**期望章数**剔除：全体同样残缺时不能靠「最大完成度」兜底，
    否则汇总表收了人，脚注却写「不参与汇总」——自相矛盾。"""
    def run(model, done):
        return {"model": model, "tier": "bare", "k": 0, "chars": 1000, "cost": 0.0,
                "summary": {"model": model, "tier": "bare", "k": 0,
                            "chapters_done": done},
                "violations": {"pre_fix": [vio("constraint")], "post_fix": []}}
    runs = {"m1__bare__k0": run("m1", 2), "m2__bare__k0": run("m2", 2)}
    check("全体残缺+无期望章数时按最大完成度兜底",
          {a["model"] for a in M.aggregate_by_model(runs)} == {"m1", "m2"})
    check("给出期望章数则全部剔除",
          M.aggregate_by_model(runs, expected_chapters=6) == [])


def test_load_runs():
    out = Path(tempfile.mkdtemp())
    d = out / "m1__full__k0"
    d.mkdir()
    (d / "ch1.md").write_text("正文一百字。" * 5, encoding="utf-8")       # 30 字
    (d / "ch2.md").write_text("第二章正文。" * 5, encoding="utf-8")       # 30 字
    (d / "ch1.fixed.md").write_text("修正稿" * 100, encoding="utf-8")     # 应被排除
    (d / "run.json").write_text(json.dumps({
        "run_id": "m1__full__k0", "cost": {"currency_cost": 0.02},
        "_summary": {"model": "m1", "tier": "full", "k": 0,
                     "billing": "subscription"},
    }), encoding="utf-8")
    (d / "violations.json").write_text(json.dumps(
        {"pre_fix": [vio("constraint")], "post_fix": []}), encoding="utf-8")
    runs = M.load_runs(out)
    check("装载一个运行", list(runs) == ["m1__full__k0"])
    r = runs["m1__full__k0"]
    check("字数排除修正稿", r["chars"] == 60)
    check("成本读出", r["cost"] == 0.02)
    check("计费口径读出", r["billing"] == "subscription")
    check("违反读出", len(r["violations"]["pre_fix"]) == 1)

    # 缺 billing 字段的老 run.json（v1 之前）：按量口径兜底，不误标订阅
    (d / "run.json").write_text(json.dumps({
        "run_id": "m1__full__k0", "cost": {"currency_cost": 0.02},
        "_summary": {"model": "m1", "tier": "full", "k": 0},
    }), encoding="utf-8")
    check("老 manifest 兜底为按量", M.load_runs(out)["m1__full__k0"]["billing"] == "per_token")

    empty = Path(tempfile.mkdtemp())
    check("空目录返回空", M.load_runs(empty) == {})


if __name__ == "__main__":
    test_rate_and_summary()
    test_family_split()
    test_pass_k()
    test_bootstrap_ci()
    test_attribution()
    test_build_table_and_full_uses_post()
    test_aggregate_by_model()
    test_incomplete_and_paired_gate()
    test_render_markdown()
    test_subscription_cost_not_rendered_as_zero()
    test_leaderboard()
    test_gate_contribution_counts_fully_cleaned_runs()
    test_gate_contribution_applies_exclusion_rules()
    test_missing_violations_json_is_not_zero_violations()
    test_aggregate_excludes_partial_by_expected_chapters()
    test_load_runs()
    print(f"\n结果: {_PASS}/{_PASS + _FAIL} 通过")
    sys.exit(1 if _FAIL else 0)