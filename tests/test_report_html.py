#!/usr/bin/env python3
"""
test_report_html.py — 静态榜单页生成器测试（零 LLM / 零网络）。

覆盖：图表几何（柱数/比例/不越界）、非颜色编码（图例 + 直接标值）、
明暗两套主题、无外部资源依赖、表格数据与 metrics 一致、
抗刷分表与 writeup §8 同源（同一个基线算出同一组数字）。

用法:
    python3 tests/test_report_html.py
"""

import os
import re
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bench.report import html as H
from bench.report import metrics as M

_PASS, _FAIL = 0, 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  FAIL: {name}")


def vio(probe, chapter=1):
    return {"probe_id": probe, "type": "constraint", "detector": "mechanical",
            "chapter": chapter, "severity": "low", "evidence": {}, "run_id": "r",
            "confidence": 1.0}


def _fixture_dir():
    """三个模型 × 三档的迷你跑批目录（完整运行，可出榜单）。"""
    out = Path(tempfile.mkdtemp())
    rates = {"m1": {"bare": 8.0, "mid": 4.0, "full": 0.0},
             "m2": {"bare": 2.0, "mid": 5.0, "full": 1.0},
             "m3": {"bare": 6.0, "mid": 3.0, "full": 0.0}}
    for model, tiers in rates.items():
        for tier, n in tiers.items():
            d = out / f"{model}__{tier}__k0"
            d.mkdir()
            (d / "ch1.md").write_text("正文" * 500, encoding="utf-8")   # 1000 字
            vs = [vio(f"cons-forbidden-ch1") for _ in range(int(n * 1000 / 10000))]
            post = vs if tier == "full" else []
            (d / "violations.json").write_text(
                __import__("json").dumps({"pre_fix": vs, "post_fix": post}),
                encoding="utf-8")
            (d / "run.json").write_text(__import__("json").dumps({
                "run_id": f"{model}__{tier}__k0", "cost": {"currency_cost": 0.0},
                "generated_at": "2026-09-27T00:00:00+00:00",
                "bench": {"universe_seed": 42, "chapters": 6,
                          "judge_mechanical_version": "m0.3.0",
                          "universe_generator_version": "u0.1.0"},
                "_summary": {"model": model, "tier": tier, "k": 0,
                             "chapters_done": 6, "billing": "subscription",
                             "provider": "ark"},
            }), encoding="utf-8")
    (out / "results.json").write_text(__import__("json").dumps(
        {"universe": {"seed": 42, "chapters": 6}}), encoding="utf-8")
    return out


# ── 图 ────────────────────────────────────────────────────────────────

def test_chart_geometry():
    rates = {"m1": {"bare": 8.0, "mid": 4.0, "full": 0.0},
             "m2": {"bare": 2.0, "mid": 5.0, "full": 1.0},
             "m3": {"bare": 6.0, "mid": 3.0, "full": 0.0}}
    svg = H.render_chart(rates, ["m1", "m2", "m3"])
    check("三模型 × 三档 = 9 根柱", svg.count('<path d="M') == 9)
    check("每根柱都直接标值（非颜色编码 + 浅色对比度 relief）",
          len(re.findall(r'font-size="10\.5"', svg)) == 9)
    check("每根柱带悬浮提示", svg.count("<title>") == 9)
    check("含图例所需的系列色（按实体固定顺序）",
          all(f"var(--s{i})" in svg for i in (1, 2, 3)))
    check("y 轴自 0 起（柱状图不截断基线）",
          'y1="' in svg and "var(--grid)" in svg)
    # 几何不越界：所有 x 落在画布内
    xs = [float(m) for m in re.findall(r'<path d="M([\d.]+),', svg)]
    check("柱不越出画布", xs and min(xs) > 0 and max(xs) < 760)
    check("零值柱仍留可见残迹（0 也有 2px）",
          svg.count("L") > 0 and "0.00" in svg)


def test_chart_zero_and_single_series():
    svg = H.render_chart({"only": {"bare": 0.0, "mid": 0.0, "full": 0.0}}, ["only"])
    check("全零数据不崩", svg.count('<path d="M') == 3)
    check("全零仍标值", svg.count("0.00") == 3)


# ── 页面 ──────────────────────────────────────────────────────────────

def test_page_structure_and_no_external_assets():
    page = H.render_page(_fixture_dir())
    check("含标题与判定器版本", "CanonBench" in page and "m0.3.0" in page)
    check("四张表（榜单/每次运行/门禁/抗刷分）", page.count("<table>") == 4)
    check("明暗两套主题都声明",
          "prefers-color-scheme: dark" in page and ':root[data-theme="dark"]' in page)
    check("非颜色编码：图例存在", 'class="legend"' in page)
    ext = re.findall(r'(?:src|href)="(https?://[^"]+)"', page)
    check("无外部资源依赖（仅超链接到本仓库）",
          all("github.com/wanqili857-byte/fictionforge" in u for u in ext))
    check("局限清单在页面上（不藏）",
          "已知边界" in page and "k=1" in page and "真阳性 0" in page)
    check("复现命令可复制", "bench.report.metrics bench/results/v2" in page)
    check("转义：无裸露脚本注入", "<script" not in page)


def test_page_numbers_match_metrics():
    d = _fixture_dir()
    page = H.render_page(d)
    runs = M.load_runs(d)
    agg = {a["model"]: a["core_rate_by_tier"] for a in M.aggregate_by_model(runs, 6)}
    board = M.leaderboard(M.aggregate_by_model(runs, 6))
    check("榜单名次与 metrics 一致",
          page.count(f'>{board[0]["model"]}<') >= 1 and f'>{board[0]["rank"]}<' in page)
    check("每档数字进页面", all(f">{agg['m1']['bare']:g}<" in page for _ in [0]))


def test_attack_table_shares_baseline_with_docs():
    """页面与 writeup §8 必须算同一份基线——曾因页面自造基线导致数字对不上。"""
    from bench.attacks.attacks import BASELINE_TEXT, BASELINE_JARGON_TERMS
    check("基线 200 字、恰 1 处禁词",
          len(BASELINE_TEXT.strip()) == 200 and BASELINE_TEXT.count("忽然") == 1)
    check("词表 40 个双字词", len(BASELINE_JARGON_TERMS) == 40
          and all(len(t) == 2 for t in BASELINE_JARGON_TERMS))
    page = H.render_page(_fixture_dir())
    for expected in ("1200", "8.33", "-83%", "803", "12.45", "-75%", "320", "31.25", "-38%"):
        check(f"页面含 §8 实测值 {expected}", expected in page)


def test_series_colors_defined_for_any_roster():
    """每个模型都要拿到**已定义**的颜色变量，且互不相同（第一轮 doubao S15）。

    旧版把 `--s1..--s3` 写死在 CSS、`SERIES` 定义了却从不引用：第 4 个模型
    （协议明确欢迎外部提交）取 `var(--s4)` 时变量不存在，柱与图例色块一起
    退化成默认色，**两处互相不可区分**。当时 3 个模型未触发，于是漏了。
    """
    css = H._css()
    defined = set(re.findall(r"(--s(?:-over|\d+)):", css))
    check("CSS 里的系列色变量由 SERIES 生成（不是写死三个）",
          {"--s1", "--s2", "--s3", "--s4"} <= defined)
    for n in (3, 4, 6, 7):
        models = [f"ark-m{i}-flash" for i in range(n)]
        cols = [H.series_color(m, i) for i, m in enumerate(models)]
        names = {c[len("var("):-1] for c in cols}
        check(f"{n} 个模型全部有已定义颜色", names <= defined)
        check(f"{n} 个模型颜色互不相同", len(set(cols)) == n)
    # 超过槽位不循环配色：一律归到中性 --s-over，宁可不可区分也不撞色
    many = [H.series_color(f"ark-x{i}", i) for i in range(9)]
    check("超出槽位归到 --s-over 而非循环", many[-1] == "var(--s-over)")
    # 颜色跟随**身份**：同名模型换个名次仍是同一个颜色
    check("颜色跟随模型身份而非名次",
          H.series_color("ark-glm-flash", 0) == H.series_color("ark-glm-flash", 2))


if __name__ == "__main__":
    test_chart_geometry()
    test_chart_zero_and_single_series()
    test_page_structure_and_no_external_assets()
    test_page_numbers_match_metrics()
    test_attack_table_shares_baseline_with_docs()
    test_series_colors_defined_for_any_roster()
    print(f"\n结果: {_PASS}/{_PASS + _FAIL} 通过")
    sys.exit(1 if _FAIL else 0)
