"""Local interactive review service adapted from APP-REQ-011's proven HTTP contract."""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.reviewutils import (
    CODE_APPROVED_OR_LATER, SPEC_APPROVED_OR_LATER, FeatureStatus, ReviewStore,
    add_runtime_arguments, atomic_write, code_eligible, file_hash, process_lock,
    fingerprint, read_json, report_path, resolve_runtime, spec_eligible, system_reviewer_identity,
    with_code_approval_eligibility,
)
from scripts.status_queue_mcp import build_status_update_request, write_request_atomically


class CodeApprovalQueue:
    """Reuse Task Manager's durable queue writer; rejected requests remain retryable."""
    status = FeatureStatus.CODE_APPROVED
    later = CODE_APPROVED_OR_LATER
    gate = staticmethod(code_eligible)

    def __init__(self, pending_dir, task_ledger_path, task_id, review_context=None):
        self.pending_dir = Path(pending_dir).resolve()
        self.task_ledger_path = Path(task_ledger_path).resolve()
        self.task_id = task_id
        self.review_context = review_context or {}
        self._lock = threading.RLock()

    def _task_status(self):
        if self.task_ledger_path.is_file():
            for task in read_json(self.task_ledger_path).get("TASKS") or []:
                if task.get("id") == self.task_id:
                    return task.get("status", "")
        return ""

    def _existing_request_for(self, status, snapshot=None):
        for directory in (self.pending_dir, self.pending_dir.parent / "processed"):
            for path in directory.glob("*.json"):
                try:
                    document = read_json(path)
                except (OSError, ValueError):
                    continue
                if any(task.get("id") == self.task_id and task.get("status") == status
                       for task in document.get("TASKS") or []) and (
                           snapshot is None or (document.get("REVIEW") or {}).get("snapshot") == snapshot):
                    return path
        return None

    def enqueue(self, review_ledger, expected_revision=None):
        if expected_revision is not None and int(expected_revision) != review_ledger["revision"]:
            raise RuntimeError("The review changed; reload and retry.")
        if not self.gate(review_ledger):
            blockers = "requirement specifications" if self.status == FeatureStatus.SPEC_APPROVED else (
                "features, artifacts, revisions, or coverage")
            raise RuntimeError(f"{self.status} approval gate is incomplete ({blockers}).")
        with self._lock, process_lock(self.pending_dir.parent / ".review-queue.lock"):
            current = self._task_status()
            if current in self.later:
                return {"state": "already_applied", "status": current}
            snapshot = fingerprint(review_ledger)
            existing = self._existing_request_for(self.status, snapshot)
            if existing:
                return {"state": "already_queued", "status": self.status, "request": existing.name}
            request = build_status_update_request(self.task_id, status=self.status,
                comment=f"{self.status} approved through interactive review.", author=system_reviewer_identity())
            request["REVIEW"] = {**self.review_context, "snapshot": snapshot}
            path = write_request_atomically(self.pending_dir, request, self.task_id)
            return {"state": "queued", "status": self.status, "request": path.name}


class SpecApprovalQueue(CodeApprovalQueue):
    status = FeatureStatus.SPEC_APPROVED
    later = SPEC_APPROVED_OR_LATER
    gate = staticmethod(spec_eligible)


def save_static_review_snapshot(report_path, task_id):
    report_path = Path(report_path)
    source = report_path.read_text(encoding="utf-8")
    source = re.sub(r"<script\b[^>]*>.*?</script>", "", source, flags=re.S | re.I)
    source = re.sub(r"<(?:button|textarea|select)\b[^>]*>.*?</(?:button|textarea|select)>", "", source, flags=re.S | re.I)
    source = source.replace("</head>", "<style>.toolbar,.review-actions{display:none!important}</style></head>", 1)
    source = source.replace("<body>", "<body><p>Static review snapshot — live controls disabled.</p>", 1)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%fZ")
    target = report_path.with_name(f"{task_id} - {'Spec' if 'Spec Review' in report_path.name else 'Code'} Review Static {stamp}.html")
    atomic_write(target, source)
    return target


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "BaseAppReview/1.0"

    @property
    def review_server(self):
        return self.server

    def _send_json(self, payload, status=HTTPStatus.OK):
        if isinstance(payload, dict) and "features" in payload and "revision" in payload:
            payload = {**with_code_approval_eligibility(payload),
                       "can_code": code_eligible(payload), "can_spec": spec_eligible(payload)}
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, download=False):
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.send_header("X-Review-File", path.name)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_download(self, path):
        self._send_file(path, download=True)

    def do_GET(self):
        path = urlparse(self.path).path
        server = self.review_server
        try:
            if path in {"/", "/index.html"}:
                self._send_file(server.report_path)
            elif path == "/spec":
                self._send_file(report_path(server.results_root, server.store.task_id, "spec"))
            elif path == "/code":
                self._send_file(report_path(server.results_root, server.store.task_id, "code"))
            elif path == "/health":
                self._send_json({"status": "ok", "task_id": server.store.task_id, "root": str(server.store.root),
                    "pid": server.instance_pid, "worker_pid": server.worker_pid, "instance_id": server.instance_id})
            elif path == "/api/review":
                self._send_json(server.store.cached_snapshot())
                server.maybe_refresh()
            elif path == "/api/review/pending":
                ledger = server.store.cached_snapshot()
                self._send_json([{**node, "request_id": node["revision_request"]["request_id"]}
                    for node in ledger.get("features", {}).values()
                    if (node.get("revision_request") or {}).get("state") == "pending"])
                server.maybe_refresh()
            elif path == "/api/review/report-state":
                server.maybe_refresh()
                metadata_path = server.store.review_path.with_name("reports.json")
                metadata = read_json(metadata_path) if metadata_path.is_file() else {}
                self._send_json({"version": file_hash(server.report_path) if server.report_path.is_file() else "",
                                 "snapshot_hash": metadata.get("snapshot_hash"),
                                 "refresh_error": server.refresh_error, "refreshing": server.refreshing})
            elif path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            else:
                self.send_error(404)
        except (OSError, ValueError, RuntimeError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.SERVICE_UNAVAILABLE)

    def _mutation_allowed(self):
        server = self.review_server
        origin = self.headers.get("Origin")
        host, port = server.server_address[:2]
        allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
        # CLI worker mutations have no browser Origin; browsers must use same-origin plus task header.
        return (origin is None or origin in allowed) and self.headers.get("Host") in {
            f"{host}:{port}", f"127.0.0.1:{port}", f"localhost:{port}"} and (
                self.headers.get("X-Review-Task") == server.store.task_id)

    def do_POST(self):
        if not self._mutation_allowed():
            self._send_json({"error": "Invalid review origin, host, or task header."}, HTTPStatus.FORBIDDEN)
            return
        server = self.review_server
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= 65536:
                raise ValueError("Invalid request size.")
            payload = json.loads(self.rfile.read(length).decode())
            if not isinstance(payload, dict) or payload.get("expected_revision") is None:
                raise ValueError("expected_revision is required.")
            if path in {"/api/review/task-status/spec-approved", "/api/review/task-status/code-approved"}:
                queue = server.spec_approval_queue if path.endswith("spec-approved") else server.approval_queue
                result = server.store.enqueue_approval(queue, payload["expected_revision"])
            elif path in {"/api/review/report/static", "/api/review/report/static/download"}:
                with server.store.transaction():
                    ledger = server.store._initialize()
                    server.store._check(ledger, payload["expected_revision"])
                    server.refresh_sync(ledger)
                    target = save_static_review_snapshot(server.report_path, server.store.task_id)
                if path.endswith("/download"):
                    self._send_download(target)
                    return
                result = {"state": "saved", "file": target.name}
            elif path.startswith("/api/review/requirements/") and path.endswith("/spec"):
                requirement_id = unquote(path[len("/api/review/requirements/"):-len("/spec")])
                result = server.store.approve_spec_requirement(requirement_id, str(payload.get("comment") or ""),
                                                               payload["expected_revision"])
            elif path.startswith("/api/review/requirements/") and path.endswith("/code"):
                requirement_id = unquote(path[len("/api/review/requirements/"):-len("/code")])
                result = server.store.approve_code_requirement(requirement_id, str(payload.get("comment") or ""),
                                                               payload["expected_revision"])
            elif path.startswith("/api/review/features/"):
                feature_id = unquote(path[len("/api/review/features/"):])
                if feature_id.endswith("/revision"):
                    result = server.store.transition_revision(feature_id[:-len("/revision")], payload["request_id"],
                        payload["state"], str(payload.get("response") or ""), payload["expected_revision"])
                else:
                    result = server.store.update_feature(feature_id, str(payload.get("action") or ""),
                                                         str(payload.get("comment") or ""), payload["expected_revision"])
            else:
                self.send_error(404)
                return
            server.maybe_refresh(force=True)
            self._send_json(result)
        except KeyError as error:
            self._send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
        except RuntimeError as error:
            self._send_json({"error": str(error)}, HTTPStatus.CONFLICT)
        except (ValueError, TypeError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except OSError as error:
            self._send_json({"error": f"Persistence/status queue failure: {error}"}, HTTPStatus.SERVICE_UNAVAILABLE)

    def log_message(self, format, *args):
        pass


class ReviewHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, store, report_path, approval_queue, spec_approval_queue,
                 runtime=None, worker_pid=None, instance_id=None):
        if address[0] not in {"127.0.0.1", "localhost"}:
            raise ValueError("Review service must bind to localhost.")
        super().__init__(address, ReviewHandler)
        import os
        import uuid
        self.store, self.report_path = store, Path(report_path)
        self.approval_queue, self.spec_approval_queue = approval_queue, spec_approval_queue
        self.runtime = runtime
        self.results_root = runtime["results_root"] if runtime else self.report_path.parent.parent
        self.worker_pid, self.instance_pid = worker_pid, os.getpid()
        self.instance_id = instance_id or uuid.uuid4().hex
        self.refresh_error, self.refreshing, self.last_refresh = "", False, 0
        self._refresh_lock = threading.Lock()

    def refresh_sync(self, ledger=None):
        if self.runtime:
            from scripts.review_report_generator import generate_reports
            generate_reports(self.store, self.runtime["results_root"], self.runtime["template"],
                             self.runtime["refresh_interval_seconds"], ledger)

    def maybe_refresh(self, force=False):
        import time
        interval = self.runtime["refresh_interval_seconds"] if self.runtime else 3
        with self._refresh_lock:
            if not self.runtime or self.refreshing or (not force and time.monotonic() - self.last_refresh < interval):
                return
            self.refreshing = True
        def run():
            try:
                self.refresh_sync()
                self.refresh_error = ""
            except Exception as error:
                self.refresh_error = str(error)
            finally:
                with self._refresh_lock:
                    self.last_refresh = time.monotonic()
                    self.refreshing = False
        threading.Thread(target=run, name="review-report-refresh", daemon=True).start()


def parser():
    result = add_runtime_arguments(argparse.ArgumentParser(description=__doc__))
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("initialize")
    sub.add_parser("pending")
    transition = sub.add_parser("transition")
    transition.add_argument("feature_id")
    transition.add_argument("request_id")
    transition.add_argument("state", choices=("processing", "completed", "removed"))
    transition.add_argument("--response", default="")
    transition.add_argument("--expected-revision", type=int, required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    runtime = resolve_runtime(args)
    store = ReviewStore(runtime["root"], runtime["task_id"], runtime["architecture"],
                        runtime["requirements"], runtime["baseline_ref"], runtime["layer"])
    if args.command == "pending":
        value = store.pending_revisions()
    elif args.command == "transition":
        value = store.transition_revision(args.feature_id, args.request_id, args.state,
                                          args.response, args.expected_revision)
    else:
        value = store.initialize()
    print(json.dumps(value, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
