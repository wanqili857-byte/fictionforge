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


def test_load_keys():
    d = Path(tempfile.mkdtemp()) / ".env"
    d.write_text("OPENROUTER_API_KEY=sk-or-test\nDEEPSEEK_API_KEY=sk-ds-test\n"
                 "OTHER_TOKEN=should_not_load\n", encoding="utf-8")
    keys = llm.load_keys(str(d))
    check("只读需要的 key", set(keys) == {"OPENROUTER_API_KEY", "DEEPSEEK_API_KEY"})
    check("值正确", keys["DEEPSEEK_API_KEY"] == "sk-ds-test")
    check("缺文件返回空", llm.load_keys("/tmp/绝对不存在-.env") == {})


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


def test_resume_skips_generation():
    u = generate(seed=7, chapters=3)
    out = Path(tempfile.mkdtemp())
    gen1 = FakeGen()
    run_batch(u, ["ds-flash"], tiers=("mid",), k=1, out_dir=out,
              generate_fn=gen1, judge_fn=fake_judge, log=lambda *_: None)
    n1 = len(gen1.calls)
    gen2 = FakeGen()
    run_batch(u, ["ds-flash"], tiers=("mid",), k=1, out_dir=out,
              generate_fn=gen2, judge_fn=fake_judge, log=lambda *_: None)
    check("续跑不重复调用 LLM", n1 == 3 and len(gen2.calls) == 0)


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


def test_rules_from_universe():
    u = generate(seed=7, chapters=3)
    r = rules_for_universe(u, 1)
    check("规则取宇宙禁词", r.forbidden_words == u.facts_forbidden)
    check("规则取主角与人称", r.protagonist == u.protagonist
          and r.protagonist_pronoun in ("她", "他"))
    check("规则含全 cast 性别", set(r.cast_genders) == {c["name"] for c in u.cast})
    check("篇幅目标来自 spec", r.target_chars == u.specs[0]["target_chars"])


if __name__ == "__main__":
    test_request_body()
    test_parse_response()
    test_load_keys()
    test_matrix_and_layout()
    test_resume_skips_generation()
    test_full_tier_pre_post_and_fix()
    test_prior_text_chaining_and_boundary()
    test_error_breaks_but_records()
    test_rules_from_universe()
    print(f"\n结果: {_PASS}/{_PASS + _FAIL} 通过")
    sys.exit(1 if _FAIL else 0)