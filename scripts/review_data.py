"""Persist the upstream review catalog, diff snippets, change vectors and dependencies.

The source rebuild/numbering helpers supplied a task-specific component catalog;
the legacy make_step producer wrote its before/after snippets and diff map.
This callable port uses the same renderer helpers with committed portable inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.reviewutils import (
    ReviewStore, add_runtime_arguments, atomic_json, atomic_write, fingerprint,
    read_json, resolve_runtime,
)
from scripts import review_report_generator as renderer


def populate_review_data(store):
    ledger = store.load()
    components, architecture, requirements, _ = renderer.review_components(ledger, store)
    baseline = ""
    with renderer._RENDER_LOCK:
        renderer.ROOT, renderer.TASK_ID = store.root, store.task_id
        baseline = renderer.run("git", "rev-parse", "--verify", store.baseline_ref).strip()
        renderer.REVIEW_BASE = renderer.LEGACY.REVIEW_BASE = baseline
        rendered = [renderer.locate_component(component, architecture) for component in components]
        renderer.attach_dependencies(rendered)
        renderer.attach_behavior_details(rendered)
    catalog, mapping, snippets = [], {}, {}
    positions = {}
    for component in rendered:
        requirement_id = component["requirement_id"]
        thread_number = ledger["spec_reviews"][requirement_id]["number"]
        positions[requirement_id] = positions.get(requirement_id, 0) + 1
        item_order = positions[requirement_id]
        relative = component["relative"]
        # Original make_step naming and snippet/map fields; only session paths
        # become snapshot-relative paths, so the complete snapshot is portable.
        key = f"t{thread_number:02d}-s{item_order:02d}-{hashlib.sha1(relative.encode()).hexdigest()[:8]}"
        suffix = Path(relative).suffix or ".txt"
        before = f"review-diff-snippets/{key}.before{suffix}"
        after = f"review-diff-snippets/{key}.after{suffix}"
        snippets[before] = component["before"]
        snippets[after] = component["after"]
        mapping[key] = {
            "title": component["title"], "relative": relative,
            "before": before, "after": after, "working": relative,
            "line": component["start"], "component_id": component["component_id"],
        }
        feature = component["feature"]
        catalog.append({
            "review_id": f"{thread_number}.{item_order}",
            "component_id": component["component_id"], "requirement_id": requirement_id,
            "requirements": component.get("requirements", []),
            "relative": relative, "selector": feature.get("selector"),
            "needle": component["needle"], "span": component["span"],
            "title": component["title"], "question": component["question"],
            "thread_number": thread_number, "item_order": item_order,
            "label": feature["review"]["label"],
            "source": feature.get("source"), "requirement": requirements[requirement_id],
            "change_details": component["details"], "dependencies": component["dependencies"],
            "source_fingerprint": component["source_fingerprint"],
            "spec_fingerprint": component["spec_fingerprint"],
        })
    content = {"schema_version": 1, "task_id": store.task_id, "baseline": baseline,
               "components": catalog, "diff_map": mapping, "snippets": snippets,
               "uncovered_changes": ledger.get("uncovered_changes", [])}
    digest = fingerprint(content)
    snapshot = store.review_path.parent / "data" / digest
    with store.transaction():
        current = store._initialize()
        if fingerprint(current) != fingerprint(ledger):
            raise RuntimeError("Review changed during data population; retry.")
        for relative, text in snippets.items():
            atomic_write(snapshot / relative, text)
        atomic_json(snapshot / "requirement-review-components.json", catalog)
        atomic_json(snapshot / "review-diff-map.json", mapping)
        manifest = {"schema_version": 1, "task_id": store.task_id, "baseline": baseline,
            "content_hash": digest, "snapshot": str(snapshot.relative_to(store.review_path.parent)),
            "components": len(catalog), "threads": len(positions),
            "uncovered_changes": ledger.get("uncovered_changes", [])}
        atomic_json(store.review_path.with_name("data.json"), manifest)
    return manifest


def validate_review_data(store):
    """Reproduce the full data fingerprint, including persisted snippet bytes."""
    errors = []
    manifest_path = store.review_path.with_name("data.json")
    if not manifest_path.is_file():
        return ["Review data has not been populated."]
    try:
        manifest = read_json(manifest_path)
        snapshot = (store.review_path.parent / manifest["snapshot"]).resolve()
        if not snapshot.is_relative_to(store.review_path.parent.resolve()):
            return ["Review data snapshot must be inside the task review directory."]
        catalog = read_json(snapshot / "requirement-review-components.json")
        mapping = read_json(snapshot / "review-diff-map.json")
        snippets = {}
        for entry in mapping.values():
            for field in ("before", "after"):
                relative = entry[field]
                path = (snapshot / relative).resolve()
                if not path.is_relative_to(snapshot):
                    return ["Diff snippet must be inside its snapshot."]
                snippets[relative] = path.read_text(encoding="utf-8")
        content = {"schema_version": 1, "task_id": manifest["task_id"],
                   "baseline": manifest["baseline"], "components": catalog,
                   "diff_map": mapping, "snippets": snippets,
                   "uncovered_changes": manifest.get("uncovered_changes", [])}
        if fingerprint(content) != manifest["content_hash"]:
            errors.append("Review data content hash mismatch.")
        ledger = store.load()
        baseline = subprocess.run(["git", "rev-parse", "--verify", store.baseline_ref],
            cwd=store.root, check=True, capture_output=True, text=True).stdout.strip()
        if baseline != manifest["baseline"]:
            errors.append("Review data baseline is stale.")
        expected, _, _, _ = renderer.review_components(ledger, store)
        if {(node["component_id"], node["source_fingerprint"], node["spec_fingerprint"])
            for node in catalog} != {
                (node["component_id"], node["source_fingerprint"], node["spec_fingerprint"])
                for node in expected}:
            errors.append("Review data source/specification snapshot is stale.")
        if ledger.get("uncovered_changes"):
            errors.append("Review data has uncovered changes.")
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        errors.append(f"Review data is invalid: {error}")
    return errors


def main(argv=None):
    parser = add_runtime_arguments(argparse.ArgumentParser(description=__doc__))
    parser.add_argument("command", choices=("populate", "validate"))
    parser.add_argument("--render", action="store_true", help="Generate exclusive review reports after populating.")
    args = parser.parse_args(argv)
    runtime = resolve_runtime(args)
    store = ReviewStore(runtime["root"], runtime["task_id"], runtime["architecture"],
                        runtime["requirements"], runtime["baseline_ref"], runtime["layer"])
    if args.command == "validate":
        errors = validate_review_data(store)
        print(json.dumps({"valid": not errors, "errors": errors}, indent=2))
        return int(bool(errors))
    result = populate_review_data(store)
    if args.render:
        result["reports"] = renderer.generate_reports(store, runtime["results_root"],
            runtime["template"], runtime["refresh_interval_seconds"])
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
