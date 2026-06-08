import pandas as pd
import numpy as np
import json
import os
import concurrent.futures
import datetime as dt
from typing import Any, Dict, List, Optional



def path_join(*parts):
    """Join path parts with os.path.join after converting to strings and align slashes."""
    return os.path.join(*[str(p) for p in parts]).replace("\\", "/")


# Feature 6.2.1
class DataFrameConverter:
    """Feature ID: 6.2.1. Securely applies transformation rules to a pandas DataFrame.

    Supports:
        - Column-level transformations
        - Row-level transformations
        - Full DataFrame expressions
        - Optional filtering
        - Safe support for datetime via `dt`, numpy via `np`, and pandas via `pd`
    """

    SAFE_GLOBALS = {
        "__builtins__": {},
        "np": np,
        "pd": pd,
        "dt": dt,
        "json": json,
        "abs": abs,
        "min": min,
        "max": max,
        "sum": sum,
        "len": len,
        "round": round,
        "float": float,
        "int": int,
        "str": str,
    }

    def __init__(
        self,
        conversions: Optional[List[Dict]] = None,
        verbose: bool = False,
        context: Optional[Dict[str, Any]] = None,
        log_func=print,
    ):
        self.conversions = conversions or []
        self.verbose = verbose
        self.context = context or {}
        self.log_func = log_func or print

    def _safe_eval(self, expr: str, local_vars: Dict[str, Any]):
        """Safely evaluate an expression with restricted globals."""
        try:
            eval_locals = dict(self.context)
            eval_locals.update(local_vars)
            return eval(expr, self.SAFE_GLOBALS, eval_locals)
        except Exception as e:
            raise ValueError(f"Conversion expression failed: {expr}\n{str(e)}")

    def apply(self, df: pd.DataFrame):
        """Apply conversion list to DataFrame or context data.

        For ``col``, ``row``, and ``df`` scopes the working value is a DataFrame and
        the return value is a DataFrame.  For ``custom`` scope the expression is
        evaluated with the full context (no DataFrame required) and its result is
        returned as-is, allowing arbitrary data shapes (dict, list, etc.) to be
        produced by a process step.
        """
        df = df.copy()
        # Track whether a custom scope expression produced a non-DataFrame result.
        custom_result = None
        has_custom = False

        for conv in self.conversions:
            target = conv.get("target")
            source = conv.get("source", target)
            op = conv.get("op")
            scope = conv.get("scope", "col")
            filt = conv.get("filter")

            if not op:
                continue
            if scope in {"col", "row"} and not target:
                continue

            if self.verbose:
                self.log_func(f"Applying conversion -> {target or '<df>'} | scope={scope}")

            if scope == "custom":
                # Evaluate expression with the full context; no DataFrame manipulation.
                custom_result = self._safe_eval(op, {})
                has_custom = True
                continue

            if filt:
                df = df[df[source].isin(filt)]

            if scope == "col":
                if source not in df.columns:
                    continue
                df[target] = df[source].apply(
                    lambda v: self._safe_eval(op, {"v": v, "df": df})
                )

            elif scope == "row":
                df[target] = df.apply(
                    lambda row: self._safe_eval(
                        op,
                        {
                            "row": row,
                            "df": df,
                        },
                    ),
                    axis=1,
                )

            elif scope == "df":
                df = self._safe_eval(op, {"df": df})

        if has_custom:
            return custom_result
        return df


# Feature 6.2.2
class DataLoader:
    """Feature ID: 6.2.2. Load files from a source file/folder using configurable format and optional logging.

    Supports an optional `delta` mode controlled via the source settings (``delta: true``).
    In delta mode the loader rescans the source surface on every call and only loads files
    that are either missing from the previously loaded ``data`` dict or whose on-disk
    modification time differs from the previously recorded ``last_modified`` entry. This
    makes cyclic / periodic input scanning cheap for large input stores and also picks up
    files that were modified after their initial load. When ``delta`` is false (default),
    every discovered file is (re)loaded on each call.
    """

    def __init__(self, source, logger=None, data=None, base_dir=None, last_modified=None):
        self.source = source if isinstance(source, dict) else vars(source)
        self.logger = logger
        self.data = data if isinstance(data, dict) else {}
        self.base_dir = str(base_dir) if base_dir else os.getcwd()
        # Per-file modification timestamps from prior loads; used to detect changes in delta mode.
        self.last_modified = dict(last_modified) if isinstance(last_modified, dict) else {}
        # Delta mode flag is sourced from the input settings so it lives next to other input options.
        self.delta = bool(self.source.get("delta", False))

        source_path = self.source.get("path", "")
        source_format = self.source.get("format", "")

        resolved_format = str(source_format).lower().strip()
        if not resolved_format:
            resolved_format = os.path.splitext(str(source_path))[1].lower().lstrip(".")
        self.format = resolved_format

        if os.path.isabs(str(source_path)):
            path_str = os.path.abspath(str(source_path))
        else:
            path_str = os.path.abspath(path_join(self.base_dir, str(source_path)))
        self.path = path_str

        if os.path.isfile(path_str):
            self.file_map = {os.path.basename(path_str): path_str}
        elif os.path.isdir(path_str):
            self.file_map = {
                os.path.relpath(path_join(root, name), self.path): path_join(root, name)
                for root, _, names in sorted(os.walk(path_str))
                for name in names
                if os.path.isfile(path_join(root, name))
                and (not self.format or name.lower().endswith(self.format))
            }
        else:
            self.file_map = {}

        self.files = list(self.file_map.values())

        if self.logger:
            self.logger.log(
                message_code="BASE009",
                data={
                    "source": self.source,
                    "path": self.path,
                    "format": self.format,
                    "file_count": len(self.files),
                },
            )

    def _load_file(self, file_path):
        """Load a single file by configured format."""
        data = None
        try:
            if self.format == "csv":
                data = pd.read_csv(file_path, encoding="utf-8")
            elif self.format == "json":
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            elif self.format in {"txt", "text", "md", "log"}:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = f.read()
            else:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = f.read()
        except Exception as e:
            if self.logger:
                self.logger.log(
                    message_code="BASEW10",
                    message_type="WARN",
                    data={"file_path": file_path, "error": str(e)},
                )
        return data

    def load(self):
        """Load configured files and return relative-path keyed results.

        In delta mode, only load files that are new or whose mtime changed since the last load.
        Outside delta mode, (re)load every discovered file. ``self.last_modified`` is refreshed
        for every successfully loaded file so subsequent delta calls can compare against it.
        """
        loaded = 0
        skipped = 0

        # Decide which files actually need to be (re)loaded this call.
        pending = {}
        for key, file_path in self.file_map.items():
            try:
                mtime = os.path.getmtime(file_path)
            except OSError:
                mtime = None

            if self.delta:
                prior_mtime = self.last_modified.get(key)
                already_loaded = key in self.data
                unchanged = (
                    prior_mtime is not None
                    and mtime is not None
                    and prior_mtime == mtime
                )
                if already_loaded and unchanged:
                    skipped += 1
                    continue

            pending[key] = (file_path, mtime)

        with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
            futures = {
                executor.submit(self._load_file, file_path): key
                for key, (file_path, _mtime) in pending.items()
            }
            for future in concurrent.futures.as_completed(futures):
                key = futures[future]
                file_path, mtime = pending[key]
                self.data[key] = future.result()
                if mtime is not None:
                    self.last_modified[key] = mtime
                loaded += 1

                if self.logger:
                    self.logger.log(
                        message_code="BASE010",
                        data={
                            "file_path": file_path,
                            "items": len(self.data[key]) if hasattr(self.data[key], "__len__") else None,
                            "loaded%": round(loaded / len(pending) * 100, 2) if pending else 0,
                        },
                    )

            rows = sum(len(v) if hasattr(v, "__len__") else 0 for v in self.data.values())

        if self.logger:
            self.logger.log(
                message_code="BASE012",
                data={
                    "source": self.path,
                    "delta": self.delta,
                    "loaded": loaded,
                    "skipped": skipped,
                    "items": len(self.data) if hasattr(self.data, "__len__") else None,
                    "rows": rows,
                },
            )

        return self.data
