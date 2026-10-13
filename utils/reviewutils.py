"""Shared persistent review model, adapted from the APP-REQ-011 runtime."""
from __future__ import annotations

import argparse
import ast
import copy
import difflib
import getpass
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class FeatureStatus:
    IN_PROGRESS = "InProgress"
    SPECIFIED = "Specified"
    SPEC_APPROVED = "SpecApproved"
    READY = "Ready"
    REVISED = "Revised"
    CODE_APPROVED = "CodeApproved"
    REMOVED = "Removed"
    FILTER_OPTIONS = (IN_PROGRESS, SPECIFIED, SPEC_APPROVED, READY, REVISED, CODE_APPROVED, REMOVED)
    SPEC_APPROVABLE = frozenset({IN_PROGRESS, SPECIFIED})
    FINAL = frozenset({CODE_APPROVED, REMOVED})
    CODE_APPROVABLE = frozenset(FILTER_OPTIONS) - FINAL
    RANK = {IN_PROGRESS: 0, SPECIFIED: 1, SPEC_APPROVED: 2, READY: 3, REVISED: 3, CODE_APPROVED: 4}


FEATURE_STATUS_ALIASES = {
    "Pending": FeatureStatus.READY, "Revisit Requested": FeatureStatus.IN_PROGRESS,
    "Revisited": FeatureStatus.REVISED, "Approved": FeatureStatus.CODE_APPROVED,
}
FEATURE_STATUS_FILTER_OPTIONS = FeatureStatus.FILTER_OPTIONS
FEATURE_DISPLAY_MODE_OPTIONS = ("matching-features", "all-thread-features")
VALID_ACTIONS = {"approve", "approve_spec", "revise", "remove"}
VALID_REVISION_STATES = {"pending", "processing", "completed", "removed"}
SPEC_APPROVED_OR_LATER = {FeatureStatus.SPEC_APPROVED, FeatureStatus.READY, FeatureStatus.CODE_APPROVED,
                          "Deployed", "BuildApproved", "Done"}
CODE_APPROVED_OR_LATER = {FeatureStatus.CODE_APPROVED, "Deployed", "BuildApproved", "Done"}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def system_reviewer_identity():
    return (os.environ.get("USERNAME") or os.environ.get("USER") or getpass.getuser() or "Reviewer").strip()


def normalize_feature_status(status):
    value = str(status or FeatureStatus.IN_PROGRESS)
    return FEATURE_STATUS_ALIASES.get(value, value)


def review_action_comment(action, comment):
    return comment.strip() or {"approve": "Approved by reviewer.",
                               "approve_spec": "Specification approved by reviewer."}.get(action, "")


def migrate_review_terms(ledger):
    before = json.dumps(ledger, sort_keys=True)
    for feature in (ledger.get("features") or {}).values():
        if "revisit" in feature:
            feature.setdefault("revision_request", feature.pop("revisit"))
        feature["status"] = normalize_feature_status(feature.get("status"))
        for comment in feature.get("comments") or []:
            comment["action"] = {"revisit": "revise", "revisited": "revised"}.get(
                comment.get("action"), comment.get("action"))
    for event in ledger.get("events") or []:
        if event.get("status"):
            event["status"] = normalize_feature_status(event["status"])
        event["action"] = {"revisit": "revise", "revisit_completed": "revision_completed",
                           "revisit_summary_added": "revision_summary_added"}.get(
                               event.get("action"), event.get("action"))
    return before != json.dumps(ledger, sort_keys=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(10):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                # Windows indexing/read handles can briefly deny replacement;
                # retry the atomic operation, never fall back to in-place writes.
                time.sleep(.03 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


@contextmanager
def process_lock(path, timeout=30):
    """OS-held byte lock releases on a crashed worker, unlike sentinel files."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Review is busy; retry.")
                time.sleep(.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def natural_key(value):
    return tuple((0, int(x)) if x.isdigit() else (1, x.casefold()) for x in re.split(r"(\d+)", str(value)))


def architecture_features(document):
    result = {}
    def visit(node):
        if not isinstance(node, dict):
            return
        for identifier, child in (node.get("features") or {}).items():
            if isinstance(child, dict):
                result[str(identifier)] = child
                visit(child)
    visit(document)
    return result


def requirement_records(document):
    result = {}
    def visit(node):
        if node.get("id"):
            result[str(node["id"])] = node
        for child in node.get("breakdown") or []:
            if isinstance(child, dict):
                visit(child)
    for node in document.get("REQUIREMENTS") or []:
        visit(node)
    return result


def requirement_titles(document):
    return {key: str(node.get("title") or node.get("description") or "") for key, node in requirement_records(document).items()}


def add_runtime_arguments(parser):
    parser.add_argument("--root", type=Path)
    parser.add_argument("--task-id")
    for name in ("architecture", "requirements", "results-root", "status-queue"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--baseline-ref")
    parser.add_argument("--layer", choices=("base", "app"))
    return parser


def resolve_runtime(args, config=None):
    root = Path(args.root or Path(__file__).resolve().parents[1]).resolve()
    if config is None:
        from utils.baseutils import Config
        config_path = root / "config" / "base.json"
        config = Config(base_config_path=str(config_path)).config if config_path.is_file() else {}
    if hasattr(config, "get_dict"):
        config = config.get_dict()
    config = config or {}
    manager = config.get("APP", {}).get("TASK_MANAGER", {})
    review = manager.get("review") or {}
    task_id = args.task_id or (root.name if re.fullmatch(r"[A-Z]+-TASK-\d{6}-\d{4}", root.name) else None)
    if not task_id:
        raise ValueError("Unable to infer the task id; pass --task-id.")
    if not re.fullmatch(r"[A-Z]+-TASK-\d{6}-\d{4}", task_id):
        raise ValueError("Invalid task id.")
    layer = args.layer or review.get("layer") or (
        "base" if task_id.startswith("BASE-TASK-") or config.get("COMMON", {}).get("APP_NAME") == "BaseApp" else "app")
    def path(value, default):
        target = Path(value or default)
        return (target if target.is_absolute() else root / target).resolve()
    return {
        "root": root, "task_id": task_id, "layer": layer,
        "architecture": path(args.architecture or review.get("architecture"), Path("build") / "architecture" / f"{layer}.json"),
        "requirements": path(args.requirements or review.get("requirements"), Path("build") / "requirements" / f"{layer}.json"),
        "results_root": path(args.results_root or manager.get("result_store"), Path("build") / "task_results"),
        "status_queue": path(args.status_queue or manager.get("status_queue"), Path("build") / "status_queue"),
        "baseline_ref": args.baseline_ref or review.get("baseline_ref") or "main",
        "template": path(review.get("template"), Path("resources") / "templates" / "code_review.html"),
        "refresh_interval_seconds": max(1, float(review.get("refresh_interval_seconds") or 3)),
        "task_ledger": root / "build" / "tasks" / f"{layer}.json",
    }


def report_path(results_root, task_id, mode="code"):
    if mode not in {"spec", "code"}:
        raise ValueError("Unknown review report mode.")
    return Path(results_root) / task_id / f"{task_id} - {'Spec' if mode == 'spec' else 'Code'} Review.html"


def safe_artifact(root, value):
    target = (root / str(value).split("::")[0].replace("\\", "/")).resolve()
    if not target.is_relative_to(root):
        raise ValueError("Review artifact must be inside the task root.")
    return target


def json_spans(text):
    """Parse JSON structurally and retain value/key spans for exact line coverage."""
    decoder = json.JSONDecoder()
    spans = {}
    def whitespace(index):
        while index < len(text) and text[index].isspace():
            index += 1
        return index
    def visit(index, path):
        index = whitespace(index)
        start = index
        if text[index] == "{":
            index = whitespace(index + 1)
            while text[index] != "}":
                key_start = index
                key, index = decoder.raw_decode(text, index)
                index = whitespace(index)
                if text[index] != ":":
                    raise ValueError("Invalid JSON object.")
                index = visit(index + 1, path + (str(key),))
                child_start, child_end = spans[path + (str(key),)]
                spans[path + (str(key),)] = (key_start, child_end)
                index = whitespace(index)
                if text[index] == ",":
                    index = whitespace(index + 1)
                elif text[index] != "}":
                    raise ValueError("Invalid JSON delimiter.")
            end = index + 1
        elif text[index] == "[":
            index, number = whitespace(index + 1), 0
            while text[index] != "]":
                index = visit(index, path + (str(number),))
                number += 1
                index = whitespace(index)
                if text[index] == ",":
                    index = whitespace(index + 1)
                elif text[index] != "]":
                    raise ValueError("Invalid JSON delimiter.")
            end = index + 1
        else:
            _, end = decoder.raw_decode(text, index)
        spans[path] = (start, end)
        return end
    visit(0, ())
    return spans


def selected_source(root, feature):
    artifact = str(feature.get("artifact") or feature.get("path") or "")
    if not artifact:
        return "", [], False
    path = safe_artifact(root, artifact)
    if not path.is_file():
        return "", [], False
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    selector = feature.get("selector") or {}
    symbol = artifact.split("::", 1)[1] if "::" in artifact else None
    if isinstance(selector, str):
        symbol, selector = selector, {}
    symbol = selector.get("symbol") or selector.get("name") or symbol
    if symbol and path.suffix == ".py":
        tree = ast.parse(text)
        parts = str(symbol).split(".")
        nodes = tree.body
        found = None
        for part in parts:
            found = next((node for node in nodes if getattr(node, "name", None) == part), None)
            if found is None:
                return "", [], False
            nodes = getattr(found, "body", [])
        start = min([found.lineno] + [node.lineno for node in getattr(found, "decorator_list", [])])
        end = found.end_lineno
        return "\n".join(lines[start - 1:end]), list(range(start, end + 1)), True
    json_path = selector.get("json_path") or selector.get("pointer") or selector.get("keys")
    if json_path:
        value = json.loads(text)
        if isinstance(json_path, list):
            parts = [str(part) for part in json_path]
        elif str(json_path).startswith("/"):
            parts = [part.replace("~1", "/").replace("~0", "~") for part in str(json_path)[1:].split("/")]
        else:
            parts = str(json_path).removeprefix("$.").split(".")
        try:
            for part in parts:
                value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, ValueError, TypeError):
            return "", [], False
        start, end = json_spans(text)[tuple(parts)]
        first, last = text.count("\n", 0, start) + 1, text.count("\n", 0, end) + 1
        return json.dumps(value, sort_keys=True), list(range(first, last + 1)), True
    start = selector.get("start_line") or selector.get("start")
    end = selector.get("end_line") or selector.get("end")
    if isinstance(start, int) and isinstance(end, int):
        return "\n".join(lines[start - 1:end]), list(range(start, min(end, len(lines)) + 1)), end <= len(lines)
    return text, list(range(1, len(lines) + 1)), True


def git_changes(root, baseline):
    def git(*args):
        completed = subprocess.run(["git", "--no-pager", "-C", str(root), *args],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace")
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip() or "Unable to determine baseline changes.")
        return completed.stdout
    git("rev-parse", "--verify", baseline)
    tracked = set(filter(None, git("diff", "--name-only", "--no-renames", "-z", baseline, "--").split("\0")))
    names = tracked | set(filter(None, git("ls-files", "--others", "--exclude-standard", "-z").split("\0")))
    changes = {}
    for name in sorted(names):
        if name.startswith("build/review/"):
            continue
        path = root / name
        try:
            if not path.resolve().is_relative_to(root):
                changes[name] = [0]
                continue
            # Git may return an untracked nested repository or pytest directory
            # junction with a trailing separator. Neither is a source file.
            if path.is_dir():
                continue
            if path.exists() and not path.is_file():
                changes[name] = [0]
                continue
        except OSError:
            changes[name] = [0]
            continue
        if not path.exists():
            if name in tracked:
                changes[name] = [0]
            continue
        diff = git("diff", "--no-ext-diff", "--unified=0", "--no-renames", baseline, "--", name)
        positions = []
        for match in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", diff, re.M):
            start, count = int(match[1]), int(match[2] or 1)
            positions.extend(range(start, start + count) if count else [start])
        if not diff:
            try:
                positions = list(range(1, len(path.read_bytes().splitlines()) + 1)) or [0]
            except OSError:
                positions = [0]
        changes[name] = sorted(set(positions))
    return changes


def discover_components(root, task_id, architecture, requirements, baseline):
    features = architecture_features(architecture)
    records = requirement_records(requirements)
    changes = git_changes(root, baseline)
    markers = {}
    pattern = re.compile(r"\[" + re.escape(task_id) + r"\].*?\[Requirement ([^\]]+)\].*?\[Feature ([^\]]+)\]")
    for name in changes:
        path = root / name
        if not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        opened = {}
        for index, line in enumerate(lines, 1):
            match = pattern.search(line)
            if not match:
                continue
            key = match[2]
            if "START" in line:
                opened[key] = (index, match[1])
            elif "END" in line and key in opened:
                start, requirement = opened.pop(key)
                markers.setdefault(key, []).append((name, start, index, requirement))
    components = {}
    diffs = {}
    for identifier, feature in features.items():
        review = feature.get("review") or {}
        owned = review.get("task_id") == task_id or feature.get("task_id") == task_id or identifier in markers
        linked = [req for req, record in records.items() for link in record.get("solution") or []
                  if isinstance(link, dict) and str(link.get("feature")) == identifier]
        if not owned:
            owned = any((records[req].get("review") or {}).get("task_id") == task_id or
                        records[req].get("task_id") == task_id for req in linked)
        if not owned:
            continue
        item = copy.deepcopy(feature)
        item["feature_id"] = identifier
        item["requirements"] = list(dict.fromkeys((feature.get("requirements") or []) + linked +
                                                  [entry[3] for entry in markers.get(identifier, [])]))
        if identifier in markers and not item.get("selector"):
            name, start, end, _ = markers[identifier][0]
            item["artifact"] = name
            item["selector"] = {"start_line": start, "end_line": end}
        item["artifact"] = item.get("artifact") or item.get("path")
        item["title"] = review.get("title") or feature.get("name") or identifier
        item["requirement_id"] = next(iter(item["requirements"]), "")
        item["requirement_title"] = requirement_titles(requirements).get(item["requirement_id"], "")
        try:
            source, positions, ready = selected_source(root, item)
        except (ValueError, SyntaxError, OSError):
            source, positions, ready = "", [], False
        item["source"] = source
        artifact_name = str(item.get("artifact") or "").split("::")[0].replace("\\", "/")
        if artifact_name in changes and artifact_name not in diffs:
            completed = subprocess.run(["git", "--no-pager", "-C", str(root), "diff", "--no-ext-diff",
                "--unified=3", baseline, "--", artifact_name], capture_output=True, text=True,
                encoding="utf-8", errors="replace")
            if completed.returncode:
                raise RuntimeError("Unable to render baseline diff: " + completed.stderr.strip())
            diff = completed.stdout
            if not diff and ready:
                path = safe_artifact(root, artifact_name)
                text = path.read_text(encoding="utf-8", errors="replace")
                diff = "".join(difflib.unified_diff([], text.splitlines(keepends=True),
                                                   fromfile="(new file)", tofile=artifact_name))
            diffs[artifact_name] = diff
        item["diff"] = diffs.get(artifact_name, "")
        item["covered_lines"] = positions
        item["artifact_ready"] = ready
        item["source_fingerprint"] = fingerprint({"artifact": item["artifact"], "selector": item.get("selector"), "source": source})
        spec = {key: value for key, value in feature.items() if key not in {"approval", "features"}}
        item["spec_fingerprint"] = fingerprint({"feature": spec, "requirements": {
            req: records.get(req) for req in item["requirements"]}})
        components[identifier] = item
    uncovered = []
    for name, changed_lines in changes.items():
        covered = set()
        for item in components.values():
            if str(item.get("artifact") or "").split("::")[0].replace("\\", "/") == name:
                covered.update(item["covered_lines"])
                if not (root / name).exists() and (item.get("approval") or {}).get("status") == FeatureStatus.REMOVED:
                    covered.add(0)
        missing = sorted(set(changed_lines) - covered)
        if missing:
            uncovered.append({"path": name, "lines": missing})
    return components, uncovered, records


def spec_eligible(ledger):
    return bool(ledger.get("spec_reviews")) and all(
        node.get("status") == FeatureStatus.SPEC_APPROVED for node in ledger["spec_reviews"].values())


def feature_code_approvable(feature):
    """Explicit reviewer approval needs discovered implementation, not a lifecycle prerequisite."""
    return bool(feature) and feature.get("status") not in FeatureStatus.FINAL and bool(
        feature.get("artifact_ready")) and not feature.get("discovery_missing") and (
        feature.get("revision_request") or {}).get("state") not in {"pending", "processing"}


def requirement_code_features(ledger, requirement_id):
    return {identifier: feature for identifier, feature in (ledger.get("features") or {}).items()
            if requirement_id in (feature.get("requirements") or [])}


def requirement_code_approvable(ledger, requirement_id):
    remaining = [feature for feature in requirement_code_features(ledger, requirement_id).values()
                 if feature.get("status") not in FeatureStatus.FINAL]
    return requirement_id in (ledger.get("spec_reviews") or {}) and bool(remaining) and all(
        feature_code_approvable(feature) for feature in remaining)


def with_code_approval_eligibility(ledger):
    """Publish the same action gates for the embedded report and live API."""
    return {**ledger,
        "features": {identifier: {**feature, "can_approve_code": feature_code_approvable(feature)}
                     for identifier, feature in (ledger.get("features") or {}).items()},
        "spec_reviews": {identifier: {**thread,
            "can_approve_code": requirement_code_approvable(ledger, identifier),
            "code_feature_ids": list(requirement_code_features(ledger, identifier))}
            for identifier, thread in (ledger.get("spec_reviews") or {}).items()}}


def code_eligible(ledger):
    features = ledger.get("features") or {}
    return bool(features) and not ledger.get("uncovered_changes") and all(
        feature.get("status") in FeatureStatus.FINAL and
        (feature.get("revision_request") or {}).get("state") not in {"pending", "processing"} and
        (feature.get("status") == FeatureStatus.REMOVED or (
            feature.get("artifact_ready") and
            feature.get("approved_source_fingerprint") == feature.get("source_fingerprint")))
        for feature in features.values())


def artifact_readiness(ledger):
    """A file's commit gate is independent of every other reviewed file."""
    artifacts = {}
    for identifier, feature in (ledger.get("features") or {}).items():
        artifact = str(feature.get("artifact") or "").split("::")[0]
        node = artifacts.setdefault(artifact, {"artifact": artifact, "features": [], "ready_to_commit": True})
        node["features"].append(identifier)
        node["ready_to_commit"] = node["ready_to_commit"] and (
            feature.get("status") in FeatureStatus.FINAL and
            (feature.get("revision_request") or {}).get("state") not in {"pending", "processing"} and
            (feature.get("status") == FeatureStatus.REMOVED or (
                feature.get("artifact_ready") and feature.get("approved_source_fingerprint") == feature.get("source_fingerprint"))))
    uncovered = {change["path"] for change in ledger.get("uncovered_changes") or []}
    for artifact, node in artifacts.items():
        node["ready_to_commit"] = bool(artifact and node["features"] and node["ready_to_commit"] and
                                       artifact.replace("\\", "/") not in uncovered)
    return artifacts


class ReviewStore:
    """Source-compatible decisions with fingerprint invalidation and process-safe CAS."""
    def __init__(self, root, task_id, architecture=None, requirements=None, baseline_ref="main", layer=None):
        self.root = Path(root).resolve()
        self.task_id = task_id
        layer = layer or ("base" if task_id.startswith("BASE-") else "app")
        self.architecture_path = Path(architecture or self.root / "build" / "architecture" / f"{layer}.json")
        self.requirements_path = Path(requirements or self.root / "build" / "requirements" / f"{layer}.json")
        self.baseline_ref = baseline_ref
        self.review_path = self.root / "build" / "review" / task_id / "review.json"
        self.reviewer_identity = system_reviewer_identity()
        self._lock = threading.RLock()

    @contextmanager
    def transaction(self):
        with self._lock, process_lock(self.review_path.with_suffix(".lock")):
            yield

    def _initialize(self, persist=True):
        architecture = read_json(self.architecture_path) if self.architecture_path.is_file() else {}
        requirements = read_json(self.requirements_path) if self.requirements_path.is_file() else {}
        components, uncovered, records = discover_components(self.root, self.task_id, architecture, requirements, self.baseline_ref)
        existing = read_json(self.review_path) if self.review_path.exists() else {}
        ledger = copy.deepcopy(existing)
        migrate_review_terms(ledger)
        ledger.update(schema_version=2, task_id=self.task_id, baseline_ref=self.baseline_ref, uncovered_changes=uncovered)
        ledger.setdefault("created_at", utc_now())
        ledger.setdefault("revision", 0)
        ledger.setdefault("events", [])
        prior_features = ledger.get("features") or {}
        new_features = {}
        thread_numbers = {key: node.get("number") for key, node in (ledger.get("spec_reviews") or {}).items()}
        for identifier in sorted(components, key=natural_key):
            component = components[identifier]
            prior = prior_features.get(identifier) or {}
            active = (prior.get("revision_request") or {}).get("state") in {"pending", "processing"}
            architecture_status = normalize_feature_status((component.get("approval") or {}).get("status") or
                                                          (component.get("review") or {}).get("status"))
            status = normalize_feature_status(prior.get("status") or architecture_status)
            removed = status == FeatureStatus.REMOVED or any(c.get("action") == "removed" for c in prior.get("comments") or [])
            if not removed and status == FeatureStatus.CODE_APPROVED:
                if prior.get("approved_source_fingerprint") != component["source_fingerprint"]:
                    status = FeatureStatus.READY if component["artifact_ready"] else FeatureStatus.IN_PROGRESS
            if not removed and not active and status in {FeatureStatus.IN_PROGRESS, FeatureStatus.SPEC_APPROVED, FeatureStatus.SPECIFIED} and component["artifact_ready"]:
                if architecture_status in {FeatureStatus.READY, FeatureStatus.REVISED}:
                    status = architecture_status
            spec_status = prior.get("spec_status", FeatureStatus.SPECIFIED)
            if prior.get("approved_spec_fingerprint") != component["spec_fingerprint"]:
                spec_status = FeatureStatus.SPECIFIED
                if status == FeatureStatus.CODE_APPROVED and prior.get("approved_code_spec_fingerprint") != component["spec_fingerprint"]:
                    status = FeatureStatus.READY
            number = prior.get("number") or max([n.get("number", 0) for n in prior_features.values()] +
                                                [n.get("number", 0) for n in new_features.values()] + [0]) + 1
            new_features[identifier] = {**component, **{key: copy.deepcopy(prior[key]) for key in
                ("comments", "revision_request", "approved_source_fingerprint", "approved_spec_fingerprint", "approved_code_spec_fingerprint", "updated_at")
                if key in prior}, "number": number, "status": FeatureStatus.REMOVED if removed else status,
                "spec_status": spec_status}
            new_features[identifier].setdefault("comments", [])
        # Missing previously reviewed components remain visible, never silently reduce the gate.
        for identifier, prior in prior_features.items():
            if identifier not in new_features:
                new_features[identifier] = {**prior, "artifact_ready": False, "discovery_missing": True}
                if prior.get("status") != FeatureStatus.REMOVED:
                    new_features[identifier]["status"] = FeatureStatus.IN_PROGRESS
        ledger["features"] = new_features
        removed_paths = {str(node.get("artifact") or "").split("::")[0].replace("\\", "/")
                         for node in new_features.values() if node.get("status") == FeatureStatus.REMOVED}
        ledger["uncovered_changes"] = [change for change in uncovered if not (
            change["lines"] == [0] and change["path"] in removed_paths)]
        specs = {}
        for requirement_id in sorted({req for node in new_features.values() for req in node.get("requirements") or []}, key=natural_key):
            prior = (ledger.get("spec_reviews") or {}).get(requirement_id) or {}
            digest = fingerprint(records.get(requirement_id) or {"missing": requirement_id})
            approved = prior.get("approved_spec_fingerprint") == digest and prior.get("status") == FeatureStatus.SPEC_APPROVED
            number = thread_numbers.get(requirement_id) or max([n or 0 for n in thread_numbers.values()] + [0]) + 1
            thread_numbers[requirement_id] = number
            specs[requirement_id] = {**prior, "requirement_id": requirement_id, "number": number,
                "title": str((records.get(requirement_id) or {}).get("title") or requirement_id),
                "requirement": records.get(requirement_id), "source_fingerprint": digest,
                "status": FeatureStatus.SPEC_APPROVED if approved else FeatureStatus.SPECIFIED}
        ledger["spec_reviews"] = specs
        ledger["artifacts"] = artifact_readiness(ledger)
        if ledger != existing:
            ledger["revision"] = int(existing.get("revision") or 0) + 1
            ledger["updated_at"] = utc_now()
            if persist:
                atomic_json(self.review_path, ledger)
        if persist:
            atomic_json(self.review_path.with_name("components.json"), components)
        return ledger

    def initialize(self):
        with self.transaction():
            return self._initialize()

    def load(self):
        return self.initialize()

    def cached_snapshot(self):
        """Read the last atomically published model without blocking on rendering."""
        if self.review_path.is_file():
            return read_json(self.review_path)
        return self.initialize()

    def _check(self, ledger, expected_revision):
        if expected_revision is not None and int(expected_revision) != int(ledger["revision"]):
            raise RuntimeError("The review changed; reload and retry.")

    def _append_record(self, ledger, node, action, comment, **metadata):
        now, event_id = utc_now(), uuid.uuid4().hex
        node.setdefault("comments", []).append({"id": event_id, "action": action, "text": comment,
            "actor": "reviewer", "actor_name": self.reviewer_identity, "created_at": now, **metadata})
        node["updated_at"] = now
        ledger["updated_at"] = now
        ledger["events"].append({"id": event_id, "action": action, "comment": comment,
                                 "actor_name": self.reviewer_identity, "created_at": now, **metadata})

    def _publish_decision(self, ledger):
        ledger["revision"] += 1
        ledger["artifacts"] = artifact_readiness(ledger)
        atomic_json(self.review_path, ledger)
        return ledger

    def _record(self, ledger, node, action, comment, **metadata):
        self._append_record(ledger, node, action, comment, **metadata)
        return self._publish_decision(ledger)

    def _approve_code(self, node):
        node["status"] = FeatureStatus.CODE_APPROVED
        node["approved_source_fingerprint"] = node["source_fingerprint"]
        node["approved_code_spec_fingerprint"] = node["spec_fingerprint"]

    def approve_code_requirement(self, requirement_id, comment="", expected_revision=None):
        """Approve all remaining thread features with one CAS and atomic publication."""
        with self.transaction():
            ledger = self._initialize(persist=False)
            self._check(ledger, expected_revision)
            if requirement_id not in ledger["spec_reviews"]:
                raise KeyError(requirement_id)
            remaining = {identifier: node for identifier, node in
                         requirement_code_features(ledger, requirement_id).items()
                         if node.get("status") not in FeatureStatus.FINAL}
            if any(not feature_code_approvable(node) for node in remaining.values()):
                raise RuntimeError("Approve Code requires available selected implementation source "
                                   "without an active revision or missing discovery for every remaining thread feature.")
            if not remaining:
                return ledger
            for identifier, node in remaining.items():
                self._approve_code(node)
                self._append_record(ledger, node, "approve", review_action_comment("approve", comment),
                                    feature_id=identifier, requirement_id=requirement_id)
            return self._publish_decision(ledger)

    def approve_spec_requirement(self, requirement_id, comment="", expected_revision=None):
        with self.transaction():
            ledger = self._initialize()
            self._check(ledger, expected_revision)
            node = ledger["spec_reviews"][requirement_id]
            if node.get("requirement") is None:
                raise RuntimeError("Requirement specification is missing.")
            node["status"] = FeatureStatus.SPEC_APPROVED
            node["approved_spec_fingerprint"] = node["source_fingerprint"]
            return self._record(ledger, node, "approve_spec", review_action_comment("approve_spec", comment),
                                requirement_id=requirement_id)

    def update_feature(self, feature_id, action, comment="", expected_revision=None):
        action, comment = action.strip().lower(), comment.strip()
        if action not in VALID_ACTIONS:
            raise ValueError(f"Unsupported review action: {action}")
        if action in {"revise", "remove"} and not comment:
            raise ValueError("Revision and removal require a comment.")
        with self.transaction():
            ledger = self._initialize()
            self._check(ledger, expected_revision)
            node = ledger["features"][feature_id]
            if node["status"] == FeatureStatus.REMOVED:
                raise RuntimeError("Removed features are terminal.")
            active = (node.get("revision_request") or {}).get("state") in {"pending", "processing"}
            if action == "approve":
                if not feature_code_approvable(node):
                    raise RuntimeError("Approve Code requires available selected implementation source "
                                       "without an active revision or missing discovery.")
                self._approve_code(node)
            elif action == "approve_spec":
                if active or node.get("discovery_missing") or not node.get("requirements"):
                    raise RuntimeError("Feature specification is not approvable.")
                node["spec_status"] = FeatureStatus.SPEC_APPROVED
                node["approved_spec_fingerprint"] = node["spec_fingerprint"]
                if node["status"] in FeatureStatus.SPEC_APPROVABLE:
                    node["status"] = FeatureStatus.SPEC_APPROVED
            elif action == "remove":
                node["status"] = FeatureStatus.REMOVED
                if node.get("revision_request"):
                    node["revision_request"]["state"] = "completed"
            else:
                if active:
                    raise RuntimeError("A revision request is already active.")
                node["status"] = FeatureStatus.IN_PROGRESS
                node["revision_request"] = {"request_id": uuid.uuid4().hex, "state": "pending",
                    "requested_at": utc_now(), "claimed_at": None, "completed_at": None, "response": None}
            return self._record(ledger, node, "removed" if action == "remove" else action,
                                review_action_comment(action, comment), feature_id=feature_id)

    def pending_revisions(self):
        return [{**node, "request_id": node["revision_request"]["request_id"]}
                for node in self.load()["features"].values() if (node.get("revision_request") or {}).get("state") == "pending"]

    def transition_revision(self, feature_id, request_id, state, response="", expected_revision=None):
        if state not in VALID_REVISION_STATES - {"pending"}:
            raise ValueError("Unsupported revision state.")
        with self.transaction():
            ledger = self._initialize()
            self._check(ledger, expected_revision)
            node = ledger["features"][feature_id]
            request = node.get("revision_request") or {}
            if node["status"] == FeatureStatus.REMOVED or request.get("request_id") != request_id:
                raise RuntimeError("Revision request no longer exists.")
            allowed = {"processing": {"pending"}, "completed": {"processing"}, "removed": {"pending", "processing"}}
            if request.get("state") not in allowed[state]:
                raise RuntimeError("Revision state changed; reload and retry.")
            if state != "processing" and not response.strip():
                raise ValueError("Revision completion requires a response.")
            request["state"] = "completed" if state == "removed" else state
            if state == "processing":
                request["claimed_at"] = utc_now()
            else:
                request.update(completed_at=utc_now(), response=response.strip())
                node["status"] = FeatureStatus.REMOVED if state == "removed" else FeatureStatus.REVISED
            return self._record(ledger, node, "removed" if state == "removed" else "revision_" + state,
                                response.strip(), feature_id=feature_id, request_id=request_id)

    def enqueue_approval(self, queue, expected_revision=None):
        with self.transaction():
            ledger = self._initialize()
            self._check(ledger, expected_revision)
            return queue.enqueue(ledger, expected_revision)
