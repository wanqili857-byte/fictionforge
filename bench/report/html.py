#!/usr/bin/env python3
"""bench.report.html — 静态榜单页生成器（零依赖，纯字符串拼接）。

产出**自包含**的单页：内联 SVG 分组柱状图 + 全部表格 + 复现命令。
无外部资源、无 JS（悬浮提示用 SVG 原生 `<title>`），可直接丢到 GitHub Pages。

设计约束（来自可视化规范，不是随手挑的）：
- 形式：量级比较 → 分组柱状图；x = 三档 harness（有序：裸/注入/满），
  系列 = 模型（分类色，**固定顺序，按实体分配，不随名次变动**）
- 配色：分类槽 1/2/3（blue/orange/aqua），明暗两套都跑过校验脚本；
  浅色下 aqua 对比度 2.74 触发 WARN → 以「每根柱直接标值 + 下方完整表格」作为
  必需的补偿（规范里 WARN 不可忽略，必须给可见标签或表格视图）
- 非颜色编码：图例常在 + 直接标值，identity 不靠颜色单独承载
- 深色模式是**另选步进**，不是自动翻转
- 柱端 4px 圆角、相邻柱之间 2px 表面留白、网格线退到背景
"""

import html
import json
from pathlib import Path

# ── 配色（与 dataviz 参考调色板一致；改动需重跑校验脚本）────────────────
# **单一事实源**：CSS 里的 `--s1..--sN` 与图表/图例的取色都由这张表生成。
# 旧版把 3 组颜色写死在 CSS、把 `SERIES` 定义在 Python 却从不引用——
# 于是第 4 个模型（BENCH_PROTOCOL §8 明确欢迎外部提交）取 `var(--s4)` 时
# 变量不存在，柱与图例色块一起退化成同一个默认色，**互相不可区分**
# （第一轮 doubao S15，当时 3 模型未触发，漏进了台账；第二轮 codex 重新发现）。
# 现在颜色跟随**模型身份**（短名匹配），不是位置索引——名次变动不会重新上色。
SERIES = [  # (模型短名, 亮色, 暗色)
    ("glm-flash", "#2a78d6", "#3987e5"),
    ("ds-flash", "#eb6834", "#d95926"),
    ("db-lite", "#1baf7a", "#199e70"),
    # 备用槽位：roster 扩到 4+ 时不会无颜色可用。超出这张表则报错，
    # 不静默循环配色（dataviz：分类色不得循环使用）
    ("(备用 4)", "#8a5cd6", "#a07ae0"),
    ("(备用 5)", "#c2913a", "#d9a94f"),
    ("(备用 6)", "#2f9fb5", "#43b6cb"),
]
TIERS = [("bare", "裸写"), ("mid", "注入上下文"), ("full", "满配门禁")]


def series_color(model: str, index: int = 0) -> str:
    """模型 → 该系列的颜色变量名。

    ① 先按短名匹配 `SERIES`——颜色跟**模型身份**走，名次变动不重新上色；
    ② 匹配不上（新模型）按**位置**取槽位，保证不与已匹配的撞色；
    ③ 超过槽位数一律归到 `--s-over`（中性灰），**不循环配色**——
       循环会让两个不同模型拿到同一个颜色，那正是这个函数要防的事。
    """
    for i, (short, _light, _dark) in enumerate(SERIES):
        if short and short in (model or ""):
            return f"var(--s{i + 1})"
    return f"var(--s{index + 1})" if index < len(SERIES) else "var(--s-over)"


def _series_vars() -> str:
    """由 `SERIES` 生成 `--s1..--sN`（亮/暗两套），供 CSS 注入。"""
    light = " ".join(f"--s{i + 1}:{c[1]};" for i, c in enumerate(SERIES))
    dark = " ".join(f"--s{i + 1}:{c[2]};" for i, c in enumerate(SERIES))
    return light, dark


_CSS = """
:root{
  color-scheme: light;
  --surface-1:#fcfcfb; --surface-2:#f4f4f2; --border:#dcdcd6;
  --text-primary:#0b0b0b; --text-secondary:#52514e; --text-muted:#7a7975;
  /*SERIES_LIGHT*/ --s-over:#8a8a84; --grid:#e6e6e1;
}
@media (prefers-color-scheme: dark){
  :root:where(:not([data-theme="light"])){
    color-scheme: dark;
    --surface-1:#1a1a19; --surface-2:#232322; --border:#3a3a37;
    --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#95948a;
    /*SERIES_DARK*/ --s-over:#9a9a93; --grid:#33332f;
  }
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --surface-1:#1a1a19; --surface-2:#232322; --border:#3a3a37;
  --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#95948a;
  /*SERIES_DARK*/ --s-over:#9a9a93; --grid:#33332f;
}
*{box-sizing:border-box}
body{margin:0;padding:28px 20px 64px;background:var(--surface-1);color:var(--text-primary);
  font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB",
  "Microsoft YaHei",sans-serif;}
main{max-width:980px;margin:0 auto}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.01em}
h2{font-size:17px;margin:36px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--border)}
h3{font-size:14px;margin:22px 0 8px;color:var(--text-secondary);font-weight:600}
p{margin:8px 0}
.lede{color:var(--text-secondary);margin:0 0 4px}
.prov{color:var(--text-muted);font-size:13px;margin-top:10px}
.card{background:var(--surface-2);border:1px solid var(--border);border-radius:10px;
  padding:14px 16px;margin:14px 0}
.finding{border-left:3px solid var(--s2)}
.finding b{color:var(--text-primary)}
table{border-collapse:collapse;width:100%;margin:10px 0;font-size:14px}
th,td{text-align:right;padding:7px 10px;border-bottom:1px solid var(--border)}
th:first-child,td:first-child{text-align:left}
th{color:var(--text-secondary);font-weight:600;font-size:13px;background:var(--surface-2)}
tbody tr:hover{background:var(--surface-2)}
.mut{color:var(--text-muted);font-size:13px}
code{background:var(--surface-2);border:1px solid var(--border);border-radius:4px;
  padding:1px 5px;font-size:13px}
pre{background:var(--surface-2);border:1px solid var(--border);border-radius:8px;
  padding:12px 14px;overflow-x:auto;font-size:13px;line-height:1.5}
a{color:var(--s1)}
ul{margin:8px 0;padding-left:22px}
li{margin:4px 0}
.legend{display:flex;flex-wrap:wrap;gap:16px;margin:10px 0 2px;font-size:13px}
.legend span{display:flex;align-items:center;gap:6px;color:var(--text-secondary)}
.sw{width:11px;height:11px;border-radius:3px;display:inline-block}
.chart-wrap{overflow-x:auto}
svg{display:block;max-width:100%;height:auto}
.tblwrap{overflow-x:auto}
footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--border);
  color:var(--text-muted);font-size:13px}
"""


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _fmt(v) -> str:
    return "—" if v is None else f"{v:g}"


# ── 图：分组柱状（x=三档，系列=模型）──────────────────────────────────

def _css() -> str:
    """把 SERIES 展开进 CSS——颜色只有一处定义（SERIES）。"""
    light, dark = _series_vars()
    return (_CSS.replace("/*SERIES_LIGHT*/", light)
                 .replace("/*SERIES_DARK*/", dark))


def render_chart(core_by_model_tier: dict, models: list, width: int = 760,
                 height: int = 300) -> str:
    """core_by_model_tier: {模型: {档位: 值}}；models 决定系列顺序（固定，按实体）。"""
    pad_l, pad_r, pad_t, pad_b = 52, 16, 22, 46
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    vals = [core_by_model_tier.get(m, {}).get(t, 0) for m in models for t, _ in TIERS]
    vmax = max(vals + [1.0])
    top = max(3.0, (int(vmax / 3) + 1) * 3)          # 轴上界取 3 的倍数
    y = lambda v: pad_t + plot_h - (v / top) * plot_h

    group_w = plot_w / len(TIERS)
    bar_w = min(26.0, group_w * 0.22)
    gap = 2.0                                         # 相邻柱之间的表面留白
    total_w = len(models) * bar_w + (len(models) - 1) * gap

    p = [f'<svg viewBox="0 0 {width} {height}" role="img" '
         f'aria-label="各模型在三档 harness 下的核心违反率对比，越低越好">']
    # 网格（退到背景）+ y 轴刻度
    for i in range(0, int(top) + 1, 3):
        yy = y(i)
        p.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{pad_l + plot_w}" y2="{yy:.1f}" '
                 f'stroke="var(--grid)" stroke-width="1"/>')
        p.append(f'<text x="{pad_l - 10}" y="{yy + 4:.1f}" text-anchor="end" '
                 f'font-size="11" fill="var(--text-muted)">{i}</text>')
    p.append(f'<text x="{pad_l - 10}" y="{pad_t - 8}" text-anchor="end" font-size="11" '
             f'fill="var(--text-muted)">违反/万字</text>')
    # 轴
    p.append(f'<line x1="{pad_l}" y1="{pad_t + plot_h}" x2="{pad_l + plot_w}" '
             f'y2="{pad_t + plot_h}" stroke="var(--border)" stroke-width="1"/>')

    for gi, (tier, tier_label) in enumerate(TIERS):
        cx = pad_l + group_w * (gi + 0.5)
        x0 = cx - total_w / 2
        for mi, model in enumerate(models):
            v = core_by_model_tier.get(model, {}).get(tier, 0) or 0
            bx = x0 + mi * (bar_w + gap)
            color = series_color(models[mi], mi)
            h = max(2.0, (v / top) * plot_h)          # 0 也留 2px 残迹：柱存在感 > 无
            by = pad_t + plot_h - h
            p.append(
                f'<path d="M{bx:.1f},{pad_t + plot_h:.1f} L{bx:.1f},{by + 4:.1f} '
                f'Q{bx:.1f},{by:.1f} {bx + 4:.1f},{by:.1f} '
                f'L{bx + bar_w - 4:.1f},{by:.1f} Q{bx + bar_w:.1f},{by:.1f} '
                f'{bx + bar_w:.1f},{by + 4:.1f} L{bx + bar_w:.1f},{pad_t + plot_h:.1f} Z" '
                f'fill="{color}"><title>{_esc(model)} · {_esc(tier_label)}: '
                f'{_fmt(round(v, 2))} 违反/万字</title></path>')
            # 直接标值（≥2 系列时前 4 个必须直标；同时补足浅色对比度的 relief）
            p.append(f'<text x="{bx + bar_w / 2:.1f}" y="{by - 5:.1f}" text-anchor="middle" '
                     f'font-size="10.5" fill="var(--text-secondary)">{v:.2f}</text>')
        p.append(f'<text x="{cx:.1f}" y="{pad_t + plot_h + 20:.1f}" text-anchor="middle" '
                 f'font-size="12" fill="var(--text-secondary)">{_esc(tier_label)}</text>')
    p.append("</svg>")
    return "\n".join(p)


def render_legend(models: list) -> str:
    items = "".join(
        f'<span><i class="sw" style="background:{series_color(m, i)}"></i>{_esc(m)}</span>'
        for i, m in enumerate(models))
    return f'<div class="legend">{items}<span class="mut">颜色跟随模型，不随名次变动</span></div>'


# ── 表格 ──────────────────────────────────────────────────────────────

def _table(headers: list, rows: list) -> str:
    th = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tblwrap"><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>'


def _harness_finding(agg):
    """「上下文工程对谁帮助最大」——**从数据现算**，不写死句子。

    写死的代价先前付过一次：初版页面上钉着一句「给 glm 注入上下文，它反而更差」，
    那是当时被头条推翻的结论；数据改了两轮，卡片照旧渲染，页面成了自证。
    """
    deltas = []
    for a in agg or []:
        t = a.get("core_rate_by_tier") or {}
        if t.get("bare") is None or t.get("mid") is None:
            continue
        deltas.append((a["model"], round(t["bare"] - t["mid"], 2)))   # 正 = 注入后更好
    if not deltas:
        return ""
    deltas.sort(key=lambda x: -x[1])
    best, bd = deltas[0]
    body = f"<b>{_esc(best)}</b> 受益最大（裸写→注入上下文 {bd:+.2f} 违反/万）"
    if len(deltas) > 1:
        worst, wd = deltas[-1]
        body += f"；<b>{_esc(worst)}</b> 最小（{wd:+.2f}）"
    return ('<div class="card finding"><b>上下文工程对谁有用：</b>' + body +
            "。<b>「哪个模型最好」取决于你把它装进什么样的管线。</b></div>")


def _state_axis_card(runs):
    """状态轴在入库产物里贡献了多少——**现算**，并如实说明它意味着什么。

    这条必须常驻页面：一个叫「长程一致性」的基准，读者有权第一眼知道
    一致性判据在这批产物里命中了几条。零命中是观测（对照见
    `python3 -m bench.calib.corpus_control bench/results/v2`），不是判据失效。

    口径：直接数**产物里的全部判决**（修前 + 修后），不跟着下表走——
    下表 full 档用门禁后的数字，混着算会让「合计」变成两种口径的和。
    """
    from bench.report.metrics import count_by_family
    # 对照数字**现算**，不抄文档：抄一次就会漂一次（本页此前正因写死结论而翻车）。
    # 0.3 秒、零 LLM。
    try:
        from bench.calib.corpus_control import run_control
        cs = run_control(str(Path(__file__).resolve().parent.parent / "results" / "v2"))["summary"]
        pos_hit = sum(v["hit"] for v in cs["positive"].values())
        pos_n = sum(v["n"] for v in cs["positive"].values())
        neg_hit = sum(v["hit"] for v in cs["negative"].values())
        neg_n = sum(v["n"] for v in cs["negative"].values())
        ctrl = f"{pos_hit}/{pos_n}"
        ctrl_neg = f"{neg_hit}/{neg_n}"
    except Exception:
        ctrl, ctrl_neg = "?", "?"
    n_all = st_all = 0
    for r in (runs or {}).values():
        for key in ("pre_fix", "post_fix"):
            vs = (r.get("violations") or {}).get(key) or []
            n_all += len(vs)
            st_all += count_by_family(vs).get("state", 0)
    if not n_all:
        return ""
    return ('<div class="card finding"><b>状态轴的实际贡献：</b>'
            f"入库产物的<b>全部 {n_all} 条判决</b>里，状态类"
            f"（死人复活 / 天数倒退）<b>{st_all}</b> 条——本批语料在一致性轴上"
            "<b>没有区分度</b>。同一批文本注入真违反后判据 333/333 全中、"
            "孪生负例 0/297 误报，所以这是观测不是失效；"
            "榜单实际排序的是文体与篇幅。"
            "复现：<code>python3 -m bench.calib.corpus_control bench/results/v2</code>"
            "</div>")


def render_page(out_dir) -> str:
    """从跑批目录（含各 run 子目录）生成整页 HTML。"""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from bench.report import metrics as M

    runs = M.load_runs(out_dir)
    if not runs:
        raise SystemExit(f"{out_dir} 下没有可读的 run")
    expected = None
    rj = Path(out_dir) / "results.json"
    if rj.exists():
        expected = (json.loads(rj.read_text(encoding="utf-8"))
                    .get("universe", {}).get("chapters"))
    rows = M.build_table(runs, expected_chapters=expected)
    agg = M.aggregate_by_model(runs, expected_chapters=expected)
    board = M.leaderboard(agg)

    by_model = {a["model"]: a["core_rate_by_tier"] for a in agg}
    models = [a["model"] for a in agg]

    sample = json.loads((sorted(Path(out_dir).glob("*/run.json"))[0]).read_text("utf-8"))
    seed = sample["bench"]["universe_seed"]
    chapters = sample["bench"].get("chapters") or expected
    judge_ver = sample["bench"]["judge_mechanical_version"]
    gen = sample["bench"]["universe_generator_version"]
    when = (sample.get("generated_at") or "")[:10]
    channels = sorted({(r.get("summary") or {}).get("provider") or "?"
                       for r in runs.values()})

    # 榜单表
    def _pair(rate, ab):
        """密度（绝对）——两者成对是本项目自己的协议要求（§8-4）。"""
        if rate is None:
            return "—"
        return f"{_fmt(rate)}（{ab:g}）" if ab is not None else _fmt(rate)

    board_rows = [[e["rank"], _esc(e["model"]),
                   _pair(e["bare"], e.get("bare_abs")),
                   _pair(e.get("mid"), e.get("mid_abs")),
                   _pair(e.get("full"), e.get("full_abs")),
                   M._billing_label(e.get("billing", "per_token")),
                   ",".join(e["missing"]) or "—"] for e in board]

    # 每次运行表
    run_rows = []
    for r in sorted(rows, key=lambda x: (x["model"], x["tier"])):
        if r["incomplete"]:
            run_rows.append([_esc(r["run_id"]), "未完成", "—", "—", "—", "—"])
            continue
        fixed = (f"{r['pre_fix_abs']}→{r['post_fix_abs']}"
                 if r["post_fix_abs"] is not None else "—")
        run_rows.append([_esc(r["run_id"]), r["chars"], r["violations_abs"],
                         r["core_abs"], _fmt(r["core_per_10k"]), fixed])

    # 门禁配对
    gate_rows = []
    for a in agg:
        g = a["gate_paired"]
        if g["full_runs"]:
            gate_rows.append([_esc(a["model"]), g["full_runs"], g["core_pre"],
                              g["core_post"], g["removed"]])

    # 抗刷分：现算（不抄文档）。基线与 writeup §8 同构：约 200 字、含 1 处禁词
    # 基线与 writeup §8 同源（包里的 BASELINE_TEXT，200 字含 1 处禁词）——
    # 页面与文档必须算同一份输入，否则两个数字会各自漂移
    from bench.attacks.attacks import (run_attack, BASELINE_TEXT,
                                       BASELINE_JARGON_TERMS)
    from bench.judges.mechanical import MechanicalRules, mechanical_judge
    rules = MechanicalRules(forbidden_words=["忽然", "突然"], para_max_chars=100,
                            target_chars=0, pov="third_limited")
    base = BASELINE_TEXT
    judge = (lambda t, ch=1: mechanical_judge(t, rules, run_id="page", chapter=ch))
    atk = []
    for name, kw in (("注水 ×6", {"factor": 6.0}),
                     ("复制 ×4", {"times": 4}),
                     ("术语轰炸（40 词）", {"terms": BASELINE_JARGON_TERMS})):
        r = run_attack({"注水 ×6": "inflate", "复制 ×4": "duplicate",
                        "术语轰炸（40 词）": "jargon_bomb"}[name], base, judge, **kw)
        delta = (r["rate_after"] / r["rate_before"] - 1) * 100 if r["rate_before"] else 0
        atk.append([name, r["chars_after"], r["abs_after"],
                    f'{r["rate_after"]:.2f}', f"{delta:+.0f}%",
                    f'{r["dup_ratio_after"]:.2f}'])

    parts = [
        "<!doctype html>", '<html lang="zh-CN">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>CanonBench · 长程一致性基准</title>",
        "<style>" + _css() + "</style>", "</head>", "<body>", "<main>",
        "<h1>CanonBench · 长程一致性基准</h1>",
        '<p class="lede">12 章的小说里，第 2 章死掉的人会不会在第 4 章端着水走过来？'
        "——把长程一致性做成可自动判定、可复现、抗刷分的尺子。</p>",
        f'<p class="prov">seed={seed} · {chapters} 章 · 判定器 <code>{_esc(judge_ver)}</code> · '
        f'宇宙生成器 <code>{_esc(gen)}</code> · 通道 {_esc("/".join(channels))} · '
        f'{_esc(when)} · k=1（方向性结论，精确归因需 k≥3）</p>',

        _harness_finding(agg),
        _state_axis_card(runs),

        "<h2>三档 harness 消融</h2>",
        '<div class="chart-wrap">', render_chart(by_model, models), "</div>",
        render_legend(models),
        '<p class="mut">纵轴为核心违反率（每万字，已剔除「篇幅合规」——篇幅属指令跟随）。'
        "越低越好。数字直接标在柱上；下表是同一份数据。</p>",

        "<h2>榜单</h2>",
        _table(["名次", "模型", "裸写 密度(绝对)", "注入上下文 密度(绝对)",
                "满配门禁 密度(绝对)", "计费", "缺档"], board_rows),
        '<p class="mut">排序键 = 裸写档核心违反率；并列同名次。每格为<b>密度（绝对违反数）</b>——'
        "密度可被加字稀释、绝对数不能，成对才不可刷。缺档位照登但不可当完整行读。</p>",

        "<h2>每次运行</h2>",
        _table(["run", "字数", "总违反", "核心违反", "核心/万字", "门禁 修前→修后"], run_rows),

        "<h2>门禁贡献（配对测量）</h2>",
        _table(["模型", "full 运行数", "修前核心", "修后核心", "擦除"], gate_rows),
        '<p class="mut">门禁只用配对测量（同一次运行内修前→修后），不用 mid↔full 差值'
        "——后者是两次独立生成，混着采样噪声。门禁只能做减法，补不了篇幅。</p>",

        "<h2>抗刷分自测（现算）</h2>",
        _table(["攻击", "字数", "绝对违反", "违反/万字", "密度变化", "重复率"], atk),
        '<p class="mut">三种攻击都能刷低密度指标——所以本基准密度与绝对数成对同报。'
        "术语轰炸无专门判据（它不是重复），只能靠篇幅门禁与绝对数兜，如实列出。</p>",

        "<h2>复现</h2>",
        "<pre>git clone https://github.com/wanqili857-byte/fictionforge.git\n"
        "cd fictionforge\n"
        "python3 -m bench.report.metrics bench/results/v2 --write   # 重算本页所有数字\n"
        "python3 tests/test_state_judge.py                          # 判定器自检</pre>",
        "<p>官方跑批产物（生成正文/判决/运行清单）随仓库发布：clone 后可离线重判、"
        "逐字节核对 prompt hash。"
        f'提交格式与校验清单见 <a href="https://github.com/wanqili857-byte/fictionforge/blob/main/docs/BENCH_PROTOCOL.md">复现协议</a>，'
        f'设计与发现见 <a href="https://github.com/wanqili857-byte/fictionforge/blob/main/docs/canonbench-writeup.md">方法文</a>，'
        f'完整注解见 <a href="https://github.com/wanqili857-byte/fictionforge/blob/main/docs/canonbench-results-v2.md">结果文档</a>。</p>',

        "<h2>已知边界（诚实清单）</h2>",
        "<ul>"
        "<li>k=1（单次采样）：档位差值含采样噪声，只能看方向；精确归因需 k≥3</li>"
        "<li>合成宇宙由模板生成，文本重复度高——当前定位是<b>校准判定器</b>，"
        "真实负载靠后续接入真实内容包</li>"
        "<li>语义判定（知识边界：谁在第几章不该知道什么）尚未实装</li>"
        "<li>机械判定口径是中文、第三人称、特定引号约定——换语言/人称/排版要改判据</li>"
        "<li>比喻/传闻词（像/仿佛/听说）会整句豁免「死人活动」判据，"
        "会漏掉「老周走过来，像往常一样…」这类真复活；收窄实测为误报 4 : 真阳性 0，故保留</li>"
        "<li>相对天数只豁免「第二天」这一惯用式</li>"
        "</ul>",

        "<footer>CanonBench 的判定器源于 "
        '<a href="https://github.com/wanqili857-byte/fictionforge">FictionForge</a>'
        " 的质量门禁；本页由 <code>bench/report/html.py</code> 从跑批产物生成。</footer>",
        "</main>", "</body>", "</html>",
    ]
    return "\n".join(parts) + "\n"


def main():
    import argparse
    ap = argparse.ArgumentParser(description="CanonBench 静态榜单页")
    ap.add_argument("out_dir", help="跑批目录（含各 run 子目录）")
    ap.add_argument("--write", default="", help="输出 HTML 路径；缺省打印到 stdout")
    a = ap.parse_args()
    page = render_page(a.out_dir)
    if a.write:
        p = Path(a.write)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(page, encoding="utf-8")
        print(f"→ {p}（{len(page) / 1024:.0f} KB）")
    else:
        print(page)


if __name__ == "__main__":
    main()
