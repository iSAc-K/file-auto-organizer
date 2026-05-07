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
SPREADSHEET_EXTENSIONS = {".xlsx", ".xls", ".xlsm", ".csv", ".tsv"}
WINDOWS_ILLEGAL_CHARS = r'\/:*?"<>|'
SYSTEM_FOLDER_NAMES = {"__pycache__", ".git", ".hg", ".svn", ".venv", "venv", "env"}
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
    "categories": {},
    "do_not_merge_keywords": [],
    "naming": {
        "single_template": "{seq}-{date}-{category}-{orders}单-{quantity}个",
        "merged_template": "{seq_range}-{date}-{category}-{orders}单-{quantity}个",
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
    "conflict": {"target_exists": "skip"},
    "already_processed": {
        "enabled": True,
        "action": "skip",
        "patterns": [
            r"^\d+-(?:\d{4}|未知日期|\d{4}-\d{4})-.+-\d+单-\d+个$",
            r"^\d+~\d+-(?:\d{4}|未知日期|\d{4}-\d{4})-.+-\d+单-\d+个$",
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
    quantity_sources: List[str] = field(default_factory=list)
    date_sources: List[str] = field(default_factory=list)
    category_sources: List[str] = field(default_factory=list)
    do_not_merge_hits: List[str] = field(default_factory=list)


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
    text = config_path.read_text(encoding="utf-8")
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

    naming_allowed = {
        "seq",
        "seq_range",
        "date",
        "category",
        "orders",
        "quantity",
        "original_name",
        "custom_text",
        "merge_name",
    }
    inner_allowed = {"seq", "original_name", "category", "date", "orders", "quantity"}
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
        if is_already_processed(path.name, config):
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
    names = [item.original_name] + [entry.name for entry in item.scan_entries] + [entry.rel_path for entry in item.scan_entries]
    return " ".join(names).casefold()


def keyword_hit(text: str, keywords: Sequence[str]) -> bool:
    folded = text.casefold()
    return any(str(keyword).casefold() in folded for keyword in keywords)


def priority_index(text: str, keywords: Sequence[str]) -> int:
    folded = text.casefold()
    for idx, keyword in enumerate(keywords):
        if str(keyword).casefold() in folded:
            return idx
    return len(keywords) + 1


def assign_sequence_numbers(items: List[WorkItem], config: Dict[str, Any]) -> None:
    seq = config.get("sequence", {})
    if not seq.get("enabled", True):
        return
    candidates: List[WorkItem] = []
    include_keywords = seq.get("include_keywords", []) or []
    exclude_keywords = seq.get("exclude_keywords", []) or []
    for item in items:
        if item.skip_reason:
            continue
        text = combined_text(item)
        if include_keywords and not keyword_hit(text, include_keywords):
            item.skip_reason = "未命中 sequence.include_keywords，不参与序号。"
            continue
        if exclude_keywords and keyword_hit(text, exclude_keywords):
            item.skip_reason = "命中 sequence.exclude_keywords，不参与序号。"
            continue
        candidates.append(item)
    sort_by = seq.get("sort_by", "name")
    if sort_by == "created_time":
        key = lambda it: (it.created_time, it.original_name.casefold())
    elif sort_by == "modified_time":
        key = lambda it: (it.modified_time, it.original_name.casefold())
    elif sort_by == "custom_priority":
        priorities = seq.get("priority_keywords", []) or []
        key = lambda it: (priority_index(combined_text(it), priorities), it.original_name.casefold())
    else:
        key = lambda it: it.original_name.casefold()
    for idx, item in enumerate(sorted(candidates, key=key), start=1):
        item.sequence_number = idx


def normalize_date(month: int, day: int) -> Optional[str]:
    if 1 <= month <= 12 and 1 <= day <= 31:
        return f"{month:02d}{day:02d}"
    return None


def detect_dates(entries: List[ScanEntry], original_name: str, unknown: str) -> Tuple[List[str], str, List[str]]:
    sources: List[str] = []
    found: List[str] = []
    texts = [(entry.name, entry.rel_path) for entry in entries] + [(original_name, "外层名称")]
    patterns = [
        re.compile(r"(?<!\d)(?:20\d{2})[-.]?(0[1-9]|1[0-2])[-.]?([0-2]\d|3[01])(?!\d)"),
        re.compile(r"(?<!\d)(0?[1-9]|1[0-2])[-.](0?[1-9]|[12]\d|3[01])(?!\d)"),
        re.compile(r"(?<!\d)(0[1-9]|1[0-2])([0-2]\d|3[01])(?!\d)"),
    ]
    for text, source in texts:
        for pattern in patterns:
            for match in pattern.finditer(text):
                date_text = normalize_date(int(match.group(1)), int(match.group(2)))
                if date_text:
                    found.append(date_text)
                    sources.append(source)
    unique = sorted(set(found), key=lambda d: (int(d[:2]), int(d[2:])))
    if not unique:
        return [], unknown, []
    if len(unique) == 1:
        return unique, unique[0], sources
    return unique, f"{unique[0]}-{unique[-1]}", sources


def detect_category(entries: List[ScanEntry], original_name: str, config: Dict[str, Any]) -> Tuple[str, str, bool, List[str]]:
    best: Tuple[int, str, str, bool, str] = (0, "", "", False, "")
    texts = [(entry.name, entry.rel_path) for entry in entries] + [(original_name, "外层名称")]
    for category, data in config.get("categories", {}).items():
        for keyword in data.get("keywords", []) or []:
            kw = str(keyword)
            for text, source in texts:
                if kw.casefold() in text.casefold() and len(kw) > best[0]:
                    best = (len(kw), category, kw, bool(data.get("merge_enabled", False)), source)
    if best[1]:
        return best[1], best[2], best[3], [best[4]]
    return config["fallback"]["unknown_category"], "", False, []


def entry_priority(entry: ScanEntry) -> int:
    if not entry.is_dir and entry.suffix in SPREADSHEET_EXTENSIONS:
        return 1
    if entry.is_dir:
        return 2
    return 3


def detect_orders_quantity(entries: List[ScanEntry], config: Dict[str, Any]) -> Tuple[int, int, List[str]]:
    pattern = re.compile(
        r"(\d+)\s*(?:订单|单|orders?|order)\s*[-,，、\s]*\s*(\d+)\s*(?:个|件|pcs?|pieces?)",
        re.IGNORECASE,
    )
    matches: List[Tuple[int, int, int, str]] = []
    seen = set()
    for entry in entries:
        match = pattern.search(entry.name)
        if not match or entry.rel_path in seen:
            continue
        seen.add(entry.rel_path)
        matches.append((entry_priority(entry), int(match.group(1)), int(match.group(2)), entry.rel_path))
    if matches:
        best = min(match[0] for match in matches)
        selected = [match for match in matches if match[0] == best]
        return sum(match[1] for match in selected), sum(match[2] for match in selected), [match[3] for match in selected]
    immediate_dirs = {
        entry.rel_path.replace("\\", "/").split("/")[0]
        for entry in entries
        if entry.is_dir and len(entry.rel_path.replace("\\", "/").split("/")) == 1
    }
    default_orders = int(config["fallback"].get("default_orders_per_folder", 1))
    default_qty = int(config["fallback"].get("default_quantity_per_order", 1))
    orders = max(len(immediate_dirs), default_orders)
    quantity = max(orders * default_qty, 1)
    return orders, quantity, ["未识别到数量，使用 fallback 规则"]


def detect_do_not_merge(entries: List[ScanEntry], original_name: str, config: Dict[str, Any]) -> List[str]:
    text = " ".join([original_name] + [entry.name for entry in entries] + [entry.rel_path for entry in entries]).casefold()
    return [str(keyword) for keyword in config.get("do_not_merge_keywords", []) or [] if str(keyword).casefold() in text]


def scan_and_detect(item: WorkItem, config: Dict[str, Any]) -> None:
    item.scan_entries = archive_entries_to_scan(item.archive_entries, item.original_name) if item.source_type == "archive" else folder_entries_to_scan(item.current_path)
    detection = Detection()
    dates, label, date_sources = detect_dates(item.scan_entries, item.original_name, config["fallback"]["unknown_date"])
    detection.dates = dates
    detection.date_label = label
    detection.date_sources = date_sources
    category, keyword, merge_enabled, category_sources = detect_category(item.scan_entries, item.original_name, config)
    detection.category = category
    detection.matched_keyword = keyword
    detection.merge_enabled = merge_enabled
    detection.category_sources = category_sources
    orders, quantity, quantity_sources = detect_orders_quantity(item.scan_entries, config)
    detection.orders = orders
    detection.quantity = quantity
    detection.quantity_sources = quantity_sources
    detection.do_not_merge_hits = detect_do_not_merge(item.scan_entries, item.original_name, config)
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


def build_plan(root: Path, config: Dict[str, Any], mode: str, log_path: Path) -> Tuple[List[WorkItem], List[PlanGroup]]:
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
    return items, groups


def print_plan(root: Path, config: Dict[str, Any], items: List[WorkItem], groups: List[PlanGroup], mode: str) -> None:
    archives = [item for item in items if item.source_type == "archive"]
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
        print(f"[{item.sequence_number}-{item.original_name}]")
        print(f"  日期：{det.date_label}")
        print(f"  产品：{det.category}")
        print(f"  命中关键词：{det.matched_keyword or '未命中'}")
        print(f"  merge_enabled: {str(det.merge_enabled).lower()}")
        if det.do_not_merge_hits:
            print(f"  强制不合并命中：{', '.join(det.do_not_merge_hits)}")
        print(f"  单数：{det.orders}")
        print(f"  个数：{det.quantity}")
        print(f"  数量来源：{'; '.join(det.quantity_sources)}")
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
            print("  合并后内部结构计划：")
            print(f"  {group.final_name}\\")
            for item in group.items:
                print(f"      {item.inner_name}\\")
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


def apply_plan(groups: List[PlanGroup], log_path: Path) -> None:
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
                for item in group.items:
                    destination = next_available_path(group.target_path / item.inner_name)
                    shutil.move(str(item.current_path), str(destination))
                    log_item(log_path, "apply", "move", item, group=group, source_path=item.current_path, target_path=destination)
                    log_item(log_path, "apply", "merge", item, group=group, source_path=destination, target_path=group.target_path)
                print(f"完成合并：{group.final_name}")
            else:
                item = group.items[0]
                if item.current_path == group.target_path:
                    log_item(log_path, "apply", "rename", item, group=group, status="skipped", error_message="来源名称已经等于目标名称。")
                    print(f"跳过重命名：{item.current_path.name}")
                    continue
                item.current_path.rename(group.target_path)
                log_item(log_path, "apply", "rename", item, group=group, source_path=item.current_path, target_path=group.target_path)
                print(f"完成重命名：{item.original_name} -> {group.final_name}")
        except Exception as exc:
            for item in group.items:
                log_item(log_path, "apply", "skip", item, group=group, status="failed", error_message=str(exc))
            print(f"错误：{group.final_name} 执行失败，已跳过。原因：{exc}")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Windows 文件整理自动化脚本 MVP")
    parser.add_argument("--root", required=True, help="需要处理的根目录")
    parser.add_argument("--config", default=str(Path(__file__).with_name("config.yaml")), help="配置文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只预览计划，不执行真实修改")
    parser.add_argument("--apply", action="store_true", help="执行真实整理，执行前仍需输入 YES")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve()
    mode = "apply" if args.apply else "dry-run"
    log_path = root / "rename_log.csv"
    if args.apply and args.dry_run:
        print("错误：--dry-run 和 --apply 不能同时使用。")
        return 2
    if not root.exists() or not root.is_dir():
        print(f"错误：root 不是有效目录：{root}")
        return 2
    try:
        config = load_config(config_path)
        validate_config(config)
    except Exception as exc:
        write_log(log_path, {"time": now_text(), "mode": mode, "action": "plan_skip" if mode == "dry-run" else "skip", "status": "failed", "error_message": str(exc)})
        print(f"配置错误：{exc}")
        return 1
    try:
        items, groups = build_plan(root, config, mode, log_path)
        print_plan(root, config, items, groups, mode)
    except Exception as exc:
        write_log(log_path, {"time": now_text(), "mode": mode, "action": "plan_skip" if mode == "dry-run" else "skip", "status": "failed", "error_message": str(exc)})
        print(f"生成计划失败：{exc}")
        return 1
    if mode == "dry-run":
        print("dry-run 完成：没有解压、移动、重命名或合并任何文件夹。")
        print(f"日志已写入：{log_path}")
        return 0
    answer = input("即将执行以上整理计划，是否继续？输入 YES 后继续：").strip()
    if answer != "YES":
        write_log(log_path, {"time": now_text(), "mode": "apply", "action": "skip", "status": "cancelled", "error_message": "用户未输入 YES，取消执行。"})
        print("已取消：未输入大写 YES，没有执行任何真实修改。")
        return 0
    for item in items:
        if item.skip_reason:
            log_item(log_path, "apply", "skip", item, status="skipped", error_message=item.skip_reason)
    apply_plan(groups, log_path)
    print(f"apply 完成。日志已写入：{log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
