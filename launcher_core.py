from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys
import threading
from typing import Any, Literal, cast

from update_manager import DownloadProgress


SETTINGS_NAME = "launcher_settings.json"
Mode = Literal["dry-run", "apply", "undo-last"]
UpdateStatus = Literal[
    "checking",
    "latest",
    "available",
    "downloading",
    "verifying",
    "cancelled",
    "failed",
    "preparing_install",
    "updater_started",
]
VALID_MODES: set[str] = {"dry-run", "apply", "undo-last"}
PREVIEW_COLUMN_WIDTHS = {
    "序号": 48,
    "原文件夹": 210,
    "识别日期": 82,
    "识别品类": 118,
    "命中关键词": 130,
    "单量": 60,
    "数量": 60,
    "动作": 72,
    "目标名称": 220,
    "状态": 78,
    "原因": 240,
}
PREVIEW_COLUMN_MAX_WIDTH = 600
PREVIEW_COLUMN_PADDING = 24


class OperationGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._task_running = False
        self._update_running = False

    def begin_task(self) -> bool:
        with self._lock:
            if self._task_running or self._update_running:
                return False
            self._task_running = True
            return True

    def end_task(self) -> None:
        with self._lock:
            self._task_running = False

    def begin_update(self) -> bool:
        with self._lock:
            if self._task_running or self._update_running:
                return False
            self._update_running = True
            return True

    def end_update(self) -> None:
        with self._lock:
            self._update_running = False


@dataclass(frozen=True)
class LauncherSettings:
    python_command: str
    script_path: str
    root_path: str
    config_path: str
    mode: Mode
    archive_enabled: bool
    open_result_folder: bool
    last_output_dir: str = ""
    workers: int = 4


@dataclass(frozen=True)
class PreviewRow:
    sequence: str
    original_name: str
    detected_date: str
    detected_category: str
    matched_keyword: str
    orders: str
    quantity: str
    action: str
    target_name: str
    status: str
    reason: str


@dataclass(frozen=True)
class ValidationResult:
    script_path: Path
    root_path: Path
    config_path: Path | None


@dataclass(frozen=True)
class UpdateProgressText:
    downloaded: str
    speed: str
    remaining: str
    percent: str
    value: float
    indeterminate: bool


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def clean_path_value(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        return cleaned[1:-1].strip()
    return cleaned


def format_python_command(command: str) -> str:
    value = clean_path_value(command)
    lower_value = value.lower()
    looks_like_path = (
        "\\" in value
        or "/" in value
        or ":" in value
        or lower_value.endswith(".exe")
    )
    if looks_like_path:
        return f"& {ps_quote(value)}"
    return value


def coerce_mode(value: object) -> Mode:
    if value in VALID_MODES:
        return cast(Mode, value)
    return "dry-run"


def default_settings(base_dir: Path | None = None) -> LauncherSettings:
    resolved_base = base_dir if base_dir is not None else app_base_dir()
    default_script = resolved_base / "file_helper.py"
    default_config = resolved_base / "config.default.yaml"
    return LauncherSettings(
        python_command="py",
        script_path=str(default_script) if default_script.exists() else "",
        root_path="",
        config_path=str(default_config) if default_config.exists() else "",
        mode="dry-run",
        archive_enabled=False,
        open_result_folder=True,
        last_output_dir="",
        workers=4,
    )


def settings_to_dict(settings: LauncherSettings) -> dict[str, object]:
    return {
        "python_command": settings.python_command.strip(),
        "script_path": clean_path_value(settings.script_path),
        "root_path": clean_path_value(settings.root_path),
        "config_path": clean_path_value(settings.config_path),
        "mode": settings.mode,
        "archive_enabled": bool(settings.archive_enabled),
        "open_result_folder": bool(settings.open_result_folder),
        "last_output_dir": clean_path_value(settings.last_output_dir),
        "last_root_dir": clean_path_value(settings.root_path),
        "last_config_path": clean_path_value(settings.config_path),
        "last_python_path": settings.python_command.strip(),
        "workers": int(settings.workers),
    }


def settings_from_dict(data: dict[str, Any], defaults: LauncherSettings) -> LauncherSettings:
    merged = settings_to_dict(defaults)
    for key in merged:
        if key in data:
            merged[key] = data[key]
    if data.get("last_root_dir") and not data.get("root_path"):
        merged["root_path"] = data["last_root_dir"]
    if data.get("last_config_path") and not data.get("config_path"):
        merged["config_path"] = data["last_config_path"]
    if data.get("last_python_path") and not data.get("python_command"):
        merged["python_command"] = data["last_python_path"]

    return LauncherSettings(
        python_command=str(merged["python_command"]).strip(),
        script_path=clean_path_value(str(merged["script_path"])),
        root_path=clean_path_value(str(merged["root_path"])),
        config_path=clean_path_value(str(merged["config_path"])),
        mode=coerce_mode(merged["mode"]),
        archive_enabled=bool(merged["archive_enabled"]),
        open_result_folder=bool(merged["open_result_folder"]),
        last_output_dir=clean_path_value(str(merged.get("last_output_dir", ""))),
        workers=int(merged.get("workers", 4) or 4),
    )


def load_settings(settings_path: Path, defaults: LauncherSettings) -> LauncherSettings:
    if not settings_path.exists():
        return defaults
    try:
        with settings_path.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise ValueError("launcher_settings.json 顶层必须是对象。")
        return settings_from_dict(loaded, defaults)
    except Exception:
        return defaults


def save_settings(settings_path: Path, settings: LauncherSettings) -> None:
    with settings_path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(settings_to_dict(settings), f, ensure_ascii=False, indent=2)
        f.write("\n")


def build_safety_status_text(mode: str, archive_enabled: bool) -> str:
    normalized = coerce_mode(mode)
    if normalized == "apply":
        text = "当前模式：Apply｜需要确认｜冲突跳过｜不会覆盖已有目标"
        if archive_enabled:
            text += "｜压缩：开启｜同名压缩包存在时跳过"
        return text
    if normalized == "undo-last":
        return "当前模式：Undo｜撤销：仅根据日志执行｜不会猜测路径｜不会覆盖已有路径"
    return "当前模式：Dry Run｜不会修改文件｜不会压缩｜不会删除原件"


def build_update_status_text(
    status: UpdateStatus,
    current_version: str,
    latest_version: str = "",
    notes: list[str] | None = None,
    error: str = "",
) -> str:
    if status == "verifying":
        return (
            f"正在校验更新文件...\n\n当前版本：{current_version}\n"
            f"目标版本：{latest_version}"
        )
    if status == "cancelled":
        return (
            f"更新已取消。\n\n当前版本：{current_version}\n"
            f"目标版本：{latest_version}"
        )
    if status == "preparing_install":
        return (
            f"正在准备安装更新...\n\n当前版本：{current_version}\n"
            f"目标版本：{latest_version}"
        )
    if status == "updater_started":
        return (
            f"更新程序已启动。\n\n当前版本：{current_version}\n"
            f"目标版本：{latest_version}"
        )
    if status == "checking":
        return f"正在检查更新…\n\n当前版本：{current_version}"
    if status == "latest":
        version = latest_version or current_version
        return f"已是最新版本。\n\n当前版本：{current_version}\n线上版本：{version}"
    if status == "available":
        note_text = "\n".join(f"• {note}" for note in (notes or [])) or "• 未提供更新说明"
        return (
            f"发现新版本。\n\n当前版本：{current_version}\n"
            f"最新版本：{latest_version}\n\n更新内容：\n{note_text}"
        )
    if status == "downloading":
        return (
            f"正在下载并校验更新…\n\n当前版本：{current_version}\n"
            f"目标版本：{latest_version}\n\n完成后软件将自动关闭、安装并重新启动。"
        )
    return (
        f"检查更新失败。\n\n当前版本：{current_version}\n"
        f"失败原因：{error or '未知错误'}\n\n可以点击“重新检查”再次尝试。"
    )


def format_byte_count(byte_count: int | float) -> str:
    value = max(0.0, float(byte_count))
    if value < 1024:
        return f"{int(value)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        displayed_value = float(f"{value:.1f}")
        if displayed_value < 1024 or unit == "TB":
            return f"{displayed_value:.1f} {unit}"
    raise AssertionError("unreachable")


def format_download_speed(bytes_per_second: int | float) -> str:
    return f"{format_byte_count(bytes_per_second)}/s"


def format_remaining_time(seconds: int | float | None) -> str:
    if seconds is None:
        return "计算中"
    rounded_seconds = max(0, math.ceil(seconds))
    if rounded_seconds < 60:
        return f"约 {rounded_seconds} 秒"
    return f"约 {math.ceil(rounded_seconds / 60)} 分钟"


def build_update_progress_text(progress: DownloadProgress) -> UpdateProgressText:
    downloaded = format_byte_count(progress.downloaded_bytes)
    speed = format_download_speed(progress.average_bytes_per_second)
    if progress.total_bytes is None or progress.total_bytes <= 0:
        return UpdateProgressText(
            downloaded=downloaded,
            speed=speed,
            remaining="计算中",
            percent="下载中",
            value=0,
            indeterminate=True,
        )
    remaining = format_remaining_time(progress.estimated_seconds_remaining)
    value = min(1.0, max(0.0, progress.downloaded_bytes / progress.total_bytes))
    return UpdateProgressText(
        downloaded=f"{downloaded} / {format_byte_count(progress.total_bytes)}",
        speed=speed,
        remaining=remaining,
        percent=f"{round(value * 100)}%",
        value=value,
        indeterminate=False,
    )


def can_cancel_update(status: UpdateStatus) -> bool:
    return status in {"downloading", "verifying"}


def can_close_update_window(status: UpdateStatus) -> bool:
    return status in {"checking", "available", "latest", "failed", "cancelled"}


def wheel_delta_to_units(delta: int) -> int:
    if delta == 0:
        return 0
    return int(-1 * (delta / 120))


def preview_expanded_width(default_width: int, measured_widths: list[int]) -> int:
    if not measured_widths:
        return default_width
    return min(
        PREVIEW_COLUMN_MAX_WIDTH,
        max(default_width, max(measured_widths) + PREVIEW_COLUMN_PADDING),
    )


def toggle_preview_column(expanded_columns: set[str], column: str) -> set[str]:
    updated = set(expanded_columns)
    if column in updated:
        updated.remove(column)
    else:
        updated.add(column)
    return updated


def default_window_geometry(screen_width: int, screen_height: int) -> str:
    width = min(1440, max(1120, screen_width - 80))
    height = min(900, max(600, screen_height - 80))
    if screen_width >= 1280 and width < 1440:
        width = 1280
    if screen_height >= 720 and height < 900:
        height = 720
    return f"{width}x{height}"


def find_latest_report(root_path: str | Path, output_dir: str | Path | None = None) -> Path | None:
    candidates: list[Path] = []
    roots = [Path(root_path)]
    if output_dir:
        roots.append(Path(output_dir))
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        direct = root / "整理报告.xlsx"
        if direct.exists() and direct.is_file():
            candidates.append(direct)
        logs_report = root / "logs" / "整理报告.xlsx"
        if logs_report.exists() and logs_report.is_file():
            candidates.append(logs_report)
        try:
            candidates.extend(path for path in root.glob("*/整理报告.xlsx") if path.is_file())
        except OSError:
            continue
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def undo_log_status(root_path: str | Path) -> tuple[bool, str]:
    root = Path(root_path)
    run_log = root / "organizer_run_log.json"
    if not run_log.exists():
        return False, f"未找到 organizer_run_log.json：{run_log}"
    try:
        with run_log.open("r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception as exc:
        return False, f"organizer_run_log.json 无法读取：{exc}"
    runs = data.get("runs") if isinstance(data, dict) else None
    if not isinstance(runs, list) or not runs:
        return False, "organizer_run_log.json 中没有可检查的运行记录。"
    return True, f"已找到 organizer_run_log.json，共 {len(runs)} 条运行记录。"


def read_version(base_dir: str | Path | None = None) -> str:
    root = Path(base_dir) if base_dir is not None else app_base_dir()
    version_path = root / "VERSION.txt"
    if not version_path.exists():
        return ""
    for line in version_path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value:
            return value[1:] if value.lower().startswith("v") else value
    return ""


def build_preview_rows(root_path: str | Path, config_path: str | Path | None = None) -> list[PreviewRow]:
    from file_helper import build_plan, load_config, validate_config

    root = Path(root_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"root 不是有效目录：{root}")
    config_file = Path(config_path).expanduser().resolve() if config_path else app_base_dir() / "config.default.yaml"
    config = load_config(config_file)
    validate_config(config)
    log_path = root / "rename_log.csv"
    items, groups = build_plan(root, config, "dry-run", log_path, archive_enabled=False)
    group_by_item = {id(item): group for group in groups for item in group.items}
    rows: list[PreviewRow] = []
    for item in sorted(items, key=lambda current: current.sequence_number or 999999):
        det = item.detection
        group = group_by_item.get(id(item))
        reason = item.skip_reason
        status = "skipped" if item.skip_reason else "planned"
        target_name = group.final_name if group else ""
        if group and group.target_path.exists() and group.target_path != item.current_path:
            status = "conflict"
            reason = "目标目录已存在，apply 会按 skip 处理。"
        if item.skip_reason:
            action = "跳过"
        elif group and group.is_merge:
            action = "合并"
        elif group and item.current_path == group.target_path:
            action = "保持"
        else:
            action = "重命名"
        rows.append(
            PreviewRow(
                sequence=str(item.sequence_number or ""),
                original_name=item.original_name,
                detected_date=det.date_label,
                detected_category=det.category,
                matched_keyword=det.matched_keyword,
                orders=str(det.orders or ""),
                quantity=str(det.quantity or ""),
                action=action,
                target_name=target_name,
                status=status,
                reason=reason,
            )
        )
    return rows


def validate_paths(settings: LauncherSettings) -> tuple[ValidationResult | None, str | None]:
    if not settings.python_command.strip():
        return None, "Python 命令不能为空。"

    script_value = clean_path_value(settings.script_path)
    if not script_value:
        return None, "file_helper.py 路径不能为空。"
    script_path = Path(script_value)
    if not script_path.exists() or not script_path.is_file():
        return None, f"file_helper.py 不存在：\n{script_path}"

    root_value = clean_path_value(settings.root_path)
    if not root_value:
        return None, "要处理的文件夹路径不能为空。"
    root_path = Path(root_value)
    if not root_path.exists() or not root_path.is_dir():
        return None, f"要处理的文件夹不存在：\n{root_path}"

    if settings.mode == "undo-last":
        return ValidationResult(script_path=script_path, root_path=root_path, config_path=None), None

    config_value = clean_path_value(settings.config_path)
    config_path = Path(config_value) if config_value else None
    if config_path is not None and (not config_path.exists() or not config_path.is_file()):
        return None, f"config.yaml 不存在：\n{config_path}"

    return ValidationResult(script_path=script_path, root_path=root_path, config_path=config_path), None


def build_command(settings: LauncherSettings, include_yes: bool = False) -> str:
    mode = coerce_mode(settings.mode)
    normalized = LauncherSettings(
        python_command=settings.python_command,
        script_path=settings.script_path,
        root_path=settings.root_path,
        config_path=settings.config_path,
        mode=mode,
        archive_enabled=settings.archive_enabled,
        open_result_folder=settings.open_result_folder,
        last_output_dir=settings.last_output_dir,
        workers=settings.workers,
    )
    validated, error = validate_paths(normalized)
    if validated is None:
        raise ValueError(error or "启动器设置无效。")

    parts = [
        format_python_command(normalized.python_command),
        ps_quote(validated.script_path),
        "--root",
        ps_quote(validated.root_path),
    ]

    if mode == "apply":
        if validated.config_path is not None:
            parts.extend(["--config", ps_quote(validated.config_path)])
        parts.append("--apply")
        if normalized.archive_enabled:
            parts.append("--archive")
    elif mode == "undo-last":
        parts.append("--undo-last")
    else:
        if validated.config_path is not None:
            parts.extend(["--config", ps_quote(validated.config_path)])
        parts.append("--dry-run")

    if include_yes and mode in {"apply", "undo-last"}:
        parts.append("--yes")

    command = " ".join(parts)
    if normalized.open_result_folder:
        command += f"; if ($LASTEXITCODE -eq 0) {{ Start-Process -FilePath {ps_quote(validated.root_path)} }}"
    return command
