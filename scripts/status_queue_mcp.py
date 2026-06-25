#!/usr/bin/env python3
"""Per-agent stdio MCP server: a thin ``enqueue`` writer over the durable
task status store.

This server is launched by ``launch_task_agent.ps1`` (via the Copilot CLI's
``--additional-mcp-config`` hook) and is therefore bound to a single worker
agent's lifecycle. It exposes one tool, ``enqueue_status_update``, which lets
the agent request a task-ledger change (status and/or a progress comment)
without writing request files by hand.

The server is intentionally a *thin writer*: an ``enqueue`` simply drops a
durable JSON request file into the status store's ``pending`` directory (the
same files the Task Manager already samples and applies to ``main``). It holds
no in-memory queue and keeps no state, so it is restart-safe and never on the
critical path -- if this server is unavailable the agent can still write the
request file directly (the documented fallback). Dequeue is deliberately *not*
implemented here: the Task Manager consumes the shared store directly from the
filesystem, so there is no shared-server dependency for the consumer side.

Transport: newline-delimited JSON-RPC 2.0 over stdin/stdout (the MCP stdio
transport). All diagnostics go to stderr so stdout stays a clean message
stream.

Configuration (environment variables, set by the launcher):
  TASK_STATUS_PENDING_DIR  Absolute path to the status store's ``pending`` dir.
  TASK_STATUS_TASK_ID      The task id this agent is working on.
  TASK_STATUS_AUTHOR       Optional default comment author (defaults to
                           ``"<task id> worker"``).
"""

import datetime as dt
import json
import os
import sys
from pathlib import Path

SERVER_NAME = "task-status-queue"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"

ENV_PENDING_DIR = "TASK_STATUS_PENDING_DIR"
ENV_TASK_ID = "TASK_STATUS_TASK_ID"
ENV_AUTHOR = "TASK_STATUS_AUTHOR"

ENQUEUE_TOOL = {
    "name": "enqueue_status_update",
    "description": (
        "Request a durable update to this task's ledger entry (its status "
        "and/or a single progress comment). The request is enqueued as a file "
        "in the task status store; the Task Manager samples the store and "
        "applies it to the ledger on the main branch (committed and pushed). "
        "Use this instead of editing the task ledger directly -- your work may "
        "run on a separate branch where direct ledger edits are invisible to "
        "the Task Manager. The task id is taken from the agent's environment, "
        "so you only supply the status and/or comment. Provide at least one of "
        "'status' or 'comment'."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": (
                    "New task status, e.g. one of ToDo, InProgress, Specified, "
                    "Ready, Deployed, Approved, Done."
                ),
            },
            "comment": {
                "type": "string",
                "description": "A single progress comment to append to the task.",
            },
        },
        "additionalProperties": False,
    },
}


def _now_iso():
    """UTC timestamp matching the Task Manager's comment timestamp format."""
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def build_status_update_request(task_id, status=None, comment=None, author=None, timestamp=None):
    """Feature ID: 3.6.13.1. Build a restricted ``{"TASKS": [...]}`` update request for one task.

    Only the fields that are supplied are included, mirroring the
    ``build/tasks/update.template.json`` shape consumed by the Task Manager
    (status overwrites; comments are appended). Raises ``ValueError`` when
    neither a status nor a comment is provided.
    """
    task_id = str(task_id or "").strip()
    if not task_id:
        raise ValueError("a task id is required to enqueue a status update")

    status = str(status).strip() if status is not None else ""
    comment = str(comment).strip() if comment is not None else ""
    if not status and not comment:
        raise ValueError("provide at least one of 'status' or 'comment'")

    task = {"id": task_id}
    if status:
        task["status"] = status
    if comment:
        task["comments"] = [{
            "author": (str(author).strip() if author else "") or f"{task_id} worker",
            "content": comment,
            "timestamp": timestamp or _now_iso(),
        }]
    return {"TASKS": [task]}


def write_request_atomically(pending_dir, request, task_id):
    """Feature ID: 3.6.13.2. Write ``request`` as a uniquely named JSON file into ``pending_dir``.

    The file is written to a temporary name and then atomically renamed into
    place so the Task Manager never reads a half-written request. Returns the
    final file path.
    """
    pending = Path(pending_dir)
    pending.mkdir(parents=True, exist_ok=True)

    safe_id = str(task_id or "task").replace("/", "_").replace("\\", "_")
    stamp = dt.datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    name = f"{safe_id}-{stamp}.json"
    final = pending / name
    tmp = pending / (name + ".tmp")
    tmp.write_text(json.dumps(request, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, final)
    return final


def _log(message):
    sys.stderr.write(f"[{SERVER_NAME}] {message}\n")
    sys.stderr.flush()


def _handle_enqueue(arguments):
    """Execute the enqueue tool. Returns an MCP ``tools/call`` result dict."""
    pending_dir = os.environ.get(ENV_PENDING_DIR, "").strip()
    task_id = os.environ.get(ENV_TASK_ID, "").strip()
    author = os.environ.get(ENV_AUTHOR, "").strip() or None

    if not pending_dir:
        return _tool_error(
            f"The status store is not configured ({ENV_PENDING_DIR} is unset); "
            "cannot enqueue a status update."
        )
    if not task_id:
        return _tool_error(
            f"No task id is configured ({ENV_TASK_ID} is unset); cannot enqueue."
        )

    arguments = arguments or {}
    try:
        request = build_status_update_request(
            task_id,
            status=arguments.get("status"),
            comment=arguments.get("comment"),
            author=author,
        )
        path = write_request_atomically(pending_dir, request, task_id)
    except (ValueError, OSError) as exc:
        return _tool_error(str(exc))

    summary = []
    if request["TASKS"][0].get("status"):
        summary.append(f"status -> {request['TASKS'][0]['status']}")
    if request["TASKS"][0].get("comments"):
        summary.append("comment appended")
    return _tool_text(
        f"Enqueued status update for {task_id} ({', '.join(summary)}). "
        f"Request file: {path.as_posix()}"
    )


def _tool_text(text):
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _tool_error(text):
    return {"content": [{"type": "text", "text": f"Error: {text}"}], "isError": True}


def _dispatch(method, params):
    """Return (result, is_notification) for a JSON-RPC method.

    ``result`` is the JSON-RPC result payload (or an Exception to signal an
    error). For notifications the caller must not send a response.
    """
    if method == "initialize":
        client_version = (params or {}).get("protocolVersion") or DEFAULT_PROTOCOL_VERSION
        return {
            "protocolVersion": client_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
    if method == "tools/list":
        return {"tools": [ENQUEUE_TOOL]}
    if method == "tools/call":
        name = (params or {}).get("name")
        if name != ENQUEUE_TOOL["name"]:
            raise _RpcError(-32602, f"Unknown tool: {name}")
        return _handle_enqueue((params or {}).get("arguments"))
    if method == "ping":
        return {}
    raise _RpcError(-32601, f"Method not found: {method}")


class _RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _write_message(message):
    data = json.dumps(message, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(data + b"\n")
    sys.stdout.buffer.flush()


def main():
    """Feature ID: 3.6.13.3. Run the stdio JSON-RPC loop: read newline-delimited requests and dispatch them."""
    _log(
        f"started (task={os.environ.get(ENV_TASK_ID, '?')}, "
        f"pending={os.environ.get(ENV_PENDING_DIR, '?')})"
    )
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _log(f"ignoring non-JSON line: {exc}")
            continue

        method = request.get("method")
        msg_id = request.get("id")
        is_request = msg_id is not None

        # Notifications (no id) are handled but never answered.
        if not is_request:
            if method and method.startswith("notifications/"):
                continue
            continue

        try:
            result = _dispatch(method, request.get("params"))
            _write_message({"jsonrpc": "2.0", "id": msg_id, "result": result})
        except _RpcError as exc:
            _write_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": exc.code, "message": exc.message},
            })
        except Exception as exc:  # noqa: BLE001 - surface as JSON-RPC error
            _log(f"internal error handling {method}: {exc}")
            _write_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": f"Internal error: {exc}"},
            })

    _log("stdin closed; exiting")


if __name__ == "__main__":
    main()
