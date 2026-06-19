#!/usr/bin/env python3
"""Feature ID: 3.6. Local web interface for managing build/tasks/base.json."""

import argparse
import datetime as dt
import html
import json
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS_PATH = PROJECT_ROOT / "build" / "tasks" / "base.json"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _now_iso():
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _load_tasks_store(tasks_path):
    path = Path(tasks_path)
    if path.is_file():
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    else:
        data = {}

    if not isinstance(data, dict):
        data = {}

    data.setdefault("TASKS", [])
    if not isinstance(data["TASKS"], list):
        data["TASKS"] = []

    return data


def _save_tasks_store(tasks_path, data):
    path = Path(tasks_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=4, ensure_ascii=False)
        handle.write("\n")


def _split_description(description):
    text = "\n".join(description) if isinstance(description, list) else str(description or "")
    lines = [line.rstrip() for line in text.splitlines()]
    meaningful = [line for line in lines if line.strip()]
    if len(meaningful) > 1:
        return meaningful
    if meaningful:
        return meaningful[0]
    return ""


def _normalise_comment(comment):
    if isinstance(comment, str):
        content = comment.strip()
        author = "Task Manager UI"
        timestamp = _now_iso()
    elif isinstance(comment, dict):
        content = str(comment.get("content", "")).strip()
        author = str(comment.get("author", "Task Manager UI")).strip() or "Task Manager UI"
        timestamp = str(comment.get("timestamp", "")).strip() or _now_iso()
    else:
        content = str(comment or "").strip()
        author = "Task Manager UI"
        timestamp = _now_iso()

    return {
        "author": author,
        "content": content,
        "timestamp": timestamp,
    }


def _next_task_id(tasks):
    today = dt.datetime.utcnow().strftime("%y%m%d")
    prefix = f"BASE-TASK-{today}-"
    next_number = 1
    for task in tasks:
        task_id = str(task.get("id", ""))
        if not task_id.startswith(prefix):
            continue
        try:
            next_number = max(next_number, int(task_id.rsplit("-", 1)[-1]) + 1)
        except ValueError:
            continue
    return f"{prefix}{next_number:04d}"


def _find_task(tasks, task_id):
    for index, task in enumerate(tasks):
        if str(task.get("id", "")) == str(task_id):
            return index, task
    return None, None


def _create_task(tasks, payload):
    task = {
        key: value
        for key, value in payload.items()
        if key not in {"id", "uuid", "title", "description", "type", "priority", "status", "comments", "comment"}
    }
    task["id"] = str(payload.get("id") or _next_task_id(tasks)).strip()
    task["uuid"] = str(payload.get("uuid") or uuid.uuid4())
    task["title"] = str(payload.get("title", "")).strip()
    task["description"] = _split_description(payload.get("description", ""))
    task["type"] = str(payload.get("type") or "Feature").strip() or "Feature"
    task["priority"] = str(payload.get("priority") or "Medium").strip() or "Medium"
    task["status"] = str(payload.get("status") or "ToDo").strip() or "ToDo"

    comments = payload.get("comments") or []
    if not isinstance(comments, list):
        comments = [comments]
    task["comments"] = [
        _normalise_comment(comment)
        for comment in comments
        if str(comment or "").strip()
    ]

    comment = payload.get("comment")
    if comment:
        task["comments"].append(_normalise_comment(comment))

    tasks.append(task)
    return task


def _update_task(tasks, task_id, payload):
    index, task = _find_task(tasks, task_id)
    if task is None:
        raise KeyError(task_id)

    for field in ("title", "type", "priority", "status"):
        if field in payload and payload[field] is not None:
            value = str(payload[field]).strip()
            if value:
                task[field] = value

    if "description" in payload and payload["description"] is not None:
        task["description"] = _split_description(payload["description"])

    if "comments" in payload and payload["comments"] is not None:
        comments = payload["comments"]
        if not isinstance(comments, list):
            comments = [comments]
        task["comments"] = [
            _normalise_comment(comment)
            for comment in comments
            if str(comment or "").strip()
        ]

    if payload.get("comment"):
        task.setdefault("comments", []).append(_normalise_comment(payload["comment"]))

    tasks[index] = task
    return task


def _append_comment(tasks, task_id, payload):
    _, task = _find_task(tasks, task_id)
    if task is None:
        raise KeyError(task_id)

    comment = payload.get("comment", payload)
    task.setdefault("comments", []).append(_normalise_comment(comment))
    return task


def _tasks_summary(tasks):
    summary = {}
    for task in tasks:
        status = str(task.get("status") or "ToDo").strip() or "ToDo"
        summary[status] = summary.get(status, 0) + 1
    return summary


def _render_index_html(tasks_path):
    """Render the single-page task manager UI."""
    escaped_path = html.escape(str(Path(tasks_path).resolve()))
    return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>BaseApp Task Manager</title>
    <style>
        :root {{
            --line: rgba(148, 163, 184, 0.28);
            --text: #e5e7eb;
            --muted: #94a3b8;
            --accent: #38bdf8;
            --accent-2: #22c55e;
            --shadow: 0 16px 40px rgba(15, 23, 42, 0.24);
            font-family: "Segoe UI", Arial, sans-serif;
        }}
        * {{ box-sizing: border-box; }}
        body {{ margin: 0; background: linear-gradient(135deg, #0f172a 0%, #111827 45%, #0f172a 100%); color: var(--text); }}
        header {{ padding: 32px 24px 18px; border-bottom: 1px solid var(--line); background: rgba(15, 23, 42, 0.75); backdrop-filter: blur(10px); position: sticky; top: 0; z-index: 2; }}
        h1 {{ margin: 0 0 8px; font-size: 2rem; letter-spacing: 0.02em; }}
        .subtle {{ color: var(--muted); margin: 0; }}
        main {{ display: grid; gap: 18px; padding: 18px 24px 28px; grid-template-columns: minmax(320px, 380px) 1fr; align-items: start; }}
        .panel {{ background: rgba(17, 24, 39, 0.88); border: 1px solid var(--line); border-radius: 18px; box-shadow: var(--shadow); }}
        .panel h2 {{ margin: 0; padding: 18px 18px 0; font-size: 1.1rem; }}
        .panel .content {{ padding: 18px; }}
        form {{ display: grid; gap: 12px; }}
        label {{ display: grid; gap: 6px; font-size: 0.92rem; color: var(--text); }}
        input, select, textarea, button {{ border-radius: 12px; border: 1px solid var(--line); background: rgba(15, 23, 42, 0.75); color: var(--text); padding: 10px 12px; font: inherit; }}
        textarea {{ min-height: 92px; resize: vertical; }}
        button {{ cursor: pointer; font-weight: 600; }}
        .actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}
        .primary {{ background: linear-gradient(135deg, var(--accent), #0ea5e9); color: #fff; border-color: transparent; }}
        .secondary {{ background: rgba(30, 41, 59, 0.88); }}
        .stats {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }}
        .pill {{ border-radius: 999px; padding: 6px 12px; background: rgba(30, 41, 59, 0.92); color: var(--text); border: 1px solid var(--line); font-size: 0.85rem; }}
        .task-list {{ display: grid; gap: 14px; }}
        .task-card {{ padding: 16px; border: 1px solid var(--line); border-radius: 16px; background: rgba(15, 23, 42, 0.78); }}
        .task-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: start; margin-bottom: 12px; }}
        .task-id {{ color: var(--accent); font-size: 0.85rem; font-weight: 700; letter-spacing: 0.08em; }}
        .task-title {{ margin: 4px 0 0; font-size: 1.05rem; }}
        .task-grid {{ display: grid; gap: 10px; grid-template-columns: repeat(2, minmax(0, 1fr)); }}
        .task-grid .full {{ grid-column: 1 / -1; }}
        .task-meta {{ display: grid; gap: 4px; color: var(--muted); font-size: 0.85rem; }}
        .comments {{ margin-top: 12px; display: grid; gap: 10px; }}
        .comment {{ border-left: 3px solid rgba(56, 189, 248, 0.55); padding: 8px 12px; background: rgba(30, 41, 59, 0.55); border-radius: 10px; }}
        .comment strong {{ display: block; margin-bottom: 4px; }}
        .toolbar {{ display: flex; gap: 8px; flex-wrap: wrap; }}
        .muted {{ color: var(--muted); }}
        .stack {{ display: grid; gap: 12px; }}
        .msg {{ margin-top: 10px; color: var(--accent-2); min-height: 1.4em; }}
        .err {{ color: #fca5a5; }}
        @media (max-width: 960px) {{ main {{ grid-template-columns: 1fr; }} }}
    </style>
</head>
<body>
<header>
    <h1>BaseApp Task Manager</h1>
    <p class="subtle">Local editor for <span>{escaped_path}</span></p>
</header>
<main>
    <section class="panel">
        <h2>Create Task</h2>
        <div class="content">
            <form id="create-form">
                <label>Title <input name="title" required></label>
                <label>Description <textarea name="description" placeholder="Describe the task"></textarea></label>
                <label>Type <input name="type" value="Feature"></label>
                <label>Priority <input name="priority" value="Medium"></label>
                <label>Status
                    <select name="status">
                        <option>ToDo</option>
                        <option>InProgress</option>
                        <option>Done</option>
                    </select>
                </label>
                <label>Initial Comment <textarea name="comment" placeholder="Optional"></textarea></label>
                <div class="actions">
                    <button class="primary" type="submit">Create Task</button>
                    <button class="secondary" type="button" id="reload-btn">Reload</button>
                </div>
            </form>
            <div class="msg" id="create-msg"></div>
        </div>
    </section>
    <section class="panel">
        <h2>Tasks</h2>
        <div class="content">
            <div class="stats" id="stats"></div>
            <div class="task-list" id="task-list"></div>
        </div>
    </section>
</main>
<script>
const taskList = document.getElementById('task-list');
const statsEl = document.getElementById('stats');
const msgEl = document.getElementById('create-msg');
const createForm = document.getElementById('create-form');

function val(v) {{ return Array.isArray(v) ? v.join('\\n') : (v ?? ''); }}
function esc(v) {{ return String(v ?? ''); }}
function pill(t) {{
    const s = document.createElement('span'); s.className = 'pill'; s.textContent = t; return s;
}}
function renderStats(tasks) {{
    const c = tasks.reduce((a, t) => {{ const s = t.status || 'ToDo'; a[s] = (a[s]||0)+1; return a; }}, {{}});
    statsEl.innerHTML = '';
    statsEl.appendChild(pill('Total ' + tasks.length));
    Object.keys(c).sort().forEach(s => statsEl.appendChild(pill(s + ' ' + c[s])));
}}
function renderComments(task) {{
    const box = document.createElement('div'); box.className = 'comments';
    const comments = Array.isArray(task.comments) ? task.comments : [];
    if (!comments.length) {{
        const e = document.createElement('div'); e.className = 'muted'; e.textContent = 'No comments yet.'; box.appendChild(e); return box;
    }}
    comments.forEach(c => {{
        const item = document.createElement('div'); item.className = 'comment';
        const h = document.createElement('strong'); h.textContent = (c.author||'') + ' \u00b7 ' + (c.timestamp||'');
        const b = document.createElement('div'); b.textContent = c.content || '';
        item.append(h, b); box.appendChild(item);
    }});
    return box;
}}
function taskCard(task) {{
    const card = document.createElement('article'); card.className = 'task-card'; card.dataset.taskId = task.id;
    const form = document.createElement('form'); form.className = 'stack';
    const head = document.createElement('div'); head.className = 'task-head';
    const hd = document.createElement('div');
    hd.innerHTML = '<div class="task-id">' + esc(task.id) + '</div><h3 class="task-title"></h3>';
    hd.querySelector('.task-title').textContent = task.title || '';
    const meta = document.createElement('div'); meta.className = 'task-meta';
    meta.innerHTML = '<span>' + esc(task.uuid||'') + '</span><span>' + esc(task.status||'') + '</span>';
    head.append(hd, meta);
    const grid = document.createElement('div'); grid.className = 'task-grid';
    [['Title','title','input'],['Type','type','input'],['Priority','priority','input'],
     ['Status','status','select'],['Description','description','textarea','full'],
     ['Add Comment','comment','textarea','full']].forEach(([lbl, name, kind, xc]) => {{
        const w = document.createElement('label'); if (xc) w.className = xc; w.textContent = lbl;
        let ctrl;
        if (kind === 'textarea') {{ ctrl = document.createElement('textarea'); ctrl.name = name; ctrl.value = val(task[name]); }}
        else if (kind === 'select') {{
            ctrl = document.createElement('select'); ctrl.name = name;
            ['ToDo','InProgress','Done'].forEach(o => {{
                const opt = document.createElement('option'); opt.value = o; opt.textContent = o;
                if (o === (task[name]||'ToDo')) opt.selected = true; ctrl.appendChild(opt);
            }});
        }} else {{ ctrl = document.createElement('input'); ctrl.name = name; ctrl.value = val(task[name]); }}
        w.appendChild(ctrl); grid.appendChild(w);
    }});
    const tb = document.createElement('div'); tb.className = 'toolbar full';
    const sb = document.createElement('button'); sb.className = 'primary'; sb.type = 'button'; sb.textContent = 'Save Changes';
    sb.addEventListener('click', () => saveTask(task.id, card));
    const cb = document.createElement('button'); cb.className = 'secondary'; cb.type = 'button'; cb.textContent = 'Add Comment';
    cb.addEventListener('click', () => addComment(task.id, card));
    tb.append(sb, cb); grid.appendChild(tb);
    form.append(head, grid, renderComments(task)); card.appendChild(form);
    return card;
}}
async function loadTasks() {{
    const r = await fetch('/api/tasks');
    if (!r.ok) throw new Error('Load failed (' + r.status + ')');
    const p = await r.json();
    const tasks = Array.isArray(p.tasks) ? p.tasks : [];
    taskList.innerHTML = ''; renderStats(tasks);
    tasks.forEach(t => taskList.appendChild(taskCard(t)));
}}
async function createTask(ev) {{
    ev.preventDefault(); msgEl.className = 'msg'; msgEl.textContent = '';
    const r = await fetch('/api/tasks', {{ method: 'POST', headers: {{'Content-Type':'application/json'}}, body: JSON.stringify(Object.fromEntries(new FormData(createForm).entries())) }});
    if (!r.ok) throw new Error(await r.text() || 'Create failed (' + r.status + ')');
    createForm.reset(); createForm.status.value = 'ToDo'; createForm.type.value = 'Feature'; createForm.priority.value = 'Medium';
    msgEl.textContent = 'Task created.'; await loadTasks();
}}
async function saveTask(id, card) {{
    const r = await fetch('/api/tasks/' + encodeURIComponent(id), {{ method: 'PUT', headers: {{'Content-Type':'application/json'}}, body: JSON.stringify(Object.fromEntries(new FormData(card.querySelector('form')).entries())) }});
    if (!r.ok) throw new Error('Save failed (' + r.status + ')'); await loadTasks();
}}
async function addComment(id, card) {{
    const fd = Object.fromEntries(new FormData(card.querySelector('form')).entries());
    const r = await fetch('/api/tasks/' + encodeURIComponent(id) + '/comments', {{ method: 'POST', headers: {{'Content-Type':'application/json'}}, body: JSON.stringify({{comment: fd.comment}}) }});
    if (!r.ok) throw new Error('Comment failed (' + r.status + ')'); await loadTasks();
}}
createForm.addEventListener('submit', async ev => {{
    try {{ await createTask(ev); }} catch(e) {{ msgEl.className = 'msg err'; msgEl.textContent = e.message; }}
}});
document.getElementById('reload-btn').addEventListener('click', async () => {{
    try {{ await loadTasks(); msgEl.className = 'msg'; msgEl.textContent = 'Refreshed.'; }}
    catch(e) {{ msgEl.className = 'msg err'; msgEl.textContent = e.message; }}
}});
loadTasks().catch(e => {{ msgEl.className = 'msg err'; msgEl.textContent = e.message; }});
</script>
</body>
</html>
"""


class _TaskManagerHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the task manager JSON API and UI."""

    store = None

    def _send_json(self, status_code, payload):
        encoded = json.dumps(payload, ensure_ascii=False, indent=4).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def _get_tasks(self):
        return _load_tasks_store(self.__class__.store.tasks_path)

    def _write_tasks(self, data):
        _save_tasks_store(self.__class__.store.tasks_path, data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = _render_index_html(self.__class__.store.tasks_path).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/tasks":
            data = self._get_tasks()
            self._send_json(200, {"tasks": data["TASKS"], "summary": _tasks_summary(data["TASKS"]), "tasks_path": str(self.__class__.store.tasks_path)})
            return
        if parsed.path.startswith("/api/tasks/"):
            task_id = parsed.path.removeprefix("/api/tasks/").split("/", 1)[0]
            data = self._get_tasks()
            _, task = _find_task(data["TASKS"], task_id)
            if task is None:
                self._send_json(404, {"error": "task not found", "task_id": task_id})
                return
            self._send_json(200, {"task": task})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        data = self._get_tasks()
        if parsed.path == "/api/tasks":
            task = _create_task(data["TASKS"], self._read_body())
            self._write_tasks(data)
            self._send_json(201, {"task": task})
            return
        if parsed.path.startswith("/api/tasks/") and parsed.path.endswith("/comments"):
            task_id = parsed.path.removeprefix("/api/tasks/").removesuffix("/comments").strip("/")
            try:
                task = _append_comment(data["TASKS"], task_id, self._read_body())
            except KeyError:
                self._send_json(404, {"error": "task not found", "task_id": task_id})
                return
            self._write_tasks(data)
            self._send_json(200, {"task": task})
            return
        self._send_json(404, {"error": "not found"})

    def do_PUT(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/tasks/"):
            self._send_json(404, {"error": "not found"})
            return
        task_id = parsed.path.removeprefix("/api/tasks/").strip("/")
        data = self._get_tasks()
        try:
            task = _update_task(data["TASKS"], task_id, self._read_body())
        except KeyError:
            self._send_json(404, {"error": "task not found", "task_id": task_id})
            return
        self._write_tasks(data)
        self._send_json(200, {"task": task})

    def log_message(self, format, *args):
        return


class _TaskStore:
    """Container for the task manager's mutable state (tasks file path)."""

    def __init__(self, tasks_path):
        self.tasks_path = Path(tasks_path)


# Feature 3.6.1
def parse_args(argv=None):
    """Feature ID: 3.6.1. Parse CLI arguments for the local task manager."""
    parser = argparse.ArgumentParser(description="Run the BaseApp task manager UI.")
    parser.add_argument("--tasks-path", default=str(DEFAULT_TASKS_PATH), help="Path to build/tasks/base.json")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host interface to bind")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int, help="Port to bind")
    parser.add_argument("--open-browser", action="store_true", help="Open the UI in a browser after startup")
    return parser.parse_args(argv)


def _create_server(host, port, tasks_path):
    """Create and return a ThreadingHTTPServer bound to the given host/port."""
    store = _TaskStore(tasks_path)
    _TaskManagerHandler.store = store
    server = ThreadingHTTPServer((host, int(port)), _TaskManagerHandler)
    server.allow_reuse_address = True
    return server


# Feature 3.6.2
def run(argv=None):
    """Feature ID: 3.6.2. Run the local task manager web app."""
    args = parse_args(argv)
    server = _create_server(args.host, args.port, args.tasks_path)
    url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    if args.open_browser:
        webbrowser.open(url)
    try:
        print(f"Task manager listening at {url}")
        print(f"Editing {Path(args.tasks_path).resolve()}")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
