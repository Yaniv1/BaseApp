# Feature 6.1

import pandas as pd
import copy
import json
import os
import datetime as dt 
import html
import sys
import numpy as np
import importlib
import uuid
import pprint
import re
import webbrowser
import time
import threading
import hashlib
import inspect
from functools import wraps
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from .datautils import DataFrameConverter, DataLoader
except ImportError:
    try:
        from utils.datautils import DataFrameConverter, DataLoader
    except ImportError:
        from datautils import DataFrameConverter, DataLoader


# Feature 6.1.1
def trackit(func):
    """Feature ID: 6.1.1. Track function execution and return result with extensible metrics."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        function_result = func(*args, **kwargs)
        duration_seconds = round(time.perf_counter() - started, 6)
        return {
            "function": func.__name__,
            "result": function_result,
            "metrics": {
                "duration_seconds": duration_seconds,
            },
        }

    return wrapper

# Feature 6.1.2
def dict_merge(base: dict = {}, override: dict = {}):
    """Feature ID: 6.1.2. Recursively merge override dict into base dict and return merged result."""
    merged = base.copy()
    for key, override_value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(override_value, dict):
            merged[key] = dict_merge(base_value, override_value)
        else:
            merged[key] = override_value
    return merged

# Feature 6.1.3
class Params:
    """Feature ID: 6.1.3. Simple container that exposes dictionary values as attributes."""
    def __init__(self, params={}):
        """Initialize the container from a parameter dictionary."""
        self.set(**params)

    def _normalize_value(self, value):
        """Normalize nested dict/list values into Params-friendly structures."""
        if isinstance(value, dict):
            return Params(value)
        if isinstance(value, list):
            return [Params(v) if isinstance(v, dict) else v for v in value]
        return value

    def _as_plain_dict(self, value):
        """Return plain dictionary for Params/dict values, otherwise None."""
        if isinstance(value, Params):
            return value.get_dict()
        if isinstance(value, dict):
            return value
        return None

    def _as_plain_list(self, value):
        """Return plain list for list values, otherwise None."""
        if not isinstance(value, list):
            return None
        plain = []
        for item in value:
            if isinstance(item, Params):
                plain.append(item.get_dict())
            else:
                plain.append(item)
        return plain

    def _merge_lists(self, base_list, override_list):
        """Merge lists by prepending overrides and removing duplicates."""
        merged = []
        seen = set()

        def add(item):
            normalized = self._as_plain_dict(item) if self._as_plain_dict(item) is not None else item
            key = json.dumps(normalized, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                merged.append(normalized)

        for item in override_list:
            add(item)
        for item in base_list:
            add(item)

        return merged

    def _merge_dicts(self, base_dict, override_dict):
        """Recursively merge override_dict into base_dict and return merged dict."""
        merged = dict(base_dict)
        for key, override_value in override_dict.items():
            base_value = merged.get(key)
            base_child = self._as_plain_dict(base_value)
            override_child = self._as_plain_dict(override_value)
            base_list = self._as_plain_list(base_value)
            override_list = self._as_plain_list(override_value)

            if base_child is not None and override_child is not None:
                merged[key] = self._merge_dicts(base_child, override_child)
            elif base_list is not None and override_list is not None:
                merged[key] = self._merge_lists(base_list, override_list)
            else:
                merged[key] = override_value
        return merged

    def set(self, **params):
        """Set one or more attributes on the instance."""
        for key, value in params.items():
            existing = getattr(self, key, None)
            existing_dict = self._as_plain_dict(existing)
            incoming_dict = self._as_plain_dict(value)
            existing_list = self._as_plain_list(existing)
            incoming_list = self._as_plain_list(value)

            if existing_dict is not None and incoming_dict is not None:
                merged = self._merge_dicts(existing_dict, incoming_dict)
                setattr(self, key, Params(merged))
            elif existing_list is not None and incoming_list is not None:
                merged_list = self._merge_lists(existing_list, incoming_list)
                setattr(self, key, self._normalize_value(merged_list))
            else:
                setattr(self, key, self._normalize_value(value))

    def get(self, *keys):
        """Return selected attributes or all attributes when no keys are provided."""
        if keys:
            return {key: getattr(self, key) for key in keys}
        else:
            return self.__dict__

    def get_dict(self):
        """Return the attributes as a dictionary."""
        result = {}
        for key, value in self.__dict__.items():
            if isinstance(value, Params):
                result[key] = value.get_dict()
            elif isinstance(value, list):
                result[key] = [v.get_dict() if isinstance(v, Params) else v for v in value]
            else:
                result[key] = value
        return result

    def evaluate(self, common_nodes=("COMMON",)):
        """Evaluate values under common nodes and keep raw values when evaluation fails."""

        def eval_value(value):
            if isinstance(value, Params):
                value = value.get_dict()
            if isinstance(value, dict):
                return {k: eval_value(v) for k, v in value.items()}
            if isinstance(value, list):
                return [eval_value(v) for v in value]
            if isinstance(value, str):
                return tryeval(value)
            return value

        for node_name in common_nodes:
            node = getattr(self, node_name, None)
            if node is None:
                continue

            node_dict = node.get_dict() if isinstance(node, Params) else node
            if not isinstance(node_dict, dict):
                continue

            evaluated = {k: eval_value(v) for k, v in node_dict.items()}
            setattr(self, node_name, Params(evaluated))

        return self

    def populate(self, common_nodes=("COMMON",), wrappers=["{$", "$}"]):
        """Populate wrapped placeholders across all attributes from common node values."""
        data = self.get_dict()

        replacements = {}

        for node_name in common_nodes:
            node = data.get(node_name)
            if not isinstance(node, dict):
                continue

            for key, value in node.items():
                
                replacements[str(key)] = value

        # Resolve placeholders inside replacement values themselves.
        resolved_replacements = {k: str(v) for k, v in replacements.items()}
        for _ in range(max(1, len(resolved_replacements))):
            changed = False
            for key, value in list(resolved_replacements.items()):
                new_value = value
                for inner_key, inner_value in resolved_replacements.items():
                    inner_placeholder = f"{wrappers[0]}{inner_key}{wrappers[1]}"
                    new_value = new_value.replace(inner_placeholder, inner_value)
                if new_value != value:
                    resolved_replacements[key] = new_value
                    changed = True
            if not changed:
                break

        raw = json.dumps(data)
        for key, value in resolved_replacements.items():
            placeholder = f"{wrappers[0]}{key}{wrappers[1]}"
            raw = raw.replace(placeholder, value)

        populated = json.loads(raw)
        self.set(**populated)
        return self
        

def get_config(config_path="../config/base.json", overrides={}):
    """Load configuration from JSON and apply runtime overrides."""
    with open(os.path.abspath(config_path), "r") as f:
        config = json.load(f)
    config.update(overrides)
    return config


# Feature 6.1.4
class Config:
    """Feature ID: 6.1.4. Load, merge, evaluate, and populate runtime configuration."""

    def __init__(self, args=None, base_config_path="../config/base.json", overrides=None):

        self.args = args or []
        self.base_config_path = os.path.abspath(base_config_path)
        self.base_dir = os.path.abspath(os.path.join(os.path.dirname(self.base_config_path), ".."))

        self.overrides = {
            a.split("=")[0].strip("-"): tryeval(a.split("=")[1])
            for a in self.args
            if "=" in a
        }
        if isinstance(overrides, dict):
            self.overrides.update(overrides)

        self.config = Params(get_config(config_path=self.base_config_path, overrides=self.overrides))
        self.config.base_dir = self.base_dir
        config_dir = os.path.dirname(self.base_config_path)
        base_config_name = os.path.basename(self.base_config_path).lower()
        for file_name in sorted(os.listdir(config_dir)):
            file_path = os.path.join(config_dir, file_name)
            if not os.path.isfile(file_path):
                continue
            if not file_name.lower().endswith(".json"):
                continue
            if file_name.lower() == base_config_name:
                continue

            with open(file_path, "r") as f:
                app_config = json.load(f)
            if isinstance(app_config, dict):
                self.config.set(**app_config)
       

        self.config = self.config \
                        .evaluate(common_nodes=["COMMON"]) \
                        .populate(common_nodes=["COMMON"], 
                                  wrappers=self.config.COMMON.CONFIG_WRAPPERS)

    

def tryeval(val):
    """Try to evaluate a string value, falling back to the original value on failure."""
    try:
        return eval(val)
    except:
        return val


# Feature 6.1.5
def resolve_dotted(value, path):
    """Feature ID: 6.1.5. Resolve object, Params, and dict values by dotted path syntax."""
    if not path:
        return value

    parts = str(path).split(".", 1)
    head = parts[0]
    tail = parts[1] if len(parts) > 1 else None

    if isinstance(value, Params):
        next_value = getattr(value, head, None)
    elif isinstance(value, dict):
        next_value = value.get(head)
    else:
        next_value = getattr(value, head, None)

    if next_value is None:
        return None
    if tail is None:
        return next_value

    return resolve_dotted(next_value, tail)


# Feature 6.1.6
class HtmlDoc:
    """Feature ID: 6.1.6. Render DataFrame/dict/list datasets into HTML documents via template files."""

    def __init__(self, data, template, title="Output", wrappers=("{$", "$}")):
        self.data = data
        self.template = os.path.abspath(template)
        self.title = title
        if not isinstance(wrappers, (list, tuple)) or len(wrappers) != 2:
            raise ValueError("wrappers must be a 2-item list/tuple, for example ['{$', '$}']")
        self.wrappers = (str(wrappers[0]), str(wrappers[1]))

    def _to_plain(self, value):
        if isinstance(value, Params):
            return value.get_dict()
        if isinstance(value, pd.DataFrame):
            return value.to_dict(orient="records")
        if isinstance(value, dict):
            return {k: self._to_plain(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._to_plain(v) for v in value]
        return value

    def _render_cell(self, value):
        plain = self._to_plain(value)
        if isinstance(plain, (dict, list)):
            headers, rows = self._table_parts(plain)
            thead = ''.join([f"<th>{html.escape(str(h))}</th>" for h in headers])
            tbody = ''.join(rows)
            return f"<table class=\"nested\"><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>"
        return html.escape(str(plain))

    def _value_type_label(self, value):
        plain = self._to_plain(value)
        if isinstance(plain, dict):
            return f"dict[{len(plain)}]"
        if isinstance(plain, list):
            return f"list[{len(plain)}]"
        if plain is None:
            return "NoneType"
        return type(plain).__name__

    def _table_parts(self, dataset):
        plain = self._to_plain(dataset)

        if isinstance(plain, dict):
            headers = ["item", "key", "type", "value"]
            rows = [
                f"<tr><td>{idx}</td><td>{html.escape(str(k))}</td><td>{html.escape(self._value_type_label(v))}</td><td>{self._render_cell(v)}</td></tr>"
                for idx, (k, v) in enumerate(plain.items())
            ]
            return headers, rows

        if isinstance(plain, list):
            if plain and all(isinstance(item, dict) for item in plain):
                columns = []
                for item in plain:
                    for key in item.keys():
                        if key not in columns:
                            columns.append(key)
                headers = ["item"] + columns
                rows = []
                for idx, item in enumerate(plain):
                    cells = [f"<td>{self._render_cell(item.get(col, ''))}</td>" for col in columns]
                    rows.append(f"<tr><td>{idx}</td>{''.join(cells)}</tr>")
                return headers, rows

            headers = ["item", "value"]
            rows = [f"<tr><td>{idx}</td><td>{self._render_cell(v)}</td></tr>" for idx, v in enumerate(plain)]
            return headers, rows

        return ["item", "value"], [f"<tr><td>0</td><td>{html.escape(str(plain))}</td></tr>"]

    def load_template(self):
        with open(self.template, "r", encoding="utf-8") as f:
            return f.read()

    def _ph(self, key):
        return f"{self.wrappers[0]}{key}{self.wrappers[1]}"

    def to_html(self):
        """Render instance data into a full HTML document using the instance template."""
        headers, rows = self._table_parts(self.data)
        template = self.load_template()
        return (
            template
            .replace(self._ph("title"), html.escape(str(self.title)))
            .replace(self._ph("title_colspan"), str(max(1, len(headers))))
            .replace(self._ph("thead"), ''.join([f"<th>{html.escape(str(h))}</th>" for h in headers]))
            .replace(self._ph("tbody"), ''.join(rows))
        )

    def save(self, output_path):
        """Save rendered HTML to output_path."""
        output_path = os.path.abspath(output_path)
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(self.to_html())
        return output_path
    

# Feature 6.1.7
def load_message_lookup(paths):
    """Load and concatenate message dictionary CSV files, deduplicating by code (first wins)."""
    
    files = [os.path.abspath(str(path)) for path in paths if os.path.isfile(os.path.abspath(str(path))) and path.lower().endswith(".csv")]
    
    for path in paths:
        if os.path.isdir(path):
            for file_name in sorted(os.listdir(path)):
                file_path = os.path.join(path, file_name)
                if os.path.isfile(file_path) and file_name.lower().endswith(".csv"):
                    files.append(os.path.abspath(str(file_path)))
    dfs = []
    for file_path in files:
        if os.path.isfile(file_path):
            try:
                df = pd.read_csv(file_path, dtype=str).fillna("")
                if "code" in df.columns and "text" in df.columns:
                    cols = [c for c in ["code", "type", "text"] if c in df.columns]
                    dfs.append(df[cols])
            except Exception:
                pass
    if not dfs:
        return pd.DataFrame(columns=["code", "type", "text"])
    combined = pd.concat(dfs, ignore_index=True)
    if "type" not in combined.columns:
        combined["type"] = ""
    return combined.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)

# Feature 6.1.9
class Logger:
    """Feature ID: 6.1.9. Simple logger that stores messages with timestamps."""
    def __init__(self, log_path=None, start_time=None, max_items=None, verbose=False, 
                 log_types=None, 
                 type_colors=None, message_lookup=None):
        
        self._lock = threading.RLock()
        self.log_types = self._normalize_log_types(log_types) or {0: "NONE", 1: "INFO", 2: "GOOD", 3: "WARN", 4: "ERROR"}
        self.log_type_keys = {v: k for k, v in self.log_types.items()}
        self.type_colors = self._normalize_type_colors(type_colors) or {}
        self.color_reset = "\033[0m"
        self.start_time = start_time or dt.datetime.utcnow()
        self.logs = []
        self.data_map = {}
        self.log_columns = ["timestamp", "type_key", "type", "elapsed_sec", "caller", "message_code", "message", "data"]
        self.message_lookup = message_lookup if message_lookup is not None else pd.DataFrame(columns=["code", "type", "text"])
        self.log_path = log_path
        self._csv_written_count = 0
        self.max_items = max_items
        self.log_folders = {
            'csv': os.path.dirname(log_path) if log_path else None,
            'html': os.path.dirname(log_path) if log_path else None
        } 
        self.verbose_key = self._resolve_verbose_key(verbose)
        os.makedirs(os.path.dirname(log_path), exist_ok=True) if log_path else None
        self._print_color_demo_once()
        self.log(message_code="LOG001", data={"start_time": self.start_time.isoformat()})
        self.log(message_code="LOG002", data={"log_path": log_path})

        n_deleted = 0
        if max_items:
            for ext in ["csv", "html"]:
                folder = self.log_folders[ext]                
                if folder and os.path.isdir(folder):
                    deleted_in_folder = self._cleanup_folder(folder, max_items=max_items, extensions=[ext])
                    n_deleted += deleted_in_folder
                    if deleted_in_folder > 0:
                        self.log(
                            message_code="LOG003",
                            data={"deleted_count": deleted_in_folder, "extension": ext, "folder": folder},
                        )
                
        if max_items:
            self.log(message_code="LOG004", data={"max_items": max_items})
            

    def _cleanup_folder(self, folder_path, max_items=None, extensions=None):
        """Delete oldest files in a folder while respecting max_items and extension filter."""
        if not folder_path or not max_items or not os.path.isdir(folder_path):
            return 0

        files = [
            name
            for name in os.listdir(folder_path)
            if os.path.isfile(os.path.join(folder_path, name))
        ]

        if extensions:
            extension_set = {ext.lower() for ext in extensions}
            files = [name for name in files if os.path.splitext(name)[1].lower().lstrip('.') in extension_set]

        files = sorted(files, key=lambda x: os.path.getctime(os.path.join(folder_path, x)))
        n_deleted = 0
        while len(files) > max_items:
            oldest_name = files.pop(0)
            oldest_path = os.path.join(folder_path, oldest_name)
            if os.path.isfile(oldest_path):
                os.remove(oldest_path)
                n_deleted += 1
        return n_deleted

    def _as_dict(self, value):
        """Convert Params/dict-like values to plain dict for schema parsing."""
        if value is None:
            return None
        if hasattr(value, "get_dict"):
            return value.get_dict()
        if isinstance(value, dict):
            return value
        return None

    def _normalize_log_types(self, log_types):
        """Normalize configured log types into {int: UPPER_LABEL}."""
        raw = self._as_dict(log_types)
        if not raw:
            return None
        normalized = {}
        for key, label in raw.items():
            try:
                key_int = int(key)
            except Exception:
                continue
            label_text = str(label).strip().upper()
            if label_text:
                normalized[key_int] = label_text
        return normalized or None

    def _resolve_color_code(self, value):
        """Resolve ANSI color code from configured name or escape code."""
        if value is None:
            return None
        named_colors = {
            "BLACK": "\033[30m",
            "RED": "\033[31m",
            "GREEN": "\033[32m",
            "YELLOW": "\033[33m",
            "BLUE": "\033[34m",
            "MAGENTA": "\033[35m",
            "CYAN": "\033[36m",
            "WHITE": "\033[37m"
        }
        text = str(value).strip()
        upper = text.upper()
        if upper in named_colors:
            return named_colors[upper]
        if text.startswith("\033[") and text.endswith("m"):
            return text
        return None

    def _normalize_type_colors(self, type_colors):
        """Normalize configured type colors into {UPPER_LABEL: ANSI_CODE}."""
        raw = self._as_dict(type_colors)
        if not raw:
            return None
        normalized = {}
        for key, value in raw.items():
            label = str(key).strip().upper()
            code = self._resolve_color_code(value)
            if label and code:
                normalized[label] = code
        return normalized or None

    def _resolve_verbose_key(self, verbose):
        """Resolve verbosity threshold key from bool, numeric key, or text."""
        if isinstance(verbose, bool):
            return 1 if verbose else 0

        if isinstance(verbose, (int, float)):
            verbose = int(verbose)
            if verbose in self.log_types:
                return verbose

        if isinstance(verbose, str):
            cleaned = verbose.strip().upper()
            if cleaned.isdigit():
                key = int(cleaned)
                if key in self.log_types:
                    return key


            if cleaned in self.log_type_keys:
                return self.log_type_keys[cleaned]

        return 0

    def _resolve_message_key(self, message_type):
        """Resolve log message type key from numeric key or text type."""
        if isinstance(message_type, (int, float)):
            key = int(message_type)
            if key in self.log_types:
                return key
            return 1

        text = str(message_type).strip().upper()
        if text.isdigit():
            key = int(text)
            if key in self.log_types:
                return key
            return 1

        return self.log_type_keys.get(text, 1)

    def _should_print(self, message_key):
        """Print when message level is at/above threshold, unless verbosity is NONE."""
        if self.verbose_key == 0:
            return False
        return message_key >= self.verbose_key

    def _format_console_row(self, message_type, line):
        """Apply console color to the full output line for configured message types."""
        color = self.type_colors.get(message_type)
        if not color:
            return line
        return f"{color}{line}{self.color_reset}"

    def _print_color_demo_once(self):
        """Print one-time console preview of configured type colors at startup."""
        if self.verbose_key == 0:
            return
        demo_types = [self.log_types[k] for k in sorted(self.log_types) if self.log_types[k] != "NONE"]
        for demo_type in demo_types:
            demo_line = f"LOG COLOR DEMO | {demo_type.rjust(5)}"
            print(self._format_console_row(demo_type, demo_line))

    def save_html(self, html_path=None, title="BaseApp Logs", template=None):
        """Save logs as HTML using HtmlDoc template rendering."""
        if html_path is None:
                if not self.log_path:
                        return None
                base_path, _ = os.path.splitext(self.log_path)
                html_path = f"{base_path}.html"

        if template is None:
            raise ValueError("template is required for Logger.save_html")

        HtmlDoc(self.logs, template=template, title=title).save(html_path)

        if getattr(self, "max_items", None):
            html_folder = os.path.dirname(html_path)
            self._cleanup_folder(html_folder, max_items=self.max_items, extensions=["html"])
        return html_path

    def _lookup_entry(self, code):
        """Look up message text and type from the message dictionary by code."""
        if self.message_lookup.empty:
            return "", ""
        match = self.message_lookup.loc[self.message_lookup["code"] == str(code)]
        if match.empty:
            return "", ""
        row = match.iloc[0]
        text = str(row.get("text", ""))
        type_val = str(row.get("type", "")) if "type" in self.message_lookup.columns else ""
        return text, type_val

    def _normalize_data(self, data):
        """Normalize optional per-event data payload into dict or None."""
        if data is None:
            return None
        if isinstance(data, Params):
            data = data.get_dict()
        if not isinstance(data, dict):
            raise ValueError("data must be a dict when provided")
        if len(data) == 0:
            return None
        return data

    def _write_csv(self, append=True):
        """Append any new log entries to the CSV log file."""
        if not self.log_path:
            return

        new_events = self.logs[self._csv_written_count:]
        
        rows = []
        events = new_events if append else self.logs
        for event in events:
            row = {}
            for column in self.log_columns:
                val = event.get(column, None)
                if isinstance(val, (dict, list)):
                    val = json.dumps(val, default=str)
                row[column] = val
            rows.append(row)

        write_header = not os.path.isfile(self.log_path) or self._csv_written_count == 0
        try:
            if append:
                pd.DataFrame(rows, columns=self.log_columns).to_csv(
                    self.log_path, mode='a', header=write_header, index=False, encoding='utf-8'
                )
                self._csv_written_count += len(rows)
            else:
                pd.DataFrame(rows, columns=self.log_columns).to_csv(
                    self.log_path, mode='w', header=True, index=False, encoding='utf-8'
                )
                self._csv_written_count = len(rows)
        except Exception:
            pass


    def update_data_map(self, data=None):
        """Merge one payload into the live snapshot map and return the updated snapshot."""
        normalized = self._normalize_data(data)
        with self._lock:
            if normalized is not None:
                self.data_map = dict_merge(self.data_map, normalized)
            return copy.deepcopy(self.data_map)

    def get_data_map_snapshot(self):
        """Return a deep copy of the current aggregated data snapshot."""
        with self._lock:
            return copy.deepcopy(self.data_map)

    def resolve_data(self, path=None, default=None):
        """Resolve one value from the aggregated data snapshot using dotted paths."""
        snapshot = self.get_data_map_snapshot()
        if not path:
            return snapshot
        value = resolve_dotted(snapshot, path)
        return default if value is None else value

    # Feature 6.1.9.19
    def _get_caller_lineage(self, max_depth=12):
        """Feature ID: 6.1.9.19. Walk the call stack and capture the lineage of callers outside this logger."""
        lineage = []
        frame = inspect.currentframe()
        try:
            # Skip this helper and the log() frame.
            outer = frame.f_back.f_back if frame and frame.f_back else None
            while outer is not None and len(lineage) < max_depth:
                info = inspect.getframeinfo(outer)
                module = inspect.getmodule(outer)
                module_name = module.__name__ if module else None
                cls = None
                self_obj = outer.f_locals.get("self")
                if self_obj is not None:
                    cls = type(self_obj).__name__
                lineage.append({
                    "file": os.path.basename(info.filename),
                    "module": module_name,
                    "class": cls,
                    "function": info.function,
                    "line": info.lineno,
                })
                outer = outer.f_back
        finally:
            del frame
        return lineage

    # Feature 6.1.9.18
    def log(self, message="", message_type=None, data=None, message_code=None, entry=True):
        """Feature ID: 6.1.9.18. Store a message or data with the current timestamp."""
        
        looked_up_text = ""
        looked_up_type = ""

        if message_code:
            looked_up_text, looked_up_type = self._lookup_entry(message_code)

        resolved_message = message if message else looked_up_text
        resolved_type = message_type if message_type is not None else (looked_up_type if looked_up_type else "INFO")
        
        message_key = self._resolve_message_key(resolved_type)
        message_type = self.log_types[message_key]

                
        with self._lock:

            normalized_data = self._normalize_data(data)
            if normalized_data is not None:
                self.data_map = dict_merge(self.data_map, normalized_data)
            
            if entry:
                now = dt.datetime.utcnow()
                elapsed_time = (now - self.start_time).total_seconds()
                caller_lineage = self._get_caller_lineage()
                event = {"timestamp": now.strftime('%Y-%m-%dT%H:%M:%SZ'), 
                        "type_key": message_key,
                        "type": message_type.rjust(5),
                        "elapsed_sec": f"{elapsed_time:06.3f}",
                        "caller": caller_lineage,
                        "message_code": message_code,
                        "message": resolved_message,
                        "data": normalized_data}

                self.logs.append(event)

            if entry and self._should_print(message_key):
                console_line = ' | '.join([str(v) for v in list(event.values())])
                print(self._format_console_row(message_type, console_line))
            self._write_csv()
      


# Feature 6.1.17
def to_json_compatible(value):
    """Feature ID: 6.1.17. Recursively convert a value to a JSON-serializable primitive.

    Handles the following non-primitive types that arise during BaseApp output serialization:

    - ``Params`` instances are expanded to their underlying dict via ``get_dict()``.
    - ``dict`` values are processed key-by-key.
    - ``list`` / ``tuple`` values are processed element-by-element.
    - ``pd.DataFrame`` is converted to a list of record dicts and then processed.
    - ``np.ndarray`` is converted to a Python list and then processed.
    - ``pd.NA`` is mapped to ``None``.
    - ``np.floating`` values are mapped to Python ``float``; NaN/Inf become ``None``.
    - ``np.integer`` values are mapped to Python ``int``.
    - ``np.bool_`` values are mapped to Python ``bool``.
    - Python ``float`` NaN/Inf values are mapped to ``None``.
    - All other values are returned unchanged.

    Args:
        value: Any Python object.

    Returns:
        A JSON-serializable equivalent of ``value``.
    """
    if isinstance(value, Params):
        value = value.get_dict()

    if isinstance(value, dict):
        return {k: to_json_compatible(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [to_json_compatible(v) for v in value]

    if isinstance(value, pd.DataFrame):
        return to_json_compatible(value.to_dict(orient="records"))

    if isinstance(value, np.ndarray):
        return [to_json_compatible(v) for v in value.tolist()]

    if value is pd.NA:
        return None

    if isinstance(value, np.floating):
        numeric = float(value)
        if math.isnan(numeric) or math.isinf(numeric):
            return None
        return numeric

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.bool_):
        return bool(value)

    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None

    return value


# Feature 6.1.10
def save(data, path, format="json", **kwargs):
    """Feature ID: 6.1.10. Save data to a file in the specified format, supporting json, csv, and html."""

    os.makedirs(os.path.dirname(path), exist_ok=True)
    if format == "json":
        kwargs1 = {"indent": 4, "default": str, "allow_nan": False}
        kwargs1.update(kwargs)
        data = to_json_compatible(data)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, **kwargs1)
    elif format == "csv":
        kwargs1 = {"index": False, "encoding": "utf-8"}
        kwargs1.update(kwargs)
        if isinstance(data, pd.DataFrame):
            data.to_csv(path, **kwargs1)
        elif isinstance(data, list) and all(isinstance(item, dict) for item in data):
            pd.DataFrame(data).to_csv(path, **kwargs1)
        else:
            raise ValueError("CSV format requires a DataFrame or list of dicts.")
    elif format == "html":
        if not(isinstance(data, HtmlDoc)):
            data = HtmlDoc(data=data, 
                           template=kwargs.get("template", None),
                           title=kwargs.get("title", "Output"))
        data.save(path)
    else:
        raise ValueError(f"Unsupported format: {format}")
    
    if os.path.isfile(path):
        return path
    
    return None


# Feature 6.1.8
def create_logger(settings):
    """Feature ID: 6.1.8. Create logger instance using configured paths, verbosity, and message dictionaries."""

    messages_dir = settings.get("messages_dir")
    if messages_dir and not os.path.isabs(str(messages_dir)):
        base_dir = settings.get("base_dir")
        anchor_dir = base_dir or os.getcwd()
        messages_dir = os.path.abspath(os.path.join(str(anchor_dir), str(messages_dir)))

    message_lookup = load_message_lookup([messages_dir] if messages_dir else [])

    logger_name = settings.get("name", "logger")
    logger_dict = {

        "log_path": None,
        "start_time": settings.get("start_time", dt.datetime.utcnow()),
        "max_items": settings.get("max_items", None),
        "verbose": settings.get("verbose", False),
        "log_types": settings.get("types", None),
        "type_colors": settings.get("colors", None),
        "message_lookup": message_lookup,
    }

    if isinstance(settings.get('start_time'), str):
        try:
            logger_dict["start_time"] = dt.datetime.strptime(settings.get('start_time'), "%Y-%m-%dT%H:%M:%S")
        except Exception:
            logger_dict["start_time"] = dt.datetime.utcnow()

    if settings.get("path", None) is not None:
        log_path = settings.get("path")
        os.makedirs(log_path, exist_ok=True)
        log_path = "/".join([
            log_path,
            f"{logger_dict.get('start_time').strftime('%Y-%m-%dT%H-%M-%SZ')}.csv",
        ])
        logger_dict["log_path"] = log_path
    else:
        logger_dict["log_path"] = None

    logger = Logger(**logger_dict)
    logger.log(message_code="LOG005", data=logger_dict)
    return logger


def add_to_dict(DICT={}, **params):
    """Convert keyword arguments into a plain dictionary."""
    DICT.update(params)
    return DICT

def try_except(default=None, logger=None, func=print, args=[], kwargs={}):
    try:
        return func(*args, **kwargs)
    except Exception as e:
        if logger:
            logger.log(message_code="ERR001", data={"error": str(e)})
        return default

def as_list(value):
    """Convert a value into a list if it is not already a list."""
    if isinstance(value, list):
        return value
    if value is None:
        return []
    if isinstance(value, tuple):
        return list(value)
    return [value]


# Feature 6.1.11
class AppManager:
    """Feature ID: 6.1.11. Main runtime lifecycle class that loads inputs, processes data, stores outputs, and finalizes run state."""
    
    def __init__(self, config=None, logger=None, results=None):
        """Run data loading pipeline with optional injected runtime dependencies."""

        
        if config is not None:
            self.base_dir = config.base_dir
            self.CONFIG = config
            self.config = self.CONFIG
            self.RESULTS = results if results is not None else self.create_results()
            self.results = self.RESULTS
            logger_settings = self.CONFIG.LOG.get_dict() if hasattr(self.CONFIG, "LOG") else {}
            logger_settings["base_dir"] = self.base_dir
            logger_settings["start_time"] = self.RESULTS.start_time

            self.logger_name = logger_settings.get("name", "logger")
            setattr(self, self.logger_name, logger if logger is not None else create_logger(logger_settings))
            self.LOGS = getattr(getattr(self, self.logger_name), "logs", None)

        else:
            self.base_dir = os.getcwd()
            self.CONFIG = config if isinstance(config, Params) else Params(config)
            self.config = self.CONFIG
            self.RESULTS = results if results is not None else Params()
            self.results = self.RESULTS
            if not hasattr(self.RESULTS, "start_time"):
                self.RESULTS.start_time = dt.datetime.utcnow()
            if not hasattr(self.RESULTS, "run_id"):
                self.RESULTS.run_id = self.CONFIG.COMMON.RUN_ID or str(uuid.uuid4().hex[:6]).upper()
            if not hasattr(self.RESULTS, "signature"):
                self.RESULTS.signature = f"run_id={self.RESULTS.run_id} start_time={self.RESULTS.start_time}"
            if not hasattr(self.RESULTS, "app_title"):
                self.RESULTS.app_title = f"{self.CONFIG.APP.name} v{self.CONFIG.APP.version}"
            if not hasattr(self.RESULTS, "html_template"):
                self.RESULTS.html_template = self.CONFIG.COMMON.HTML_TEMPLATE
            setattr(self, 'logger', logger if logger is not None else Logger(start_time=self.RESULTS.start_time))
            self.LOGS = getattr(self.logger, "logs", None)

        # Per-input delta state: maps input_key -> {file_key: mtime}. Persists across load_data() calls
        # so that subsequent runs in delta mode can detect which files are new or changed.
        self.input_meta = {}

        # Output delta manifest: maps full_output_path -> {"sha256": str, "mtime": float}. Loaded
        # from <OUTPUT_PATH>/manifest.json on init (if present) and rewritten after every save so
        # output delta mode can skip artifacts whose serialized content matches the on-disk file.
        self.output_manifest_path = None
        common = getattr(self.CONFIG, "COMMON", None)
        output_root = getattr(common, "OUTPUT_PATH", None) if common is not None else None
        if output_root:
            self.output_manifest_path = os.path.join(str(output_root), "manifest.json")
        self.output_manifest = {}
        if self.output_manifest_path and os.path.isfile(self.output_manifest_path):
            try:
                with open(self.output_manifest_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self.output_manifest = loaded
            except (OSError, ValueError):
                self.output_manifest = {}

        # Lock that serialises output_manifest reads/writes across worker threads (Feature 6.1.11).
        self._output_manifest_lock = threading.Lock()

        self.logger.log(
            f"{self.__class__.__name__} initialized",
            message_code="BASE001",
            data={"instance": str(self)},
        )
        self.logger.log(message_code="BASE002", data={"run_id": self.RESULTS.run_id})
        
    def create_results(self):
        """Create initialized run metadata derived from current config."""
        results = Params()
        results.start_time = (
            dt.datetime.strptime(self.CONFIG.COMMON.START_TIME, self.CONFIG.COMMON.DATETIME_FORMAT)
            if self.CONFIG.COMMON.START_TIME
            else dt.datetime.utcnow()
        )
        results.run_id = self.CONFIG.COMMON.RUN_ID or str(uuid.uuid4().hex[:6]).upper()
        results.signature = f"run_id={results.run_id} start_time={results.start_time}"
        results.app_title = f"{self.CONFIG.APP.name} v{self.CONFIG.APP.version}"
        results.html_template = self.CONFIG.COMMON.HTML_TEMPLATE
        return results

    def touch_result(self, source, when=None):
        """Deprecated no-op kept for backwards compatibility; output delta now uses content checksums."""
        return None

    def _data_checksum(self, data):
        """Return a sha256 hex digest of a JSON-serialized canonical form of ``data``."""
        try:
            payload = json.dumps(data, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            payload = repr(data).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _save_output_manifest(self):
        """Persist the in-memory output manifest to <OUTPUT_PATH>/manifest.json."""
        if not self.output_manifest_path:
            return
        try:
            os.makedirs(os.path.dirname(self.output_manifest_path), exist_ok=True)
            with open(self.output_manifest_path, "w", encoding="utf-8") as f:
                json.dump(self.output_manifest, f, indent=2, default=str)
        except OSError:
            pass

    # Feature 6.1.11.1
    def load_data(self):
        """Feature ID: 6.1.11.1. Load all enabled config.input entries and store results under configured targets."""
        input_node = getattr(self.CONFIG, "INPUT", None)
        if input_node is None:
            return

        for input_key, input_settings in input_node.get_dict().items():
            if not isinstance(input_settings, dict):
                continue

            if not bool(input_settings.get("load", False)):
                continue

            target_name = input_settings.get("target", input_key)

            # Instantiate the loader with prior per-file mtimes so delta mode can short-circuit
            # files that have not changed since the previous cycle.
            prior_last_modified = dict(self.input_meta.get(input_key, {}))
            loader = DataLoader(
                source=input_settings,
                logger=self.logger,
                data=getattr(self.RESULTS, target_name, {}),
                base_dir=self.base_dir,
                last_modified=prior_last_modified,
            )
            setattr(self.RESULTS, target_name, loader.load())
            # Persist refreshed mtime map for the next cycle / delta scan.
            self.input_meta[input_key] = loader.last_modified

            # Publish lightweight runtime state into the logger data map for external monitoring.
            self.logger.log(
                data={
                    "results": {
                        f"{target_name}_loaded": True,
                    }
                },
                entry=False,
            )
            
            getattr(self, self.logger_name).log(
                message_code="BASE003",
                message=f"Loaded input '{input_key}' into RESULTS.{target_name}",        
                data={"num_of_results": len(self.RESULTS.get_dict().keys())},
            )

    # Feature 6.1.11.2
    def process_data(self):
        """Feature ID: 6.1.11.2. Run configured processing steps against loaded results data."""
        process_node = getattr(self.CONFIG, "PROCESS", None)
        if process_node is None:
            return

        for step_id, step_settings in process_node.get_dict().items():
            if not isinstance(step_settings, dict):
                continue
            if step_settings.get("run", False) is not True:
                continue

            source_name = step_settings.get("source")
            source_value = getattr(self.RESULTS, source_name, None) if source_name else None

            context = {}
            for alias, result_name in step_settings.get("context", {}).items():
                context[alias] = getattr(self.RESULTS, result_name, None)
            context["source_data"] = source_value

            source_df = source_value.copy() if isinstance(source_value, pd.DataFrame) else pd.DataFrame()

            result = DataFrameConverter(
                conversions=step_settings.get("conversions", []),
                verbose=bool(step_settings.get("verbose", False)),
                context=context,
                log_func=(lambda msg, code=None, data=None: self.logger.log(message=msg, message_code=code, data=data))
            ).apply(source_df)

            # Store the result regardless of type; custom scope steps return dicts/lists.
            setattr(self.RESULTS, step_settings.get("target", step_id), result)

    def _to_raw_data(self, value, max_items=None):
        """Convert Params/objects into plain dict/list primitives with optional item limits."""
        if isinstance(value, str):
            return value

        if isinstance(value, Params):
            value = value.get_dict()

        if isinstance(value, pd.DataFrame):
            rows = value.to_dict(orient="records")
            rows = rows if max_items is None else rows[:max_items]
            return [self._to_raw_data(row, max_items=max_items) for row in rows]

        if isinstance(value, dict):
            items = value.items() if max_items is None else list(value.items())[:max_items]
            return {k: self._to_raw_data(v, max_items=max_items) for k, v in items}

        if isinstance(value, (list, tuple)):
            items = value if max_items is None else value[:max_items]
            return [self._to_raw_data(v, max_items=max_items) for v in items]

        if hasattr(value, "__dict__") and not isinstance(value, (str, int, float, bool, bytes)):
            attrs = vars(value)
            items = attrs.items() if max_items is None else list(attrs.items())[:max_items]
            return {k: self._to_raw_data(v, max_items=max_items) for k, v in items}

        return value

    def _split_output_path(self, output_dict, item_keys):
        """Build the output path for one split artifact item."""
        output_path = output_dict.get("path")
        output_file = output_dict.get("file")
        output_format = output_dict.get("format", "json")
        key_parts = [str(key) for key in item_keys]

        if output_file:
            return os.path.join(output_path, *key_parts, output_file)

        leaf_name = key_parts[-1] if key_parts else "output"
        parent_parts = key_parts[:-1]
        return os.path.join(output_path, *parent_parts, f"{leaf_name}.{output_format}")

    def _save_output_artifact(self, output_key, output_dict, data, item_keys=None):
        """Save one output payload and emit the normal output logs.

        Honors output-level `delta` mode: when `delta` is true, the artifact is skipped if its
        content checksum matches the entry recorded in ``self.output_manifest`` for this path AND
        the on-disk file mtime matches what we last wrote. Otherwise the artifact is (re)saved
        and the manifest is updated and flushed to ``<OUTPUT_PATH>/manifest.json``.
        """
        if data is None:
            return

        output_path = output_dict.get("path")
        os.makedirs(output_path, exist_ok=True)

        item_keys = list(item_keys or [])

        if not item_keys:
            output_file = output_dict.get("file", f"{output_key}.json")
            full_output_path = os.path.join(output_path, output_file)
        else:
            full_output_path = self._split_output_path(output_dict, item_keys)

        delta_mode = bool(output_dict.get("delta", False))
        new_checksum = self._data_checksum(data) if delta_mode else None

        # Delta mode: skip when the manifest entry matches both the new checksum and the on-disk
        # file's current mtime (no external tampering since we last wrote it).
        if delta_mode and os.path.isfile(full_output_path):
            with self._output_manifest_lock:
                entry = self.output_manifest.get(full_output_path)
            try:
                current_mtime = os.path.getmtime(full_output_path)
            except OSError:
                current_mtime = None
            if (
                isinstance(entry, dict)
                and entry.get("sha256") == new_checksum
                and current_mtime is not None
                and entry.get("mtime") == current_mtime
            ):
                log_data = {
                    "output_key": output_key,
                    "path": full_output_path,
                    "skipped": True,
                    "delta": True,
                    "sha256": new_checksum,
                }
                if item_keys:
                    log_data["item_key"] = "/".join([str(k) for k in item_keys])
                self.logger.log(message_code="BASE003", data=log_data)
                return

        artifact_data = data
        if output_dict.get('format', '').lower() == 'html':
            title_suffix = output_key.capitalize() if not item_keys else f"{output_key.capitalize()} {' / '.join([str(k) for k in item_keys])}"
            artifact_data = HtmlDoc(
                data=data,
                template=output_dict.get("kwargs", {}).get("template", self.RESULTS.html_template),
                title=f"{self.RESULTS.app_title} {self.RESULTS.signature} {title_suffix}",
            )

        save_kwargs = {
            "data": artifact_data,
            "path": full_output_path,
            "format": output_dict.get("format", "json"),
        }
        save_kwargs.update(output_dict.get("kwargs", {}))
        saved_path = save(**save_kwargs)

        # Update and persist the manifest entry for this artifact (delta mode only).
        if delta_mode and saved_path:
            try:
                saved_mtime = os.path.getmtime(saved_path)
            except OSError:
                saved_mtime = None
            with self._output_manifest_lock:
                self.output_manifest[full_output_path] = {
                    "sha256": new_checksum,
                    "mtime": saved_mtime,
                }
                self._save_output_manifest()

        log_data = {"output_key": output_key, "path": full_output_path}
        if delta_mode:
            log_data["sha256"] = new_checksum
        if item_keys:
            log_data["item_key"] = "/".join([str(k) for k in item_keys])
        self.logger.log(message_code="BASE003", data=log_data)

        if output_dict.get("open", False) and saved_path:
            open_key = output_key if not item_keys else f"{output_key}:{'/'.join([str(k) for k in item_keys])}"
            self.open_output(saved_path, open_key)

    def _store_split_outputs(self, output_key, output_dict, data, split_depth, item_keys=None):
        """Recursively store split outputs across one or more dict layers."""
        item_keys = list(item_keys or [])

        if split_depth > 0 and isinstance(data, dict):
            for child_key, child_value in data.items():
                self._store_split_outputs(
                    output_key,
                    output_dict,
                    child_value,
                    split_depth - 1,
                    item_keys=item_keys + [child_key],
                )
            return

        self._save_output_artifact(output_key, output_dict, data, item_keys=item_keys)
       
    # Feature 6.1.11.9
    def _store_one_output(self, output_key, output_dict):
        """Feature ID: 6.1.11.9. Resolve, convert, and persist a single configured output artifact; callable from both sequential and concurrent paths."""
        data = resolve_dotted(self, output_dict.get("source", output_key))
        data = self._to_raw_data(
            data,
            max_items=output_dict.get("max_items", None),
        )
        split_setting = output_dict.get("split", False)
        split_depth = int(split_setting) if isinstance(split_setting, (int, float)) else (1 if split_setting else 0)
        if split_depth > 0 and isinstance(data, dict):
            self._store_split_outputs(output_key, output_dict, data, split_depth)
        else:
            self._save_output_artifact(output_key, output_dict, data)

    # Feature 6.1.11.3
    def store_outputs(self, output_config='CONFIG.OUTPUT', outputs=[]):
        """Feature ID: 6.1.11.3. Persist configured outputs, sequentially or concurrently based on CONFIG.COMMON.OUTPUT_WORKERS. Workers > 1 dispatches each artifact to a ThreadPoolExecutor; per-worker exceptions are caught and logged without aborting remaining writes."""

        pending = [
            (output_key, output_dict)
            for output_key, output_dict in resolve_dotted(self, output_config).get_dict().items()
            if (not outputs or output_key in outputs) and output_dict.get("store", True)
        ]

        max_workers = int(getattr(getattr(self.CONFIG, "COMMON", None), "OUTPUT_WORKERS", 0) or 0)

        if max_workers <= 1 or len(pending) <= 1:
            # Sequential path — unchanged behavior.
            for output_key, output_dict in pending:
                self._store_one_output(output_key, output_dict)
        else:
            # Concurrent path — dispatch independent artifacts to a thread pool.
            n_workers = min(max_workers, len(pending))
            with ThreadPoolExecutor(max_workers=n_workers) as executor:
                futures = {
                    executor.submit(self._store_one_output, output_key, output_dict): output_key
                    for output_key, output_dict in pending
                }
                for future in as_completed(futures):
                    exc = future.exception()
                    if exc:
                        self.logger.log(
                            message_code="BASEW06",
                            message_type="WARN",
                            data={"output_key": futures[future], "error": str(exc)},
                        )

    def open_output(self, saved_path, output_key):
        try:
            if sys.platform.startswith("win") and hasattr(os, "startfile"):
                os.startfile(saved_path)
            else:
                webbrowser.open(f"file://{os.path.abspath(saved_path).replace(os.sep, '/')}")
            getattr(self, self.logger_name).log(
                message_code="BASE004",
                data={"output_key": output_key, "path": saved_path},
            )
        except Exception as ex:
            getattr(self, self.logger_name).log(
                message_code="BASEW05",
                message_type="WARN",
                data={"output_key": output_key, "error": str(ex)},
            )
        
    
    # Feature 6.1.11.4
    def close(self):
        """Feature ID: 6.1.11.4. Finalize the run state and prepare runtime artifacts for saving."""
        self.RESULTS.end_time = dt.datetime.utcnow()
        self.RESULTS.elapsed_seconds = (self.RESULTS.end_time - self.RESULTS.start_time).total_seconds()
        self.logger.log(
            data={
                "results": {
                    "end_time": self.RESULTS.end_time,
                    "elapsed_seconds": self.RESULTS.elapsed_seconds,
                }
            },
            entry=False,
        )
        self.logger.log(
            f"{self.__class__.__name__} completed",
            message_code="BASE999",
            data={"elapsed_seconds": round(self.RESULTS.elapsed_seconds, 2)},
        )
        self.LOGS = self.logger.logs

    # Feature 6.1.11.5
    def run(self):
        """Feature ID: 6.1.11.5. Run the full data loading and processing pipeline, then store outputs."""
        self.load_data()
        self.process_data()
        self.close()
        self.store_outputs()
        