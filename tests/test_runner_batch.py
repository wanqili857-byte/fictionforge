#!/usr/bin/env python3
"""
test_runner_batch.py — W7 下半：LLM 调用层（纯函数）+ 跑批编排单元测试。

零网络 / 零 LLM：网络调用不测（那是真实跑批的事），编排逻辑用注入的
假生成器与假判定器验证。覆盖：
- 请求体构造 / 响应解析（含错误路径）/ key 读取
- 矩阵展开、目录布局、manifest 字段、成本累加
- 续跑（不重复烧 token）、失败中断记录
- full 档：修前/修后分开记 + 修正稿落盘
- 前文串接（后续章的 prompt 含上一章末尾）
- 知识边界在跑批路径上仍然成立（第 1 章 prompt 不含终局真相）

用法:
    python3 tests/test_runner_batch.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bench.runner import llm, models
from bench.runner.batch import run_batch, rules_for_universe
from bench.universe.generator import generate

_PASS, _FAIL = 0, 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  FAIL: {name}")


# ── llm 纯函数 ────────────────────────────────────────────────────────

def test_request_body():
    m = models.get("kimi")
    body = llm.build_request_body(m, "SYS", "USER")
    check("请求体 model", body["model"] == m.model)
    check("请求体 system/user", [x["role"] for x in body["messages"]] == ["system", "user"])
    check("请求体 temperature/max_tokens",
          body["temperature"] == m.temperature and body["max_tokens"] == m.max_tokens)


def test_parse_response():
    good = {"choices": [{"message": {"content": "  正文  "}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 250}}
    r = llm.parse_response(good)
    check("解析正文并 strip", r["text"] == "正文" and r["error"] is None)
    check("解析 token", r["tokens_in"] == 100 and r["tokens_out"] == 250)

    err = llm.parse_response({"error": {"message": "402 Payment Required"}})
    check("解析错误对象", err["error"] == "402 Payment Required" and err["text"] == "")
    check("缺 choices 不崩", llm.parse_response({"choices": []})["error"] is not None)
    check("非对象不崩", llm.parse_response("nope")["error"] is not None)
    check("usage 缺失时 token 记 0",
          llm.parse_response({"choices": [{"message": {"content": "x"}}]})["tokens_out"] == 0)


def test_retry_policy():
    """429/超时类瞬态错误重试（正赛首轮：方舟账号级 429 + glm 思考超时打断跑批），
    400/402 类硬错误不重试。"""
    check("429 重试", llm._should_retry(429, None, 1))
    check("5xx 重试", llm._should_retry(503, None, 1))
    check("超时重试", llm._should_retry(None, "ReadTimeout", 1))
    check("SSL 抖动重试", llm._should_retry(None, "SSLError", 1))
    check("400 不重试", not llm._should_retry(400, None, 1))
    check("402 不重试", not llm._should_retry(402, None, 1))
    check("404 不重试", not llm._should_retry(404, None, 1))
    check("超次数不重试", not llm._should_retry(429, None, 3))
    check("末次不重试", not llm._should_retry(None, "ReadTimeout", 3))


def test_empty_content_is_error():
    """空正文必须算 error：思考型模型把 max_tokens 烧光时正文为空，
    若照常入库，判定器对空文本判 0 违反——白拿第一（筛选实测 glm-5.3-flash）。"""
    empty = llm.parse_response({"choices": [{"finish_reason": "length",
                                             "message": {"content": ""}}],
                                "usage": {"prompt_tokens": 10, "completion_tokens": 8192}})
    check("空 content 报错", empty["error"] is not None and empty["text"] == "")
    check("错误点名 finish_reason", empty["error"] and "length" in empty["error"])
    ws = llm.parse_response({"choices": [{"finish_reason": "stop",
                                          "message": {"content": "  \n "}}]})
    check("纯空白同样报错", ws["error"] is not None)
    ok = llm.parse_response({"choices": [{"finish_reason": "stop",
                                          "message": {"content": "正文"}}]})
    check("正常正文不报错", ok["error"] is None and ok["text"] == "正文")


def test_load_keys():
    d = Path(tempfile.mkdtemp()) / ".env"
    d.write_text("OPENROUTER_API_KEY=sk-or-test\nDEEPSEEK_API_KEY=sk-ds-test\n"
                 "OTHER_TOKEN=should_not_load\n", encoding="utf-8")
    keys = llm.load_keys(str(d))
    check("只读需要的 key", set(keys) == {"OPENROUTER_API_KEY", "DEEPSEEK_API_KEY"})
    check("值正确", keys["DEEPSEEK_API_KEY"] == "sk-ds-test")
    check("缺文件返回空", llm.load_keys("/tmp/绝对不存在-.env") == {})


# ── 通道装配（方舟 coding plan）────────────────────────────────────────

def test_proxy_policy():
    """国内通道必须直连：requests 默认继承 macOS 系统代理，会把国内域名一起劫走
    （本机实测方舟经 127.0.0.1:7897 握手 EOF）。这条不能靠环境默认。"""
    check("方舟直连", llm.resolve_proxy("ark", {})["trust_env"] is False)
    check("DeepSeek 直连", llm.resolve_proxy("deepseek", {})["trust_env"] is False)
    check("OpenRouter 走系统代理", llm.resolve_proxy("openrouter", {})["trust_env"] is True)
    check("未知通道默认系统代理", llm.resolve_proxy("mystery", {})["trust_env"] is True)

    d = llm.resolve_proxy("ark", {"LCB_PROXY": "direct"})
    check("LCB_PROXY=direct 覆盖为直连", d["trust_env"] is False and d["proxies"] is None)
    o = llm.resolve_proxy("ark", {"LCB_PROXY": "http://127.0.0.1:9999"})
    check("LCB_PROXY 覆盖为显式代理",
          o["trust_env"] is False and o["proxies"]["https"] == "http://127.0.0.1:9999")
    check("LCB_PROXY 空串视为未设置",
          llm.resolve_proxy("ark", {"LCB_PROXY": "  "})["source"] == "policy:direct")


def test_ark_provider_wiring():
    """方舟 coding plan：端点路径、key 名、目录条目。路径错一个字符就全盘 404。"""
    check("方舟端点走 /api/coding/v3",
          llm._BASE_URLS["ark"].endswith("/api/coding/v3/chat/completions"))
    check("方舟 key 名 ARK_API_KEY", llm._KEY_NAMES["ark"] == "ARK_API_KEY")
    d = Path(tempfile.mkdtemp()) / ".env"
    d.write_text("ARK_API_KEY=ark-test\nDEEPSEEK_API_KEY=sk-ds\n", encoding="utf-8")
    check("ARK_API_KEY 能被读出", llm.load_keys(str(d)).get("ARK_API_KEY") == "ark-test")

    ark = {a: s for a, s in models.CATALOG.items() if s.provider == "ark"}
    check("方舟目录非空", len(ark) >= 6)
    check("方舟条目全为订阅制",
          all(s.billing == "subscription" for s in ark.values()))
    check("方舟条目的 model id 非空且无空格",
          all(s.model and " " not in s.model for s in ark.values()))
    check("方舟覆盖三个家族",
          {s.family for s in ark.values()} >= {"doubao", "glm", "deepseek"})
    check("思考型模型给足 max_tokens（推理 token 占 completion）",
          all(s.max_tokens >= 4096 for s in ark.values()))
    check("别名与 model id 不重名", len({s.model for s in ark.values()}) == len(ark))


def test_subscription_billing_is_zero_not_fake_price():
    """订阅通道边际成本 = 0，且不能靠 price 字段伪装成按量计费。"""
    s = models.get("ark-db-lite")
    check("订阅模型成本恒为 0", s.cost(100000, 100000) == 0.0)
    fq = models.get("dash-kimi-k3")
    check("免费额度模型成本恒为 0", fq.cost(100000, 100000) == 0.0)
    check("免费额度 billing 标记", fq.billing == "free_quota")
    check("按量模型照常计价", models.get("ds-flash").cost(1_000_000, 0) == 0.05)


def test_dashscope_wiring():
    """百炼聚合渠道：端点、key 名、直连策略、kimi/qwen 家族覆盖。"""
    check("百炼端点 compatible-mode",
          llm._BASE_URLS["dashscope"].endswith("/compatible-mode/v1/chat/completions"))
    check("百炼 key 名", llm._KEY_NAMES["dashscope"] == "DASHSCOPE_API_KEY")
    check("百炼直连", llm.resolve_proxy("dashscope", {})["trust_env"] is False)
    check("kimi 家族补上了（方舟没有）",
          models.families(["dash-kimi-k3"]) == {"moonshot"})
    check("qwen 家族回到目录（openrouter 停用后）",
          models.get("dash-qwen-max").family == "qwen"
          and models.get("dash-qwen-flash").family == "qwen")


def test_channel_gate_fails_fast():
    """通道不可用要在跑批前中止（首轮就是跑到一半撞 402，留下废行）。"""
    u = generate(seed=7, chapters=2)
    out = Path(tempfile.mkdtemp())
    raised = None
    try:
        run_batch(u, ["kimi"], tiers=("bare",), k=1, out_dir=out,
                  generate_fn=None, log=lambda *_: None)
    except RuntimeError as e:
        raised = str(e)
    check("停用通道真调用前中止", raised is not None and "通道不可用" in raised)
    check("中止信息点名通道与原因", raised and "openrouter" in raised)
    check("中止时不产生任何运行目录",
          not any(p.is_dir() for p in out.iterdir()))
    # 注入假生成器 = 不触网 → 通道状态与编排无关，不该被拦
    res = run_batch(u, ["kimi"], tiers=("bare",), k=1, out_dir=Path(tempfile.mkdtemp()),
                    generate_fn=FakeGen(), judge_fn=fake_judge, log=lambda *_: None)
    check("注入假生成器时不查通道", len(res["runs"]) == 1)


def test_summary_records_billing():
    u = generate(seed=7, chapters=2)
    out = Path(tempfile.mkdtemp())
    res = run_batch(u, ["ark-db-lite"], tiers=("bare",), k=1, out_dir=out,
                    generate_fn=FakeGen(), judge_fn=fake_judge, log=lambda *_: None)
    s = res["runs"][0]
    check("summary 记 provider", s["provider"] == "ark")
    check("summary 记 billing", s["billing"] == "subscription")
    check("订阅通道成本为 0", s["cost"] == 0.0)
    man = json.loads((out / "ark-db-lite__bare__k0" / "run.json").read_text(encoding="utf-8"))
    check("manifest 也带订阅口径", man["_summary"]["billing"] == "subscription")


# ── 跑批编排（注入假件）───────────────────────────────────────────────

class FakeGen:
    """假生成器：记录收到的 prompt，返回确定性文本 + 假 token 数。"""
    def __init__(self, text="他停住。雾压下来。", error_at=None):
        self.calls = []
        self.text = text
        self.error_at = error_at

    def __call__(self, spec, system, user):
        self.calls.append({"model": spec.alias, "system": system, "user": user})
        if self.error_at is not None and len(self.calls) == self.error_at:
            return {"text": "", "tokens_in": 0, "tokens_out": 0,
                    "error": "HTTP 500: boom", "cost": 0.0}
        return {"text": self.text, "tokens_in": 100, "tokens_out": 200,
                "cost": spec.cost(100, 200), "error": None}


def fake_judge(text, chapter):
    from bench.contracts import Violation, ViolationType, DetectorKind, Severity
    out = []
    if "忽然" in text:
        out.append(Violation(probe_id=f"cons-forbidden-ch{chapter}",
                             type=ViolationType.CONSTRAINT,
                             detector=DetectorKind.MECHANICAL, chapter=chapter,
                             severity=Severity.FATAL,
                             evidence={"word": "忽然", "count": text.count("忽然")},
                             run_id="test"))
    return out


def test_matrix_and_layout():
    u = generate(seed=7, chapters=3)
    out = Path(tempfile.mkdtemp())
    gen = FakeGen()
    res = run_batch(u, ["ds-flash", "glm-flash"], tiers=("bare", "mid", "full"),
                    k=1, out_dir=out, generate_fn=gen, judge_fn=fake_judge, log=lambda *_: None)
    check("矩阵展开 6 个运行", len(res["runs"]) == 6)
    check("results.json 落盘", (out / "results.json").exists())
    d = out / "ds-flash__mid__k0"
    check("运行目录布局", (d / "ch1.md").exists() and (d / "run.json").exists()
          and (d / "violations.json").exists())
    man = json.loads((d / "run.json").read_text(encoding="utf-8"))
    check("manifest 字段齐", man["run_id"] == "ds-flash__mid__k0"
          and man["harness"] == "mid" and man["k_index"] == 0
          and man["bench"]["universe_seed"] == 7
          and man["bench"]["universe_generator_version"] == u.version)
    check("prompt_hashes 每章一条", len(man["prompt_hashes"]) == 3)
    check("成本按模型价格算", man["cost"]["currency_cost"] > 0)
    cheap = next(r for r in res["runs"] if r["model"] == "ds-flash")
    pricey = next(r for r in res["runs"] if r["model"] == "glm-flash")
    check("贵模型成本更高", pricey["cost"] > cheap["cost"])
    check("调用次数 = 模型×档位×章", len(gen.calls) == 2 * 3 * 3)


def test_resume_skips_generation_and_keeps_cost():
    u = generate(seed=7, chapters=3)
    out = Path(tempfile.mkdtemp())
    gen1 = FakeGen()
    r1 = run_batch(u, ["ds-flash"], tiers=("mid",), k=1, out_dir=out,
                   generate_fn=gen1, judge_fn=fake_judge, log=lambda *_: None)
    n1 = len(gen1.calls)
    gen2 = FakeGen()
    r2 = run_batch(u, ["ds-flash"], tiers=("mid",), k=1, out_dir=out,
                   generate_fn=gen2, judge_fn=fake_judge, log=lambda *_: None)
    check("续跑不重复调用 LLM", n1 == 3 and len(gen2.calls) == 0)
    # 逐章用量账本：续跑后成本/token 不丢
    check("续跑保留成本", r2["runs"][0]["cost"] == r1["runs"][0]["cost"])
    check("续跑保留 token", r2["runs"][0]["tokens_in"] == r1["runs"][0]["tokens_in"]
          and r2["runs"][0]["tokens_out"] == r1["runs"][0]["tokens_out"])
    check("usage.jsonl 落盘", (out / "ds-flash__mid__k0" / "usage.jsonl").exists())


def test_resume_skips_only_completed_runs():
    """带错误中断的 run 也写过 run.json——续跑必须 redo 它，不能静默冒充完成。"""
    u = generate(seed=7, chapters=3)
    out = Path(tempfile.mkdtemp())
    gen = FakeGen(error_at=2)   # ch2 失败：ch1 生成、run.json 带 errors 落盘
    r1 = run_batch(u, ["ds-flash"], tiers=("mid",), k=1, out_dir=out,
                   generate_fn=gen, judge_fn=fake_judge, log=lambda *_: None)
    check("首轮中断留残缺 manifest", r1["runs"][0]["errors"]
          and r1["runs"][0]["chapters_done"] == 1)
    gen2 = FakeGen()
    r2 = run_batch(u, ["ds-flash"], tiers=("mid",), k=1, out_dir=out,
                   generate_fn=gen2, judge_fn=fake_judge, log=lambda *_: None)
    check("残缺 run 被 redo 而非 skip", len(gen2.calls) > 0)
    check("redo 后补齐到完整", r2["runs"][0]["chapters_done"] == 3
          and not r2["runs"][0]["errors"])
    gen3 = FakeGen()
    r3 = run_batch(u, ["ds-flash"], tiers=("mid",), k=1, out_dir=out,
                   generate_fn=gen3, judge_fn=fake_judge, log=lambda *_: None)
    check("补齐后的 run 才真正 skip", len(gen3.calls) == 0
          and r3["runs"][0]["chapters_done"] == 3)


def test_full_tier_pre_post_and_fix():
    u = generate(seed=7, chapters=2)
    out = Path(tempfile.mkdtemp())
    gen = FakeGen(text="他忽然停住。忽然起风。")
    res = run_batch(u, ["ds-flash"], tiers=("full",), k=1, out_dir=out,
                    generate_fn=gen, judge_fn=fake_judge, log=lambda *_: None)
    v = json.loads((out / "ds-flash__full__k0" / "violations.json").read_text(encoding="utf-8"))
    check("修前有违反", len(v["pre_fix"]) == 2)          # 两章各一条禁词
    check("修后无违反（门禁删了禁词）", v["post_fix"] == [])
    fixed = (out / "ds-flash__full__k0" / "ch1.fixed.md")
    check("修正稿落盘", fixed.exists() and "忽然" not in fixed.read_text(encoding="utf-8"))
    check("summary 记修前修后", res["runs"][0]["violations_pre_fix"] == 2
          and res["runs"][0]["violations_post_fix"] == 0)


def test_prior_text_chaining_and_boundary():
    u = generate(seed=11, chapters=3)
    out = Path(tempfile.mkdtemp())
    gen = FakeGen(text="第一章尾巴标记XYZZY。")
    run_batch(u, ["ds-flash"], tiers=("mid",), k=1, out_dir=out,
              generate_fn=gen, judge_fn=fake_judge, log=lambda *_: None)
    check("第 2 章 prompt 含上一章末尾", "XYZZY" in gen.calls[1]["user"])
    check("第 1 章 prompt 不含前文标记", "XYZZY" not in gen.calls[0]["user"])
    terminal = next(f for f in u.truth_table if f["id"] == u.terminal_fact_id)["statement"]
    check("第 1 章 prompt 不含终局真相（知识边界）", terminal not in gen.calls[0]["user"])
    # 档位差异：mid 有世界设定段，bare 没有
    check("mid 档含世界设定段", "世界设定" in gen.calls[0]["user"])
    gen_bare = FakeGen()
    run_batch(u, ["ds-flash"], tiers=("bare",), k=1, out_dir=Path(tempfile.mkdtemp()),
              generate_fn=gen_bare, judge_fn=fake_judge, log=lambda *_: None)
    check("bare 档无世界设定段/无前文", "世界设定" not in gen_bare.calls[0]["user"]
          and "前文末尾" not in gen_bare.calls[1]["user"])


def test_error_breaks_but_records():
    u = generate(seed=7, chapters=3)
    out = Path(tempfile.mkdtemp())
    gen = FakeGen(error_at=2)
    res = run_batch(u, ["ds-flash"], tiers=("mid",), k=1, out_dir=out,
                    generate_fn=gen, judge_fn=fake_judge, log=lambda *_: None)
    run = res["runs"][0]
    check("失败中断并记录错误", run["errors"] and run["chapters_done"] == 1)
    check("已生成章节保留", (out / "ds-flash__mid__k0" / "ch1.md").exists())


def test_empty_generation_is_recorded_not_written():
    """空正文 = 错误，不落盘、不进判定——否则空文本 0 违反会污染榜单。"""
    u = generate(seed=7, chapters=3)
    out = Path(tempfile.mkdtemp())
    gen = FakeGen(text="   ")
    res = run_batch(u, ["ds-flash"], tiers=("bare",), k=1, out_dir=out,
                    generate_fn=gen, judge_fn=fake_judge, log=lambda *_: None)
    run = res["runs"][0]
    check("空正文记为错误", run["errors"] and "正文为空" in run["errors"][0]["error"])
    check("空正文不落盘", not (out / "ds-flash__mid__k0" / "ch1.md").exists()
          and not (out / "ds-flash__bare__k0" / "ch1.md").exists())
    check("空正文书目数为 0", run["chapters_done"] == 0)


def test_rules_from_universe():
    u = generate(seed=7, chapters=3)
    r = rules_for_universe(u, 1)
    check("规则取宇宙禁词", r.forbidden_words == u.facts_forbidden)
    check("规则取主角与人称", r.protagonist == u.protagonist
          and r.protagonist_pronoun in ("她", "他"))
    check("规则含全 cast 性别", set(r.cast_genders) == {c["name"] for c in u.cast})
    check("篇幅目标来自 spec", r.target_chars == u.specs[0]["target_chars"])


def test_rejudge_without_regeneration():
    """判定器升级后必须能只重判、不重跑（文本缓存，判决可重算）。"""
    u = generate(seed=7, chapters=3)
    out = Path(tempfile.mkdtemp())
    gen1 = FakeGen(text="他忽然停住。")
    run_batch(u, ["ds-flash"], tiers=("mid",), k=1, out_dir=out,
              generate_fn=gen1, judge_fn=fake_judge, log=lambda *_: None)
    v1 = json.loads((out / "ds-flash__mid__k0" / "violations.json").read_text(encoding="utf-8"))
    check("首次判定有违反", len(v1["pre_fix"]) == 3)

    gen2 = FakeGen()
    def strict_judge(text, chapter):
        return []          # 假装判定器改了：全部放行
    r2 = run_batch(u, ["ds-flash"], tiers=("mid",), k=1, out_dir=out,
                   generate_fn=gen2, judge_fn=strict_judge, rejudge=True,
                   log=lambda *_: None)
    check("rejudge 不调用 LLM", len(gen2.calls) == 0)
    v2 = json.loads((out / "ds-flash__mid__k0" / "violations.json").read_text(encoding="utf-8"))
    check("判决已按新判定器重算", v2["pre_fix"] == [])
    check("summary 同步更新", r2["runs"][0]["violations_pre_fix"] == 0)
    check("正文未被改动",
          (out / "ds-flash__mid__k0" / "ch1.md").read_text(encoding="utf-8").strip() == "他忽然停住。")


if __name__ == "__main__":
    test_request_body()
    test_parse_response()
    test_empty_content_is_error()
    test_load_keys()
    test_proxy_policy()
    test_retry_policy()
    test_ark_provider_wiring()
    test_subscription_billing_is_zero_not_fake_price()
    test_dashscope_wiring()
    test_channel_gate_fails_fast()
    test_summary_records_billing()
    test_matrix_and_layout()
    test_resume_skips_generation_and_keeps_cost()
    test_resume_skips_only_completed_runs()
    test_full_tier_pre_post_and_fix()
    test_prior_text_chaining_and_boundary()
    test_error_breaks_but_records()
    test_empty_generation_is_recorded_not_written()
    test_rules_from_universe()
    test_rejudge_without_regeneration()
    print(f"\n结果: {_PASS}/{_PASS + _FAIL} 通过")
    sys.exit(1 if _FAIL else 0)