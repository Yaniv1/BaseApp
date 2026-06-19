"""Feature ID: 5.3.1.7. Task manager interface prep test module."""

import copy
import json
import shutil
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from scripts.task_manager import _append_comment, _create_server, _create_task, _load_tasks_store, _save_tasks_store, _update_task


def _build_result(status, message, criteria, features, data=None):
    payload = {
        "status": status,
        "message": message,
        "criteria": criteria,
        "features": features,
    }
    if data is not None:
        payload["data"] = data
    return payload


def _request_json(url, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json; charset=utf-8")
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body) if body else {}


def _request_text(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, response.read().decode("utf-8")


def test_task_manager_interface(manager=None, message=None, **kwargs):
    """Feature ID: 5.3.1.7.1. Verify the local task manager can load, create, update, and persist tasks.

    Covers features:
      - 3.6.1 (parse_args for the local task manager)
      - 3.6.2 (run/create server flow for the local task manager)
    """
    features = ["3.6.1", "3.6.2"]
    criteria = []
    workdir = tempfile.mkdtemp(prefix="basetask_ui_")
    source_tasks = Path(__file__).resolve().parents[3] / "build" / "tasks" / "base.json"
    temp_tasks = Path(workdir) / "base.json"
    shutil.copyfile(source_tasks, temp_tasks)

    server = None
    thread = None

    try:
        data = _load_tasks_store(temp_tasks)
        original_count = len(data["TASKS"])

        store_loaded = isinstance(data, dict) and isinstance(data.get("TASKS"), list) and original_count > 0
        criteria.append({
            "name": "task_store_loads_expected_json_shape",
            "operator": "eq",
            "actual": original_count,
            "expected": len(data["TASKS"]),
            "success": store_loaded,
            "status": "PASS" if store_loaded else "FAIL",
        })

        created = _create_task(data["TASKS"], {
            "title": "UI smoke task",
            "description": "First line\nSecond line",
            "type": "Feature",
            "priority": "Low",
            "status": "InProgress",
            "comment": "Seeded by the prep test",
        })
        _save_tasks_store(temp_tasks, data)
        reloaded = _load_tasks_store(temp_tasks)
        persisted = len(reloaded["TASKS"]) == original_count + 1 and any(task.get("id") == created["id"] for task in reloaded["TASKS"])
        criteria.append({
            "name": "create_task_persists_new_record",
            "operator": "eq",
            "actual": created["id"],
            "expected": created["id"],
            "success": persisted,
            "status": "PASS" if persisted else "FAIL",
        })

        updated = _update_task(reloaded["TASKS"], created["id"], {
            "title": "UI smoke task updated",
            "status": "Done",
            "description": "Updated description",
        })
        updated_ok = updated["title"] == "UI smoke task updated" and updated["status"] == "Done" and updated["description"] == "Updated description"
        criteria.append({
            "name": "update_task_changes_fields",
            "operator": "eq",
            "actual": updated.get("status"),
            "expected": "Done",
            "success": updated_ok,
            "status": "PASS" if updated_ok else "FAIL",
        })

        commented = _append_comment(reloaded["TASKS"], created["id"], {"comment": "Added through API", "author": "Copilot"})
        comment_count = len(commented.get("comments", []))
        comments_ok = comment_count >= 2 and any(comment.get("content") == "Added through API" for comment in commented.get("comments", []))
        criteria.append({
            "name": "append_comment_persists_comment",
            "operator": "eq",
            "actual": comment_count,
            "expected": comment_count,
            "success": comments_ok,
            "status": "PASS" if comments_ok else "FAIL",
        })

        _save_tasks_store(temp_tasks, reloaded)

        server = _create_server("127.0.0.1", 0, temp_tasks)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.2)
        port = server.server_address[1]
        root_status, root_html = _request_text(f"http://127.0.0.1:{port}/")
        api_status, api_payload = _request_json(f"http://127.0.0.1:{port}/api/tasks")
        http_ok = root_status == 200 and api_status == 200 and "BaseApp Task Manager" in root_html and any(task.get("id") == created["id"] for task in api_payload.get("tasks", []))
        criteria.append({
            "name": "http_interface_round_trip",
            "operator": "eq",
            "actual": {"root_status": root_status, "api_status": api_status},
            "expected": {"root_status": 200, "api_status": 200},
            "success": http_ok,
            "status": "PASS" if http_ok else "FAIL",
        })

        http_created_status, http_created = _request_json(f"http://127.0.0.1:{port}/api/tasks", method="POST", payload={
            "title": "HTTP created task",
            "description": "Created through the UI service",
            "type": "Bug",
            "priority": "High",
            "status": "ToDo",
            "comment": "Created through the HTTP endpoint",
        })
        http_created_id = http_created.get("task", {}).get("id")
        http_update_status, _ = _request_json(f"http://127.0.0.1:{port}/api/tasks/{http_created_id}", method="PUT", payload={
            "status": "InProgress",
            "priority": "Medium",
            "title": "HTTP created task updated",
        })
        http_comment_status, _ = _request_json(f"http://127.0.0.1:{port}/api/tasks/{http_created_id}/comments", method="POST", payload={
            "comment": "Persisted through the endpoint",
        })
        final_store = _load_tasks_store(temp_tasks)
        final_task = next(task for task in final_store["TASKS"] if task.get("id") == http_created_id)
        http_round_trip_ok = (
            http_created_status == 201
            and http_update_status == 200
            and http_comment_status == 200
            and http_created_id
            and final_task.get("status") == "InProgress"
            and final_task.get("title") == "HTTP created task updated"
            and any(comment.get("content") == "Persisted through the endpoint" for comment in final_task.get("comments", []))
        )
        criteria.append({
            "name": "http_endpoints_persist_changes",
            "operator": "eq",
            "actual": {
                "create": http_created_status,
                "update": http_update_status,
                "comment": http_comment_status,
            },
            "expected": {
                "create": 201,
                "update": 200,
                "comment": 200,
            },
            "success": http_round_trip_ok,
            "status": "PASS" if http_round_trip_ok else "FAIL",
        })

        overall = "PASS" if all(item["success"] for item in criteria) else "FAIL"
        return _build_result(
            status=overall,
            message=message or "Validated task manager UI round-trips against build/tasks/base.json",
            criteria=criteria,
            features=features,
            data={
                "original_count": original_count,
                "created_task_id": created["id"],
                "http_created_task_id": http_created_id,
            },
        )
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
            
"""Feature ID: 5.3.1.7. Task manager interface prep test module."""

import json
import shutil
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from scripts.task_manager import _append_comment, _create_server, _create_task, _load_tasks_store, _save_tasks_store, _update_task


def _build_result(status, message, criteria, features, data=None):
    payload = {
        "status": status,
        "message": message,
        "criteria": criteria,
        "features": features,
    }
    if data is not None:
        payload["data"] = data
    return payload


def _request_json(url, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json; charset=utf-8")
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body) if body else {}


def _request_text(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, response.read().decode("utf-8")


# Feature 5.3.1.7.1
def test_task_manager_interface(manager=None, message=None, **kwargs):
    """Feature ID: 5.3.1.7.1. Verify the local task manager can load, create, update, and persist tasks.

    Covers features:
      - 3.6.1 (parse_args for the local task manager)
      - 3.6.2 (run/create server flow for the local task manager)
    """
    features = ["3.6.1", "3.6.2"]
    criteria = []
    workdir = tempfile.mkdtemp(prefix="basetask_ui_")
    source_tasks = Path(__file__).resolve().parents[3] / "build" / "tasks" / "base.json"
    temp_tasks = Path(workdir) / "base.json"
    shutil.copyfile(source_tasks, temp_tasks)

    server = None
    thread = None

    try:
        data = _load_tasks_store(temp_tasks)
        original_count = len(data["TASKS"])

        store_loaded = isinstance(data, dict) and isinstance(data.get("TASKS"), list) and original_count > 0
        criteria.append({
            "name": "task_store_loads_expected_json_shape",
            "operator": "eq",
            "actual": original_count,
            "expected": len(data["TASKS"]) if isinstance(data, dict) else None,
            "success": store_loaded,
            "status": "PASS" if store_loaded else "FAIL",
        })

        created = _create_task(data["TASKS"], {
            "title": "UI smoke task",
            "description": "First line\nSecond line",
            "type": "Feature",
            "priority": "Low",
            "status": "InProgress",
            "comment": "Seeded by the prep test",
        })
        _save_tasks_store(temp_tasks, data)
        reloaded = _load_tasks_store(temp_tasks)
        persisted = len(reloaded["TASKS"]) == original_count + 1 and any(task.get("id") == created["id"] for task in reloaded["TASKS"])
        criteria.append({
            "name": "create_task_persists_new_record",
            "operator": "eq",
            "actual": created["id"],
            "expected": created["id"],
            "success": persisted,
            "status": "PASS" if persisted else "FAIL",
        })

        updated = _update_task(reloaded["TASKS"], created["id"], {
            "title": "UI smoke task updated",
            "status": "Done",
            "description": "Updated description",
        })
        updated_ok = updated["title"] == "UI smoke task updated" and updated["status"] == "Done" and updated["description"] == "Updated description"
        criteria.append({
            "name": "update_task_changes_fields",
            "operator": "eq",
            "actual": updated.get("status"),
            "expected": "Done",
            "success": updated_ok,
            "status": "PASS" if updated_ok else "FAIL",
        })

        commented = _append_comment(reloaded["TASKS"], created["id"], {"comment": "Added through API", "author": "Copilot"})
        comment_count = len(commented.get("comments", []))
        comments_ok = comment_count >= 2 and any(comment.get("content") == "Added through API" for comment in commented.get("comments", []))
        criteria.append({
            "name": "append_comment_persists_comment",
            "operator": "eq",
            "actual": comment_count,
            "expected": comment_count,
            "success": comments_ok,
            "status": "PASS" if comments_ok else "FAIL",
        })

        _save_tasks_store(temp_tasks, reloaded)

        server = _create_server("127.0.0.1", 0, temp_tasks)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.2)
        port = server.server_address[1]
        root_status, root_html = _request_text(f"http://127.0.0.1:{port}/")
        api_status, api_payload = _request_json(f"http://127.0.0.1:{port}/api/tasks")
        http_ok = root_status == 200 and api_status == 200 and "BaseApp Task Manager" in root_html and any(task.get("id") == created["id"] for task in api_payload.get("tasks", []))
        criteria.append({
            "name": "http_interface_round_trip",
            "operator": "eq",
            "actual": {"root_status": root_status, "api_status": api_status},
            "expected": {"root_status": 200, "api_status": 200},
            "success": http_ok,
            "status": "PASS" if http_ok else "FAIL",
        })

        http_created_status, http_created = _request_json(f"http://127.0.0.1:{port}/api/tasks", method="POST", payload={
            "title": "HTTP created task",
            "description": "Created through the UI service",
            "type": "Bug",
            "priority": "High",
            "status": "ToDo",
            "comment": "Created through the HTTP endpoint",
        })
        http_created_id = http_created.get("task", {}).get("id")
        http_update_status, _ = _request_json(f"http://127.0.0.1:{port}/api/tasks/{http_created_id}", method="PUT", payload={
            "status": "InProgress",
            "priority": "Medium",
            "title": "HTTP created task updated",
        })
        http_comment_status, _ = _request_json(f"http://127.0.0.1:{port}/api/tasks/{http_created_id}/comments", method="POST", payload={
            "comment": "Persisted through the endpoint",
        })
        final_store = _load_tasks_store(temp_tasks)
        final_task = next(task for task in final_store["TASKS"] if task.get("id") == http_created_id)
        http_round_trip_ok = (
            http_created_status == 201
            and http_update_status == 200
            and http_comment_status == 200
            and http_created_id
            and final_task.get("status") == "InProgress"
            and final_task.get("title") == "HTTP created task updated"
            and any(comment.get("content") == "Persisted through the endpoint" for comment in final_task.get("comments", []))
        )
        criteria.append({
            "name": "http_endpoints_persist_changes",
            "operator": "eq",
            "actual": {
                "create": http_created_status,
                "update": http_update_status,
                "comment": http_comment_status,
            },
            "expected": {
                "create": 201,
                "update": 200,
                "comment": 200,
            },
            "success": http_round_trip_ok,
            "status": "PASS" if http_round_trip_ok else "FAIL",
        })

        overall = "PASS" if all(item["success"] for item in criteria) else "FAIL"
        return _build_result(
            status=overall,
            message=message or "Validated task manager UI round-trips against build/tasks/base.json",
            criteria=criteria,
            features=features,
            data={
                "original_count": original_count,
                "created_task_id": created["id"],
                "http_created_task_id": http_created_id,
            },
        )
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)