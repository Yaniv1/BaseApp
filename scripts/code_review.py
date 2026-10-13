"""Generate and serve a task's interactive review (source APP-REQ-011 launcher contract)."""
from __future__ import annotations
import argparse
import ctypes
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.reviewutils import ReviewStore, add_runtime_arguments, atomic_json, report_path, resolve_runtime
from scripts.review_report_generator import generate_reports
from scripts.review_server import CodeApprovalQueue, ReviewHttpServer, SpecApprovalQueue


def verify_health(url, task_id, root=None, worker_pid=None, instance_id=None, attempts=30):
    last_error = None
    for _ in range(attempts):
        try:
            with urlopen(url.rstrip("/") + "/health", timeout=1) as response:
                payload = json.loads(response.read().decode())
            if payload.get("status") != "ok" or payload.get("task_id") != task_id:
                raise RuntimeError("Review health task identity mismatch.")
            if root is not None and Path(payload.get("root", "")).resolve() != Path(root).resolve():
                raise RuntimeError("Review health root identity mismatch.")
            if worker_pid is not None and payload.get("worker_pid") != int(worker_pid):
                raise RuntimeError("Review health worker identity mismatch.")
            if instance_id is not None and payload.get("instance_id") != instance_id:
                raise RuntimeError("Review health instance identity mismatch.")
            return payload
        except Exception as error:
            last_error = error
            time.sleep(.1)
    raise RuntimeError(f"Review server health check failed: {last_error}")


def open_review_browser(url):
    """Preserve source's visible Windows new-window browser activation."""
    if sys.platform == "win32":
        roots = [Path(os.environ.get(name, "")) for name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        candidates = [(shutil.which("msedge"), "--new-window"),
                      *[(str(root / "Microsoft" / "Edge" / "Application" / "msedge.exe"), "--new-window") for root in roots],
                      (shutil.which("chrome"), "--new-window"),
                      *[(str(root / "Google" / "Chrome" / "Application" / "chrome.exe"), "--new-window") for root in roots],
                      (shutil.which("firefox"), "-new-window")]
        for executable, flag in candidates:
            if executable and Path(executable).is_file():
                try:
                    subprocess.Popen([executable, flag, url], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
                except OSError:
                    pass
    return webbrowser.open(url, new=1)


def worker_alive(pid):
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def parser():
    result = add_runtime_arguments(argparse.ArgumentParser(description=__doc__))
    result.add_argument("--host", default="127.0.0.1")
    result.add_argument("--port", type=int, default=0)
    result.add_argument("--mode", choices=("spec", "code"), default="code")
    result.add_argument("--worker-pid", type=int)
    result.add_argument("--instance-id")
    result.add_argument("--health-file", type=Path)
    result.add_argument("--no-browser", action="store_true")
    result.add_argument("--no-refresh", action="store_true")
    result.add_argument("--open-existing", action="store_true")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    runtime = resolve_runtime(args)
    store = ReviewStore(runtime["root"], runtime["task_id"], runtime["architecture"],
                        runtime["requirements"], runtime["baseline_ref"], runtime["layer"])
    health_file = args.health_file or store.review_path.with_name("server.json")
    if args.open_existing:
        payload = json.loads(health_file.read_text(encoding="utf-8"))
        verify_health(payload["url"], runtime["task_id"], runtime["root"], payload["worker_pid"], payload["instance_id"], attempts=1)
        open_review_browser(payload["url"] + ("spec" if args.mode == "spec" else "code"))
        return 0
    report = report_path(runtime["results_root"], runtime["task_id"], args.mode)
    cached = report.is_file()
    if not cached:
        generate_reports(store, runtime["results_root"], runtime["template"], runtime["refresh_interval_seconds"])
    pending = runtime["status_queue"] / "pending"
    context = {key: str(runtime[key]) for key in ("root", "architecture", "requirements", "baseline_ref", "layer")}
    server = ReviewHttpServer((args.host, args.port), store, report,
        CodeApprovalQueue(pending, runtime["task_ledger"], runtime["task_id"], context),
        SpecApprovalQueue(pending, runtime["task_ledger"], runtime["task_id"], context),
        runtime=runtime, worker_pid=args.worker_pid, instance_id=args.instance_id)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"
    stopped = threading.Event()
    def stop(signum, frame):
        stopped.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    thread = threading.Thread(target=server.serve_forever, name="interactive-review", daemon=True)
    thread.start()
    try:
        verify_health(url, runtime["task_id"], runtime["root"], args.worker_pid, server.instance_id)
        atomic_json(health_file, {"url": url, "root": str(runtime["root"]), "task_id": runtime["task_id"],
                                 "pid": os.getpid(), "worker_pid": args.worker_pid, "instance_id": server.instance_id})
        print(f"Serving {runtime['task_id']} review at {url}", flush=True)
        if not args.no_browser:
            open_review_browser(url)
        if not args.no_refresh:
            server.maybe_refresh(force=True)
        else:
            server.runtime = None
        while thread.is_alive() and not stopped.wait(.5):
            if args.worker_pid is not None and not worker_alive(args.worker_pid):
                break
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if health_file.is_file():
            try:
                if json.loads(health_file.read_text(encoding="utf-8")).get("instance_id") == server.instance_id:
                    health_file.unlink()
            except (OSError, ValueError):
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
