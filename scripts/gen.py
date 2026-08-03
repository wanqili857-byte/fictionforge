#!/usr/bin/env python3
"""
Novel generation pipeline.

Reads a chapter spec → assembles prompts from adapt rules → routes to models →
generates sections (normal together, expanded individually) → stitches → anti-AI check.

Usage:
    python scripts/gen.py novels/<novel>/specs/<spec>.json

Example:
    python scripts/gen.py novels/静默轨道/specs/ch1.json
"""

import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

# ── Ensure project root is on sys.path for lib/ and server/ imports ────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.log import get_logger
log = get_logger("gen")

# ── Paths ──────────────────────────────────────────────────────────────
BASE = Path(__file__).resolve().parent.parent  # scripts/../ = project root
ADAPT = BASE / "adapt"

# 逐节顺序生成时，串给下一节的"上文实稿"最大窗口（防上下文无限膨胀）
_SECTION_CONTEXT_WINDOW = 4000

# ── Model routes ───────────────────────────────────────────────────────
from server._api_client import MODEL_ROUTES

# ── Novel config（框架与小说的唯一接口）─────────────────────────────
from framework.novel_config import (
    load as load_novel_config,
    protagonist_name as config_protagonist_name,
    character_name,
    protagonist_pronoun,
)


def _protagonist(config: dict) -> str:
    """主角中文名；无配置时降级为"主角"。"""
    return config_protagonist_name(config) or "主角"


# ── Load adapt rules ──────────────────────────────────────────────────
def load_character_bible(novel_dir):
    """Load character profiles from bible/人设.md.

    Returns dict of {name: section_text} parsed by ## headings.
    Returns empty dict if file doesn't exist.
    """
    path = Path(novel_dir) / "bible" / "人设.md"
    if not path.exists():
        log.info(f"  [info] no character bible at {path}")
        return {}

    text = path.read_text(encoding="utf-8")
    sections = {}
    current_name = None
    current_lines = []

    for line in text.split("\n"):
        if line.startswith("## "):
            if current_name:
                sections[current_name] = "\n".join(current_lines).strip()
            # "角色名（主角）" → "角色名"
            current_name = line[3:].split("（")[0].strip()
            current_lines = []
        elif current_name:
            current_lines.append(line)

    if current_name:
        sections[current_name] = "\n".join(current_lines).strip()

    return sections


def load_worldbuilding(novel_dir, novel_config=None):
    """Load worldbuilding rules from novel_config + bible/世界观.md.

    通用实现（与 world_sim 同一策略，framework 不绑定任何小说的 marker 语义）：
    1. novel_config.quality.world_summary → 世界观摘要
    2. novel_config.quality.world_core_principle → 核心原则
    3. 两者皆无 → distill_world_lore 从 世界观.md 通用蒸馏
    4. 蒸馏无结果 → 注入 世界观.md 全文兜底
    """
    from lib.bible_utils import load_bible_file, distill_world_lore

    quality = (novel_config or {}).get("quality", {})
    summary = (quality.get("world_summary") or "").strip()
    core = (quality.get("world_core_principle") or "").strip()

    rules = ["## 世界观规则"]
    if summary:
        rules.append("世界观：" + summary)
    if core:
        rules.append("核心原则：" + core)

    if not (summary or core):
        full = load_bible_file(novel_dir, "世界观.md")
        if full:
            lore = distill_world_lore(full) or full
            rules.append(lore)

    result = "\n".join(rules)
    log.info(f"  [worldbuilding] {len(rules)-1} rules, {len(result)} chars")
    return result


@lru_cache(maxsize=16)
def load_bible_file(novel_dir, filename):
    """Load a bible section file from bible/<filename>.

    Returns full content with ## header preserved (ready to inject into system prompt),
    or empty string if file doesn't exist.
    """
    path = Path(novel_dir) / "bible" / filename
    if not path.exists():
        log.info(f"  [bible] no file at {path}")
        return ""

    content = path.read_text(encoding="utf-8").strip()
    log.info(f"  [bible] loaded {filename}: {len(content)} chars")
    return content


def load_adapt_rules():
    """Load and cache all adapt rule files."""
    def _load_json(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    rules = {
        "anti_ai": _load_json(ADAPT / "anti-ai-zh.json"),
    }
    return rules


# ── Character state ───────────────────────────────────────────────────
CHARACTER_STATE_FILE = "角色状态.json"

def default_character_state():
    return {
        "chapter": 0,
        "dimensions": {
            "trust": 4,
            "assertiveness": 2,
            "caution": 2,
            "moral_burden": 4
        },
        "history": [
            {"chapter": 0, "changes": {}, "description": "初始状态。有秩序社会下成长的好孩子。"}
        ]
    }

def load_character_state(novel_dir):
    """Load role state from JSON file, return defaults if missing."""
    path = Path(novel_dir) / "chapters" / CHARACTER_STATE_FILE
    if not path.exists():
        state = default_character_state()
        # Save initial state so later chapters can read it
        save_character_state(novel_dir, state)
        log.info(f"  [state] 角色状态.json created (initial)")
        return state
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except:
        return default_character_state()

def save_character_state(novel_dir, state):
    """Save role state to JSON file."""
    path = Path(novel_dir) / "chapters" / CHARACTER_STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def apply_character_impact(spec, current_state):
    """Apply character_impact (array of {dimension, delta, description}) from spec to state."""
    impacts = spec.get("character_impact", [])
    if not impacts:
        return current_state, False

    changed = False
    chapter_num = spec.get("chapter", 0)
    entry = {"chapter": chapter_num, "changes": {}, "description": ""}

    for impact in impacts:
        dim = impact.get("dimension")
        delta = impact.get("delta", 0)
        desc = impact.get("description", "")
        if dim and dim in current_state["dimensions"]:
            old = current_state["dimensions"][dim]
            new_val = max(1, min(5, old + delta))
            current_state["dimensions"][dim] = new_val
            entry["changes"][dim] = delta
            if desc:
                entry["description"] += desc + " "
            changed = True

    if changed:
        entry["description"] = entry["description"].strip()
        current_state["history"].append(entry)
        current_state["chapter"] = chapter_num

    return current_state, changed

def format_character_state(state, chapter_num=None, pronoun="主角"):
    """Format character state for system prompt injection.

    pronoun 从 novel_config.protagonist_pronoun 传入（他/她/它），
    主角状态描述不再硬编码人称。
    """
    dims = state["dimensions"]
    # Get changes since start of story
    recent = [h for h in state["history"] if h["chapter"] > 0]
    recent_descs = [h["description"] for h in recent if h["description"]]

    # Hide the numerical scores — inject as qualitative description
    lines = []
    if dims["trust"] <= 2:
        lines.append(f"{pronoun}对陌生人普遍不信任。")
    elif dims["trust"] >= 4:
        lines.append(f"{pronoun}对人有基本的信任倾向。")
    else:
        lines.append(f"{pronoun}对陌生人持观望态度。")

    if dims["assertiveness"] >= 4:
        lines.append(f"{pronoun}遇到问题会主动介入。")
    elif dims["assertiveness"] <= 2:
        lines.append(f"{pronoun}倾向于先观察后行动，不主动介入。")

    if dims["caution"] >= 4:
        lines.append(f"{pronoun}高度谨慎，风险规避意识强。")
    elif dims["caution"] <= 2:
        lines.append(f"{pronoun}风险承受度较高。")

    if dims["moral_burden"] >= 4:
        lines.append(f"{pronoun}的道德包袱重——不做某些事不是因为不想，是良心过不去。")
    elif dims["moral_burden"] <= 2:
        lines.append(f"{pronoun}在做取舍时内疚感较轻。")

    if recent_descs:
        lines.append("\n最近重大变化：" + "；".join(recent_descs[-3:]))

    return "\n".join(lines)


# ── System prompt assembly ─────────────────────────────────────────────
def build_system_prompt(novel_title, novel_dir, character_state=None,
                        novel_config=None):
    """Assemble system prompt from active guidance rules (not prohibition-heavy).

    Character bible (bible/人设.md) is loaded if available to enrich character anchors.
    Worldbuilding rules (bible/世界观.md) are loaded if available.
    Inject character state evolution if provided.
    novel_config: novel_config.json 内容（主角名等），可为 None。
    """
    characters = load_character_bible(novel_dir)
    world_rules = load_worldbuilding(novel_dir, novel_config)

    # ── Core writing rules (always present, read from bible/) ──
    rules = load_bible_file(novel_dir, "写作法则.md")

    # ── Physics constraints (read from bible/) ──
    physics = load_bible_file(novel_dir, "物理约束.md")

    # ── Banned words (read from bible/) ──
    forbidden = load_bible_file(novel_dir, "禁止词.md")

    # ── Humanness reference (read from bible/人味参考.md) ──
    human_ref = load_bible_file(novel_dir, "人味参考.md")
    if human_ref:
        human_ref = f"## 角色生活质感参考\n\n以下段落展示了目标的生活质感和人味密度。模型正文应达到类似的角色互动密度、职业细节嵌入、和内心活动。\n\n{human_ref}"

    # ── Character anchors (read from bible/人设.md → 模型注入锚点) ──
    inject = characters.get("模型注入锚点", "").strip()
    if inject:
        char_section = f"## 人物锚点\n\n{inject}"
    elif fb := load_bible_file(novel_dir, "人物锚点.md"):
        char_section = fb
    else:
        # 无锚点来源——不硬编码人物，人物交给 spec/模型注入
        char_section = ""

    # ── 主角人称硬约束（机械正确性，同禁止词：防止模型混用 她/他）──
    pronoun = protagonist_pronoun(novel_config or {})
    pronoun_rule = ""
    if pronoun:
        protagonist = _protagonist(novel_config or {})
        pronoun_rule = (f"主角{protagonist}的人称代词固定为「{pronoun}」，"
                        "正文中涉及主角时一律用「" + pronoun + "」，不得混用或更换。")

    parts = [
        f"你是悬疑小说《{novel_title}》的写作者。",
        rules,
        physics,
        world_rules,
        char_section,
        human_ref,
        pronoun_rule,
        forbidden,
    ]

    # Character state evolution (if available)
    if character_state:
        pronoun = protagonist_pronoun(novel_config or {}) or "主角"
        state_text = format_character_state(character_state, pronoun=pronoun)
        if state_text.strip():
            protagonist = _protagonist(novel_config or {})
            parts.append(f"## {protagonist}当前性格状态（截至上一章结束）\n\n{state_text}")

    # Filter out empty sections
    parts = [p for p in parts if p.strip()]

    return "\n\n".join(parts)


# ── User prompt assembly ──────────────────────────────────────────────
def build_normal_prompt(spec, sections, context_before=None, novel_config=None,
                        section_len_hint=None):
    """Build user prompt for a single normal-weight section（逐节顺序生成）。

    context_before: 前一节真实生成文本（上文实稿）。模型紧接它继续写，
    不能重复已写内容，写到本场景结束为止——场景边界由"一次调用=一个场景"结构锁死。
    """
    sec = sections[0]
    lines = [f"## {spec['title']}·写作需求", ""]
    if spec.get("mood"):
        lines.append(f"情绪线：{spec['mood']}")
        lines.append("")

    # 世界观由 system prompt 从 bible/世界观.md 注入；这里不硬编码任何小说设定
    if novel_config and novel_config.get("quality", {}).get("world_summary"):
        lines.append(f"世界观：{novel_config['quality']['world_summary']}")
        lines.append("")

    if context_before:
        lines.append("### 上文实稿（紧接这段继续写，保持连贯，不要重复或重新介绍已写内容）")
        lines.append(context_before)
        lines.append("")

    lines.append(f"### {sec['id']}、{sec['subject']}")
    lines.append(sec["description"])
    if "tension_direction" in sec and sec["tension_direction"]:
        lines.append(f"张力方向：{sec['tension_direction']}")
    if "divergence_vibe" in sec and sec["divergence_vibe"]:
        lines.append(f"发散方向：{sec['divergence_vibe']}")
    lines.append("")

    lines.append("### 输出要求")
    lines.append("承接上文，写本场景的内容，写到本场景结束为止。不要写本场景之后的内容。"
                 "不要分节标题，不要***，不要任何标记。短段落，1-2句换行。")
    if section_len_hint:
        lines.append(f"本场景篇幅：约{section_len_hint}字，充分展开。")
    lines.append("")

    return "\n".join(lines)


def build_expanded_prompt(spec, section, context_before=None, section_len_hint=None):
    """Build user prompt for a single expanded section（核心场景，独立调用展开）。"""
    lines = [f"## {spec['title']}·单独段落写作", ""]
    lines.append("这一段是章节的核心段落，需要详细展开。")
    lines.append("")

    if context_before:
        lines.append("### 上文实稿（紧接这段继续写，保持连贯，不要重复或重新介绍已写内容）")
        lines.append(context_before)
        lines.append("")

    lines.append(f"### {section['subject']}")
    lines.append(section['description'])
    lines.append("")

    if "tension_direction" in section and section["tension_direction"]:
        lines.append(f"张力方向：{section['tension_direction']}")
        lines.append("")

    if "divergence_vibe" in section and section["divergence_vibe"]:
        lines.append(f"发散方向：{section['divergence_vibe']}")
        lines.append("")

    if "expanded_direction" in section and section["expanded_direction"]:
        lines.append("### 展开方向")
        lines.append(section["expanded_direction"])
        lines.append("")

    lines.append("### 输出要求")
    lines.append("承接上文，详细展开本段，写到本场景结束为止。不要写本场景之后的内容。"
                 "不要分节标题，不要***。短段落，1-3句换行，场景转换用空行隔开。")
    if section_len_hint:
        lines.append(f"本段篇幅：约{section_len_hint}字，核心场景务必展开到位。")
    lines.append("")

    return "\n".join(lines)


# ── API call ──────────────────────────────────────────────────────────
def call_api(system_prompt, user_prompt, route, silent=False):
    """Call the generation API with SSE streaming. 空响应自动重试 1 次。"""
    # Use shared SSE client at project root
    from server._api_client import sse_request

    body = {
        "messages": [{"role": "user", "content": user_prompt}],
        "systemPrompt": system_prompt,
        "provider": route["provider"],
        "model": route["model"],
        "temperature": route["temperature"],
        "maxTokens": route["maxTokens"],
    }

    MAX_RETRIES = 1
    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            log.warning(f"  [api] call_api 空响应，重试 1 次 ({route['model']})")

        if silent:
            result = sse_request(body)
            if result:
                return result
            continue

        full = []

        def on_chunk(c):
            print(c, end="", flush=True)
            full.append(c)

        result = sse_request(body, stream_callback=on_chunk)
        if result and not "".join(full):
            return result  # 流回调没收集到但响应有内容
        text = "".join(full)
        if text:
            return text
        # 空输出，进入重试
    return None


# ── Spec validation ─────────────────────────────────────────────────────
def validate_spec(spec, novel_dir):
    """Validate chapter spec before generation. Returns list of issues.

    Checks:
    1. Empty/too-short descriptions
    2. Missing tension_direction
    3. Negative tension instructions (should prefer positive)
    4. Expanded sections missing expanded_direction
    5. Section count vs target_chars sanity
    6. Cross-chapter continuity (章节目录.md)
    7. Anomaly density for buffer period (ch ≤3)
    """
    issues = []
    chapter_num = spec.get("chapter", 1)
    is_buffer = chapter_num <= 3

    sections = spec.get("sections", [])
    target_chars = spec.get("target_chars", 0)

    if not sections:
        issues.append("ERROR: spec has no sections")
        return issues

    # 1. Empty/filler descriptions
    for sec in sections:
        desc = sec.get("description", "").strip()
        if not desc:
            issues.append(f"ERROR: section {sec['id']} has empty description")
        elif len(desc) < 20:
            issues.append(f"WARN: section {sec['id']} description too short ({len(desc)} chars)")

    # 2. Missing tension_direction
    for sec in sections:
        td = sec.get("tension_direction", "").strip()
        if not td:
            issues.append(f"WARN: section {sec['id']} missing tension_direction")

    # 3. Negative tension instructions
    negative_markers = ["不要", "别写", "禁止", "不能写", "避免", "不要写"]
    for sec in sections:
        td = sec.get("tension_direction", "")
        if not td:
            continue
        for m in negative_markers:
            if m in td:
                issues.append(
                    f"WARN: section {sec['id']} tension_direction uses negative "
                    f"instruction '{m}' (prefer positive direction)"
                )
                break

    # 4. Expanded sections missing expanded_direction
    for sec in sections:
        if sec.get("weight") == "expanded":
            ed = sec.get("expanded_direction", "").strip()
            if not ed:
                issues.append(
                    f"WARN: section {sec['id']} is expanded but missing expanded_direction"
                )

    # 5. Section count vs target_chars
    if target_chars > 0:
        avg = target_chars / max(len(sections), 1)
        if avg < 400 and len(sections) >= 4:
            issues.append(
                f"WARN: {len(sections)} sections for {target_chars} chars target "
                f"(~{int(avg)}/section, may be tight)"
            )

    # 6. Cross-chapter continuity
    state_path = Path(novel_dir) / "chapters" / "章节状态.md"
    if state_path.exists():
        text = state_path.read_text(encoding="utf-8")
        existing = set()
        for line in text.split("\n"):
            m = re.match(r"^## 第(\d+)章", line)
            if m:
                existing.add(int(m.group(1)))
        if existing:
            latest = max(existing)
            expected = latest + 1
            if chapter_num != expected:
                issues.append(
                    f"ERROR: spec chapter={chapter_num}, but 章节状态.md latest "
                    f"is ch{latest} (expected ch{expected})"
                )

    # 7. Anomaly density for buffer period (conservative keyword matching)
    if is_buffer:
        anomaly_sections = 0
        for sec in sections:
            desc = sec.get("description", "")
            td = sec.get("tension_direction", "")
            combined = desc + " " + td
            signals = 0
            # Specific anomaly phrases (low false-positive rate)
            if "回不来" in combined or "没有回来" in combined:
                signals += 1
            if "手表" in combined and ("左手" in combined or "右手" in combined):
                signals += 1
            if "消失了" in combined or "不见" in combined or "不存在" in combined:
                signals += 1
            if "空无一人" in combined or "什么也没有" in combined:
                signals += 1
            if signals > 0:
                anomaly_sections += 1

        if anomaly_sections > 3:
            issues.append(
                f"WARN: buffer period (ch{chapter_num}) has {anomaly_sections} sections "
                f"with anomaly signals (recommended ≤3)"
            )

    return issues


def print_validation_issues(issues):
    """Print validation report. Returns True if any ERROR-level issues."""
    if not issues:
        log.info("[✓] Spec validation: clean")
        return False

    has_error = False
    log.info(f"\n[Spec validation] {len(issues)} issue(s):")
    for issue in issues:
        if issue.startswith("ERROR"):
            has_error = True
        log.info(f"  {issue}")

    return has_error


# ── Anti-AI check ─────────────────────────────────────────────────────
def anti_ai_check(text, rules):
    """Scan text for anti-AI violations. Returns list of violations."""
    violations = []
    anti = rules["anti_ai"]
    lines = text.split("\n")

    # Fatal words
    fatal_words = set()
    for cat in anti["words"].values():
        for entry in cat.get("entries", []):
            if entry.get("severity") == "fatal":
                fatal_words.add(entry["word"])
            elif cat.get("severity") == "fatal":
                fatal_words.add(entry["word"])
    # Also add temporalCrutches' fatal entries
    for entry in anti["words"].get("temporalCrutches", {}).get("entries", []):
        if entry.get("severity") == "fatal":
            fatal_words.add(entry["word"])

    for i, line in enumerate(lines, 1):
        for w in fatal_words:
            if w in line:
                violations.append({
                    "severity": "fatal",
                    "word": w,
                    "line": i,
                    "context": line.strip()[:80],
                })

    # High severity words (narrative only, not dialogue)
    high_words = set()
    for entry in anti["words"].get("fuzzyAdverbs", {}).get("entries", []):
        high_words.add(entry["word"])
    for entry in anti["words"].get("AIEmotionMarkers", {}).get("entries", []):
        high_words.add(entry["word"])
    for entry in anti["words"].get("evaluativeConstructs", {}).get("entries", []):
        high_words.add(entry["word"])
    high_words -= fatal_words  # don't double-count
    # Un-prohibit ambiguity words — useful for suspense (模糊感)
    high_words -= {"似乎", "仿佛", "好像", "不禁", "不由得", "下意识", "本能地"}

    # Check narrative lines (not in quotes/dialogue)
    in_dialogue = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # Simple dialogue detection: line starts with " or 「
        if stripped.startswith('"') or stripped.startswith('「'):
            in_dialogue = True
            continue
        if in_dialogue and (stripped.endswith('"') or stripped.endswith('」')):
            in_dialogue = False
            continue
        if in_dialogue:
            continue

        for w in high_words:
            if w in stripped:
                violations.append({
                    "severity": "high",
                    "word": w,
                    "line": i,
                    "context": stripped[:80],
                })

    # Overused modifiers - count per word
    modifiers = {}
    for entry in anti["words"].get("overusedModifiers", {}).get("entries", []):
        word = entry["word"]
        limit = entry.get("allowedCount", 3)
        count = text.count(word)
        if count > limit:
            modifiers[word] = {"count": count, "limit": limit}

    for word, info in modifiers.items():
        violations.append({
            "severity": "medium",
            "word": word,
            "count": info["count"],
            "limit": info["limit"],
        })

    # Deduplicate: group by (word, line)
    seen = set()
    unique = []
    for v in violations:
        key = (v["word"], v.get("line", 0))
        if key not in seen:
            seen.add(key)
            unique.append(v)

    return unique


def print_violations(violations):
    """Print violation report."""
    if not violations:
        log.info("\n[✓] Anti-AI check: clean")
        return True

    log.info(f"\n[!] Anti-AI violations: {len(violations)}")
    fatal_count = sum(1 for v in violations if v["severity"] == "fatal")
    high_count = sum(1 for v in violations if v["severity"] == "high")
    med_count = sum(1 for v in violations if v["severity"] == "medium")

    if fatal_count:
        log.info(f"    FATAL: {fatal_count}")
    if high_count:
        log.info(f"    HIGH:  {high_count}")
    if med_count:
        log.info(f"    MED:   {med_count}")

    for v in violations:
        if v["severity"] == "medium":
            if "count" in v:
                log.info(f"  [{v['severity']}] '{v['word']}' used {v['count']}x (limit {v['limit']})")
        else:
            log.info(f"  [{v['severity']}] '{v['word']}' L{v.get('line','?')}: {v.get('context','')}")

    return False


def auto_fix_violations(text, violations):
    """Auto-fix known violations by replacing forbidden words."""
    fix_map = {
        # Only the truly empty AI-ese patterns — ambiguity words are allowed
        "某种": "",
    }

    fixed = text
    for v in violations:
        word = v["word"]
        if v["severity"] == "fatal" and word not in fix_map:
            log.warning(f"  [!] Manual fix needed: '{word}' at L{v.get('line','?')}")
            continue
        if word in fix_map:
            replacement = fix_map[word]
            # Only replace first occurrence per violation (don't blanket replace)
            lines = fixed.split("\n")
            if "line" in v:
                idx = v["line"] - 1
                if 0 <= idx < len(lines):
                    if replacement:
                        lines[idx] = lines[idx].replace(word, replacement, 1)
                    else:
                        # Delete the word but keep the rest (handle whitespace)
                        lines[idx] = lines[idx].replace(word, "", 1)
            fixed = "\n".join(lines)

    return fixed


# ── Quality check ─────────────────────────────────────────────────────



def quality_check(text, spec, chapter_num, forbidden_words=None):
    """Post-generation quality check. Returns list of issues dicts.

    Checks: forbidden words, simile density (per 写作法则.md), length.
    Does NOT check character layers (A/B/C) — those are measured against
    the hand-written reference chapter (01_第一章.md), not artificial counts.
    Each issue: {severity, category, message, fixable}.
    forbidden_words: 禁止词列表，config 优先，无则用默认。
    """
    issues = []

    # 1. 禁止词
    forbidden = forbidden_words or ["突然", "忽然", "只见"]
    for word in forbidden:
        count = text.count(word)
        if count > 0:
            issues.append({
                "severity": "error",
                "category": "forbidden",
                "message": f"禁止词「{word}」出现 {count} 次",
                "fixable": False,
            })

    # 2. 比喻密度（写作法则：全章 ≤5 处）
    simile_markers = ["像", "如同", "仿佛", "好似", "宛如"]
    simile_count = sum(text.count(m) for m in simile_markers)
    if simile_count > 5:
        issues.append({
            "severity": "warn",
            "category": "simile",
            "message": f"比喻密度: {simile_count} 个比喻标记词（建议 ≤5）",
            "fixable": True,
        })

    # 3. 长度检查
    target = spec.get("target_chars", 0)
    if target:
        actual = len(text)
        ratio = actual / target
        if ratio < 0.5:
            issues.append({
                "severity": "error",
                "category": "length",
                "message": f"篇幅过短: {actual} 字, 目标 {target} 字（{ratio:.0%}）",
                "fixable": True,
            })
        elif ratio < 0.7:
            issues.append({
                "severity": "warn",
                "category": "length",
                "message": f"篇幅偏短: {actual} 字, 目标 {target} 字（{ratio:.0%}）",
                "fixable": True,
            })
        elif ratio > 1.5:
            issues.append({
                "severity": "info",
                "category": "length",
                "message": f"篇幅偏长: {actual} 字, 目标 {target} 字（{ratio:.0%}）",
                "fixable": False,
            })

    return issues


def print_quality_issues(issues):
    """Print quality report. Returns True if any error-level issues."""
    if not issues:
        log.info("[✓] Quality check: clean")
        return True

    errors = [i for i in issues if i["severity"] == "error"]
    warns = [i for i in issues if i["severity"] == "warn"]
    infos = [i for i in issues if i["severity"] == "info"]

    log.info(f"\n[Quality check] {len(issues)} issue(s): "
             f"{len(errors)} errors, {len(warns)} warnings, {len(infos)} info")
    for issue in issues:
        icon = {"error": "✗", "warn": "!", "info": "i"}
        fixable = " [fixable]" if issue.get("fixable") else ""
        log.info(f"  {icon[issue['severity']]} {issue['category']}: "
                 f"{issue['message']}{fixable}")

    return len(errors) == 0


def auto_fix_quality(text, issues, system_prompt, route, reference_text=None):
    """Fix quality issues via targeted API call. Returns (fixed_text, success).

    If reference_text (hand-written ch1) is provided, includes it as a
    quality target so the model knows what "natural" looks like.
    """
    fix_instructions = []
    seen = set()

    for issue in issues:
        if not issue.get("fixable"):
            continue
        key = issue["category"]
        if key in seen:
            continue
        seen.add(key)

        if key == "simile":
            fix_instructions.append(
                "- 把部分「像XX」的比喻句改为直接描述，保留信息量、去掉比喻修辞"
            )
        elif key == "length":
            fix_instructions.append(
                "- 在不改变风格的前提下，适当扩展场景细节和感官描写"
            )

    if not fix_instructions:
        return text, True

    fix_parts = [
        "请修改以下章节正文，要求：",
        *fix_instructions,
        "不要增加新的比喻句。不要改变叙事顺序和段落结构。",
        "不要加注释说明。直接输出修改后的正文。",
    ]
    if reference_text:
        fix_parts.insert(1, "\n参考目标质量标准——以下是一段人工写作的章节摘录（注意其自然感、节奏、细节密度）：\n```\n" + reference_text + "\n```\n")

    fix_prompt = "\n".join(fix_parts) + "\n\n" + text

    result = call_api(system_prompt, fix_prompt, route, silent=True)
    if not result:
        log.warning("  [!] Auto-fix API call failed, keeping original")
        return text, False
    if len(result) < len(text) * 0.5:
        log.warning("  [!] Auto-fix output too short, keeping original")
        return text, False

    log.info(f"  Auto-fix applied: {len(text)} → {len(result)} chars")
    return result, True


def print_quality_summary(issues_before, issues_after):
    """Summarize fix results."""
    if not issues_before:
        return
    fixed_categories = set()
    remaining = []
    for i in issues_after:
        remaining.append(i["category"])
    for i in issues_before:
        if i["category"] not in remaining and i.get("fixable"):
            fixed_categories.add(i["category"])
    if fixed_categories:
        log.info(f"  Fixed: {', '.join(fixed_categories)}")
    if remaining:
        still = [i for i in issues_after if i["severity"] != "info"]
        if still:
            log.info(f"  Remaining: {len(still)} issue(s)")


# ── Verve review (人味缺漏审) ──────────────────────────────────────────

def verve_review(text, spec, novel_config=None):
    """Review generated text for '人味' (liveliness/naturalness/humanness).

    Checks dimensions that quantitative checks can't measure:
    - Inner voice (A层): self-talk, sharp observations, wry humor
    - 温情角色 interaction quality: specific actions vs generic dialogue
    - C-layer actions: showing care through action, not words
    - Sensory temperature variety: hot/cold/warm touch
    - Humor/wry detachment
    - Physical specificity (texture, sound, smell density)

    novel_config: novel_config.json 内容（主角名/温情角色），可为 None。

    Returns list of dicts: {severity, dimension, detail, suggestion}
    """
    config = novel_config or {}
    protagonist = _protagonist(config)
    raw_warm = config.get("quality", {}).get("warmth_char") or ""
    warm_char = (character_name(config, raw_warm) if raw_warm
                 else protagonist or "主角")

    findings = []

    # ── 1. Inner voice (A层) ──
    inner_markers = ["心想", "这很", "真", "蠢", "无聊", "烦", "没道理",
                     "这不", "算什么"]
    inner_hits = sum(1 for m in inner_markers if m in text)
    # Also check for self-questioning
    question_marks = text.count("？")
    # Heuristic: at least 2-3 inner voice moments per 3000 chars
    inner_ratio = inner_hits / max(len(text), 1) * 3000
    if inner_ratio < 1.5:
        findings.append({
            "severity": "warn",
            "dimension": "A层内心独白",
            "detail": f"内省标记仅 {inner_hits} 处（目标 2-3 处/3000字）",
            "suggestion": f"增加{protagonist}式的短/锐/自嘲评价——对眼前事的即时反应, 1-3句不拖节奏"
        })

    # ── 2. 温情角色 interaction quality（warmth_char，默认主角） ──
    mom_lines = [l for l in text.split("\n")
                 if warm_char in l or (warm_char and warm_char[0] in l[:3])]
    if mom_lines:
        # Check for generic dialogue patterns
        generic_mom = sum(1 for l in mom_lines
                          for phrase in ["脸色", "没事吧", "怎么了", "早点睡", "早点"]
                          if phrase in l)
        specific_mom = sum(1 for l in mom_lines
                           for phrase in [",", "着", "塑料袋", "拉链", "袋子", "手指",
                                          "温热", "盐", "干贝", "碰撞", "沙沙"]
                           if phrase in l)
        if generic_mom >= specific_mom and specific_mom < 3:
            findings.append({
                "severity": "warn",
                "dimension": f"{warm_char}温度",
                "detail": f"{warm_char}对话倾向功能型（generic {generic_mom} vs specific {specific_mom}）",
                "suggestion": f"{warm_char}的关心不用台词说出来——用具体小动作（抱怨/指关节温度/塑料袋声/物品安排）代替"
            })
    else:
        findings.append({
            "severity": "info",
            "dimension": f"{warm_char}温度",
            "detail": f"全章无{warm_char}出场",
            "suggestion": f"如果本章有{warm_char}场景, 用具体动作代替情感表白"
        })

    # ── 3. C-layer actions (show not tell) ──
    c_phrases = ["没", "停下", "走过去", "站在原地", "回头", "迟疑",
                 "没说话", "没应声", "没回答", "接了"]
    c_hits = sum(1 for p in c_phrases if p in text)
    # Look for action-sequence patterns (做A→发现B→决定C)
    _pronoun = protagonist_pronoun(novel_config or {}) or "主角"
    action_seq = len(re.findall(rf'[。]\s*{_pronoun}[^。]{{2,30}}[了]', text))
    if c_hits < 2 and action_seq < 3:
        findings.append({
            "severity": "info",
            "dimension": "C层动作",
            "detail": f"C层信号词 {c_hits} 处（建议 >2）",
            "suggestion": '增加1处"不说在乎但做"的动作——嘴上没反应, 下一幕做无关但有关的事'
        })

    # ── 4. Sensory temperature ──
    temp_words = ["烫", "凉", "热", "温", "冰", "冷", "暖", "烧",
                  "发烫", "发凉", "冰凉", "温热"]
    temp_hits = sum(1 for w in temp_words if w in text)
    if temp_hits < 2:
        findings.append({
            "severity": "warn",
            "dimension": "温度触觉",
            "detail": f"温度词仅 {temp_hits} 处",
            "suggestion": f"增加1-2处体表温度感受（阳光晒/金属冰凉/风的温差/{warm_char}指关节温热）"
        })

    # ── 5. Humor / wry detachment ──
    humor_markers = ["心想", "你说", "这", "卷", "懒", "吐槽",
                     "蠢", "无聊", "没办法"]
    humor_hits = sum(1 for m in humor_markers if m in text)
    # Look for self-deprecating patterns
    self_dep = len(re.findall(r'知道|承认|意识到|告诉自己', text))
    if humor_hits < 3 and self_dep < 2:
        findings.append({
            "severity": "info",
            "dimension": "幽默/自嘲",
            "detail": f"未检测到明显的{protagonist}式自嘲或吐槽",
            "suggestion": f"{protagonist}对眼前事的锋利评价是其标志——吐槽世界也吐槽自己"
        })

    # ── 6. Physical specificity density ──
    # Check for multi-sensory details in each paragraph
    sensory_markers = ["声", "味", "气", "湿", "黏", "涩", "刺",
                       "痛", "酸", "麻", "僵", "肿", "干", "裂"]
    sensory_hits = sum(1 for m in sensory_markers if m in text[:2000])
    # First 2000 chars should have decent sensory density
    if sensory_hits < 20:
        findings.append({
            "severity": "info",
            "dimension": "感官密度",
            "detail": f"前2000字感官信号约 {sensory_hits} 处",
            "suggestion": "通过身体信号代替心理描写（呼吸消失/喉咙锁住/指尖发凉/闻到气味）"
        })

    return findings


def print_verve_review(findings):
    """Print verve review report."""
    if not findings:
        log.info("\n[verve] 人味审: clean (no suggestions)")
        return

    log.info(f"\n[verve] 人味缺漏审 ({len(findings)} 条建议):")
    for f in findings:
        icon = {"warn": "!", "info": "i"}
        log.info(f"  {icon.get(f['severity'], '?')} {f['dimension']}: {f['detail']}")
        log.info(f"    建议: {f['suggestion']}")


# ── Chapter state extraction ───────────────────────────────────────────


def update_state_file(state_path, chapter_num, extracted_text,
                      protagonist="主角"):
    """Update 章节状态.md with extracted chapter state."""
    clean_text = extracted_text.strip()
    target = f"## 第{chapter_num}章"
    new_section = f"{target}\n\n{clean_text}\n\n---"

    if not state_path.exists():
        header = "# 章节状态记录\n\n> 每完成一章自动更新。\n\n"
        ref_table = (f"\n## 章节对照表\n\n| 章 | 时间跨度 | 地点 | 核心事件 "
                     f"| {protagonist}状态 |\n|---|---|---|---|---|\n")
        state_path.write_text(
            header + new_section + "\n\n" + ref_table, encoding="utf-8"
        )
        log.info(f"  [state] 章节状态.md created for ch{chapter_num}")
        return

    text = state_path.read_text(encoding="utf-8")

    # Find existing section
    start = text.find(f"\n{target}") if f"\n{target}" in text else text.find(target)
    if start >= 0:
        # Replace from target to next ## section or EOF
        next_pos = len(text)
        for marker in ["\n## ", "\n# 章"]:
            pos = text.find(marker, start + len(target))
            if 0 < pos < next_pos:
                next_pos = pos
        text = text[:start] + new_section + "\n" + text[next_pos:]
    else:
        # Insert before 章节对照表 or append
        ref_idx = text.find("## 章节对照表")
        if ref_idx >= 0:
            text = text[:ref_idx] + new_section + "\n\n" + text[ref_idx:]
        else:
            text = text.rstrip() + "\n\n" + new_section + "\n"

    state_path.write_text(text, encoding="utf-8")
    log.info(f"  [state] 章节状态.md updated for ch{chapter_num}")


def update_chapter_state(novel_dir, chapter_num, chapter_text, spec=None,
                         novel_config=None):
    """Extract state from generated chapter and update 章节状态.md.

    确定性规则提取（无 LLM 调用）。主角名从 novel_config 取。
    """
    state_path = Path(novel_dir) / "chapters" / "章节状态.md"
    protagonist = _protagonist(novel_config or {})

    sections = spec.get("sections", [])

    # 关键事件：直接取 spec descriptions
    key_events = []
    for sec in sections:
        desc = sec.get("description", "")
        if desc:
            subject = sec.get("subject", "")
            key_events.append(f"- {subject}：{desc[:120]}")

    parts = ["### 关键事件\n"]
    parts.extend(key_events)
    parts.append("")

    # 张力方向
    tension = ""
    for sec in sections:
        td = sec.get("tension_direction", "")
        if td:
            tension = td
            break
    if tension:
        parts.append(f"### 张力方向\n{tension}\n")

    # 从 spec 推断主角状态（主角名来自 novel_config）
    char_hints = []
    for sec in sections:
        desc = sec.get("description", "")
        for marker in [protagonist, "她"]:
            if marker and marker in desc:
                first_sentence = desc.split("。")[0] if "。" in desc else desc[:60]
                char_hints.append(first_sentence)
                break

    if char_hints:
        parts.append(f"### {protagonist}状态（从 spec 推断）")
        for h in char_hints[:2]:
            parts.append(f"- {h}")
        parts.append("")

    # 上一章状态中的信息
    if chapter_num > 1 and state_path.exists():
        raw = state_path.read_text(encoding="utf-8")
        prev_target = f"## 第{chapter_num - 1}章"
        idx = raw.find(prev_target)
        if idx >= 0:
            rest = raw[idx + len(prev_target):]
            next_start = len(rest)
            for m in ["\n## ", "\n# 章"]:
                pos = rest.find(m)
                if 0 < pos < next_start:
                    next_start = pos
            prev_section = rest[:next_start].strip()
            # 提取之前的悬念列表
            if "### 未解悬念" in prev_section:
                parts.append("### 未解悬念（继承上一章）\n继承上一章未解悬念，等待后续回收。\n")

    result_text = "\n".join(parts)
    update_state_file(state_path, chapter_num, result_text,
                      protagonist=protagonist)
    return []



# ── Merge sections ─────────────────────────────────────────────────────
def merge_sections(section_texts):
    """逐节顺序生成的文本按顺序拼接。

    一次调用=一个场景（见 Pipeline._phase_sequential），无需分节解析、
    无需 fallback、无需去重——场景边界在生成时已结构锁死。
    """
    return "\n\n".join(t.strip() for t in section_texts if t.strip())


# ── Engine tick bridge ─────────────────────────────────────────────────

def load_tick_result(path: str) -> dict:
    """Load and validate tick result JSON from engine."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    for key in ["character_trajectories", "suggested_chapter_split", "arc_id"]:
        if key not in data:
            raise ValueError(f"Tick result missing required key: '{key}'")
    return data


def build_spec_from_tick(tick_data: dict, chapter_num: int, novel: str) -> dict:
    """Auto-build chapter spec from tick engine output.

    Groups tick events by chapter, builds section descriptions.
    Returns spec dict compatible with gen.py generation pipeline.
    """
    scopes = tick_data.get("suggested_chapter_split", [])
    scope = None
    for s in scopes:
        if s.get("chapter_num") == chapter_num:
            scope = s
            break
    if not scope and scopes:
        scope = scopes[0]
    n_sections = scope.get("sections", 3) if scope else 3

    # Map chapters to day ranges
    all_events = []
    for pov, events in tick_data.get("character_trajectories", {}).items():
        for ev in events:
            all_events.append((ev.get("day", 0), ev.get("time", ""), pov, ev))
    all_events.sort(key=lambda x: (x[0], x[1]))

    total_days = sorted(set(e[0] for e in all_events))
    n = len(scopes) if scopes else 1
    days_per = max(1, len(total_days) // n) if total_days else 1

    ch_indices = [s["chapter_num"] for s in scopes] if scopes else [chapter_num]
    try:
        idx = ch_indices.index(chapter_num)
    except ValueError:
        idx = 0
    start_idx = idx * days_per
    end_idx = (idx + 1) * days_per if idx < n - 1 else len(total_days)
    ch_days = set(total_days[start_idx:end_idx])

    ch_events = [e for e in all_events if e[0] in ch_days]

    # Build sections from events
    sec_ids = ["一", "二", "三", "四", "五", "六"]
    sections = []
    if ch_events:
        events_per = max(1, len(ch_events) // n_sections)
        for i in range(n_sections):
            group = ch_events[i * events_per : (i + 1) * events_per]
            if not group and sections:
                group = ch_events[-events_per:] if ch_events else []

            locs = list(set(e[3].get("location", "?") for e in group)) if group else ["?"]
            loc_str = " / ".join(locs[:3])
            briefs = "\n".join(
                f"- {e[3].get('pov', '?')}: {e[3].get('brief', '')[:60]}"
                for e in group[:5]
            ) if group else "（无具体事件）"

            is_reversal = scope.get("reversal_at_section", 0) == i + 1
            sections.append({
                "id": sec_ids[i],
                "subject": loc_str,
                "description": f"（引擎输出）{loc_str}。{briefs}",
                "tension_direction": scope.get("tension_direction", "") if scope else "",
                "weight": "expanded" if is_reversal else "normal",
            })
            if is_reversal:
                sections[-1]["expanded_direction"] = scope.get("tension_direction",
                     "反转场景，重点展开本章核心事件")
    else:
        for i in range(n_sections):
            sections.append({
                "id": sec_ids[i],
                "subject": f"场景{i+1}",
                "description": f"（引擎无具体事件，请手动补充本章内容）",
                "tension_direction": scope.get("tension_direction", "") if scope else "",
                "weight": "normal",
            })

    return {
        "novel": novel,
        "chapter": chapter_num,
        "title": f"第{chapter_num}章",
        "sections": sections,
        "target_chars": max(2000, n_sections * 800),
        "from_engine": True,
        "tick_arc_id": tick_data.get("arc_id", ""),
    }


# ── Pipeline ─────────────────────────────────────────────────────────────

class Pipeline:
    """生成管线：spec → 生成 → merge → anti-AI → 输出。"""

    def __init__(self, spec, novel_dir, vault_reader, rules, system_prompt, output_path,
                 chapter_context="", character_state=None, novel_config=None):
        self.spec = spec
        self.novel_dir = novel_dir
        self.vault_reader = vault_reader
        self.rules = rules
        self.system_prompt = system_prompt
        self.output_path = output_path
        self.chapter_context = chapter_context
        self.chapter_num = spec.get("chapter", 1)
        self.character_state = character_state or default_character_state()
        self.novel_config = novel_config or {}
        self.sections = spec["sections"]

    def run(self) -> str:
        """执行完整管线：逐节顺序生成 → 拼接 → anti-AI → 输出。返回最终文本。"""
        log.info(f"[pipeline] {self.spec['title']}  ->  {self.output_path.name}")
        log.info(f"[pipeline] sections: {len(self.sections)}")
        log.info(f"[pipeline] system prompt: {len(self.system_prompt)} chars")

        section_texts = self._phase_sequential()
        final_text = self._phase_merge_check(section_texts)
        final_text = self._phase_antiai(final_text)
        final_text = self._phase_quality_check(final_text, max_fix_rounds=1)
        self._phase_output(final_text)

        # 角色状态更新（如果 spec 有 character_impact）
        new_state, changed = apply_character_impact(self.spec, self.character_state)
        if changed:
            save_character_state(self.novel_dir, new_state)
            log.info(f"  [state] 角色状态.json updated for ch{self.chapter_num}")

        return final_text

    def _phase_sequential(self) -> list[str]:
        """逐节顺序生成：一次调用=一个场景，前一节真实文本串接为后一节上文。

        场景边界由结构锁死（每节独立调用），expanded 节由独立调用 + 展开方向
        真正展开，杜绝旧管线的"批里写穿全章 + expanded 又重写一遍"重复。
        """
        texts = []
        prev_text = self.chapter_context  # 第1节上文 = vault 概要；之后 = 上一节真实文本
        n = len(self.sections)
        for sec in self.sections:
            is_expanded = sec.get("weight") == "expanded"
            route = MODEL_ROUTES["expanded"] if is_expanded else MODEL_ROUTES["normal"]
            log.info(f"\n[pipeline] section {sec['id']} ({sec['subject']}) "
                     f"weight={'expanded' if is_expanded else 'normal'}")
            log.info(f"  model: {route['model']}")
            log.info("-" * 40)

            # 每节篇幅软约束：expanded 用 target_words 或双倍均摊；normal 均摊
            section_len_hint = None
            if self.spec.get("target_chars"):
                if is_expanded:
                    hint = sec.get("target_words") or int(self.spec["target_chars"] / n * 2)
                else:
                    hint = int(self.spec["target_chars"] / n)
                section_len_hint = max(150, hint)

            if is_expanded:
                user = build_expanded_prompt(self.spec, sec, prev_text, section_len_hint)
            else:
                user = build_normal_prompt(self.spec, [sec], prev_text,
                                           self.novel_config, section_len_hint)
            log.info(f"  prompt: {len(user)} chars")

            text = call_api(self.system_prompt, user, route)
            if text is None:
                log.error(f"[ERROR] section {sec['id']} failed")
                sys.exit(1)
            log.info(f"\n  -> {len(text)} chars")
            texts.append(text)

            # 串接上文：之后每节的上文 = 该节真实文本（截断窗口防上下文膨胀）
            prev_text = (text if len(text) <= _SECTION_CONTEXT_WINDOW
                         else text[-_SECTION_CONTEXT_WINDOW:])

        return texts

    def _phase_merge_check(self, section_texts: list[str]) -> str:
        """Phase 3: merge + 字数检查 + 章节状态更新。"""
        log.info("\n[pipeline] Phase 3: merge")
        final_text = merge_sections(section_texts)
        char_count = len(final_text)
        log.info(f"  total: {char_count} chars")

        # 字数检查
        target_chars = self.spec.get("target_chars", 0)
        if target_chars:
            margin = target_chars * 0.2
            lower = int(target_chars - margin)
            upper = int(target_chars + margin)
            if char_count < lower:
                log.warning(f"  SHORT: {char_count} chars (target ~{target_chars}, min {lower})")
            elif char_count > upper:
                log.warning(f"  LONG: {char_count} chars (target ~{target_chars}, max {upper})")
            else:
                log.info(f"  length OK (target ~{target_chars})")

        # 章节状态提取
        log.info("\n[pipeline] Phase 3b: state extraction & consistency check")
        try:
            state_warnings = update_chapter_state(self.novel_dir, self.chapter_num,
                                                   final_text, self.spec,
                                                   self.novel_config)
            for w in state_warnings:
                log.warning(f"  [consistency] {w}")
            if not state_warnings:
                log.info("  no consistency issues")
        except Exception as e:
            log.warning(f"  [state] error: {e}")

        return final_text

    def _phase_antiai(self, text: str) -> str:
        """Phase 4: anti-AI 检查 + 自动修复。"""
        log.info("\n[pipeline] Phase 4: anti-AI check")
        violations = anti_ai_check(text, self.rules)
        clean = print_violations(violations)

        if not clean:
            log.info("\n  Auto-fixing...")
            text = auto_fix_violations(text, violations)
            log.info(f"  After fix: {len(text)} chars")
            # Re-check
            violations2 = anti_ai_check(text, self.rules)
            print_violations(violations2)

        return text

    def _phase_quality_check(self, text: str, max_fix_rounds: int = 1) -> str:
        """Phase 5: 质量检查 + 自动修复循环。

        检查禁止词/比喻密度/篇幅。
        如需修复，加载手写 ch1 作为参考质量标准。
        """
        log.info("\n[pipeline] Phase 5: quality check")

        forbidden = (self.novel_config.get("quality", {})
                     .get("forbidden_words", None))
        issues = quality_check(text, self.spec, self.chapter_num,
                               forbidden_words=forbidden)
        print_quality_issues(issues)

        # 人味缺漏审（仅报告，不打断流程）
        verve_findings = verve_review(text, self.spec, self.novel_config)
        print_verve_review(verve_findings)

        if not [i for i in issues if i.get("fixable")]:
            return text

        # 加载手写 ch1 作为 auto-fix 的参考质量标准（找 chapters/01_*.md）
        reference_text = None
        ref_path = None
        chapters_dir = self.novel_dir / "chapters"
        if chapters_dir.is_dir():
            for p in sorted(chapters_dir.glob("01_*.md")):
                ref_path = p
                break
        if ref_path:
            reference_text = ref_path.read_text(encoding="utf-8")[:2000]
            log.info(f"  [ref] 加载手写 ch1 作为修复参考 ({len(reference_text)} chars)")

        for attempt in range(max_fix_rounds):
            log.info(f"\n  Auto-fix attempt {attempt + 1}/{max_fix_rounds}...")
            fixed, ok = auto_fix_quality(
                text, issues, self.system_prompt, MODEL_ROUTES["normal"],
                reference_text=reference_text,
            )
            if not ok:
                break

            # Re-check after fix
            issues_after = quality_check(fixed, self.spec, self.chapter_num,
                                         forbidden_words=forbidden)
            print_quality_summary(issues, issues_after)
            remaining = [i for i in issues_after if i.get("fixable")]

            if not remaining:
                log.info("[✓] All fixable issues resolved")
                text = fixed
                break

            text = fixed
            issues = issues_after

        else:
            log.warning(f"  [!] {len(remaining)} fixable issue(s) remain after {max_fix_rounds} round(s)")

        return text

    def _phase_output(self, text: str):
        """Phase 5: 写文件。"""
        log.info(f"\n[pipeline] Output: {self.output_path}")
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(text + "\n", encoding="utf-8")
        log.info("[pipeline] Done.")


# ── Main ──────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2 or "--help" in sys.argv or "-h" in sys.argv:
        print("Usage:", file=sys.stderr)
        print("  python gen.py <spec_path>                           # validate + generate", file=sys.stderr)
        print("  python gen.py --validate <spec_path>                # validate only", file=sys.stderr)
        print("  python gen.py --force <spec_path>                   # skip validation", file=sys.stderr)
        print("  python gen.py --use-engine <tick.json> --chapter N  # tick -> gen", file=sys.stderr)
        sys.exit(0 if "--help" in sys.argv or "-h" in sys.argv else 1)

    validate_only = False
    force = False
    use_engine = False
    engine_tick_path = None
    engine_chapter = None
    engine_novel = None
    spec_arg = None

    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--validate":
            validate_only = True
        elif arg == "--force":
            force = True
        elif arg == "--use-engine":
            use_engine = True
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                i += 1
                engine_tick_path = argv[i]
        elif arg == "--chapter":
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                i += 1
                engine_chapter = int(argv[i])
        elif arg == "--novel":
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                i += 1
                engine_novel = argv[i]
        else:
            spec_arg = arg
        i += 1

    # ── Load spec (from file or engine tick) ──
    if use_engine:
        if not engine_tick_path:
            log.error("ERROR: --use-engine requires <tick_json_path>")
            sys.exit(1)
        if engine_chapter is None:
            log.error("ERROR: --use-engine requires --chapter <num>")
            sys.exit(1)

        tick_data = load_tick_result(engine_tick_path)
        # novel 优先级：--novel 标志 > tick 元数据 > 默认
        novel = engine_novel or tick_data.get("novel", "")
        # framework 是通用包（项目根）；novel agents 在 novels/<novel>/agents，需小说目录入 sys.path
        novel_dir = BASE / "novels" / novel
        sys.path.append(str(novel_dir))

        # Try LLM-driven SpecBuilder first, fall back to mechanical build
        try:
            from framework.spec_builder import SpecBuilder
            builder = SpecBuilder(novel_dir=str(novel_dir))
            spec = builder.build(tick_data, engine_chapter, novel=novel)
        except ImportError:
            spec = None
            log.info("[engine] SpecBuilder not available, using mechanical build")
        except Exception as e:
            spec = None
            log.warning(f"[engine] SpecBuilder error: {e}, fallback to mechanical")

        if not spec:
            spec = build_spec_from_tick(tick_data, engine_chapter, novel)
            log.info("[engine] mechanical build used")

        force = True  # auto-built spec skips validation
        log.info(f"[engine] tick: {spec['tick_arc_id'] if spec.get('tick_arc_id') else '?'}, "
                 f"ch{engine_chapter}, {len(spec.get('sections', []))} sections")
    else:
        if not spec_arg:
            log.error("ERROR: no spec path provided (use --use-engine for tick mode)")
            sys.exit(1)

        spec_path = Path(spec_arg)
        if not spec_path.is_absolute():
            spec_path = BASE / spec_path

        with open(spec_path, encoding="utf-8") as f:
            spec = json.load(f)

    novel = spec["novel"]
    title = spec["title"]
    novel_dir = BASE / "novels" / novel
    # 内容包可在仓库外（私有连载库）：spec 声明 novel_dir 即指向外部。
    # 相对路径以 spec 所在目录为锚（"novel_dir": ".." = 内容包根目录）；
    # 缺省落回 novels/<novel>（示例/默认布局）。engine tick 模式不受影响。
    if not use_engine and spec.get("novel_dir"):
        nd = Path(spec["novel_dir"])
        novel_dir = (nd if nd.is_absolute() else spec_path.parent / nd).resolve()

    # ── Vault reader ──
    vault_reader_obj = None
    chapter_num = spec.get("chapter", 1)
    vault_dir = novel_dir / "vault"
    if vault_dir.is_dir():
        sys.path.append(str(novel_dir))
        try:
            from lib.vault_reader import VaultReader
            vault_reader_obj = VaultReader(str(vault_dir))
            log.info(f"[vault] reading from {vault_dir}")
        except ImportError as e:
            log.warning(f"[vault] WARN: couldn't load vault_reader: {e}")

    # ── Validation ──
    if not force:
        issues = validate_spec(spec, novel_dir)
        # Add vault continuity checks
        if vault_reader_obj:
            try:
                vault_issues = vault_reader_obj.check_continuity(chapter_num, spec)
                for w in vault_issues:
                    issues.append(("WARN", w))
            except Exception as e:
                log.warning(f"  [vault] continuity check error: {e}")
        has_error = print_validation_issues(issues)
        if validate_only:
            sys.exit(1 if has_error else 0)
        if has_error:
            log.error("\n[!] Fix errors above, or use --force to skip validation")
            sys.exit(1)

    # ── Output path ──
    output_path = spec.get("output")
    if output_path:
        output_path = (spec_path.parent / output_path).resolve()
    else:
        output_path = BASE / "novels" / novel / "chapters" / f"{title}.md"

    # ── Load rules ──
    rules = load_adapt_rules()

    # ── Character state ──
    character_state = load_character_state(novel_dir)

    # ── Novel config（框架与小说的接口）──
    novel_config = load_novel_config(str(novel_dir))

    # ── System prompt ──
    system_prompt = build_system_prompt(novel, novel_dir, character_state,
                                        novel_config)

    # ── Chapter context ──
    chapter_context = ""
    if vault_reader_obj:
        try:
            chapter_context = vault_reader_obj.build_context_for_model(chapter_num)
            if chapter_context:
                log.info(f"[vault] vault context: {len(chapter_context)} chars")
        except Exception as e:
            log.warning(f"  [vault] context build error: {e}")

    # ── Run pipeline ──
    pipeline = Pipeline(spec, novel_dir, vault_reader_obj, rules,
                        system_prompt, output_path, chapter_context, character_state,
                        novel_config)
    pipeline.run()


if __name__ == "__main__":
    main()
