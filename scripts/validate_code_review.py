"""Validate exact baseline coverage, report freshness, and shared approval gates."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.reviewutils import (
    ReviewStore, add_runtime_arguments, code_eligible, file_hash, fingerprint,
    read_json, report_path, resolve_runtime,
)
from scripts.review_report_generator import render_report
import hashlib
import html
import re
from utils.reviewutils import FeatureStatus


def validate_presentation(report, ledger):
    """Portable checks copied from the perfected requirement-review validator."""
    count = len(ledger.get("features", {}))
    threads = len(ledger.get("spec_reviews", {}))
    checks = {
        "requirement_threads": report.count('<section class="thread" id="requirement-') == threads,
        "architecture_components": report.count('<article class="component"') == count,
        "architecture_names": all(html.escape(str(node.get("name") or "")) in report
                                  for node in ledger.get("features", {}).values()),
        "architecture_descriptions": all(html.escape(str(node.get("description") or "")) in report
                                         for node in ledger.get("features", {}).values()),
        "artifact_gate": "Artifact commit gate" in report,
        "expand_all": "Expand all diffs" in report,
        "collapse_all": "Collapse all diffs" in report,
        "inline_diffs": report.count('<details class="inline-diff">') == count,
        "thread_change_vectors": report.count('data-change-vector="thread"') == threads,
        "step_change_vectors": report.count('data-change-vector="step"') == count,
        "thread_dependencies": report.count('data-dependency-map="thread"') == threads,
        "step_dependencies": report.count('data-dependency-map="step"') == count,
        "before_now_how_coverage": all(report.count(f"<dt>{label}</dt>") >= threads + count
                                     for label in ("Before", "Now", "How")),
        "thread_sorting": all(value in report for value in
            ('id="thread-sort-order"', "steps-asc", "steps-desc", "approval-asc", "approval-desc", "sortThreadNavigation")),
        "sidebar_approval_progress": all(value in report for value in
            ("data-sidebar-approval-fill", "data-sidebar-approval-share", "data-sidebar-approval-count",
             "statusCounts.approved + statusCounts.removed")),
        "sidebar_status_filter": report.count('<input type="checkbox" data-status-filter') == len(FeatureStatus.FILTER_OPTIONS)
            and all(f'value="{status}"' in report for status in FeatureStatus.FILTER_OPTIONS),
        "feature_display_mode": all(value in report for value in
            ('value="matching-features"', 'value="all-thread-features"', "selectedFeatureDisplayMode",
             "showAllThreadFeatures || selected.has(status)", "navigateToFilteredThread")),
        "interactive_review_controls": all(value in report for value in
            ('data-review-action="approve_spec"', 'data-review-action="approve"', 'data-review-action="revise"',
             "submitReviewAction", "submitSpecApproval", "submitTaskSpecApproved", "submitCodeApproved",
             "saveStaticHtml", "response.blob()", "link.download = filename", "comment.actor_name || 'Reviewer'")),
        "thread_code_approval": report.count('data-code-action="approve"') == threads and all(
            value in report for value in ("submitThreadCodeApproval", "specReview.can_approve_code !== true",
                                         "entry.can_approve_code !== true")),
        "report_freshness_polling": all(value in report for value in
            ("/api/review/report-state", "reviewReportVersion", "setInterval", "cache: 'no-store'")),
    }
    if count == 0:
        checks["interactive_review_controls"] = "No task-owned review components" in report
    return [f"Upstream presentation contract failed: {name}" for name, passed in checks.items() if not passed]


def validate_review(store, results_root, template_path=None, require_approved=False):
    template_path = Path(template_path or Path(__file__).resolve().parents[1] / "resources" / "templates" / "code_review.html")
    errors = []
    with store.transaction():
        ledger = store._initialize()
        if not ledger["features"]:
            errors.append("No task-owned review components.")
        if ledger.get("uncovered_changes"):
            errors.append("Uncovered baseline changes: " + json.dumps(ledger["uncovered_changes"]))
        if any(not node.get("artifact_ready") and node.get("status") != "Removed" for node in ledger["features"].values()):
            errors.append("Review artifacts or structural selectors are not ready.")
        metadata_path = store.review_path.with_name("reports.json")
        if not metadata_path.is_file():
            errors.append("Rendered report metadata is missing.")
        else:
            metadata = read_json(metadata_path)
            if metadata.get("input_hash") != fingerprint(ledger):
                errors.append("Rendered review source snapshot is stale.")
            if metadata.get("template_hash") != file_hash(template_path):
                errors.append("Rendered report template is stale.")
            for mode in ("spec", "code"):
                path = report_path(results_root, store.task_id, mode)
                expected = hashlib.sha256(render_report(ledger, template_path.read_text(encoding="utf-8"),
                    mode, metadata.get("interval", 3), store).encode()).hexdigest()
                if not path.is_file() or file_hash(path) != expected or (
                    metadata.get("reports", {}).get(mode, {}).get("hash") != expected):
                    errors.append(f"{mode.title()} report hash does not match actual rendered snapshot.")
                if path.is_file():
                    errors.extend(validate_presentation(path.read_text(encoding="utf-8"), ledger))
        if require_approved and not code_eligible(ledger):
            errors.append("CodeApproved task gate is incomplete.")
    return errors


def main(argv=None):
    parser = add_runtime_arguments(argparse.ArgumentParser(description=__doc__))
    parser.add_argument("--require-approved", action="store_true")
    args = parser.parse_args(argv)
    runtime = resolve_runtime(args)
    store = ReviewStore(runtime["root"], runtime["task_id"], runtime["architecture"],
                        runtime["requirements"], runtime["baseline_ref"], runtime["layer"])
    errors = validate_review(store, runtime["results_root"], runtime["template"], args.require_approved)
    print(json.dumps({"valid": not errors, "errors": errors}, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
