#!/usr/bin/env python3
"""bench.runner.batch — 跑批编排（W7）：模型 × harness 档位 × k 次，逐章生成。

设计要点：
- **注入式**：generate_fn / judge_fn 由外部传入 → 编排逻辑可离线单测
- **可续跑**：章节文件已存在则跳过（不重复烧 token），失败重跑不丢已有结果
- **修前/修后分开记**：full 档同时记录门禁修正前与修正后的违反（门禁功劳单独记账）
- **成本入账**：每次调用的 tokens 与美元成本累加进 manifest

目录布局：
    {out}/{model}__{tier}__k{k}/ch{n}.md     生成正文
    {out}/{model}__{tier}__k{k}/run.json     RunManifest（契约）
    {out}/{model}__{tier}__k{k}/violations.json  违反清单（修前/修后）
    {out}/results.json                       汇总（模型 × 档位 → 指标原料）

用法：
    python3 -m bench.runner.batch --seed 42 --chapters 6 \\
        --models ds-flash,glm-flash --tiers bare,mid,full --k 1 --out runs/first
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from bench.contracts import BenchVersion, HarnessTier, RunManifest
from bench.runner import models as model_catalog
from bench.runner.harness import build_prompt, apply_gate_fix
from bench.runner.llm import call_model
from bench.state.ledger import build_ledger
from bench.judges.mechanical import MechanicalRules, mechanical_judge
from bench.judges.state_judge import state_judge

BENCH_VERSION = "0.1.0"
JUDGE_MECHANICAL_VERSION = "m0.1.0"

DEFAULT_PARA_MAX = 100
DEFAULT_PRIOR_TAIL = 1200


# ── 判定器装配（从宇宙构造规则 + 账本）────────────────────────────────

def rules_for_universe(u, chapter: int) -> MechanicalRules:
    spec = next((s for s in u.specs if s["chapter"] == chapter), {})
    pronoun = next((("她" if c["gender"] == "f" else "他")
                    for c in u.cast if c["name"] == u.protagonist), "")
    return MechanicalRules(
        forbidden_words=list(u.facts_forbidden),
        target_chars=spec.get("target_chars", 0),
        para_max_chars=DEFAULT_PARA_MAX,
        pov="third_limited",
        protagonist=u.protagonist,
        protagonist_pronoun=pronoun,
        cast_genders={c["name"]: c["gender"] for c in u.cast},
    )


def make_judge(u, ledger, run_id: str):
    def judge_fn(text: str, chapter: int) -> list:
        out = mechanical_judge(text, rules_for_universe(u, chapter), run_id, chapter)
        out += state_judge(text, ledger, chapter=chapter, run_id=run_id)
        return out
    return judge_fn


# ── 编排 ──────────────────────────────────────────────────────────────

def _hash(system: str, user: str) -> str:
    return hashlib.sha256((system + "\n\n" + user).encode("utf-8")).hexdigest()


def run_one(u, model_spec, tier: str, k_index: int, out_dir: Path,
            generate_fn, judge_fn, chapters=None, prior_tail=DEFAULT_PRIOR_TAIL,
            force=False, log=print) -> dict:
    """跑一个 (模型 × 档位 × k) 组合：逐章生成 + 判定。返回汇总 dict。"""
    run_id = f"{model_spec.alias}__{tier}__k{k_index}"
    run_dir = out_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if (run_dir / "run.json").exists() and not force:
        log(f"  [skip] {run_id} 已完成（force 可覆盖）")
        return json.loads((run_dir / "run.json").read_text(encoding="utf-8")).get("_summary", {})

    wanted = chapters or [s["chapter"] for s in u.specs]
    prior_text, prompt_hashes, chapter_paths = "", {}, {}
    violations = {"pre_fix": [], "post_fix": []}
    tokens_in = tokens_out = 0
    cost = 0.0
    errors = []

    for ch in wanted:
        spec = next(s for s in u.specs if s["chapter"] == ch)
        ch_file = run_dir / f"ch{ch}.md"
        if ch_file.exists() and not force:
            text = ch_file.read_text(encoding="utf-8").rstrip("\n")
            log(f"  [resume] {run_id} ch{ch}")
        else:
            system, user = build_prompt(tier, u, spec, prior_text=prior_text,
                                       prior_tail=prior_tail)
            prompt_hashes[str(ch)] = _hash(system, user)
            res = generate_fn(model_spec, system, user)
            tokens_in += res.get("tokens_in", 0)
            tokens_out += res.get("tokens_out", 0)
            cost += res.get("cost", 0.0)
            if res.get("error"):
                errors.append({"chapter": ch, "error": res["error"]})
                log(f"  [error] {run_id} ch{ch}: {res['error'][:100]}")
                break
            text = res["text"]
            ch_file.write_text(text + "\n", encoding="utf-8")

        if tier == "full":
            before = judge_fn(text, ch)
            fixed = apply_gate_fix(text, forbidden_words=u.facts_forbidden,
                                   para_max=DEFAULT_PARA_MAX)
            fixed_file = run_dir / f"ch{ch}.fixed.md"
            fixed_file.write_text(fixed + "\n", encoding="utf-8")
            after = judge_fn(fixed, ch)
            violations["pre_fix"] += [v.to_dict() for v in before]
            violations["post_fix"] += [v.to_dict() for v in after]
            text = fixed
        else:
            violations["pre_fix"] += [v.to_dict() for v in judge_fn(text, ch)]

        chapter_paths[str(ch)] = str(ch_file.name)
        prior_text = (text if len(text) <= prior_tail else text[-prior_tail:])

    summary = {
        "run_id": run_id, "model": model_spec.alias, "tier": tier, "k": k_index,
        "chapters_done": len(chapter_paths), "tokens_in": tokens_in,
        "tokens_out": tokens_out, "cost": round(cost, 6), "errors": errors,
        "violations_pre_fix": len(violations["pre_fix"]),
        "violations_post_fix": len(violations["post_fix"]) if tier == "full" else None,
    }
    manifest = RunManifest(
        run_id=run_id,
        bench=BenchVersion(bench_version=BENCH_VERSION,
                           universe_generator_version=u.version,
                           judge_mechanical_version=JUDGE_MECHANICAL_VERSION,
                           judge_semantic_model="", judge_semantic_prompt_version="",
                           universe_seed=u.seed),
        model=model_spec.model, provider=model_spec.provider,
        temperature=model_spec.temperature, max_tokens=model_spec.max_tokens,
        harness=HarnessTier(tier), k_index=k_index,
        prompt_hashes=prompt_hashes,
        generated_at=datetime.now(timezone.utc).isoformat(),
        cost={"tokens_in": tokens_in, "tokens_out": tokens_out,
              "currency_cost": round(cost, 6)},
        chapter_paths=chapter_paths, notes="",
    )
    data = manifest.to_dict()
    data["_summary"] = summary
    (run_dir / "run.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    (run_dir / "violations.json").write_text(
        json.dumps(violations, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  [done] {run_id}: {summary['chapters_done']} 章 | "
        f"{tokens_in}+{tokens_out} tok | ${summary['cost']:.4f} | "
        f"违反(修前) {summary['violations_pre_fix']}"
        + (f" (修后) {summary['violations_post_fix']}" if tier == "full" else ""))
    return summary


def run_batch(u, model_aliases, tiers=("bare", "mid", "full"), k=1, out_dir="runs",
              generate_fn=None, judge_fn=None, chapters=None, force=False,
              log=print) -> dict:
    """跑完整矩阵。generate_fn/judge_fn 缺省用真实实现。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    generate_fn = generate_fn or call_model
    league = build_ledger(u.title, u.specs, [c["name"] for c in u.cast],
                          protagonist=u.protagonist)
    judge_fn = judge_fn or make_judge(u, league, run_id="batch")

    summaries = []
    for alias in model_aliases:
        spec = model_catalog.get(alias)
        for tier in tiers:
            for kk in range(k):
                log(f"[run] {alias} × {tier} × k{kk}")
                summaries.append(run_one(u, spec, tier, kk, out, generate_fn,
                                         judge_fn, chapters=chapters, force=force,
                                         log=log))
    results = {"universe": {"title": u.title, "seed": u.seed, "chapters": u.chapters},
               "runs": summaries}
    (out / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def main():
    ap = argparse.ArgumentParser(description="LCB 跑批")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--chapters", type=int, default=6)
    ap.add_argument("--models", default="ds-flash,glm-flash")
    ap.add_argument("--tiers", default="bare,mid,full")
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--out", default="runs/first")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from bench.universe.generator import generate

    u = generate(seed=args.seed, chapters=args.chapters)
    res = run_batch(u, args.models.split(","), tuple(args.tiers.split(",")),
                    k=args.k, out_dir=args.out, force=args.force)
    total = sum(r["cost"] for r in res["runs"])
    print(f"\n总成本 ${total:.4f} | {len(res['runs'])} 个运行 → {args.out}/results.json")


if __name__ == "__main__":
    main()