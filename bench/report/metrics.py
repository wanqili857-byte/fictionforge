#!/usr/bin/env python3
"""bench.report.metrics — W8 指标与报告（纯函数 + 一层 I/O 装载）。

指标定义（**必须成对出现**，理由见 writeup §8）：
- `violations_per_10k` 每万字违反率——受篇幅影响，**可被干净注水稀释**
- `violations_abs` 绝对违反数——不受篇幅影响，二者同报才不可刷

其余：
- `pass_k`：k 次重复中「零违反」的比例（不是 pass@k——这里 k 次都要过）
- `bootstrap_ci`：自助法置信区间（固定种子可复现）
- `attribution`：三档消融的差值（bare→mid 上下文工程；mid→full 门禁）
- `load_runs`：从跑批目录读回结果（I/O 层，目录结构见 batch.py）
"""

import json
import random
from pathlib import Path

TIERS = ("bare", "mid", "full")

# 违反分族：聚合数字会把「叙事一致性」和「篇幅/文体合规」混在一起——
# 首轮跑批实测：cons-length 在每一行都出现（模型普遍写不到目标字数），
# 于是 ds-flash mid 档看起来"还有 3 条问题"，其实叙事一致性违反为 0。
# 报表明必须分族，否则头条数字误导。
FAMILIES = {
    "length": ("cons-length",),                        # 篇幅合规（指令跟随）
    "style": ("cons-forbidden", "cons-para"),           # 禁词 / 段落（文体）
    "pov": ("cons-pov", "cons-pronoun", "cons-pronoun-adj"),  # 视角 / 人称
    "state": ("state-",),                               # 状态一致（前缀匹配）
}


def family_of(probe_id: str) -> str:
    """把 probe_id 归到族；未知归 other。"""
    for fam, prefixes in FAMILIES.items():
        for p in prefixes:
            if p.endswith("-"):
                if probe_id.startswith(p):
                    return fam
            elif probe_id.startswith(p):
                return fam
    return "other"


def count_by_family(violations: list) -> dict:
    out = {}
    for v in violations:
        fam = family_of(v.get("probe_id", ""))
        out[fam] = out.get(fam, 0) + 1
    return dict(sorted(out.items()))


# ── 基础指标 ──────────────────────────────────────────────────────────

def violations_per_10k(count: int, chars: int) -> float:
    if chars <= 0:
        return 0.0
    return round(count / chars * 10000, 2)


def count_by_type(violations: list) -> dict:
    out = {}
    for v in violations:
        t = v.get("type", "unknown")
        out[t] = out.get(t, 0) + 1
    return dict(sorted(out.items()))


def summarize_run(violations: list, chars: int) -> dict:
    """一次运行的指标原料（绝对值 + 密度成对 + 分族 + 剔篇幅的核心数）。

    `core_*` = 剔除「篇幅合规」后的违反——即叙事一致性（状态/视角/文体/知识边界）。
    篇幅是**指令跟随**，不是叙事一致性；混在一起会让头条数字失真。
    """
    n = len(violations)
    fam = count_by_family(violations)
    core = n - fam.get("length", 0)
    return {
        "violations_abs": n,
        "chars": chars,
        "violations_per_10k": violations_per_10k(n, chars),
        "by_type": count_by_type(violations),
        "by_family": fam,
        "core_abs": core,
        "core_per_10k": violations_per_10k(core, chars),
    }


def pass_k(violation_counts: list) -> float:
    """k 次重复中零违反的比例。空输入 → 0.0。"""
    if not violation_counts:
        return 0.0
    return round(sum(1 for c in violation_counts if c == 0) / len(violation_counts), 4)


def bootstrap_ci(samples: list, iters: int = 2000, seed: int = 0,
                 alpha: float = 0.05) -> tuple:
    """自助法置信区间（固定种子 → 可复现）。样本为空 → (0.0, 0.0)。"""
    if not samples:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(samples)
    means = []
    for _ in range(iters):
        means.append(sum(rng.choice(samples) for _ in range(n)) / n)
    means.sort()
    lo = means[int(iters * (alpha / 2))]
    hi = means[min(iters - 1, int(iters * (1 - alpha / 2)))]
    return (round(lo, 3), round(hi, 3))


# ── 归因（三档消融）──────────────────────────────────────────────────

def attribution(rate_by_tier: dict) -> dict:
    """三档差值。缺失档位则不产出对应项。"""
    out = {}
    if "bare" in rate_by_tier and "mid" in rate_by_tier:
        out["context_engineering"] = round(rate_by_tier["bare"] - rate_by_tier["mid"], 3)
    if "mid" in rate_by_tier and "full" in rate_by_tier:
        out["gate_postprocessing"] = round(rate_by_tier["mid"] - rate_by_tier["full"], 3)
    if "bare" in rate_by_tier and "full" in rate_by_tier:
        out["total"] = round(rate_by_tier["bare"] - rate_by_tier["full"], 3)
    return out


# ── I/O：装载跑批结果 ─────────────────────────────────────────────────

def load_runs(out_dir) -> dict:
    """读回 {run_id: {summary, violations, chars, tier, model, k}}。"""
    out = Path(out_dir)
    runs = {}
    for run_dir in sorted(p for p in out.iterdir() if p.is_dir()):
        run_json = run_dir / "run.json"
        if not run_json.exists():
            continue
        data = json.loads(run_json.read_text(encoding="utf-8"))
        summary = data.get("_summary") or {}
        vio_path = run_dir / "violations.json"
        violations = json.loads(vio_path.read_text(encoding="utf-8")) if vio_path.exists() else {"pre_fix": [], "post_fix": []}
        # 篇幅：正文（非修正稿）字数合计
        chars = 0
        for f in sorted(run_dir.glob("ch*.md")):
            if f.name.endswith(".fixed.md"):
                continue
            chars += len(f.read_text(encoding="utf-8").strip())
        runs[data["run_id"]] = {
            "model": summary.get("model"), "tier": summary.get("tier"),
            "k": summary.get("k"), "summary": summary, "violations": violations,
            "chars": chars,
            "cost": (data.get("cost") or {}).get("currency_cost", 0.0),
        }
    return runs


# ── 报表 ──────────────────────────────────────────────────────────────

def build_table(runs: dict) -> list:
    """每次运行一行：模型 × 档位 × k → 违反（总计/剔篇幅/分族）、成本、pass 标记。"""
    rows = []
    for run_id, r in sorted(runs.items()):
        tier = r["tier"]
        pre = r["violations"].get("pre_fix", [])
        post = r["violations"].get("post_fix", [])
        used = post if (tier == "full" and post is not None) else pre
        s = summarize_run(used, r["chars"])
        fam = s["by_family"]
        rows.append({
            "run_id": run_id, "model": r["model"], "tier": tier, "k": r["k"],
            "chars": r["chars"], "cost": round(r["cost"], 5),
            "violations_abs": s["violations_abs"],
            "violations_per_10k": s["violations_per_10k"],
            "core_abs": s["core_abs"], "core_per_10k": s["core_per_10k"],
            "by_family": fam,
            "length_abs": fam.get("length", 0),
            "style_abs": fam.get("style", 0),
            "pov_abs": fam.get("pov", 0),
            "state_abs": fam.get("state", 0),
            "by_type": s["by_type"],
            "pre_fix_abs": len(pre), "post_fix_abs": len(post) if tier == "full" else None,
        })
    return rows


def aggregate_by_model(runs: dict) -> list:
    """模型汇总：每档平均**核心**违反率（剔篇幅）+ 三档归因 + 平均成本。"""
    by_model = {}
    for r in runs.values():
        by_model.setdefault(r["model"], {}).setdefault(r["tier"], []).append(r)
    out = []
    for model, tiers in sorted(by_model.items()):
        core_rates, costs, per_tier, total_rates = {}, [], {}, {}
        for tier, rs in tiers.items():
            cores, totals = [], []
            for r in rs:
                used = (r["violations"].get("post_fix", []) if tier == "full"
                        else r["violations"].get("pre_fix", []))
                s = summarize_run(used, r["chars"])
                cores.append(s["core_per_10k"])
                totals.append(s["violations_per_10k"])
            per_tier[tier] = round(sum(cores) / len(cores), 2) if cores else 0.0
            total_rates[tier] = round(sum(totals) / len(totals), 2) if totals else 0.0
            costs += [r["cost"] for r in rs]
        out.append({
            "model": model,
            "core_rate_by_tier": per_tier,
            "total_rate_by_tier": total_rates,
            "attribution": attribution(per_tier),
            "avg_cost_per_run": round(sum(costs) / max(1, len(costs)), 5),
        })
    return out


def render_markdown(rows: list, agg: list) -> str:
    lines = ["# LCB 跑批结果", "",
             "> 核心违反 = 剔除「篇幅合规」后的违反（状态/视角/文体/知识边界），",
             "> 即叙事一致性；篇幅属指令跟随，单列。两个数字都要看：",
             "> 密度可被加字稀释（详见 writeup §8），所以绝对数一并给出。", "",
             "> **解读警示**：k=1 时档位间的差值混着采样噪声（同一 prompt 两次运行",
             "> 本就会抖动），此时只能看方向、不能当精确归因；要谈归因需 k≥3。",
             "> 重判免费（`--rejudge` 不调 LLM），重生成才花钱。", "",
             "## 每次运行", "",
             "| run | 模型 | 档位 | k | 字数 | 总违反 | 核心违反 | 核心/万字 | 篇幅 | 文体 | 视角 | 状态 | 修前→修后 | 成本$ |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        fixed = (f"{r['pre_fix_abs']}→{r['post_fix_abs']}"
                 if r["post_fix_abs"] is not None else "—")
        lines.append(f"| {r['run_id']} | {r['model']} | {r['tier']} | {r['k']} | "
                     f"{r['chars']} | {r['violations_abs']} | {r['core_abs']} | "
                     f"{r['core_per_10k']} | {r['length_abs']} | {r['style_abs']} | "
                     f"{r['pov_abs']} | {r['state_abs']} | {fixed} | {r['cost']} |")
    lines += ["", "## 按模型汇总（三档归因，核心违反率）", "",
              "| 模型 | bare | mid | full | 上下文工程 | 门禁 | 合计 | 平均成本$ |",
              "|---|---|---|---|---|---|---|---|"]
    for a in agg:
        t = a["core_rate_by_tier"]
        d = a["attribution"]
        lines.append(f"| {a['model']} | {t.get('bare','—')} | {t.get('mid','—')} | "
                     f"{t.get('full','—')} | {d.get('context_engineering','—')} | "
                     f"{d.get('gate_postprocessing','—')} | {d.get('total','—')} | "
                     f"{a['avg_cost_per_run']} |")
    return "\n".join(lines) + "\n"


def main():
    import argparse
    ap = argparse.ArgumentParser(description="LCB 报表")
    ap.add_argument("out_dir", help="跑批输出目录（含 results.json 与各 run 目录）")
    ap.add_argument("--write", action="store_true", help="写入 report.md")
    args = ap.parse_args()
    runs = load_runs(args.out_dir)
    if not runs:
        print("没有可读的运行目录"); return 1
    rows = build_table(runs)
    agg = aggregate_by_model(runs)
    md = render_markdown(rows, agg)
    print(md)
    if args.write:
        p = Path(args.out_dir) / "report.md"
        p.write_text(md, encoding="utf-8")
        print(f"→ {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())