"""Portable interactive review runtime contract tests."""
import copy
import hashlib
import json
import os
import re
import stat
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

from utils.reviewutils import (
    FeatureStatus, ReviewStore, atomic_json, code_eligible, feature_code_approvable, git_changes, migrate_review_terms,
    report_path, requirement_code_approvable, resolve_runtime, spec_eligible, with_code_approval_eligibility,
)
from scripts.code_review import parser, verify_health
from scripts.review_report_generator import generate_reports
from scripts.review_server import CodeApprovalQueue, ReviewHttpServer, SpecApprovalQueue, save_static_review_snapshot
from scripts.validate_code_review import validate_review
import scripts.task_manager as task_manager
from utils.baseutils import Params
from types import SimpleNamespace


TASK = "BASE-TASK-261011-9001"


@pytest.fixture
def repository():
    # Keep test repositories owned by this checkout, never in the system temp directory.
    root = Path.cwd() / "build" / "review_test_cases" / uuid.uuid4().hex / TASK
    root.mkdir(parents=True)
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)
    git("init", "-b", "main")
    git("config", "user.email", "review-test@example.invalid")
    git("config", "user.name", "Review test")
    (root / ".gitignore").write_text("build/review/\nresults/\nqueue/\n__pycache__/\n*.pyc\n", encoding="utf-8")
    (root / "sample.py").write_text("def first():\n    return 1\n\ndef second():\n    return 2\n", encoding="utf-8")
    features = {
        "1.1": {"type": "Review.Component", "name": "first", "path": "sample.py::first",
                "requirements": ["BASE-REQ-001"], "review": {"task_id": TASK}, "approval": {"status": "Ready"}},
        "1.2": {"type": "Review.Component", "name": "second", "path": "sample.py::second",
                "requirements": ["BASE-REQ-002"], "review": {"task_id": TASK}, "approval": {"status": "Ready"}},
        "2": {"type": "Review.Component", "name": "other task", "path": "sample.py",
              "review": {"task_id": "BASE-TASK-261011-9999"}}}
    atomic_json(root / "build" / "architecture" / "base.json", {"features": features})
    atomic_json(root / "build" / "requirements" / "base.json", {"REQUIREMENTS": [
        {"id": "BASE-REQ-001", "title": "First", "description": "Full first specification", "source": {"task": "source-task"}},
        {"id": "BASE-REQ-002", "title": "Second", "description": "Full second specification"}]})
    git("add", ".")
    git("commit", "-m", "Fixture baseline")
    (root / "sample.py").write_text("def first():\n    return 10\n\ndef second():\n    return 20\n", encoding="utf-8")
    try:
        yield root, ReviewStore(root, TASK), git
    finally:
        def remove_readonly(function, path, exception):
            os.chmod(path, stat.S_IWRITE)
            function(path)
        shutil.rmtree(root.parent, onerror=remove_readonly)


def approve(store, feature="1.1", requirement="BASE-REQ-001"):
    ledger = store.load()
    ledger = store.approve_spec_requirement(requirement, expected_revision=ledger["revision"])
    ledger = store.update_feature(feature, "approve_spec", expected_revision=ledger["revision"])
    return store.update_feature(feature, "approve", expected_revision=ledger["revision"])


def test_discovery_and_independent_spec_code_gates(repository):
    root, store, _ = repository
    ledger = store.initialize()
    assert set(ledger["features"]) == {"1.1", "1.2"}
    assert not ledger["uncovered_changes"]
    assert not spec_eligible(ledger)
    assert ledger["features"]["1.1"]["artifact_ready"]
    assert not ledger["artifacts"]["sample.py"]["ready_to_commit"]
    ledger = store.update_feature("1.1", "approve", expected_revision=ledger["revision"])
    assert not spec_eligible(ledger)
    assert ledger["features"]["1.1"]["spec_status"] != FeatureStatus.SPEC_APPROVED
    assert ledger["features"]["1.1"]["status"] == FeatureStatus.CODE_APPROVED
    assert ledger["features"]["1.2"]["status"] == FeatureStatus.READY
    assert not code_eligible(ledger)
    assert not ledger["artifacts"]["sample.py"]["ready_to_commit"]
    ledger = store.load()
    ledger = store.update_feature("1.2", "approve", expected_revision=ledger["revision"])
    assert code_eligible(ledger)
    assert not spec_eligible(ledger)
    assert ledger["artifacts"]["sample.py"]["ready_to_commit"]
    restarted = ReviewStore(root, TASK).load()
    assert restarted == ledger
    assert restarted["features"]["1.1"]["comments"][0]["text"]


def test_browser_first_component_approval_has_no_circular_file_gate(repository):
    root, store, _ = repository
    ledger = store.load()
    assert ledger["features"]["1.1"]["artifact_ready"]
    assert not ledger["artifacts"]["sample.py"]["ready_to_commit"]
    generate_reports(store, root / "results")
    page = report_path(root / "results", TASK).read_text(encoding="utf-8")
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable for isolated browser-control evaluation.")
    data = re.search(r'<script id="review-data" type="application/json">(.*?)</script>', page, re.S)[1]
    controls = re.search(r"""    card\.querySelector\('\[data-review-action="approve_spec"\]'\)\.disabled =.*?\n  \}\);""", page, re.S)[0].rsplit("\n  });", 1)[0]
    program = f"""
const assert=require('node:assert/strict');
const ledger={data};
const entry=ledger.features['1.1'], status=entry.status;
const approve={{disabled:null}}, approveSpec={{disabled:null}};
const card={{querySelector:selector=>selector.includes('approve_spec')?approveSpec:approve}};
{controls}
assert.equal(approve.disabled,false);
assert.equal(ledger.can_code,false);
"""
    subprocess.run([node, "-e", program], check=True, capture_output=True, text=True)
    ledger = store.update_feature("1.1", "approve", expected_revision=ledger["revision"])
    assert ledger["features"]["1.1"]["status"] == "CodeApproved"
    assert not ledger["artifacts"]["sample.py"]["ready_to_commit"]


@pytest.mark.parametrize("status", ["Specified", "InProgress", "SpecApproved", "Ready", "Revised"])
def test_explicit_code_approval_has_no_status_or_spec_prerequisite(repository, status):
    root, store, _ = repository
    architecture = json.loads(store.architecture_path.read_text())
    architecture["features"]["1.1"]["approval"]["status"] = status
    atomic_json(store.architecture_path, architecture)
    ledger = store.load()
    assert ledger["features"]["1.1"]["status"] == status
    assert feature_code_approvable(ledger["features"]["1.1"])
    specs = copy.deepcopy(ledger["spec_reviews"])
    generate_reports(store, root / "results")
    page = report_path(root / "results", TASK).read_text(encoding="utf-8")
    data = re.search(r'<script id="review-data" type="application/json">(.*?)</script>', page, re.S)[1]
    browser = json.loads(data)
    assert browser["features"]["1.1"]["can_approve_code"]
    assert browser["spec_reviews"]["BASE-REQ-001"]["can_approve_code"]
    node = shutil.which("node")
    if node:
        controls = re.search(r"""    card\.querySelector\('\[data-review-action="approve_spec"\]'\)\.disabled =.*?\n  \}\);""",
                             page, re.S)[0].rsplit("\n  });", 1)[0]
        subprocess.run([node, "-e", f"""
const assert=require('node:assert/strict'), ledger={data};
const entry=ledger.features['1.1'], status=entry.status;
const approve={{disabled:null}}, approveSpec={{disabled:null}};
const card={{querySelector:s=>s.includes('approve_spec')?approveSpec:approve}};
{controls}
assert.equal(approve.disabled,false);
assert.equal(ledger.can_spec,false);
"""], check=True, capture_output=True, text=True)
    ledger = store.update_feature("1.1", "approve", expected_revision=ledger["revision"])
    feature = ledger["features"]["1.1"]
    assert feature["status"] == "CodeApproved"
    assert feature["approved_source_fingerprint"] == feature["source_fingerprint"]
    assert feature["approved_code_spec_fingerprint"] == feature["spec_fingerprint"]
    assert feature["spec_status"] == "Specified"
    assert "approved_spec_fingerprint" not in feature
    assert ledger["spec_reviews"] == specs
    assert feature["comments"][-1]["text"] == "Approved by reviewer."


def test_actual_task_specified_components_with_source_have_code_action(monkeypatch):
    # Reproduce fresh discovery from real task metadata without changing its reviewer history.
    store = ReviewStore(Path.cwd(), "BASE-TASK-261011-0001")
    if not store.review_path.is_file():
        pytest.skip("Actual task ledger is unavailable.")
    import utils.reviewutils as reviewutils
    original = reviewutils.read_json
    monkeypatch.setattr(reviewutils, "read_json", lambda path: {} if Path(path) == store.review_path else original(path))
    with store.transaction():
        before = store.review_path.read_bytes()
        ledger = store._initialize(persist=False)
        assert store.review_path.read_bytes() == before
    specified = [entry for entry in ledger["features"].values()
                 if entry["status"] == "Specified" and entry["artifact_ready"] and not (
                     entry.get("revision_request") or {}).get("state") in {"pending", "processing"}]
    assert specified, "Regression requires real task-owned Specified features with selected source."
    browser = with_code_approval_eligibility(ledger)
    assert all(browser["features"][entry["feature_id"]]["can_approve_code"] for entry in specified)


def add_thread_features(store):
    architecture = json.loads(store.architecture_path.read_text())
    architecture["features"]["1.1"]["approval"]["status"] = "Specified"
    architecture["features"]["1.3"] = {
        "type": "Review.Component", "name": "Shared hidden implementation", "path": "sample.py::second",
        "requirements": ["BASE-REQ-002", "BASE-REQ-001"], "review": {"task_id": TASK, "status": "InProgress"}}
    atomic_json(store.architecture_path, architecture)
    return store.load()


def test_thread_code_approval_atomic_single_publish_all_linked_features(repository, monkeypatch):
    root, store, _ = repository
    ledger = add_thread_features(store)
    assert ledger["features"]["1.3"]["requirement_id"] == "BASE-REQ-002"
    assert requirement_code_approvable(ledger, "BASE-REQ-001")
    specs = copy.deepcopy(ledger["spec_reviews"])
    second = copy.deepcopy(ledger["features"]["1.2"])
    import utils.reviewutils as reviewutils
    original = reviewutils.atomic_json
    writes = []
    def track_write(path, document):
        writes.append(Path(path))
        original(path, document)
    monkeypatch.setattr(reviewutils, "atomic_json", track_write)
    revision = ledger["revision"]
    ledger = store.approve_code_requirement("BASE-REQ-001", expected_revision=revision)
    assert writes == [store.review_path]
    assert ledger["revision"] == revision + 1
    assert ledger["spec_reviews"] == specs
    assert ledger["features"]["1.2"] == second
    assert len(ledger["events"]) == 2
    assert len({event["id"] for event in ledger["events"]}) == 2
    for identifier in ("1.1", "1.3"):
        feature = ledger["features"][identifier]
        assert feature["status"] == "CodeApproved"
        assert feature["spec_status"] == "Specified"
        assert "approved_spec_fingerprint" not in feature
        assert feature["approved_source_fingerprint"] == feature["source_fingerprint"]
        assert feature["comments"][-1]["feature_id"] == identifier
        assert feature["comments"][-1]["requirement_id"] == "BASE-REQ-001"
        assert feature["comments"][-1]["text"] == "Approved by reviewer."
    assert json.loads(store.review_path.read_text()) == ledger
    assert not requirement_code_approvable(ledger, "BASE-REQ-001")
    before = store.review_path.read_bytes()
    assert store.approve_code_requirement("BASE-REQ-001", expected_revision=ledger["revision"]) == ledger
    assert store.review_path.read_bytes() == before


def test_thread_code_approval_skips_approved_and_removed_without_spec_changes(repository):
    _, store, _ = repository
    ledger = add_thread_features(store)
    architecture = json.loads(store.architecture_path.read_text())
    architecture["features"]["1.4"] = {
        "name": "Removed implementation", "path": "missing.py", "requirements": ["BASE-REQ-001"],
        "review": {"task_id": TASK}, "approval": {"status": "Removed"}}
    atomic_json(store.architecture_path, architecture)
    ledger = store.load()
    ledger = store.update_feature("1.1", "approve", expected_revision=ledger["revision"])
    skipped = {identifier: copy.deepcopy(ledger["features"][identifier]) for identifier in ("1.1", "1.4")}
    specs = copy.deepcopy(ledger["spec_reviews"])
    ledger = store.approve_code_requirement("BASE-REQ-001", "Thread accepted", ledger["revision"])
    assert ledger["features"]["1.3"]["status"] == "CodeApproved"
    assert ledger["features"]["1.3"]["comments"][-1]["text"] == "Thread accepted"
    assert all(ledger["features"][identifier] == feature for identifier, feature in skipped.items())
    assert ledger["spec_reviews"] == specs


@pytest.mark.parametrize("blocked", ["pending", "processing", "missing-source", "missing-discovery"])
def test_thread_code_approval_prevalidation_rolls_back_and_browser_disables(repository, blocked):
    root, store, _ = repository
    ledger = add_thread_features(store)
    if blocked in {"pending", "processing"}:
        ledger = store.update_feature("1.3", "revise", "Fix it", ledger["revision"])
        if blocked == "processing":
            request = ledger["features"]["1.3"]["revision_request"]["request_id"]
            ledger = store.transition_revision("1.3", request, "processing", expected_revision=ledger["revision"])
    else:
        architecture = json.loads(store.architecture_path.read_text())
        if blocked == "missing-source":
            architecture["features"]["1.3"]["path"] = "missing.py::absent"
        else:
            del architecture["features"]["1.3"]
        atomic_json(store.architecture_path, architecture)
        ledger = store.load()
    before = store.review_path.read_bytes()
    components = store.review_path.with_name("components.json").read_bytes()
    assert feature_code_approvable(ledger["features"]["1.1"])
    assert not feature_code_approvable(ledger["features"]["1.3"])
    assert not requirement_code_approvable(ledger, "BASE-REQ-001")
    with pytest.raises(RuntimeError, match="selected implementation source"):
        store.approve_code_requirement("BASE-REQ-001", expected_revision=ledger["revision"])
    assert store.review_path.read_bytes() == before
    assert store.review_path.with_name("components.json").read_bytes() == components
    with pytest.raises(RuntimeError):
        store.update_feature("1.3", "approve", expected_revision=ledger["revision"])
    generate_reports(store, root / "results")
    page = report_path(root / "results", TASK).read_text(encoding="utf-8")
    data = re.search(r'<script id="review-data" type="application/json">(.*?)</script>', page, re.S)[1]
    browser = json.loads(data)
    assert not browser["features"]["1.3"]["can_approve_code"]
    assert not browser["spec_reviews"]["BASE-REQ-001"]["can_approve_code"]
    assert re.search(r'data-code-action="approve" disabled', page)
    node = shutil.which("node")
    if node:
        controls = re.search(r"""    card\.querySelector\('\[data-review-action="approve_spec"\]'\)\.disabled =.*?\n  \}\);""",
                             page, re.S)[0].rsplit("\n  });", 1)[0]
        thread_control = re.search(r"""    thread\.querySelector\('\[data-code-action\]'\)\.disabled =.*?;""", page, re.S)[0]
        subprocess.run([node, "-e", f"""
const assert=require('node:assert/strict'), ledger={data};
const entry=ledger.features['1.3'], status=entry.status, specReview=ledger.spec_reviews['BASE-REQ-001'];
const approve={{disabled:null}}, approveSpec={{disabled:null}}, batch={{disabled:null}};
const card={{querySelector:s=>s.includes('approve_spec')?approveSpec:approve}};
const thread={{querySelector:()=>batch}};
{controls}
{thread_control}
assert.equal(approve.disabled,true);assert.equal(batch.disabled,true);
"""], check=True, capture_output=True, text=True)


def test_thread_code_approval_stale_revision_and_source_refresh_publish_nothing(repository):
    root, store, _ = repository
    ledger = add_thread_features(store)
    stale = ledger["revision"]
    ledger = store.update_feature("1.2", "approve", expected_revision=stale)
    before = store.review_path.read_bytes()
    with pytest.raises(RuntimeError, match="changed"):
        store.approve_code_requirement("BASE-REQ-001", expected_revision=stale)
    assert store.review_path.read_bytes() == before
    (root / "sample.py").write_text("def first():\n    return 11\n\ndef second():\n    return 20\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed"):
        store.approve_code_requirement("BASE-REQ-001", expected_revision=ledger["revision"])
    assert store.review_path.read_bytes() == before


def test_thread_code_approval_persistence_failure_leaves_no_partial_decisions(repository, monkeypatch):
    _, store, _ = repository
    ledger = add_thread_features(store)
    before = store.review_path.read_bytes()
    import utils.reviewutils as reviewutils
    def fail(path, document):
        assert Path(path) == store.review_path
        assert document["features"]["1.1"]["status"] == "CodeApproved"
        assert document["features"]["1.3"]["status"] == "CodeApproved"
        raise OSError("Publication unavailable")
    monkeypatch.setattr(reviewutils, "atomic_json", fail)
    with pytest.raises(OSError, match="Publication unavailable"):
        store.approve_code_requirement("BASE-REQ-001", expected_revision=ledger["revision"])
    assert store.review_path.read_bytes() == before
    assert store.cached_snapshot() == ledger


def test_browser_thread_code_action_uses_requirement_not_visible_cards(repository):
    root, store, _ = repository
    add_thread_features(store)
    generate_reports(store, root / "results")
    page = report_path(root / "results", TASK).read_text(encoding="utf-8")
    data = re.search(r'<script id="review-data" type="application/json">(.*?)</script>', page, re.S)[1]
    assert json.loads(data)["spec_reviews"]["BASE-REQ-001"]["code_feature_ids"] == ["1.1", "1.3"]
    assert page.count('data-code-action="approve"') == 2
    assert "including hidden features" in page
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable for isolated browser action evaluation.")
    action = re.search(r"async function submitThreadCodeApproval\(button\) \{.*?\n\}", page, re.S)[0]
    scripts = re.findall(r'<script(?![^>]*type="application/json")[^>]*>(.*?)</script>', page, re.S)
    subprocess.run([node, "-e", f"""
const assert=require('node:assert/strict'), ledger={data};
{json.dumps(scripts)}.forEach(source=>new(require('node:vm').Script)(source));
const REVIEW_API_BASE='', REVIEW_TASK_ID=ledger.task_id, reviewRevision=ledger.revision;
let currentReviewLedger=ledger, applied=null, request=null;
const message={{}}, batch={{disabled:false,closest:()=>thread}};
const thread={{dataset:{{requirementId:'BASE-REQ-001',featureIds:'["1.1"]'}},querySelector:()=>message}};
const applyReviewLedger=value=>{{applied=value;batch.disabled=true;}};
const loadReviewLedger=async()=>{{}};
const fetch=async(url,options)=>{{request={{url,options}};return {{ok:true,json:async()=>ledger}};}};
{action}
submitThreadCodeApproval(batch).then(()=>{{
  assert.equal(request.url,'/api/review/requirements/BASE-REQ-001/code');
  assert.equal(request.options.headers['X-Review-Task'],REVIEW_TASK_ID);
  assert.deepEqual(JSON.parse(request.options.body),{{comment:'Approved by reviewer.',expected_revision:reviewRevision}});
  assert.equal(applied,ledger);assert.equal(batch.disabled,true);
}}).catch(error=>{{console.error(error);process.exitCode=1;}});
"""], check=True, capture_output=True, text=True)


def test_http_thread_code_approval_cas_atomic_scope_and_no_spec(repository):
    root, store, _ = repository
    ledger = add_thread_features(store)
    generate_reports(store, root / "results")
    server = ReviewHttpServer(("127.0.0.1", 0), store, report_path(root / "results", TASK),
        CodeApprovalQueue(root / "queue" / "pending", root / "tasks.json", TASK),
        SpecApprovalQueue(root / "queue" / "pending", root / "tasks.json", TASK))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    def post(revision, requirement="BASE-REQ-001"):
        request = urllib.request.Request(url + f"/api/review/requirements/{requirement}/code",
            data=json.dumps({"expected_revision": revision}).encode(), method="POST",
            headers={"X-Review-Task": TASK, "Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(request).read())
    try:
        with urllib.request.urlopen(url + "/api/review") as response:
            browser = json.loads(response.read())
        assert browser["features"]["1.1"]["can_approve_code"]
        assert browser["spec_reviews"]["BASE-REQ-001"]["can_approve_code"]
        ledger = post(ledger["revision"])
        assert ledger["features"]["1.1"]["status"] == "CodeApproved"
        assert ledger["features"]["1.3"]["status"] == "CodeApproved"
        assert ledger["features"]["1.2"]["status"] == "Ready"
        assert all(entry["status"] == "Specified" for entry in ledger["spec_reviews"].values())
        assert not ledger["can_spec"]
        assert not ledger["spec_reviews"]["BASE-REQ-001"]["can_approve_code"]
        before = store.review_path.read_bytes()
        with pytest.raises(urllib.error.HTTPError) as error:
            post(ledger["revision"] - 1)
        assert error.value.code == 409
        assert store.review_path.read_bytes() == before
        ledger = store.update_feature("1.2", "revise", "Fix it", ledger["revision"])
        before = store.review_path.read_bytes()
        with pytest.raises(urllib.error.HTTPError) as error:
            post(ledger["revision"], "BASE-REQ-002")
        assert error.value.code == 409
        assert store.review_path.read_bytes() == before
        with pytest.raises(urllib.error.HTTPError) as error:
            post(ledger["revision"], "missing")
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_browser_status_filter_is_persistent_multichoice(repository):
    root, store, _ = repository
    generate_reports(store, root / "results")
    page = report_path(root / "results", TASK).read_text(encoding="utf-8")
    assert page.count('<input type="checkbox" data-status-filter') == 7
    assert "JSON.stringify(Array.from(selected))" in page
    assert "Array.isArray(selected)" in page
    assert "showAllThreadFeatures || selected.has(status)" in page
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable for isolated browser-filter evaluation.")
    functions = "\n".join(re.search(r'function ' + name + r'\([^)]*\) \{.*?\n\}', page, re.S)[0]
        for name in ("featureStatus", "selectedFeatureStatuses", "selectedFeatureDisplayMode", "applyStatusFilter", "restoreStatusFilter"))
    program = f"""
const assert=require('node:assert/strict');
const STATUS_FILTER_KEY='filters', FEATURE_DISPLAY_MODE_KEY='display';
const storage=new Map([['filters',JSON.stringify(['Ready','Revised'])],['display','matching-features']]);
const localStorage={{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)}};
const filters=['Ready','Revised','Specified','CodeApproved'].map(value=>({{value,checked:false}}));
const modes=['matching-features','all-thread-features'].map(value=>({{value,checked:false}}));
const card=(id,status)=>({{dataset:{{featureId:id,featureStatus:status}},style:{{}}}});
const cards=[card('ready'),card('specified'),card('revised')], other=[card('approved')];
const thread=(id,items)=>({{id,dataset:{{featureIds:JSON.stringify(items.map(c=>c.dataset.featureId))}},
 style:{{}},querySelectorAll:()=>items}});
const threads=[thread('first',cards),thread('second',other),thread('empty',[])];
const links={{first:{{style:{{}}}},second:{{style:{{}}}},empty:{{style:{{}}}}}};
let currentReviewLedger={{features:{{ready:{{status:'Ready'}},specified:{{status:'Specified'}},
 revised:{{status:'Revised'}},approved:{{status:'CodeApproved'}}}}}};
const document={{
 querySelectorAll:selector=>selector==='[data-status-filter]'?filters:
 selector==='[data-status-filter]:checked'?filters.filter(f=>f.checked):
 selector==='[data-feature-display-mode]'?modes:threads,
 querySelector:selector=>selector.startsWith('nav')?links[threads.find(t=>selector.includes(t.id)).id]:
 modes.find(m=>m.checked)
}};
{functions}
restoreStatusFilter();
assert.deepEqual([...selectedFeatureStatuses()],['Ready','Revised']);
assert.equal(cards[0].hidden,false);assert.equal(cards[1].hidden,true);assert.equal(cards[2].hidden,false);
assert.equal(threads[1].hidden,true);assert.equal(links.second.hidden,true);
assert.equal(threads[2].hidden,true);assert.equal(links.empty.hidden,true);
modes[0].checked=false;modes[1].checked=true;applyStatusFilter();
assert.equal(cards[1].hidden,false);
assert.deepEqual(JSON.parse(storage.get('filters')),['Ready','Revised']);
assert.equal(storage.get('display'),'all-thread-features');
filters.forEach(f=>f.checked=false);applyStatusFilter();
assert.ok(threads.every(t=>t.hidden));assert.ok(Object.values(links).every(l=>l.hidden));
assert.ok(cards.every(c=>c.hidden));assert.ok(other.every(c=>c.hidden));
currentReviewLedger=null;
cards[0].dataset.featureStatus='Ready';cards[1].dataset.featureStatus='Specified';
cards[2].dataset.featureStatus='Revised';other[0].dataset.featureStatus='CodeApproved';
filters[0].checked=true;modes[0].checked=true;modes[1].checked=false;applyStatusFilter();
assert.equal(threads[0].hidden,false);assert.equal(threads[1].hidden,true);
assert.equal(cards[0].hidden,false);assert.equal(cards[1].hidden,true);assert.equal(cards[2].hidden,true);
"""
    subprocess.run([node, "-e", program], check=True, capture_output=True, text=True)


def test_fingerprints_invalidate_only_changed_component(repository):
    root, store, _ = repository
    approve(store)
    ledger = approve(store, "1.2", "BASE-REQ-002")
    (root / "sample.py").write_text("def first():\n    return 11\n\ndef second():\n    return 20\n", encoding="utf-8")
    ledger = store.load()
    assert ledger["features"]["1.1"]["status"] != "CodeApproved"
    assert ledger["features"]["1.2"]["status"] == "CodeApproved"
    assert ledger["features"]["1.1"]["spec_status"] == "SpecApproved"
    requirement_path = root / "build" / "requirements" / "base.json"
    document = json.loads(requirement_path.read_text())
    document["REQUIREMENTS"][0]["description"] = "Changed specification"
    atomic_json(requirement_path, document)
    ledger = store.load()
    assert ledger["features"]["1.1"]["spec_status"] == "Specified"
    assert ledger["spec_reviews"]["BASE-REQ-001"]["status"] == "Specified"


def test_revision_conflicts_migration_and_terminal_removal(repository):
    root, store, _ = repository
    ledger = store.load()
    old_revision = ledger["revision"]
    ledger = store.update_feature("1.1", "revise", "Fix this", old_revision)
    with pytest.raises(RuntimeError, match="changed"):
        ReviewStore(root, TASK).update_feature("1.2", "revise", "Other change", old_revision)
    request = ledger["features"]["1.1"]["revision_request"]["request_id"]
    with pytest.raises(RuntimeError):
        store.update_feature("1.1", "revise", "Again", ledger["revision"])
    ledger = store.transition_revision("1.1", request, "processing", expected_revision=ledger["revision"])
    with pytest.raises(RuntimeError):
        store.transition_revision("1.1", request, "processing", expected_revision=ledger["revision"])
    ledger = store.transition_revision("1.1", request, "completed", "Fixed", ledger["revision"])
    assert ledger["features"]["1.1"]["status"] == "Revised"
    ledger = store.update_feature("1.1", "remove", "No longer needed", ledger["revision"])
    with pytest.raises(RuntimeError, match="terminal"):
        store.update_feature("1.1", "approve_spec", expected_revision=ledger["revision"])
    assert ReviewStore(root, TASK).load()["features"]["1.1"]["status"] == "Removed"
    legacy = {"features": {"x": {"status": "Revisited", "revisit": {"state": "pending"},
                                "comments": [{"action": "revisit"}]}}}
    assert migrate_review_terms(legacy)
    assert legacy["features"]["x"]["status"] == "Revised"
    assert legacy["features"]["x"]["revision_request"]["state"] == "pending"


def test_cross_process_revision_compare_and_swap(repository):
    root, store, _ = repository
    revision = store.load()["revision"]
    program = (
        "import sys;sys.path.insert(0,sys.argv[1]);"
        "from utils.reviewutils import ReviewStore;"
        "store=ReviewStore(sys.argv[2],sys.argv[3]);"
        "store.update_feature(sys.argv[4],'revise','Process revision',int(sys.argv[5]))"
    )
    import sys
    args = [sys.executable, "-c", program, str(Path.cwd()), str(root), TASK]
    children = [subprocess.Popen(args + [feature, str(revision)], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True) for feature in ("1.1", "1.2")]
    try:
        outputs = [child.communicate(timeout=30) for child in children]
        assert sorted(child.returncode for child in children) == [0, 1]
        assert any("review changed" in stderr.lower() for _, stderr in outputs)
        assert len(store.pending_revisions()) == 1
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()


def test_empty_task_bootstraps_but_never_approves(repository):
    root, store, _ = repository
    document = json.loads(store.architecture_path.read_text())
    for node in document["features"].values():
        node["review"]["task_id"] = "BASE-TASK-261011-9998"
    atomic_json(store.architecture_path, document)
    metadata = generate_reports(store, root / "results")
    ledger = store.load()
    assert not ledger["features"] and not code_eligible(ledger) and not spec_eligible(ledger)
    page = report_path(root / "results", TASK).read_text(encoding="utf-8")
    assert "No task-owned review components" in page
    assert '"can_code": false' in page
    assert metadata["reports"]["code"]["hash"]


def test_artifact_readiness_and_exact_untracked_coverage(repository):
    root, store, _ = repository
    (root / "extra.py").write_text("unreviewed = True\n", encoding="utf-8")
    ledger = store.load()
    assert any(change["path"] == "extra.py" for change in ledger["uncovered_changes"])
    (root / "sample.py").unlink()
    ledger = store.load()
    assert not ledger["features"]["1.1"]["artifact_ready"]
    assert not code_eligible(ledger)


def test_untracked_nested_repository_directories_are_not_read(repository):
    root, store, _ = repository
    nested = root / "pytest-current"
    nested.mkdir()
    subprocess.run(["git", "-C", str(nested), "init", "-q"], check=True, capture_output=True)
    (nested / "fixture.txt").write_text("fixture", encoding="utf-8")
    changes = git_changes(root, "main")
    assert not any(name.startswith("pytest-current") for name in changes)
    assert store.initialize()["features"]


def test_numbering_and_imported_ordinary_task_components(repository):
    root, store, _ = repository
    ledger = store.load()
    first = ledger["features"]["1.1"]["number"]
    manifest = root / "build" / "architecture" / "base.json"
    document = json.loads(manifest.read_text())
    document["features"]["0.1"] = {"name": "Imported ordinary symbol", "type": "Folder.PyFile.Function",
        "path": "sample.py::first", "review": {"task_id": TASK}, "requirements": ["BASE-REQ-001"]}
    atomic_json(manifest, document)
    ledger = store.load()
    assert ledger["features"]["1.1"]["number"] == first
    assert ledger["features"]["0.1"]["number"] > ledger["features"]["1.2"]["number"]
    assert set(ledger["features"]) == {"0.1", "1.1", "1.2"}


def test_config_precedence_and_report_ownership(repository):
    root, _, _ = repository
    config = Params({"COMMON": {"APP_NAME": "MyApp"}, "APP": {"TASK_MANAGER": {
        "result_store": "results", "status_queue": "queue", "review": {"baseline_ref": "main"}}}})
    args = parser().parse_args(["--root", str(root), "--task-id", TASK])
    runtime = resolve_runtime(args, config)
    assert runtime["layer"] == "base"
    assert runtime["architecture"].name == "base.json"
    args = parser().parse_args(["--root", str(root), "--task-id", TASK, "--layer", "app",
        "--architecture", "custom.json", "--requirements", "custom-req.json", "--results-root", "explicit",
        "--status-queue", "explicit-queue", "--baseline-ref", "HEAD"])
    runtime = resolve_runtime(args, config)
    assert runtime["layer"] == "app"
    assert runtime["architecture"] == root / "custom.json"
    assert runtime["requirements"] == root / "custom-req.json"
    assert runtime["results_root"] == root / "explicit"
    assert runtime["status_queue"] == root / "explicit-queue"
    assert runtime["baseline_ref"] == "HEAD"
    assert report_path(root, TASK).name == TASK + " - Code Review.html"
    assert report_path(root, TASK, "spec").name == TASK + " - Spec Review.html"


def test_generator_validator_hashes_static_and_taskreport_isolation(repository):
    root, store, _ = repository
    results = root / "results"
    canonical = results / TASK / (TASK + ".html")
    canonical.parent.mkdir(parents=True)
    canonical.write_text("TaskReport owns this", encoding="utf-8")
    generate_reports(store, results)
    assert not validate_review(store, results)
    assert canonical.read_text() == "TaskReport owns this"
    html = report_path(results, TASK).read_text(encoding="utf-8")
    assert "Full first specification" in html and "source-task" in html and "return 10" in html
    snapshot = save_static_review_snapshot(report_path(results, TASK), TASK)
    static = snapshot.read_text(encoding="utf-8")
    assert "<script" not in static and "<button" not in static and "return 10" in static
    report_path(results, TASK).write_text(html + "modified", encoding="utf-8")
    assert any("hash" in error for error in validate_review(store, results))


def test_copied_upstream_layout_styles_and_before_after_tables(repository):
    root, store, _ = repository
    generate_reports(store, root / "results")
    page = report_path(root / "results", TASK).read_text(encoding="utf-8")
    css = re.search(r"<style>(.*?)</style>", page, re.S)[1]
    # Exact stylesheet from the user-designated perfected static reference.
    assert hashlib.sha256(css.encode()).hexdigest() == "0eabd996e05bf194736a617b7958069b80d66b0e217ef293774de5c824843b6e"
    assert "grid-template-columns:340px minmax(0,1fr)" in css
    assert page.count('<table class="inline-diff-table">') == 2
    assert page.count('<th colspan="2">Task baseline</th>') == 2
    assert page.count('<th colspan="2">Working tree</th>') == 2
    assert 'class="diff-number' in page and 'class="diff-code diff-before' in page
    assert page.count('data-change-vector="thread"') == 2
    assert page.count('data-change-vector="step"') == 2
    assert page.count('data-dependency-map="thread"') == 2
    assert page.count('data-dependency-map="step"') == 2
    assert '<div class="component-index">1.a</div>' in page
    assert '<div class="component-index">2.a</div>' in page
    assert '<pre>{' not in page


def test_queue_gate_idempotency_failure_retry(repository, monkeypatch):
    root, store, _ = repository
    queue = CodeApprovalQueue(root / "queue" / "pending", root / "tasks.json", TASK)
    with pytest.raises(RuntimeError):
        queue.enqueue(store.load())
    ledger = approve(store)
    ledger = approve(store, "1.2", "BASE-REQ-002")
    import scripts.review_server as server
    writer = server.write_request_atomically
    def fail(*args):
        raise OSError("queue unavailable")
    monkeypatch.setattr(server, "write_request_atomically", fail)
    with pytest.raises(OSError, match="unavailable"):
        queue.enqueue(ledger)
    monkeypatch.setattr(server, "write_request_atomically", writer)
    assert queue.enqueue(ledger)["state"] == "queued"
    assert queue.enqueue(ledger)["state"] == "already_queued"
    spec_queue = SpecApprovalQueue(root / "queue" / "pending", root / "tasks.json", TASK)
    assert spec_queue.enqueue(ledger)["state"] == "queued"
    assert len(list((root / "queue" / "pending").glob("*.json"))) == 2


def test_taskmanager_revalidates_review_queue_snapshot(repository, monkeypatch):
    root, store, _ = repository
    approve(store)
    ledger = approve(store, "1.2", "BASE-REQ-002")
    tasks_path = root / "queue" / "tasks.json"
    atomic_json(tasks_path, {"TASKS": [{"id": TASK, "title": "Test", "status": "Ready", "comments": []}]})
    context = {"root": str(root), "architecture": str(store.architecture_path),
               "requirements": str(store.requirements_path), "baseline_ref": "main", "layer": "base"}
    queue = CodeApprovalQueue(root / "queue" / "pending", tasks_path, TASK, context)
    result = queue.enqueue(ledger)
    task_store = SimpleNamespace(tasks_path=tasks_path, base_dir=root, config=Params({}))
    monkeypatch.setattr(task_manager, "_sync_task_store_to_git", lambda *args, **kwargs: {})
    (root / "sample.py").write_text("def first():\n    return 11\n\ndef second():\n    return 20\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        task_manager._apply_status_request_file(task_store, root / "queue" / "pending" / result["request"])
    assert json.loads(tasks_path.read_text())["TASKS"][0]["status"] == "Ready"
    ledger = store.update_feature("1.1", "approve", expected_revision=store.load()["revision"])
    old = root / "queue" / "pending" / result["request"]
    (root / "queue" / "processed").mkdir()
    old.rename(root / "queue" / "processed" / old.name)
    new_result = queue.enqueue(ledger)
    assert new_result["state"] == "queued"
    task_manager._apply_status_request_file(task_store, root / "queue" / "pending" / new_result["request"])
    assert json.loads(tasks_path.read_text())["TASKS"][0]["status"] == "CodeApproved"


def test_per_artifact_commit_gate_not_blocked_by_another_missing_artifact(repository):
    root, store, _ = repository
    approve(store)
    approve(store, "1.2", "BASE-REQ-002")
    manifest = json.loads(store.architecture_path.read_text())
    manifest["features"]["3"] = {"type": "Review.Component", "name": "Missing file",
        "path": "not_generated.py", "requirements": ["BASE-REQ-001"], "review": {"task_id": TASK}}
    atomic_json(store.architecture_path, manifest)
    ledger = store.load()
    assert ledger["artifacts"]["sample.py"]["ready_to_commit"]
    assert not ledger["artifacts"]["not_generated.py"]["ready_to_commit"]
    assert not code_eligible(ledger)


def test_http_health_origin_cached_refresh_failure(repository, monkeypatch):
    root, store, _ = repository
    results = root / "results"
    generate_reports(store, results)
    runtime = {"results_root": results, "template": Path.cwd() / "resources" / "templates" / "code_review.html",
               "refresh_interval_seconds": 1}
    server = ReviewHttpServer(("127.0.0.1", 0), store, report_path(results, TASK),
        CodeApprovalQueue(root / "queue" / "pending", root / "tasks.json", TASK),
        SpecApprovalQueue(root / "queue" / "pending", root / "tasks.json", TASK),
        runtime=runtime, worker_pid=123, instance_id="test-instance")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:" + str(server.server_address[1]) + "/"
    try:
        assert verify_health(url, TASK, root, 123, "test-instance", attempts=1)["status"] == "ok"
        with pytest.raises(RuntimeError):
            verify_health(url, TASK, root, 999, "test-instance", attempts=1)
        def fail(*args):
            raise RuntimeError("Refresh failed")
        monkeypatch.setattr(server, "refresh_sync", fail)
        server.maybe_refresh(force=True)
        for _ in range(100):
            if not server.refreshing:
                break
            time.sleep(.01)
        assert server.refresh_error == "Refresh failed"
        assert urllib.request.urlopen(url).status == 200
        request = urllib.request.Request(url + "api/review/features/1.1", data=json.dumps({
            "action": "approve_spec", "expected_revision": store.load()["revision"]}).encode(), method="POST",
            headers={"Origin": "https://evil.invalid", "X-Review-Task": TASK, "Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_http_review_snapshot_stays_responsive_during_refresh_lock(repository, monkeypatch):
    root, store, _ = repository
    generate_reports(store, root / "results")
    queue = CodeApprovalQueue(root / "queue" / "pending", root / "tasks.json", TASK)
    server = ReviewHttpServer(("127.0.0.1", 0), store, report_path(root / "results", TASK),
                             queue, SpecApprovalQueue(root / "queue" / "pending", root / "tasks.json", TASK))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    acquired, release = threading.Event(), threading.Event()
    def blocking_refresh():
        with store.transaction():
            acquired.set()
            release.wait(5)
    refresh = threading.Thread(target=blocking_refresh, daemon=True)
    refresh.start()
    try:
        assert acquired.wait(5)
        start = time.monotonic()
        url = f"http://127.0.0.1:{server.server_address[1]}/api/review"
        with urllib.request.urlopen(url, timeout=2) as response:
            payload = json.loads(response.read())
        assert payload["task_id"] == TASK
        assert time.monotonic() - start < 2
    finally:
        release.set()
        refresh.join(5)
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_launcher_process_is_bound_to_its_worker_and_cleans_health(repository):
    root, store, _ = repository
    import sys
    template = root / "resources" / "templates" / "code_review.html"
    template.parent.mkdir(parents=True)
    shutil.copyfile(Path.cwd() / "resources" / "templates" / "code_review.html", template)
    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    launcher = subprocess.Popen([sys.executable, str(Path.cwd() / "scripts" / "code_review.py"),
        "--root", str(root), "--task-id", TASK, "--layer", "base", "--baseline-ref", "main",
        "--results-root", str(root / "results"), "--status-queue", str(root / "queue"),
        "--port", "0", "--worker-pid", str(worker.pid), "--no-browser"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    health_path = store.review_path.with_name("server.json")
    try:
        deadline = time.monotonic() + 20
        while not health_path.is_file() and time.monotonic() < deadline and launcher.poll() is None:
            time.sleep(.1)
        if not health_path.is_file():
            if launcher.poll() is None:
                launcher.terminate()
            stdout, stderr = launcher.communicate(timeout=10)
            pytest.fail("Launcher did not publish health: " + stdout + stderr)
        health = json.loads(health_path.read_text(encoding="utf-8"))
        assert verify_health(health["url"], TASK, root, worker.pid, health["instance_id"], attempts=1)
        worker.terminate()
        worker.wait(timeout=5)
        stdout, stderr = launcher.communicate(timeout=10)
        assert launcher.returncode == 0, stdout + stderr
        assert not health_path.exists()
        assert report_path(root / "results", TASK).is_file()
    finally:
        for child in (launcher, worker):
            if child.poll() is None:
                child.kill()
                child.wait()


def test_render_publication_rejects_newer_decision_and_keeps_last_report(repository, monkeypatch):
    root, store, _ = repository
    import scripts.review_report_generator as generator
    generate_reports(store, root / "results")
    target = report_path(root / "results", TASK)
    previous = target.read_bytes()
    metadata = store.review_path.with_name("reports.json").read_bytes()
    original = generator.render_report

    def render_with_concurrent_decision(ledger, *args):
        rendered = original(ledger, *args)
        store.approve_spec_requirement("BASE-REQ-001", expected_revision=ledger["revision"])
        return rendered

    monkeypatch.setattr(generator, "render_report", render_with_concurrent_decision)
    with pytest.raises(RuntimeError, match="changed during rendering"):
        generate_reports(store, root / "results")
    assert target.read_bytes() == previous
    assert store.review_path.with_name("reports.json").read_bytes() == metadata
    assert store.cached_snapshot()["spec_reviews"]["BASE-REQ-001"]["status"] == FeatureStatus.SPEC_APPROVED


def copy_review_runtime(root):
    for relative in (
        "code_review.py", "scripts/code_review.py", "scripts/review_server.py",
        "scripts/review_report_generator.py", "scripts/status_queue_mcp.py", "scripts/review_data.py",
        "utils/reviewutils.py", "utils/baseutils.py", "utils/datautils.py",
        "resources/templates/code_review.html",
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path.cwd() / relative, destination)
    atomic_json(root / "config" / "base.json", {
        "COMMON": {"APP_NAME": "BaseApp", "CONFIG_WRAPPERS": ["{$", "$}"]},
        "APP": {"TASK_MANAGER": {"result_store": "results", "status_queue": "queue",
                                "review": {"baseline_ref": "main"}}},
    })
    atomic_json(root / "config" / "app.json", {"COMMON": {"APP_NAME": "MyApp"}})


@pytest.mark.parametrize("folder", ["", "scripts"])
def test_task_root_entrypoints_infer_base_context_and_serve_actions(repository, folder):
    root, store, _ = repository
    import sys
    copy_review_runtime(root)
    config = task_manager.Config(base_config_path=str(root / "config" / "base.json")).config
    assert config.COMMON.APP_NAME == "MyApp"
    context = resolve_runtime(parser().parse_args([]), config)
    assert context["task_id"].startswith("BASE-TASK-") and context["layer"] == "base"
    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(45)"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    launcher = subprocess.Popen([
        sys.executable, "code_review.py", "--no-browser", "--no-refresh",
        "--worker-pid", str(worker.pid),
    ], cwd=root / folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    health_path = store.review_path.with_name("server.json")
    try:
        deadline = time.monotonic() + 25
        while not health_path.is_file() and time.monotonic() < deadline and launcher.poll() is None:
            time.sleep(.1)
        if not health_path.is_file():
            launcher.terminate()
            stdout, stderr = launcher.communicate(timeout=10)
            pytest.fail("Context-free launcher did not publish health: " + stdout + stderr)
        health = json.loads(health_path.read_text(encoding="utf-8"))
        assert verify_health(health["url"], TASK, root, worker.pid, health["instance_id"], attempts=1)
        url = health["url"]
        with urllib.request.urlopen(url, timeout=2) as response:
            assert "Requirement and Architecture Review" in response.read().decode()
        with urllib.request.urlopen(url + "api/review", timeout=2) as response:
            ledger = json.loads(response.read())
        assert set(ledger["features"]) == {"1.1", "1.2"}
        for endpoint, payload in (
            ("requirements/BASE-REQ-001/spec", {}),
            ("features/1.1", {"action": "approve_spec"}),
            ("features/1.1", {"action": "approve"}),
        ):
            payload["expected_revision"] = ledger["revision"]
            request = urllib.request.Request(url + "api/review/" + endpoint,
                data=json.dumps(payload).encode(), method="POST",
                headers={"Origin": url.rstrip("/"), "X-Review-Task": TASK,
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=5) as response:
                ledger = json.loads(response.read())
        assert ledger["features"]["1.1"]["status"] == FeatureStatus.CODE_APPROVED
        assert ledger["features"]["1.2"]["status"] == FeatureStatus.READY
        worker.terminate()
        worker.wait(timeout=5)
        stdout, stderr = launcher.communicate(timeout=10)
        assert launcher.returncode == 0, stdout + stderr
        assert not health_path.exists()
    finally:
        for child in (launcher, worker):
            if child.poll() is None:
                child.kill()
                child.wait()


def test_callable_data_pipeline_runs_without_source_session_and_preserves_metadata(repository):
    root, store, git = repository
    import sys
    from scripts.review_data import populate_review_data, validate_review_data
    copy_review_runtime(root)
    architecture_path = root / "build" / "architecture" / "base.json"
    architecture = json.loads(architecture_path.read_text(encoding="utf-8"))
    review = architecture["features"]["1.1"]["review"]
    review["change_details"] = [{
        "name": "first", "before": "Original return", "now": "Revised return",
        "how": ["Return the declared value"],
    }]
    review["dependencies"] = {"reliances": ["Committed dependency contract"]}
    architecture["features"]["1.1"]["source"] = {"task": "original-task", "feature": "original-feature"}
    atomic_json(architecture_path, architecture)
    git("add", "scripts", "utils", "resources", "config", "code_review.py",
        "build/architecture/base.json")
    git("commit", "-m", "Persist isolated review runtime and metadata")
    command = [sys.executable, str(root / "scripts" / "review_data.py")]
    completed = subprocess.run([*command, "populate", "--render"], cwd=root,
        capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    manifest = json.loads(completed.stdout)
    assert manifest["components"] == 2 and manifest["threads"] == 2
    snapshot = store.review_path.parent / manifest["snapshot"]
    catalog = json.loads((snapshot / "requirement-review-components.json").read_text(encoding="utf-8"))
    assert catalog[0]["change_details"] == review["change_details"]
    assert catalog[0]["dependencies"]["reliances"] == ["Committed dependency contract"]
    assert catalog[0]["source"]["feature"] == "original-feature"
    mapping = json.loads((snapshot / "review-diff-map.json").read_text(encoding="utf-8"))
    entry = next(iter(mapping.values()))
    assert "return 1" in (snapshot / entry["before"]).read_text(encoding="utf-8")
    assert "return 10" in (snapshot / entry["after"]).read_text(encoding="utf-8")
    assert not Path(entry["before"]).is_absolute()
    assert ".copilot" not in json.dumps(mapping) and "AI4EDG" not in json.dumps(mapping)
    assert not store.cached_snapshot()["events"]
    assert not validate_review_data(store), store.cached_snapshot()["uncovered_changes"]
    assert populate_review_data(store)["content_hash"] == manifest["content_hash"]
    checked = subprocess.run([*command, "validate"], cwd=root, check=True,
        capture_output=True, text=True, timeout=15)
    assert json.loads(checked.stdout)["valid"]
    (snapshot / entry["after"]).write_text("Tampered", encoding="utf-8")
    assert "Review data content hash mismatch." in validate_review_data(store)


def test_taskmanager_optional_review_launch_options(repository):
    root, _, _ = repository
    store = SimpleNamespace(base_dir=root, config=Params({"APP": {"TASK_MANAGER": {
        "review": {"enabled": True}, "result_store": "results", "status_queue": "queue"}}}))
    (root / "scripts").mkdir()
    (root / "scripts" / "code_review.py").write_text("# runtime\n", encoding="utf-8")
    options = task_manager._review_launch_options(store, {"id": TASK, "status": "Specified"})
    assert options["mode"] == "spec" and options["layer"] == "base"
    assert options["results_root"] == str(root / "results")
    store.config = Params({"APP": {"TASK_MANAGER": {"review": {"enabled": False}}}})
    assert task_manager._review_launch_options(store, {"id": TASK}) is None
