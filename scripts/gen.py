#!/usr/bin/env python3
"""
Novel generation pipeline.

Reads a chapter spec → assembles prompts from adapt rules → routes to models →
generates sections (normal together, expanded individually) → stitches → anti-AI check.

Usage:
    python scripts/gen.py novels/<novel>/specs/<spec>.json

Example:
    python scripts/gen.py novels/示例/specs/ch1.json
"""

import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from collections import defaultdict

# ── Ensure project root is on sys.path for lib/ and server/ imports ────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.log import get_logger
log = get_logger("gen")

# ── Paths ──────────────────────────────────────────────────────────────
BASE = Path(__file__).resolve().parent.parent  # scripts/../ = project root
ADAPT = BASE / "adapt"

# ── Model routes ───────────────────────────────────────────────────────
from server._api_client import MODEL_ROUTES


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
            # "主角（主角）" → "主角"
            current_name = line[3:].split("（")[0].strip()
            current_lines = []
        elif current_name:
            current_lines.append(line)

    if current_name:
        sections[current_name] = "\n".join(current_lines).strip()

    return sections


@lru_cache(maxsize=4)
def load_worldbuilding(novel_dir):
    """Load and distill worldbuilding rules from bible/世界观.md.

    Returns a concise rule block for system prompt, or empty string.
    """
    from lib.bible_utils import load_bible_file
    text = load_bible_file(novel_dir, "世界观.md")
    if not text:
        log.info(f"  [info] no worldbuilding bible at {Path(novel_dir) / 'bible' / '世界观.md'}")
        return ""

    lines = text.split("\n")

    # Extract content under key sections
    sections = {}
    current_key = None
    current_lines = []
    key_markers = {
        "异常本质": "异常本质",
        "阶段递进": "阶段递进结构",
        "理性边界": "理性的边界",
        "进化": "进化 + 主动选择",
    }

    for line in lines:
        stripped = line.strip()
        # Detect key section headers
        for key, marker in key_markers.items():
            if marker in stripped and stripped.startswith("###"):
                if current_key:
                    sections[current_key] = current_lines
                current_key = key
                current_lines = []
                break
        else:
            if current_key and stripped:
                current_lines.append(stripped)

    if current_key:
        sections[current_key] = current_lines

    def strip_md(s):
        """Remove markdown bold markers from string."""
        return s.replace("**", "")

    # Build compact rule block
    rules = ["## 世界观规则"]

    if "异常本质" in sections:
        # Take clean sentences
        content = "\n".join(sections["异常本质"])
        # Split into individual lines, take the ones that start a concept
        key_sentences = []
        for line_n in content.split("\n"):
            s = strip_md(line_n.strip())
            if not s:
                continue
            # Skip ### headers, bullet markers, table lines
            if s.startswith("###") or s.startswith("- **") or s.startswith("|"):
                continue
            # Skip lines about "触发机制" etc that are sub-section titles
            if s.startswith("触发机制") or s.startswith("传导"):
                continue
            key_sentences.append(s)

        # Take up to 3 clean sentences
        taken = 0
        for s in key_sentences:
            if taken >= 3:
                break
            # Skip sentences that are too long (contain multiple concepts)
            if len(s) > 150 and "。" in s:
                for part in s.split("。"):
                    if part.strip() and taken < 3:
                        rules.append(part.strip() + "。")
                        taken += 1
            elif s.endswith("。") or s.endswith("。"):
                rules.append(s)
                taken += 1

    if "阶段递进" in sections:
        content = "\n".join(sections["阶段递进"])
        for line_n in content.split("\n"):
            s = strip_md(line_n.strip())
            if "外层" in s and "身体" in s:
                rules.append(s)
            elif "中层" in s and "认知" in s:
                rules.append(s)
            elif "内层" in s and "规则" in s and "改写" in s:
                rules.append(s)
            elif "规则倾向" in s:
                rules.append(s)

    if "理性边界" in sections:
        content = " ".join(sections["理性边界"])
        s = strip_md(content)
        # Take the line about Lin Mo's limitation, skip table rows and artifacts
        lines_clean = [l for l in s.split(" ") if l.strip() and "|---" not in l and not l.startswith("|")]
        combined = " ".join(lines_clean).replace("|", "")
        if "主角" in combined:
            idx = combined.find("主角")
            end = combined.find("。", idx)
            if end > idx:
                rules.append(combined[idx:end+1].strip())

    if "进化" in sections:
        content = "\n".join(sections["进化"])
        taken = 0
        for line_n in content.split("\n"):
            s = strip_md(line_n.strip())
            if not s or s.startswith("###"):
                continue
            if s.startswith("🔬") or s.startswith("- "):
                continue
            if s.startswith("但主角") or "主动选择异常方向" in s:
                rules.append(s)
                taken += 1
            elif "反复做" in s or "变异是创伤" in s:
                rules.append(s)
                taken += 1
            if taken >= 3:
                break
            # Limit to 3 lines
            if sum(1 for r in rules if "进化" in r or "反复" in r or "变异是" in r or "主动" in r) >= 3:
                break

    # Core principle (always appended)
    rules.append("核心原则：异常没有目的。不同角色有自己的解读（神罚/进化/噪声）。主角的AI思维让她主动分析规则→选择方向→让身体生长。把自己当实验对象优化。")

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
    # Optional files — load if exist
    for fname in ("character-profiles.json",):
        p = ADAPT / fname
        if p.exists():
            rules[fname.replace(".json", "").replace("-", "_")] = _load_json(p)
    return rules


# ── System prompt assembly ─────────────────────────────────────────────
def build_system_prompt(novel_title, novel_dir):
    """Assemble system prompt from active guidance rules (not prohibition-heavy).

    Character bible (bible/人设.md) is loaded if available to enrich character anchors.
    Worldbuilding rules (bible/世界观.md) are loaded if available.
    """
    characters = load_character_bible(novel_dir)
    world_rules = load_worldbuilding(novel_dir)

    # ── Core writing rules (always present, read from bible/) ──
    rules = load_bible_file(novel_dir, "写作法则.md")

    # ── Physics constraints (read from bible/) ──
    physics = load_bible_file(novel_dir, "物理约束.md")

    # ── Banned words (read from bible/) ──
    forbidden = load_bible_file(novel_dir, "禁止词.md")

    # ── Character anchors (read from bible/人设.md → 模型注入锚点) ──
    inject = characters.get("模型注入锚点", "").strip()
    if inject:
        char_section = f"## 人物锚点\n\n{inject}"
    elif fb := load_bible_file(novel_dir, "人物锚点.md"):
        char_section = fb
    else:
        char_section = "## 人物锚点\n\n主角：28岁，AI博士。不抽烟。缺陷：计算强迫。"

    parts = [
        f"你是悬疑小说《{novel_title}》的写作者。",
        rules,
        physics,
        world_rules,
        char_section,
        forbidden,
    ]

    # Filter out empty sections
    parts = [p for p in parts if p.strip()]

    return "\n\n".join(parts)


# ── User prompt assembly ──────────────────────────────────────────────
def build_normal_prompt(spec, sections, context_before=None):
    """Build user prompt for a batch of normal-weight sections."""
    lines = [f"## {spec['title']}·写作需求", ""]
    if "mood" in spec and spec["mood"]:
        lines.append(f"情绪线：{spec['mood']}")
        lines.append("")

    lines.append("世界观：悬疑+世界突变。主角28岁，和亲属在城市度假村。")
    lines.append("第一章里异常不出现，只出异常。亲属感受不到异常，和主角形成落差。")
    lines.append("")

    if context_before:
        lines.append("### 上文概要")
        lines.append(context_before)
        lines.append("")

    lines.append(f"### {len(sections)}节写作需求")
    lines.append("")

    for sec in sections:
        lines.append(f"#### {sec['id']}、{sec['subject']}")
        lines.append(sec["description"])
        if "tension_direction" in sec and sec["tension_direction"]:
            lines.append(f"张力方向：{sec['tension_direction']}")
        lines.append("")

    lines.append("## 输出要求")
    lines.append("直接输出正文。不要注释、不要说明。")
    lines.append("每节以" + "、".join(f"\"{s['id']}\"" for s in sections) + "开头单独一行。")
    lines.append("节与节之间用 *** 分隔。")
    lines.append("")
    lines.append("### 每节硬约束（写前必读，超过任何系统指引）")
    lines.append("- 计数限1次以内。主角不是一个在数数的人。如果这节不需要计数，就不要写。")
    lines.append("- 主角至少做一次推演——不写她觉得怎样，写她算了什么、排除了什么、选了哪条路。")
    lines.append("- 不写主角自己不会注意到的信息（她的穿着、外貌、上帝视角时间）。")
    lines.append("- 写不下去时允许用 *** 或空行跳过，不补充填充句。")
    lines.append("")

    return "\n".join(lines)


def build_expanded_prompt(spec, section, context_before=None):
    """Build user prompt for a single expanded section."""
    lines = [f"## {spec['title']}·单独段落写作", ""]
    lines.append(f"这一段是章节的核心段落，需要详细展开。至少写{section.get('target_words', 600)}字。")
    lines.append("")

    if context_before:
        lines.append("### 上文概要（用于衔接上下文）")
        lines.append(context_before)
        lines.append("")

    lines.append(f"### {section['id']}、{section['subject']}")
    lines.append(section['description'])
    lines.append("")

    if "expanded_direction" in section and section["expanded_direction"]:
        lines.append("### 展开方向（重点）")
        lines.append(section["expanded_direction"])
        lines.append("")

    if "tension_direction" in section and section["tension_direction"]:
        lines.append("### 张力方向")
        lines.append(section["tension_direction"])
        lines.append("")

    lines.append("### 输出要求")
    lines.append("直接输出这一个段落的正文。不要注释、不要说明。")
    lines.append("注意分段：和前面各节一样的格式，短段落，1-3句换行，场景转换用空行隔开。")
    lines.append(f"第一行写\"{section['id']}\"作为节标记，然后直接写正文。")
    lines.append("")
    lines.append("### 硬约束（写前必读，超过任何系统指引）")
    lines.append("- 计数限1次以内。主角不是一个在数数的人。如果这节不需要计数，就不要写。")
    lines.append("- 至少写一次主角的推演——不写她觉得怎样，写她算了什么、排除了什么、选了哪条路。")
    lines.append("- 不写主角自己不会注意到的信息（她的穿着、外貌、上帝视角时间）。")
    lines.append("- 写不下去时用 *** 跳过，不补充填充句。")
    lines.append("")

    return "\n".join(lines)


# ── API call ──────────────────────────────────────────────────────────
def call_api(system_prompt, user_prompt, route, silent=False):
    """Call the generation API with SSE streaming."""
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

    if silent:
        result = sse_request(body)
        return result if result else None

    full = []

    def on_chunk(c):
        print(c, end="", flush=True)
        full.append(c)

    result = sse_request(body, stream_callback=on_chunk)
    if not result and not full:
        return None  # error — consistent with original contract
    if result and not "".join(full):
        return result
    return "".join(full) or None


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
    high_words -= {"似乎", "仿佛", "好像", "不禁", "不由得", "下意识"}

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


# ── Chapter state extraction ───────────────────────────────────────────


def update_state_file(state_path, chapter_num, extracted_text):
    """Update 章节状态.md with extracted chapter state."""
    clean_text = extracted_text.strip()
    target = f"## 第{chapter_num}章"
    new_section = f"{target}\n\n{clean_text}\n\n---"

    if not state_path.exists():
        header = "# 章节状态记录\n\n> 每完成一章自动更新。\n\n"
        ref_table = "\n## 章节对照表\n\n| 章 | 时间跨度 | 地点 | 核心事件 | 主角异常阶段 |\n|---|---|---|---|---|\n"
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


def update_chapter_state(novel_dir, chapter_num, chapter_text, spec=None):
    """Extract state from generated chapter and update 章节状态.md.

    确定性规则提取（无 LLM 调用）。
    """
    state_path = Path(novel_dir) / "chapters" / "章节状态.md"

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

    # 从 spec 推断主角状态
    char_hints = []
    for sec in sections:
        desc = sec.get("description", "")
        for marker in ["主角", "她"]:
            if marker in desc:
                first_sentence = desc.split("。")[0] if "。" in desc else desc[:60]
                char_hints.append(first_sentence)
                break

    if char_hints:
        parts.append("### 主角状态（从 spec 推断）")
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
    update_state_file(state_path, chapter_num, result_text)
    return []



# ── Merge sections ─────────────────────────────────────────────────────
def parse_normal_sections(text, normal_secs):
    """Parse generated text into {section_id: content}.

    Each section starts with its id marker (一, 一、, 二, 二、...).
    Returns dict of {section_id: text}.
    """
    result = {}
    current_id = None
    current_lines = []

    for line in text.split("\n"):
        stripped = line.strip()
        matched_id = None
        for sec in normal_secs:
            if stripped == sec["id"] or stripped.startswith(sec["id"] + "、"):
                matched_id = sec["id"]
                break
        if matched_id:
            if current_id:
                result[current_id] = "\n".join(current_lines)
            current_id = matched_id
            current_lines = [line]
        else:
            if current_id:
                current_lines.append(line)

    if current_id:
        result[current_id] = "\n".join(current_lines)

    return result


def merge_sections(spec, normal_texts, expanded_texts):
    """Merge multiple normal batch texts + expanded texts at correct positions."""
    sections = spec["sections"]
    normal_secs = [s for s in sections if s.get("weight", "normal") != "expanded"]

    # Parse all normal batch texts
    all_normal = {}
    for text in normal_texts:
        parsed = parse_normal_sections(text, normal_secs)
        all_normal.update(parsed)

    if not all_normal:
        # Fallback: use first normal text as-is
        fallback = normal_texts[0] if normal_texts else ""
        log.warning("[!] Section parsing failed, using raw normal_text")
        for sec in normal_secs:
            all_normal[sec["id"]] = fallback
        if not all_normal:
            return ""

    # Assemble in spec order
    result_parts = []
    for sec in sections:
        sid = sec["id"]
        if sec.get("weight") == "expanded":
            if sid in expanded_texts:
                result_parts.append(expanded_texts[sid])
            else:
                log.warning(f"[!] Missing expanded section {sid}")
        else:
            if sid in all_normal:
                result_parts.append(all_normal[sid])

    if not result_parts:
        return normal_texts[-1] if normal_texts else ""

    # Strip trailing *** and whitespace from each part
    cleaned = []
    for text in result_parts:
        lines = text.rstrip().split("\n")
        while lines and lines[-1].strip() == "***":
            lines.pop()
            if lines and lines[-1].strip() == "":
                lines.pop()
        cleaned.append("\n".join(lines).strip())

    # Dedup: if section parsing failed and all parts are identical, return just one
    if len(cleaned) > 1 and len(set(cleaned)) == 1:
        cleaned = [cleaned[0]]

    return "\n\n***\n\n".join(cleaned)


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
                 chapter_context=""):
        self.spec = spec
        self.novel_dir = novel_dir
        self.vault_reader = vault_reader
        self.rules = rules
        self.system_prompt = system_prompt
        self.output_path = output_path
        self.chapter_context = chapter_context
        self.chapter_num = spec.get("chapter", 1)

        self.expanded_secs = [s for s in spec["sections"] if s.get("weight") == "expanded"]
        self.normal_secs = [s for s in spec["sections"] if s.get("weight", "normal") != "expanded"]

        # Group normal sections by batch
        self.batch_groups = defaultdict(list)
        for s in self.normal_secs:
            self.batch_groups[s.get("batch", 0)].append(s)

    def run(self) -> str:
        """执行完整管线。返回最终文本。"""
        log.info(f"[pipeline] {self.spec['title']}  ->  {self.output_path.name}")
        log.info(f"[pipeline] sections: {len(self.spec['sections'])}")
        log.info(f"[pipeline] system prompt: {len(self.system_prompt)} chars")

        normal_texts = self._phase_normal_batches()
        expanded_texts = self._phase_expanded_sections(normal_texts)
        final_text = self._phase_merge_check(normal_texts, expanded_texts)
        final_text = self._phase_antiai(final_text)
        self._phase_output(final_text)
        return final_text

    def _phase_normal_batches(self) -> list[str]:
        """Phase 1: 生成 normal-weight 段落。"""
        texts = []
        for batch_id in sorted(self.batch_groups.keys()):
            batch_secs = self.batch_groups[batch_id]
            log.info(f"\n[pipeline] Phase 1: normal batch {batch_id}")
            log.info(f"  sections: {[s['id'] for s in batch_secs]}")
            log.info(f"  model: {MODEL_ROUTES['normal']['model']}")
            log.info("-" * 40)

            normal_user = build_normal_prompt(self.spec, batch_secs, self.chapter_context)
            log.info(f"  prompt: {len(normal_user)} chars")

            text = call_api(self.system_prompt, normal_user, MODEL_ROUTES["normal"])
            if text is None:
                log.error(f"[ERROR] Phase 1 batch {batch_id} failed")
                sys.exit(1)
            log.info(f"\n  -> {len(text)} chars")
            texts.append(text)
        return texts

    def _phase_expanded_sections(self, normal_texts: list[str]) -> dict[str, str]:
        """Phase 2: 生成 expanded-weight 段落。"""
        expanded_texts = {}
        if not self.expanded_secs:
            return expanded_texts

        log.info("\n[pipeline] Phase 2: expanded sections")

        # Build context from chapter context + ALL normal batches
        context_summary = ""
        if self.chapter_context:
            context_summary = self.chapter_context + "\n\n"
        context_summary += "前文概要："
        for sec in self.normal_secs:
            context_summary += f"{sec['id']}、{sec['subject']}。"

        for sec in self.expanded_secs:
            log.info(f"  section: {sec['id']} ({sec['subject']})")
            log.info(f"  model: {MODEL_ROUTES['expanded']['model']}")
            log.info("-" * 40)

            expanded_user = build_expanded_prompt(self.spec, sec, context_summary)
            log.info(f"  prompt: {len(expanded_user)} chars")

            result = call_api(self.system_prompt, expanded_user, MODEL_ROUTES["expanded"])
            if result is None:
                log.warning(f"[WARN] Section {sec['id']} failed, skipping")
                continue
            expanded_texts[sec["id"]] = result
            log.info(f"\n  -> {len(result)} chars")

        return expanded_texts

    def _phase_merge_check(self, normal_texts: list[str],
                            expanded_texts: dict[str, str]) -> str:
        """Phase 3: merge + 字数检查 + 章节状态更新。"""
        log.info("\n[pipeline] Phase 3: merge")
        final_text = merge_sections(self.spec, normal_texts, expanded_texts)
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
                                                   final_text, self.spec)
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
        novel = "示例"

        # Try LLM-driven SpecBuilder first, fall back to mechanical build
        try:
            from engine.spec_builder import SpecBuilder
            builder = SpecBuilder()
            spec = builder.build(tick_data, engine_chapter)
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

    # ── System prompt ──
    system_prompt = build_system_prompt(novel, novel_dir)

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
                        system_prompt, output_path, chapter_context)
    pipeline.run()


if __name__ == "__main__":
    main()
