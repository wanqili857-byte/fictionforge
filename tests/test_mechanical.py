#!/usr/bin/env python3
"""
test_mechanical.py — W1 机械判定器单元测试。

零 LLM / 零网络。覆盖（BENCH_PLAN W1 验收）：
- 禁词（逐词计数）
- 段落过长（非对话行；对话行豁免）
- 篇幅（±20%；target=0 跳过）
- POV 越界（旁白第一人称；对话豁免；非 third_limited 跳过）
- 人称混用（主角名同句 + 反性别人称 + 句内无同性别角色名才报）
- 边界：空文本 / 单句 / 规则全空
- 确定性：同输入两次结果逐项相等

用法:
    python3 tests/test_mechanical.py
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bench.contracts import Violation, ViolationType, DetectorKind, Severity
from bench.judges.mechanical import MechanicalRules, mechanical_judge

_PASS, _FAIL = 0, 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  FAIL: {name}")


def sig(vs):
    """违反集签名：排序后的 (probe_id, type, severity, 关键 evidence) 元组列表。"""
    out = []
    for v in vs:
        ev_key = v.evidence.get("word") or v.evidence.get("count") or v.evidence.get("span", "")[:12]
        out.append((v.probe_id, v.type.value, v.severity.value, ev_key))
    return sorted(out)


def types(vs):
    return sorted(v.type.value for v in vs)


# ── 禁词 ──────────────────────────────────────────────────────────────

def test_forbidden_words():
    rules = MechanicalRules(forbidden_words=["忽然", "突然"])
    text = "风忽然转了向。他突然停住。风忽然又停。"
    vs = mechanical_judge(text, rules, run_id="r1", chapter=1)
    s = sig(vs)
    check("禁词 2 词各 1 条", s == [
        ("cons-forbidden-ch1", "constraint", "fatal", "忽然"),
        ("cons-forbidden-ch1", "constraint", "fatal", "突然"),
    ])
    ev = {v.evidence["word"]: v.evidence["count"] for v in vs}
    check("忽然 计数 2", ev.get("忽然") == 2)
    check("突然 计数 1", ev.get("突然") == 1)
    check("无禁词不报", mechanical_judge("风转向了。他停住。", rules, "r1", 1) == [])


# ── 段落过长 ──────────────────────────────────────────────────────────

def test_overlength_paragraph():
    rules = MechanicalRules(para_max_chars=100)
    long_para = "字" * 120
    vs = mechanical_judge(long_para, rules, run_id="r1", chapter=1)
    check("120 字段落报 1 条", len(vs) == 1 and vs[0].type == ViolationType.CONSTRAINT
          and vs[0].severity == Severity.LOW)
    # 对话行豁免
    dialog = "「" + "字" * 130 + "」"
    check("130 字对话行豁免", mechanical_judge(dialog, rules, "r1", 1) == [])
    # 短段落不报
    check("短段落不报", mechanical_judge("字" * 99, rules, "r1", 1) == [])


# ── 篇幅 ─────────────────────────────────────────────────────────────

def test_target_length():
    rules = MechanicalRules(target_chars=1000)
    text = "字" * 1300
    vs = mechanical_judge(text, rules, run_id="r1", chapter=1)
    check("超 30% 报 1 条", len(vs) == 1 and vs[0].severity == Severity.MEDIUM)
    check("target 内不报", mechanical_judge("字" * 1000, rules, "r1", 1) == [])
    check("缺 30% 报", len(mechanical_judge("字" * 700, rules, "r1", 1)) == 1)
    check("边界 ±20% 内不报", mechanical_judge("字" * 1190, rules, "r1", 1) == [])
    check("target=0 跳过", mechanical_judge("字" * 5000, MechanicalRules(target_chars=0), "r1", 1) == [])


# ── POV 越界 ──────────────────────────────────────────────────────────

def test_pov_first_person():
    rules = MechanicalRules(pov="third_limited")
    text = "我蹲进凹陷。\n她没停手。\n「我要走了。」"
    vs = mechanical_judge(text, rules, run_id="r1", chapter=1)
    check("旁白我 报 1 条，对话豁免", len(vs) == 1
          and vs[0].type == ViolationType.CONSTRAINT and vs[0].severity == Severity.HIGH)
    check("pov=first 跳过", mechanical_judge("我蹲下。", MechanicalRules(pov="first"), "r1", 1) == [])
    vs2 = mechanical_judge("我们走了。", MechanicalRules(pov="third_limited"), "r1", 1)
    check("我们也算第一人称", len(vs2) == 1)
    # 弯引号对话豁免（真实稿用 “…” 而非 「…」——验收时抓到的误报类）
    vs3 = mechanical_judge("“叔，这个我已经捆好啦。”\n“我？”", MechanicalRules(pov="third_limited"), "r1", 1)
    check("弯引号对话豁免", vs3 == [])
    # 弯引号对话内的人称混用同样豁免
    rules_d = MechanicalRules(protagonist="江晚", protagonist_pronoun="她",
                              cast_genders={"江晚": "f"})
    vs4 = mechanical_judge("“江晚，他说走吧。”", rules_d, "r1", 1)
    check("弯引号内人称豁免", vs4 == [])


# ── 人称混用 ──────────────────────────────────────────────────────────

def test_pronoun_switch():
    rules = MechanicalRules(protagonist="江晚", protagonist_pronoun="她",
                            cast_genders={"江晚": "f", "张三": "m"})
    # 紧邻判据：主角名后紧跟反性别人称 → 无歧义，报
    vs = mechanical_judge("江晚他收了剑。", rules, "r1", 1)
    adj = [v for v in vs if "adj" in v.probe_id]
    check("紧邻反性别人称报 1 条", len(adj) == 1 and adj[0].severity == Severity.HIGH)
    # 章节级缺位判据：主角名多现、正确人称 0、反性别人称反复 → 真 bug 形状
    buggy = "\n".join(["江晚推开门。", "江晚看着屋里的人。", "江晚没说话。",
                       "他把灯挑亮。", "他递过来一碗水。", "他退到门边。"])
    vs2 = mechanical_judge(buggy, rules, "r1", 1)
    hit = [v for v in vs2 if v.probe_id.startswith("cons-pronoun-ch")]
    check("章节级人称缺位报 1 条", len(hit) == 1 and hit[0].confidence < 1.0)

    # ↓ 真实跑批抓到的误报类：他指代场上另一男性角色，名字未出现 → 不报
    for text in ["江晚转身看他。", "江晚顺着他的目光看过去。",
                 "江晚没理他，掏出本子翻开。", "江晚等着他往下说。"]:
        vs3 = mechanical_judge(text, rules, "r1", 1)
        check(f"他指代他人不误报: {text[:8]}",
              not [v for v in vs3 if "pronoun" in v.probe_id])
    # 正确人称充分 → 不报
    good = "\n".join(["江晚推开门。", "她把灯挑亮。", "她没说话。", "她退到门边。"])
    check("正确人称充分不报",
          not [v for v in mechanical_judge(good, rules, "r1", 1) if "pronoun" in v.probe_id])
    # 行首对话豁免
    check("行首对话豁免", mechanical_judge("「江晚，他说走吧。」", rules, "r1", 1) == [])
    # 无 protagonist/人称 → 跳过
    check("无主角人称配置跳过",
          mechanical_judge("江晚他收了剑。", MechanicalRules(protagonist="江晚"), "r1", 1) == [])


def test_quoted_span_not_narration():
    """引文（任意位置）不算旁白——真实跑批抓到的误报类。"""
    rules = MechanicalRules(pov="third_limited")
    for text in ['他说：“这箱子是我的，你少管。”',
                 '江晚不松手：“那带我去你家看看。”',
                 '“苏小姐是在审我？”',
                 '江晚说：「我想问问盐的事。」']:
        vs = mechanical_judge(text, rules, "r1", 1)
        check(f"引文内第一人称豁免: {text[:10]}",
              not [v for v in vs if v.probe_id.startswith("cons-pov")])
    # 旁白第一人称仍要报（句首）
    vs2 = mechanical_judge("我蹲进凹陷。", rules, "r1", 1)
    check("句首第一人称仍报", len([v for v in vs2 if v.probe_id.startswith("cons-pov")]) == 1)
    # ↓ 真实跑批抓到的第四类误报：无引号直接引语（中文小说正当手法）
    for text in ["苏茜说我没听说有这回事。",
                 "她抬头说，那你去跟柳娘说，这批箱我查完了再放。",
                 "宋管事问，这箱是你的？"]:
        vs3 = mechanical_judge(text, rules, "r1", 1)
        check(f"无引号引语中之我不报: {text[:10]}",
              not [v for v in vs3 if v.probe_id.startswith("cons-pov")])


# ── 边界与确定性 ──────────────────────────────────────────────────────

def test_edges_and_determinism():
    rules = MechanicalRules(forbidden_words=["忽然"], target_chars=1000)
    check("空文本不崩不报", mechanical_judge("", rules, "r1", 1) == [])
    check("单句正常", isinstance(mechanical_judge("一句。", rules, "r1", 1), list))
    check("规则全空全跳", mechanical_judge("任意文本" * 500, MechanicalRules(), "r1", 1) == [])
    text = "风忽然转向。" * 3
    a = mechanical_judge(text, rules, "r1", 1)
    b = mechanical_judge(text, rules, "r1", 1)
    check("确定性：两次逐项相等", [v.to_dict() for v in a] == [v.to_dict() for v in b])
    # 全部是 constraint / mechanical
    vs = mechanical_judge("忽然" * 2 + "我" + "字" * 200, rules, "r1", 1)
    check("类型恒 constraint", types(vs) == ["constraint"] * len(vs) and len(vs) > 0)
    check("detector 恒 mechanical",
          all(v.detector == DetectorKind.MECHANICAL for v in vs))
    check("probe_id 带章号", all("ch1" in v.probe_id for v in vs))
    check("run_id 回填", all(v.run_id == "r1" for v in vs))


if __name__ == "__main__":
    test_forbidden_words()
    test_overlength_paragraph()
    test_target_length()
    test_pov_first_person()
    test_pronoun_switch()
    test_quoted_span_not_narration()
    test_edges_and_determinism()
    print(f"\n结果: {_PASS}/{_PASS + _FAIL} 通过")
    sys.exit(1 if _FAIL else 0)