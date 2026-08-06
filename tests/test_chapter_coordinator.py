#!/usr/bin/env python3
"""
test_chapter_coordinator.py — 顶层协调器单元测试。

无 LLM / 无 I/O（除 tempdir 内容包）。覆盖：
- get_pipeline_mode 优先级链（cli > arc > override > default > gen）
- deterministic_tick shape
- gen/engine/hybrid 三路分发（mock runner）
- 依赖注入 stub
- gen 模式知识推进 + 落盘（tempdir）
- 无真相表时 spec 原样（行为降级）
- 活内容包配置解析

用法:
    python3 tests/test_chapter_coordinator.py
"""

import json
import os
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from framework import novel_config as nc
from framework.chapter_coordinator import (
    ChapterCoordinator, deterministic_tick,
)
from framework.vault_sync import MemoryStore

FACTS_MD = """| id   | 类别     | 命题 |
|------|----------|------|
| T-01 | 世界真相 | 静默号二十年前最后一条日志声称发现可证伪的文明遗迹，随后全员失联 |
| T-02 | 世界真相 | 残骸是数据层一段可自我改写的程序，会按观察者语言习惯生成档案 |
| T-03 | 世界真相 | 静默号船员仍活着并正常作息 | false |
"""


def make_novel(base: str, tom=True):
    """在 tempdir 搭最小内容包。返回 novel_dir 字符串。"""
    novel_dir = os.path.join(base, "novel")
    os.makedirs(os.path.join(novel_dir, "bible"), exist_ok=True)
    os.makedirs(os.path.join(novel_dir, "vault"), exist_ok=True)
    config = {
        "title": "测试",
        "protagonist": "luli",
        "protagonist_pronoun": "他",
        "cast": [{"key": "luli", "name": "陆离", "tier": 1,
                  "state_key": "luli_state"}],
        "pipeline": {"default_mode": "gen"},
    }
    if tom:
        config["theory_of_mind"] = {"enabled": True, "truth_table": "真相表.md"}
    with open(os.path.join(novel_dir, "novel_config.json"), "w",
              encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False)
    if tom:
        with open(os.path.join(novel_dir, "bible", "真相表.md"), "w",
                  encoding="utf-8") as f:
            f.write(FACTS_MD)
    return novel_dir


def make_hand_spec(novel_dir, chapter=1):
    """手写 spec：description 含 T-01 实词，供知识推进测试。"""
    spec = {
        "novel": "测试",
        "chapter": chapter,
        "title": f"第{chapter}章",
        "target_chars": 2400,
        "sections": [
            {"id": "一", "subject": "发现", "weight": "normal",
             "scene_anchor": "第1天 @舱内",
             "description": "静默号二十年前发现文明遗迹后全员失联。",
             "tension_direction": "慢节奏铺垫"},
            {"id": "二", "subject": "档案", "weight": "expanded",
             "scene_anchor": "第1天 @档案室",
             "description": "残骸能生成档案。",
             "tension_direction": "信息揭示", "expanded_direction": "展开档案细节",
             "target_words": 700},
            {"id": "三", "subject": "收尾", "weight": "normal",
             "scene_anchor": "第1天 @舱内",
             "description": "他带着档案回到住处。",
             "tension_direction": "松弛后反弹"},
        ],
    }
    specs_dir = os.path.join(novel_dir, "specs")
    os.makedirs(specs_dir, exist_ok=True)
    path = os.path.join(specs_dir, f"ch{chapter}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False)
    return path


# ── 管线模式优先级 ─────────────────────────────────────────────────

def test_get_pipeline_mode_priority_chain():
    config = {"pipeline": {"default_mode": "gen",
                           "chapter_overrides": {"5": "engine"}}}
    arc = {"pipeline_mode": "hybrid"}
    assert nc.get_pipeline_mode(config, 5, None, "gen") == "gen"          # cli 最高
    assert nc.get_pipeline_mode(config, 5, arc, None) == "hybrid"         # arc > override
    assert nc.get_pipeline_mode(config, 5, None, None) == "engine"        # override > default
    assert nc.get_pipeline_mode(config, 6, None, None) == "gen"           # default


def test_get_pipeline_mode_fallback_gen():
    assert nc.get_pipeline_mode({}, 3) == "gen"                            # 无配置
    assert nc.get_pipeline_mode({"pipeline": {"default_mode": "bogus"}}, 3) == "gen"


def test_get_pipeline_mode_invalid_cli_falls_back():
    config = {"pipeline": {"default_mode": "engine"}}
    # 非法 cli 值 → 不挡，落回 default
    assert nc.get_pipeline_mode(config, 3, None, "bogus") == "engine"


# ── deterministic_tick ──────────────────────────────────────────────

def test_deterministic_tick_shape():
    tick = deterministic_tick(5, {"arc_id": "测试弧", "chapters": [4, 5, 6]},
                              novel_dir="/x/novels/静默轨道")
    for key in ("character_trajectories", "suggested_chapter_split", "arc_id"):
        assert key in tick, f"missing {key}"
    assert tick["arc_id"] == "测试弧"
    assert tick["suggested_chapter_split"][0]["chapter_num"] == 5
    assert tick["type_a_events"] == []
    assert tick["novel"] == "静默轨道"


# ── 三路分发 ───────────────────────────────────────────────────────

def test_gen_mode_annotates_and_dispatches():
    with tempfile.TemporaryDirectory() as tmp:
        novel_dir = make_novel(tmp)
        spec_path = make_hand_spec(novel_dir)
        captured = {}

        def stub_pipeline(spec, novel_dir=None, force=False, spec_path=None,
                          output_path=None):
            captured["spec"] = spec
            captured["out"] = output_path
            return "TEXT"

        coord = ChapterCoordinator(novel_dir, pipeline_runner=stub_pipeline,
                                   engine_runner=lambda ch, arc: None)
        res = coord.run(1, spec_path=spec_path)
        assert res.mode == "gen"
        assert "info_gaps" in captured["spec"]                              # 已标注
        assert captured["spec"]["info_gaps"]["per_character"][0]["name"] == "陆离"
        assert captured["spec"]["info_gaps"]["type_a_candidates"]           # 有 A 型候选
        assert res.text == "TEXT"
        assert res.output_path.endswith("chapters/第1章.md")
        # 手写 spec 未被覆盖（annotate 不改原文件）
        with open(spec_path, encoding="utf-8") as f:
            assert "info_gaps" not in json.load(f)


def test_engine_mode_dispatches_tick_only():
    with tempfile.TemporaryDirectory() as tmp:
        novel_dir = make_novel(tmp)
        calls = {"engine": 0, "pipeline": 0}
        tick = deterministic_tick(3, {"arc_id": "引擎弧"})

        def stub_engine(ch, arc):
            calls["engine"] += 1
            return tick

        def stub_pipeline(*a, **k):
            calls["pipeline"] += 1
            return ""

        coord = ChapterCoordinator(novel_dir, engine_runner=stub_engine,
                                   pipeline_runner=stub_pipeline)
        res = coord.run(3, mode="engine")
        assert res.mode == "engine"
        assert calls["engine"] == 1 and calls["pipeline"] == 0
        assert res.tick_path and os.path.exists(res.tick_path)
        assert res.output_path is None
        assert res.type_a_events == []


def test_hybrid_mode_dispatches_all():
    with tempfile.TemporaryDirectory() as tmp:
        novel_dir = make_novel(tmp)
        tick = deterministic_tick(2, {"arc_id": "h"})
        captured = {}

        def stub_pipeline(spec, novel_dir=None, force=False, spec_path=None,
                          output_path=None):
            captured["spec"] = spec
            return "HYBRID"

        coord = ChapterCoordinator(novel_dir, engine_runner=lambda ch, arc: tick,
                                   pipeline_runner=stub_pipeline)
        res = coord.run(2, mode="hybrid")
        assert res.mode == "hybrid"
        assert captured["spec"]["novel"] == "novel"                          # 机械 spec
        assert "info_gaps" in captured["spec"]
        assert res.tick_path and res.spec_path and res.output_path


def test_injected_stubs_respected():
    with tempfile.TemporaryDirectory() as tmp:
        novel_dir = make_novel(tmp)
        log = {"engine": 0, "spec": 0, "pipeline": 0}

        def stub_engine(ch, arc):
            log["engine"] += 1
            return deterministic_tick(ch, {})

        def stub_spec(tick, ch, novel):
            log["spec"] += 1
            return {"novel": novel, "chapter": ch, "title": f"第{ch}章",
                    "sections": [{"id": "一", "subject": "s", "description": "x" * 30,
                                  "tension_direction": "d", "weight": "normal",
                                  "scene_anchor": "第1天 @x"}],
                    "target_chars": 1000}

        def stub_pipeline(spec, novel_dir=None, force=False, spec_path=None,
                          output_path=None):
            log["pipeline"] += 1
            return "T"

        coord = ChapterCoordinator(novel_dir, engine_runner=stub_engine,
                                   spec_builder_fn=stub_spec,
                                   pipeline_runner=stub_pipeline)
        coord.run(2, mode="hybrid")
        assert log == {"engine": 1, "spec": 1, "pipeline": 1}


def test_gen_mode_requires_spec_path():
    with tempfile.TemporaryDirectory() as tmp:
        novel_dir = make_novel(tmp)
        coord = ChapterCoordinator(novel_dir, engine_runner=lambda ch, arc: None,
                                   pipeline_runner=lambda *a, **k: "")
        try:
            coord.run(1)
            assert False, "应抛 ValueError"
        except ValueError:
            pass


# ── 知识推进 + 落盘 ────────────────────────────────────────────────

def test_gen_mode_knowledge_persists():
    with tempfile.TemporaryDirectory() as tmp:
        novel_dir = make_novel(tmp)
        spec_path = make_hand_spec(novel_dir)
        mem = MemoryStore(os.path.join(novel_dir, "vault"))
        coord = ChapterCoordinator(novel_dir, memory_store=mem,
                                   engine_runner=lambda ch, arc: None,
                                   pipeline_runner=lambda *a, **k: "T")
        coord.run(1, spec_path=spec_path)
        saved = mem.load_agent_state("luli")
        assert "T-01" in saved["knowledge"]        # description 含 T-01 实词 → 已确认
        assert "T-02" in saved["knowledge"]        # 「残骸能生成档案」→ T-02
        assert "T-03" not in saved["knowledge"]    # truth=False 不写 knowledge


# ── 无真相表降级 ───────────────────────────────────────────────────

def test_no_truth_table_spec_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        novel_dir = make_novel(tmp, tom=False)
        spec_path = make_hand_spec(novel_dir)
        captured = {}

        def stub_pipeline(spec, novel_dir=None, force=False, spec_path=None,
                          output_path=None):
            captured["spec"] = spec
            return "T"

        coord = ChapterCoordinator(novel_dir, pipeline_runner=stub_pipeline,
                                   engine_runner=lambda ch, arc: None)
        coord.run(1, spec_path=spec_path)
        assert "info_gaps" not in captured["spec"]   # 行为与 v0.1.x 一致


# ── 活内容包配置 ───────────────────────────────────────────────────

def test_live_config_resolution():
    novel_dir = os.path.join(PROJECT_ROOT, "novels", "静默轨道")
    config = nc.load(novel_dir)
    assert nc.get_pipeline_mode(config, 5) == "gen"
    assert nc.get_tom_enabled(config) is True
    assert nc.get_truth_table_file(config) == "真相表.md"


if __name__ == "__main__":
    print("=" * 50)
    print("  顶层协调器单元测试")
    print("=" * 50)
    print()

    tests = [
        test_get_pipeline_mode_priority_chain,
        test_get_pipeline_mode_fallback_gen,
        test_get_pipeline_mode_invalid_cli_falls_back,
        test_deterministic_tick_shape,
        test_gen_mode_annotates_and_dispatches,
        test_engine_mode_dispatches_tick_only,
        test_hybrid_mode_dispatches_all,
        test_injected_stubs_respected,
        test_gen_mode_requires_spec_path,
        test_gen_mode_knowledge_persists,
        test_no_truth_table_spec_unchanged,
        test_live_config_resolution,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print()
    print(f"结果: {passed}/{len(tests)} 通过", end="")
    if failed:
        print(f", {failed} 失败", end="")
    print()
    sys.exit(0 if failed == 0 else 1)
