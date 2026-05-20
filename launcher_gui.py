from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime
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


APP_TITLE = "Windows 文件整理助手"
LAUNCHER_OUTPUT_LOG = "launcher_run_output.log"

MODE_LABELS = {
    "dry-run": "预览模式",
    "apply": "执行整理",
    "undo-last": "撤销上次",
}


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
        for variable in (
            self.python_command,
            self.script_path,
            self.root_path,
            self.config_path,
        ):
            variable.trace_add("write", self.update_path_status)
        self.on_mode_changed()
        self.update_path_status()

    def load_settings(self) -> LauncherSettings:
        if not self.settings_path.exists():
            return self.defaults
        try:
            with self.settings_path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("设置文件顶层必须是对象。")
        except Exception as exc:
            messagebox.showwarning(
                APP_TITLE,
                f"launcher_settings.json 已损坏，已使用默认设置。\n{exc}",
            )
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
            messagebox.showinfo(APP_TITLE, "设置已保存。")

    def clear_settings(self) -> None:
        try:
            if self.settings_path.exists():
                self.settings_path.unlink()
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"清空设置失败：\n{exc}")
            return
        self.apply_settings(self.defaults)
        messagebox.showinfo(APP_TITLE, "已清空保存的路径和选项。")

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

    def _build_ui(self) -> None:
        self.root.configure(fg_color="#EEF2F4")
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self.root, fg_color="#101820", corner_radius=0, width=238)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(5, weight=1)

        ctk.CTkLabel(
            sidebar,
            text="Windows\n文件整理助手",
            text_color="#F7FAFC",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=23, weight="bold"),
            justify="left",
        ).grid(row=0, column=0, sticky="w", padx=22, pady=(28, 6))
        ctk.CTkLabel(
            sidebar,
            text="预览优先 / 确认执行 / 可撤销",
            text_color="#98A6B3",
            font=ctk.CTkFont(size=12),
        ).grid(row=1, column=0, sticky="w", padx=22, pady=(0, 28))

        self._add_mode_button(sidebar, "dry-run", "预览模式", "--dry-run，不修改文件", 2)
        self._add_mode_button(sidebar, "apply", "执行整理", "--apply，需要确认", 3)
        self._add_mode_button(sidebar, "undo-last", "撤销上次", "--undo-last", 4)

        self.safety_status = ctk.CTkLabel(
            sidebar,
            text="当前不会修改文件",
            text_color="#DFF3E8",
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.safety_status.grid(row=6, column=0, sticky="sew", padx=22, pady=(0, 26))

        workspace = ctk.CTkFrame(self.root, fg_color="#EEF2F4", corner_radius=0)
        workspace.grid(row=0, column=1, sticky="nsew", padx=26, pady=24)
        workspace.grid_columnconfigure(0, weight=1)
        workspace.grid_rowconfigure(2, weight=1)

        self._build_header(workspace)

        content = ctk.CTkFrame(workspace, fg_color="transparent")
        content.grid(row=1, column=0, sticky="nsew", pady=(18, 0))
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, minsize=310)
        content.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(content, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(4, weight=1)

        self._add_path_card(
            left,
            0,
            "Python 命令",
            self.python_command,
            None,
            r"示例：py、python、C:\Program Files\Python\python.exe",
        )
        self._add_path_card(
            left,
            1,
            "file_helper.py 路径",
            self.script_path,
            self.select_script,
            "启动器只调用这个脚本，不在界面里实现整理逻辑。",
        )
        self._add_path_card(
            left,
            2,
            "要处理的文件夹路径",
            self.root_path,
            self.select_root_folder,
            "请选择待整理的根目录。预览模式不会修改其中的文件。",
        )
        self._add_path_card(
            left,
            3,
            "config.yaml 路径",
            self.config_path,
            self.select_config,
            "可留空；撤销上次模式不使用 config.yaml。",
        )
        self._build_command_area(left)

        right = ctk.CTkFrame(content, fg_color="#FFFFFF", border_color="#D6DEE5", border_width=1, corner_radius=8)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        self._build_options_area(right)

        self._build_action_bar(workspace)

    def _build_header(self, parent: ctk.CTkFrame) -> None:
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="整理任务启动器",
            text_color="#101820",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=28, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        self.mode_badge = ctk.CTkLabel(
            header,
            text="Dry Run",
            fg_color="#DFF3E8",
            text_color="#176342",
            corner_radius=14,
            height=28,
            width=96,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.mode_badge.grid(row=0, column=1, sticky="e")
        self.mode_description = ctk.CTkLabel(
            header,
            text="先生成预览命令，确认计划后再执行真实整理。",
            text_color="#5E6B75",
            font=ctk.CTkFont(size=13),
        )
        self.mode_description.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def _add_mode_button(
        self,
        parent: ctk.CTkFrame,
        value: str,
        title: str,
        subtitle: str,
        row: int,
    ) -> None:
        button = ctk.CTkButton(
            parent,
            text=f"{title}\n{subtitle}",
            command=lambda: self.run_mode.set(value),
            anchor="w",
            height=58,
            fg_color="#1B2630",
            hover_color="#24313C",
            text_color="#DCE6EE",
            corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        button.grid(row=row, column=0, sticky="ew", padx=16, pady=(0, 10))
        self.mode_buttons[value] = button

    def _add_path_card(
        self,
        parent: ctk.CTkFrame,
        row: int,
        label: str,
        variable: ctk.StringVar,
        browse_command: object,
        hint: str,
    ) -> None:
        card = ctk.CTkFrame(
            parent,
            fg_color="#FFFFFF",
            border_color="#D6DEE5",
            border_width=1,
            corner_radius=8,
        )
        card.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        card.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(12, 6))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            top,
            text=label,
            text_color="#4F5E69",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        status = ctk.CTkLabel(top, text="", text_color="#6A7884", font=ctk.CTkFont(size=12))
        status.grid(row=0, column=1, sticky="e")
        self.path_status_labels[label] = status

        entry = ctk.CTkEntry(
            card,
            textvariable=variable,
            height=38,
            border_color="#D6DEE5",
            fg_color="#F6F8FA",
        )
        entry.grid(row=1, column=0, sticky="ew", padx=(14, 8), pady=(0, 10))
        if browse_command is not None:
            ctk.CTkButton(
                card,
                text="选择",
                command=browse_command,
                width=74,
                height=38,
                fg_color="#101820",
                hover_color="#24313C",
            ).grid(row=1, column=1, sticky="e", padx=(0, 14), pady=(0, 10))
        ctk.CTkLabel(
            card,
            text=hint,
            text_color="#7A8791",
            font=ctk.CTkFont(size=11),
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 12))

    def _build_options_area(self, parent: ctk.CTkFrame) -> None:
        ctk.CTkLabel(
            parent,
            text="模式说明",
            text_color="#101820",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(18, 8))
        self.mode_help = ctk.CTkLabel(
            parent,
            text="",
            text_color="#4F5E69",
            justify="left",
            wraplength=260,
        )
        self.mode_help.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 18))

        ctk.CTkLabel(
            parent,
            text="选项",
            text_color="#101820",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=2, column=0, sticky="w", padx=16, pady=(0, 8))
        self.archive_check = ctk.CTkCheckBox(
            parent,
            text="整理完成后压缩最终文件夹",
            variable=self.use_archive,
            fg_color="#F05A28",
            hover_color="#C84418",
        )
        self.archive_check.grid(row=3, column=0, sticky="w", padx=16, pady=(0, 10))
        ctk.CTkCheckBox(
            parent,
            text="处理完成后打开结果目录",
            variable=self.open_result_folder,
            fg_color="#F05A28",
            hover_color="#C84418",
        ).grid(row=4, column=0, sticky="w", padx=16, pady=(0, 18))

        ctk.CTkLabel(
            parent,
            text="设置",
            text_color="#101820",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=5, column=0, sticky="w", padx=16, pady=(0, 8))
        ctk.CTkLabel(
            parent,
            text="保存设置只记录路径和启动选项，不保存客户文件内容。",
            text_color="#667580",
            justify="left",
            wraplength=260,
        ).grid(row=6, column=0, sticky="ew", padx=16, pady=(0, 12))
        ctk.CTkButton(
            parent,
            text="保存设置",
            command=self.save_settings,
            fg_color="#101820",
            hover_color="#24313C",
        ).grid(row=7, column=0, sticky="ew", padx=16, pady=(0, 8))
        ctk.CTkButton(
            parent,
            text="清空设置",
            command=self.clear_settings,
            fg_color="#FFFFFF",
            text_color="#101820",
            border_color="#C7D0D8",
            border_width=1,
            hover_color="#EEF2F4",
        ).grid(row=8, column=0, sticky="ew", padx=16)

    def _build_command_area(self, parent: ctk.CTkFrame) -> None:
        card = ctk.CTkFrame(
            parent,
            fg_color="#FFFFFF",
            border_color="#D6DEE5",
            border_width=1,
            corner_radius=8,
        )
        card.grid(row=4, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)
        parent.grid_rowconfigure(4, weight=1)
        ctk.CTkLabel(
            card,
            text="命令预览",
            text_color="#101820",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 8))
        self.command_preview = ctk.CTkTextbox(
            card,
            height=150,
            wrap="word",
            fg_color="#101820",
            text_color="#DCE6EE",
            border_width=0,
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.command_preview.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 8))
        ctk.CTkLabel(
            card,
            text="普通预览和复制命令不带 --yes；只有确认执行或撤销后才追加。",
            text_color="#667580",
            font=ctk.CTkFont(size=11),
        ).grid(row=2, column=0, sticky="w", padx=14, pady=(0, 14))

    def _build_action_bar(self, parent: ctk.CTkFrame) -> None:
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            bar,
            text="生成命令",
            command=self.generate_command,
            fg_color="#FFFFFF",
            text_color="#101820",
            border_color="#C7D0D8",
            border_width=1,
            hover_color="#E2E8EE",
        ).grid(row=0, column=1, padx=(0, 10))
        ctk.CTkButton(
            bar,
            text="复制命令",
            command=self.copy_command,
            fg_color="#FFFFFF",
            text_color="#101820",
            border_color="#C7D0D8",
            border_width=1,
            hover_color="#E2E8EE",
        ).grid(row=0, column=2, padx=(0, 10))
        ctk.CTkButton(
            bar,
            text="后台运行",
            command=self.run_in_powershell,
            fg_color="#F05A28",
            hover_color="#C84418",
            width=178,
        ).grid(row=0, column=3)

    def on_mode_changed(self, *_args: object) -> None:
        mode = self.run_mode.get()
        for value, button in self.mode_buttons.items():
            if value == mode:
                button.configure(fg_color="#F05A28", hover_color="#C84418", text_color="#FFFFFF")
            else:
                button.configure(fg_color="#1B2630", hover_color="#24313C", text_color="#DCE6EE")

        if mode == "apply":
            self.mode_badge.configure(text="Apply", fg_color="#FFE7D8", text_color="#9B3417")
            self.safety_status.configure(text="真实整理需要确认", text_color="#FFE7D8")
            self.mode_description.configure(text="执行前请先查看 dry-run 结果；确认后启动器会追加一次性 --yes。")
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
            "Python 命令": (
                "已填写" if self.python_command.get().strip() else "未填写",
                "#176342" if self.python_command.get().strip() else "#9B3417",
            ),
            "file_helper.py 路径": self._path_status(
                self.script_path.get(),
                expect_file=True,
                empty_text="未选择",
            ),
            "要处理的文件夹路径": self._path_status(
                self.root_path.get(),
                expect_file=False,
                empty_text="未选择",
            ),
            "config.yaml 路径": (
                ("不使用", "#7A8791")
                if self.run_mode.get() == "undo-last"
                else self._path_status(self.config_path.get(), expect_file=True, empty_text="可留空")
            ),
        }
        for label, (text, color) in statuses.items():
            widget = self.path_status_labels.get(label)
            if widget is not None:
                widget.configure(text=text, text_color=color)

    def _path_status(self, value: str, expect_file: bool, empty_text: str) -> tuple[str, str]:
        cleaned = clean_path_value(value)
        if not cleaned:
            color = "#9B3417" if empty_text == "未选择" else "#7A8791"
            return empty_text, color
        path = Path(cleaned)
        exists = path.is_file() if expect_file else path.is_dir()
        if exists:
            return "已找到", "#176342"
        return "路径不存在", "#9B3417"

    def select_script(self) -> None:
        path = filedialog.askopenfilename(
            title="选择整理脚本 file_helper.py",
            filetypes=[("Python 脚本文件", "*.py"), ("所有文件", "*.*")],
        )
        if path:
            self.script_path.set(path)

    def select_root_folder(self) -> None:
        path = filedialog.askdirectory(title="选择要处理的文件夹")
        if path:
            self.root_path.set(path)

    def select_config(self) -> None:
        path = filedialog.askopenfilename(
            title="选择配置文件 config.yaml",
            filetypes=[("YAML 配置文件", "*.yaml *.yml"), ("所有文件", "*.*")],
        )
        if path:
            self.config_path.set(path)

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

    def generate_command(self) -> str | None:
        command = self.build_command()
        if command is None:
            return None
        self.set_preview(command)
        self.save_settings(show_message=False)
        return command

    def copy_command(self) -> None:
        command = self.generate_command()
        if not command:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(command)
        self.root.update()
        messagebox.showinfo(APP_TITLE, "命令已复制。")

    def confirm_real_run(self) -> bool:
        mode = self.run_mode.get()
        if mode == "apply":
            return messagebox.askyesno(
                "确认执行 apply",
                "即将执行真实整理操作，可能会移动、合并、重命名文件夹。\n"
                "请确认你已经看过 dry-run 结果。\n"
                "是否继续？",
            )
        if mode == "undo-last":
            return messagebox.askyesno(
                "确认撤销上次整理",
                "即将撤销最近一次 apply 记录。\n"
                "程序只会根据 organizer_run_log.json 回退，不会覆盖已有路径。\n"
                "是否继续？",
            )
        return True

    def run_in_powershell(self) -> None:
        mode = self.run_mode.get()
        settings_saved = False
        if mode in {"apply", "undo-last"}:
            if self.build_command() is None:
                return
            if not self.confirm_real_run():
                return
            command = self.build_command(include_yes=True)
            if not command:
                return
            self.set_preview(command)
        else:
            command = self.generate_command()
            settings_saved = command is not None
        if not command:
            return
        if not settings_saved:
            self.save_settings(show_message=False)
        log_path = self.base_dir / LAUNCHER_OUTPUT_LOG
        try:
            thread = threading.Thread(
                target=self.run_hidden_powershell,
                args=(command, log_path),
                daemon=True,
            )
            thread.start()
            messagebox.showinfo(
                APP_TITLE,
                f"已在后台启动，不会显示 PowerShell 窗口。\n输出日志：\n{log_path}",
            )
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"无法启动后台任务：\n{exc}")

    def run_hidden_powershell(self, command: str, log_path: Path) -> None:
        started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
                log_file.write(f"\n[{started_at}] 启动后台 PowerShell\n")
                log_file.write(f"命令：{command}\n")
                log_file.flush()
                powershell_command = (
                    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
                    "$OutputEncoding = [Console]::OutputEncoding; "
                    f"{command}"
                )
                env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
                proc = subprocess.Popen(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        powershell_command,
                    ],
                    cwd=str(self.base_dir),
                    env=env,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
                return_code = proc.wait()
                finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                log_file.write(f"\n[{finished_at}] 后台任务结束，退出码：{return_code}\n")
        except Exception as exc:
            error_message = str(exc)
            self.root.after(0, lambda: messagebox.showerror(APP_TITLE, f"后台任务运行失败：\n{error_message}"))
            return

        if return_code == 0:
            self.root.after(
                0,
                lambda: messagebox.showinfo(
                    APP_TITLE,
                    f"后台任务已完成。\n输出日志：\n{log_path}",
                ),
            )
        else:
            self.root.after(
                0,
                lambda: messagebox.showerror(
                    APP_TITLE,
                    f"后台任务失败，退出码：{return_code}\n请查看日志：\n{log_path}",
                ),
            )


def main() -> None:
    root = ctk.CTk()
    LauncherGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
