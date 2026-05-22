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

    def _table_parts(self, dataset):
        plain = self._to_plain(dataset)

        if isinstance(plain, dict):
            headers = ["key", "value"]
            rows = [f"<tr><td>{html.escape(str(k))}</td><td>{self._render_cell(v)}</td></tr>" for k, v in plain.items()]
            return headers, rows

        if isinstance(plain, list):
            if plain and all(isinstance(item, dict) for item in plain):
                columns = []
                for item in plain:
                    for key in item.keys():
                        if key not in columns:
                            columns.append(key)
                rows = []
                for item in plain:
                    cells = [f"<td>{self._render_cell(item.get(col, ''))}</td>" for col in columns]
                    rows.append(f"<tr>{''.join(cells)}</tr>")
                return columns, rows

            headers = ["index", "value"]
            rows = [f"<tr><td>{idx}</td><td>{self._render_cell(v)}</td></tr>" for idx, v in enumerate(plain)]
            return headers, rows

        return ["value"], [f"<tr><td>{html.escape(str(plain))}</td></tr>"]

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
    

def load_message_dict(paths):
    """Load and concatenate message dictionary CSV files, deduplicating by code (first wins)."""
    dfs = []
    for path in paths:
        path_str = os.path.abspath(str(path))
        if os.path.isfile(path_str):
            try:
                df = pd.read_csv(path_str, dtype=str).fillna("")
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
    def __init__(self, log_path=None, start_time=None, max_items=None, verbose=False, log_types=None, type_colors=None, message_dict=None):
        self.log_types = self._normalize_log_types(log_types) or {0: "NONE", 1: "INFO", 2: "GOOD", 3: "WARN", 4: "ERROR"}
        self.log_type_keys = {v: k for k, v in self.log_types.items()}
        self.type_colors = self._normalize_type_colors(type_colors) or {}
        self.color_reset = "\033[0m"
        self.start_time = start_time or dt.datetime.utcnow()
        self.logs = []
        self.log_columns = ["timestamp", "type_key", "type", "elapsed_sec", "message_code", "message", "data"]
        self.message_dict = message_dict if message_dict is not None else pd.DataFrame(columns=["code", "text"])
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
        if self.message_dict.empty:
            return "", ""
        match = self.message_dict.loc[self.message_dict["code"] == str(code)]
        if match.empty:
            return "", ""
        row = match.iloc[0]
        text = str(row.get("text", ""))
        type_val = str(row.get("type", "")) if "type" in self.message_dict.columns else ""
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

    os.makedirs(os.path.dirname(path), exist_ok=True)
    if format == "json":
        kwargs1 = {"indent": 4, "default": str}
        kwargs1.update(kwargs)
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


class Main:
    """Main class for running the data loading and label mapping pipeline."""
    
    def __init__(self, args=[], base_config_path="../config/base.json"):
        """Run data loading and label mapping pipeline using CLI-style overrides."""

        self.results = Params()
                
        overrides = { a.split("=")[0].strip('-'):tryeval(a.split("=")[1]) for a in args if "=" in a }

        base_config_path = os.path.abspath(base_config_path)
        self.base_dir = os.path.abspath(os.path.join(os.path.dirname(base_config_path), ".."))
        self.config = Params(get_config(config_path=base_config_path, overrides=overrides))

        # Apply additional app config files on top of base config.
        config_dir = os.path.dirname(base_config_path)
        base_config_name = os.path.basename(base_config_path).lower()
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
                        .populate(common_nodes=["COMMON"], wrappers=self.config.COMMON.CONFIG_WRAPPERS)
                
        
        self.results.start_time = dt.datetime.strptime(self.config.COMMON.START_TIME, self.config.COMMON.DATETIME_FORMAT) if self.config.COMMON.START_TIME else dt.datetime.utcnow()
        self.results.run_id = self.config.COMMON.RUN_ID or str(uuid.uuid4().hex[:6]).upper()
        self.results.signature = f"run_id={self.results.run_id} start_time={self.results.start_time}"
        self.results.app_title = f"{self.config.app.name} v{self.config.app.version}"
        self.results.html_template = self.config.COMMON.HTML_TEMPLATE

        messages_dir = os.path.join(self.base_dir, "docs", "messages")
        message_dict = load_message_dict([
            os.path.join(messages_dir, "logger.csv"),
            os.path.join(messages_dir, "base.csv"),
            os.path.join(messages_dir, "app.csv"),
        ])
        logger_dict = {
            "log_path": None,
            "start_time": self.results.start_time,
            "max_items": getattr(self.config.log, "max_items", None),
            "verbose": getattr(self.config.log, "verbose", False),
            "log_types": getattr(self.config.log, "types", None),
            "type_colors": getattr(self.config.log, "colors", None),
            "message_dict": message_dict,
        }
        
        if getattr(self.config, "log", None) is not None:
            if getattr(self.config.log, "path", None) is not None:
                log_path = self.config.log.path
                
                os.makedirs(log_path, exist_ok=True)
                self.results.log_path = '/'.join([log_path, f"{self.results.start_time.strftime('%Y-%m-%dT%H-%M-%SZ')}.csv"])
                logger_dict["log_path"] = self.results.log_path
            else:
                self.results.log_path = None

        self.logger = Logger(**logger_dict)
        self.logger.log(
            f"{self.__class__.__name__} initialized",
            message_code="BASE001",
            data={"instance": str(self)},
        )
        self.logger.log(message_code="BASE002", data={"run_id": self.results.run_id})

        self.store_outputs()

    def store_outputs(self):

        for output_key, output_dict in self.config.output.get_dict().items():
            store = output_dict.get("store", True)
            if not store:
                continue

            data = getattr(self, output_dict.get("source", output_key), None)

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

        