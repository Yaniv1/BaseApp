"""Feature ID: 6.3. Testing framework utilities for prep, live, and post runtime validation."""

import ast
import datetime as dt
import importlib
import inspect
import json
import os
import re
import threading
import time
import traceback
from .baseutils import AppManager, Params, create_logger, get_config, resolve_dotted, as_list


# Feature 6.3.1
class TestManager(AppManager):
    """Feature ID: 6.3.1. Coordinate config-driven prep, live, and post runtime tests."""

    def __init__(self, config=None, logger=None, results=None):
        seeded_results = results if results is not None else Params(
            {
                "start_time": (
                    dt.datetime.strptime(config.COMMON.START_TIME, config.COMMON.DATETIME_FORMAT)
                    if getattr(getattr(config, "COMMON", Params()), "START_TIME", None)
                    else dt.datetime.utcnow()
                ),
                "run_id": getattr(getattr(config, "COMMON", Params()), "RUN_ID", None) or str(__import__("uuid").uuid4().hex[:6]).upper(),
                "html_template": getattr(getattr(config, "COMMON", Params()), "HTML_TEMPLATE", None),
                "prep": {},
                "live": {},
                "post": {},
                "monitor": {},
            }
        )
        seeded_results.signature = getattr(seeded_results, "signature", f"run_id={seeded_results.run_id} start_time={seeded_results.start_time}")
        seeded_results.app_title = getattr(seeded_results, "app_title", f"{getattr(getattr(config, 'COMMON', Params()), 'APP_NAME', 'BaseApp')} Tests")

        super().__init__(config=config, logger=logger, results=seeded_results)

        self.settings = self.config.settings
        
        self.results.summary = Params({"prep": {}, "live": [], "post": {}, "report": {} })
        self.test_logger = self._create_test_logger(self.config.test_logger)
        
        self._lock = threading.RLock()
        self._live_thread = None
        self._live_stop = threading.Event()
        self._live_state = {}
        self._phase_index = {"prep": 0, "live": 0, "post": 0}
        self._live_cycle = 0

        if self.settings.enabled and self.logger is not None:
            self.logger.log(
                message_code="BASE013",
                data={"state": {"tests": {"manager": "initialized"}}, "base_dir": self.base_dir},
            )

    def _create_test_logger(self, logger_settings):
        """Create the dedicated logger used for test results."""
        logger_settings = logger_settings.get_dict() if hasattr(logger_settings, "get_dict") else {}
        logger_settings["base_dir"] = self.config.base_dir
        logger_settings["start_time"] = self.config.COMMON.START_TIME
        return create_logger(logger_settings)

    

    
    def _monitor_snapshot(self):
        """Return the latest aggregated snapshot from the shared monitor logger."""
        if self.logger is None or not hasattr(self.logger, "get_data_map_snapshot"):
            return {}
        return self.logger.get_data_map_snapshot()

    def _resolve_monitor(self, path=None, default=None):
        """Resolve one value from the shared monitor logger snapshot."""
        if self.logger is None or not hasattr(self.logger, "resolve_data"):
            return {} if not path else default
        return self.logger.resolve_data(path, default=default)

    def mark_app_state(self, status, **data):
        """Publish high-level app state through the shared logger snapshot."""
        if self.logger is None:
            return
        payload = {"state": {"app": {"status": status, "finished": status in ["finished", "failed"]}}}
        if data:
            payload["state"]["app"]["details"] = data
        self.logger.log(message_code="BASE018", data=payload)

    def request_store(self, item, note=None):
        """Record a monitoring request for the app in the shared logger snapshot."""
        payload = {
            "item": item,
            "note": note,
            "timestamp": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if self.logger is not None:
            self.logger.log(
                message_code="BASE019",
                data={"state": {"tests": {"requests": {"last": payload}}}, "request": payload},
            )
        return payload

    def run_phase(self, phase):
        """Run all configured tests for one non-live phase and return their status summary."""
        phase = str(phase).strip().lower()
        tests = self._get_phase_tests(phase)
        summary = {"phase": phase, "enabled": self.settings.enabled, "tests_run": 0, "failures": [], "n_failures": 0}

        if not self.settings.enabled or not tests:
            return summary

        if self.logger is not None:
            self.logger.log(
                message_code="BASE014",
                data={"state": {"tests": {"current_phase": phase}}, "phase": phase, "test_count": len(tests)},
            )

        for test_def in tests:
            result = self.run_a_test(test_definition=test_def, phase=phase)
            summary["tests_run"] += 1
            summary["failures"].extend(result.get("failures", []))
            summary["n_failures"] = len(summary["failures"])
            if self.settings.fail_fast and result.get("n_failures", 0) > 0:
                break

            self.results.summary.set(**{phase: summary})
        if self.logger is not None:
            self.logger.log(message_code="BASE017", data=summary)
        return summary

    def start_live(self):
        """Start the background scheduler for live tests."""
        live_tests = self._get_phase_tests("live")
        if not self.settings.enabled or not live_tests or (self._live_thread and self._live_thread.is_alive()):
            return False

        self._live_state = {}
        started_at = time.monotonic()
        live_offset = float(getattr(self.settings, "live_offset_seconds", 0.0) or 0.0)
        self.results.summary.set(live=[])
        self._live_cycle = 0
        for test_def in live_tests:
            test_id = test_def.get("id")
            frequency = float(test_def.get("frequency_seconds", 60.0) or 60.0)
            offset = float(test_def.get("offset_seconds", live_offset) or 0.0)
            self._live_state[test_id] = {
                "definition": test_def,
                "frequency_seconds": max(0.1, frequency),
                "next_run_at": started_at + max(0.0, offset),
                "runs": 0,
                "active": True,
                "max_runs": test_def.get("max_runs"),
            }

            

        self._live_stop.clear()
        self._live_thread = threading.Thread(target=self._live_loop, name="BaseAppTestManager", daemon=True)
        self._live_thread.start()
        if self.logger is not None:
            self.logger.log(
                message_code="BASE015",
                data={"state": {"tests": {"live": {"active": True, "count": len(live_tests)}}}},
            )
        return True

    def stop_live(self, timeout=5.0):
        """Stop the background scheduler for live tests and wait briefly for exit."""
        if not self._live_thread:
            return False

        self._live_stop.set()
        self._live_thread.join(timeout=timeout)
        alive = self._live_thread.is_alive()
        if not alive:
            if self.logger is not None:
                self.logger.log(
                    message_code="BASE016",
                    data={"state": {"tests": {"live": {"active": False, "remaining_active": self._count_live_active()}}}},
                )
            self._live_thread = None
        return not alive

    def finalize(self):
        """Build the final structured test summary and expose logger output."""
        summary = self._build_summary()
        self.results.monitor = self._monitor_snapshot()
        self.test_logs = self.test_logger.logs
        return {
            "tests": self.results,
            "test_logs": self.test_logs,
            "summary": summary,
            "exit_code": 1 if summary.get("n_failures", 0) > 0 else 0,
        }

    

    def _live_loop(self, stop_cases={}):
        """Continuously schedule configured live tests on their requested cadence."""
        while not self._live_stop.is_set() and not bool(self._resolve_monitor("state.app.finished", False)):
            now = time.monotonic()
            due_ids = [
                test_id
                for test_id, state in self._live_state.items()
                if state.get("active") and now >= state.get("next_run_at", now)
            ]

            if due_ids:
                self._live_cycle += 1
                summary = {
                    "phase": "live",
                    "cycle": self._live_cycle,
                    "enabled": self.settings.enabled,
                    "tests_run": 0,
                    "failures": [],
                    "n_failures": 0,
                }

                for test_id in due_ids:
                    state = self._live_state.get(test_id, {})
                    result = self.run_a_test(test_definition=state.get("definition"), phase="live")
                    state["runs"] = int(state.get("runs", 0)) + 1
                    state["next_run_at"] = now + float(state.get("frequency_seconds", 60.0))
                    max_runs = state.get("max_runs")
                    if max_runs is not None and state["runs"] >= int(max_runs):
                        state["active"] = False
                    summary["failures"].extend(result.get("failures", []))
                    summary["n_failures"] = len(summary["failures"])
                    if result.get("n_failures", 0) > 0:
                        if self.fail_fast:
                            self._live_stop.set()
                    summary["tests_run"] += 1

                self.results.summary.live.append(summary)
                if self.fail_fast and summary["n_failures"] > 0:
                    break

            sleep_seconds = self._next_live_sleep(now)
            self._live_stop.wait(timeout=sleep_seconds)

            for stop_key, stop_value in stop_cases.items():
                # if any stop case's key in the data_map is truthy, break the loop
                if self._resolve_monitor(stop_key) in as_list(stop_value):
                    self._live_stop.set()
                    break

    def _next_live_sleep(self, now):
        """Return the next scheduler wait interval based on due live tests."""
        active_times = [
            state.get("next_run_at", now + 0.25)
            for state in self._live_state.values()
            if state.get("active")
        ]
        if not active_times:
            return 0.25
        return max(0.05, min(active_times) - now)

    def _count_live_active(self):
        """Count currently active live tests."""
        return len([state for state in self._live_state.values() if state.get("active")])

    def _get_phase_tests(self, phase):
        """Normalize configured tests for one phase into a consistent list shape."""
        raw_tests = getattr(self.config, phase, [])
        if hasattr(raw_tests, "get_dict"):
            raw_tests = raw_tests.get_dict()

        if isinstance(raw_tests, dict):
            normalized = []
            for test_id, test_definition in raw_tests.items():
                item = dict(test_definition or {})
                item.setdefault("id", test_id)
                normalized.append(item)
            raw_tests = normalized

        normalized_tests = []
        for index, item in enumerate(raw_tests or [], start=1):
            if hasattr(item, "get_dict"):
                item = item.get_dict()
            if not isinstance(item, dict):
                continue
            if item.get("enabled", True) is not True:
                continue
            candidate = dict(item)
            candidate.setdefault("id", f"{phase}_{index}")
            candidate.setdefault("name", candidate["id"])
            candidate.setdefault("phase", phase)
            normalized_tests.append(candidate)
        return normalized_tests

    def run_a_test(self, test_definition, phase):
        """Execute one configured test and persist its structured result lines."""
        if not test_definition:
            return {"failures": [], "n_failures": 0, "result_count": 0}

        test_id = test_definition.get("id")
        run_number = self._next_run_number(phase)
        result_lines = []
        failures = []
        callable_target = None

        try:
            bound_inputs = self._resolve_inputs(test_definition)
            callable_target = self._resolve_callable_target(test_definition, bound_inputs=bound_inputs)
            call_output = self._invoke_target(callable_target, bound_inputs)
            result_lines = self._normalize_test_output(call_output, test_definition, phase, run_number)
        except Exception as ex:
            traceback_entries = traceback.extract_tb(ex.__traceback__)
            last_frame = traceback_entries[-1] if traceback_entries else None
            traceback_text = "".join(traceback.format_exception(type(ex), ex, ex.__traceback__))
            result_lines = [
                self._build_result_line(
                    phase=phase,
                    test_definition=test_definition,
                    run_number=run_number,
                    status="FAIL",
                    message_code="TST005",
                    message=str(ex),
                    criteria=[
                        {
                            "name": "execution",
                            "success": False,
                            "status": "FAIL",
                            "actual": str(ex),
                            "expected": "Test callable executes successfully",
                            "file": getattr(last_frame, "filename", None),
                            "line": getattr(last_frame, "lineno", None),
                            "function": getattr(last_frame, "name", None),
                        }
                    ],
                    data={
                        "failed_callable": test_definition.get("callable") or test_definition.get("target"),
                        "traceback": {
                            "file": getattr(last_frame, "filename", None),
                            "line": getattr(last_frame, "lineno", None),
                            "function": getattr(last_frame, "name", None),
                            "code": getattr(last_frame, "line", None),
                            "text": traceback_text,
                        },
                    },
                )
            ]

        for line in result_lines:
            if line.get("status") in self.settings.fail_on and test_id not in failures:
                failures.append(test_id)
            self.test_logger.log(
                message=line.get("message", ""),
                message_type=line.get("status", "WARN"),
                message_code=line.get("message_code"),
                data={
                    "phase": phase,
                    "test_id": test_id,
                    "run_number": run_number,
                    "criteria": line.get("criteria", []),
                    **line.get("data", {}),
                },
            )

        with self._lock:
            phase_store = getattr(self.results, phase)
            existing = getattr(phase_store, test_id, []) if hasattr(phase_store, test_id) else []
            setattr(phase_store, test_id, existing + result_lines)

        return {"failures": failures, "n_failures": len(failures), "result_count": len(result_lines)}

    def _next_run_number(self, phase):
        """Increment and return the next run counter for one phase."""
        with self._lock:
            self._phase_index[phase] = int(self._phase_index.get(phase, 0)) + 1
            return self._phase_index[phase]

    def _resolve_callable_target(self, test_definition, bound_inputs=None):
        """Resolve function, class, or class method configured for a test definition."""
        target_path = test_definition.get("callable") or test_definition.get("target")
        if not target_path:
            raise ValueError("Test definition requires 'callable' or 'target'")

        module_name, attrs = self._split_module_path(target_path)
        module = importlib.import_module(module_name)
        current = module
        parent = None
        for attr in attrs:
            parent = current
            current = getattr(current, attr)

        resolved_constructor_inputs = self._resolve_mapping(test_definition.get("constructor_inputs", {}))
        bound_inputs = bound_inputs or {}

        def build_constructor_kwargs(callable_obj, extra_inputs=None):
            kwargs = dict(resolved_constructor_inputs)
            candidate_inputs = dict(extra_inputs or {})
            for key, value in candidate_inputs.items():
                kwargs.setdefault(key, value)

            try:
                signature = inspect.signature(callable_obj)
            except Exception:
                return kwargs

            parameters = signature.parameters
            if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
                return kwargs
            return {name: value for name, value in kwargs.items() if name in parameters}

        if inspect.isclass(current):
            constructor_kwargs = build_constructor_kwargs(current.__init__, bound_inputs)
            instance = current(**constructor_kwargs) if constructor_kwargs else current()
            if hasattr(instance, "run") and callable(getattr(instance, "run")):
                return getattr(instance, "run")
            if callable(instance):
                return instance
            raise ValueError(f"Configured class target '{target_path}' is not runnable")

        if inspect.isfunction(current) and inspect.isclass(parent):
            constructor_kwargs = build_constructor_kwargs(parent.__init__, bound_inputs)
            instance = parent(**constructor_kwargs) if constructor_kwargs else parent()
            return getattr(instance, current.__name__)

        if callable(current):
            return current

        raise ValueError(f"Configured target '{target_path}' is not callable")

    def _split_module_path(self, target_path):
        """Split a dotted import path into module path and remaining attributes."""
        parts = str(target_path).split(".")
        for index in range(len(parts), 0, -1):
            module_name = ".".join(parts[:index])
            try:
                importlib.import_module(module_name)
                return module_name, parts[index:]
            except Exception:
                continue
        raise ImportError(f"Could not resolve import path '{target_path}'")

    def _resolve_inputs(self, test_definition):
        """Resolve configured input bindings into call kwargs."""
        resolved = self._resolve_mapping(test_definition.get("inputs", {}))
        resolved.setdefault("manager", self)
        resolved.setdefault("monitor", self.logger)
        resolved.setdefault("monitor_logger", self.logger)
        resolved.setdefault("config", self.config)
        resolved.setdefault("results", getattr(self, "results", None) )
        resolved.setdefault("test_logger", self.test_logger)
        resolved.setdefault("phase", test_definition.get("phase"))
        resolved.setdefault("test_definition", test_definition)
        return resolved

    def _resolve_mapping(self, mapping):
        """Resolve a config mapping that may contain value/ref descriptors."""
        if hasattr(mapping, "get_dict"):
            mapping = mapping.get_dict()
        if not isinstance(mapping, dict):
            return {}
        return {key: self._resolve_value(value) for key, value in mapping.items()}

    def _resolve_value(self, value):
        """Resolve one configured value or reference."""
        if hasattr(value, "get_dict"):
            value = value.get_dict()

        if isinstance(value, dict):
            if "ref" in value:
                return self._resolve_reference(value.get("ref"))
            if "value" in value and len(value) == 1:
                return value.get("value")
            return {key: self._resolve_value(child) for key, child in value.items()}

        if isinstance(value, list):
            return [self._resolve_value(child) for child in value]

        return value

    def _resolve_reference(self, reference):
        """Resolve a reference from manager/config/results/monitor scopes."""
        reference = str(reference or "")
        if not reference:
            return None

        if reference == "manager":
            return self
        if reference in ["monitor", "monitor_logger"]:
            return self.logger

        if reference == "config":
            return self.config
        if reference == "results":
            return getattr(self, "results", None)
        if reference == "test_logger":
            return self.test_logger

        if reference.startswith("monitor."):
            return self._resolve_monitor(reference.split(".", 1)[1])
        if reference.startswith("monitor_logger."):
            return self._resolve_monitor(reference.split(".", 1)[1])
        if reference.startswith("config."):
            return resolve_dotted(self.config, reference.split(".", 1)[1])
        if reference.startswith("results."):
            return resolve_dotted(getattr(self, "results", None), reference.split(".", 1)[1])
        
        return reference

    def _invoke_target(self, callable_target, inputs):
        """Call the resolved target with only the kwargs it accepts."""
        signature = inspect.signature(callable_target)
        parameters = signature.parameters
        if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
            return callable_target(**inputs)
        accepted = {name: value for name, value in inputs.items() if name in parameters}
        return callable_target(**accepted)

    def _normalize_test_output(self, call_output, test_definition, phase, run_number):
        """Normalize a callable return value into one or more structured result lines."""
        if call_output is None:
            return [
                self._build_result_line(
                    phase=phase,
                    test_definition=test_definition,
                    run_number=run_number,
                    status="WARN",
                    message_code="TST004",
                    message=f"{test_definition.get('name', test_definition.get('id'))} completed without structured output",
                    criteria=[],
                )
            ]

        if isinstance(call_output, bool):
            return [
                self._build_result_line(
                    phase=phase,
                    test_definition=test_definition,
                    run_number=run_number,
                    status="PASS" if call_output else "FAIL",
                    message_code="TST003" if call_output else "TST005",
                    message=test_definition.get("name", test_definition.get("id")),
                    criteria=[
                        {
                            "name": "result",
                            "success": bool(call_output),
                            "status": "PASS" if call_output else "FAIL",
                            "actual": bool(call_output),
                            "expected": True,
                        }
                    ],
                )
            ]

        if isinstance(call_output, str):
            return [
                self._build_result_line(
                    phase=phase,
                    test_definition=test_definition,
                    run_number=run_number,
                    status="INFO",
                    message_code="TST001",
                    message=call_output,
                    criteria=[],
                )
            ]

        if hasattr(call_output, "get_dict"):
            call_output = call_output.get_dict()

        if isinstance(call_output, dict) and "results" in call_output:
            return [self._coerce_result_line(item, test_definition, phase, run_number) for item in call_output.get("results", [])]

        if isinstance(call_output, list):
            return [self._coerce_result_line(item, test_definition, phase, run_number) for item in call_output]

        if isinstance(call_output, dict):
            return [self._coerce_result_line(call_output, test_definition, phase, run_number)]

        return [
            self._build_result_line(
                phase=phase,
                test_definition=test_definition,
                run_number=run_number,
                status="INFO",
                message_code="TST001",
                message=str(call_output),
                criteria=[],
            )
        ]

    def _coerce_result_line(self, item, test_definition, phase, run_number):
        """Convert one raw test output dict into the canonical result line shape."""
        if hasattr(item, "get_dict"):
            item = item.get_dict()
        item = dict(item or {})
        status = str(item.get("status", "WARN")).strip().upper()
        message_code = item.get("message_code")
        if not message_code:
            message_code = {"INFO": "TST001", "PASS": "TST003", "WARN": "TST004", "FAIL": "TST005"}.get(status, "TST004")

        return self._build_result_line(
            phase=phase,
            test_definition=test_definition,
            run_number=run_number,
            status=status,
            message_code=message_code,
            message=item.get("message"),
            criteria=item.get("criteria", []),
            data=item.get("data", {}),
        )

    def _build_result_line(self, phase, test_definition, run_number, status, message_code, message=None, criteria=None, data=None):
        """Build one structured test result line."""
        normalized_criteria = []
        for criterion in criteria or []:
            if hasattr(criterion, "get_dict"):
                criterion = criterion.get_dict()
            criterion = dict(criterion or {})
            criterion_status = str(criterion.get("status", "PASS" if criterion.get("success", True) else "FAIL")).strip().upper()
            criterion["status"] = criterion_status
            criterion["success"] = bool(criterion.get("success", criterion_status == "PASS"))
            normalized_criteria.append(criterion)

        return {
            "timestamp": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "phase": phase,
            "test_id": test_definition.get("id"),
            "test_name": test_definition.get("name", test_definition.get("id")),
            "run_number": run_number,
            "status": str(status).strip().upper(),
            "message_code": message_code,
            "message": message or test_definition.get("name", test_definition.get("id")),
            "criteria": normalized_criteria,
            "criteria_summary": {
                "total": len(normalized_criteria),
                "passed": len([criterion for criterion in normalized_criteria if criterion.get("status") == "PASS"]),
                "warned": len([criterion for criterion in normalized_criteria if criterion.get("status") == "WARN"]),
                "failed": len([criterion for criterion in normalized_criteria if criterion.get("status") == "FAIL"]),
            },
            "data": data or {},
        }

    def _build_summary(self):
        """Build the final aggregate summary across all phases and tests."""
        phase_counts = {}
        failures = []
        total_result_lines = 0
        total_tests = 0

        for phase in ["prep", "live", "post"]:
            phase_store = getattr(self.results.summary, phase, [] if phase == "live" else {})

            if isinstance(phase_store, list):
                tests = sum(int(item.get("tests_run", 0)) for item in phase_store if isinstance(item, dict))
                result_lines = len(phase_store)
                phase_failures = []
                for item in phase_store:
                    if isinstance(item, dict):
                        phase_failures.extend(item.get("failures", []))
            else:
                phase_dict = phase_store.get_dict() if hasattr(phase_store, "get_dict") else dict(phase_store or {})
                if all(key in phase_dict for key in ["phase", "enabled", "tests_run", "failures"]):
                    tests = int(phase_dict.get("tests_run", 0))
                    result_lines = tests
                    phase_failures = list(phase_dict.get("failures", []))
                else:
                    tests = len(phase_dict)
                    result_lines = sum(len(items or []) for items in phase_dict.values())
                    phase_failures = sorted({
                        line.get("test_id")
                        for items in phase_dict.values()
                        for line in (items or [])
                        if line.get("status") in self.fail_on and line.get("test_id")
                    })

            phase_counts[phase] = {
                "tests": tests,
                "result_lines": result_lines,
                "failures": phase_failures,
                "n_failures": len(phase_failures),
            }
            total_tests += tests
            total_result_lines += result_lines
            failures.extend(phase_failures)

        failures = sorted(dict.fromkeys(failures))

        self.results.set(report=Params(
            {
                "enabled": self.settings.enabled,
                "fail_fast": self.settings.fail_fast,
                "fail_on": sorted(self.settings.fail_on),
                "failures": failures,
                "n_failures": len(failures),
                "total_tests": total_tests,
                "total_result_lines": total_result_lines,
                "phases": phase_counts,
            }
        ))
        return self.results.report.get_dict()


# Feature 6.3.2
def build_test_config(runtime_config, test_config_path="../test/config/base.json"):
    """Feature ID: 6.3.2. Load a flattened test config derived from the integrated runtime config."""
    if runtime_config is None:
        raise ValueError("runtime_config is required for build_test_config")

    common_config = runtime_config.COMMON.get_dict() if hasattr(runtime_config, "COMMON") else {}

    test_config_root = get_config(config_path=test_config_path)
    testing_config = test_config_root.get("testing", {}) if isinstance(test_config_root, dict) else {}

    flattened_config = Params({"COMMON": common_config} | testing_config)
    flattened_config.base_dir = runtime_config.base_dir if hasattr(runtime_config, "base_dir") else os.getcwd()
    return flattened_config.evaluate(common_nodes=["COMMON"]).populate(
        common_nodes=["COMMON"],
        wrappers=flattened_config.COMMON.CONFIG_WRAPPERS,
    )


# Feature 6.3.3
def evaluate_checks(checks=None, **kwargs):
    """Feature ID: 6.3.3. Evaluate one or more config-driven criteria and return a structured result line."""
    normalized_checks = []
    for check in checks or []:
        if hasattr(check, "get_dict"):
            check = check.get_dict()
        check = dict(check or {})

        operator = str(check.get("operator", "exists")).strip().lower()
        actual = check.get("actual")
        expected = check.get("expected")
        status = "PASS"

        if operator == "exists":
            success = actual is not None
        elif operator == "truthy":
            success = bool(actual)
        elif operator == "eq":
            success = actual == expected
        elif operator == "ne":
            success = actual != expected
        elif operator == "ge":
            success = (actual is not None and expected is not None and actual >= expected)
        elif operator == "gt":
            success = (actual is not None and expected is not None and actual > expected)
        elif operator == "le":
            success = (actual is not None and expected is not None and actual <= expected)
        elif operator == "lt":
            success = (actual is not None and expected is not None and actual < expected)
        elif operator == "contains":
            success = expected in actual if actual is not None else False
        else:
            success = False
            status = "WARN"

        if status != "WARN":
            status = "PASS" if success else str(check.get("failure_status", "FAIL")).strip().upper()

        normalized_checks.append(
            {
                "name": check.get("name", operator),
                "operator": operator,
                "success": success,
                "status": status,
                "actual": actual,
                "expected": expected,
            }
        )

    has_failures = any(check.get("status") == "FAIL" for check in normalized_checks)
    has_warns = any(check.get("status") == "WARN" for check in normalized_checks)
    overall_status = "FAIL" if has_failures else ("WARN" if has_warns else "PASS")
    return {
        "status": overall_status,
        "message": kwargs.get("message", "Evaluated configured test criteria"),
        "criteria": normalized_checks,
    }


# Feature 6.3.4
def monitor_activity(monitor=None, monitor_logger=None, min_history=1, **kwargs):
    """Feature ID: 6.3.4. Check that the shared logger monitor has received at least the expected number of updates."""
    active_monitor = monitor_logger if monitor_logger is not None else monitor
    snapshot = active_monitor.get_data_map_snapshot() if active_monitor is not None and hasattr(active_monitor, "get_data_map_snapshot") else {}
    history_count = len(getattr(active_monitor, "logs", [])) if active_monitor is not None else 0
    success = history_count >= int(min_history)
    return {
        "status": "PASS" if success else "FAIL",
        "message": kwargs.get("message", "Observed logger-backed monitor updates while the app was running"),
        "criteria": [
            {
                "name": "monitor_log_count",
                "success": success,
                "status": "PASS" if success else "FAIL",
                "actual": history_count,
                "expected": int(min_history),
            },
            {
                "name": "monitor_snapshot_available",
                "success": bool(snapshot),
                "status": "PASS" if snapshot else "WARN",
                "actual": bool(snapshot),
                "expected": True,
            }
        ],
        "data": {"history_count": history_count, "snapshot_keys": list(snapshot.keys())[:20]},
    }

