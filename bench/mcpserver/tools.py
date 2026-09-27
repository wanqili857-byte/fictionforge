#!/usr/bin/env python3
"""bench.mcpserver.tools — MCP 工具实现（纯函数，参数校验严格）。

五个工具（W12）：
  universe_generate  合成宇宙生成（零 LLM，确定性）
  novel_ledger       状态账本（spec + 真相表 → 账本查询）
  novel_truth        真相表（可按 false 过滤）
  novel_gate         门禁（机械 + 状态判定 → 违反清单 + 每万字违反率）
  novel_gen          生成草稿（调 scripts/gen.py，需 LLM 通道）

call_tool 只做「参数校验 + 调库 + 组装可 JSON 化的结果」，
不做 IO 输出 —— 便于协议层单测与复用。
"""

import json
import subprocess
import sys
from pathlib import Path

from bench.universe.generator import generate as gen_universe, invariants, write_universe
from bench.state.ledger import load_specs, build_ledger, cast_base_names
from bench.state.truth_table import load_truth_table
from bench.judges.mechanical import MechanicalRules, mechanical_judge
from bench.judges.state_judge import state_judge

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class ToolError(Exception):
    """工具调用失败（参数非法 / 前置条件不满足）。"""


def _req_str(args: dict, key: str) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ToolError(f"参数 {key} 必填且为非空字符串")
    return v


def _opt_int(args: dict, key: str, default=None, lo=None, hi=None):
    if key not in args or args[key] is None:
        return default
    v = args[key]
    if isinstance(v, bool) or not isinstance(v, int):
        raise ToolError(f"参数 {key} 必须为整数")
    if lo is not None and v < lo:
        raise ToolError(f"参数 {key} 不能小于 {lo}")
    if hi is not None and v > hi:
        raise ToolError(f"参数 {key} 不能大于 {hi}")
    return v


def _novel_dir(args: dict) -> Path:
    d = Path(_req_str(args, "novel_dir")).expanduser()
    if not d.is_dir():
        raise ToolError(f"novel_dir 不存在或不是目录: {d}")
    return d


def _config(d: Path) -> dict:
    p = d / "novel_config.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


# ── 工具实现 ──────────────────────────────────────────────────────────

def t_universe_generate(args: dict) -> dict:
    seed = _opt_int(args, "seed", lo=0)
    if seed is None:
        raise ToolError("参数 seed 必填且为非负整数")
    chapters = _opt_int(args, "chapters", default=12, lo=1, hi=60)
    u = generate_universe_cached(seed, chapters)
    out = {
        "title": u.title, "seed": u.seed, "chapters": u.chapters,
        "protagonist": u.protagonist, "cast": u.cast,
        "terminal_fact_id": u.terminal_fact_id,
        "facts": len(u.truth_table),
        "false_facts": sum(1 for f in u.truth_table if f["is_false"]),
        "knowledge_counts": {k: len(v) for k, v in u.knowledge.items()},
        "invariants": invariants(u),
    }
    out_dir = args.get("out_dir")
    if out_dir:
        d = Path(str(out_dir)).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        write_universe(u, d)
        out["written_to"] = str(d)
    return out


_UNIVERSE_CACHE = {}


def generate_universe_cached(seed: int, chapters: int):
    key = (seed, chapters)
    if key not in _UNIVERSE_CACHE:
        _UNIVERSE_CACHE[key] = gen_universe(seed=seed, chapters=chapters)
    return _UNIVERSE_CACHE[key]


def t_novel_ledger(args: dict) -> dict:
    d = _novel_dir(args)
    specs = load_specs(d)
    if not specs:
        raise ToolError(f"{d}/specs 下没有 ch*.json")
    cfg = _config(d)
    led = build_ledger(cfg.get("title", d.name), specs, cast_base_names(cfg),
                       protagonist=args.get("protagonist")
                       or _protagonist_from_specs(specs, cfg))
    payload = led.to_dict()
    payload["timeline_violations"] = led.timeline_violations()
    last = max((c.chapter for c in led.chapters), default=0)
    payload["alive_at_last"] = led.alive_at(last)
    return payload


def _protagonist_from_specs(specs, cfg):
    name = cfg.get("protagonist")
    if isinstance(name, str) and name:
        for c in cfg.get("cast", []):
            if isinstance(c, dict) and c.get("name") == name:
                return c["name"]
    cast = cast_base_names(cfg)
    return cast[0] if cast else None


def t_novel_truth(args: dict) -> dict:
    d = _novel_dir(args)
    tt = d / "bible" / "真相表.md"
    if not tt.exists():
        raise ToolError(f"真相表不存在: {tt}")
    facts = load_truth_table(tt)
    if args.get("only_false"):
        facts = [f for f in facts if f.is_false]
    return {"count": len(facts),
            "false_count": sum(1 for f in facts if f.is_false),
            "facts": [{"id": f.id, "category": f.category,
                       "statement": f.statement, "is_false": f.is_false}
                      for f in facts]}


def t_novel_gate(args: dict) -> dict:
    d = _novel_dir(args)
    chapter = _opt_int(args, "chapter", lo=1)
    if chapter is None:
        raise ToolError("参数 chapter 必填（正整数）")
    text = args.get("text")
    if text is None:
        tp = args.get("text_path")
        if not tp:
            raise ToolError("需要 text 或 text_path 之一")
        p = Path(str(tp)).expanduser()
        if not p.exists():
            raise ToolError(f"text_path 不存在: {p}")
        text = p.read_text(encoding="utf-8")
    if not isinstance(text, str):
        raise ToolError("text 必须为字符串")

    cfg = _config(d)
    quality = cfg.get("quality", {}) or {}
    specs = load_specs(d)
    spec = next((s for s in specs if s.get("chapter") == chapter), None)
    target = (spec or {}).get("target_chars", 0)

    rules = MechanicalRules(
        forbidden_words=list(quality.get("forbidden_words") or []),
        target_chars=target if isinstance(target, int) else 0,
        para_max_chars=100,
        pov="third_limited",
        protagonist=_protagonist_from_specs(specs, cfg) or "",
        protagonist_pronoun=cfg.get("protagonist_pronoun") or "",
        cast_genders={c["name"]: c.get("gender", "")
                      for c in cfg.get("cast", []) if isinstance(c, dict)},
    )
    violations = mechanical_judge(text, rules, run_id="mcp", chapter=chapter)

    if specs:
        led = build_ledger(cfg.get("title", d.name), specs, cast_base_names(cfg),
                           protagonist=rules.protagonist)
        violations += state_judge(text, led, chapter=chapter, run_id="mcp")

    n = len(text.strip())
    by_type = {}
    for v in violations:
        by_type[v.type.value] = by_type.get(v.type.value, 0) + 1
    return {
        "chapter": chapter,
        "chars": n,
        "violation_count": len(violations),
        "rate_per_10k": round(len(violations) / n * 10000, 2) if n else 0.0,
        "counts_by_type": by_type,
        "violations": [v.to_dict() for v in violations],
    }


def t_novel_gen(args: dict) -> dict:
    spec = _req_str(args, "spec")
    p = Path(spec).expanduser()
    if not p.exists():
        raise ToolError(f"spec 不存在: {p}")
    cmd = [sys.executable, str(PROJECT_ROOT / "scripts" / "gen.py"), str(p)]
    if args.get("resection"):
        cmd += ["--resection", str(args["resection"])]
    if args.get("force"):
        cmd += ["--force"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                           cwd=str(PROJECT_ROOT))
    except subprocess.TimeoutExpired:
        raise ToolError("生成超时（1800s）")
    tail = (r.stdout or "")[-3000:]
    return {"exit_code": r.returncode, "ok": r.returncode == 0,
            "stdout_tail": tail, "stderr_tail": (r.stderr or "")[-1000:]}


_TOOLS = {
    "universe_generate": t_universe_generate,
    "novel_ledger": t_novel_ledger,
    "novel_truth": t_novel_truth,
    "novel_gate": t_novel_gate,
    "novel_gen": t_novel_gen,
}


def call_tool(name: str, args: dict) -> dict:
    """调用工具。未知工具/参数非法 → ToolError。"""
    fn = _TOOLS.get(name)
    if fn is None:
        raise ToolError(f"未知工具: {name}")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolError("arguments 必须为对象")
    return fn(args)


# ── 工具描述（MCP tools/list 用）──────────────────────────────────────

def tool_specs() -> list:
    return [
        {
            "name": "universe_generate",
            "description": "生成合成宇宙（确定性，零 LLM）：cast/真相表/知识表/分幕/"
                           "章节 spec。同 seed 结果逐字节一致。可选 out_dir 落盘成内容包。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "seed": {"type": "integer", "minimum": 0,
                             "description": "随机种子（决定宇宙内容）"},
                    "chapters": {"type": "integer", "minimum": 1, "maximum": 60,
                                 "default": 12},
                    "out_dir": {"type": "string",
                                "description": "可选：落盘目录（生成 specs/bible/config）"},
                },
                "required": ["seed"],
            },
        },
        {
            "name": "novel_ledger",
            "description": "构建/查询状态账本：逐章天/时段/地点/在场角色/死亡/持有物，"
                           "含时间线违规与末章在世角色。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "novel_dir": {"type": "string", "description": "内容包目录"},
                    "protagonist": {"type": "string", "description": "可选：主角名（回填在场）"},
                },
                "required": ["novel_dir"],
            },
        },
        {
            "name": "novel_truth",
            "description": "读取真相表（权威事实）：id/类别/命题/false 标记"
                           "（false = 广泛流传但错误的认识，B 型反转素材）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "novel_dir": {"type": "string"},
                    "only_false": {"type": "boolean", "default": False},
                },
                "required": ["novel_dir"],
            },
        },
        {
            "name": "novel_gate",
            "description": "对给定章节正文跑门禁：机械判定（禁词/段落/篇幅/POV/人称）"
                           "+ 状态判定（死人复活/时间倒流/日期矛盾/物品双持有），"
                           "返回违反清单与每万字违反率。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "novel_dir": {"type": "string"},
                    "chapter": {"type": "integer", "minimum": 1},
                    "text": {"type": "string", "description": "正文文本"},
                    "text_path": {"type": "string", "description": "或：正文文件路径"},
                },
                "required": ["novel_dir", "chapter"],
            },
        },
        {
            "name": "novel_gen",
            "description": "调 gen.py 生成章节草稿（需 LLM 通道：本地 proxy + API key）。"
                           "支持 resection 局部重生成。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "spec": {"type": "string", "description": "spec JSON 路径"},
                    "resection": {"type": "string", "description": "可选：只重写该节 id"},
                    "force": {"type": "boolean", "default": False},
                },
                "required": ["spec"],
            },
        },
    ]