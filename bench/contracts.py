#!/usr/bin/env python3
"""bench.contracts — LCB 数据契约（W0）。

一切下游模块只通过本模块的 schema 交换数据。
设计约束（docs/BENCH_PLAN.md §一.5）：
- 纯数据 + 纯校验，零 LLM / 零网络 / 零第三方依赖
- 枚举封闭：未知值在 from_dict 处报错，不静默
- 版本字段必填：任何数字可追到一次运行（可复现原则）
"""

import json
from dataclasses import dataclass, field, asdict, fields, MISSING
from enum import Enum
from typing import Optional


class ContractError(ValueError):
    """契约校验失败。message 必含字段名，便于定位。"""


class ViolationType(str, Enum):
    """违反类型（探针四类，封闭枚举）。"""
    CONSTRAINT = "constraint"          # 禁词/篇幅/人称/POV
    STATE = "state"                    # 死人复活/物品瞬移/时间倒流
    KNOWLEDGE_BOUNDARY = "knowledge_boundary"
    CONTEXT_ROT = "context_rot"


class DetectorKind(str, Enum):
    """判定器种类。"""
    MECHANICAL = "mechanical"
    SEMANTIC = "semantic"


class HarnessTier(str, Enum):
    """harness 三档消融（BENCH_PLAN §三）。"""
    BARE = "bare"
    MID = "mid"
    FULL = "full"


class Severity(str, Enum):
    """违反严重度。"""
    FATAL = "fatal"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


def _req(d: dict, key: str):
    if key not in d or d[key] is None:
        raise ContractError(f"missing required field: {key}")
    return d[key]


def _enum(d: dict, key: str, enum_cls, required=True):
    if key not in d or d[key] is None:
        if required:
            raise ContractError(f"missing required field: {key}")
        return None
    raw = d[key]
    try:
        return enum_cls(raw)
    except ValueError:
        raise ContractError(f"unknown enum value for {key}: {raw!r} "
                            f"(allowed: {[e.value for e in enum_cls]})")


def _check_confidence(v: float, where: str):
    if not (0.0 <= v <= 1.0):
        raise ContractError(f"{where}.confidence out of range [0,1]: {v}")


# ── Schema ─────────────────────────────────────────────────────────────

@dataclass
class BenchVersion:
    """一次基准版本的完整身份——可复现原则的载体。"""
    bench_version: str                 # 基准自身版本，如 "0.1.0"
    version_name: str = "LCB"          # 基准名（占位，可换）
    universe_generator_version: str = ""
    judge_mechanical_version: str = ""
    judge_semantic_model: str = ""     # 如 "deepseek-v4-flash"；无语义判定则 ""
    judge_semantic_prompt_version: str = ""
    universe_seed: Optional[int] = None  # 合成宇宙种子；真实包为 None

    @classmethod
    def from_dict(cls, d: dict) -> "BenchVersion":
        return cls(
            bench_version=_req(d, "bench_version"),
            version_name=d.get("version_name", "LCB"),
            universe_generator_version=d.get("universe_generator_version", ""),
            judge_mechanical_version=d.get("judge_mechanical_version", ""),
            judge_semantic_model=d.get("judge_semantic_model", ""),
            judge_semantic_prompt_version=d.get("judge_semantic_prompt_version", ""),
            universe_seed=d.get("universe_seed"),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProbeSpec:
    """一条探针：埋点/检测单元。"""
    probe_id: str                      # 如 "kb-ch03-charX-T08"
    type: ViolationType
    chapter: int
    payload: dict = field(default_factory=dict)
    # payload 约定（按 type）：
    #   knowledge_boundary: {"character": str, "checklist": [str…],   ← 此刻不该知道的事
    #                        "known": [str…]}                        ← 此刻已知集合（judge 参照）
    #   context_rot:        {"fact": str, "planted_chapter": int, "expected": str}
    #   state:              {"kind": "death|possession|location|timeline",
    #                        "entity": str, "expected": str}
    #   constraint:         {}（机械层直接从 spec 规则来，不走探针）

    @classmethod
    def from_dict(cls, d: dict) -> "ProbeSpec":
        return cls(
            probe_id=_req(d, "probe_id"),
            type=_enum(d, "type", ViolationType),
            chapter=_req(d, "chapter"),
            payload=d.get("payload") or {},
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Violation:
    """一条判定的违反。evidence 必须可人工复核（span/expected/actual）。"""
    probe_id: str
    type: ViolationType
    detector: DetectorKind
    chapter: int
    severity: Severity
    evidence: dict                     # {"span": str, "expected": str, "actual": str}
    run_id: str
    confidence: float = 1.0
    note: str = ""

    def __post_init__(self):
        _check_confidence(self.confidence, f"violation[{self.probe_id}]")

    @classmethod
    def from_dict(cls, d: dict) -> "Violation":
        return cls(
            probe_id=_req(d, "probe_id"),
            type=_enum(d, "type", ViolationType),
            detector=_enum(d, "detector", DetectorKind),
            chapter=_req(d, "chapter"),
            severity=_enum(d, "severity", Severity),
            evidence=_req(d, "evidence"),
            run_id=_req(d, "run_id"),
            confidence=float(d.get("confidence", 1.0)),
            note=d.get("note", ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunManifest:
    """一次运行（模型 × 档位 × seed × k）的完整身份——可复现原则的载体。"""
    run_id: str
    bench: BenchVersion
    model: str
    provider: str
    temperature: float
    max_tokens: int
    harness: HarnessTier
    k_index: int                       # 第几次重复（pass^k 用，从 0 起）
    prompt_hashes: dict = field(default_factory=dict)   # {chapter: sha256}
    generated_at: Optional[str] = None
    cost: dict = field(default_factory=dict)   # {"tokens_in","tokens_out","currency_cost"}
    chapter_paths: dict = field(default_factory=dict)  # {chapter(str): 相对路径}
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "RunManifest":
        bench = d.get("bench")
        if not isinstance(bench, dict):
            raise ContractError("missing required field: bench")
        return cls(
            run_id=_req(d, "run_id"),
            bench=BenchVersion.from_dict(bench),
            model=_req(d, "model"),
            provider=_req(d, "provider"),
            temperature=float(_req(d, "temperature")),
            max_tokens=int(_req(d, "max_tokens")),
            harness=_enum(d, "harness", HarnessTier),
            k_index=int(_req(d, "k_index")),
            prompt_hashes=d.get("prompt_hashes") or {},
            generated_at=d.get("generated_at"),
            cost=d.get("cost") or {},
            chapter_paths=d.get("chapter_paths") or {},
            notes=d.get("notes", ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


# ── JSON Schema 导出（从 dataclass 字段程序化生成，防手写漂移）─────────

_PY_TO_SCHEMA = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
}


def export_schemas() -> dict:
    """导出四个 schema。轻量自写格式（零依赖，非完整 JSON Schema 标准）。"""
    out = {}
    for dc in (BenchVersion, ProbeSpec, Violation, RunManifest):
        required, props = [], {}
        for f in fields(dc):
            optional = (f.default is not MISSING) or (f.default_factory is not MISSING)
            if not optional:
                required.append(f.name)
            props[f.name] = {"type": _py_type_to_schema(f.type)}
        out[dc.__name__] = {"type": "object", "required": required, "properties": props}
    return out


def _py_type_to_schema(t) -> str:
    # py3.9：简单注解是类对象（如 str），复杂注解才是字符串（如 "Optional[str]"）
    if isinstance(t, str):
        base = t.replace("Optional[", "").rstrip("]")
    else:
        base = t
    for py, name in _PY_TO_SCHEMA.items():
        if base == py or base == py.__name__:
            return name
    return "object"  # BenchVersion / 嵌套 dict 等复合类型


def dumps_schemas() -> str:
    return json.dumps(export_schemas(), ensure_ascii=False, indent=2)