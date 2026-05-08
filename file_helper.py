#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows file organizer MVP.

Default mode is dry-run. Real filesystem changes require --apply and a final
uppercase YES confirmation.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import shutil
import string
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import yaml
except ImportError:  # PyYAML is recommended, but the default config has a fallback parser.
    yaml = None


ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z"}
RUN_LOG_NAME = "organizer_run_log.json"
RUN_LOG_TMP_NAME = "organizer_run_log.json.tmp"
UNDOABLE_RUN_STATUSES = {"success", "partial"}
WINDOWS_ILLEGAL_CHARS = r'\/:*?"<>|'
SYSTEM_FOLDER_NAMES = {"__pycache__", ".git", ".hg", ".svn", ".venv", "venv", "env"}
QUANTITY_SOURCE_LABELS = {
    "outer_folder_name_only": "outer_folder_name_only",
    "fallback": "fallback",
}
GENERATED_MERGE_FOLDER_PATTERNS = [
    re.compile(r"^\d+(?:[~～-]\d+|\+\d+(?:\+\d+)*)-(?:\d{4}|未知日期|\d{4}-\d{4})-.+-\d+单-\d+个$"),
    re.compile(r"^\d+(?:[~～]\d+|-\d{1,3}|\+\d+(?:\+\d+)*)-.+-\d+单-\d+个$"),
]
LOG_FIELDS = [
    "time",
    "mode",
    "action",
    "sequence_number",
    "sequence_range",
    "source_path",
    "target_path",
    "detected_date",
    "detected_category",
    "detected_orders",
    "detected_quantity",
    "source_name",
    "naming_template",
    "merge_enabled",
    "status",
    "error_message",
]

DEFAULT_CONFIG: Dict[str, Any] = {
    "category_priority": [],
    "categories": {},
    "do_not_merge_keywords": [],
    "naming": {
        "single_keep_original": True,
        "single_template": "{seq}-{clean_original_name}",
        "merged_template": "{seq_range}-{category}-{orders}单-{quantity}个",
        "custom_text": "",
        "merge_name": "",
    },
    "inner_folder_naming": {"template": "{seq}-{original_name}"},
    "sequence": {
        "enabled": True,
        "scope": "all_extracted_folders",
        "sort_by": "name",
        "merged_range_style": "min_max",
        "priority_keywords": [],
        "include_keywords": [],
        "exclude_keywords": [],
    },
    "quantity_detection": {
        "source": "outer_folder_name_only",
    },
    "conflict": {"target_exists": "skip"},
    "already_processed": {
        "enabled": True,
        "action": "skip",
        "patterns": [
            r"^\d+~\d+-(?:\d{4}|未知日期|\d{4}-\d{4})-.+-\d+单-\d+个$",
            r"^\d+~\d+-.+-\d+单-\d+个$",
        ],
    },
    "fallback": {
        "unknown_date": "未知日期",
        "unknown_category": "未知产品",
        "default_orders_per_folder": 1,
        "default_quantity_per_order": 1,
    },
}


class ConfigError(Exception):
    """Configuration is invalid."""


@dataclass
class ScanEntry:
    name: str
    rel_path: str
    is_dir: bool
    suffix: str = ""


@dataclass
class Detection:
    dates: List[str] = field(default_factory=list)
    date_label: str = ""
    category: str = ""
    matched_keyword: str = ""
    merge_enabled: bool = False
    orders: int = 0
    quantity: int = 0
    quantity_source_label: str = ""
    quantity_sources: List[str] = field(default_factory=list)
    ignored_quantity_sources: List[str] = field(default_factory=list)
    date_sources: List[str] = field(default_factory=list)
    category_sources: List[str] = field(default_factory=list)
    do_not_merge_hits: List[str] = field(default_factory=list)
    category_priority_index: Optional[int] = None


@dataclass
class WorkItem:
    source_type: str
    original_name: str
    current_path: Path
    root: Path
    archive_path: Optional[Path] = None
    archive_tool: Optional[Path] = None
    archive_entries: List[str] = field(default_factory=list)
    extract_destination: Optional[Path] = None
    created_time: float = 0
    modified_time: float = 0
    scan_entries: List[ScanEntry] = field(default_factory=list)
    sequence_number: Optional[int] = None
    clean_original_name: str = ""
    removed_windows_duplicate_suffix: bool = False
    skip_reason: str = ""
    detection: Detection = field(default_factory=Detection)
    final_path: Optional[Path] = None
    naming_template: str = ""
    inner_name: str = ""


@dataclass
class PlanGroup:
    items: List[WorkItem]
    is_merge: bool
    sequence_range: str
    date_label: str
    category: str
    orders: int
    quantity: int
    final_name: str
    target_path: Path
    naming_template: str
    reason: str


def now_text() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_id_text() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def absolute_text(path: Path) -> str:
    return str(path.expanduser().resolve())


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "[]":
        return []
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return value


def simple_yaml_load(text: str) -> Dict[str, Any]:
    lines: List[Tuple[int, str]] = []
    for original in text.splitlines():
        if not original.strip() or original.lstrip().startswith("#"):
            continue
        lines.append((len(original) - len(original.lstrip(" ")), original.strip()))

    def parse_block(index: int, indent: int) -> Tuple[Any, int]:
        if index >= len(lines):
            return {}, index
        if lines[index][1].startswith("- "):
            values: List[Any] = []
            while index < len(lines):
                line_indent, content = lines[index]
                if line_indent != indent or not content.startswith("- "):
                    break
                rest = content[2:].strip()
                index += 1
                if rest:
                    values.append(parse_scalar(rest))
                else:
                    child, index = parse_block(index, indent + 2)
                    values.append(child)
            return values, index

        values: Dict[str, Any] = {}
        while index < len(lines):
            line_indent, content = lines[index]
            if line_indent < indent:
                break
            if line_indent > indent:
                break
            if ":" not in content:
                raise ConfigError(f"无法解析 YAML 行：{content}")
            key, rest = content.split(":", 1)
            key = key.strip().strip('"').strip("'")
            rest = rest.strip()
            index += 1
            if rest:
                values[key] = parse_scalar(rest)
            else:
                child, index = parse_block(index, indent + 2)
                values[key] = child
        return values, index

    parsed, final = parse_block(0, 0)
    if final != len(lines) or not isinstance(parsed, dict):
        raise ConfigError("内置 YAML 解析器无法完整解析 config.yaml，请安装 PyYAML。")
    return parsed


def load_config(config_path: Path) -> Dict[str, Any]:
    if not config_path.exists():
        print(f"提示：找不到配置文件，使用内置默认配置：{config_path}")
        return dict(DEFAULT_CONFIG)
    text = config_path.read_text(encoding="utf-8").lstrip("\ufeff")
    try:
        loaded = simple_yaml_load(text) if yaml is None else (yaml.safe_load(text) or {})
    except Exception as exc:
        raise ConfigError(f"config.yaml 读取失败：{exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError("config.yaml 顶层必须是字典。")
    return deep_merge(DEFAULT_CONFIG, loaded)


def placeholders(template: str) -> List[str]:
    found: List[str] = []
    for _, field_name, _, _ in string.Formatter().parse(template):
        if field_name:
            found.append(field_name.split(".")[0].split("[")[0])
    return found


def validate_template(name: str, template: str, allowed: Iterable[str]) -> None:
    unknown = sorted(set(placeholders(template)) - set(allowed))
    if unknown:
        raise ConfigError(f"{name} 包含未知占位符：{', '.join(unknown)}")


def validate_config(config: Dict[str, Any]) -> None:
    if not isinstance(config.get("category_priority", []), list):
        raise ConfigError("category_priority 必须是列表。")
    if not isinstance(config.get("categories"), dict):
        raise ConfigError("categories 必须是字典。")
    for category, data in config["categories"].items():
        if not isinstance(data, dict):
            raise ConfigError(f"categories.{category} 必须是字典。")
        if not isinstance(data.get("keywords", []), list):
            raise ConfigError(f"categories.{category}.keywords 必须是列表。")
        if not isinstance(data.get("merge_enabled", False), bool):
            raise ConfigError(f"categories.{category}.merge_enabled 必须是 true/false。")

    seq = config.get("sequence", {})
    if seq.get("sort_by") not in {"name", "created_time", "modified_time", "custom_priority"}:
        raise ConfigError("sequence.sort_by 只支持 name、created_time、modified_time、custom_priority。")
    if seq.get("merged_range_style") not in {"min_max", "list"}:
        raise ConfigError("sequence.merged_range_style 只支持 min_max 或 list。")
    if config.get("conflict", {}).get("target_exists") != "skip":
        raise ConfigError("第一版 conflict.target_exists 只支持 skip。")
    quantity_detection = config.get("quantity_detection", {})
    if not isinstance(quantity_detection, dict):
        raise ConfigError("quantity_detection 必须是字典。")
    source = quantity_detection.get("source", "outer_folder_name_only")
    if source != "outer_folder_name_only":
        raise ConfigError("quantity_detection.source 只支持 outer_folder_name_only。")

    naming_allowed = {
        "seq",
        "seq_range",
        "date",
        "category",
        "orders",
        "quantity",
        "original_name",
        "clean_original_name",
        "custom_text",
        "merge_name",
    }
    inner_allowed = {"seq", "original_name", "clean_original_name", "category", "date", "orders", "quantity"}
    validate_template("naming.single_template", config["naming"]["single_template"], naming_allowed)
    validate_template("naming.merged_template", config["naming"]["merged_template"], naming_allowed)
    validate_template("inner_folder_naming.template", config["inner_folder_naming"]["template"], inner_allowed)


def write_log(log_path: Path, row: Dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    exists = log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in LOG_FIELDS})


def load_run_log(run_log_path: Path) -> Dict[str, Any]:
    if not run_log_path.exists():
        return {"runs": []}
    with run_log_path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("organizer_run_log.json 顶层必须是对象。")
    runs = data.get("runs", [])
    if not isinstance(runs, list):
        raise ValueError("organizer_run_log.json 中 runs 必须是列表。")
    data["runs"] = runs
    return data


def safe_write_run_log(run_log_path: Path, data: Dict[str, Any]) -> None:
    run_log_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = run_log_path.with_name(RUN_LOG_TMP_NAME)
    with tmp_path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp_path, run_log_path)


def create_apply_run(root: Path, run_log_path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    data = load_run_log(run_log_path)
    run = {
        "run_id": run_id_text(),
        "root": absolute_text(root),
        "time": now_text(),
        "status": "running",
        "undone": False,
        "undo_time": "",
        "undo_status": "",
        "operations": [],
    }
    data["runs"].append(run)
    safe_write_run_log(run_log_path, data)
    return data, run


def append_run_operation(
    run_log_path: Path,
    data: Dict[str, Any],
    run: Dict[str, Any],
    operation: Dict[str, Any],
) -> None:
    run.setdefault("operations", []).append(operation)
    safe_write_run_log(run_log_path, data)


def update_run_status(run_log_path: Path, data: Dict[str, Any], run: Dict[str, Any], status: str) -> None:
    run["status"] = status
    safe_write_run_log(run_log_path, data)


def update_run_undo_status(
    run_log_path: Path,
    data: Dict[str, Any],
    run: Dict[str, Any],
    undone: bool,
    undo_status: str,
) -> None:
    run["undone"] = undone
    run["undo_time"] = now_text()
    run["undo_status"] = undo_status
    safe_write_run_log(run_log_path, data)


def make_operation(action: str, source_before: Optional[Path] = None, target_after: Optional[Path] = None) -> Dict[str, Any]:
    operation: Dict[str, Any] = {"action": action}
    if source_before is not None:
        operation["source_before"] = absolute_text(source_before)
    if target_after is not None:
        operation["target_after"] = absolute_text(target_after)
    return operation


def log_item(
    log_path: Path,
    mode: str,
    action: str,
    item: Optional[WorkItem] = None,
    group: Optional[PlanGroup] = None,
    status: str = "success",
    error_message: str = "",
    source_path: Optional[Path] = None,
    target_path: Optional[Path] = None,
) -> None:
    det = item.detection if item else Detection()
    write_log(
        log_path,
        {
            "time": now_text(),
            "mode": mode,
            "action": action,
            "sequence_number": item.sequence_number if item and item.sequence_number else "",
            "sequence_range": group.sequence_range if group else "",
            "source_path": str(source_path or (item.current_path if item else "")),
            "target_path": str(target_path or (group.target_path if group else (item.final_path if item else ""))),
            "detected_date": det.date_label,
            "detected_category": det.category,
            "detected_orders": det.orders,
            "detected_quantity": det.quantity,
            "source_name": "; ".join(det.quantity_sources or det.category_sources or det.date_sources),
            "naming_template": group.naming_template if group else (item.naming_template if item else ""),
            "merge_enabled": str(det.merge_enabled).lower() if item else "",
            "status": status,
            "error_message": error_message,
        },
    )


def log_undo(
    log_path: Path,
    action: str,
    status: str = "success",
    error_message: str = "",
    source_path: Optional[Path] = None,
    target_path: Optional[Path] = None,
) -> None:
    write_log(
        log_path,
        {
            "time": now_text(),
            "mode": "undo-last",
            "action": action,
            "source_path": str(source_path or ""),
            "target_path": str(target_path or ""),
            "status": status,
            "error_message": error_message,
        },
    )


def log_confirmation(log_path: Path, mode: str, method: str) -> None:
    write_log(
        log_path,
        {
            "time": now_text(),
            "mode": mode,
            "action": "confirm",
            "status": "success",
            "error_message": f"confirmation_method={method}",
        },
    )


def zip_path_for_folder(folder: Path) -> Path:
    return folder.parent / f"{folder.name}.zip"


def log_group_zip(
    log_path: Path,
    mode: str,
    action: str,
    group: PlanGroup,
    status: str = "success",
    error_message: str = "",
) -> None:
    for item in group.items:
        log_item(
            log_path,
            mode,
            action,
            item,
            group=group,
            status=status,
            error_message=error_message,
            source_path=group.target_path,
            target_path=zip_path_for_folder(group.target_path),
        )


def find_executable(names: Sequence[str], extra_paths: Sequence[Path]) -> Optional[Path]:
    for path in extra_paths:
        if path.exists():
            return path
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def find_winrar() -> Optional[Path]:
    return find_executable(
        ["WinRAR.exe", "WinRAR"],
        [Path(r"C:\Program Files\WinRAR\WinRAR.exe"), Path(r"C:\Program Files (x86)\WinRAR\WinRAR.exe")],
    )


def find_unrar() -> Optional[Path]:
    return find_executable(
        ["UnRAR.exe", "UnRAR"],
        [Path(r"C:\Program Files\WinRAR\UnRAR.exe"), Path(r"C:\Program Files (x86)\WinRAR\UnRAR.exe")],
    )


def choose_archive_tool(archive: Path, winrar: Optional[Path], unrar: Optional[Path]) -> Tuple[Optional[Path], str]:
    if winrar:
        return winrar, ""
    if archive.suffix.lower() == ".rar" and unrar:
        return unrar, ""
    if archive.suffix.lower() in {".zip", ".7z"}:
        return None, "找不到 WinRAR.exe，zip/7z 第一版不使用 UnRAR.exe 处理。"
    return None, "找不到 WinRAR.exe；rar 文件也找不到可用的 UnRAR.exe。"


def list_zip_with_stdlib(archive: Path) -> Tuple[bool, List[str], str]:
    try:
        with zipfile.ZipFile(archive) as zf:
            return True, zf.namelist(), ""
    except Exception as exc:
        return False, [], f"zip 清单读取失败：{exc}"


def run_archive_list(tool: Path, archive: Path) -> Tuple[bool, List[str], str]:
    if archive.suffix.lower() == ".zip":
        return list_zip_with_stdlib(archive)
    command = [str(tool), "lb", "-p-", str(archive)]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3)
    except Exception as exc:
        return False, [], str(exc)
    output = (proc.stdout or "") + (proc.stderr or "")
    names = [line.strip() for line in output.splitlines() if line.strip()]
    if proc.returncode != 0 or not names:
        return False, names, output.strip() or "压缩包清单为空或读取失败。"
    return True, names, ""


def top_component(name: str) -> str:
    clean = name.replace("\\", "/").strip("/")
    return clean.split("/", 1)[0] if clean else ""


def archive_outer_name(archive: Path, entries: List[str]) -> Tuple[str, Path]:
    tops = {top_component(name) for name in entries if top_component(name)}
    if len(tops) == 1:
        return next(iter(tops)), archive.parent
    return archive.stem, archive.parent / archive.stem


def is_already_processed(name: str, config: Dict[str, Any]) -> bool:
    settings = config.get("already_processed", {})
    if not settings.get("enabled", True):
        return False
    for pattern in settings.get("patterns", []):
        try:
            if re.match(pattern, name):
                return True
        except re.error:
            continue
    return False


def is_generated_merge_folder_name(name: str) -> bool:
    return any(pattern.match(name) for pattern in GENERATED_MERGE_FOLDER_PATTERNS)


def list_root_archives(root: Path) -> List[Path]:
    return sorted(
        [p for p in root.iterdir() if p.is_file() and p.suffix.lower() in ARCHIVE_EXTENSIONS],
        key=lambda p: p.name.casefold(),
    )


def existing_root_folders(root: Path, config: Dict[str, Any]) -> List[WorkItem]:
    items: List[WorkItem] = []
    for path in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name.casefold()):
        if path.name in SYSTEM_FOLDER_NAMES or path.name.startswith("."):
            continue
        stat = path.stat()
        item = WorkItem(
            source_type="folder",
            original_name=path.name,
            current_path=path,
            root=root,
            created_time=stat.st_ctime,
            modified_time=stat.st_mtime,
        )
        if is_generated_merge_folder_name(path.name):
            item.skip_reason = "识别为已生成的合并目标目录，跳过，避免作为来源重复统计。"
        elif is_already_processed(path.name, config):
            item.skip_reason = "可能已经整理过，默认跳过。"
        items.append(item)
    return items


def archive_entries_to_scan(entries: List[str], outer_name: str) -> List[ScanEntry]:
    result: Dict[str, ScanEntry] = {}
    for raw in entries:
        clean = raw.replace("\\", "/").strip("/")
        if not clean:
            continue
        parts = clean.split("/")
        if parts and parts[0] == outer_name:
            parts = parts[1:]
        if not parts:
            continue
        rel = "/".join(parts)
        result[rel] = ScanEntry(name=parts[-1], rel_path=rel, is_dir=raw.endswith(("/", "\\")), suffix=Path(parts[-1]).suffix.lower())
        for idx in range(1, len(parts)):
            folder_rel = "/".join(parts[:idx])
            result.setdefault(folder_rel, ScanEntry(name=parts[idx - 1], rel_path=folder_rel, is_dir=True))
    return list(result.values())


def folder_entries_to_scan(folder: Path) -> List[ScanEntry]:
    entries: List[ScanEntry] = []
    try:
        for child in folder.rglob("*"):
            try:
                rel = str(child.relative_to(folder))
                entries.append(ScanEntry(name=child.name, rel_path=rel, is_dir=child.is_dir(), suffix=child.suffix.lower()))
            except OSError:
                continue
    except OSError:
        pass
    return entries


def combined_text(item: WorkItem) -> str:
    return item.original_name.casefold()


def keyword_hit(text: str, keywords: Sequence[str]) -> bool:
    folded = text.casefold()
    return any(str(keyword).casefold() in folded for keyword in keywords)


def priority_index(text: str, keywords: Sequence[str]) -> int:
    folded = text.casefold()
    for idx, keyword in enumerate(keywords):
        if str(keyword).casefold() in folded:
            return idx
    return len(keywords) + 1


def clean_windows_duplicate_suffix(name: str) -> Tuple[str, bool]:
    cleaned = re.sub(r"\(\d+\)$", "", name).rstrip()
    return cleaned or name, cleaned != name


def category_priority_index(category: str, config: Dict[str, Any]) -> Optional[int]:
    for idx, configured in enumerate(config.get("category_priority", []) or []):
        if str(configured) == category:
            return idx
    return None


def secondary_sequence_key(item: WorkItem, config: Dict[str, Any]) -> Tuple[Any, ...]:
    seq = config.get("sequence", {})
    sort_by = seq.get("sort_by", "name")
    name_key = item.original_name.casefold()
    if sort_by == "created_time":
        return (item.created_time, name_key)
    if sort_by == "modified_time":
        return (item.modified_time, name_key)
    if sort_by == "custom_priority":
        priorities = seq.get("priority_keywords", []) or []
        return (priority_index(combined_text(item), priorities), name_key)
    return (name_key,)


def category_sequence_key(item: WorkItem, config: Dict[str, Any]) -> Tuple[Any, ...]:
    unknown = config["fallback"]["unknown_category"]
    priority = item.detection.category_priority_index
    if priority is not None:
        category_rank = (0, priority)
    elif item.detection.category == unknown:
        category_rank = (2, item.detection.category.casefold())
    else:
        category_rank = (1, item.detection.category.casefold())
    return (*category_rank, *secondary_sequence_key(item, config))


def assign_sequence_numbers(items: List[WorkItem], config: Dict[str, Any]) -> None:
    seq = config.get("sequence", {})
    if not seq.get("enabled", True):
        return
    candidates: List[WorkItem] = []
    include_keywords = seq.get("include_keywords", []) or []
    for item in items:
        if item.skip_reason:
            continue
        text = combined_text(item)
        if include_keywords and not keyword_hit(text, include_keywords):
            item.skip_reason = "未命中 sequence.include_keywords，不参与序号。"
            continue
        candidates.append(item)
    for idx, item in enumerate(sorted(candidates, key=lambda it: category_sequence_key(it, config)), start=1):
        item.sequence_number = idx


def normalize_date(month: int, day: int) -> Optional[str]:
    if 1 <= month <= 12 and 1 <= day <= 31:
        return f"{month:02d}{day:02d}"
    return None


def detect_dates(original_name: str, unknown: str) -> Tuple[List[str], str, List[str]]:
    sources: List[str] = []
    found: List[str] = []
    search_text = re.sub(r"^\d{1,3}\s*[-_]\s*", "", original_name, count=1)
    patterns = [
        re.compile(r"(?<!\d)(?:20\d{2})[-.]?(0[1-9]|1[0-2])[-.]?([0-2]\d|3[01])(?!\d)"),
        re.compile(r"(?<!\d)(0?[1-9]|1[0-2])[-.](0?[1-9]|[12]\d|3[01])(?!\d)"),
        re.compile(r"(?<!\d)(0[1-9]|1[0-2])([0-2]\d|3[01])(?!\d)"),
    ]
    for pattern in patterns:
        for match in pattern.finditer(search_text):
            date_text = normalize_date(int(match.group(1)), int(match.group(2)))
            if date_text:
                found.append(date_text)
                sources.append("outer_folder_name_only")
    unique = sorted(set(found), key=lambda d: (int(d[:2]), int(d[2:])))
    if not unique:
        return [], unknown, []
    if len(unique) == 1:
        return unique, unique[0], sources
    return unique, f"{unique[0]}-{unique[-1]}", sources


def detect_category(original_name: str, config: Dict[str, Any]) -> Tuple[str, str, bool, List[str]]:
    best: Tuple[int, str, str, bool, str] = (0, "", "", False, "")
    for category, data in config.get("categories", {}).items():
        for keyword in data.get("keywords", []) or []:
            kw = str(keyword)
            if kw.casefold() in original_name.casefold() and len(kw) > best[0]:
                best = (len(kw), category, kw, bool(data.get("merge_enabled", False)), "outer_folder_name_only")
    if best[1]:
        return best[1], best[2], best[3], [best[4]]
    return config["fallback"]["unknown_category"], "", False, []


def detect_orders_quantity(
    original_name: str,
    config: Dict[str, Any],
) -> Tuple[int, int, str, List[str], List[str]]:
    pattern = re.compile(r"(\d+)\s*(?:单|订单)\s*[-_,，\s]*\s*(\d+)\s*(?:个|件)")
    match = pattern.search(original_name)
    if match:
        orders = int(match.group(1))
        quantity = int(match.group(2))
        return orders, quantity, QUANTITY_SOURCE_LABELS["outer_folder_name_only"], [], []

    default_orders = int(config["fallback"].get("default_orders_per_folder", 1))
    default_qty = int(config["fallback"].get("default_quantity_per_order", 1))
    orders = max(default_orders, 1)
    quantity = max(orders * default_qty, 1)
    return orders, quantity, QUANTITY_SOURCE_LABELS["fallback"], ["外层文件夹名未识别到数量，使用 fallback"], []


def detect_do_not_merge(original_name: str, config: Dict[str, Any]) -> List[str]:
    text = original_name.casefold()
    return [str(keyword) for keyword in config.get("do_not_merge_keywords", []) or [] if str(keyword).casefold() in text]


def scan_and_detect(item: WorkItem, config: Dict[str, Any]) -> None:
    item.scan_entries = []
    item.clean_original_name, item.removed_windows_duplicate_suffix = clean_windows_duplicate_suffix(item.original_name)
    detection = Detection()
    dates, label, date_sources = detect_dates(item.original_name, config["fallback"]["unknown_date"])
    detection.dates = dates
    detection.date_label = label
    detection.date_sources = date_sources
    category, keyword, merge_enabled, category_sources = detect_category(item.original_name, config)
    detection.category = category
    detection.matched_keyword = keyword
    detection.merge_enabled = merge_enabled
    detection.category_sources = category_sources
    detection.category_priority_index = category_priority_index(category, config)
    orders, quantity, quantity_source_label, quantity_sources, ignored_quantity_sources = detect_orders_quantity(item.original_name, config)
    detection.orders = orders
    detection.quantity = quantity
    detection.quantity_source_label = quantity_source_label
    detection.quantity_sources = quantity_sources
    detection.ignored_quantity_sources = ignored_quantity_sources
    detection.do_not_merge_hits = detect_do_not_merge(item.original_name, config)
    item.detection = detection


def safe_folder_name(name: str) -> str:
    cleaned = name.translate(str.maketrans({char: "-" for char in WINDOWS_ILLEGAL_CHARS})).strip().rstrip(".")
    return cleaned or "未命名"


def render_template(template: str, values: Dict[str, Any]) -> str:
    unknown = sorted(set(placeholders(template)) - set(values.keys()))
    if unknown:
        raise ConfigError(f"模板包含未知占位符：{', '.join(unknown)}")
    return safe_folder_name(template.format(**values))


def sequence_range(items: List[WorkItem], config: Dict[str, Any]) -> str:
    numbers = sorted(item.sequence_number for item in items if item.sequence_number is not None)
    if not numbers:
        return ""
    if len(numbers) == 1:
        return str(numbers[0])
    if config.get("sequence", {}).get("merged_range_style") == "list":
        return "+".join(str(number) for number in numbers)
    return f"{numbers[0]}~{numbers[-1]}"


def group_date_label(items: List[WorkItem], unknown: str) -> str:
    dates = sorted({date for item in items for date in item.detection.dates}, key=lambda d: (int(d[:2]), int(d[2:])))
    if not dates:
        return unknown
    if len(dates) == 1:
        return dates[0]
    return f"{dates[0]}-{dates[-1]}"


def render_inner_folder_name(item: WorkItem, config: Dict[str, Any]) -> str:
    return render_template(
        config["inner_folder_naming"]["template"],
        {
            "seq": item.sequence_number or "",
            "original_name": item.original_name,
            "clean_original_name": item.clean_original_name or item.original_name,
            "category": item.detection.category,
            "date": item.detection.date_label,
            "orders": item.detection.orders,
            "quantity": item.detection.quantity,
        },
    )


def render_group(items: List[WorkItem], is_merge: bool, config: Dict[str, Any], root: Path, reason: str) -> PlanGroup:
    seq_range = sequence_range(items, config)
    date_label = group_date_label(items, config["fallback"]["unknown_date"])
    category = items[0].detection.category
    orders = sum(item.detection.orders for item in items)
    quantity = sum(item.detection.quantity for item in items)
    template = config["naming"]["merged_template"] if is_merge else config["naming"]["single_template"]
    first = sorted(items, key=lambda item: item.sequence_number or 999999)[0]
    final_name = render_template(
        template,
        {
            "seq": first.sequence_number or "",
            "seq_range": seq_range,
            "date": date_label,
            "category": category,
            "orders": orders,
            "quantity": quantity,
            "original_name": first.original_name,
            "clean_original_name": first.clean_original_name or first.original_name,
            "custom_text": config.get("naming", {}).get("custom_text", ""),
            "merge_name": config.get("naming", {}).get("merge_name", category),
        },
    )
    group = PlanGroup(
        items=sorted(items, key=lambda item: item.sequence_number or 999999),
        is_merge=is_merge,
        sequence_range=seq_range,
        date_label=date_label,
        category=category,
        orders=orders,
        quantity=quantity,
        final_name=final_name,
        target_path=root / final_name,
        naming_template=template,
        reason=reason,
    )
    for item in group.items:
        item.final_path = group.target_path
        item.naming_template = template
        item.inner_name = render_inner_folder_name(item, config) if is_merge else ""
    return group


def build_merge_groups(items: List[WorkItem], config: Dict[str, Any], root: Path) -> List[PlanGroup]:
    active = [item for item in items if not item.skip_reason and item.sequence_number is not None]
    buckets: Dict[str, List[WorkItem]] = {}
    groups: List[PlanGroup] = []
    unknown = config["fallback"]["unknown_category"]
    for item in active:
        force_single = bool(item.detection.do_not_merge_hits)
        if item.detection.category == unknown or not item.detection.merge_enabled or force_single:
            groups.append(render_group([item], False, config, root, "不合并"))
        else:
            buckets.setdefault(item.detection.category, []).append(item)
    for category_items in buckets.values():
        if len(category_items) > 1:
            groups.append(render_group(category_items, True, config, root, "merge_enabled: true"))
        else:
            groups.append(render_group(category_items, False, config, root, "只有一个同类项目"))
    return sorted(groups, key=lambda group: min(item.sequence_number or 999999 for item in group.items))


def build_plan(root: Path, config: Dict[str, Any], mode: str, log_path: Path, archive_enabled: bool = False) -> Tuple[List[WorkItem], List[PlanGroup]]:
    log_dry_run = mode == "dry-run"
    winrar = find_winrar()
    unrar = find_unrar()
    items: List[WorkItem] = []
    for archive in list_root_archives(root):
        tool, error = choose_archive_tool(archive, winrar, unrar)
        temp = WorkItem("archive", archive.stem, root / archive.stem, root, archive_path=archive)
        if not tool:
            temp.skip_reason = error
            if log_dry_run:
                log_item(log_path, mode, "plan_extract", temp, status="failed", error_message=error, source_path=archive)
            items.append(temp)
            continue
        ok, entries, list_error = run_archive_list(tool, archive)
        if not ok:
            temp.skip_reason = f"压缩包列表读取失败：{list_error}"
            if log_dry_run:
                log_item(log_path, mode, "plan_extract", temp, status="failed", error_message=temp.skip_reason, source_path=archive)
            items.append(temp)
            continue
        outer, extract_destination = archive_outer_name(archive, entries)
        stat = archive.stat()
        item = WorkItem(
            source_type="archive",
            original_name=outer,
            current_path=root / outer,
            root=root,
            archive_path=archive,
            archive_tool=tool,
            archive_entries=entries,
            extract_destination=extract_destination,
            created_time=stat.st_ctime,
            modified_time=stat.st_mtime,
        )
        if item.current_path.exists():
            item.skip_reason = "解压目标文件夹已存在，跳过该压缩包；如需处理，会作为现有文件夹单独扫描。"
        items.append(item)
        if log_dry_run:
            log_item(log_path, mode, "plan_extract", item, status="success" if not item.skip_reason else "skipped", error_message=item.skip_reason, source_path=archive, target_path=item.current_path)

    items.extend(existing_root_folders(root, config))
    for item in items:
        if not item.skip_reason:
            try:
                scan_and_detect(item, config)
            except Exception as exc:
                item.skip_reason = f"扫描识别失败：{exc}"
        if log_dry_run:
            log_item(log_path, mode, "plan_scan", item, status="skipped" if item.skip_reason else "success", error_message=item.skip_reason)

    assign_sequence_numbers(items, config)
    if log_dry_run:
        for item in items:
            if item.skip_reason:
                log_item(log_path, mode, "plan_skip", item, status="skipped", error_message=item.skip_reason)
    groups = build_merge_groups(items, config, root)
    if log_dry_run:
        for group in groups:
            for item in group.items:
                if group.target_path.exists():
                    log_item(log_path, mode, "plan_skip", item, group=group, status="skipped", error_message="目标目录已存在，apply 会按 skip 处理。")
                else:
                    log_item(log_path, mode, "plan_merge" if group.is_merge else "plan_rename", item, group=group)
            if archive_enabled:
                zip_path = zip_path_for_folder(group.target_path)
                if group.target_path.exists() and not any(item.current_path == group.target_path for item in group.items):
                    log_group_zip(log_path, mode, "plan_zip", group, status="skipped", error_message="整理目标目录已存在，apply 会跳过该整理项，因此不压缩。")
                elif zip_path.exists():
                    log_group_zip(log_path, mode, "plan_zip", group, status="skipped", error_message="同名 zip 已存在，压缩阶段会跳过。")
                else:
                    log_group_zip(log_path, mode, "plan_zip", group)
    return items, groups


def category_priority_label(item: WorkItem, config: Dict[str, Any]) -> str:
    if item.detection.category_priority_index is not None:
        return f"第 {item.detection.category_priority_index + 1} 类"
    if item.detection.category == config["fallback"]["unknown_category"]:
        return "未识别，排在最后"
    return "未在 category_priority 中，排在最后"


def print_plan(root: Path, config: Dict[str, Any], items: List[WorkItem], groups: List[PlanGroup], mode: str, archive_enabled: bool = False) -> None:
    archives = [item for item in items if item.source_type == "archive"]
    group_by_item = {id(item): group for group in groups for item in group.items}
    print("\n========== 文件整理计划 ==========")
    print(f"模式：{mode}")
    print(f"根目录：{root}")
    print("\n当前命名模板：")
    print(f"- 单个文件夹：{config['naming']['single_template']}")
    print(f"- 合并文件夹：{config['naming']['merged_template']}")
    print(f"- 内部来源文件夹：{config['inner_folder_naming']['template']}")
    print("\n发现压缩包：")
    if archives:
        for item in archives:
            print(f"- {item.archive_path.name if item.archive_path else item.original_name} -> {item.current_path.name}")
            if item.skip_reason:
                print(f"  跳过原因：{item.skip_reason}")
    else:
        print("- 未发现 zip/rar/7z 压缩包")
    assigned = [item for item in items if item.sequence_number is not None]
    print("\n分配序号：")
    if assigned:
        for item in sorted(assigned, key=lambda it: it.sequence_number or 999999):
            print(f"{item.sequence_number} = {item.original_name}")
    else:
        print("- 没有可分配序号的项目")
    skipped = [item for item in items if item.skip_reason]
    if skipped:
        print("\n跳过项目：")
        for item in skipped:
            print(f"- {item.original_name}: {item.skip_reason}")
    print("\n识别结果：")
    for item in sorted(assigned, key=lambda it: it.sequence_number or 999999):
        det = item.detection
        group = group_by_item.get(id(item))
        if group and group.is_merge:
            merge_decision = f"有同品类文件夹，合并为：{group.final_name}"
        elif group:
            merge_decision = "不合并" if group.reason == "不合并" else f"{group.reason}，不合并"
        else:
            merge_decision = "未生成整理计划"
        print(f"[{item.sequence_number}-{item.original_name}]")
        print(f"  外层文件夹名：{item.original_name}")
        print("  识别来源：outer_folder_name_only")
        print(f"  清理名称：{item.clean_original_name or item.original_name}")
        print(f"  删除末尾括号编号：{'是' if item.removed_windows_duplicate_suffix else '否'}")
        print(f"  日期：{det.date_label}")
        print(f"  识别品类：{det.category}")
        print(f"  识别数量：{det.orders}单{det.quantity}个")
        print(f"  命中关键词：{det.matched_keyword or '未命中'}")
        print(f"  品类排序：{category_priority_label(item, config)}")
        print(f"  分配序号：{item.sequence_number}")
        print(f"  merge_enabled: {str(det.merge_enabled).lower()}")
        if det.do_not_merge_hits:
            print(f"  强制不合并命中：{', '.join(det.do_not_merge_hits)}")
        if det.quantity_source_label == "fallback":
            print(f"  说明：外层文件夹名未识别到数量，使用 fallback；未扫描内层文件名和内层文件夹名")
        else:
            print("  说明：未扫描内层文件名和内层文件夹名")
        print(f"  合并判断：{merge_decision}")
        print(f"  最终名称：{group.final_name if group else '无'}")
    print("\n整理计划：")
    if not groups:
        print("- 没有可执行的合并或重命名计划")
    for group in groups:
        label = "合并" if group.is_merge else "重命名"
        if group.target_path.exists():
            label = f"跳过({label})"
        source_label = " + ".join(f"{item.sequence_number}-{item.original_name}" for item in group.items)
        print(f"- {label}: {source_label}")
        print(f"  原因：{group.reason}")
        if group.target_path.exists():
            print("  跳过原因：目标目录已存在，按 conflict.target_exists=skip 处理")
        print(f"  使用模板：{group.naming_template}")
        print(f"  最终名称：{group.final_name}")
        if group.is_merge:
            print(f"  合并组：{group.category}")
            print(f"  来源数量：{len(group.items)} 个")
            print("  来源明细：")
            for item in group.items:
                seq_label = item.sequence_number if item.sequence_number is not None else "未分配"
                print(f"  - {seq_label}：{item.detection.orders}单{item.detection.quantity}个")
            print(f"  合计：{group.orders}单{group.quantity}个")
        if group.is_merge:
            print("  合并后内部结构计划：")
            print(f"  {group.final_name}\\")
            for item in group.items:
                print(f"      {item.inner_name}\\")
    print("\n压缩计划：")
    if not archive_enabled:
        print("- 未启用 --archive，本次不会压缩最终文件夹")
        print("==================================\n")
        return
    if not groups:
        print("- 没有需要压缩的最终文件夹")
    for group in groups:
        zip_path = zip_path_for_folder(group.target_path)
        source_is_already_target = any(item.current_path == group.target_path for item in group.items)
        if group.target_path.exists() and not source_is_already_target:
            print(f"- 跳过压缩: {group.final_name}")
            print("  跳过原因：整理目标目录已存在，apply 会跳过该整理项")
        elif zip_path.exists():
            print(f"- 跳过压缩: {group.final_name} -> {zip_path.name}")
            print("  跳过原因：同名 zip 已存在")
        else:
            print(f"- 压缩: {group.final_name}\\ -> {zip_path.name}")
    print("==================================\n")


def run_extract(item: WorkItem) -> Tuple[bool, str]:
    if not item.archive_path or not item.archive_tool or not item.extract_destination:
        return False, "缺少压缩包或解压工具信息。"
    item.extract_destination.mkdir(parents=True, exist_ok=True)
    command = [str(item.archive_tool), "x", "-ibck", "-o-", str(item.archive_path), str(item.extract_destination) + os.sep]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    except Exception as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, ((proc.stdout or "") + (proc.stderr or "")).strip()
    if not item.current_path.exists():
        return False, f"解压后未找到预期外层文件夹：{item.current_path}"
    return True, ""


def ensure_archive_extracted(item: WorkItem, log_path: Path) -> bool:
    if item.source_type != "archive":
        return item.current_path.exists()
    if item.current_path.exists():
        log_item(log_path, "apply", "extract", item, status="skipped", error_message="目标已存在，未重复解压。", source_path=item.archive_path, target_path=item.current_path)
        return True
    ok, error = run_extract(item)
    log_item(log_path, "apply", "extract", item, status="success" if ok else "failed", error_message=error, source_path=item.archive_path, target_path=item.current_path)
    return ok


def next_available_path(path: Path) -> Path:
    if not path.exists():
        return path
    for idx in range(1, 1000):
        candidate = path.with_name(f"{path.name}_{idx:03d}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法生成不冲突的路径：{path}")


def add_folder_to_zip(folder: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED) as zf:
        has_entries = False
        for path in sorted(folder.rglob("*"), key=lambda p: str(p).casefold()):
            arcname = path.relative_to(folder.parent)
            if path.is_dir():
                children = list(path.iterdir())
                if not children:
                    zf.write(path, str(arcname).replace("\\", "/") + "/")
                    has_entries = True
                continue
            zf.write(path, str(arcname).replace("\\", "/"))
            has_entries = True
        if not has_entries:
            zf.write(folder, f"{folder.name}/")


def compress_groups(
    groups: List[PlanGroup],
    log_path: Path,
    run_log_path: Path,
    run_log_data: Dict[str, Any],
    run: Dict[str, Any],
) -> int:
    failed_count = 0
    print("\n开始压缩最终文件夹：")
    if not groups:
        print("- 没有需要压缩的最终文件夹")
        return failed_count
    for group in groups:
        zip_path = zip_path_for_folder(group.target_path)
        if not group.target_path.exists() or not group.target_path.is_dir():
            log_group_zip(log_path, "apply", "zip", group, status="skipped", error_message="最终文件夹不存在，跳过压缩。")
            print(f"跳过压缩：最终文件夹不存在 -> {group.target_path}")
            continue
        if zip_path.exists():
            log_group_zip(log_path, "apply", "zip", group, status="skipped", error_message="同名 zip 已存在，未覆盖。")
            print(f"跳过压缩：同名 zip 已存在 -> {zip_path}")
            continue
        try:
            add_folder_to_zip(group.target_path, zip_path)
            append_run_operation(run_log_path, run_log_data, run, make_operation("archive_create", target_after=zip_path))
            log_group_zip(log_path, "apply", "zip", group)
            print(f"完成压缩：{zip_path.name}")
        except Exception as exc:
            if zip_path.exists():
                try:
                    zip_path.unlink()
                except OSError:
                    pass
            log_group_zip(log_path, "apply", "zip", group, status="failed", error_message=str(exc))
            print(f"错误：{group.final_name} 压缩失败，已跳过。原因：{exc}")
            failed_count += 1
    return failed_count


def apply_plan(
    groups: List[PlanGroup],
    log_path: Path,
    run_log_path: Path,
    run_log_data: Dict[str, Any],
    run: Dict[str, Any],
) -> Tuple[List[PlanGroup], int]:
    completed_groups: List[PlanGroup] = []
    failed_count = 0
    for group in groups:
        try:
            if group.target_path.exists():
                for item in group.items:
                    log_item(log_path, "apply", "skip", item, group=group, status="skipped", error_message="目标目录已存在，按 conflict.target_exists=skip 跳过。")
                print(f"跳过：目标目录已存在 -> {group.target_path}")
                continue
            for item in group.items:
                if not ensure_archive_extracted(item, log_path):
                    raise RuntimeError(f"来源准备失败：{item.original_name}")
            if group.is_merge:
                group.target_path.mkdir(parents=False, exist_ok=False)
                append_run_operation(run_log_path, run_log_data, run, make_operation("create_dir", target_after=group.target_path))
                for item in group.items:
                    destination = next_available_path(group.target_path / item.inner_name)
                    source_before = item.current_path
                    shutil.move(str(item.current_path), str(destination))
                    append_run_operation(run_log_path, run_log_data, run, make_operation("move", source_before=source_before, target_after=destination))
                    log_item(log_path, "apply", "move", item, group=group, source_path=source_before, target_path=destination)
                    log_item(log_path, "apply", "merge", item, group=group, source_path=destination, target_path=group.target_path)
                print(f"完成合并：{group.final_name}")
                completed_groups.append(group)
            else:
                item = group.items[0]
                if item.current_path == group.target_path:
                    log_item(log_path, "apply", "rename", item, group=group, status="skipped", error_message="来源名称已经等于目标名称。")
                    print(f"跳过重命名：{item.current_path.name}")
                    completed_groups.append(group)
                    continue
                source_before = item.current_path
                item.current_path.rename(group.target_path)
                append_run_operation(run_log_path, run_log_data, run, make_operation("move", source_before=source_before, target_after=group.target_path))
                log_item(log_path, "apply", "rename", item, group=group, source_path=source_before, target_path=group.target_path)
                print(f"完成重命名：{item.original_name} -> {group.final_name}")
                completed_groups.append(group)
        except Exception as exc:
            failed_count += 1
            for item in group.items:
                log_item(log_path, "apply", "skip", item, group=group, status="failed", error_message=str(exc))
            print(f"错误：{group.final_name} 执行失败，已跳过。原因：{exc}")
    return completed_groups, failed_count


def find_last_undoable_run(data: Dict[str, Any], root: Path) -> Optional[Dict[str, Any]]:
    root_text = absolute_text(root)
    for run in reversed(data.get("runs", [])):
        if not isinstance(run, dict):
            continue
        if run.get("root") != root_text:
            continue
        if run.get("undone") is True:
            continue
        if run.get("status") not in UNDOABLE_RUN_STATUSES:
            continue
        if not isinstance(run.get("operations"), list) or not run.get("operations"):
            continue
        return run
    return None


def print_undo_plan(run: Dict[str, Any], log_path: Path) -> None:
    print("\n========== 撤销上次整理计划 ==========")
    print(f"run_id：{run.get('run_id', '')}")
    print(f"运行时间：{run.get('time', '')}")
    print(f"原始状态：{run.get('status', '')}")
    print(f"根目录：{run.get('root', '')}")
    print("\n将按记录倒序处理：")
    for operation in reversed(run.get("operations", [])):
        action = operation.get("action", "")
        source = Path(operation["source_before"]) if operation.get("source_before") else None
        target = Path(operation["target_after"]) if operation.get("target_after") else None
        if action == "move":
            print(f"- 移回: {target} -> {source}")
            log_undo(log_path, "plan_undo", source_path=target, target_path=source)
        elif action == "create_dir":
            print(f"- 删除空目录: {target}")
            log_undo(log_path, "plan_undo", source_path=target, target_path=target)
        elif action == "archive_create":
            print(f"- 本次生成 zip，不自动删除: {target}")
            log_undo(log_path, "plan_undo", status="skipped", error_message="archive_create 第一版撤销不删除 zip。", source_path=target, target_path=target)
        else:
            print(f"- 跳过未知操作: {action}")
            log_undo(log_path, "plan_undo", status="skipped", error_message=f"未知操作：{action}", source_path=target, target_path=source)
    print("====================================\n")


def undo_move(source_before: Path, target_after: Path, log_path: Path) -> Tuple[int, int]:
    if not target_after.exists():
        message = "撤销来源不存在，跳过。"
        log_undo(log_path, "undo_skip", status="skipped", error_message=message, source_path=target_after, target_path=source_before)
        print(f"跳过移回：{message} {target_after}")
        return 0, 1
    if source_before.exists():
        message = "回退目标已存在，跳过，避免覆盖。"
        log_undo(log_path, "undo_skip", status="skipped", error_message=message, source_path=target_after, target_path=source_before)
        print(f"跳过移回：{message} {source_before}")
        return 0, 1
    if not source_before.parent.exists():
        message = "回退目标父目录不存在，跳过。"
        log_undo(log_path, "undo_skip", status="skipped", error_message=message, source_path=target_after, target_path=source_before)
        print(f"跳过移回：{message} {source_before.parent}")
        return 0, 1
    try:
        shutil.move(str(target_after), str(source_before))
        log_undo(log_path, "undo_move", source_path=target_after, target_path=source_before)
        print(f"完成移回：{target_after} -> {source_before}")
        return 1, 0
    except Exception as exc:
        log_undo(log_path, "undo_error", status="failed", error_message=str(exc), source_path=target_after, target_path=source_before)
        print(f"错误：移回失败 {target_after} -> {source_before}，原因：{exc}")
        return 0, 1


def undo_created_dir(target_after: Path, log_path: Path) -> Tuple[int, int]:
    if not target_after.exists():
        message = "本次创建目录已不存在，跳过。"
        log_undo(log_path, "undo_skip", status="skipped", error_message=message, source_path=target_after, target_path=target_after)
        print(f"跳过删除空目录：{message} {target_after}")
        return 0, 1
    if not target_after.is_dir():
        message = "记录目标不是目录，跳过。"
        log_undo(log_path, "undo_skip", status="skipped", error_message=message, source_path=target_after, target_path=target_after)
        print(f"跳过删除空目录：{message} {target_after}")
        return 0, 1
    try:
        next(target_after.iterdir())
    except StopIteration:
        try:
            target_after.rmdir()
            log_undo(log_path, "undo_remove_empty_dir", source_path=target_after, target_path=target_after)
            print(f"完成删除空目录：{target_after}")
            return 1, 0
        except Exception as exc:
            log_undo(log_path, "undo_error", status="failed", error_message=str(exc), source_path=target_after, target_path=target_after)
            print(f"错误：删除空目录失败 {target_after}，原因：{exc}")
            return 0, 1
    except Exception as exc:
        log_undo(log_path, "undo_error", status="failed", error_message=str(exc), source_path=target_after, target_path=target_after)
        print(f"错误：检查目录失败 {target_after}，原因：{exc}")
        return 0, 1
    message = "目录非空，跳过，避免删除未知文件。"
    log_undo(log_path, "undo_skip", status="skipped", error_message=message, source_path=target_after, target_path=target_after)
    print(f"跳过删除空目录：{message} {target_after}")
    return 0, 1


def undo_last(root: Path, log_path: Path, run_log_path: Path, confirmed: bool = False) -> int:
    try:
        data = load_run_log(run_log_path)
    except Exception as exc:
        message = f"organizer_run_log.json 损坏或无法读取：{exc}"
        log_undo(log_path, "undo_error", status="failed", error_message=message)
        print(f"错误：{message}")
        print("未执行任何撤销移动。")
        return 1

    run = find_last_undoable_run(data, root)
    if run is None:
        log_undo(log_path, "undo_skip", status="skipped", error_message="没有找到可撤销的最近一次 apply run。")
        print("没有找到可撤销的最近一次 apply run。")
        return 0

    print_undo_plan(run, log_path)
    if confirmed:
        print("已通过 --yes 确认，开始执行 undo-last")
        log_confirmation(log_path, "undo-last", "arg_yes")
    else:
        answer = input("即将执行以上撤销计划，是否继续？输入 YES 后继续：").strip()
        if answer != "YES":
            log_undo(log_path, "undo_skip", status="cancelled", error_message="用户未输入 YES，取消撤销。")
            print("已取消：未输入大写 YES，没有执行任何撤销。")
            return 0
        log_confirmation(log_path, "undo-last", "cli_yes")

    success_count = 0
    issue_count = 0
    for operation in reversed(run.get("operations", [])):
        action = operation.get("action", "")
        target_after = Path(operation["target_after"]) if operation.get("target_after") else None
        source_before = Path(operation["source_before"]) if operation.get("source_before") else None
        if action == "move" and source_before is not None and target_after is not None:
            successes, issues = undo_move(source_before, target_after, log_path)
            success_count += successes
            issue_count += issues
        elif action == "create_dir" and target_after is not None:
            successes, issues = undo_created_dir(target_after, log_path)
            success_count += successes
            issue_count += issues
        elif action == "archive_create" and target_after is not None:
            message = "archive_create 第一版撤销不删除 zip；该文件是本次生成产物。"
            log_undo(log_path, "undo_skip", status="skipped", error_message=message, source_path=target_after, target_path=target_after)
            print(f"提示：{message} {target_after}")
        else:
            issue_count += 1
            message = f"未知或不完整操作，跳过：{action}"
            log_undo(log_path, "undo_skip", status="skipped", error_message=message, source_path=target_after, target_path=source_before)
            print(f"跳过：{message}")

    if issue_count == 0:
        undo_status = "success"
    elif success_count > 0:
        undo_status = "partial"
    else:
        undo_status = "failed"
    update_run_undo_status(run_log_path, data, run, True, undo_status)
    print(f"undo-last 完成，状态：{undo_status}。日志已写入：{log_path}")
    return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Windows 文件整理自动化脚本 MVP")
    parser.add_argument("--root", required=True, help="需要处理的根目录")
    parser.add_argument("--config", default=str(Path(__file__).with_name("config.yaml")), help="配置文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只预览计划，不执行真实修改")
    parser.add_argument("--apply", action="store_true", help="执行真实整理，执行前仍需输入 YES")
    parser.add_argument("--undo-last", action="store_true", help="撤销最近一次成功或部分成功的 apply 运行")
    parser.add_argument("--archive", action="store_true", help="apply 成功后压缩最终文件夹")
    parser.add_argument("--yes", action="store_true", help="已确认执行 apply 或 undo-last，跳过命令行 YES 输入")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve()
    mode_count = sum(1 for enabled in (args.dry_run, args.apply, args.undo_last) if enabled)
    if mode_count > 1:
        print("错误：--dry-run、--apply 和 --undo-last 不能同时使用。")
        return 2
    if args.archive and args.undo_last:
        print("错误：--undo-last 模式不支持 --archive")
        return 2
    if args.archive and not args.apply:
        print("错误：--archive 只在 --apply 模式下生效")
        return 2
    if args.yes and args.dry_run:
        print("错误：--yes 不能用于 --dry-run")
        return 2
    if args.yes and not (args.apply or args.undo_last):
        print("错误：--yes 只能与 --apply 或 --undo-last 一起使用")
        return 2
    mode = "undo-last" if args.undo_last else ("apply" if args.apply else "dry-run")
    log_path = root / "rename_log.csv"
    run_log_path = root / RUN_LOG_NAME
    if not root.exists() or not root.is_dir():
        print(f"错误：root 不是有效目录：{root}")
        return 2
    if args.undo_last:
        return undo_last(root, log_path, run_log_path, confirmed=args.yes)
    try:
        config = load_config(config_path)
        validate_config(config)
    except Exception as exc:
        write_log(log_path, {"time": now_text(), "mode": mode, "action": "plan_skip" if mode == "dry-run" else "skip", "status": "failed", "error_message": str(exc)})
        print(f"配置错误：{exc}")
        return 1
    try:
        items, groups = build_plan(root, config, mode, log_path, archive_enabled=args.archive)
        print_plan(root, config, items, groups, mode, archive_enabled=args.archive)
    except Exception as exc:
        write_log(log_path, {"time": now_text(), "mode": mode, "action": "plan_skip" if mode == "dry-run" else "skip", "status": "failed", "error_message": str(exc)})
        print(f"生成计划失败：{exc}")
        return 1
    if mode == "dry-run":
        print("dry-run 完成：没有解压、移动、重命名、合并或压缩任何文件夹。")
        print(f"日志已写入：{log_path}")
        return 0
    if args.yes:
        print("已通过 --yes 确认，开始执行 apply")
        log_confirmation(log_path, "apply", "arg_yes")
    else:
        answer = input("即将执行以上整理计划，是否继续？输入 YES 后继续：").strip()
        if answer != "YES":
            write_log(log_path, {"time": now_text(), "mode": "apply", "action": "skip", "status": "cancelled", "error_message": "用户未输入 YES，取消执行。"})
            print("已取消：未输入大写 YES，没有执行任何真实修改。")
            return 0
        log_confirmation(log_path, "apply", "cli_yes")
    try:
        run_log_data, run = create_apply_run(root, run_log_path)
    except Exception as exc:
        write_log(log_path, {"time": now_text(), "mode": "apply", "action": "skip", "status": "failed", "error_message": f"无法创建 organizer_run_log.json：{exc}"})
        print(f"错误：无法创建 organizer_run_log.json：{exc}")
        print("未执行任何真实整理。")
        return 1
    for item in items:
        if item.skip_reason:
            log_item(log_path, "apply", "skip", item, status="skipped", error_message=item.skip_reason)
    completed_groups, failed_count = apply_plan(groups, log_path, run_log_path, run_log_data, run)
    if args.archive:
        failed_count += compress_groups(completed_groups, log_path, run_log_path, run_log_data, run)
    operation_count = len(run.get("operations", []))
    if operation_count == 0:
        run_status = "failed"
    elif failed_count:
        run_status = "partial"
    else:
        run_status = "success"
    update_run_status(run_log_path, run_log_data, run, run_status)
    print(f"apply 完成，状态：{run_status}。日志已写入：{log_path}")
    print(f"可撤销操作日志已写入：{run_log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
