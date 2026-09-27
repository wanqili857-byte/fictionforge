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


def is_incomplete(run: dict) -> bool:
    """未完成 = 0 字，或明确记录 chapters_done == 0。

    0 章 0 违反若照常显示，会被读成"零违反"（完美）——必须显式标记并剔除。
    缺 chapters_done 字段时按未知处理，不误判。
    """
    if run.get("chars", 0) == 0:
        return True
    return (run.get("summary") or {}).get("chapters_done") == 0


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

def gate_contribution(runs: dict) -> dict:
    """门禁的真实贡献 = **配对测量**：同一 full 运行内 修前核心 − 修后核心。

    为什么不用 mid↔full 差值：那是两次独立生成，差值混着采样噪声
    （k=1 时实测 glm-flash 的 mid↔full 差值 7.08，而配对测量只有 1）。
    配对测量在同一批文本上只多一道门禁 → 干净归因。
    """
    pre_core = post_core = 0
    n_runs = 0
    for r in runs.values():
        # 注意：必须判「键是否存在」，不能判真假——post_fix == [] 正是门禁
        # 把违反擦干净的最好情形，用 `not r[...].get("post_fix")` 会把它跳过，
        # 导致 removed 系统性低估（三家里若有全擦干净的模型，直接从表里消失）。
        if r["tier"] != "full" or "post_fix" not in (r["violations"] or {}):
            continue
        if not r["chars"]:
            continue
        n_runs += 1
        pre = summarize_run(r["violations"].get("pre_fix", []), r["chars"])
        post = summarize_run(r["violations"].get("post_fix", []), r["chars"])
        pre_core += pre["core_abs"]
        post_core += post["core_abs"]
    return {"full_runs": n_runs, "core_pre": pre_core, "core_post": post_core,
            "removed": pre_core - post_core}


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
        # **缺 violations.json = 该 run 不可信**：兜底成空清单会让残缺 run
        # 以「零违反」身份上榜（且续跑因 run.json 完整而永不修复）。
        # 这里标记出来，由 build_table 当未完成处理。
        violations_missing = not vio_path.exists()
        violations = (json.loads(vio_path.read_text(encoding="utf-8"))
                      if not violations_missing
                      else {"pre_fix": [], "post_fix": []})
        # 篇幅：正文（非修正稿）字数合计
        chars = 0
        for f in sorted(run_dir.glob("ch*.md")):
            if f.name.endswith(".fixed.md"):
                continue
            chars += len(f.read_text(encoding="utf-8").strip())
        runs[data["run_id"]] = {
            "model": summary.get("model"), "tier": summary.get("tier"),
            "k": summary.get("k"), "summary": summary, "violations": violations,
            "chars": chars, "violations_missing": violations_missing,
            "cost": (data.get("cost") or {}).get("currency_cost", 0.0),
            "billing": summary.get("billing") or "per_token",
        }
    return runs


# ── 报表 ──────────────────────────────────────────────────────────────

def build_table(runs: dict, expected_chapters: int = None) -> list:
    """每次运行一行：模型 × 档位 × k → 违反（总计/剔篇幅/分族）、成本、完成度。

    expected_chapters 给出时应标出「部分完成」（跑了 N/M 章）——
    部分运行的数字不能当完整行读（kimi 撞额度只跑 2/6 章就是这情况）。
    """
    rows = []
    for run_id, r in sorted(runs.items()):
        tier = r["tier"]
        pre = r["violations"].get("pre_fix", [])
        post = r["violations"].get("post_fix", [])
        used = post if (tier == "full" and post is not None) else pre
        s = summarize_run(used, r["chars"])
        fam = s["by_family"]
        # 未完成（0 章 / 0 字）不能显示为 0 违反——那会被读成"完美"
        done = (r["summary"] or {}).get("chapters_done")
        # 缺 violations.json 的 run 一律当未完成：它没有可核对的判决，
        # 显示成 0 违反会冒充榜首。
        incomplete = is_incomplete(r) or r.get("violations_missing", False)
        partial = (not incomplete and expected_chapters
                   and done is not None and done < expected_chapters)
        rows.append({
            "incomplete": incomplete, "partial": bool(partial),
            "chapters_done": done, "expected_chapters": expected_chapters,
            "run_id": run_id, "model": r["model"], "tier": tier, "k": r["k"],
            "chars": r["chars"], "cost": round(r["cost"], 5),
            "billing": r.get("billing", "per_token"),
            "violations_abs": s["violations_abs"],
            "violations_per_10k": s["violations_per_10k"],
            "core_abs": (None if incomplete else s["core_abs"]),
            "core_per_10k": (None if incomplete else s["core_per_10k"]),
            "by_family": fam,
            "length_abs": fam.get("length", 0),
            "style_abs": fam.get("style", 0),
            "pov_abs": fam.get("pov", 0),
            "state_abs": fam.get("state", 0),
            "by_type": s["by_type"],
            "pre_fix_abs": len(pre), "post_fix_abs": len(post) if tier == "full" else None,
        })
    return rows


def aggregate_by_model(runs: dict, expected_chapters: int = None) -> list:
    """模型汇总：每档平均**核心**违反率（剔篇幅）+ 三档归因 + 平均成本。

    剔除规则（与 render_markdown 脚注必须一致）：
    - 未完成 / 缺判决的 run 不进汇总
    - 部分完成（chapters_done < 期望章数）不进汇总——部分运行的密度不可比。
      expected_chapters 缺省时退化为「以本批最大完成度为准」，此时**全体同样
      残缺就会全体入选**，与脚注「不参与汇总」自相矛盾；正式报表一律传期望章数。
    """
    by_model = {}
    done_map = {k: (v.get("summary") or {}).get("chapters_done")
                for k, v in runs.items()}
    if expected_chapters is None:
        expected = max([d for d in done_map.values() if d], default=None)
    else:
        expected = expected_chapters
    for k, r in runs.items():
        if is_incomplete(r) or r.get("violations_missing", False):
            continue
        if expected and done_map[k] is not None and done_map[k] < expected:
            continue
        by_model.setdefault(r["model"], {}).setdefault(r["tier"], []).append(r)
    out = []
    for model, tiers in sorted(by_model.items()):
        core_rates, costs, per_tier, total_rates = {}, [], {}, {}
        billings = {r.get("billing", "per_token")
                    for rs in tiers.values() for r in rs}
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
        gate = gate_contribution({k: v for k, v in runs.items()
                                 if v["model"] == model})
        att = attribution(per_tier)
        att.pop("gate_postprocessing", None)   # 用配对测量替代，见 gate_paired
        out.append({
            "model": model,
            "core_rate_by_tier": per_tier,
            "total_rate_by_tier": total_rates,
            "attribution": att,
            "gate_paired": gate,
            "billing": billings.pop() if len(billings) == 1 else "mixed",
            "avg_cost_per_run": round(sum(costs) / max(1, len(costs)), 5),
        })
    return out


def _cost_cell(cost, billing: str) -> str:
    """零边际成本通道的成本列不能显示 $0——那会被读成「免费」，而不是「边际成本为 0」。

    订阅/免费额度跑批的可比量是 **token 用量**（manifest 里照常记录），不是美元。
    混合通道的汇总表里也按同一口径标注，避免拿零边际成本行去和按量行的钱数比。
    """
    if billing == "subscription":
        return "订阅"
    if billing == "free_quota":
        return "免费额度"
    return f"{cost}"


# ── 榜单（W10）：静态、可复算，排序规则公开 ─────────────────────────────

def _billing_label(billing: str) -> str:
    """榜单的计费列只标口径，**不显示金额**。

    榜单不报成本数字，是因为零边际成本通道与按量通道的钱数不可比；
    曾经复用 _cost_cell(0.0, "per_token") 渲染出字面量 "0.0"，读起来就是
    「这个模型免费」——按量模型被显示成 $0 是误导，不是省略。
    """
    return {"subscription": "订阅", "free_quota": "免费额度"}.get(billing, "按量")


def leaderboard(agg: list, tiers=("bare", "mid", "full")) -> list:
    """从模型汇总出榜单：核心违反率升序、并列同名次、缺档位降级。

    规则（与 BENCH_PROTOCOL §8 一致）：
    - 排序键 = bare 档核心违反率（裸能力），并列取相同名次（competition ranking）
    - 档位不齐的模型照常上榜但标 partial——榜单不藏半成品，读者自己看列
    - 未完成（0 章）不进榜（build_table/aggregate 已剔除，这里再兜一层）
    """
    entries = []
    for a in agg:
        rates = a.get("core_rate_by_tier") or {}
        bare = rates.get("bare")
        if bare is None:
            continue
        entries.append({
            "model": a["model"],
            "bare": bare, "mid": rates.get("mid"), "full": rates.get("full"),
            "billing": a.get("billing", "per_token"),
            "missing": [t for t in tiers if t not in rates],
        })
    entries.sort(key=lambda e: (e["bare"], e["model"]))
    ranked, prev, rank = [], object(), 0
    for i, e in enumerate(entries, 1):
        if e["bare"] != prev:
            rank, prev = i, e["bare"]
        e["rank"] = rank
        ranked.append(e)
    return ranked


def render_leaderboard(ranked: list) -> str:
    lines = ["# LCB 榜单（核心违反率，升序 = 越一致）", "",
             "> 排序键 = bare 档核心违反率（裸能力）；并列同名次。",
             "> `订阅`/`免费额度` = 零边际成本通道，成本列不可与按量行比钱数。",
             "> 缺档位 = 跑批未完成该档，数字照登但不可当完整行读。", "",
             "| 名次 | 模型 | bare | mid | full | 计费 | 缺档 |",
             "|---|---|---|---|---|---|---|"]
    for e in ranked:
        miss = ",".join(e["missing"]) if e["missing"] else "—"
        cell = lambda v: "—" if v is None else v
        lines.append(f"| {e['rank']} | {e['model']} | {e['bare']} | "
                     f"{cell(e.get('mid'))} | {cell(e.get('full'))} | "
                     f"{_billing_label(e.get('billing', 'per_token'))} | {miss} |")
    return "\n".join(lines) + "\n"


def render_markdown(rows: list, agg: list) -> str:
    lines = ["# LCB 跑批结果", "",
             "> 核心违反 = 剔除「篇幅合规」后的违反（状态/视角/文体/知识边界），",
             "> 即叙事一致性；篇幅属指令跟随，单列。两个数字都要看：",
             "> 密度可被加字稀释（详见 writeup §8），所以绝对数一并给出。", "",
             "> **解读警示**：k=1 时档位间的差值混着采样噪声（同一 prompt 两次运行",
             "> 本就会抖动），此时只能看方向、不能当精确归因；要谈归因需 k≥3。",
             "> 重判免费（`--rejudge` 不调 LLM），重生成才花钱。",
             "> **成本口径**：`订阅`/`免费额度` = 零边际成本通道，可比量是",
             "> token 用量而非美元；不可与按量计费通道的钱数直接比较。", "",
             "## 每次运行", "",
             "| run | 模型 | 档位 | k | 字数 | 总违反 | 核心违反 | 核心/万字 | 篇幅 | 文体 | 视角 | 状态 | 修前→修后 | 成本$ |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        fixed = (f"{r['pre_fix_abs']}→{r['post_fix_abs']}"
                 if r["post_fix_abs"] is not None else "—")
        cost = _cost_cell(r["cost"], r.get("billing", "per_token"))
        if r["incomplete"]:
            lines.append(f"| {r['run_id']} | {r['model']} | {r['tier']} | {r['k']} | "
                         f"**未完成** | — | — | — | — | — | — | — | — | {cost} |")
            continue
        if r.get("partial"):
            lines.append(f"| {r['run_id']} | {r['model']} | {r['tier']} | {r['k']} | "
                         f"{r['chars']} | {r['violations_abs']} | {r['core_abs']} | "
                         f"{r['core_per_10k']} | {r['length_abs']} | {r['style_abs']} | "
                         f"{r['pov_abs']} | {r['state_abs']} | {fixed} | {cost} | "
                         f"⚠ 部分 {r['chapters_done']}/{r['expected_chapters']} 章 |")
            continue
        lines.append(f"| {r['run_id']} | {r['model']} | {r['tier']} | {r['k']} | "
                     f"{r['chars']} | {r['violations_abs']} | {r['core_abs']} | "
                     f"{r['core_per_10k']} | {r['length_abs']} | {r['style_abs']} | "
                     f"{r['pov_abs']} | {r['state_abs']} | {fixed} | {cost} |")
    for r in rows:
        if r.get("partial"):
            lines.append("")
            lines.append(f"> ⚠ `{r['run_id']}` 仅完成 {r['chapters_done']}/"
                         f"{r['expected_chapters']} 章，**不参与汇总**"
                         f"（数字不可与完整运行比较）。")
            break
    lines += ["", "## 按模型汇总（三档归因，核心违反率）", "",
              "| 模型 | bare | mid | full | 上下文工程(bare→mid) | 合计(bare→full) | 平均成本$ |",
              "|---|---|---|---|---|---|---|"]
    for a in agg:
        t = a["core_rate_by_tier"]
        d = a["attribution"]
        lines.append(f"| {a['model']} | {t.get('bare','—')} | {t.get('mid','—')} | "
                     f"{t.get('full','—')} | {d.get('context_engineering','—')} | "
                     f"{d.get('total','—')} | "
                     f"{_cost_cell(a['avg_cost_per_run'], a.get('billing','per_token'))} |")
    lines += ["", "## 门禁贡献（配对测量：同一次 full 运行内 修前核心 → 修后核心）", "",
              "| 模型 | full 运行数 | 修前核心 | 修后核心 | 擦除 |",
              "|---|---|---|---|---|"]
    for a in agg:
        g = a["gate_paired"]
        if g["full_runs"]:
            lines.append(f"| {a['model']} | {g['full_runs']} | {g['core_pre']} | "
                         f"{g['core_post']} | {g['removed']} |")
    lines += ["", "> 门禁只用配对测量：mid↔full 差值是两次独立生成，含采样噪声",
              "> （实测 glm-flash 的 mid↔full 差值 7.08，配对测量只有 1）。",
              "> 门禁只能做减法（删禁词/切长段），补不了篇幅。"]
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
    # 期望章数从 results.json 的 universe 读（标「部分完成」用）
    expected = None
    rj = Path(args.out_dir) / "results.json"
    if rj.exists():
        try:
            expected = (json.loads(rj.read_text(encoding="utf-8"))
                        .get("universe", {}).get("chapters"))
        except Exception:
            expected = None
    rows = build_table(runs, expected_chapters=expected)
    agg = aggregate_by_model(runs, expected_chapters=expected)
    md = render_markdown(rows, agg)
    # 榜单进报表：此前 leaderboard/render_leaderboard 有实现有单测却没人调用，
    # 于是「静态榜单页已就绪」是一句空话——页面并不存在。
    md += "\n" + render_leaderboard(leaderboard(agg))
    print(md)
    if args.write:
        p = Path(args.out_dir) / "report.md"
        p.write_text(md, encoding="utf-8")
        print(f"→ {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())