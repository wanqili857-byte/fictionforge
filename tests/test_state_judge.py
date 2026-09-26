#!/usr/bin/env python3
"""
test_state_judge.py — W3 机械状态判定单元测试。

零 LLM / 零网络。覆盖（BENCH_PLAN W3 验收）：
- 死人复活：命中 / 过去时豁免（回忆·遗物·听说）/ 对话豁免 / 未死角色不报
- 时间倒流：从账本 timeline_violations 转 Violation
- 日期矛盾：正文提到早于本章锚点的天 + 无过去时标记 → 报；有标记/对话 → 不报
- 物品双持有：同一物品被声明给两个持有者且无转移注记 → 报
- 边界：空文本 / 无死亡账本 / 确定性 / 类型与 detector 恒定

用法:
    python3 tests/test_state_judge.py
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bench.contracts import Violation, ViolationType, DetectorKind, Severity
from bench.state.ledger import build_ledger
from bench.judges.state_judge import state_judge

_PASS, _FAIL = 0, 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  FAIL: {name}")


def spec(chapter, anchors, delta=None, desc=""):
    s = {"novel": "T", "title": f"第{chapter}章", "chapter": chapter,
         "sections": [{"id": str(i + 1), "subject": "s", "scene_anchor": a,
                       "description": desc if i == 0 else ""}
                      for i, a in enumerate(anchors)]}
    if delta:
        s["state_delta"] = delta
    return s


def ledger_with_death():
    return build_ledger("T", [
        spec(1, ["第1天上午 @A"], desc="江晚蹲下。老周递水。"),
        spec(2, ["第2天上午 @B"], delta={"deaths": ["老周"]}),
    ], cast_names=["江晚", "老周"], protagonist="江晚")


def sig(vs):
    return sorted((v.probe_id, v.type.value, v.severity.value) for v in vs)


# ── 死人复活 ──────────────────────────────────────────────────────────

def test_dead_resurrection():
    led = ledger_with_death()
    # 命中：老周在第3章正文里正常活动
    vs = state_judge("老周把水递过来，手指关节发白。", led, chapter=3, run_id="r1")
    dead = [v for v in vs if "dead" in v.probe_id]
    check("死人复活命中 1 条", len(dead) == 1 and dead[0].severity == Severity.HIGH)
    check("复活证据含角色名", "老周" in dead[0].evidence.get("span", ""))

    # 过去时豁免
    for text in ["她想起老周当年的话。", "老周的坟就在坡上。",
                 "她翻出老周的遗物。", "听说老周死了。"]:
        vs2 = state_judge(text, led, chapter=3, run_id="r1")
        check(f"豁免不报: {text[:8]}", [v for v in vs2 if "dead" in v.probe_id] == [])

    # 对话豁免
    vs3 = state_judge("“老周还欠我半袋粮。”", led, chapter=3, run_id="r1")
    check("对话内豁免", [v for v in vs3 if "dead" in v.probe_id] == [])

    # 未死角色不报
    vs4 = state_judge("江晚把水递过来。", led, chapter=3, run_id="r1")
    check("未死角色不报", [v for v in vs4 if "dead" in v.probe_id] == [])

    # 死亡当章不报（第2章死的，第2章正文提及不算复活）
    vs5 = state_judge("老周倒下。", led, chapter=2, run_id="r1")
    check("死亡当章不报", [v for v in vs5 if "dead" in v.probe_id] == [])


# ── 时间倒流（账本级） ────────────────────────────────────────────────

def test_timeline_regression():
    bad = build_ledger("T", [
        spec(1, ["第3天上午 @A"]),
        spec(2, ["第1天上午 @B"]),
    ], cast_names=[])
    vs = state_judge("随便写。", bad, chapter=2, run_id="r1")
    tl = [v for v in vs if "timeline" in v.probe_id]
    check("账本时间倒流转 Violation", len(tl) == 1 and tl[0].type == ViolationType.STATE)

    ok = build_ledger("T", [
        spec(1, ["第1天上午 @A"]), spec(2, ["第2天上午 @B"]),
    ], cast_names=[])
    check("正常时间线不报",
          [v for v in state_judge("x。", ok, 2, "r1") if "timeline" in v.probe_id] == [])


# ── 日期矛盾（正文 vs 锚点） ──────────────────────────────────────────

def test_day_contradiction():
    led = build_ledger("T", [
        spec(1, ["第1天上午 @A"]), spec(2, ["第5天上午 @B"]),
    ], cast_names=[])
    vs = state_judge("第三天，她回到了河湾。", led, chapter=2, run_id="r1")
    dc = [v for v in vs if "day" in v.probe_id]
    check("正文天数早于锚点 → 报", len(dc) == 1 and dc[0].severity == Severity.MEDIUM)

    vs2 = state_judge("她记得第一天早上很冷。", led, chapter=2, run_id="r1")
    check("过去时标记豁免", [v for v in vs2 if "day" in v.probe_id] == [])

    vs3 = state_judge("“第一天我就来过。”", led, chapter=2, run_id="r1")
    check("对话豁免", [v for v in vs3 if "day" in v.probe_id] == [])

    vs4 = state_judge("第五天，她又来了。", led, chapter=2, run_id="r1")
    check("与锚点一致不报", [v for v in vs4 if "day" in v.probe_id] == [])


# ── 物品双持有 ────────────────────────────────────────────────────────

def test_item_conflict():
    led = build_ledger("T", [
        spec(1, ["第1天上午 @A"], delta={"items": {"旧终端": "江晚"}}),
        spec(2, ["第2天上午 @B"], delta={"items": {"旧终端": "老周"}}),
    ], cast_names=["江晚", "老周"])
    vs = state_judge("旧终端在桌上。", led, chapter=2, run_id="r1")
    ic = [v for v in vs if "item" in v.probe_id]
    check("双持有命中", len(ic) == 1 and ic[0].type == ViolationType.STATE)

    # 带转移注记 → 豁免（作者声明 transfer）
    led2 = build_ledger("T", [
        spec(1, ["第1天上午 @A"], delta={"items": {"旧终端": "江晚"}}),
        spec(2, ["第2天上午 @B"], delta={"items": {"旧终端": "老周"},
                                        "transfer": ["旧终端"]}),
    ], cast_names=["江晚", "老周"])
    check("有转移注记豁免",
          [v for v in state_judge("x。", led2, 2, "r1") if "item" in v.probe_id] == [])


# ── 边界与确定性 ──────────────────────────────────────────────────────

def test_edges():
    led = ledger_with_death()
    check("空文本不崩不报", state_judge("", led, 3, "r1") == [])
    vs = state_judge("老周来了。", led, 3, "r1")
    check("类型恒 state", all(v.type == ViolationType.STATE for v in vs))
    check("detector 恒 mechanical",
          all(v.detector == DetectorKind.MECHANICAL for v in vs))
    check("run_id 回填", all(v.run_id == "r1" for v in vs))
    a = state_judge("老周来了。", led, 3, "r1")
    b = state_judge("老周来了。", led, 3, "r1")
    check("确定性", [v.to_dict() for v in a] == [v.to_dict() for v in b])
    check("启发式置信<1", all(v.confidence < 1.0 for v in a))


if __name__ == "__main__":
    test_dead_resurrection()
    test_timeline_regression()
    test_day_contradiction()
    test_item_conflict()
    test_edges()
    print(f"\n结果: {_PASS}/{_PASS + _FAIL} 通过")
    sys.exit(1 if _FAIL else 0)