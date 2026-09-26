#!/usr/bin/env python3
"""
test_ledger.py — W2 状态账本 + 真相表解析单元测试。

零 LLM / 零网络。覆盖（BENCH_PLAN W2 验收）：
- scene_anchor 解析（第N天[时段] @地点[→地点2]，含畸形锚点）
- 账本构建：天/时段/地点/角色在场（含同名消歧、括号限定名）
- 声明式 delta：死亡 / 持有物 → alive_at / holder_of 查询
- 时间线单调性：正例无违规 / 负例（天倒退、同日时段倒退）命中
- diff 可读、确定性、JSON 往返、空账本边界
- 真相表解析：表格行 / false 标记（false|假|错）/ 畸形行容错

用法:
    python3 tests/test_ledger.py
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bench.state.ledger import (
    parse_anchor, build_ledger, StateLedger, ChapterState,
    cast_base_names, base_name,
)
from bench.state.truth_table import load_truth_table, TruthFact

_PASS, _FAIL = 0, 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  FAIL: {name}")


# ── fixtures ──────────────────────────────────────────────────────────

def spec(chapter, anchors, desc="", delta=None):
    s = {
        "novel": "T", "title": f"第{chapter}章 T", "chapter": chapter,
        "sections": [
            {"id": str(i + 1), "subject": f"s{i+1}", "scene_anchor": a,
             "description": desc if i == 0 else ""}
            for i, a in enumerate(anchors)
        ],
    }
    if delta:
        s["state_delta"] = delta
    return s


TRUTH_MD = """# 真相表

> 注记文字。

| id    | 类别     | 命题 |
|-------|----------|------|
| T-01  | 世界真相 | 大崩解是零缓冲优化崩的 |
| T-07  | 角色真相 | 林汐的直觉是引擎泄漏 |
| F-01  | 世界真相 | 末世是战争炸毁的 | false |
| F-02  | 世界真相 | 好运是神迹 | 假 |
| F-03  | 世界真相 | 主脑只有三神 | 错 |
| 畸形行 | 只有两列 |
"""


# ── scene_anchor 解析 ─────────────────────────────────────────────────

def test_parse_anchor():
    check("标准锚点", parse_anchor("第1天上午 @残岸·垃圾场东边纸板堆")
          == (1, "上午", ["残岸·垃圾场东边纸板堆"]))
    check("无时段", parse_anchor("第2天 @残岸·铁架区") == (2, None, ["残岸·铁架区"]))
    check("箭头地点拆两条", parse_anchor("第1天黄昏 @残岸·江边→铁皮棚")
          == (1, "黄昏", ["残岸·江边", "铁皮棚"]))
    check("下午识别", parse_anchor("第2天下午 @A") == (2, "下午", ["A"]))
    check("深夜识别", parse_anchor("第3天深夜 @Y") == (3, "深夜", ["Y"]))
    check("畸形锚点不崩", parse_anchor("无锚点") == (None, None, []))
    check("空锚点不崩", parse_anchor("") == (None, None, []))


# ── 账本构建 ──────────────────────────────────────────────────────────

def test_build_ledger():
    specs = [
        spec(1, ["第1天上午 @残岸·纸板堆", "第1天中午 @残岸·废品摊",
                 "第1天黄昏 @残岸·江边→铁皮棚"]),
        spec(2, ["第2天上午 @残岸·铁架区"]),
    ]
    led = build_ledger("T", specs, cast_names=["林汐", "老赵"])
    check("章数", len(led.chapters) == 2)
    c1 = led.chapter(1)
    check("ch1 天", c1.day == 1)
    check("ch1 时段序列", c1.slots == ["上午", "中午", "黄昏"])
    check("ch1 地点", c1.locations == ["残岸·纸板堆", "残岸·废品摊",
                                       "残岸·江边", "铁皮棚"])
    check("ch2 天", led.chapter(2).day == 2)


def test_characters_present():
    # 复合名 + 独立名同时出现 → 两人都在场（真在场），按 cast 声明顺序
    specs = [spec(1, ["第1天上午 @A"], desc="林汐的朋友来了，林汐跟在后面。")]
    led = build_ledger("T", specs, cast_names=["林汐", "林汐的朋友"])
    check("复合名与独立名同时在场",
          led.chapter(1).characters == ["林汐", "林汐的朋友"])

    # 只有复合名出现 → 不把 "林汐" 单独记一次（最长名优先掩码）
    specs2 = [spec(1, ["第1天上午 @A"], desc="林汐的朋友递来半块饼。")]
    led2 = build_ledger("T", specs2, cast_names=["林汐", "林汐的朋友"])
    check("只出现复合名时不重复计林汐",
          led2.chapter(1).characters == ["林汐的朋友"])

    specs3 = [spec(1, ["第1天上午 @A"], desc="林汐蹲下。")]
    led3 = build_ledger("T", specs3, cast_names=["林汐", "林汐的朋友"])
    check("只提林汐时记林汐", led3.chapter(1).characters == ["林汐"])

    # 括号限定名：cast 里 "明处朋友（名待定）" 用基名匹配
    specs4 = [spec(1, ["第1天上午 @A"], desc="明处朋友递过来半块饼。")]
    led4 = build_ledger("T", specs4, cast_names=["林汐", "明处朋友（名待定）"])
    check("括号限定名基名匹配",
          led4.chapter(1).characters == ["明处朋友（名待定）"])
    check("cast_base_names 保留声明名（显示用）",
          cast_base_names({"cast": ["明处朋友（名待定）", "林汐"]})
          == ["明处朋友（名待定）", "林汐"])
    check("base_name 去括号（匹配用）",
          base_name("明处朋友（名待定）") == "明处朋友")
    check("base_name 无括号原样", base_name("林汐") == "林汐")

    # 主角回填：spec 只用「她」不写名字时，有场景锚点即视为在场
    specs5 = [spec(1, ["第1天上午 @A"], desc="她蹲进凹陷，膝头往上一顶。")]
    led5 = build_ledger("T", specs5, cast_names=["林汐"], protagonist="林汐")
    check("主角回填在场", led5.chapter(1).characters == ["林汐"])
    # 无场景锚点则不回填
    specs6 = [{"novel": "T", "title": "x", "chapter": 1, "sections": [
        {"id": "1", "subject": "s", "description": "她蹲下。"}]}]
    led6 = build_ledger("T", specs6, cast_names=["林汐"], protagonist="林汐")
    check("无场景锚点不回填", led6.chapter(1).characters == [])


def test_declared_delta():
    specs = [
        spec(1, ["第1天上午 @A"]),
        spec(2, ["第2天上午 @B"], delta={"deaths": ["老赵"],
                                        "items": {"旧终端": "林汐"}}),
    ]
    led = build_ledger("T", specs, cast_names=["林汐", "老赵"])
    check("ch1 老赵在世", "老赵" in led.alive_at(1))
    check("ch2 老赵已死", "老赵" not in led.alive_at(2))
    check("ch1 未持旧终端", led.holder_of("旧终端", 1) is None)
    check("ch2 林汐持旧终端", led.holder_of("旧终端", 2) == "林汐")
    check("未知物品返回 None", led.holder_of("不存在", 2) is None)


# ── 时间线单调性 ──────────────────────────────────────────────────────

def test_timeline_monotonic():
    ok = build_ledger("T", [
        spec(1, ["第1天上午 @A", "第1天黄昏 @B"]),
        spec(2, ["第2天上午 @C"]),
    ], cast_names=[])
    check("正例：天递增无违规", ok.timeline_violations() == [])

    ok2 = build_ledger("T", [
        spec(1, ["第1天上午 @A"]),
        spec(2, ["第1天黄昏 @B"]),
    ], cast_names=[])
    check("正例：同日时段递增无违规", ok2.timeline_violations() == [])

    bad = build_ledger("T", [
        spec(1, ["第2天上午 @A"]),
        spec(2, ["第1天上午 @B"]),
    ], cast_names=[])
    v = bad.timeline_violations()
    check("反例：天倒退命中", len(v) == 1 and "第2章" in v[0])

    bad2 = build_ledger("T", [
        spec(1, ["第1天黄昏 @A"]),
        spec(2, ["第1天上午 @B"]),
    ], cast_names=[])
    v2 = bad2.timeline_violations()
    check("反例：同日时段倒退命中", len(v2) == 1 and "第2章" in v2[0])

    check("空账本无违规", build_ledger("T", [], cast_names=[]).timeline_violations() == [])


# ── diff / 确定性 / 往返 ──────────────────────────────────────────────

def test_diff_and_determinism():
    a = build_ledger("T", [spec(1, ["第1天上午 @A"])], cast_names=["林汐"])
    b = build_ledger("T", [spec(1, ["第3天上午 @A"])], cast_names=["林汐"])
    d = a.diff(b)
    check("diff 可读且含章号", len(d) == 1 and "第1章" in d[0] and "天" in d[0])
    check("自 diff 为空", a.diff(a) == [])

    check("确定性：两次 to_dict 相等", a.to_dict() == a.to_dict())
    rt = StateLedger.from_dict(a.to_dict())
    check("往返一致", rt.to_dict() == a.to_dict())

    c = build_ledger("T", [spec(1, ["第1天上午 @A"], desc="林汐蹲下。")],
                     cast_names=["林汐"])
    d2 = a.diff(c)
    check("角色差异可读", any("角色" in line for line in d2))


# ── 真相表解析 ────────────────────────────────────────────────────────

def test_truth_table(tmpdir=None):
    import tempfile
    from pathlib import Path
    p = Path(tempfile.mkdtemp()) / "真相表.md"
    p.write_text(TRUTH_MD, encoding="utf-8")
    facts = load_truth_table(p)
    check("跳表头/分隔行/畸形行，得 5 条", len(facts) == 5)
    by_id = {f.id: f for f in facts}
    check("T-01 命题保留", by_id["T-01"].statement == "大崩解是零缓冲优化崩的")
    check("T-01 非 false", by_id["T-01"].is_false is False)
    check("F-01 false 标记", by_id["F-01"].is_false is True)
    check("F-02 「假」识别", by_id["F-02"].is_false is True)
    check("F-03 「错」识别", by_id["F-03"].is_false is True)
    check("类别保留", by_id["T-07"].category == "角色真相")
    check("缺文件返回空", load_truth_table(p.parent / "不存在.md") == [])


if __name__ == "__main__":
    test_parse_anchor()
    test_build_ledger()
    test_characters_present()
    test_declared_delta()
    test_timeline_monotonic()
    test_diff_and_determinism()
    test_truth_table()
    print(f"\n结果: {_PASS}/{_PASS + _FAIL} 通过")
    sys.exit(1 if _FAIL else 0)