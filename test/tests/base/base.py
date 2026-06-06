"""Feature ID: 5.3.1.1. Base architecture alignment test module."""

import ast
import json
import os
import re
from pathlib import Path

from utils.baseutils import AppManager, dict_merge, save


# Feature 5.3.1.1.1
class ArchitectureAlignmentTest(AppManager):
    """Feature ID: 5.3.1.1.1. AppManager-backed test that compares the codebase against docs/architecture and emits review artifacts."""

    FEATURE_PATTERN = re.compile(r"(?im)^\s*Feature(?:\s+ID)?\s*:\s*([0-9]+(?:\.[0-9]+)*)\.?\s*")
    EXCLUDED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache"}
    EXTENSION_TYPES = {
        ".py": "PyFile",
        ".json": "JsonFile",
        ".md": "MdFile",
        ".csv": "CsvFile",
        ".txt": "TxtFile",
        ".html": "HtmlFile",
    }
    APP_OWNED_PATHS = {
        "app/app.py",
        "config/app.json",
        "utils/apputils.py",
        "docs/readme/app.md",
        "resources/version/app.txt",
        "resources/message_codes/app.csv",
        "build/architecture/app.json",
        "test/config/app.json",
    }

    def __init__(
        self,
        config=None,
        logger=None,
        results=None,
        test_logger=None,
        message="Validated the codebase against docs/architecture and generated candidate temp architecture files",
        artifact_rel_dir="tests/results/architecture",
        candidate_rel_dir="tests/results/architecture_changes",
        architecture_html_file="combined_architecture.html",
        code_tree_html_file="code_tree.html",
        **kwargs,
    ):
        """Initialize the architecture alignment test with shared runtime context."""
        super().__init__(config=config, logger=logger, results=results)
        self.test_logger = test_logger
        self.architecture_dir = Path(self.base_dir) / "build" / "architecture"
        self.temp_dir = self.architecture_dir / "temp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self._result = self._execute(
            message=message,
            artifact_rel_dir=artifact_rel_dir,
            candidate_rel_dir=candidate_rel_dir,
            architecture_html_file=architecture_html_file,
            code_tree_html_file=code_tree_html_file,
            **kwargs,
        )

    def _normalize_rel(self, value):
        return str(value or "").replace("\\", "/").strip("/")

    def _normalize_path(self, value):
        path = Path(value)
        try:
            path = path.relative_to(self.base_dir)
        except ValueError:
            pass
        return self._normalize_rel(path)

    def _normalize_name(self, value):
        text = str(value or "").strip().strip(".")
        if not text:
            return ""
        for extension in [".py", ".json", ".csv", ".md", ".txt", ".html"]:
            if text.lower().endswith(extension):
                return text[: -len(extension)]
        return text

    def _extract_doc_metadata(self, docstring):
        text = str(docstring or "").strip()
        if not text:
            return None, ""
        match = self.FEATURE_PATTERN.search(text)
        feature_id = match.group(1) if match else None
        cleaned = self.FEATURE_PATTERN.sub("", text, count=1).strip()
        cleaned = " ".join(line.strip() for line in cleaned.splitlines() if line.strip())
        return feature_id, cleaned

    def _describe_item(self, item):
        kind = item.get("kind")
        name = item.get("name")
        if kind == "folder":
            return f"Folder '{name}' in the codebase."
        if kind == "file":
            return f"File '{item.get('path')}'."
        if kind == "class":
            return f"Python class '{name}'."
        if kind == "method":
            return f"Python method '{name}'."
        if kind == "function":
            return f"Python function '{name}'."
        return f"Code item '{name}'."

    def _resolve_template_path(self):
        template_value = getattr(getattr(self.config, "COMMON", object()), "HTML_TEMPLATE", "../resources/templates/dataset_table.html")
        template_path = Path(str(template_value))
        if template_path.is_absolute():
            return str(template_path)
        return str((Path(self.base_dir) / "config" / template_path).resolve())

    def _read_architecture_paths(self):
        if not self.architecture_dir.is_dir():
            return []
        base_path = self.architecture_dir / "base.json"
        paths = []
        if base_path.is_file():
            paths.append(base_path)
        for path in sorted(self.architecture_dir.glob("*.json")):
            if path.name == "base.json":
                continue
            paths.append(path)
        return paths

    def _walk_architecture_nodes(self, features, source_key, items_by_id, items_by_name, source_items, children_map, parent_id=None):
        for feature_id, feature_value in (features or {}).items():
            if not isinstance(feature_value, dict):
                item = {
                    "id": str(feature_id),
                    "name": str(feature_value).strip(),
                    "raw_name": str(feature_value).strip(),
                    "description": "",
                    "type": None,
                    "path": None,
                    "source_key": source_key,
                    "parent_id": parent_id,
                }
            else:
                item = {
                    "id": str(feature_id),
                    "name": str(feature_value.get("name", "")).strip(),
                    "raw_name": str(feature_value.get("name", "")).strip(),
                    "description": str(feature_value.get("description", "")).strip(),
                    "type": str(feature_value.get("type", "")).strip() or None,
                    "path": self._normalize_rel(feature_value.get("path")),
                    "source_key": source_key,
                    "parent_id": parent_id,
                }

            item["name"] = self._normalize_name(item["name"])
            item["code_like"] = bool(item.get("type"))
            items_by_id[item["id"]] = item
            if item["name"]:
                items_by_name[item["name"]] = item
                source_items.setdefault(source_key, {})[item["name"]] = item
            children_map.setdefault(source_key, {}).setdefault(parent_id, set()).add(item["id"])

            if isinstance(feature_value, dict):
                self._walk_architecture_nodes(
                    feature_value.get("features", {}),
                    source_key,
                    items_by_id,
                    items_by_name,
                    source_items,
                    children_map,
                    parent_id=item["id"],
                )

    def _load_architecture(self):
        paths = self._read_architecture_paths()
        items_by_id = {}
        items_by_name = {}
        items_by_path = {}
        source_items = {}
        children_map = {}
        documents = {}
        combined_document = {"features": {}}

        for path in paths:
            source_key = path.stem
            with open(path, "r", encoding="utf-8") as handle:
                documents[source_key] = json.load(handle)
            combined_document = dict_merge(combined_document, documents[source_key])
            self._walk_architecture_nodes(
                documents[source_key].get("features", {}),
                source_key,
                items_by_id,
                items_by_name,
                source_items,
                children_map,
            )

        for item in items_by_id.values():
            if item.get("path"):
                items_by_path[item["path"]] = item

        return {
            "paths": paths,
            "documents": documents,
            "combined_document": combined_document,
            "items_by_id": items_by_id,
            "items_by_name": items_by_name,
            "items_by_path": items_by_path,
            "source_items": source_items,
            "children_map": children_map,
        }

    def _collect_duplicate_stems(self):
        duplicate_stems = {}
        for root, dirs, files in os.walk(self.base_dir):
            rel_root = Path(root).relative_to(self.base_dir)
            rel_root_text = "" if str(rel_root) == "." else rel_root.as_posix()
            dirs[:] = [
                name
                for name in dirs
                if name not in self.EXCLUDED_DIRS and not (rel_root_text == "build/architecture" and name == "temp")
            ]
            stem_counts = {}
            stem_exts = {}
            for file_name in files:
                stem = Path(file_name).stem
                stem_counts[stem] = stem_counts.get(stem, 0) + 1
                stem_exts.setdefault(stem, set()).add(Path(file_name).suffix.lower())
            duplicate_stems[rel_root_text] = {
                stem: exts
                for stem, exts in stem_exts.items()
                if stem_counts.get(stem, 0) > 1
            }
        return duplicate_stems

    def _path_to_name(self, rel_path, duplicate_stems, is_dir=False):
        rel_path = self._normalize_rel(rel_path)
        if not rel_path:
            return ""
        parts = rel_path.split("/")
        if is_dir:
            return ".".join(parts)

        folder_rel = "/".join(parts[:-1])
        file_name = parts[-1]
        stem = Path(file_name).stem
        suffix = Path(file_name).suffix.lower().lstrip(".")
        if len(duplicate_stems.get(folder_rel, {}).get(stem, set())) > 1:
            stem = f"{stem}_{suffix}"
        return ".".join(parts[:-1] + [stem])

    def _folder_type(self, rel_path):
        parts = [part for part in self._normalize_rel(rel_path).split("/") if part]
        return ".".join(["Folder"] * len(parts))

    def _file_type(self, rel_path):
        rel_path = self._normalize_rel(rel_path)
        parts = [part for part in rel_path.split("/") if part]
        leaf_type = self.EXTENSION_TYPES.get(Path(rel_path).suffix.lower(), "DataSet")
        return ".".join((["Folder"] * max(0, len(parts) - 1)) + [leaf_type])

    def _register_code_item(self, code_items, item):
        item = dict(item)
        item["path"] = self._normalize_rel(item.get("path"))
        item["name"] = self._normalize_name(item.get("name"))
        item["match_path"] = self._normalize_rel(item.get("match_path") or item.get("path"))
        if not item.get("name"):
            return
        if not item.get("description"):
            item["description"] = self._describe_item(item)
        code_items[item["name"]] = item

    def _parse_python_file(self, rel_path, absolute_path, duplicate_stems, code_items, parse_errors):
        rel_path = self._normalize_rel(rel_path)
        module_name = self._normalize_name(self._path_to_name(rel_path, duplicate_stems, is_dir=False))
        module_type = self._file_type(rel_path)
        try:
            with open(absolute_path, "r", encoding="utf-8") as handle:
                source_text = handle.read()
            tree = ast.parse(source_text, filename=str(absolute_path))
        except Exception as ex:
            parse_errors.append({"path": rel_path, "error": str(ex)})
            return

        module_feature_id, module_description = self._extract_doc_metadata(ast.get_docstring(tree))
        self._register_code_item(
            code_items,
            {
                "kind": "file",
                "path": rel_path,
                "name": module_name,
                "type": module_type,
                "match_path": rel_path,
                "feature_id": module_feature_id,
                "description": module_description,
            },
        )

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                if node.name.startswith("_"):
                    continue
                class_feature_id, class_description = self._extract_doc_metadata(ast.get_docstring(node))
                class_name = f"{module_name}.{node.name}"
                self._register_code_item(
                    code_items,
                    {
                        "kind": "class",
                        "path": rel_path,
                        "name": class_name,
                        "type": f"{module_type}.Class",
                        "match_path": f"{rel_path}::{node.name}",
                        "feature_id": class_feature_id,
                        "description": class_description,
                    },
                )

                for child in node.body:
                    if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if child.name.startswith("_"):
                        continue
                    method_feature_id, method_description = self._extract_doc_metadata(ast.get_docstring(child))
                    self._register_code_item(
                        code_items,
                        {
                            "kind": "method",
                            "path": rel_path,
                            "name": f"{class_name}.{child.name}",
                            "type": f"{module_type}.Class.Method",
                            "match_path": f"{rel_path}::{node.name}.{child.name}",
                            "feature_id": method_feature_id,
                            "description": method_description,
                        },
                    )

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue
                function_feature_id, function_description = self._extract_doc_metadata(ast.get_docstring(node))
                self._register_code_item(
                    code_items,
                    {
                        "kind": "function",
                        "path": rel_path,
                        "name": f"{module_name}.{node.name}",
                        "type": f"{module_type}.Function",
                        "match_path": f"{rel_path}::{node.name}",
                        "feature_id": function_feature_id,
                        "description": function_description,
                    },
                )

    def _collect_codebase_items(self):
        duplicate_stems = self._collect_duplicate_stems()
        code_items = {}
        parse_errors = []

        for root, dirs, files in os.walk(self.base_dir):
            rel_root = Path(root).relative_to(self.base_dir)
            rel_root_text = "" if str(rel_root) == "." else rel_root.as_posix()
            dirs[:] = [
                name
                for name in dirs
                if name not in self.EXCLUDED_DIRS
                and not name.startswith(".")
                and not (rel_root_text == "build/architecture" and name == "temp")
            ]

            if rel_root_text:
                self._register_code_item(
                    code_items,
                    {
                        "kind": "folder",
                        "path": rel_root_text,
                        "name": self._path_to_name(rel_root_text, duplicate_stems, is_dir=True),
                        "type": self._folder_type(rel_root_text),
                        "match_path": rel_root_text,
                    },
                )

            for file_name in sorted(files):
                rel_path = rel_root / file_name if rel_root_text else Path(file_name)
                rel_path_text = rel_path.as_posix()
                if rel_path_text.startswith("build/architecture/temp/"):
                    continue
                if file_name == "__init__.py":
                    continue
                if file_name.startswith("."):
                    continue

                self._register_code_item(
                    code_items,
                    {
                        "kind": "file",
                        "path": rel_path_text,
                        "name": self._path_to_name(rel_path_text, duplicate_stems, is_dir=False),
                        "type": self._file_type(rel_path_text),
                        "match_path": rel_path_text,
                    },
                )

                if rel_path.suffix.lower() == ".py":
                    self._parse_python_file(rel_path_text, Path(root) / file_name, duplicate_stems, code_items, parse_errors)

        items_by_path = {}
        for item in code_items.values():
            if item.get("match_path"):
                items_by_path[item["match_path"]] = item

        return {"items_by_name": code_items, "items_by_path": items_by_path, "parse_errors": parse_errors}

    def _find_architecture_match_for_code_item(self, item, architecture):
        if item.get("match_path") and item["match_path"] in architecture["items_by_path"]:
            return architecture["items_by_path"][item["match_path"]]
        return architecture["items_by_name"].get(item["name"])

    def _find_code_match_for_architecture_item(self, item, codebase):
        if item.get("path") and item["path"] in codebase["items_by_path"]:
            return codebase["items_by_path"][item["path"]]
        match = codebase["items_by_name"].get(item["name"])
        if match is not None:
            return match
        return self._resolve_json_pointer(item, codebase)

    def _resolve_json_pointer(self, item, codebase):
        """Resolve an architecture path of the form 'file.json::dotted.key' against the on-disk JSON file."""
        path = item.get("path") or ""
        if "::" not in path:
            return None
        file_part, pointer = path.split("::", 1)
        if not file_part.lower().endswith(".json"):
            return None
        absolute_path = Path(self.base_dir) / file_part
        if not absolute_path.is_file():
            return None
        cache = codebase.setdefault("json_cache", {})
        if file_part not in cache:
            try:
                with open(absolute_path, "r", encoding="utf-8") as handle:
                    cache[file_part] = json.load(handle)
            except Exception:
                cache[file_part] = None
        data = cache.get(file_part)
        if data is None:
            return None
        node = data
        for key in pointer.split("."):
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return None
        return {
            "kind": "json_node",
            "path": path,
            "name": item.get("name"),
            "match_path": path,
        }

    def _arch_item_unparseable(self, item):
        """True when the architecture item points inside a file the comparator does not parse (e.g. JSON pointers)."""
        path = item.get("path") or ""
        if "::" not in path:
            return False
        file_part = path.split("::", 1)[0]
        return not file_part.lower().endswith(".py")

    def _compare_architecture_to_code(self, architecture, codebase):
        code_items = codebase["items_by_name"]
        architecture_items = [item for item in architecture["items_by_id"].values() if item.get("code_like") and item.get("name")]

        missing_in_code = [
            item for item in sorted(architecture_items, key=lambda value: value["name"])
            if self._find_code_match_for_architecture_item(item, codebase) is None
        ]
        missing_in_architecture = [
            item for item in sorted(code_items.values(), key=lambda value: value["name"])
            if self._find_architecture_match_for_code_item(item, architecture) is None
        ]

        feature_id_mismatches = []
        for item in sorted(code_items.values(), key=lambda value: value["name"]):
            feature_id = item.get("feature_id")
            if not feature_id:
                continue
            matched = architecture["items_by_id"].get(feature_id)
            if matched is None:
                feature_id_mismatches.append(
                    {
                        "kind": "unknown_feature_id",
                        "feature_id": feature_id,
                        "code_name": item["name"],
                        "path": item["path"],
                    }
                )
            elif matched.get("path"):
                if matched.get("path") != item.get("match_path"):
                    feature_id_mismatches.append(
                        {
                            "kind": "path_mismatch",
                            "feature_id": feature_id,
                            "code_name": item["name"],
                            "architecture_name": matched.get("name"),
                            "architecture_path": matched.get("path"),
                            "code_path": item.get("match_path"),
                            "path": item["path"],
                        }
                    )
            elif matched.get("name") != item.get("name"):
                feature_id_mismatches.append(
                    {
                        "kind": "name_mismatch",
                        "feature_id": feature_id,
                        "code_name": item["name"],
                        "architecture_name": matched.get("name"),
                        "path": item["path"],
                    }
                )

        mismatches_by_name = {item["code_name"]: item for item in feature_id_mismatches}
        return {
            "missing_in_code": missing_in_code,
            "missing_in_architecture": missing_in_architecture,
            "feature_id_mismatches": feature_id_mismatches,
            "mismatches_by_name": mismatches_by_name,
        }

    def _classify_source_key(self, item_name, item_path, architecture):
        parts = item_name.split(".")
        for index in range(len(parts), 0, -1):
            candidate_name = ".".join(parts[:index])
            matched = architecture["items_by_name"].get(candidate_name)
            if matched:
                return matched["source_key"]
        if item_path in self.APP_OWNED_PATHS or item_path.startswith("test/tests/app/"):
            return "app" if "app" in architecture["source_items"] else next(iter(architecture["source_items"]), "base")
        return "base" if "base" in architecture["source_items"] else next(iter(architecture["source_items"]), "base")

    def _build_candidate_documents(self, missing_items, architecture):
        candidate_docs = {source_key: {"features": {}} for source_key in architecture["source_items"].keys()}
        candidate_lookup = {source_key: {} for source_key in architecture["source_items"].keys()}
        next_numbers = {}

        def child_number(parent_id, child_id):
            if parent_id:
                suffix = str(child_id)[len(f"{parent_id}."):]
            else:
                suffix = str(child_id)
            head = suffix.split(".", 1)[0]
            return int(head) if head.isdigit() else None

        for source_key, parent_map in architecture["children_map"].items():
            next_numbers[source_key] = {}
            for parent_id, child_ids in parent_map.items():
                used = {child_number(parent_id, child_id) for child_id in child_ids}
                used.discard(None)
                next_numbers[source_key][parent_id] = used

        def ensure_anchor(source_key, item_name):
            if not item_name:
                return None
            if item_name in candidate_lookup[source_key]:
                return candidate_lookup[source_key][item_name]
            architecture_item = architecture["source_items"].get(source_key, {}).get(item_name)
            if architecture_item is None:
                return None

            parent_name = architecture["items_by_id"].get(architecture_item.get("parent_id"), {}).get("name", "")
            parent_anchor = ensure_anchor(source_key, parent_name)
            node = {
                "name": architecture_item.get("raw_name") or architecture_item.get("name"),
                "description": architecture_item.get("description"),
                "type": architecture_item.get("type"),
                "path": architecture_item.get("path"),
                "features": {},
            }
            if parent_anchor is None:
                candidate_docs[source_key]["features"][architecture_item["id"]] = node
            else:
                parent_anchor["node"].setdefault("features", {})[architecture_item["id"]] = node
            candidate_lookup[source_key][item_name] = {"id": architecture_item["id"], "node": node}
            return candidate_lookup[source_key][item_name]

        def allocate_child_id(source_key, parent_id):
            used = next_numbers.setdefault(source_key, {}).setdefault(parent_id, set())
            number = 1
            while number in used:
                number += 1
            used.add(number)
            return f"{parent_id}.{number}" if parent_id else str(number)

        for item in sorted(missing_items, key=lambda value: (value["name"].count("."), value["name"])):
            source_key = self._classify_source_key(item["name"], item["path"], architecture)
            parts = item["name"].split(".")
            parent_anchor = None
            for index in range(len(parts) - 1, 0, -1):
                candidate_name = ".".join(parts[:index])
                if candidate_name in candidate_lookup[source_key] or candidate_name in architecture["source_items"].get(source_key, {}):
                    parent_anchor = ensure_anchor(source_key, candidate_name)
                    break
            parent_id = parent_anchor["id"] if parent_anchor else None
            candidate_id = allocate_child_id(source_key, parent_id)
            node = {
                "name": item["name"],
                "description": item.get("description") or self._describe_item(item),
                "path": item.get("match_path") or item.get("path"),
                "type": item.get("type"),
            }
            if parent_anchor is None:
                candidate_docs[source_key]["features"][candidate_id] = node
            else:
                parent_anchor["node"].setdefault("features", {})[candidate_id] = node
            candidate_lookup[source_key][item["name"]] = {"id": candidate_id, "node": node}

        return candidate_docs

    def _write_candidate_documents(self, missing_items, architecture, candidate_rel_dir="tests/results/architecture_changes"):
        candidate_docs = self._build_candidate_documents(missing_items, architecture)
        candidate_paths = {}
        candidate_dir = Path(self.CONFIG.COMMON.OUTPUT_PATH) / candidate_rel_dir
        candidate_dir.mkdir(parents=True, exist_ok=True)
        for source_key, document in candidate_docs.items():
            output_path = candidate_dir / f"{source_key}.json"
            with open(output_path, "w", encoding="utf-8") as handle:
                json.dump(document, handle, indent=4)
            candidate_paths[source_key] = self._normalize_path(output_path)
        return candidate_paths

    def _architecture_status(self, architecture_item, code_item, comparison):
        if code_item and code_item.get("name") in comparison["mismatches_by_name"]:
            return "feature_id_mismatch"
        if code_item is not None:
            return "covered"
        return "missing_in_code"

    def _build_architecture_review_node(self, feature_id, feature_value, codebase, comparison):
        if not isinstance(feature_value, dict):
            return {
                "feature_id": feature_id,
                "name": str(feature_value),
                "description": "Simple feature leaf.",
                "type": None,
                "path": None,
                "compliance": "informational",
            }

        name = self._normalize_name(feature_value.get("name"))
        architecture_item = {"name": name, "path": self._normalize_rel(feature_value.get("path"))}
        code_item = self._find_code_match_for_architecture_item(architecture_item, codebase)
        status = self._architecture_status(architecture_item, code_item, comparison) if feature_value.get("type") else "informational"
        node = {
            "feature_id": feature_id,
            "name": feature_value.get("name"),
            "description": feature_value.get("description"),
            "type": feature_value.get("type"),
            "path": feature_value.get("path"),
            "compliance": status,
            "code_path": code_item.get("path") if code_item else None,
            "code_match_path": code_item.get("match_path") if code_item else None,
            "code_feature_id": code_item.get("feature_id") if code_item else None,
            "features": {},
        }
        for child_id, child_value in (feature_value.get("features", {}) or {}).items():
            node["features"][child_id] = self._build_architecture_review_node(child_id, child_value, codebase, comparison)
        return node

    def _build_architecture_review(self, architecture, codebase, comparison, candidate_paths):
        review = {
            "summary": {
                "architecture_files": [str(path.relative_to(self.base_dir)).replace("\\", "/") for path in architecture["paths"]],
                "missing_in_code": len(comparison["missing_in_code"]),
                "missing_in_architecture": len(comparison["missing_in_architecture"]),
                "feature_id_mismatches": len(comparison["feature_id_mismatches"]),
                "candidate_paths": candidate_paths,
            },
            "combined_architecture": {
                "source": "deep_merge(base.json -> additional architecture files)",
                "features": {
                    feature_id: self._build_architecture_review_node(feature_id, feature_value, codebase, comparison)
                    for feature_id, feature_value in (architecture["combined_document"].get("features", {}) or {}).items()
                },
            },
            "sources": {},
        }
        for source_key, document in architecture["documents"].items():
            review["sources"][source_key] = {
                "source": f"build/architecture/{source_key}.json",
                "features": {
                    feature_id: self._build_architecture_review_node(feature_id, feature_value, codebase, comparison)
                    for feature_id, feature_value in (document.get("features", {}) or {}).items()
                },
            }
        return review

    def _code_item_status(self, item, architecture, comparison):
        if item["name"] in comparison["mismatches_by_name"]:
            return "feature_id_mismatch"
        if self._find_architecture_match_for_code_item(item, architecture) is not None:
            return "covered"
        return "missing_in_architecture"

    def _build_code_tree_review(self, codebase, architecture, comparison):
        review = {
            "summary": {
                "code_items": len(codebase["items_by_name"]),
                "missing_in_architecture": len(comparison["missing_in_architecture"]),
                "feature_id_mismatches": len(comparison["feature_id_mismatches"]),
                "parse_errors": len(codebase["parse_errors"]),
            },
            "tree": {},
        }

        for item in sorted(codebase["items_by_name"].values(), key=lambda value: (value["path"], value["name"])):
            path_parts = [part for part in item["path"].split("/") if part]
            cursor = review["tree"]

            if item["kind"] == "folder":
                for part in path_parts:
                    cursor = cursor.setdefault(part, {})
                cursor["_meta"] = {
                    "name": item["name"],
                    "type": item["type"],
                    "path": item.get("match_path"),
                    "feature_id": item.get("feature_id"),
                    "compliance": self._code_item_status(item, architecture, comparison),
                }
                continue

            if path_parts:
                for part in path_parts[:-1]:
                    cursor = cursor.setdefault(part, {})
                file_node = cursor.setdefault(path_parts[-1], {})
            else:
                file_node = cursor.setdefault(item["name"], {})

            if item["kind"] == "file":
                file_node["_meta"] = {
                    "name": item["name"],
                    "type": item["type"],
                    "path": item.get("match_path"),
                    "feature_id": item.get("feature_id"),
                    "compliance": self._code_item_status(item, architecture, comparison),
                }
                continue

            file_node.setdefault("symbols", {})[item["name"]] = {
                "kind": item["kind"],
                "type": item["type"],
                "path": item.get("match_path"),
                "feature_id": item.get("feature_id"),
                "compliance": self._code_item_status(item, architecture, comparison),
                "architecture_name": self._find_architecture_match_for_code_item(item, architecture).get("name") if self._find_architecture_match_for_code_item(item, architecture) else None,
                "architecture_path": self._find_architecture_match_for_code_item(item, architecture).get("path") if self._find_architecture_match_for_code_item(item, architecture) else None,
            }

        if codebase["parse_errors"]:
            review["parse_errors"] = codebase["parse_errors"]
        return review

    def _store_review_artifacts(self, architecture_review, code_tree_review, artifact_rel_dir, architecture_html_file, code_tree_html_file):
        artifact_dir = Path(self.CONFIG.COMMON.OUTPUT_PATH) / artifact_rel_dir
        artifact_dir.mkdir(parents=True, exist_ok=True)
        template_path = self._resolve_template_path()

        architecture_path = artifact_dir / architecture_html_file
        code_tree_path = artifact_dir / code_tree_html_file
        save(architecture_review, str(architecture_path), format="html", template=template_path, title=f"{self.RESULTS.app_title} Architecture Review")
        save(code_tree_review, str(code_tree_path), format="html", template=template_path, title=f"{self.RESULTS.app_title} Code Tree Review")

        return {
            "architecture_html": self._normalize_rel(architecture_path),
            "code_tree_html": self._normalize_rel(code_tree_path),
        }

    def _build_result_lines(self, message, comparison, parse_errors, candidate_paths, artifact_paths, report_path):
        result_lines = []

        for item in comparison["missing_in_code"]:
            result_lines.append(
                {
                    "status": "WARN",
                    "message_code": "TST004",
                    "message": f"Architecture item missing from code: {item['name']}",
                    "criteria": [
                        {
                            "name": "architecture_item_present_in_code",
                            "success": False,
                            "status": "WARN",
                            "actual": False,
                            "expected": True,
                        }
                    ],
                    "data": {"category": "missing_in_code", "item": item, "feature_ids": [item.get("id")]},
                }
            )

        for item in comparison["missing_in_architecture"]:
            result_lines.append(
                {
                    "status": "WARN",
                    "message_code": "TST004",
                    "message": f"Code item missing from architecture: {item['name']}",
                    "criteria": [
                        {
                            "name": "code_item_present_in_architecture",
                            "success": False,
                            "status": "WARN",
                            "actual": False,
                            "expected": True,
                        }
                    ],
                    "data": {"category": "missing_in_architecture", "item": item, "candidate_paths": candidate_paths, "feature_ids": [item.get("feature_id") or "5.3.1.1.1"]},
                }
            )

        for item in comparison["feature_id_mismatches"]:
            result_lines.append(
                {
                    "status": "WARN",
                    "message_code": "TST004",
                    "message": f"Feature ID mismatch for {item['code_name']}",
                    "criteria": [
                        {
                            "name": "feature_id_matches_architecture",
                            "success": False,
                            "status": "WARN",
                            "actual": item.get("feature_id"),
                            "expected": item.get("architecture_name") or "Existing architecture feature id",
                        }
                    ],
                    "data": {"category": "feature_id_mismatch", "item": item, "feature_ids": [item.get("feature_id") or "5.3.1.1.1"]},
                }
            )

        for item in parse_errors:
            result_lines.append(
                {
                    "status": "WARN",
                    "message_code": "TST004",
                    "message": f"AST parse failed for {item['path']}",
                    "criteria": [
                        {
                            "name": "python_file_parseable",
                            "success": False,
                            "status": "WARN",
                            "actual": item.get("error"),
                            "expected": "AST parse succeeds",
                        }
                    ],
                    "data": {"category": "parse_error", "item": item, "feature_ids": ["5.3.1.1.1"]},
                }
            )

        has_warnings = bool(result_lines)
        result_lines.append(
            {
                "status": "WARN" if has_warnings else "PASS",
                "message_code": "TST004" if has_warnings else "TST003",
                "message": message if has_warnings else "Architecture fully matches the codebase",
                "criteria": [
                    {
                        "name": "architecture_alignment",
                        "success": not has_warnings,
                        "status": "WARN" if has_warnings else "PASS",
                        "actual": {
                            "missing_in_code": len(comparison["missing_in_code"]),
                            "missing_in_architecture": len(comparison["missing_in_architecture"]),
                            "feature_id_mismatches": len(comparison["feature_id_mismatches"]),
                            "parse_errors": len(parse_errors),
                        },
                        "expected": {
                            "missing_in_code": 0,
                            "missing_in_architecture": 0,
                            "feature_id_mismatches": 0,
                            "parse_errors": 0,
                        },
                    }
                ],
                "data": {
                    "candidate_paths": candidate_paths,
                    "artifact_paths": artifact_paths,
                    "report_path": self._normalize_rel(report_path),
                    "feature_ids": ["5.3.1.1", "5.3.1.1.1", "5.3.1.1.1.1"],
                },
            }
        )
        return result_lines

    def _execute(
        self,
        message="Validated the codebase against docs/architecture and generated candidate temp architecture files",
        artifact_rel_dir="tests/results/architecture",
        candidate_rel_dir="tests/results/architecture_changes",
        architecture_html_file="combined_architecture.html",
        code_tree_html_file="code_tree.html",
        **kwargs,
    ):
        architecture = self._load_architecture()
        codebase = self._collect_codebase_items()
        comparison = self._compare_architecture_to_code(architecture, codebase)
        candidate_paths = self._write_candidate_documents(comparison["missing_in_architecture"], architecture, candidate_rel_dir=candidate_rel_dir)
        architecture_review = self._build_architecture_review(architecture, codebase, comparison, candidate_paths)
        code_tree_review = self._build_code_tree_review(codebase, architecture, comparison)
        artifact_paths = self._store_review_artifacts(
            architecture_review=architecture_review,
            code_tree_review=code_tree_review,
            artifact_rel_dir=artifact_rel_dir,
            architecture_html_file=architecture_html_file,
            code_tree_html_file=code_tree_html_file,
        )

        report = {
            "architecture_files": [str(path.relative_to(self.base_dir)).replace("\\", "/") for path in architecture["paths"]],
            "architecture_item_count": len(architecture["items_by_id"]),
            "code_item_count": len(codebase["items_by_name"]),
            "missing_in_code": comparison["missing_in_code"],
            "missing_in_architecture": comparison["missing_in_architecture"],
            "feature_id_mismatches": comparison["feature_id_mismatches"],
            "parse_errors": codebase["parse_errors"],
            "candidate_paths": candidate_paths,
            "artifact_paths": artifact_paths,
        }
        report_path = Path(self.CONFIG.COMMON.OUTPUT_PATH) / candidate_rel_dir / "report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=4)

        return {"results": self._build_result_lines(message, comparison, codebase["parse_errors"], candidate_paths, artifact_paths, report_path)}

    # Feature 5.3.1.1.1.1
    def run(self):
        """Feature ID: 5.3.1.1.1.1. Return the architecture alignment result prepared during construction."""
        return self._result
