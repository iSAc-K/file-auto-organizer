# Modern Launcher UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a modern `CustomTkinter` launcher for Windows 文件整理助手 while preserving the existing safety model and keeping all organizer business logic in `file_helper.py`.

**Architecture:** Split launcher-only command/settings helpers into a small pure-Python module so command generation can be tested without opening a GUI. Rebuild `launcher_gui.py` as a `CustomTkinter` shell that uses those helpers for path cleanup, PowerShell quoting, settings defaults, and command construction. Keep `file_helper.py` untouched.

**Tech Stack:** Python 3, CustomTkinter, standard `tkinter.filedialog` and `tkinter.messagebox`, `unittest`, PowerShell launch via `subprocess.Popen`.

---

## File Structure

- Create `launcher_core.py`: launcher-only helpers for quoting, path cleanup, default settings, current settings serialization, path validation, and PowerShell command construction. This file must not import `file_helper.py` or classify customer files.
- Replace `launcher_gui.py`: modern `CustomTkinter` UI with left-side mode navigation, right-side work area, status labels, command preview, settings buttons, and the existing PowerShell launch behavior.
- Create `tests/test_launcher_core.py`: headless tests for command generation and settings behavior.
- Modify `README.md`: update the launcher section to say the GUI uses a modern desktop layout and may require `customtkinter` when run from source.
- Modify `VERSION.txt`: only if the implementation is intended to become the next packaged release. If not packaging in the same session, leave it unchanged and state that the release folder was not updated.
- Optionally modify `Windows文件整理助手.spec`: only during packaging, to collect `customtkinter` resources.

## Task 1: Add Headless Launcher Core

**Files:**
- Create: `launcher_core.py`
- Create: `tests/test_launcher_core.py`

- [ ] **Step 1: Create the first failing command tests**

Create `tests/test_launcher_core.py` with this content:

```python
from pathlib import Path
import tempfile
import unittest

from launcher_core import (
    LauncherSettings,
    build_command,
    clean_path_value,
    format_python_command,
    ps_quote,
)


class LauncherCoreTests(unittest.TestCase):
    def test_ps_quote_escapes_single_quotes(self):
        self.assertEqual(ps_quote("D:\\客户's\\root"), "'D:\\客户''s\\root'")

    def test_clean_path_value_strips_outer_quotes_and_spaces(self):
        self.assertEqual(clean_path_value("  \"D:\\待整理\"  "), "D:\\待整理")
        self.assertEqual(clean_path_value("  'D:\\待整理'  "), "D:\\待整理")

    def test_format_python_command_uses_call_operator_for_paths(self):
        command = format_python_command(r"C:\Program Files\Python\python.exe")
        self.assertEqual(command, "& 'C:\\Program Files\\Python\\python.exe'")

    def test_build_dry_run_command_has_no_yes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            script = Path(tmp) / "file_helper.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            config = Path(tmp) / "config.yaml"
            config.write_text("categories: {}\n", encoding="utf-8")

            settings = LauncherSettings(
                python_command="py",
                script_path=str(script),
                root_path=str(root),
                config_path=str(config),
                mode="dry-run",
                archive_enabled=False,
                open_result_folder=False,
            )

            command = build_command(settings)

        self.assertIn("--dry-run", command)
        self.assertIn("--config", command)
        self.assertNotIn("--yes", command)
        self.assertNotIn("Start-Process", command)

    def test_build_apply_archive_command_adds_yes_only_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            script = Path(tmp) / "file_helper.py"
            script.write_text("print('ok')\n", encoding="utf-8")

            settings = LauncherSettings(
                python_command="py",
                script_path=str(script),
                root_path=str(root),
                config_path="",
                mode="apply",
                archive_enabled=True,
                open_result_folder=True,
            )

            normal_command = build_command(settings)
            confirmed_command = build_command(settings, include_yes=True)

        self.assertIn("--apply", normal_command)
        self.assertIn("--archive", normal_command)
        self.assertNotIn("--yes", normal_command)
        self.assertIn("--yes", confirmed_command)
        self.assertIn("Start-Process", confirmed_command)

    def test_build_undo_command_omits_config_and_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            script = Path(tmp) / "file_helper.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            config = Path(tmp) / "config.yaml"
            config.write_text("categories: {}\n", encoding="utf-8")

            settings = LauncherSettings(
                python_command="py",
                script_path=str(script),
                root_path=str(root),
                config_path=str(config),
                mode="undo-last",
                archive_enabled=True,
                open_result_folder=False,
            )

            command = build_command(settings, include_yes=True)

        self.assertIn("--undo-last", command)
        self.assertIn("--yes", command)
        self.assertNotIn("--config", command)
        self.assertNotIn("--archive", command)
        self.assertNotIn("--apply", command)
        self.assertNotIn("--dry-run", command)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the failing tests**

Run:

```powershell
python -m unittest tests.test_launcher_core -v
```

Expected: import failure for `launcher_core`.

- [ ] **Step 3: Implement `launcher_core.py`**

Create `launcher_core.py` with this content:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Literal


SETTINGS_NAME = "launcher_settings.json"
Mode = Literal["dry-run", "apply", "undo-last"]


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
    return value.strip().strip('"').strip("'")


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


def default_settings(base_dir: Path) -> LauncherSettings:
    default_script = base_dir / "file_helper.py"
    default_config = base_dir / "config.yaml"
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


def settings_from_dict(data: dict[str, object], defaults: LauncherSettings) -> LauncherSettings:
    merged = settings_to_dict(defaults)
    for key in merged:
        if key in data:
            merged[key] = data[key]
    if merged.get("mode") not in {"dry-run", "apply", "undo-last"}:
        merged["mode"] = "dry-run"
    return LauncherSettings(
        python_command=str(merged["python_command"]),
        script_path=str(merged["script_path"]),
        root_path=str(merged["root_path"]),
        config_path=str(merged["config_path"]),
        mode=str(merged["mode"]),  # type: ignore[arg-type]
        archive_enabled=bool(merged["archive_enabled"]),
        open_result_folder=bool(merged["open_result_folder"]),
    )


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
    if config_path and (not config_path.exists() or not config_path.is_file()):
        return None, f"config.yaml 不存在：\n{config_path}"

    return ValidationResult(script_path=script_path, root_path=root_path, config_path=config_path), None


def build_command(settings: LauncherSettings, include_yes: bool = False) -> str:
    validated, error_message = validate_paths(settings)
    if validated is None:
        raise ValueError(error_message or "启动器设置无效。")

    parts = [
        format_python_command(settings.python_command),
        ps_quote(validated.script_path),
        "--root",
        ps_quote(validated.root_path),
    ]

    if settings.mode != "undo-last" and validated.config_path is not None:
        parts.extend(["--config", ps_quote(validated.config_path)])

    if settings.mode == "apply":
        parts.append("--apply")
        if settings.archive_enabled:
            parts.append("--archive")
    elif settings.mode == "undo-last":
        parts.append("--undo-last")
    else:
        parts.append("--dry-run")

    if include_yes:
        parts.append("--yes")

    command = " ".join(parts)
    if settings.open_result_folder:
        command += f"; if ($LASTEXITCODE -eq 0) {{ Start-Process -FilePath {ps_quote(validated.root_path)} }}"
    return command
```

- [ ] **Step 4: Run the tests until they pass**

Run:

```powershell
python -m unittest tests.test_launcher_core -v
```

Expected: all six tests pass.

- [ ] **Step 5: Run syntax checks**

Run:

```powershell
python -m py_compile launcher_core.py launcher_gui.py file_helper.py
```

Expected: no output and exit code 0.

- [ ] **Step 6: Commit Task 1**

Run:

```powershell
git add launcher_core.py tests/test_launcher_core.py
git commit -m "test: cover launcher command generation"
```

## Task 2: Replace Tkinter Layout With CustomTkinter Shell

**Files:**
- Modify: `launcher_gui.py`
- Test: `tests/test_launcher_core.py`

- [ ] **Step 1: Verify CustomTkinter availability**

Run:

```powershell
python -c "import customtkinter; print(customtkinter.__version__)"
```

Expected: prints a version number. If it fails with `ModuleNotFoundError`, install it with:

```powershell
python -m pip install customtkinter
```

- [ ] **Step 2: Replace imports and use the core module**

In `launcher_gui.py`, replace the current `tkinter` import block with:

```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from launcher_core import (
    SETTINGS_NAME,
    LauncherSettings,
    app_base_dir,
    build_command,
    clean_path_value,
    default_settings,
    settings_from_dict,
    settings_to_dict,
    validate_paths,
)
```

Keep:

```python
APP_TITLE = "Windows 文件整理助手"
```

- [ ] **Step 3: Convert `LauncherGui.__init__` to CustomTkinter variables**

Use this initialization shape:

```python
class LauncherGui:
    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.minsize(1080, 680)
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self.base_dir = app_base_dir()
        self.settings_path = self.base_dir / SETTINGS_NAME
        self.defaults = default_settings(self.base_dir)
        settings = self.load_settings()

        self.python_command = ctk.StringVar(value=settings.python_command)
        self.script_path = ctk.StringVar(value=settings.script_path)
        self.root_path = ctk.StringVar(value=settings.root_path)
        self.config_path = ctk.StringVar(value=settings.config_path)
        self.run_mode = ctk.StringVar(value=settings.mode)
        self.use_archive = ctk.BooleanVar(value=bool(settings.archive_enabled))
        self.open_result_folder = ctk.BooleanVar(value=bool(settings.open_result_folder))

        self.mode_buttons: dict[str, ctk.CTkButton] = {}
        self.path_status_labels: dict[str, ctk.CTkLabel] = {}
        self.archive_check: ctk.CTkCheckBox | None = None

        self._build_ui()
        self.run_mode.trace_add("write", self.on_mode_changed)
        for variable in (self.script_path, self.root_path, self.config_path):
            variable.trace_add("write", self.update_path_status)
        self.on_mode_changed()
        self.update_path_status()
```

- [ ] **Step 4: Implement settings methods using `LauncherSettings`**

Update these methods in `launcher_gui.py`:

```python
    def load_settings(self) -> LauncherSettings:
        if not self.settings_path.exists():
            return self.defaults
        try:
            with self.settings_path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("设置文件顶层必须是对象。")
        except Exception as exc:
            messagebox.showwarning(APP_TITLE, f"launcher_settings.json 已损坏，已使用默认设置。\n{exc}")
            return self.defaults
        return settings_from_dict(loaded, self.defaults)

    def current_settings(self) -> LauncherSettings:
        mode = self.run_mode.get()
        if mode not in {"dry-run", "apply", "undo-last"}:
            mode = "dry-run"
        return LauncherSettings(
            python_command=self.python_command.get().strip(),
            script_path=clean_path_value(self.script_path.get()),
            root_path=clean_path_value(self.root_path.get()),
            config_path=clean_path_value(self.config_path.get()),
            mode=mode,  # type: ignore[arg-type]
            archive_enabled=bool(self.use_archive.get()),
            open_result_folder=bool(self.open_result_folder.get()),
        )

    def save_settings(self, show_message: bool = True) -> None:
        try:
            with self.settings_path.open("w", encoding="utf-8", newline="\n") as f:
                json.dump(settings_to_dict(self.current_settings()), f, ensure_ascii=False, indent=2)
                f.write("\n")
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"保存设置失败：\n{exc}")
            return
        if show_message:
            messagebox.showinfo(APP_TITLE, "设置已保存")

    def clear_settings(self) -> None:
        try:
            if self.settings_path.exists():
                self.settings_path.unlink()
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"清空设置失败：\n{exc}")
            return
        self.apply_settings(self.defaults)
        messagebox.showinfo(APP_TITLE, "已清空保存的路径和选项")

    def apply_settings(self, settings: LauncherSettings) -> None:
        self.python_command.set(settings.python_command)
        self.script_path.set(settings.script_path)
        self.root_path.set(settings.root_path)
        self.config_path.set(settings.config_path)
        self.run_mode.set(settings.mode)
        self.use_archive.set(bool(settings.archive_enabled))
        self.open_result_folder.set(bool(settings.open_result_folder))
        self.set_preview("")
        self.update_path_status()
```

- [ ] **Step 5: Build the new UI skeleton**

Replace `_build_ui` with a CustomTkinter layout that creates:

```python
    def _build_ui(self) -> None:
        self.root.configure(fg_color="#EEF2F4")
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self.root, fg_color="#101820", corner_radius=0, width=230)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(5, weight=1)

        ctk.CTkLabel(
            sidebar,
            text="Windows\n文件整理助手",
            text_color="#F7FAFC",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=22, weight="bold"),
            justify="left",
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(24, 4))
        ctk.CTkLabel(
            sidebar,
            text="批量整理 / 预览优先 / 可撤销",
            text_color="#98A6B3",
            font=ctk.CTkFont(size=12),
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 26))

        self._add_mode_button(sidebar, "dry-run", "预览模式", "--dry-run，不改文件", 2)
        self._add_mode_button(sidebar, "apply", "执行整理", "--apply，需要确认", 3)
        self._add_mode_button(sidebar, "undo-last", "撤销上次", "--undo-last", 4)

        self.safety_status = ctk.CTkLabel(
            sidebar,
            text="当前不会修改文件",
            text_color="#DFF3E8",
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.safety_status.grid(row=6, column=0, sticky="sew", padx=20, pady=(0, 24))

        content = ctk.CTkFrame(self.root, fg_color="#EEF2F4", corner_radius=0)
        content.grid(row=0, column=1, sticky="nsew", padx=22, pady=22)
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(2, weight=1)

        self.mode_badge = ctk.CTkLabel(
            content,
            text="Dry Run",
            fg_color="#DFF3E8",
            text_color="#176342",
            corner_radius=18,
            width=90,
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.mode_badge.grid(row=0, column=1, sticky="ne")

        ctk.CTkLabel(
            content,
            text="整理任务设置",
            text_color="#101820",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=26, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        self.mode_description = ctk.CTkLabel(
            content,
            text="先生成预览命令，确认计划后再执行真实整理。",
            text_color="#5D6B76",
            font=ctk.CTkFont(size=13),
        )
        self.mode_description.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 14))

        body = ctk.CTkFrame(content, fg_color="#EEF2F4", corner_radius=0)
        body.grid(row=2, column=0, columnspan=2, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color="#EEF2F4", corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        left.grid_columnconfigure(0, weight=1)

        right = ctk.CTkFrame(body, fg_color="#FFFFFF", border_color="#D6DEE5", border_width=1, corner_radius=8, width=310)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_propagate(False)

        self._build_path_area(left)
        self._build_options_area(right)
        self._build_command_area(left)
        self._build_action_bar(content)
```

- [ ] **Step 6: Add the helper UI builders**

Add these helper methods to `LauncherGui`:

```python
    def _add_mode_button(self, parent: ctk.CTkFrame, mode: str, title: str, subtitle: str, row: int) -> None:
        button = ctk.CTkButton(
            parent,
            text=f"{title}\n{subtitle}",
            command=lambda value=mode: self.run_mode.set(value),
            anchor="w",
            height=54,
            corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        button.grid(row=row, column=0, sticky="ew", padx=16, pady=5)
        self.mode_buttons[mode] = button

    def _build_path_area(self, parent: ctk.CTkFrame) -> None:
        self._path_card(parent, 0, "Python 命令", self.python_command, None, "示例：py、python、完整 python.exe 路径")
        self._path_card(parent, 1, "file_helper.py 路径", self.script_path, self.select_script, "核心整理脚本")
        self._path_card(parent, 2, "要处理的文件夹路径", self.root_path, self.select_root_folder, "命令行里的 --root")
        self._path_card(parent, 3, "config.yaml 路径", self.config_path, self.select_config, "可留空，由 file_helper.py 使用默认配置")

    def _path_card(
        self,
        parent: ctk.CTkFrame,
        row: int,
        label: str,
        variable: ctk.StringVar,
        browse_command: object,
        hint: str,
    ) -> None:
        card = ctk.CTkFrame(parent, fg_color="#FFFFFF", border_color="#D6DEE5", border_width=1, corner_radius=8)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        card.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(12, 6))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text=label, text_color="#4F5E69", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, sticky="w")
        status = ctk.CTkLabel(top, text="", text_color="#6A7884", font=ctk.CTkFont(size=12))
        status.grid(row=0, column=1, sticky="e")
        self.path_status_labels[label] = status
        entry = ctk.CTkEntry(card, textvariable=variable, height=38, border_color="#D6DEE5", fg_color="#F6F8FA")
        entry.grid(row=1, column=0, sticky="ew", padx=(14, 8), pady=(0, 12))
        if browse_command is not None:
            ctk.CTkButton(card, text="选择", command=browse_command, width=72, height=38, fg_color="#101820", hover_color="#24313C").grid(row=1, column=1, sticky="e", padx=(0, 14), pady=(0, 12))
        ctk.CTkLabel(card, text=hint, text_color="#7A8791", font=ctk.CTkFont(size=11)).grid(row=2, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 12))

    def _build_options_area(self, parent: ctk.CTkFrame) -> None:
        ctk.CTkLabel(parent, text="模式说明", text_color="#101820", font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(18, 8))
        self.mode_help = ctk.CTkLabel(parent, text="", text_color="#4F5E69", justify="left", wraplength=260)
        self.mode_help.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 18))
        ctk.CTkLabel(parent, text="选项", text_color="#101820", font=ctk.CTkFont(size=16, weight="bold")).grid(row=2, column=0, sticky="w", padx=16, pady=(0, 8))
        self.archive_check = ctk.CTkCheckBox(parent, text="整理完成后压缩最终文件夹", variable=self.use_archive, fg_color="#F05A28", hover_color="#C84418")
        self.archive_check.grid(row=3, column=0, sticky="w", padx=16, pady=(0, 10))
        ctk.CTkCheckBox(parent, text="处理完成后打开结果目录", variable=self.open_result_folder, fg_color="#F05A28", hover_color="#C84418").grid(row=4, column=0, sticky="w", padx=16, pady=(0, 18))
        ctk.CTkLabel(parent, text="设置", text_color="#101820", font=ctk.CTkFont(size=16, weight="bold")).grid(row=5, column=0, sticky="w", padx=16, pady=(0, 8))
        ctk.CTkLabel(parent, text="保存设置只记录路径和启动选项，不保存客户文件内容。", text_color="#667580", justify="left", wraplength=260).grid(row=6, column=0, sticky="ew", padx=16, pady=(0, 12))
        ctk.CTkButton(parent, text="保存设置", command=self.save_settings, fg_color="#101820", hover_color="#24313C").grid(row=7, column=0, sticky="ew", padx=16, pady=(0, 8))
        ctk.CTkButton(parent, text="清空设置", command=self.clear_settings, fg_color="#FFFFFF", text_color="#101820", border_color="#C7D0D8", border_width=1, hover_color="#EEF2F4").grid(row=8, column=0, sticky="ew", padx=16)

    def _build_command_area(self, parent: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(parent, fg_color="#FFFFFF", border_color="#D6DEE5", border_width=1, corner_radius=8)
        card.grid(row=4, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)
        parent.grid_rowconfigure(4, weight=1)
        ctk.CTkLabel(card, text="命令预览", text_color="#101820", font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 8))
        self.command_preview = ctk.CTkTextbox(card, height=150, wrap="word", fg_color="#101820", text_color="#DCE6EE", border_width=0, font=ctk.CTkFont(family="Consolas", size=12))
        self.command_preview.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 8))
        ctk.CTkLabel(card, text="普通预览和复制命令不带 --yes；只有确认执行或撤销后才追加。", text_color="#667580", font=ctk.CTkFont(size=11)).grid(row=2, column=0, sticky="w", padx=14, pady=(0, 14))

    def _build_action_bar(self, parent: ctk.CTkFrame) -> None:
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(bar, text="生成命令", command=self.generate_command, fg_color="#FFFFFF", text_color="#101820", border_color="#C7D0D8", border_width=1, hover_color="#E2E8EE").grid(row=0, column=1, padx=(0, 10))
        ctk.CTkButton(bar, text="复制命令", command=self.copy_command, fg_color="#FFFFFF", text_color="#101820", border_color="#C7D0D8", border_width=1, hover_color="#E2E8EE").grid(row=0, column=2, padx=(0, 10))
        ctk.CTkButton(bar, text="在 PowerShell 中运行", command=self.run_in_powershell, fg_color="#F05A28", hover_color="#C84418", width=170).grid(row=0, column=3)
```

- [ ] **Step 7: Update mode and status behavior**

Add or update these methods:

```python
    def on_mode_changed(self, *_args: object) -> None:
        mode = self.run_mode.get()
        colors = {
            "dry-run": ("#F05A28", "#C84418"),
            "apply": ("#F05A28", "#C84418"),
            "undo-last": ("#F05A28", "#C84418"),
        }
        for value, button in self.mode_buttons.items():
            if value == mode:
                button.configure(fg_color=colors[value][0], hover_color=colors[value][1], text_color="#FFFFFF")
            else:
                button.configure(fg_color="#1B2630", hover_color="#24313C", text_color="#DCE6EE")

        if mode == "apply":
            self.mode_badge.configure(text="Apply", fg_color="#FFE7D8", text_color="#9B3417")
            self.safety_status.configure(text="真实整理需要确认", text_color="#FFE7D8")
            self.mode_description.configure(text="执行前请先看过 dry-run 结果；确认后启动器会追加一次性 --yes。")
            self.mode_help.configure(text="执行模式会移动、合并、重命名文件夹。点击运行时会先弹窗确认。")
            if self.archive_check is not None:
                self.archive_check.configure(state="normal")
        elif mode == "undo-last":
            self.use_archive.set(False)
            self.mode_badge.configure(text="Undo", fg_color="#FFE7D8", text_color="#9B3417")
            self.safety_status.configure(text="撤销需要确认", text_color="#FFE7D8")
            self.mode_description.configure(text="撤销只根据 organizer_run_log.json 记录执行，不根据文件名猜测。")
            self.mode_help.configure(text="撤销模式不使用 config.yaml，也不会启用压缩选项。")
            if self.archive_check is not None:
                self.archive_check.configure(state="disabled")
        else:
            self.mode_badge.configure(text="Dry Run", fg_color="#DFF3E8", text_color="#176342")
            self.safety_status.configure(text="当前不会修改文件", text_color="#DFF3E8")
            self.mode_description.configure(text="先生成预览命令，确认计划后再执行真实整理。")
            self.mode_help.configure(text="预览模式只扫描并输出整理计划，不移动、不合并、不压缩任何文件。")
            if self.archive_check is not None:
                self.archive_check.configure(state="normal")
        self.update_path_status()

    def update_path_status(self, *_args: object) -> None:
        statuses = {
            "file_helper.py 路径": self._path_status(self.script_path.get(), expect_file=True, empty_text="未选择"),
            "要处理的文件夹路径": self._path_status(self.root_path.get(), expect_file=False, empty_text="未选择"),
            "config.yaml 路径": ("撤销模式不使用", "#7A8791") if self.run_mode.get() == "undo-last" else self._path_status(self.config_path.get(), expect_file=True, empty_text="可留空"),
            "Python 命令": ("已填写" if self.python_command.get().strip() else "未填写", "#176342" if self.python_command.get().strip() else "#9B3417"),
        }
        for label, (text, color) in statuses.items():
            widget = self.path_status_labels.get(label)
            if widget is not None:
                widget.configure(text=text, text_color=color)

    def _path_status(self, value: str, expect_file: bool, empty_text: str) -> tuple[str, str]:
        cleaned = clean_path_value(value)
        if not cleaned:
            return empty_text, "#9B3417" if empty_text == "未选择" else "#7A8791"
        path = Path(cleaned)
        exists = path.is_file() if expect_file else path.is_dir()
        if exists:
            return "已找到", "#176342"
        return "路径不存在", "#9B3417"
```

- [ ] **Step 8: Wire command generation and PowerShell launch to `launcher_core`**

Update command methods:

```python
    def validate_inputs(self) -> bool:
        _validated, error_message = validate_paths(self.current_settings())
        if error_message:
            messagebox.showerror(APP_TITLE, error_message)
            return False
        return True

    def build_command(self, include_yes: bool = False) -> str | None:
        try:
            return build_command(self.current_settings(), include_yes=include_yes)
        except ValueError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return None

    def set_preview(self, command: str) -> None:
        self.command_preview.delete("1.0", "end")
        self.command_preview.insert("1.0", command)

    def get_preview(self) -> str:
        return self.command_preview.get("1.0", "end").strip()
```

Keep existing `generate_command`, `copy_command`, `confirm_real_run`, and `run_in_powershell` behavior, adjusting only widget API differences. `run_in_powershell` must still call `self.build_command()` before confirmation, then `self.build_command(include_yes=True)` only after confirmation.

- [ ] **Step 9: Update `main`**

Use:

```python
def main() -> None:
    root = ctk.CTk()
    LauncherGui(root)
    root.mainloop()
```

- [ ] **Step 10: Run checks**

Run:

```powershell
python -m unittest tests.test_launcher_core -v
python -m py_compile launcher_core.py launcher_gui.py file_helper.py
python -c "import launcher_gui; print('launcher_gui import ok')"
```

Expected: tests pass, compile succeeds, import prints `launcher_gui import ok`.

- [ ] **Step 11: Commit Task 2**

Run:

```powershell
git add launcher_gui.py launcher_core.py tests/test_launcher_core.py
git commit -m "feat: modernize launcher UI"
```

## Task 3: Update Documentation for the Modern Launcher

**Files:**
- Modify: `README.md`
- Optionally modify: `VERSION.txt`

- [ ] **Step 1: Update source-run setup**

In `README.md`, under installation preparation, add:

```markdown
如果要从源码运行现代化图形启动器，还需要安装 CustomTkinter：

```powershell
python -m pip install customtkinter
```

打包后的 EXE 会自带界面依赖，普通用户不需要单独安装。
```

- [ ] **Step 2: Replace the launcher description**

In the `图形启动器 launcher_gui.py` section, keep the command `py launcher_gui.py`, then describe:

```markdown
现代化启动器左侧是模式导航，右侧是整理任务工作区：

- `预览模式` 对应 `--dry-run`，默认选中，不做真实修改。
- `执行整理` 对应 `--apply`，点击运行时会先弹窗确认，确认后才追加一次性 `--yes`。
- `撤销上次` 对应 `--undo-last`，不使用 `config.yaml`，也会禁用压缩选项。
- 路径行会显示“已找到 / 未选择 / 路径不存在”等状态，方便在运行前检查。
- 普通命令预览和复制命令永远不带 `--yes`。
- `保存设置` 会保存 Python 命令、脚本路径、root 路径、config 路径、运行模式、压缩选项和打开结果目录选项；它只保存这些路径和启动选项，不保存客户文件内容。
```

- [ ] **Step 3: Decide `VERSION.txt`**

If this session also updates packaged release artifacts, change `VERSION.txt` to the next version string agreed with the user. If this session only changes source UI, do not edit `VERSION.txt`.

- [ ] **Step 4: Run documentation sanity checks**

Run:

```powershell
Select-String -Path README.md -Pattern "CustomTkinter|预览模式|普通命令预览"
git diff -- README.md VERSION.txt
```

Expected: README contains the new launcher language. `VERSION.txt` changes only if packaging is in scope.

- [ ] **Step 5: Commit Task 3**

Run:

```powershell
git add README.md VERSION.txt
git commit -m "docs: update launcher UI instructions"
```

If `VERSION.txt` was not changed, use:

```powershell
git add README.md
git commit -m "docs: update launcher UI instructions"
```

## Task 4: Manual GUI Verification

**Files:**
- No source edits unless verification finds a bug.

- [ ] **Step 1: Create a disposable root folder**

Run:

```powershell
$root = Join-Path $PWD ".tmp_launcher_ui_check"
New-Item -ItemType Directory -Force -Path $root | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $root "0507-测试产品-1单2个") | Out-Null
Write-Output $root
```

Expected: prints the temp root path.

- [ ] **Step 2: Launch the GUI from source**

Run:

```powershell
python launcher_gui.py
```

Expected: modern CustomTkinter window opens with a dark left sidebar, `预览模式` selected, and right-side task settings.

- [ ] **Step 3: Verify dry-run command behavior manually**

In the GUI:

1. Set root folder to `.tmp_launcher_ui_check`.
2. Confirm `预览模式` is selected.
3. Click `生成命令`.
4. Confirm the command contains `--dry-run`.
5. Confirm the command does not contain `--yes`.
6. Click `复制命令`.
7. Paste into a temporary text field and confirm it still does not contain `--yes`.

- [ ] **Step 4: Verify apply command behavior manually**

In the GUI:

1. Select `执行整理`.
2. Check `整理完成后压缩最终文件夹`.
3. Click `生成命令`.
4. Confirm the command contains `--apply --archive`.
5. Confirm the command does not contain `--yes`.
6. Click `在 PowerShell 中运行`.
7. When the confirmation dialog appears, click No.
8. Confirm no PowerShell execution happens.

- [ ] **Step 5: Verify undo command behavior manually**

In the GUI:

1. Select `撤销上次`.
2. Confirm the compression checkbox is disabled.
3. Click `生成命令`.
4. Confirm the command contains `--undo-last`.
5. Confirm the command does not contain `--config`, `--archive`, `--apply`, or `--dry-run`.

- [ ] **Step 6: Verify settings behavior manually**

In the GUI:

1. Click `保存设置`.
2. Confirm `launcher_settings.json` appears next to `launcher_gui.py`.
3. Click `清空设置`.
4. Confirm root path is cleared and default paths are restored.

- [ ] **Step 7: Run final automated checks**

Run:

```powershell
python -m unittest tests.test_launcher_core -v
python -m py_compile launcher_core.py launcher_gui.py file_helper.py
git status --short
```

Expected: tests pass, compile succeeds, and only intentional files are modified. Runtime files such as `launcher_settings.json` and `.tmp_launcher_ui_check` must not be staged.

- [ ] **Step 8: Commit manual-verification fixes if needed**

If verification required source fixes, commit them:

```powershell
git add launcher_gui.py launcher_core.py tests/test_launcher_core.py README.md
git commit -m "fix: polish modern launcher verification issues"
```

If no source fixes were needed, do not create an empty commit.

## Task 5: Optional Packaging Follow-Up

**Files:**
- Modify: `Windows文件整理助手.spec`
- Modify: `VERSION.txt`
- Modify or create: `dist/Windows文件整理助手/`
- Copy into release folder only if the user asks for packaging.

- [ ] **Step 1: Confirm packaging is in scope**

Ask the user whether to rebuild the foldered EXE now. Do not package silently.

- [ ] **Step 2: Update PyInstaller spec for CustomTkinter**

If packaging is requested, update the spec to collect CustomTkinter data. The relevant Python snippet is:

```python
from PyInstaller.utils.hooks import collect_all

customtkinter_datas, customtkinter_binaries, customtkinter_hiddenimports = collect_all("customtkinter")
```

Then merge those lists into `Analysis(datas=...)`, `Analysis(binaries=...)`, and `Analysis(hiddenimports=...)`.

- [ ] **Step 3: Build foldered release**

Run:

```powershell
py -m PyInstaller --noconfirm --onedir --windowed --name "Windows文件整理助手" launcher_gui.py
```

Expected: `dist\Windows文件整理助手\Windows文件整理助手.exe` exists.

- [ ] **Step 4: Sync external companion files**

Run:

```powershell
Copy-Item -Force file_helper.py "dist\Windows文件整理助手\file_helper.py"
Copy-Item -Force config.yaml "dist\Windows文件整理助手\config.yaml"
Copy-Item -Force README.md "dist\Windows文件整理助手\README.md"
Copy-Item -Force VERSION.txt "dist\Windows文件整理助手\VERSION.txt"
```

Expected: release folder contains the EXE, `_internal\`, `file_helper.py`, `config.yaml`, `README.md`, and `VERSION.txt`.

- [ ] **Step 5: Verify packaged startup**

Run:

```powershell
Start-Process -FilePath "dist\Windows文件整理助手\Windows文件整理助手.exe"
```

Expected: app window opens. Close it manually after confirming startup.

- [ ] **Step 6: Commit packaging source changes only**

Do not commit `dist/`, `build/`, or release archives. Commit only source/config/docs/spec changes:

```powershell
git add Windows文件整理助手.spec VERSION.txt README.md launcher_gui.py launcher_core.py tests/test_launcher_core.py
git commit -m "build: support modern launcher packaging"
```

## Self-Review

- Spec coverage: The plan covers CustomTkinter UI, side navigation, industrial-clean style, settings preservation, path status, command preview, `--yes` safety, archive disabling in undo mode, documentation, and optional packaging.
- Boundary check: No task modifies `file_helper.py`; all new logic is launcher-only command/settings behavior.
- Test coverage: `tests/test_launcher_core.py` covers quoting, path cleanup, dry-run command generation, apply archive command generation, `--yes` behavior, and undo command exclusions.
- Packaging scope: Packaging is explicitly optional and requires user confirmation before rebuilding the EXE.
