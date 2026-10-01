#!/usr/bin/env python3
"""
test_calib.py — 判定器真实文本校准的测试（零 LLM / 零网络）。

覆盖：语料装载过滤、差分测量（真实文本固有命中不算判据反应）、
信号量测量（禁词按章聚合时注入仍可见）、逐判据召回/特异性、
以及一条**测量设计回归**：真实文本自带命中时，诱饵判据不得被误判为失效。

用法:
    python3 tests/test_calib.py
"""

import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bench.calib import real_text as R

_PASS, _FAIL = 0, 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  FAIL: {name}")


# 载体文本：一段真实的连续散文（含超长段落，用来模拟真实负载的固有命中）
CARRIER = "\n".join([
    "雾从江口漫上来，贴着货棚的油布顶往下滴水。",
    "她数着木板缝，一直数到门开。" * 30,          # 超长段落 → cons-para 固有命中
    "门里的话断断续续，听不真切。",
    "她退后半步，脚跟先着地，没出声。" * 6,
    "风把绳子吹得发响，远处有人在卸货。",
])


def test_load_corpus_filters():
    d = Path(tempfile.mkdtemp())
    (d / "第1章.md").write_text("正文" * 400, encoding="utf-8")
    (d / "第2章.md").write_text("太短", encoding="utf-8")            # 短样本应跳过
    (d / "章节状态.md").write_text("状态" * 400, encoding="utf-8")   # 非正文应跳过
    (d / "第1章.ai.md").write_text("快照" * 400, encoding="utf-8")   # 快照应跳过
    got = R.load_corpus([d])
    check("只收正文 md（跳过状态/快照/过短）", [n for n, _ in got] == ["第1章.md"])
    check("单文件路径也可用", len(R.load_corpus([d / "第1章.md"])) == 1)


def test_recall_and_specificity_on_carrier():
    res = R.calibrate_texts([("carrier", CARRIER)], chapter=2)
    row = res["rows"][0]
    for p in R.PROBES:
        check(f"正例被抓到: {p['kind']}", row["probes"][p["kind"]]["recall"] is True)
        if p["decoy"]:
            check(f"诱饵不误报: {p['kind']}",
                  row["probes"][p["kind"]]["specificity"] is True)
    summ = R.summarize(res)
    for k, v in summ["kinds"].items():
        check(f"{k} 召回 100%", v["recall"] == 1.0)
        if v["specificity"] is not None:
            check(f"{k} 特异性 100%", v["specificity"] == 1.0)


def test_differential_measurement_ignores_carrier_hits():
    """载体自带超长段落 → cons-para 基线 > 0；诱饵仍须判为「未误报」。

    这是测量设计的回归：曾用「全文是否命中」当判据反应，于是载体固有的
    超长段落把 cons-para 的诱饵判成失效，得出特异性 0% 的假结论。
    """
    res = R.calibrate_texts([("carrier", CARRIER)], chapter=2)
    row = res["rows"][0]
    check("载体确有固有命中（段落）", row["clean_by_kind"]["cons-para"] > 0)
    check("固有命中不污染诱饵判定", row["probes"]["cons-para"]["specificity"] is True)
    check("固有命中计入候选而非误报",
          R.summarize(res)["clean_candidates"] == row["clean_candidates"])


def test_signal_measurement_survives_chapter_aggregation():
    """禁词判据按章聚合（一章内同一禁词算一条）：载体若已含该词，
    再注入一次不增加条数——按条数测会误判成「没抓到」，按出现次数测才对。"""
    carrier = "她忽然停住，看着货棚的门。\n" + CARRIER     # 已含探针禁词
    from bench.calib.real_text import (calibration_ledger, calibration_rules,
                                       _judge_all, _signal, PROBE_FORBIDDEN)
    led, rules = calibration_ledger(), calibration_rules()
    clean = _judge_all(carrier, led, rules, 2, "c")
    base = _signal(clean, "cons-forbidden")
    check("载体已含探针禁词时基线 ≥1", base >= 1)
    injected = _judge_all(R._splice(carrier, f"她{PROBE_FORBIDDEN}停住，回头看了一眼。"),
                          led, rules, 2, "p")
    rows = [v for v in injected if v.probe_id.startswith("cons-forbidden")]
    check("条数不增（聚合口径）", len(rows) == len([v for v in clean
                                                  if v.probe_id.startswith("cons-forbidden")]))
    check("出现次数增加（信号量可见注入）", _signal(injected, "cons-forbidden") > base)
    res = R.calibrate_texts([("carrier-with-word", carrier)], chapter=2)
    check("校准仍判为抓到",
          res["rows"][0]["probes"]["cons-forbidden"]["recall"] is True)


def test_summarize_math():
    res = R.calibrate_texts([("a", CARRIER), ("b", CARRIER + "\n再多一段。")], chapter=2)
    s = R.summarize(res)
    check("样本数与字数", s["texts"] == 2 and s["chars"] > len(CARRIER))
    check("候选密度按字数算",
          abs(s["candidates_per_10k"] -
              s["clean_candidates"] / s["chars"] * 10000) < 0.01)
    check("逐判据都有记录", set(s["kinds"]) == {p["kind"] for p in R.PROBES})


def test_no_decoy_kinds_declared():
    """禁词类没有「不该报」的孪生句（判据不区分语境）——必须显式声明，
    不能被当成「特异性未测」。"""
    f = [p for p in R.PROBES if p["kind"] == "cons-forbidden"][0]
    check("禁词类显式声明无诱饵", f["decoy"] is None)


if __name__ == "__main__":
    test_load_corpus_filters()
    test_recall_and_specificity_on_carrier()
    test_differential_measurement_ignores_carrier_hits()
    test_signal_measurement_survives_chapter_aggregation()
    test_summarize_math()
    test_no_decoy_kinds_declared()
    print(f"\n结果: {_PASS}/{_PASS + _FAIL} 通过")
    sys.exit(1 if _FAIL else 0)
