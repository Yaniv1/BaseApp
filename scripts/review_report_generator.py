"""Original APP-TASK-260820 requirement renderer and embedded interface, parameterized for BaseApp."""
import argparse
import datetime as dt
import hashlib
import html
import json
import re
import shutil
import textwrap
import sys
import threading
from functools import lru_cache
from collections import defaultdict
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.reviewutils import (
    FeatureStatus, ReviewStore, add_runtime_arguments, atomic_json, atomic_write,
    code_eligible, feature_code_approvable, file_hash, fingerprint, git_changes, natural_key, read_json,
    report_path, requirement_code_approvable, resolve_runtime, safe_artifact, spec_eligible,
    with_code_approval_eligibility,
)

TASK_ID = None
ROOT = None
REVIEW_BASE = None
FEATURE_STATUS = FeatureStatus
_RENDER_LOCK = threading.RLock()
DATA_SUFFIXES = {".json", ".csv", ".txt"}
DEPENDENCY_STOPWORDS = {
    "append", "app", "azure", "bool", "close", "config", "container",
    "dict", "error", "false", "file",
    "float", "format", "get", "items", "join", "len", "list", "load",
    "log", "main", "max", "min", "mode", "module", "name", "none",
    "open", "output", "param", "path", "print", "read", "report",
    "resource", "result", "return", "run", "self", "set", "split",
    "status", "str", "string", "true", "tuple", "type", "update",
    "value", "values", "write",
}




# Original upstream diff/render helpers, extracted without source-task catalogs.
import ast
import difflib
import subprocess
from types import SimpleNamespace
MANUAL_CHANGE_DETAILS = {}
def run(*args, text=True):
    return subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=text,
    ).stdout

def changed_files():
    return list(git_changes(ROOT, REVIEW_BASE))

def is_tracked(relative):
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relative],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0

def read_head(relative):
    result = subprocess.run(
        ["git", "show", f"{REVIEW_BASE}:{relative}"],
        cwd=ROOT,
        capture_output=True,
    )
    if result.returncode:
        return ""
    return result.stdout.decode("utf-8", errors="replace")

def read_current(relative):
    path = safe_artifact(ROOT, relative)
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

def locate(lines, needle):
    if not needle:
        return 1
    lowered = needle.lower()
    for index, line in enumerate(lines, start=1):
        if lowered in line.lower():
            return index
    return 1

def snippet(content, needle, span):
    lines = content.splitlines()
    if not lines:
        return "", 1, 1
    center = locate(lines, needle)
    before = max(1, span // 3)
    start = max(1, center - before)
    end = min(len(lines), start + span - 1)
    numbered = "\n".join(lines[start - 1:end])
    return numbered + ("\n" if numbered else ""), start, end

def status_stats(relative):
    if is_tracked(relative):
        output = run("git", "diff", REVIEW_BASE, "--numstat", "--", relative).strip()
        if output:
            added, removed, _ = output.split("\t", 2)
            return added, removed, "Modified"
        return "0", "0", "Tracked"
    lines = read_current(relative).splitlines()
    return str(len(lines)), "0", "Untracked"

def _element_at(relative, content, line_number, fallback):
    lines = content.splitlines()
    if not lines:
        return fallback
    index = min(max(1, line_number), len(lines)) - 1
    suffix = Path(relative).suffix.lower()
    patterns = []
    if suffix == ".py":
        patterns = [
            r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(",
            r"^\s*class\s+([A-Za-z_]\w*)",
        ]
    elif suffix in (".js", ".ts", ".html"):
        patterns = [
            r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(",
            r"^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=",
            r"\bid=[\"']([^\"']+)[\"']",
        ]
    elif suffix in (".ps1", ".psm1"):
        patterns = [
            r"^\s*function\s+([A-Za-z_][\w-]*)",
            r"^\s*\[Parameter[^\]]*\]\s*(?:\[[^\]]+\]\s*)?\$([A-Za-z_]\w*)",
        ]
    elif suffix == ".bicep":
        patterns = [
            r"^\s*(?:resource|module|param|var|output)\s+([A-Za-z_]\w*)",
        ]
    elif suffix == ".json":
        patterns = [r'^\s*"([^"]+)"\s*:']
    elif suffix in (".md", ".txt"):
        patterns = [r"^\s*#+\s+(.+?)\s*$"]
    for cursor in range(index, max(-1, index - 180), -1):
        for pattern in patterns:
            match = re.search(pattern, lines[cursor])
            if match:
                return match.group(1)
    return fallback

def _compact_code(lines, limit=700):
    values = [line.strip() for line in lines if line.strip()]
    if not values:
        return ""
    value = " ".join(values)
    return value if len(value) <= limit else value[: limit - 1] + "…"

def _semantic_requirement(question):
    text = question.strip().rstrip(".")
    replacements = (
        ("Confirm ", "ensures "),
        ("Check that ", "ensures "),
        ("Check ", "enforces "),
        ("Verify ", "ensures "),
        ("Review whether ", "ensures "),
        ("Review how ", "coordinates "),
        ("Review ", "implements "),
        ("Resolve ", "resolves "),
        ("Compare ", "keeps "),
    )
    for prefix, replacement in replacements:
        if text.startswith(prefix):
            return replacement + text[len(prefix):]
    return "implements " + text[:1].lower() + text[1:]

def _implementation_steps(question, element_name):
    text = question.strip().rstrip(".")
    prefixes = (
        "Review how ",
        "Review whether ",
        "Review ",
        "Confirm that ",
        "Confirm ",
        "Check that ",
        "Check ",
        "Verify that ",
        "Verify ",
        "Resolve ",
        "Compare ",
    )
    for prefix in prefixes:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    clauses = [
        value.strip()
        for value in re.split(r",\s*(?:and\s+)?|\s+and\s+", text)
        if value.strip()
    ]
    if not clauses:
        clauses = [text]
    steps = []
    for clause in clauses:
        if re.match(r"^(?:the\s+)?[A-Za-z_][\w -]*\s+(?:is|are|copies|creates|loads|writes|reads|uses|keeps|preserves|rejects|returns|filters|maps|syncs|uploads|downloads)\b", clause, re.I):
            sentence = clause[:1].upper() + clause[1:]
        else:
            sentence = f"{element_name} handles {clause}"
        steps.append(sentence.rstrip(".") + ".")
    return steps

def _how_steps(value):
    if isinstance(value, list):
        return [str(item).strip().rstrip(".") + "." for item in value if str(item).strip()]
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", str(value or "").strip())
        if sentence.strip()
    ]
    if len(sentences) == 1 and ("," in sentences[0] or ";" in sentences[0]):
        sentences = [
            part.strip()
            for part in re.split(r";\s*|,\s*(?:and\s+)?", sentences[0])
            if part.strip()
        ]
    return [sentence.rstrip(".") + "." for sentence in sentences]

def _step_element_name(needle, title):
    text = str(needle or "").strip()
    match = re.match(
        r"^(?:def|class|function)\s+([A-Za-z_$][\w$-]*)",
        text,
    )
    if match:
        return match.group(1)
    if text.startswith('"') and text.endswith('"'):
        return text.strip('"')
    return text or title

def _diff_hunks(relative):
    if not is_tracked(relative):
        return []
    output = run("git", "diff", REVIEW_BASE, "--unified=0", "--", relative)
    hunks = []
    current = None
    for line in output.splitlines():
        match = re.match(
            r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@\s*(.*)$",
            line,
        )
        if match:
            if current:
                hunks.append(current)
            current = {
                "old_start": int(match.group(1)),
                "old_count": int(match.group(2) or "1"),
                "new_start": int(match.group(3)),
                "new_count": int(match.group(4) or "1"),
                "context": match.group(5).strip(),
                "removed": [],
                "added": [],
            }
        elif current and line.startswith("-") and not line.startswith("---"):
            current["removed"].append(line[1:])
        elif current and line.startswith("+") and not line.startswith("+++"):
            current["added"].append(line[1:])
    if current:
        hunks.append(current)
    return hunks

def _evidence_lines(value):
    """Return concise, code-bearing lines rather than braces or comments."""
    result = []
    for raw in str(value or "").splitlines():
        line = re.sub(r"\s+", " ", raw.strip())
        if (
            not line
            or line in {"{", "}", "[", "]", "},", "],", ")", ");"}
            or line.startswith(("#---", "//---", "<!--", "# "))
        ):
            continue
        if len(line) > 180:
            line = line[:177] + "..."
        if line not in result:
            result.append(line)
    return result

def _changed_evidence(before, after):
    removed = []
    added = []
    matcher = difflib.SequenceMatcher(
        None, str(before or "").splitlines(), str(after or "").splitlines(),
        autojunk=False,
    )
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag in {"delete", "replace"}:
            removed.extend(
                _evidence_lines("\n".join(
                    str(before or "").splitlines()[old_start:old_end]
                ))
            )
        if tag in {"insert", "replace"}:
            added.extend(
                _evidence_lines("\n".join(
                    str(after or "").splitlines()[new_start:new_end]
                ))
            )
    return removed, added

def _quoted_evidence(lines, limit=4):
    selected = list(lines)[:limit]
    return "; ".join(f"`{line}`" for line in selected)

def _implementation_symbols(lines):
    text = "\n".join(lines)
    symbols = []
    patterns = (
        r"\b(?:def|class|function|resource|module|param|var)\s+"
        r"([A-Za-z_$][A-Za-z0-9_$.-]*)",
        r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*\(",
        r"[\"']([A-Za-z_][A-Za-z0-9_.-]{2,})[\"']\s*:",
        r"--([a-z][a-z0-9-]+)",
    )
    ignored = {
        "if", "for", "while", "return", "print", "str", "int", "len",
        "list", "dict", "set", "tuple", "bool", "float", "max", "min",
    }
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = match.group(1)
            if value.lower() not in ignored and value not in symbols:
                symbols.append(value)
    return symbols[:10]

def step_change_details(relative, needle, start, end, title, question):
    manual = MANUAL_CHANGE_DETAILS.get((relative, needle))
    if manual:
        return manual
    baseline = read_head(relative)
    current = read_current(relative)
    before, _, _ = snippet(baseline, needle, max(12, end - start + 1))
    after, _, _ = snippet(current, needle, max(12, end - start + 1))
    removed, added = _changed_evidence(before, after)
    before_lines = removed or _evidence_lines(before)
    now_lines = added or _evidence_lines(after)
    selector = needle.strip().strip("\"'") or title
    if not baseline:
        before_text = (
            f"The task baseline did not contain `{relative}`; "
            f"`{selector}` and its responsibility were introduced by this task."
        )
    elif removed:
        before_text = (
            f"In the task baseline, `{selector}` used or declared "
            f"{_quoted_evidence(before_lines)}."
        )
    else:
        before_text = (
            f"The baseline form of `{selector}` was centered on "
            f"{_quoted_evidence(before_lines) or 'the existing focused block'}."
        )
    if added:
        now_text = (
            f"The task version of `{selector}` adds or replaces "
            f"{_quoted_evidence(now_lines)}."
        )
    else:
        now_text = (
            f"The current focused block retains "
            f"{_quoted_evidence(now_lines) or 'the selector contract'} "
            f"while its surrounding artifact supplies the reviewed behavior."
        )
    symbols = _implementation_symbols(added or now_lines)
    removed_symbols = _implementation_symbols(removed)
    how_parts = []
    if symbols:
        how_parts.append(
            "The implementation is carried by "
            + ", ".join(f"`{value}`" for value in symbols)
        )
    if removed_symbols:
        how_parts.append(
            "and supersedes "
            + ", ".join(f"`{value}`" for value in removed_symbols)
        )
    how_parts.append(
        f"within `{relative}` around lines {start}-{end}"
    )
    return [{
        "name": selector,
        "before": before_text,
        "now": now_text,
        "how": "; ".join(how_parts) + ".",
    }]

def inline_diff_html(before, after, focus_start=1, focus_end=None):
    """Render the actual full-file diff clipped to one working-tree range."""
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    focus_start_index = max(0, int(focus_start or 1) - 1)
    focus_end_index = min(
        len(after_lines),
        int(focus_end or len(after_lines)),
    )
    matcher = difflib.SequenceMatcher(
        None, before_lines, after_lines, autojunk=False
    )
    rows = []
    focus_size = max(1, focus_end_index - focus_start_index)

    def cell(number, text, side, state):
        number_html = "" if number is None else str(number)
        code = html.escape(text).replace("\t", "    ") if text is not None else ""
        return (
            f'<td class="diff-number {state}">{number_html}</td>'
            f'<td class="diff-code diff-{side} {state}"><pre>{code}</pre></td>'
        )

    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "delete":
            if not (focus_start_index <= new_start <= focus_end_index):
                continue
            selected_old_start = old_start
            selected_old_end = min(old_end, old_start + focus_size)
            selected_new_start, selected_new_end = new_start, new_end
        elif tag == "equal":
            selected_new_start = max(new_start, focus_start_index)
            selected_new_end = min(new_end, focus_end_index)
            if selected_new_start >= selected_new_end:
                continue
            offset_start = selected_new_start - new_start
            offset_end = selected_new_end - new_start
            selected_old_start = old_start + offset_start
            selected_old_end = old_start + offset_end
        elif tag == "insert":
            if new_end <= focus_start_index or new_start >= focus_end_index:
                continue
            selected_old_start = selected_old_end = old_start
            selected_new_start = max(new_start, focus_start_index)
            selected_new_end = min(new_end, focus_end_index)
        else:
            if new_end <= focus_start_index or new_start >= focus_end_index:
                continue
            selected_new_start = max(new_start, focus_start_index)
            selected_new_end = min(new_end, focus_end_index)
            old_length = old_end - old_start
            new_length = max(1, new_end - new_start)
            selected_old_start = old_start + int(
                old_length * (selected_new_start - new_start) / new_length
            )
            selected_old_end = old_start + int(
                old_length * (selected_new_end - new_start) / new_length
            )
            if selected_old_end <= selected_old_start and old_length:
                selected_old_end = min(old_end, selected_old_start + 1)
        old = before_lines[selected_old_start:selected_old_end]
        new = after_lines[selected_new_start:selected_new_end]
        width = max(len(old), len(new))
        for index in range(width):
            old_text = old[index] if index < len(old) else None
            new_text = new[index] if index < len(new) else None
            old_number = (
                selected_old_start + index + 1
                if old_text is not None else None
            )
            new_number = (
                selected_new_start + index + 1
                if new_text is not None else None
            )
            if tag == "equal":
                old_state = new_state = "context"
            elif tag == "delete":
                old_state, new_state = "removed", "empty"
            elif tag == "insert":
                old_state, new_state = "empty", "added"
            else:
                old_state = "removed" if old_text is not None else "empty"
                new_state = "added" if new_text is not None else "empty"
            rows.append(
                "<tr>"
                + cell(old_number, old_text, "before", old_state)
                + cell(new_number, new_text, "after", new_state)
                + "</tr>"
            )
    if not rows:
        rows.append(
            '<tr><td colspan="4" class="diff-identical">'
            "No lines are present in this focused block.</td></tr>"
        )
    return (
        '<div class="diff-scroll"><table class="inline-diff-table">'
        '<thead><tr><th colspan="2">Task baseline</th>'
        '<th colspan="2">Working tree</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )

CODE_SUMMARY_TOKEN = re.compile(
    r"(?<![\w])(?:"
    r"(?:[A-Za-z_][A-Za-z0-9_]*\.)+[A-Za-z_][A-Za-z0-9_]*"
    r"|_[A-Za-z0-9_]+"
    r"|[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+"
    r"|[A-Z][A-Z0-9_]{2,}"
    r"|[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]+)+"
    r")(?=$|[^\w])"
)

def code_summary_html(value, explicit_names=()):
    """Escape summary prose and code-style identifiers within it."""
    text = str(value or "")
    explicit = {
        str(name)
        for name in explicit_names
        if re.fullmatch(
            r"\$?[A-Za-z_][A-Za-z0-9_.-]*",
            str(name or ""),
        )
    }
    matches = list(CODE_SUMMARY_TOKEN.finditer(text))
    for name in sorted(explicit, key=len, reverse=True):
        matches.extend(
            re.finditer(
                rf"(?<![\w.]){re.escape(name)}(?=$|[^\w.])",
                text,
            )
        )
    selected = []
    for match in sorted(matches, key=lambda item: (item.start(), -item.end())):
        if selected and match.start() < selected[-1].end():
            continue
        selected.append(match)
    parts = []
    position = 0
    for match in selected:
        parts.append(html.escape(text[position:match.start()]))
        parts.append(f"<code>{html.escape(match.group(0))}</code>")
        position = match.end()
    parts.append(html.escape(text[position:]))
    return "".join(parts)

def focused_code_names(relative, source, start, end):
    """Collect identifiers referenced by one focused source block."""
    names = set()
    suffix = Path(relative).suffix.lower()
    focused_lines = source.splitlines()[max(0, start - 1):end]
    focused = "\n".join(focused_lines)
    if suffix == ".py":
        try:
            tree = ast.parse(source)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                node_start = getattr(node, "lineno", None)
                node_end = getattr(node, "end_lineno", node_start)
                if (
                    node_start is not None
                    and node_end is not None
                    and (node_end < start or node_start > end)
                ):
                    continue
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, ast.arg):
                    names.add(node.arg)
                elif isinstance(node, ast.Name):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    parts = [node.attr]
                    value = node.value
                    while isinstance(value, ast.Attribute):
                        parts.append(value.attr)
                        value = value.value
                    if isinstance(value, ast.Name):
                        parts.append(value.id)
                        names.add(".".join(reversed(parts)))
                    names.add(node.attr)
    elif suffix in {".html", ".js"}:
        for match in re.finditer(
            r"\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
            focused,
        ):
            names.add(match.group(1))
        for match in re.finditer(
            r"\b(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)"
            r"\s*\(([^)]*)\)",
            focused,
        ):
            names.add(match.group(1))
            names.update(
                item.strip().split("=", 1)[0].strip()
                for item in match.group(2).split(",")
                if item.strip()
            )
        names.update(
            match.group(0)
            for match in re.finditer(
                r"\b[A-Za-z_$][A-Za-z0-9_$]*"
                r"(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+\b",
                focused,
            )
        )
        names.update(
            match.group(1)
            for match in re.finditer(
                r"\bid=[\"']([A-Za-z_$][A-Za-z0-9_$-]*)[\"']",
                focused,
            )
        )
    elif suffix in {".bicep", ".bicepparam"}:
        names.update(
            match.group(1)
            for match in re.finditer(
                r"^\s*(?:param|var|resource|module|output)\s+"
                r"([A-Za-z_][A-Za-z0-9_]*)",
                focused,
                re.MULTILINE,
            )
        )
    elif suffix == ".ps1":
        for match in re.finditer(r"\$([A-Za-z_][A-Za-z0-9_]*)", focused):
            names.add(match.group(1))
            names.add("$" + match.group(1))
    names.update(
        match.group(0)
        for match in CODE_SUMMARY_TOKEN.finditer(focused)
    )
    return sorted(name for name in names if name)

LEGACY = SimpleNamespace(
    read_current=read_current, read_head=read_head, snippet=snippet,
    status_stats=status_stats, step_change_details=step_change_details,
    inline_diff_html=inline_diff_html, focused_code_names=focused_code_names,
    code_summary_html=code_summary_html, changed_files=changed_files,
    MANUAL_CHANGE_DETAILS=MANUAL_CHANGE_DETAILS, REVIEW_BASE=None,
)


def requirement_catalog(document):
    result = {}
    order = []

    def visit(node, parent=None):
        if not isinstance(node, dict):
            return
        identifier = node.get("id")
        if identifier:
            result[identifier] = {
                **node,
                "parent": parent,
            }
            order.append(identifier)
        for child in node.get("breakdown") or []:
            visit(child, identifier)

    for requirement in document["REQUIREMENTS"]:
        visit(requirement)
    return result, order


def architecture_catalog(document):
    result = {}

    def visit(node, parent=None):
        for identifier, child in (node.get("features") or {}).items():
            result[str(identifier)] = {
                **child,
                "parent": parent,
            }
            if isinstance(child, dict):
                visit(child, str(identifier))

    visit(document)
    return result


def requirement_sort_key(identifier, catalog, natural_order):
    job = catalog.get(identifier.split(".", 1)[0]) or {}
    declared = job.get("review_order") or []
    if identifier in declared:
        return (0, declared.index(identifier), identifier)
    top = identifier.split(".", 1)[0]
    top_index = natural_order.index(top) if top in natural_order else 999
    return (1, top_index, natural_order.index(identifier) if identifier in natural_order else 999, identifier)


def locate_component(component, architecture):
    feature = architecture[component["component_id"]]
    current = LEGACY.read_current(component["relative"])
    baseline = LEGACY.read_head(component["relative"])
    after = component["source"]
    positions = component.get("covered_lines") or [1]
    start, end = min(positions), max(positions)
    before, _, _ = LEGACY.snippet(
        baseline, component["needle"], component["span"]
    )
    added, removed, state = LEGACY.status_stats(component["relative"])
    details = (feature.get("review") or {}).get("change_details") or LEGACY.step_change_details(
        component["relative"],
        component["needle"],
        start,
        end,
        component["title"],
        component["question"],
    )
    return {
        **component,
        "feature": feature,
        "start": start,
        "end": end,
        "before": before,
        "after": after,
        "baseline_has_selector": (
            not component["needle"]
            or component["needle"].lower() in baseline.lower()
        ),
        "added": added,
        "removed": removed,
        "state": state,
        "details": details,
        "inline_diff": LEGACY.inline_diff_html(
            baseline, current, focus_start=start, focus_end=end
        ),
        "code_names": LEGACY.focused_code_names(
            component["relative"], current, start, end
        ),
    }


def status_label(component):
    review_entry = component.get("review_entry") or {}
    if review_entry.get("status"):
        return review_entry["status"]
    feature = component["feature"]
    approval = feature.get("approval") or {}
    return approval.get("status") or FEATURE_STATUS.READY


def status_slug(status):
    return re.sub(r"[^a-z0-9]+", "-", status.lower()).strip("-")


def status_bucket(status):
    if status == FEATURE_STATUS.CODE_APPROVED:
        return "approved"
    if status == FEATURE_STATUS.REMOVED:
        return "removed"
    if status == FEATURE_STATUS.IN_PROGRESS:
        return "revise"
    return "pending"


def percentage(value, total):
    return round((100 * value / total), 1) if total else 0.0


def code(text):
    return f"<code>{html.escape(str(text))}</code>"


def dependency_tokens(component):
    """Collect stable public identifiers that can connect review components."""
    feature = component["feature"]
    selector = feature.get("selector") or {}
    sources = [
        selector.get("value") or "",
        component.get("needle") or "",
    ]
    tokens = []
    for source in sources:
        for token in re.findall(
            r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*",
            str(source),
        ):
            for candidate in (token, token.rsplit(".", 1)[-1]):
                lowered = candidate.lower()
                if (
                    len(candidate) >= 4
                    and lowered not in DEPENDENCY_STOPWORDS
                    and candidate not in tokens
                ):
                    tokens.append(candidate)
    return tokens[:8]


def focused_reliances(component):
    """Extract concrete calls, configuration keys, and named contracts."""
    text = component.get("after") or ""
    values = []
    patterns = (
        r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*\(",
        r"[\"']([A-Za-z_][A-Za-z0-9_.-]{2,})[\"']\s*:",
        r"\b([A-Z][A-Z0-9_]{3,})\b",
        r"--([a-z][a-z0-9-]{2,})",
    )
    own = {value.lower() for value in dependency_tokens(component)}
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = match.group(1)
            leaf = value.rsplit(".", 1)[-1]
            if (
                leaf.lower() in DEPENDENCY_STOPWORDS
                or value.lower() in own
                or value.startswith(("assert", "raise"))
            ):
                continue
            if value not in values:
                values.append(value)
    return values[:12]


def attach_dependencies(components):
    """Attach evidence-backed caller/callee relationships to each step."""
    owners = defaultdict(list)
    for component in components:
        component["dependency_tokens"] = dependency_tokens(component)
        for token in component["dependency_tokens"]:
            owners[token.lower()].append(component)

    for component in components:
        outbound = []
        text = component.get("after") or ""
        source_path = component["relative"].replace("\\", "/")
        source_suffix = Path(source_path).suffix.lower()
        source_is_runtime = (
            source_suffix in {".py", ".html", ".js", ".ps1", ".bicep"}
            and not source_path.startswith(
                ("build/requirements/", "build/architecture/", "build/updates/")
            )
        )
        if not source_is_runtime:
            component["dependencies"] = {
                "inbound": [],
                "outbound": [],
                "reliances": focused_reliances(component),
            }
            continue
        for token, targets in owners.items():
            if not re.search(
                rf"(?<![A-Za-z0-9_$]){re.escape(token)}"
                rf"(?![A-Za-z0-9_$])",
                text,
                re.IGNORECASE,
            ):
                continue
            for target in targets:
                if target["component_id"] == component["component_id"]:
                    continue
                edge = {
                    "component_id": target["component_id"],
                    "title": target["title"],
                    "token": token,
                    "relative": target["relative"],
                }
                if edge not in outbound:
                    outbound.append(edge)
        component["dependencies"] = {
            "inbound": [],
            "outbound": outbound[:10],
            "reliances": focused_reliances(component),
        }

    by_id = {item["component_id"]: item for item in components}
    for source in components:
        for edge in source["dependencies"]["outbound"]:
            target = by_id[edge["component_id"]]
            inbound = {
                "component_id": source["component_id"],
                "title": source["title"],
                "token": edge["token"],
                "relative": source["relative"],
            }
            if inbound not in target["dependencies"]["inbound"]:
                target["dependencies"]["inbound"].append(inbound)
    for component in components:
        component["dependencies"]["inbound"] = (
            component["dependencies"]["inbound"][:10]
        )
    for component in components:
        declared = (component["feature"].get("review") or {}).get("dependencies")
        if isinstance(declared, dict):
            component["dependencies"] = {
                field: declared.get(field, component["dependencies"][field])
                for field in ("inbound", "outbound", "reliances")
            }


def _natural_name(value):
    value = str(value or "").strip("_$")
    value = value.rsplit(".", 1)[-1]
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    return value.replace("_", " ").replace("-", " ").strip().lower()


def _gerund_phrase(value):
    value = _natural_name(value)
    if not value:
        return value
    first, *rest = value.split()
    irregular = {
        "begin": "beginning",
        "build": "building",
        "close": "closing",
        "create": "creating",
        "delete": "deleting",
        "emit": "emitting",
        "get": "getting",
        "invoke": "invoking",
        "make": "making",
        "parse": "parsing",
        "run": "running",
        "set": "setting",
        "split": "splitting",
        "write": "writing",
    }
    if first in irregular:
        first = irregular[first]
    elif first.endswith("e") and not first.endswith("ee"):
        first = first[:-1] + "ing"
    elif not first.endswith("ing"):
        first += "ing"
    return " ".join([first, *rest])


def _infinitive_goal(value):
    replacements = {
        "ensures": "ensure",
        "defines": "define",
        "enforces": "enforce",
        "controls": "control",
        "provides": "provide",
        "makes": "make",
        "governs": "govern",
        "supports": "support",
        "records": "record",
        "keeps": "keep",
    }
    pattern = r"\b(" + "|".join(replacements) + r")\b"
    return re.sub(
        pattern,
        lambda match: replacements[match.group(1).lower()],
        value,
        flags=re.IGNORECASE,
    )


def _unique(values, limit=8):
    result = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in result:
            result.append(value)
    return result[:limit]


def _behavior_goal(component):
    """Turn review metadata into a declarative capability statement."""
    description = str(component["feature"].get("description") or "").strip()
    question = str(component.get("question") or "").strip().rstrip(".")
    transforms = (
        (r"^confirm\s+", "ensures "),
        (r"^check that\s+", "ensures that "),
        (r"^check\s+", "ensures "),
        (r"^verify\s+", "ensures "),
        (r"^review how\s+", "controls how "),
        (r"^review\s+", "defines and enforces "),
    )
    for pattern, replacement in transforms:
        if re.match(pattern, question, re.IGNORECASE):
            return re.sub(
                pattern, replacement, question,
                count=1, flags=re.IGNORECASE,
            )
    if question:
        return question[0].lower() + question[1:]
    if description.lower().startswith("coverage:"):
        description = ""
    sentences = [
        value.strip()
        for value in re.split(r"(?<=[.!?])\s+", description)
        if value.strip()
    ]
    declarative = [
        value for value in sentences
        if not re.match(
            r"^(review|confirm|check|verify)\b",
            value,
            re.IGNORECASE,
        )
    ]
    if declarative:
        goal = " ".join(declarative)
        return goal[0].lower() + goal[1:] if goal else goal
    return question or (
        f"provides the {_natural_name(component['title'])} capability"
    )


def _python_behavior(source):
    focused = textwrap.dedent(str(source or ""))
    inputs = []
    header = re.search(
        r"\bdef\s+[A-Za-z_][A-Za-z0-9_]*\s*\((.*?)\)\s*(?:->.*?)?:",
        focused,
        re.DOTALL,
    )
    if header:
        for argument in header.group(1).split(","):
            name = argument.strip().split(":", 1)[0].split("=", 1)[0].strip()
            if name and name not in {"self", "cls", "*", "/"}:
                inputs.append(_natural_name(name.lstrip("*")))
    inputs.extend(
        _natural_name(value)
        for value in re.findall(
            r"(?:\.get|os\.environ\.get|os\.getenv)\(\s*[\"']"
            r"([A-Za-z0-9_.-]+)",
            focused,
        )
    )
    inputs.extend(
        _natural_name(value)
        for value in re.findall(
            r"\[\s*[\"']([A-Za-z0-9_.-]+)[\"']\s*\]",
            focused,
        )
    )
    calls = re.findall(
        r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*\(",
        focused,
    )
    ignored = {
        "bool", "dict", "enumerate", "float", "getattr", "int", "isinstance",
        "len", "list", "max", "min", "print", "range", "set", "sorted",
        "str", "sum", "super", "tuple", "type", "zip",
    }
    actions = []
    effects = []
    for call in calls:
        leaf = call.rsplit(".", 1)[-1]
        natural = _natural_name(leaf)
        if leaf in ignored or natural.startswith(("test ", "assert ")):
            continue
        if re.match(
            r"^(write|save|store|upload|sync|persist|delete|remove|update|"
            r"report|log|emit|publish|close|set)",
            natural,
        ):
            effects.append(_gerund_phrase(natural))
        elif re.match(
            r"^(read|load|download|resolve|validate|filter|split|partition|"
            r"build|create|prepare|materialize|aggregate|merge|collect|"
            r"calculate|compute|acquire|begin|parse|normalize|reconcile|"
            r"wait|run|process|execute|dispatch|submit|poll)",
            natural,
        ):
            actions.append(_gerund_phrase(natural))
    returns = []
    for value in re.findall(r"\breturn\s+([^\n]+)", focused):
        value = re.sub(r"\s+", " ", value).strip()
        if value and value != "None":
            names = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", value)
            returns.extend(
                _natural_name(name)
                for name in names
                if name not in ignored and name not in {"self", "True", "False"}
            )
    state = [
        _natural_name(value)
        for value in re.findall(
            r"\bself\.([A-Za-z_][A-Za-z0-9_]*)\s*=",
            focused,
        )
    ]
    return {
        "style": "runtime",
        "inputs": _unique(inputs, 8),
        "actions": _unique(actions, 7),
        "effects": _unique(effects, 6),
        "outputs": _unique(returns + state, 7),
    }


def _find_json_key(value, target):
    if isinstance(value, dict):
        if target in value:
            return {target: value[target]}
        for child in value.values():
            found = _find_json_key(child, target)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_json_key(child, target)
            if found is not None:
                return found
    return None


def _json_behavior(component, baseline):
    source = (
        LEGACY.read_head(component["relative"])
        if baseline else
        LEGACY.read_current(component["relative"])
    )
    selector = (
        (component["feature"].get("selector") or {}).get("value") or ""
    ).strip("\"'")
    try:
        document = json.loads(source)
    except (TypeError, ValueError):
        document = None
    focused = _find_json_key(document, selector) if document is not None else None
    if focused is None:
        focused = {}
    keys = []
    if isinstance(focused, dict):
        keys.extend(focused.keys())
        for value in focused.values():
            if isinstance(value, dict):
                keys.extend(value.keys())
    return {
        "style": "configuration",
        "inputs": _unique(_natural_name(value) for value in keys[:10]),
        "actions": ["declaring configuration values and defaults"],
        "effects": [],
        "outputs": ["configuration consumed by runtime components"],
    }


def _declarative_behavior(source, suffix):
    focused = str(source or "")
    if suffix in {".json", ".jsonc"}:
        keys = re.findall(
            r"[\"']([A-Za-z_][A-Za-z0-9_.-]*)[\"']\s*:",
            focused,
        )
        return {
            "style": "configuration",
            "inputs": _unique(_natural_name(value) for value in keys[:8]),
            "actions": ["declaring configuration values and defaults"],
            "effects": [],
            "outputs": ["configuration consumed by runtime components"],
        }
    if suffix in {".bicep", ".bicepparam"}:
        params = re.findall(
            r"^\s*param\s+([A-Za-z_][A-Za-z0-9_]*)",
            focused,
            re.MULTILINE,
        )
        resources = re.findall(
            r"^\s*(?:resource|module)\s+([A-Za-z_][A-Za-z0-9_]*)",
            focused,
            re.MULTILINE,
        )
        outputs = re.findall(
            r"^\s*output\s+([A-Za-z_][A-Za-z0-9_]*)",
            focused,
            re.MULTILINE,
        )
        return {
            "style": "infrastructure",
            "inputs": _unique(_natural_name(value) for value in params),
            "actions": _unique(
                f"provisioning {_natural_name(value)}" for value in resources
            ),
            "effects": [],
            "outputs": _unique(_natural_name(value) for value in outputs),
        }
    if suffix == ".ps1":
        params = re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", focused)
        commands = re.findall(
            r"(?im)(?:^|[;&|]\s*|&\s+)(az|docker|kubectl|terraform|"
            r"Connect-[A-Za-z0-9-]+|"
            r"New-[A-Za-z0-9-]+|Set-[A-Za-z0-9-]+|Get-[A-Za-z0-9-]+)",
            focused,
        )
        return {
            "style": "command",
            "inputs": _unique(_natural_name(value) for value in params),
            "actions": _unique(
                f"invoking {_natural_name(value)}" for value in commands
            ),
            "effects": ["publishes command results and failures"],
            "outputs": [],
        }
    if suffix in {".html", ".js"}:
        controls = re.findall(
            r"(?:id|data-[A-Za-z0-9-]+)=[\"']([^\"']+)",
            focused,
        )
        events = re.findall(
            r"(?:addEventListener\(\s*[\"']|on)(click|change|submit|input)",
            focused,
            re.IGNORECASE,
        )
        return {
            "style": "interface",
            "inputs": _unique(
                f"{_natural_name(value)} control" for value in controls
            ),
            "actions": _unique(
                f"handles {_natural_name(value)} events" for value in events
            ),
            "effects": ["updates the visible review or job state"],
            "outputs": ["rendered user feedback"],
        }
    return {
        "style": "declarative",
        "inputs": [],
        "actions": ["applies the artifact's declared contract"],
        "effects": [],
        "outputs": [],
    }


def _behavior_model(component, source, baseline=False):
    suffix = Path(component["relative"]).suffix.lower()
    if suffix == ".py":
        return _python_behavior(source)
    if suffix in {".json", ".jsonc"}:
        return _json_behavior(component, baseline)
    return _declarative_behavior(source, suffix)


def _join_natural(values):
    values = list(values)
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + ", and " + values[-1]


def _describe_behavior(model, dependencies):
    style = model.get("style")
    if style == "configuration":
        settings = _join_natural(model["inputs"][:6])
        consumers = _join_natural(
            value["title"]
            for value in (dependencies.get("inbound") or [])[:3]
            if Path(value["relative"]).suffix.lower()
            in {".py", ".html", ".js", ".ps1"}
        )
        return (
            (
                f"The JSON object declares {settings}. "
                if settings else
                "The JSON object declares the reviewed settings. "
            )
            + (
                f"{consumers} load those values as their shared runtime "
                "contract, so defaults and supported choices remain "
                "configuration-owned rather than duplicated in code."
                if consumers else
                "Runtime readers load those values as a shared contract, so "
                "defaults and supported choices remain configuration-owned "
                "rather than duplicated in code."
            )
        )
    if style == "infrastructure":
        inputs = _join_natural(model["inputs"][:6])
        actions = _join_natural(model["actions"][:5])
        outputs = _join_natural(model["outputs"][:5])
        return (
            (f"The deployment accepts {inputs}. " if inputs else "")
            + (
                f"It uses those values while {actions}. "
                if actions else
                "It passes those values into the surrounding infrastructure "
                "modules. "
            )
            + (
                f"It exposes {outputs} to deployment callers."
                if outputs else
                "The resulting resource identifiers and endpoints are passed "
                "to deployment callers and runtime configuration."
            )
        )
    if style == "command":
        inputs = _join_natural(model["inputs"][:6])
        actions = _join_natural(model["actions"][:5])
        return (
            (f"The script accepts {inputs}. " if inputs else "")
            + (
                f"It validates and transforms those values while {actions}. "
                if actions else
                "It validates the requested operation and invokes the "
                "required platform tools. "
            )
            + "Command failures stop the operation; success leaves the "
            "published image or deployed resource available to its caller."
        )
    if style == "interface":
        inputs = _join_natural(model["inputs"][:5])
        actions = _join_natural(model["actions"][:5])
        return (
            (f"The interface receives user input through {inputs}. " if inputs else "")
            + (
                f"It reacts by {actions}, updates the underlying review state, "
                "and renders the result back to the user."
                if actions else
                "It binds the controls to the underlying review state and "
                "renders saved results back to the user."
            )
        )
    sentences = []
    if model["inputs"]:
        sentences.append(
            "It receives "
            + _join_natural(model["inputs"][:5])
            + " as runtime or configuration input."
        )
    if model["actions"]:
        sentences.append(
            "It processes that input by "
            + _join_natural(model["actions"][:5])
            + "."
        )
    delivered = _unique(model["effects"] + model["outputs"], 6)
    if delivered:
        sentences.append(
            "It delivers the result by "
            + _join_natural(delivered[:5])
            + "."
        )
    inbound = dependencies.get("inbound") or []
    outbound = dependencies.get("outbound") or []
    if inbound or outbound:
        integration = []
        if inbound:
            integration.append(
                "is invoked or consumed by "
                + _join_natural(value["title"] for value in inbound[:3])
            )
        if outbound:
            integration.append(
                "delegates part of the flow to "
                + _join_natural(value["title"] for value in outbound[:3])
            )
        sentences.append(
            "In the larger flow, it " + " and ".join(integration) + "."
        )
    return " ".join(sentences)


def behavioral_change_details(component):
    """Describe capability changes instead of reproducing changed syntax."""
    manual_key = (component["relative"], component["needle"])
    if (component["feature"].get("review") or {}).get("change_details"):
        return component["details"]
    if manual_key in LEGACY.MANUAL_CHANGE_DETAILS:
        return component["details"]
    goal = _behavior_goal(component).rstrip(".")
    current = _behavior_model(component, component["after"], baseline=False)
    baseline = _behavior_model(component, component["before"], baseline=True)
    selector = (
        (component["feature"].get("selector") or {}).get("value")
        or component["title"]
    )
    if str(selector).strip("\"'").lower() in {
        "", "file", "param", "resource", "module", "container",
    }:
        selector = component["title"]
    infinitive_goal = _infinitive_goal(goal)
    if not component["baseline_has_selector"]:
        before = (
            f"This capability did not exist in the task baseline. "
            f"There was no `{selector}` behavior to {infinitive_goal}."
        )
    else:
        baseline_description = _describe_behavior(
            baseline, {"inbound": [], "outbound": []}
        )
        missing_actions = [
            value for value in current["actions"]
            if value not in baseline["actions"]
        ]
        before = (
            f"`{selector}` already existed, but its responsibility was limited. "
            + (
                baseline_description
                if baseline_description
                else "It exposed the earlier local or single-path contract."
            )
            + (
                " It did not yet support "
                + _join_natural(missing_actions[:4])
                + "."
                if missing_actions
                else f" It did not yet {infinitive_goal}."
            )
        )
    now = f"`{selector}` now {goal}."
    style = current.get("style")
    if style == "configuration":
        now += (
            " Downstream components receive one authoritative set of defaults, "
            "supported values, and routing metadata."
        )
    elif style == "infrastructure":
        now += (
            " A deployment can turn those inputs into the required Azure "
            "resources and return the identifiers needed by later stages."
        )
    elif style == "command":
        now += (
            " The caller receives either a completed publication/deployment "
            "or an explicit command failure."
        )
    elif style == "interface":
        now += (
            " The reviewer can see and change the corresponding state through "
            "the rendered interface."
        )
    elif current["effects"] or current["outputs"]:
        now += (
            " Its output is "
            + _join_natural(
                _unique(current["effects"] + current["outputs"], 5)
            )
            + "."
        )
    how = _describe_behavior(current, component["dependencies"])
    if not how:
        how = (
            "The implementation applies the selector's configuration contract "
            "at the artifact boundary and passes the resulting state to its "
            "documented consumers."
        )
    return [{
        "name": selector,
        "before": before,
        "now": now,
        "how": how,
    }]


def attach_behavior_details(components):
    for component in components:
        component["details"] = behavioral_change_details(component)


def render_behavior_steps(value, code_names):
    clauses = []
    for sentence in re.split(r"(?<=[.!?])\s+", str(value or "").strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        parts = [
            part.strip()
            for part in re.split(r";\s+(?=[A-Z`])", sentence)
            if part.strip()
        ]
        clauses.extend(parts)
    if not clauses:
        clauses = ["No behavioral explanation was provided."]
    return (
        '<ol class="behavior-steps">'
        + "".join(
            "<li>"
            + LEGACY.code_summary_html(clause, code_names)
            + "</li>"
            for clause in clauses
        )
        + "</ol>"
    )


def render_change_details(details, code_names, scope):
    return (
        f'<section class="change-vector {scope}-change-vector" '
        f'data-change-vector="{scope}">'
        "<h4>Change vector</h4>"
        + "".join(
            "<article><h5>"
            + code(detail["name"])
            + "</h5><dl><dt>Before</dt><dd>"
            + render_behavior_steps(detail["before"], code_names)
            + "</dd><dt>Now</dt><dd>"
            + render_behavior_steps(detail["now"], code_names)
            + "</dd><dt>How</dt><dd>"
            + render_behavior_steps(detail["how"], code_names)
            + "</dd></dl></article>"
            for detail in details
        )
        + "</section>"
    )


def render_dependencies(dependencies, scope):
    def edges(values, direction):
        if not values:
            if direction == "inbound":
                return (
                    "No other focused review component directly references "
                    "this selector; it is an entry point, leaf behavior, or "
                    "externally consumed contract."
                )
            return (
                "No mapped review component is directly referenced from this "
                "focused block."
            )
        verb = "uses" if direction == "inbound" else "via"
        return "<ul>" + "".join(
            "<li>"
            + code(value["component_id"])
            + " "
            + html.escape(value["title"])
            + f" {verb} "
            + code(value["token"])
            + " in "
            + code(value["relative"])
            + "</li>"
            for value in values
        ) + "</ul>"

    reliance_values = dependencies.get("reliances") or []
    reliance = (
        ", ".join(code(value) for value in reliance_values)
        if reliance_values
        else (
            "No additional call, configuration-key, command-option, or "
            "constant dependency was detected in the focused block."
        )
    )
    return (
        f'<section class="dependency-map {scope}-dependency-map" '
        f'data-dependency-map="{scope}">'
        "<h4>Dependencies and impact</h4><dl>"
        "<dt>Called / consumed by</dt><dd>"
        + edges(dependencies.get("inbound") or [], "inbound")
        + "</dd><dt>Calls / consumes</dt><dd>"
        + edges(dependencies.get("outbound") or [], "outbound")
        + "</dd><dt>Other relied-on contracts</dt><dd>"
        + reliance
        + "</dd></dl></section>"
    )


def thread_change_details(requirement, components):
    missing = [
        component for component in components
        if component["details"][0]["before"].startswith(
            "This capability did not exist"
        )
    ]
    existing = [
        component for component in components
        if component not in missing
    ]
    edges = []
    for component in components:
        for edge in component["dependencies"]["outbound"]:
            value = f"{component['title']} hands work to {edge['title']}"
            if value not in edges:
                edges.append(value)
    requirement_goal = str(requirement.get("description") or "").strip()
    existing_roles = _join_natural(
        component["title"] for component in existing[:4]
    )
    missing_roles = _join_natural(
        component["title"] for component in missing[:4]
    )
    if existing and missing:
        before = (
            f"The baseline already handled {existing_roles}, but it had no "
            f"task-owned behavior for {missing_roles}. Those partial pieces "
            "did not form the complete requirement flow."
        )
    elif missing:
        before = (
            f"The baseline had no integrated implementation for this thread. "
            f"The capabilities represented by {missing_roles} were absent."
        )
    else:
        before = (
            f"The baseline contained the individual responsibilities for "
            f"{existing_roles}, but each followed its earlier, narrower "
            "contract and did not provide the revised end-to-end behavior."
        )
    now = (
        f"The thread now satisfies the requirement as one connected behavior: "
        f"{requirement_goal} "
        f"The visible responsibilities are owned by "
        + _join_natural(component["title"] for component in components)
        + "."
    )
    if edges:
        how = (
            "The thread is assembled as a data and control flow rather than "
            "as isolated edits: "
            + "; ".join(edges[:6])
            + ". Each step consumes the preceding contract, performs its "
            "specific validation or transformation, and exposes durable state "
            "for the next consumer or for the reviewer-visible result."
        )
    else:
        how = (
            "The steps implement separate sides of the same contract. "
            "Configuration and metadata define the inputs, runtime components "
            "apply them, and the resulting files, status, or user-visible "
            "state provide the output at the requirement boundary."
        )
    return [{
        "name": requirement.get("id") or components[0]["requirement_id"],
        "before": before,
        "now": now,
        "how": how,
    }]


def thread_dependencies(components):
    component_ids = {item["component_id"] for item in components}
    inbound = []
    outbound = []
    reliances = []
    for component in components:
        for edge in component["dependencies"]["inbound"]:
            if edge["component_id"] not in component_ids and edge not in inbound:
                inbound.append(edge)
        for edge in component["dependencies"]["outbound"]:
            if edge["component_id"] not in component_ids and edge not in outbound:
                outbound.append(edge)
        for value in component["dependencies"]["reliances"]:
            if value not in reliances:
                reliances.append(value)
    return {
        "inbound": inbound[:12],
        "outbound": outbound[:12],
        "reliances": reliances[:16],
    }


def _letter_label(number):
    result = ""
    while number:
        number, remainder = divmod(int(number) - 1, 26)
        result = chr(97 + remainder) + result
    return result or "a"


def source_provenance(source):
    if not isinstance(source, dict):
        return "Repository-owned capability."
    parts = []
    for key in ("task", "requirement", "feature", "features", "adaptation"):
        value = source.get(key)
        if value:
            text = ", ".join(str(item) for item in value) if isinstance(value, list) else str(value)
            parts.append(f"<strong>{html.escape(key.title())}</strong> {html.escape(text)}")
    return " · ".join(parts) or "Repository-owned capability."


def _browser_ledger(ledger):
    ledger = with_code_approval_eligibility(ledger)
    fields = ("feature_id", "status", "spec_status", "artifact_ready", "revision_request", "comments", "requirements",
              "can_approve_code")
    return {
        "task_id": ledger["task_id"], "revision": ledger["revision"],
        "features": {identifier: {key: entry.get(key) for key in fields} for identifier, entry in ledger.get("features", {}).items()},
        "spec_reviews": {identifier: {"status": entry.get("status"), "comments": entry.get("comments", []),
                                    "can_approve_code": entry["can_approve_code"],
                                    "code_feature_ids": entry["code_feature_ids"]}
                         for identifier, entry in ledger.get("spec_reviews", {}).items()},
        "artifacts": ledger.get("artifacts", {}), "can_code": code_eligible(ledger), "can_spec": spec_eligible(ledger),
    }


def review_components(ledger, store):
    """Build the original renderer catalog from portable, discovered task links."""
    requirements_document = read_json(store.requirements_path) if store.requirements_path.is_file() else {"REQUIREMENTS": []}
    architecture_document = read_json(store.architecture_path) if store.architecture_path.is_file() else {}
    requirements, natural_order = requirement_catalog(requirements_document)
    architecture = architecture_catalog(architecture_document)
    components = []
    for requirement_id, entry in ledger.get("spec_reviews", {}).items():
        requirement = requirements.setdefault(requirement_id, {"id": requirement_id, "title": entry.get("title", "")})
        requirement["review"] = {**(requirement.get("review") or {}), "thread_number": entry["number"]}
    thread_ordinals = defaultdict(int)
    for identifier, entry in sorted(ledger.get("features", {}).items(), key=lambda pair: pair[1]["number"]):
        feature = architecture.setdefault(identifier, dict(entry))
        feature = dict(feature)
        requirement_id = entry.get("requirement_id") or ""
        if not requirement_id:
            continue
        thread = ledger.get("spec_reviews", {}).get(requirement_id) or {}
        thread_ordinals[requirement_id] += 1
        feature["review"] = {**(feature.get("review") or {}),
            "label": (feature.get("review") or {}).get("label") or f"{thread.get('number', 0)}.{_letter_label(thread_ordinals[requirement_id])}",
            "item_order": entry["number"]}
        architecture[identifier] = feature
        selector = feature.get("selector") or {}
        if not isinstance(selector, dict):
            selector = {"value": selector}
        symbol = selector.get("value") or selector.get("symbol") or selector.get("name") or str(entry.get("artifact") or "").partition("::")[2]
        needle = str(symbol or "")
        if Path(str(entry.get("artifact") or "").split("::")[0]).suffix == ".py" and symbol:
            leaf = str(symbol).rsplit(".", 1)[-1]
            needle = ("class " if selector.get("kind") == "class" or leaf[:1].isupper() else "def ") + leaf
        components.append({**entry, "component_id": identifier, "relative": str(entry.get("artifact") or "").split("::")[0],
            "requirement_id": requirement_id, "review_id": f"{thread.get('number', 0)}.{entry['number']}",
            "needle": needle, "span": max(12, len(entry.get("covered_lines") or [])),
            "question": (feature.get("review") or {}).get("question") or feature.get("description") or entry.get("title", "")})
    return components, architecture, requirements, natural_order


def _render_document(ledger, template, store):
    components, architecture, requirements, natural_order = review_components(ledger, store)

    missing_features = [
        item["component_id"] for item in components
        if item["component_id"] not in architecture
    ]
    missing_requirements = [
        item["requirement_id"] for item in components
        if item["requirement_id"] not in requirements
    ]
    if missing_features or missing_requirements:
        raise RuntimeError(
            f"Traceability gaps: features={missing_features}, "
            f"requirements={missing_requirements}"
        )

    rendered = [
        locate_component(component, architecture)
        for component in components
    ]
    attach_dependencies(rendered)
    attach_behavior_details(rendered)
    review_ledger = ledger
    review_features = review_ledger.get("features") or {}
    spec_reviews = review_ledger.get("spec_reviews") or {}
    for component in rendered:
        component["review_entry"] = review_features.get(
            component["component_id"], {}
        )
    task_status_counts = {
        "approved": 0, "removed": 0, "revise": 0, "pending": 0
    }
    for component in rendered:
        task_status_counts[status_bucket(status_label(component))] += 1
    grouped = defaultdict(list)
    for requirement_id in ledger.get("spec_reviews", {}):
        grouped[requirement_id] = []
    artifacts = defaultdict(list)
    for component in rendered:
        grouped[component["requirement_id"]].append(component)
        component_artifacts = [
            component["relative"],
            *(component.get("additional_artifacts") or []),
        ]
        for relative in dict.fromkeys(component_artifacts):
            artifacts[relative].append(component)

    requirement_ids = sorted(
        grouped,
        key=lambda value: (
            (requirements[value].get("review") or {}).get(
                "thread_number", 10**9
            ),
            requirement_sort_key(value, requirements, natural_order),
        ),
    )
    changed_files = LEGACY.changed_files()
    missing_files = sorted(set(changed_files) - set(artifacts))
    # Work-in-progress coverage is displayed, not a bootstrap failure; the shared validator enforces it.

    navigation = []
    thread_html = []
    for requirement_id in requirement_ids:
        requirement = requirements[requirement_id]
        thread_number = (requirement.get("review") or {}).get(
            "thread_number"
        )
        if not thread_number:
            raise RuntimeError(
                f"Requirement review number missing: {requirement_id}"
            )
        thread_components = sorted(
            grouped[requirement_id],
            key=lambda item: (
                (item["feature"].get("review") or {}).get(
                    "item_order", 10**9
                ),
                tuple(
                    int(part) for part in item["review_id"].split(".")
                ),
            ),
        )
        approved_count = sum(
            status_label(item) == FEATURE_STATUS.CODE_APPROVED
            for item in thread_components
        )
        removed_count = sum(
            status_label(item) == FEATURE_STATUS.REMOVED
            for item in thread_components
        )
        resolved_count = approved_count + removed_count
        status = (
            FEATURE_STATUS.REMOVED
            if thread_components and removed_count == len(thread_components)
            else (
                FEATURE_STATUS.CODE_APPROVED
                if thread_components and resolved_count == len(thread_components)
                else FEATURE_STATUS.READY
            )
        )
        removed_suffix = (
            f" · {removed_count} removed" if removed_count else ""
        )
        navigation_state = (
            "removed"
            if status == FEATURE_STATUS.REMOVED
            else (
                "approved"
                if status == FEATURE_STATUS.CODE_APPROVED
                else "pending"
            )
        )
        navigation.append(
            f'<a class="thread-{navigation_state}" '
            f'data-thread-number="{thread_number}" '
            f'data-thread-steps="{len(thread_components)}" '
            f'data-thread-approval="{percentage(approved_count, len(thread_components))}" '
            f'href="#requirement-{html.escape(requirement_id)}">'
            f"<span>{thread_number}</span>"
            f"<div><strong>{html.escape(requirement_id)}</strong>"
            f"<small>{html.escape(requirement.get('title') or '')}</small>"
            f'<small class="thread-nav-metrics">'
            f'<em data-thread-step-count>{len(thread_components)} steps</em>'
            f'<em data-thread-approval-share>'
            f'{percentage(approved_count, len(thread_components)):g}% approved'
            f"{removed_suffix}"
            f"</em></small></div>"
            "</a>"
        )
        cards = []
        for component in thread_components:
            feature = component["feature"]
            component_label = (feature.get("review") or {}).get("label")
            if not component_label:
                raise RuntimeError(
                    "Architecture review label missing: "
                    + component["component_id"]
                )
            review_entry = component["review_entry"]
            approval_status = status_label(component)
            comments = review_entry.get("comments") or []
            approval_note = (
                comments[-1].get("text")
                if comments
                else (feature.get("approval") or {}).get("note") or ""
            )
            selector = feature.get("selector") or {}
            detail_html = render_change_details(
                component["details"], component["code_names"], "step"
            )
            dependency_html = render_dependencies(
                component["dependencies"], "step"
            )
            cards.append(
                f"""
                <article class="component" data-feature-id="{html.escape(component["component_id"], quote=True)}" data-artifact="{html.escape(component["relative"], quote=True)}">
                  <div class="component-index">{html.escape(component_label)}</div>
                  <div>
                    <div class="component-heading">
                      <h3>{html.escape(component["title"])}</h3>
                      <span class="approval {status_slug(approval_status)}" data-review-status>{html.escape(approval_status)}</span>
                    </div>
                    <div class="trace-row">
                      <span>Architecture</span>{code(component["component_id"])}
                      <strong>{html.escape(feature.get("name") or "")}</strong>
                    </div>
                    <p class="architecture-description">{html.escape(feature.get("description") or "")}</p>
                    <dl class="architecture-record">
                      <dt>Path</dt><dd>{code(feature.get("path") or "")}</dd>
                      <dt>Artifact</dt><dd>{code(feature.get("artifact") or component["relative"])}</dd>
                      <dt>Selector</dt><dd>{code(selector.get("kind") or "")} {code(selector.get("value") or "")}</dd>
                      <dt>Requirement</dt><dd>{code(requirement_id)} {html.escape(requirement.get("title") or "")}</dd>
                      <dt>Source provenance</dt><dd>{source_provenance(feature.get("source"))}</dd>
                      <dt>Legacy review</dt><dd>{code(component["review_id"])}</dd>
                      <dt>Approval note</dt><dd>{html.escape(approval_note) if approval_note else "Not approved yet."}</dd>
                    </dl>
                    <div class="path">Focused lines {component["start"]}-{component["end"]} · file state {html.escape(component["state"])} · +{component["added"]} -{component["removed"]}</div>
                    <p>{LEGACY.code_summary_html(component["question"], component["code_names"])}</p>
                    {detail_html}
                    {dependency_html}
                    <details class="inline-diff">
                      <summary>Show embedded diff <span>+{component["added"]} -{component["removed"]}</span></summary>
                      {component["inline_diff"]}
                    </details>
                    <section class="review-controls">
                      <label>Comments
                        <textarea data-review-comment rows="3" placeholder="Add a feature-specific review comment"></textarea>
                      </label>
                      <div class="review-actions">
                        <button type="button" class="approve-button" data-review-action="approve_spec">Approve Spec</button>
                        <button type="button" class="approve-button" data-review-action="approve" {'' if feature_code_approvable(review_entry) else 'disabled'}>Approve Code</button>
                        <button type="button" class="revise-button" data-review-action="revise">Revise</button>
                        <span class="review-message" data-review-message></span>
                      </div>
                      <div class="comment-history" data-review-comments>
                        {''.join(
                            '<article><strong>'
                            + html.escape(str(item.get("action") or "comment").title())
                            + '</strong><span>'
                            + html.escape(str(item.get("actor_name") or item.get("actor") or "Reviewer"))
                            + ' · '
                            + html.escape(str(item.get("created_at") or ""))
                            + '</span><p>'
                            + html.escape(str(item.get("text") or ""))
                            + '</p></article>'
                            for item in comments
                        ) or '<p class="no-comments">No review comments yet.</p>'}
                      </div>
                    </section>
                  </div>
                </article>
                """
            )
        thread_detail_html = render_change_details(
            thread_change_details(requirement, thread_components),
            [
                value
                for component in thread_components
                for value in component["code_names"]
            ],
            "thread",
        )
        thread_dependency_html = render_dependencies(
            thread_dependencies(thread_components), "thread"
        )
        spec_review = spec_reviews.get(requirement_id) or {}
        spec_status = (
            spec_review.get("status") or FEATURE_STATUS.SPECIFIED
        )
        spec_approved = spec_status == FEATURE_STATUS.SPEC_APPROVED
        thread_html.append(
            f"""
            <section class="thread" id="requirement-{html.escape(requirement_id)}" data-requirement-id="{html.escape(requirement_id, quote=True)}" data-feature-ids="{html.escape(json.dumps([item["component_id"] for item in thread_components]), quote=True)}">
              <header>
                <div>
                  <div class="eyebrow">Requirement thread {thread_number}</div>
                  <h2>{code(requirement_id)} {html.escape(requirement.get("title") or "")}</h2>
                  <p>{html.escape(requirement.get("description") or "")}</p>
                  <p>Source provenance: {source_provenance(requirement.get("source"))}</p>
                </div>
                <div class="thread-actions">
                  <span class="spec-status {'approved' if spec_approved else 'pending'}" data-spec-status>{html.escape('Spec Approved' if spec_approved else 'Spec Pending')}</span>
                  <button type="button" data-spec-action="approve" {'disabled' if spec_approved else ''}>Approve Spec</button>
                  <button type="button" data-code-action="approve" {'' if requirement_code_approvable(ledger, requirement_id) else 'disabled'} title="Approve all remaining features in this requirement thread, including hidden features.">Approve Code</button>
                  <span class="thread-status">{approved_count}/{len(thread_components)} code approved{f" · {removed_count} removed" if removed_count else ""}</span>
                  <span class="review-message" data-spec-message></span>
                  <span class="review-message" data-code-message></span>
                </div>
              </header>
              <div class="sequence-note">Review the architecture components below in order. Each approval is independent, including separate change points in the same artifact.</div>
              {thread_detail_html}
              {thread_dependency_html}
              {''.join(cards)}
            </section>
            """
        )

    artifact_rows = []
    ready_artifacts = 0
    for relative in sorted(artifacts):
        values = artifacts[relative]
        approved = [
            value for value in values
            if status_label(value) == FEATURE_STATUS.CODE_APPROVED
        ]
        removed = [
            value for value in values
            if status_label(value) == FEATURE_STATUS.REMOVED
        ]
        removed_artifact = len(removed) == len(values)
        ready = not removed_artifact and bool((ledger.get("artifacts", {}).get(relative) or {}).get("ready_to_commit"))
        ready_artifacts += int(ready)
        pending_ids = [
            value["component_id"] for value in values
            if status_label(value) not in FEATURE_STATUS.FINAL
        ]
        commit_state = (
            "Removed from task"
            if removed_artifact
            else ("Ready to commit" if ready else "Blocked")
        )
        commit_class = (
            "removed"
            if removed_artifact
            else ("ready" if ready else "blocked")
        )
        artifact_rows.append(
            f"<tr data-artifact='{html.escape(relative, quote=True)}' data-feature-ids='{html.escape(json.dumps([value['component_id'] for value in values]), quote=True)}'>"
            f"<td>{code(relative)}</td>"
            f"<td>{len(values)}</td>"
            f"<td data-approved-count>{len(approved)}</td>"
            f"<td><span data-commit-status class='commit {commit_class}'>"
            f"{commit_state}"
            "</span></td>"
            f"<td data-pending-features>{'None' if ready else ' '.join(code(value) for value in pending_ids)}</td>"
            "</tr>"
        )

    data_components = [
        item for item in rendered
        if Path(item["relative"]).suffix.lower() in DATA_SUFFIXES
    ]
    generated = ledger.get("updated_at", "")
    total_added = 0
    total_removed = 0
    for relative in changed_files:
        added, removed, _ = LEGACY.status_stats(relative)
        total_added += int(added) if added.isdigit() else 0
        total_removed += int(removed) if removed.isdigit() else 0

    template_values = {
        'UPSTREAM_001': f'{TASK_ID}',
        'UPSTREAM_003': f'{TASK_ID}',
        'UPSTREAM_005': f"{percentage(task_status_counts['approved'] + task_status_counts['removed'], len(rendered)):g}",
        'UPSTREAM_007': f"{percentage(task_status_counts['approved'] + task_status_counts['removed'], len(rendered)):g}",
        'UPSTREAM_009': f"{task_status_counts['approved'] + task_status_counts['removed']}",
        'UPSTREAM_011': f'{len(rendered)}',
        'UPSTREAM_013': f'{generated}',
        'UPSTREAM_015': f'{html.escape(LEGACY.REVIEW_BASE)}',
        'UPSTREAM_017': f"""{''.join((f'<label><input type="checkbox" data-status-filter value="{status}" checked> {status}</label>' for status in FEATURE_STATUS.FILTER_OPTIONS))}""",
        'UPSTREAM_019': f"{''.join(navigation)}",
        'UPSTREAM_021': f'{html.escape(LEGACY.REVIEW_BASE)}',
        'UPSTREAM_023': f'{len(requirement_ids)}',
        'UPSTREAM_025': f'{len(rendered)}',
        'UPSTREAM_027': f'{len(artifacts)}',
        'UPSTREAM_029': f'{ready_artifacts}',
        'UPSTREAM_031': f'{total_added:,}',
        'UPSTREAM_033': f'{total_removed:,}',
        'UPSTREAM_035': f"{task_status_counts['approved']}",
        'UPSTREAM_037': f'{len(rendered)}',
        'UPSTREAM_039': f"{percentage(task_status_counts['approved'], len(rendered)):g}",
        'UPSTREAM_041': f"{task_status_counts['removed']}",
        'UPSTREAM_043': f'{len(rendered)}',
        'UPSTREAM_045': f"{percentage(task_status_counts['removed'], len(rendered)):g}",
        'UPSTREAM_047': f"{task_status_counts['revise']}",
        'UPSTREAM_049': f'{len(rendered)}',
        'UPSTREAM_051': f"{percentage(task_status_counts['revise'], len(rendered)):g}",
        'UPSTREAM_053': f"{task_status_counts['pending']}",
        'UPSTREAM_055': f'{len(rendered)}',
        'UPSTREAM_057': f"{percentage(task_status_counts['pending'], len(rendered)):g}",
        'UPSTREAM_059': f'{TASK_ID}',
        'UPSTREAM_061': f'{len(data_components)}',
        'UPSTREAM_063': f"{''.join(artifact_rows)}",
        'UPSTREAM_065': f"{''.join(thread_html)}",
        'UPSTREAM_067': f'{json.dumps(TASK_ID)}',
        'UPSTREAM_069': f'{json.dumps(FEATURE_STATUS.READY)}',
        'UPSTREAM_071': f'{json.dumps(FEATURE_STATUS.READY)}',
        'UPSTREAM_073': f'{json.dumps(FEATURE_STATUS.CODE_APPROVED)}',
        'UPSTREAM_075': f'{json.dumps(FEATURE_STATUS.REMOVED)}',
        'UPSTREAM_077': f'{json.dumps(FEATURE_STATUS.IN_PROGRESS)}',
        'UPSTREAM_079': f'{json.dumps(FEATURE_STATUS.READY)}',
        'UPSTREAM_081': f'{json.dumps(FEATURE_STATUS.READY)}',
        'UPSTREAM_083': f'{json.dumps(FEATURE_STATUS.READY)}',
        'UPSTREAM_085': f'{json.dumps(sorted(FEATURE_STATUS.SPEC_APPROVABLE))}',
        'UPSTREAM_089': f'{json.dumps(FEATURE_STATUS.SPEC_APPROVED)}',
        'UPSTREAM_091': f'{json.dumps(FEATURE_STATUS.CODE_APPROVED)}',
        'UPSTREAM_093': f'{json.dumps(FEATURE_STATUS.REMOVED)}',
        'UPSTREAM_095': f'{json.dumps(FEATURE_STATUS.SPEC_APPROVED)}',
        'UPSTREAM_097': f'{json.dumps(FEATURE_STATUS.REMOVED)}',
        'UPSTREAM_099': f'{json.dumps(sorted(FEATURE_STATUS.FINAL))}',
    }
    document = template
    for token, value in template_values.items():
        document = document.replace("{{" + token + "}}", value)

    return document.replace("{{REVIEW_LEDGER}}", json.dumps(_browser_ledger(ledger), ensure_ascii=False, sort_keys=True).replace("<", "\\u003c")).replace(
        "{{SNAPSHOT_HASH}}", fingerprint({"ledger": ledger, "template": template})).replace(
        "{{REVIEW_TITLE}}", html.escape(f"{TASK_ID} · Interactive engineering review")).replace(
        "{{ARCHITECTURE_PATH}}", html.escape(str(store.architecture_path.relative_to(store.root)) if store.architecture_path.is_relative_to(store.root) else store.architecture_path.name)).replace(
        "{{UNCOVERED_CHANGES}}", "".join("<li>" + html.escape(change["path"]) + ": lines " +
            html.escape(", ".join(str(line) for line in change["lines"][:30])) + "</li>" for change in ledger.get("uncovered_changes", [])) or "<li>None.</li>").replace(
        "{{EMPTY_COMPONENTS}}", "<p>No task-owned review components discovered yet. Approval remains disabled.</p>" if not components else "")


def render_report(ledger, template, mode="code", interval=3, store=None):
    if store is None:
        raise ValueError("Rendering the upstream before/after interface requires an explicit ReviewStore.")
    global ROOT, TASK_ID, REVIEW_BASE
    with _RENDER_LOCK:
        ROOT, TASK_ID = store.root, store.task_id
        REVIEW_BASE = run("git", "rev-parse", "--verify", store.baseline_ref).strip()
        LEGACY.REVIEW_BASE = REVIEW_BASE
        # Many precise components share a file. Reuse its baseline/current text,
        # status and parse results within this render only, never across edits.
        originals = {name: getattr(LEGACY, name) for name in ("read_current", "read_head", "status_stats")}
        for name, function in originals.items():
            setattr(LEGACY, name, lru_cache(maxsize=None)(function))
        try:
            return _render_document(ledger, template, store).replace("{{POLL_INTERVAL}}", str(int(interval * 1000)))
        finally:
            for name, function in originals.items():
                setattr(LEGACY, name, function)


def generate_reports(store, results_root, template_path=None, interval=3, ledger=None):
    template_path = Path(template_path or Path(__file__).resolve().parents[1] / "resources" / "templates" / "code_review.html")
    locked_snapshot = ledger is not None
    if ledger is None:
        with store.transaction():
            ledger = store._initialize()
    template = template_path.read_text(encoding="utf-8")
    rendered = render_report(ledger, template, "code", interval, store)
    if locked_snapshot:
        return _publish_reports(store, results_root, template_path, template, interval, ledger, rendered)
    # Both exclusive views use the original combined spec/code interface.
    # Expensive before/after generation never holds the decision/CAS lock.
    with store.transaction():
        current = store._initialize()
        if fingerprint(current) != fingerprint(ledger):
            raise RuntimeError("Review changed during rendering; the last valid report is retained.")
        return _publish_reports(store, results_root, template_path, template, interval, ledger, rendered)


def _publish_reports(store, results_root, template_path, template, interval, ledger, rendered):
    outputs = {}
    for mode in ("spec", "code"):
        target = report_path(results_root, store.task_id, mode)
        if not target.is_file() or target.read_text(encoding="utf-8") != rendered:
            atomic_write(target, rendered)
        outputs[mode] = {"path": str(target), "hash": file_hash(target)}
    metadata = {"task_id": store.task_id, "revision": ledger["revision"],
        "input_hash": fingerprint(ledger), "template_hash": file_hash(template_path),
        "snapshot_hash": fingerprint({"ledger": ledger, "template": template}),
        "interval": interval, "reports": outputs}
    atomic_json(store.review_path.with_name("reports.json"), metadata)
    return metadata


def main(argv=None):
    args = add_runtime_arguments(argparse.ArgumentParser(description=__doc__)).parse_args(argv)
    runtime = resolve_runtime(args)
    store = ReviewStore(runtime["root"], runtime["task_id"], runtime["architecture"],
                        runtime["requirements"], runtime["baseline_ref"], runtime["layer"])
    print(json.dumps(generate_reports(store, runtime["results_root"], runtime["template"],
                                     runtime["refresh_interval_seconds"]), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
