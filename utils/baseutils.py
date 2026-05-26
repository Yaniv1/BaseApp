import pandas as pd
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
from functools import wraps
import math

try:
    from .datautils import DataFrameConverter, DataLoader
except ImportError:
    try:
        from utils.datautils import DataFrameConverter, DataLoader
    except ImportError:
        from datautils import DataFrameConverter, DataLoader


def trackit(func):
    """Track function execution and return result with extensible metrics."""

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

def dict_merge(base, override):
    """Recursively merge override dict into base dict and return merged result."""
    merged = dict(base)
    for key, override_value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(override_value, dict):
            merged[key] = dict_merge(base_value, override_value)
        else:
            merged[key] = override_value
    return merged

class Params:
    """Simple container that exposes dictionary values as attributes."""
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


class Config:
    """Load, merge, evaluate, and populate runtime configuration."""

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


class HtmlDoc:
    """Render DataFrame/dict/list datasets into HTML documents via template files."""

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


class Logger:
    """Simple logger that stores messages with timestamps."""
    def __init__(self, log_path=None, start_time=None, max_items=None, verbose=False, 
                 log_types=None, 
                 type_colors=None, message_lookup=None):
        self.log_types = self._normalize_log_types(log_types) or {0: "NONE", 1: "INFO", 2: "GOOD", 3: "WARN", 4: "ERROR"}
        self.log_type_keys = {v: k for k, v in self.log_types.items()}
        self.type_colors = self._normalize_type_colors(type_colors) or {}
        self.color_reset = "\033[0m"
        self.start_time = start_time or dt.datetime.utcnow()
        self.logs = []
        self.log_columns = ["timestamp", "type_key", "type", "elapsed_sec", "message_code", "message", "data"]
        self.message_lookup = message_lookup if message_lookup is not None else pd.DataFrame(columns=["code", "type", "text"])
        self.log_path = log_path
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

    def _write_csv(self):
        """Persist all logs with a stable schema including a single data column."""
        if not self.log_path:
            return

        rows = []
        for event in self.logs:
            row = {column: event.get(column, None) for column in self.log_columns}
            rows.append(row)

        pd.DataFrame(rows, columns=self.log_columns).to_csv(self.log_path, mode='w', header=True, index=False)

    def log(self, message="", message_type=None, data=None, message_code=None):
        """ store a message with the current timestamp."""
        looked_up_text = ""
        looked_up_type = ""
        if message_code:
            looked_up_text, looked_up_type = self._lookup_entry(message_code)

        resolved_message = message if message else looked_up_text
        resolved_type = message_type if message_type is not None else (looked_up_type if looked_up_type else "INFO")

        message_key = self._resolve_message_key(resolved_type)
        message_type = self.log_types[message_key]

        now = dt.datetime.utcnow()
        elapsed_time = (now - self.start_time).total_seconds()
        event = {"timestamp": now.strftime('%Y-%m-%dT%H:%M:%SZ'), 
                 "type_key": message_key,
                 "type": message_type.rjust(5),
                 "elapsed_sec": f"{elapsed_time:06.3f}",
                 "message_code": message_code,
                 "message": resolved_message,
                 "data": self._normalize_data(data)}

        self.logs.append(event)
        if self._should_print(message_key):
            console_line = ' | '.join([str(v) for v in list(event.values())])
            print(self._format_console_row(message_type, console_line))
        self._write_csv()
       


def save(data, path, format="json", **kwargs):
    """Save data to a file in the specified format, supporting json, csv, and html."""

    def to_json_compatible(value):
        """Convert NaN-like and non-primitive values into JSON-compatible equivalents."""
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

    os.makedirs(os.path.dirname(path), exist_ok=True)
    if format == "json":
        kwargs1 = {"indent": 4, "default": str, "allow_nan": False}
        kwargs1.update(kwargs)
        data = to_json_compatible(data)
        with open(path, "w") as f:
            json.dump(data, f, **kwargs1)
    elif format == "csv":
        if isinstance(data, pd.DataFrame):
            kwargs1 = {"index": False}
            kwargs1.update(kwargs)
            data.to_csv(path, index=False, **kwargs1)
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


def create_logger(settings):
    """Create logger instance using configured paths, verbosity, and message dictionaries."""

    messages_dir = settings.get("messages_dir")
    if messages_dir and not os.path.isabs(str(messages_dir)):
        base_dir = settings.get("base_dir")
        anchor_dir = base_dir or os.getcwd()
        messages_dir = os.path.abspath(os.path.join(str(anchor_dir), str(messages_dir)))

    message_lookup = load_message_lookup([messages_dir] if messages_dir else [])

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




class Main:
    """Main class for running the data loading and label mapping pipeline."""
    
    def __init__(self, config=None, logger=None, results=None):
        """Run data loading pipeline with optional injected runtime dependencies."""

        
        if config is not None:
            self.base_dir = config.base_dir
            self.config = config
            self.results = results if results is not None else self.create_results()
            logger_settings = self.config.log.get_dict() if hasattr(self.config, "log") else {}
            logger_settings["base_dir"] = self.base_dir
            logger_settings["start_time"] = self.results.start_time
            self.logger = logger if logger is not None else create_logger(logger_settings)

        else:
            self.base_dir = os.getcwd()
            self.config = config if isinstance(config, Params) else Params(config)
            self.results = results if results is not None else Params()
            if not hasattr(self.results, "start_time"):
                self.results.start_time = dt.datetime.utcnow()
            if not hasattr(self.results, "run_id"):
                self.results.run_id = self.config.COMMON.RUN_ID or str(uuid.uuid4().hex[:6]).upper()
            if not hasattr(self.results, "signature"):
                self.results.signature = f"run_id={self.results.run_id} start_time={self.results.start_time}"
            if not hasattr(self.results, "app_title"):
                self.results.app_title = f"{self.config.app.name} v{self.config.app.version}"
            if not hasattr(self.results, "html_template"):
                self.results.html_template = self.config.COMMON.HTML_TEMPLATE
            self.logger = logger if logger is not None else Logger(start_time=self.results.start_time)

        self.logger.log(
            f"{self.__class__.__name__} initialized",
            message_code="BASE001",
            data={"instance": str(self)},
        )
        self.logger.log(message_code="BASE002", data={"run_id": self.results.run_id})

    def create_results(self):
        """Create initialized run metadata derived from current config."""
        results = Params()
        results.start_time = (
            dt.datetime.strptime(self.config.COMMON.START_TIME, self.config.COMMON.DATETIME_FORMAT)
            if self.config.COMMON.START_TIME
            else dt.datetime.utcnow()
        )
        results.run_id = self.config.COMMON.RUN_ID or str(uuid.uuid4().hex[:6]).upper()
        results.signature = f"run_id={results.run_id} start_time={results.start_time}"
        results.app_title = f"{self.config.app.name} v{self.config.app.version}"
        results.html_template = self.config.COMMON.HTML_TEMPLATE
        return results

    def load_data(self):
        """Load all enabled config.input entries and store results under configured targets."""
        input_node = getattr(self.config, "input", None)
        if input_node is None:
            return

        loaded_targets = []
        for input_key, input_settings in input_node.get_dict().items():
            if not isinstance(input_settings, dict):
                continue

            if not bool(input_settings.get("load", False)):
                continue

            target_name = input_settings.get("target", input_key)

            setattr(self.results, target_name, 
                    DataLoader( source=input_settings, 
                                logger=self.logger, 
                                data=getattr(self.results, target_name, {}),
                                base_dir=self.base_dir).load())

    def process_data(self):
        """Run configured processing steps against loaded results data."""
        process_node = getattr(self.config, "process", None)
        if process_node is None:
            return

        for step_id, step_settings in process_node.get_dict().items():
            if not isinstance(step_settings, dict):
                continue
            if step_settings.get("run", False) is not True:
                continue

            source_name = step_settings.get("source")
            source_value = getattr(self.results, source_name, None) if source_name else None

            context = {}
            for alias, result_name in step_settings.get("context", {}).items():
                context[alias] = getattr(self.results, result_name, None)
            context["source_data"] = source_value

            source_df = source_value.copy() if isinstance(source_value, pd.DataFrame) else pd.DataFrame()

            result = DataFrameConverter(
                conversions=step_settings.get("conversions", []),
                verbose=bool(step_settings.get("verbose", False)),
                context=context,
                log_func=(lambda msg, code=None, data=None: self.logger.log(message=msg, message_code=code, data=data))
            ).apply(source_df)

            setattr(self.results, step_settings.get("target", step_id), result)

    def _to_raw_data(self, value, max_items=None):
        """Convert Params/objects into plain dict/list primitives with optional item limits."""

        if isinstance(value, str):
            v = value.strip()
            if (v.startswith("{") and v.endswith("}")) or (v.startswith("[") and v.endswith("]")):
                self._json_parse_stats["attempted"] += 1
                try:
                    parsed = json.loads(v)
                    self._json_parse_stats["parsed"] += 1
                    return self._to_raw_data(parsed, max_items=max_items)
                except Exception as ex:
                    self._json_parse_stats["failed"] += 1
                    if self.logger:
                        self.logger.log(
                            message_code="BASEW11",
                            message_type="WARN",
                            data={"error": str(ex), "sample": v[:200]},
                        )
                    return value
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
       
    
    def store_outputs(self, outputs=[]):

        for output_key, output_dict in self.config.output.get_dict().items():
            if outputs and output_key not in outputs:
                continue

            store = output_dict.get("store", True)
            if not store:
                continue

            data = getattr(self, output_dict.get("source", output_key), None)
            self._json_parse_stats = {"attempted": 0, "parsed": 0, "failed": 0}
            data = self._to_raw_data(data, max_items=output_dict.get("max_items", None))

            if self._json_parse_stats["attempted"] > 0:
                self.logger.log(
                    message_code="BASE011",
                    data={"output_key": output_key} | self._json_parse_stats,
                )

            if output_dict.get('format', '').lower() == 'html':
                data = HtmlDoc(data=data, 
                        template=output_dict.get("template", self.results.html_template),
                        title=f"{self.results.app_title} {self.results.signature} {output_key.capitalize()}")
            
            if data is not None:
                output_path = output_dict.get("path")                
                os.makedirs(output_path, exist_ok=True)
                output_file = output_dict.get("file", f"{output_key}.json")
                full_output_path = os.path.join(output_path, output_file)

                save_kwargs = {'data':data, 'path': full_output_path,
                               'format':output_dict.get("format", "json")}                
                save_kwargs.update(output_dict.get("kwargs", {}))
                saved_path = save(**save_kwargs)
                self.logger.log(
                    message_code="BASE003",
                    data={"output_key": output_key, "path": full_output_path},
                )

                open_output = output_dict.get("open", False)
                
                if open_output and saved_path:    
                    self.open_output(saved_path, output_key)

    def open_output(self, saved_path, output_key):
        try:
            if sys.platform.startswith("win") and hasattr(os, "startfile"):
                os.startfile(saved_path)
            else:
                webbrowser.open(f"file://{os.path.abspath(saved_path).replace(os.sep, '/')}")
            self.logger.log(
                message_code="BASE004",
                data={"output_key": output_key, "path": saved_path},
            )
        except Exception as ex:
            self.logger.log(
                message_code="BASEW05",
                message_type="WARN",
                data={"output_key": output_key, "error": str(ex)},
            )
        
    
    def close(self):
        """Finalize the run by logging the total elapsed time and saving results."""
        self.results.end_time = dt.datetime.utcnow()
        self.results.elapsed_seconds = (self.results.end_time - self.results.start_time).total_seconds()
        self.logger.log(
            f"{self.__class__.__name__} completed",
            message_code="BASE999",
            data={"elapsed_seconds": round(self.results.elapsed_seconds, 2)},
        )
        self.logs = self.logger.logs
        self.store_outputs()

        