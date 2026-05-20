from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Literal, cast


SETTINGS_NAME = "launcher_settings.json"
Mode = Literal["dry-run", "apply", "undo-last"]
VALID_MODES: set[str] = {"dry-run", "apply", "undo-last"}


@dataclass(frozen=True)
class LauncherSettings:
    python_command: str
    script_path: str
    root_path: str
    config_path: str
    mode: Mode
    archive_enabled: bool
    open_result_folder: bool


@dataclass(frozen=True)
class ValidationResult:
    script_path: Path
    root_path: Path
    config_path: Path | None


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
    default_config = resolved_base / "config.yaml"
    return LauncherSettings(
        python_command="py",
        script_path=str(default_script) if default_script.exists() else "",
        root_path="",
        config_path=str(default_config) if default_config.exists() else "",
        mode="dry-run",
        archive_enabled=False,
        open_result_folder=True,
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
    }


def settings_from_dict(data: dict[str, Any], defaults: LauncherSettings) -> LauncherSettings:
    merged = settings_to_dict(defaults)
    for key in merged:
        if key in data:
            merged[key] = data[key]

    return LauncherSettings(
        python_command=str(merged["python_command"]).strip(),
        script_path=clean_path_value(str(merged["script_path"])),
        root_path=clean_path_value(str(merged["root_path"])),
        config_path=clean_path_value(str(merged["config_path"])),
        mode=coerce_mode(merged["mode"]),
        archive_enabled=bool(merged["archive_enabled"]),
        open_result_folder=bool(merged["open_result_folder"]),
    )


def load_settings(settings_path: Path, defaults: LauncherSettings) -> LauncherSettings:
    if not settings_path.exists():
        return defaults
    with settings_path.open("r", encoding="utf-8") as f:
        loaded = json.load(f)
    if not isinstance(loaded, dict):
        raise ValueError("launcher_settings.json 顶层必须是对象。")
    return settings_from_dict(loaded, defaults)


def save_settings(settings_path: Path, settings: LauncherSettings) -> None:
    with settings_path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(settings_to_dict(settings), f, ensure_ascii=False, indent=2)
        f.write("\n")


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
