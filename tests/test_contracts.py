#!/usr/bin/env python3
"""
test_contracts.py — W0 数据契约单元测试。

零 LLM / 零网络。覆盖（BENCH_PLAN W0 验收）：
- 序列化 ↔ 反序列化往返（四 schema）
- 必填缺失报错（报字段名）
- 枚举封闭（未知值报错，报允许集）
- 版本字段必填
- confidence 边界 [0,1]
- schema 导出完整性

用法:
    python3 tests/test_contracts.py
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bench.contracts import (
    BenchVersion, ProbeSpec, Violation, RunManifest,
    ViolationType, DetectorKind, HarnessTier, Severity,
    ContractError, export_schemas,
)

_PASS, _FAIL = 0, 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  FAIL: {name}")


def raises_contract(fn, *substr):
    """期望 ContractError，且 message 含全部子串。"""
    try:
        fn()
    except ContractError as e:
        return all(s in str(e) for s in substr)
    except Exception as e:
        print(f"    (raised wrong type: {type(e).__name__}: {e})")
        return False
    return False


# ── fixtures ──────────────────────────────────────────────────────────

def make_bench() -> dict:
    return {
        "bench_version": "0.1.0",
        "version_name": "CanonBench",
        "universe_generator_version": "u0.1.0",
        "judge_mechanical_version": "m0.1.0",
        "judge_semantic_model": "deepseek-v4-flash",
        "judge_semantic_prompt_version": "p0.1.0",
        "universe_seed": 20260922,
    }


def make_probe() -> dict:
    return {
        "probe_id": "kb-ch03-charX-T08",
        "type": "knowledge_boundary",
        "chapter": 3,
        "payload": {
            "character": "charX",
            "checklist": ["不该知道终局真相"],
            "known": ["第1章的事实A", "第2章的事实B"],
        },
    }


def make_violation() -> dict:
    return {
        "probe_id": "kb-ch03-charX-T08",
        "type": "knowledge_boundary",
        "detector": "semantic",
        "chapter": 3,
        "severity": "high",
        "evidence": {"span": "她说出了终局", "expected": "未知", "actual": "已知"},
        "run_id": "run-001",
        "confidence": 0.86,
        "note": "",
    }


def make_manifest() -> dict:
    return {
        "run_id": "run-001",
        "bench": make_bench(),
        "model": "kimi-k2.6",
        "provider": "openrouter",
        "temperature": 0.85,
        "max_tokens": 32768,
        "harness": "full",
        "k_index": 0,
        "prompt_hashes": {"1": "ab12", "2": "cd34"},
        "generated_at": None,
        "cost": {"tokens_in": 1000, "tokens_out": 2000},
        "chapter_paths": {"1": "chapters/drafts/第1章.md"},
        "notes": "",
    }


# ── 往返 ──────────────────────────────────────────────────────────────

def test_roundtrip():
    for cls, fixture, name in [
        (BenchVersion, make_bench(), "BenchVersion"),
        (ProbeSpec, make_probe(), "ProbeSpec"),
        (Violation, make_violation(), "Violation"),
        (RunManifest, make_manifest(), "RunManifest"),
    ]:
        obj = cls.from_dict(fixture)
        out = obj.to_dict()
        check(f"{name} roundtrip", out == fixture)
        # 枚举字段往返后仍是枚举实例
        if name == "ProbeSpec":
            check("ProbeSpec.type is enum", isinstance(obj.type, ViolationType))
        if name == "Violation":
            check("Violation.enums are enum",
                  isinstance(obj.type, ViolationType)
                  and isinstance(obj.detector, DetectorKind)
                  and isinstance(obj.severity, Severity))
        if name == "RunManifest":
            check("RunManifest.harness is enum", isinstance(obj.harness, HarnessTier))
            check("RunManifest.bench nested", obj.bench.bench_version == "0.1.0")


# ── 必填缺失 ──────────────────────────────────────────────────────────

def test_missing_required():
    b = make_bench(); b.pop("bench_version")
    check("BenchVersion 缺版本报错含字段名", raises_contract(lambda: BenchVersion.from_dict(b), "bench_version"))

    p = make_probe(); p.pop("probe_id")
    check("ProbeSpec 缺 probe_id 报错", raises_contract(lambda: ProbeSpec.from_dict(p), "probe_id"))

    v = make_violation(); v.pop("evidence")
    check("Violation 缺 evidence 报错", raises_contract(lambda: Violation.from_dict(v), "evidence"))

    m = make_manifest(); m.pop("run_id")
    check("RunManifest 缺 run_id 报错", raises_contract(lambda: RunManifest.from_dict(m), "run_id"))

    m2 = make_manifest(); m2["bench"] = None
    check("RunManifest bench=null 报错", raises_contract(lambda: RunManifest.from_dict(m2), "bench"))


# ── 枚举封闭 ──────────────────────────────────────────────────────────

def test_closed_enums():
    p = make_probe(); p["type"] = "telepathy"
    check("未知 ViolationType 报错+允许集", raises_contract(
        lambda: ProbeSpec.from_dict(p), "type", "telepathy", "knowledge_boundary"))

    v = make_violation(); v["severity"] = "apocalyptic"
    check("未知 Severity 报错", raises_contract(
        lambda: Violation.from_dict(v), "severity", "apocalyptic"))

    v2 = make_violation(); v2["detector"] = 42
    check("detector 非字符串报错", raises_contract(
        lambda: Violation.from_dict(v2), "detector"))


# ── confidence 边界 ───────────────────────────────────────────────────

def test_confidence_bounds():
    v = make_violation()
    v["confidence"] = 1.5
    check("confidence>1 报错", raises_contract(
        lambda: Violation.from_dict(v), "confidence", "1.5"))
    v["confidence"] = -0.1
    check("confidence<0 报错", raises_contract(
        lambda: Violation.from_dict(v), "confidence", "-0.1"))
    v["confidence"] = 0.0
    check("confidence=0 合法", Violation.from_dict(v).confidence == 0.0)
    v.pop("confidence")
    check("confidence 缺省=1.0（机械层）", Violation.from_dict(v).confidence == 1.0)


# ── 版本必填 ──────────────────────────────────────────────────────────

def test_version_required():
    m = make_manifest()
    m["bench"] = {k: val for k, val in m["bench"].items() if k != "bench_version"}
    check("嵌套 bench 缺 bench_version 报错", raises_contract(
        lambda: RunManifest.from_dict(m), "bench_version"))


# ── schema 导出 ───────────────────────────────────────────────────────

def test_export_schemas():
    s = export_schemas()
    check("四 schema 齐全",
          set(s.keys()) == {"BenchVersion", "ProbeSpec", "Violation", "RunManifest"})
    check("Violation required 含 evidence", "evidence" in s["Violation"]["required"])
    check("RunManifest required 含 harness", "harness" in s["RunManifest"]["required"])
    check("BenchVersion universe_seed 可选（有默认值）",
          "universe_seed" in s["BenchVersion"]["properties"]
          and "universe_seed" not in s["BenchVersion"]["required"])
    # required 与 properties 一致性：required ⊆ properties
    for name, sc in s.items():
        check(f"{name} required⊆properties",
              set(sc["required"]).issubset(set(sc["properties"].keys())))


if __name__ == "__main__":
    test_roundtrip()
    test_missing_required()
    test_closed_enums()
    test_confidence_bounds()
    test_version_required()
    test_export_schemas()
    print(f"\n结果: {_PASS}/{_PASS + _FAIL} 通过")
    sys.exit(1 if _FAIL else 0)