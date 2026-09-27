#!/usr/bin/env python3
"""
test_universe.py — W6 合成宇宙生成器单元测试。

零 LLM / 零网络。覆盖（BENCH_PLAN W6 验收）：
- 确定性：同 seed 逐字节相同（to_dict + 落盘文件）
- 区分度：不同 seed 出不同宇宙
- 弧结构：章数/节数/锚点/weight（每章一个 expanded）
- 知识分配无矛盾：终局真相不早于末章；引用事实存在；章号在范围内
- 真相表落盘可被 W2 解析器往返（含 false 行）
- 时间线单调（账本无违规）
- **集成正例**：宇宙 → 账本 → 状态判定，死亡角色在后续章活动 → 命中违反

用法:
    python3 tests/test_universe.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bench.universe.generator import (
    generate, write_universe, universe_to_dict, invariants,
)
from bench.state.truth_table import load_truth_table
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


# ── 确定性 ────────────────────────────────────────────────────────────

def test_determinism():
    a = generate(seed=42)
    b = generate(seed=42)
    check("同 seed to_dict 相等", universe_to_dict(a) == universe_to_dict(b))

    d1, d2 = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    write_universe(a, d1)
    write_universe(b, d2)
    files1 = sorted(p.relative_to(d1) for p in d1.rglob("*") if p.is_file())
    files2 = sorted(p.relative_to(d2) for p in d2.rglob("*") if p.is_file())
    check("落盘文件集合相同", files1 == files2)
    same = all((d1 / f).read_bytes() == (d2 / f).read_bytes() for f in files1)
    check("同 seed 逐字节相同", same)

    c = generate(seed=43)
    check("不同 seed 出不同宇宙", universe_to_dict(a) != universe_to_dict(c))


# ── 弧结构 ────────────────────────────────────────────────────────────

def test_arc_structure():
    u = generate(seed=7, chapters=12)
    check("题名非空", bool(u.title))
    check("cast ≥3 且含主角", len(u.cast) >= 3 and u.protagonist == u.cast[0]["name"])
    check("specs 章数正确", len(u.specs) == 12)
    for i, sp in enumerate(u.specs, 1):
        if sp["chapter"] != i:
            check(f"章号连续 {i}", False)
            break
        secs = sp["sections"]
        if not secs or not all(s.get("scene_anchor") for s in secs):
            check(f"第{i}章每节有锚点", False)
            break
        if sum(1 for s in secs if s.get("weight") == "expanded") != 1:
            check(f"第{i}章恰一个 expanded", False)
            break
    else:
        check("章号连续/锚点齐全/每章一个 expanded", True)
    check("arcs 分幕", len(u.arcs) >= 2 and sum(len(a["chapters"]) for a in u.arcs) == 12)


# ── 知识分配 ──────────────────────────────────────────────────────────

def test_knowledge_soundness():
    u = generate(seed=11, chapters=12)
    fact_ids = {f["id"] for f in u.truth_table}
    check("事实 ≥8 条", len(u.truth_table) >= 8)
    check("含 false 行（B 型反转素材）",
          any(f["is_false"] for f in u.truth_table))

    terminal = u.terminal_fact_id
    check("终局真相在事实表中", terminal in fact_ids)
    # 终局真相不得早于末章被任何人知道
    for ch, entries in u.knowledge.items():
        for e in entries:
            if e["fact_id"] == terminal:
                check(f"{ch} 终局真相不早于末章", e["learned_chapter"] >= u.chapters)
            if e["fact_id"] not in fact_ids:
                check(f"{ch} 知识引用存在", False)
            if not (1 <= e["learned_chapter"] <= u.chapters):
                check(f"{ch} 学习章号在范围内", False)
    check("知识条目非空", any(u.knowledge.values()))
    # 至少一条 held-out（谁都不学）用于腐坏/边界探针
    learned = {e["fact_id"] for entries in u.knowledge.values() for e in entries}
    check("存在 held-out 事实", len(fact_ids - learned) >= 1)


# ── 宇宙自洽不变量（多 seed） ──────────────────────────────────────────

def test_invariants():
    # 死-学矛盾（验收时抓到的真 bug 类）：死者不得在死后获知任何事实
    for seed in (3, 7, 11, 42, 20260923):
        for ch in (6, 12):
            u = generate(seed=seed, chapters=ch)
            bad = invariants(u)
            if bad:
                check(f"seed={seed} ch={ch} 自洽", False)
                for b in bad:
                    print(f"      · {b}")
                return
    check("多 seed 宇宙全部自洽（含死者不后学）", True)

    # 阴性对照：证明不变量不是空转——人为注入矛盾必须被抓到
    u = generate(seed=3, chapters=8)
    dead = None
    for sp in u.specs:
        if (sp.get("state_delta") or {}).get("deaths"):
            dead = sp["state_delta"]["deaths"][0]
            dch = sp["chapter"]
            break
    check("对照前置：宇宙确有死亡声明", dead is not None)
    u.knowledge[dead].append({"fact_id": u.terminal_fact_id,
                              "learned_chapter": u.chapters})
    bad = invariants(u)
    check("阴性对照：死后获知被抓到",
          any("死后仍获知" in b for b in bad))
    # 另一个对照：提前获知终局
    u2 = generate(seed=3, chapters=8)
    u2.knowledge[u2.protagonist][-1]["learned_chapter"] = 1
    check("阴性对照：提前获知终局被抓到",
          any("提前获知终局" in b for b in invariants(u2)))

    # 另一个对照：死者被写进死后章节的描述（真客户端验收时抓到的类）
    u3 = generate(seed=3, chapters=8)
    dead3, dch3 = None, None
    for sp in u3.specs:
        if (sp.get("state_delta") or {}).get("deaths"):
            dead3 = sp["state_delta"]["deaths"][0]
            dch3 = sp["chapter"]
            break
    check("对照前置：确有死亡声明", dead3 is not None)
    check("正例：死者不出现在死后章节描述",
          not any(dead3 in (sec.get("description") or "")
                  for sp in u3.specs if sp["chapter"] > dch3
                  for sec in sp["sections"]))
    for sp in u3.specs:
        if sp["chapter"] == dch3 + 1:
            sp["sections"][0]["description"] += f" {dead3}走过来。"
            break
    check("阴性对照：死后被点名被抓到",
          any("被当作行动者点名" in b for b in invariants(u3)))


# ── 落盘 ↔ 解析往返 ───────────────────────────────────────────────────

def test_persistence_roundtrip():
    u = generate(seed=5, chapters=6)
    d = Path(tempfile.mkdtemp())
    paths = write_universe(u, d)
    check("specs 落盘", (d / "specs" / "ch1.json").exists())
    check("novel_config 落盘", (d / "novel_config.json").exists())

    facts = load_truth_table(paths["truth_table"])
    check("真相表条数往返", len(facts) == len(u.truth_table))
    by_id = {f.id: f for f in facts}
    for rec in u.truth_table:
        f = by_id.get(rec["id"])
        if f is None or f.statement != rec["statement"] or f.is_false != rec["is_false"]:
            check(f"真相表往返一致 {rec['id']}", False)
            break
    else:
        check("真相表往返一致", True)

    cfg = json.loads((d / "novel_config.json").read_text(encoding="utf-8"))
    check("config cast 与宇宙一致",
          [c["name"] for c in cfg["cast"]] == [c["name"] for c in u.cast])


# ── 集成：宇宙 → 账本 → 状态判定正例 ──────────────────────────────────

def test_integration_positive():
    u = generate(seed=3, chapters=8)
    cast = [c["name"] for c in u.cast]
    led = build_ledger(u.title, u.specs, cast, protagonist=u.protagonist)

    check("生成宇宙账本时间线无违规", led.timeline_violations() == [])

    dead_name = None
    dead_ch = None
    for c in led.chapters:
        if c.deaths:
            dead_name, dead_ch = c.deaths[0], c.chapter
            break
    check("宇宙声明了死亡", dead_name is not None)

    # 死亡角色的名字出现在其后章节正文 → 应命中
    text = f"{dead_name}从雾里走出来，把绳子甩在船桩上。"
    vs = state_judge(text, led, chapter=dead_ch + 1, run_id="universe-int")
    hits = [v for v in vs if "dead" in v.probe_id]
    check("集成正例：死人复活命中", len(hits) == 1 and dead_name in hits[0].evidence["span"])
    # 未声明死亡的章节不命中
    vs0 = state_judge(text, led, chapter=dead_ch, run_id="universe-int")
    check("死亡当章不命中", [v for v in vs0 if "dead" in v.probe_id] == [])


if __name__ == "__main__":
    test_determinism()
    test_arc_structure()
    test_knowledge_soundness()
    test_invariants()
    test_persistence_roundtrip()
    test_integration_positive()
    print(f"\n结果: {_PASS}/{_PASS + _FAIL} 通过")
    sys.exit(1 if _FAIL else 0)