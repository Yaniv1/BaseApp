import pandas as pd
import numpy as np
import json
import os
import concurrent.futures
import datetime as dt
from typing import Any, Dict, List, Optional


class DataFrameConverter:
    """
    Securely applies transformation rules to a pandas DataFrame.

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

    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply conversion list to DataFrame."""
        df = df.copy()

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

        return df


class DataLoader:
    """Load files from a source file/folder using configurable format and optional logging."""

    def __init__(self, source, logger=None, data=None, base_dir=None):
        self.source = source if isinstance(source, dict) else vars(source)
        self.logger = logger
        self.data = data if isinstance(data, dict) else {}
        self.base_dir = str(base_dir) if base_dir else os.getcwd()

        source_path = self.source.get("path", "")
        source_format = self.source.get("format", "")

        resolved_format = str(source_format).lower().strip()
        if not resolved_format:
            resolved_format = os.path.splitext(str(source_path))[1].lower().lstrip(".")
        self.format = resolved_format

        if os.path.isabs(str(source_path)):
            path_str = os.path.abspath(str(source_path))
        else:
            path_str = os.path.abspath(os.path.join(self.base_dir, str(source_path)))
        self.path = path_str

        if os.path.isfile(path_str):
            self.file_map = {os.path.basename(path_str): path_str}
        elif os.path.isdir(path_str):
            self.file_map = {
                os.path.relpath(os.path.join(root, name), self.path).replace("\\", "/"): os.path.join(root, name)
                for root, _, names in sorted(os.walk(path_str))
                for name in names
                if os.path.isfile(os.path.join(root, name))
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
                data = pd.read_csv(file_path)
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
        """Load configured files and return relative-path keyed results."""
        loaded = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
            futures = {
                executor.submit(self._load_file, file_path): key
                for key, file_path in self.file_map.items()
                if key not in self.data
            }
            for future in concurrent.futures.as_completed(futures):
                key = futures[future]
                file_path = self.file_map[key]
                self.data[key] = future.result()
                loaded += 1

                if self.logger:
                    self.logger.log(
                        message_code="BASE010",
                        data={
                            "file_path": file_path,
                            "items": len(self.data[key]) if hasattr(self.data[key], "__len__") else None,
                            "loaded%": round(loaded / len(self.files) * 100, 2) if self.files else 0,
                        },
                    )

            rows = sum(len(v) if hasattr(v, "__len__") else 0 for v in self.data.values())

        if self.logger:
            self.logger.log(
                message_code="BASE012",
                data={
                    "source": self.path,
                    "loaded": loaded,
                    "items": len(self.data) if hasattr(self.data, "__len__") else None,
                    "rows": rows,
                },
            )

        return self.data
