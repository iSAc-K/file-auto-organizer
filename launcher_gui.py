from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, simpledialog
from tkinter import ttk

import customtkinter as ctk

from config_manager import (
    ConfigConflictError,
    load_user_config,
    merge_user_config,
    parse_batch_keywords,
    save_user_config,
)
from file_helper import load_raw_config, validate_config
from update_manager import (
    DownloadProgress,
    UpdateCancelled,
    download_update,
    fetch_update_info_with_retry,
    is_newer_version,
)
from launcher_core import (
    PREVIEW_COLUMN_WIDTHS,
    SETTINGS_NAME,
    LauncherSettings,
    OperationGate,
    app_base_dir,
    build_command,
    build_preview_rows,
    build_safety_status_text,
    build_update_progress_text,
    build_update_status_text,
    can_close_update_window,
    clean_path_value,
    default_window_geometry,
    default_settings,
    find_latest_report,
    load_settings as core_load_settings,
    preview_expanded_width,
    read_version,
    save_settings as core_save_settings,
    toggle_preview_column,
    undo_log_status,
    validate_paths,
    wheel_delta_to_units,
)


APP_TITLE = "Windows 文件整理助手 v2.4.4"
LAUNCHER_OUTPUT_LOG = "launcher_run_output.log"
UPDATE_CHECK_LOG = "update_check.log"

MODE_LABELS = {
    "dry-run": "预览模式",
    "apply": "执行整理",
    "undo-last": "撤销上次",
}


class LauncherGui:
    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.minsize(1280, 720)
        self.root.geometry(default_window_geometry(self.root.winfo_screenwidth(), self.root.winfo_screenheight()))
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self.base_dir = app_base_dir()
        self.settings_path = self.base_dir / SETTINGS_NAME
        self.defaults = default_settings(self.base_dir)
        self.version = read_version(self.base_dir) or "2.4.4"
        settings = self.load_settings()
        self._initializing = True

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
        self.preview_table: ttk.Treeview | None = None
        self.preview_expanded_columns: set[str] = set()
        self.main_canvas: tk.Canvas | None = None
        self.scroll_container: tk.Frame | None = None
        self.scroll_window_id: int | None = None
        self.main_scroll_active = False
        self.active_page = "tasks"
        self.config_dirty = False
        self.config_entries: dict[str, dict[str, object]] = {}
        self.config_order: list[str] = []
        self.selected_config_category = ""
        self.config_keyword_widgets: list[ctk.CTkFrame] = []
        self.operation_gate = OperationGate()
        self.update_window: ctk.CTkToplevel | None = None
        self.update_status_label: ctk.CTkLabel | None = None
        self.update_action_button: ctk.CTkButton | None = None
        self.update_progress_bar: ctk.CTkProgressBar | None = None
        self.update_percent_label: ctk.CTkLabel | None = None
        self.update_downloaded_label: ctk.CTkLabel | None = None
        self.update_speed_label: ctk.CTkLabel | None = None
        self.update_remaining_label: ctk.CTkLabel | None = None
        self.update_stage_labels: list[ctk.CTkLabel] = []
        self.update_status = "latest"
        self.update_latest_version = ""
        self.update_cancel_event: threading.Event | None = None
        self.pending_update_progress: DownloadProgress | None = None
        self.pending_update_results: list[tuple[str, str]] = []
        self.last_progress_phase = ""
        self.last_progress_refresh = 0.0
        self.update_overlay: ctk.CTkFrame | None = None
        self.manual_update_info: object | None = None
        self.manual_update_check_running = False

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.run_mode.trace_add("write", self.on_mode_changed)
        self.use_archive.trace_add("write", self.on_option_changed)
        self.open_result_folder.trace_add("write", self.on_option_changed)
        for variable in (
            self.python_command,
            self.script_path,
            self.root_path,
            self.config_path,
        ):
            variable.trace_add("write", self.on_path_changed)
        self.on_mode_changed()
        self.update_path_status()
        self._initializing = False
        self.root.after(1200, self.check_for_updates_async)

    def load_settings(self) -> LauncherSettings:
        return core_load_settings(self.settings_path, self.defaults)

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
            core_save_settings(self.settings_path, self.current_settings())
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
        self.clear_preview_table()
        self.update_path_status()

    def _build_ui(self) -> None:
        self.root.configure(fg_color="#EEF2F4")
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=0)

        body = ctk.CTkFrame(self.root, fg_color="#EEF2F4", corner_radius=0)
        self.body = body
        body.grid(row=0, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=0)
        body.grid_columnconfigure(1, weight=1)
        body.grid_columnconfigure(2, weight=0)
        body.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(body, fg_color="#101820", corner_radius=0, width=238)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(6, weight=1)

        ctk.CTkLabel(
            sidebar,
            text="Windows\n文件整理助手",
            text_color="#F7FAFC",
            font=ctk.CTkFont(family="Microsoft YaHei UI", size=23, weight="bold"),
            justify="left",
        ).grid(row=0, column=0, sticky="w", padx=22, pady=(28, 6))
        ctk.CTkLabel(
            sidebar,
            text=f"v{self.version} / 预览优先 / 确认执行 / 可撤销",
            text_color="#98A6B3",
            font=ctk.CTkFont(size=12),
        ).grid(row=1, column=0, sticky="w", padx=22, pady=(0, 28))

        self._add_mode_button(sidebar, "dry-run", "预览模式", "--dry-run，不修改文件", 2)
        self._add_mode_button(sidebar, "apply", "执行整理", "--apply，需要确认", 3)
        self._add_mode_button(sidebar, "undo-last", "撤销上次", "--undo-last", 4)
        self.config_nav_button = ctk.CTkButton(
            sidebar,
            text="配置管理\n品类、关键词与排序",
            command=self.show_config_page,
            anchor="w",
            height=58,
            fg_color="#1B2630",
            hover_color="#24313C",
            text_color="#DCE6EE",
            corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.config_nav_button.grid(row=5, column=0, sticky="ew", padx=16, pady=(0, 10))

        self.safety_status = ctk.CTkLabel(
            sidebar,
            text=build_safety_status_text("dry-run", False),
            text_color="#DFF3E8",
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
            wraplength=190,
            justify="left",
        )
        self.safety_status.grid(row=6, column=0, sticky="sew", padx=22, pady=(0, 26))
        self.update_nav_button = ctk.CTkButton(
            sidebar,
            text="检查更新\n查看版本与更新状态",
            command=self.open_update_window,
            anchor="w",
            height=58,
            fg_color="#1B2630",
            hover_color="#24313C",
            text_color="#DCE6EE",
            corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.update_nav_button.grid(row=7, column=0, sticky="ew", padx=16, pady=(0, 18))

        center_shell = ctk.CTkFrame(body, fg_color="#EEF2F4", corner_radius=0)
        self.task_center = center_shell
        center_shell.grid(row=0, column=1, sticky="nsew", padx=26, pady=(22, 16))
        center_shell.grid_columnconfigure(0, weight=1)
        center_shell.grid_rowconfigure(0, weight=0)
        center_shell.grid_rowconfigure(1, weight=1)

        self._build_header(center_shell)

        scroll_container = tk.Frame(center_shell, bg="#EEF2F4")
        self.scroll_container = scroll_container
        scroll_container.grid(row=1, column=0, sticky="nsew", pady=(18, 0))
        scroll_container.grid_columnconfigure(0, weight=1)
        scroll_container.grid_rowconfigure(0, weight=1)
        scroll_container.bind("<Enter>", lambda _event: self.set_main_scroll_active(True))
        scroll_container.bind("<Leave>", lambda _event: self.set_main_scroll_active(False))

        self.main_canvas = tk.Canvas(
            scroll_container,
            bg="#EEF2F4",
            highlightthickness=0,
            borderwidth=0,
            yscrollincrement=24,
        )
        main_scrollbar = ttk.Scrollbar(scroll_container, orient="vertical", command=self.main_canvas.yview)
        self.main_canvas.configure(yscrollcommand=main_scrollbar.set)
        self.main_canvas.grid(row=0, column=0, sticky="nsew")
        main_scrollbar.grid(row=0, column=1, sticky="ns")

        scrollable_frame = ctk.CTkFrame(self.main_canvas, fg_color="#EEF2F4", corner_radius=0)
        self.scroll_window_id = self.main_canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        scrollable_frame.bind("<Configure>", self.on_scroll_frame_configure)
        scrollable_frame.bind("<Enter>", lambda _event: self.set_main_scroll_active(True))
        scrollable_frame.bind("<Leave>", lambda _event: self.set_main_scroll_active(False))
        self.main_canvas.bind("<Configure>", self.on_main_canvas_configure)
        self.main_canvas.bind_all("<MouseWheel>", self.on_main_mousewheel)
        self.main_canvas.bind_all("<Button-4>", self.on_linux_scroll_up)
        self.main_canvas.bind_all("<Button-5>", self.on_linux_scroll_down)

        scrollable_frame.grid_columnconfigure(0, weight=1)

        left = ctk.CTkFrame(scrollable_frame, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14), pady=(0, 18))
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

        right = ctk.CTkFrame(body, fg_color="#FFFFFF", border_color="#D6DEE5", border_width=1, corner_radius=8, width=310)
        self.task_right = right
        right.grid(row=0, column=2, sticky="nsew", padx=(0, 24), pady=(22, 16))
        right.grid_propagate(False)
        right.grid_columnconfigure(0, weight=1)
        self._build_options_area(right)

        self._build_action_bar(self.root)
        self._build_config_page(body)

    def _build_header(self, parent: ctk.CTkFrame) -> None:
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text=f"整理任务启动器 v{self.version}",
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
            command=lambda: self.show_task_page(value),
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

    def show_task_page(self, mode: str) -> None:
        if self.active_page == "config" and not self.confirm_discard_config_changes():
            return
        self.active_page = "tasks"
        self.config_page.grid_remove()
        self.task_center.grid()
        self.task_right.grid()
        self.action_bar.grid()
        self.config_nav_button.configure(fg_color="#1B2630", text_color="#DCE6EE")
        self.run_mode.set(mode)

    def show_config_page(self) -> None:
        if self.active_page == "config":
            return
        self.active_page = "config"
        self.task_center.grid_remove()
        self.task_right.grid_remove()
        self.action_bar.grid_remove()
        self.config_page.grid()
        self.config_nav_button.configure(fg_color="#F05A28", text_color="#FFFFFF")
        for button in self.mode_buttons.values():
            button.configure(fg_color="#1B2630", text_color="#DCE6EE")
        self.load_config_editor()

    def _build_config_page(self, parent: ctk.CTkFrame) -> None:
        page = ctk.CTkFrame(parent, fg_color="#EEF2F4", corner_radius=0)
        self.config_page = page
        page.grid(row=0, column=1, columnspan=2, sticky="nsew", padx=(26, 24), pady=(22, 16))
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        page.grid_remove()

        header = ctk.CTkFrame(page, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text="配置管理", text_color="#101820",
            font=ctk.CTkFont(size=28, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        self.config_status = ctk.CTkLabel(header, text="未修改", text_color="#6A7884")
        self.config_status.grid(row=0, column=1, padx=12)
        ctk.CTkButton(header, text="保存配置", command=self.save_config_editor, fg_color="#F05A28").grid(row=0, column=2)

        content = ctk.CTkFrame(page, fg_color="transparent")
        content.grid(row=1, column=0, sticky="nsew")
        content.grid_columnconfigure(0, weight=0, minsize=330)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(content, fg_color="#FFFFFF", border_width=1, border_color="#D6DEE5")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(left, text="+ 新增品类", command=self.add_config_category, fg_color="#101820").grid(
            row=0, column=0, sticky="ew", padx=12, pady=12
        )
        self.config_category_list = tk.Listbox(
            left, font=("Microsoft YaHei UI", 12), activestyle="none",
            selectbackground="#F05A28", borderwidth=0, highlightthickness=0,
        )
        self.config_category_list.grid(row=1, column=0, sticky="nsew", padx=12)
        self.config_category_list.bind("<<ListboxSelect>>", self.on_config_category_selected)
        self.config_category_list.bind("<ButtonPress-1>", self.on_category_drag_start)
        self.config_category_list.bind("<B1-Motion>", self.on_category_drag_motion)
        order_buttons = ctk.CTkFrame(left, fg_color="transparent")
        order_buttons.grid(row=2, column=0, sticky="ew", padx=12, pady=12)
        ctk.CTkButton(order_buttons, text="上移", width=88, command=lambda: self.move_config_category(-1)).pack(side="left")
        ctk.CTkButton(order_buttons, text="下移", width=88, command=lambda: self.move_config_category(1)).pack(side="left", padx=8)
        ctk.CTkButton(order_buttons, text="删除", width=88, fg_color="#7B8790", command=self.delete_config_category).pack(side="right")

        detail = ctk.CTkFrame(content, fg_color="#FFFFFF", border_width=1, border_color="#D6DEE5")
        detail.grid(row=0, column=1, sticky="nsew")
        detail.grid_columnconfigure(0, weight=1)
        detail.grid_rowconfigure(4, weight=1)
        self.config_category_title = ctk.CTkLabel(detail, text="请选择品类", font=ctk.CTkFont(size=20, weight="bold"))
        self.config_category_title.grid(row=0, column=0, sticky="w", padx=18, pady=(18, 8))
        switches = ctk.CTkFrame(detail, fg_color="transparent")
        switches.grid(row=1, column=0, sticky="ew", padx=18)
        self.config_enabled_var = ctk.BooleanVar(value=True)
        self.config_merge_var = ctk.BooleanVar(value=True)
        self.config_enabled_switch = ctk.CTkSwitch(
            switches, text="启用品类", variable=self.config_enabled_var, command=self.update_selected_category_flags,
            progress_color="#00B978",
        )
        self.config_enabled_switch.pack(side="left")
        self.config_merge_switch = ctk.CTkSwitch(
            switches, text="允许同品类合并", variable=self.config_merge_var, command=self.update_selected_category_flags,
            progress_color="#00B978",
        )
        self.config_merge_switch.pack(side="left", padx=28)

        add_row = ctk.CTkFrame(detail, fg_color="transparent")
        add_row.grid(row=2, column=0, sticky="ew", padx=18, pady=(16, 8))
        add_row.grid_columnconfigure(0, weight=1)
        self.keyword_entry = ctk.CTkEntry(add_row, placeholder_text="输入一个关键词")
        self.keyword_entry.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(add_row, text="添加关键词", width=110, command=self.add_single_keyword).grid(row=0, column=1, padx=(8, 0))

        batch = ctk.CTkFrame(detail, fg_color="transparent")
        batch.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 8))
        batch.grid_columnconfigure(0, weight=1)
        self.keyword_batch = ctk.CTkTextbox(batch, height=72)
        self.keyword_batch.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(batch, text="批量导入", width=110, command=self.add_batch_keywords).grid(row=0, column=1, padx=(8, 0))

        self.keyword_scroll = ctk.CTkScrollableFrame(detail, label_text="关键词（绿色启用，灰色停用）")
        self.keyword_scroll.grid(row=4, column=0, sticky="nsew", padx=18, pady=(8, 18))
        self.keyword_scroll.grid_columnconfigure(0, weight=1)

    def load_config_editor(self) -> None:
        official_path = self.base_dir / "config.default.yaml"
        user_path = self.base_dir / "user_config.yaml"
        try:
            official = load_raw_config(official_path)
            user = load_user_config(user_path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"读取配置失败：\n{exc}")
            return
        self.config_entries = {}
        official_categories = official.get("categories", {}) or {}
        user_categories = user.get("categories", {}) or {}
        for name, data in official_categories.items():
            override = user_categories.get(name, {}) or {}
            disabled = {str(item).casefold() for item in override.get("disabled_keywords", []) or []}
            keywords = [
                {"text": str(word), "official": True, "enabled": str(word).casefold() not in disabled}
                for word in data.get("keywords", []) or []
            ]
            keywords.extend(
                {"text": str(word), "official": False, "enabled": True}
                for word in override.get("added_keywords", []) or []
            )
            self.config_entries[str(name)] = {
                "official": True,
                "enabled": bool(override.get("enabled", True)),
                "merge_enabled": bool(override.get("merge_enabled", data.get("merge_enabled", False))),
                "keywords": keywords,
            }
        for name, override in user_categories.items():
            if name in official_categories or not override.get("custom", False):
                continue
            disabled = {str(item).casefold() for item in override.get("disabled_keywords", []) or []}
            self.config_entries[str(name)] = {
                "official": False,
                "enabled": bool(override.get("enabled", True)),
                "merge_enabled": bool(override.get("merge_enabled", True)),
                "keywords": [
                    {"text": str(word), "official": False, "enabled": str(word).casefold() not in disabled}
                    for word in override.get("keywords", []) or []
                ],
            }
        requested = [str(name) for name in user.get("category_order", []) or []]
        defaults = [str(name) for name in official.get("category_priority", []) or []]
        self.config_order = []
        for name in requested + defaults + list(self.config_entries):
            if name in self.config_entries and name not in self.config_order:
                self.config_order.append(name)
        self.config_dirty = False
        self.config_status.configure(text="未修改", text_color="#6A7884")
        self.refresh_config_category_list(select_name=self.config_order[0] if self.config_order else "")

    def mark_config_dirty(self) -> None:
        self.config_dirty = True
        self.config_status.configure(text="有未保存修改", text_color="#9B3417")

    def refresh_config_category_list(self, select_name: str = "") -> None:
        self.config_category_list.delete(0, "end")
        for index, name in enumerate(self.config_order):
            entry = self.config_entries[name]
            source = "官方" if entry["official"] else "自定义"
            state = "" if entry["enabled"] else "（已停用）"
            self.config_category_list.insert("end", f"{name}  [{source}]{state}")
            if not entry["enabled"]:
                self.config_category_list.itemconfig(index, fg="#9AA3AA")
        if select_name in self.config_order:
            index = self.config_order.index(select_name)
            self.config_category_list.selection_set(index)
            self.config_category_list.activate(index)
            self.selected_config_category = select_name
            self.refresh_config_detail()

    def on_config_category_selected(self, _event: object = None) -> None:
        selection = self.config_category_list.curselection()
        if not selection:
            return
        self.selected_config_category = self.config_order[int(selection[0])]
        self.refresh_config_detail()

    def refresh_config_detail(self) -> None:
        name = self.selected_config_category
        if name not in self.config_entries:
            return
        entry = self.config_entries[name]
        self.config_category_title.configure(text=f"{name}  ·  {'官方' if entry['official'] else '自定义'}")
        self.config_enabled_var.set(bool(entry["enabled"]))
        self.config_merge_var.set(bool(entry["merge_enabled"]))
        for widget in self.config_keyword_widgets:
            widget.destroy()
        self.config_keyword_widgets.clear()
        for index, keyword in enumerate(entry["keywords"]):  # type: ignore[union-attr]
            row = ctk.CTkFrame(self.keyword_scroll, fg_color="#F4F6F8" if keyword["enabled"] else "#E4E7E9")
            row.grid(row=index, column=0, sticky="ew", pady=(0, 6))
            row.grid_columnconfigure(0, weight=1)
            label = ctk.CTkLabel(
                row, text=str(keyword["text"]), anchor="w",
                text_color="#101820" if keyword["enabled"] else "#8B949B",
            )
            label.grid(row=0, column=0, sticky="ew", padx=10, pady=8)
            var = ctk.BooleanVar(value=bool(keyword["enabled"]))
            ctk.CTkSwitch(
                row, text="", width=46, variable=var, progress_color="#00B978",
                command=lambda i=index, v=var: self.toggle_keyword(i, v.get()),
            ).grid(row=0, column=1, padx=8)
            if not keyword["official"]:
                ctk.CTkButton(
                    row, text="删除", width=58, fg_color="#7B8790",
                    command=lambda i=index: self.delete_keyword(i),
                ).grid(row=0, column=2, padx=(0, 8))
            self.config_keyword_widgets.append(row)

    def update_selected_category_flags(self) -> None:
        if self.selected_config_category not in self.config_entries:
            return
        entry = self.config_entries[self.selected_config_category]
        entry["enabled"] = bool(self.config_enabled_var.get())
        entry["merge_enabled"] = bool(self.config_merge_var.get())
        self.mark_config_dirty()
        self.refresh_config_category_list(self.selected_config_category)

    def add_config_category(self) -> None:
        name = simpledialog.askstring("新增品类", "请输入品类名称：", parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name or name in self.config_entries:
            messagebox.showerror(APP_TITLE, "品类名称为空或已经存在。")
            return
        self.config_entries[name] = {
            "official": False, "enabled": True, "merge_enabled": True, "keywords": []
        }
        self.config_order.append(name)
        self.mark_config_dirty()
        self.refresh_config_category_list(name)

    def delete_config_category(self) -> None:
        name = self.selected_config_category
        if not name:
            return
        if bool(self.config_entries[name]["official"]):
            messagebox.showinfo(APP_TITLE, "官方品类不能删除，可以使用启用开关将其停用。")
            return
        del self.config_entries[name]
        self.config_order.remove(name)
        self.selected_config_category = ""
        self.mark_config_dirty()
        self.refresh_config_category_list(self.config_order[0] if self.config_order else "")

    def move_config_category(self, offset: int) -> None:
        name = self.selected_config_category
        if name not in self.config_order:
            return
        old = self.config_order.index(name)
        new = max(0, min(len(self.config_order) - 1, old + offset))
        if new == old:
            return
        self.config_order.insert(new, self.config_order.pop(old))
        self.mark_config_dirty()
        self.refresh_config_category_list(name)

    def on_category_drag_start(self, event: tk.Event) -> None:
        self.category_drag_index = self.config_category_list.nearest(event.y)

    def on_category_drag_motion(self, event: tk.Event) -> None:
        if not hasattr(self, "category_drag_index") or not self.config_order:
            return
        target = self.config_category_list.nearest(event.y)
        source = int(self.category_drag_index)
        if target == source or target < 0 or target >= len(self.config_order):
            return
        name = self.config_order.pop(source)
        self.config_order.insert(target, name)
        self.category_drag_index = target
        self.mark_config_dirty()
        self.refresh_config_category_list(name)

    def _append_keywords(self, keywords: list[str]) -> None:
        name = self.selected_config_category
        if name not in self.config_entries:
            return
        rows = self.config_entries[name]["keywords"]  # type: ignore[index]
        existing = {str(item["text"]).casefold() for item in rows}
        for keyword in keywords:
            if keyword.casefold() not in existing:
                rows.append({"text": keyword, "official": False, "enabled": True})
                existing.add(keyword.casefold())
        self.mark_config_dirty()
        self.refresh_config_detail()

    def add_single_keyword(self) -> None:
        value = self.keyword_entry.get().strip()
        if value:
            self._append_keywords([value])
            self.keyword_entry.delete(0, "end")

    def add_batch_keywords(self) -> None:
        values = parse_batch_keywords(self.keyword_batch.get("1.0", "end"))
        if values:
            self._append_keywords(values)
            self.keyword_batch.delete("1.0", "end")

    def toggle_keyword(self, index: int, enabled: bool) -> None:
        rows = self.config_entries[self.selected_config_category]["keywords"]  # type: ignore[index]
        rows[index]["enabled"] = bool(enabled)
        self.mark_config_dirty()
        self.refresh_config_detail()

    def delete_keyword(self, index: int) -> None:
        rows = self.config_entries[self.selected_config_category]["keywords"]  # type: ignore[index]
        if rows[index]["official"]:
            return
        rows.pop(index)
        self.mark_config_dirty()
        self.refresh_config_detail()

    def build_user_config_from_editor(self) -> dict[str, object]:
        categories: dict[str, object] = {}
        for name in self.config_order:
            entry = self.config_entries[name]
            keywords = entry["keywords"]  # type: ignore[assignment]
            if entry["official"]:
                categories[name] = {
                    "enabled": bool(entry["enabled"]),
                    "merge_enabled": bool(entry["merge_enabled"]),
                    "added_keywords": [item["text"] for item in keywords if not item["official"]],
                    "disabled_keywords": [item["text"] for item in keywords if item["official"] and not item["enabled"]],
                }
            else:
                categories[name] = {
                    "custom": True,
                    "enabled": bool(entry["enabled"]),
                    "merge_enabled": bool(entry["merge_enabled"]),
                    "keywords": [item["text"] for item in keywords],
                    "disabled_keywords": [item["text"] for item in keywords if not item["enabled"]],
                }
        return {"version": 1, "category_order": list(self.config_order), "categories": categories}

    def save_config_editor(self) -> bool:
        official_path = self.base_dir / "config.default.yaml"
        user_path = self.base_dir / "user_config.yaml"
        try:
            official = load_raw_config(official_path)
            user = self.build_user_config_from_editor()
            effective = merge_user_config(official, user)
            validate_config(effective)
            save_user_config(user_path, user)
        except (OSError, ValueError, ConfigConflictError) as exc:
            messagebox.showerror(APP_TITLE, f"配置无法保存：\n{exc}")
            self.config_status.configure(text="存在冲突或错误", text_color="#9B3417")
            return False
        self.config_dirty = False
        self.config_status.configure(text="已保存", text_color="#176342")
        messagebox.showinfo(APP_TITLE, f"用户配置已保存：\n{user_path}")
        return True

    def confirm_discard_config_changes(self) -> bool:
        if not self.config_dirty:
            return True
        answer = messagebox.askyesnocancel(APP_TITLE, "配置有未保存修改。\n\n是：保存\n否：放弃\n取消：继续编辑")
        if answer is None:
            return False
        if answer:
            return self.save_config_editor()
        self.config_dirty = False
        return True

    def on_close(self) -> None:
        if self.update_status in {"downloading", "verifying", "preparing_install", "updater_started"}:
            self._raise_update_window()
            return
        if self.active_page == "config" and not self.confirm_discard_config_changes():
            return
        self.root.destroy()

    def check_for_updates_async(self) -> None:
        threading.Thread(target=self._check_for_updates_worker, daemon=True).start()

    def _log_update_check_failure(self, exc: Exception) -> None:
        log_path = self.base_dir / UPDATE_CHECK_LOG
        checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(f"[{checked_at}] 检查更新失败：{type(exc).__name__}: {exc}\n")
        except OSError:
            pass

    def _check_for_updates_worker(self) -> None:
        try:
            info = fetch_update_info_with_retry()
            if is_newer_version(info.version, self.version):
                self.root.after(0, lambda: self.offer_update(info))
        except Exception as exc:
            self._log_update_check_failure(exc)

    def open_update_window(self, start_check: bool = True) -> None:
        if self.update_window is not None and self.update_window.winfo_exists():
            self._raise_update_window()
            return

        window = ctk.CTkToplevel(self.root)
        self.update_window = window
        window.title("检查更新")
        window.geometry("560x500")
        window.resizable(False, False)
        window.transient(self.root)
        window.configure(fg_color="#EEF2F4")
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(2, weight=1)
        window.protocol("WM_DELETE_WINDOW", self.close_update_window)

        heading = ctk.CTkFrame(window, fg_color="transparent")
        heading.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 12))
        heading.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            heading, text="软件更新", text_color="#101820",
            font=ctk.CTkFont(size=24, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            heading, text=f"当前版本  v{self.version}", text_color="#6A7884",
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=1, sticky="e")

        stages = ctk.CTkFrame(window, fg_color="transparent")
        stages.grid(row=1, column=0, sticky="ew", padx=28, pady=(0, 12))
        for column in range(4):
            stages.grid_columnconfigure(column, weight=1)
        self.update_stage_labels = []
        for column, text in enumerate(("下载更新", "校验文件", "准备安装", "安装更新")):
            label = ctk.CTkLabel(
                stages,
                text=text,
                text_color="#7B8790",
                fg_color="#E2E7EA",
                corner_radius=6,
                height=28,
                font=ctk.CTkFont(size=12, weight="bold"),
            )
            label.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 4, 0))
            self.update_stage_labels.append(label)

        status_card = ctk.CTkFrame(
            window,
            fg_color="#FFFFFF",
            border_color="#D6DEE5",
            border_width=1,
            corner_radius=10,
        )
        status_card.grid(row=2, column=0, sticky="nsew", padx=28, pady=(0, 16))
        status_card.grid_columnconfigure(0, weight=1)
        status_card.grid_columnconfigure(1, weight=0)
        self.update_status_label = ctk.CTkLabel(
            status_card,
            text="",
            text_color="#33414C",
            justify="left",
            anchor="nw",
            wraplength=440,
            font=ctk.CTkFont(size=13),
        )
        self.update_status_label.grid(row=0, column=0, columnspan=2, sticky="ew", padx=22, pady=(18, 10))
        self.update_progress_bar = ctk.CTkProgressBar(
            status_card,
            height=12,
            progress_color="#F05A28",
            fg_color="#DDE3E7",
        )
        self.update_progress_bar.grid(row=1, column=0, sticky="ew", padx=(22, 10), pady=(0, 8))
        self.update_progress_bar.set(0)
        self.update_percent_label = ctk.CTkLabel(
            status_card, text="0%", width=54, anchor="e",
            text_color="#101820", font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.update_percent_label.grid(row=1, column=1, sticky="e", padx=(0, 22), pady=(0, 8))

        metrics = ctk.CTkFrame(status_card, fg_color="#F5F7F8", corner_radius=8)
        metrics.grid(row=2, column=0, columnspan=2, sticky="ew", padx=22, pady=(0, 16))
        for column in range(3):
            metrics.grid_columnconfigure(column, weight=1)
        self.update_downloaded_label = self._build_update_metric(metrics, 0, "已下载", "0 B")
        self.update_speed_label = self._build_update_metric(metrics, 1, "平均速度", "0 B/s")
        self.update_remaining_label = self._build_update_metric(metrics, 2, "剩余时间", "计算中")

        self.update_action_button = ctk.CTkButton(
            window,
            text="正在检查…",
            state="disabled",
            command=self.start_manual_update_check,
            height=42,
            fg_color="#F05A28",
            hover_color="#D94C1D",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.update_action_button.grid(row=3, column=0, sticky="ew", padx=28, pady=(0, 26))
        window.after(50, window.lift)
        window.after(250, self._poll_update_progress)
        if start_check:
            self.start_manual_update_check()

    def _build_update_metric(
        self,
        parent: ctk.CTkFrame,
        column: int,
        title: str,
        value: str,
    ) -> ctk.CTkLabel:
        cell = ctk.CTkFrame(parent, fg_color="transparent")
        cell.grid(row=0, column=column, sticky="ew", padx=10, pady=10)
        ctk.CTkLabel(
            cell, text=title, text_color="#7B8790", font=ctk.CTkFont(size=11),
        ).pack(anchor="w")
        label = ctk.CTkLabel(
            cell, text=value, text_color="#33414C",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        label.pack(anchor="w", pady=(2, 0))
        return label

    def _raise_update_window(self) -> None:
        if self._update_window_is_open():
            self.update_window.lift()
            self.update_window.focus_force()

    def close_update_window(self) -> None:
        if not can_close_update_window(self.update_status):  # type: ignore[arg-type]
            self._raise_update_window()
            return
        if self.update_window is not None:
            self.update_window.destroy()
        self.update_window = None
        self.update_status_label = None
        self.update_action_button = None
        self.update_progress_bar = None
        self.update_percent_label = None
        self.update_downloaded_label = None
        self.update_speed_label = None
        self.update_remaining_label = None
        self.update_stage_labels = []
        self.manual_update_info = None

    def _update_window_is_open(self) -> bool:
        return self.update_window is not None and bool(self.update_window.winfo_exists())

    def _set_update_window_state(
        self,
        status: str,
        latest_version: str = "",
        notes: list[str] | None = None,
        error: str = "",
    ) -> None:
        self.update_status = status
        if latest_version:
            self.update_latest_version = latest_version
        if not self._update_window_is_open() or self.update_status_label is None or self.update_action_button is None:
            return
        self.update_status_label.configure(
            text=build_update_status_text(  # type: ignore[arg-type]
                status,
                self.version,
                latest_version,
                notes,
                error,
            )
        )
        self._update_stage_colors(status)
        if status == "checking":
            self.update_action_button.configure(text="正在检查…", state="disabled", command=lambda: None)
        elif status == "available":
            self.update_action_button.configure(
                text="立即更新",
                state="normal",
                command=self.start_update_download,
            )
        elif status in {"downloading", "verifying"}:
            self.update_action_button.configure(
                text="停止更新",
                state="normal",
                command=self.stop_update_download,
            )
        elif status in {"preparing_install", "updater_started"}:
            self.update_action_button.configure(
                text="正在安装…",
                state="disabled",
                command=lambda: None,
            )
        elif status in {"cancelled", "failed"}:
            self.update_action_button.configure(
                text="重新开始",
                state="normal",
                command=self.start_update_download,
            )
        else:
            self.update_action_button.configure(
                text="重新检查",
                state="normal",
                command=self.start_manual_update_check,
            )

    def _update_stage_colors(self, status: str) -> None:
        current_by_status = {
            "checking": 0,
            "available": 0,
            "downloading": 0,
            "verifying": 1,
            "preparing_install": 2,
            "updater_started": 3,
        }
        current = current_by_status.get(status, 0)
        for index, label in enumerate(self.update_stage_labels):
            if index < current:
                color = "#176342"
            elif index == current:
                color = "#F05A28"
            else:
                color = "#7B8790"
            label.configure(text_color=color)

    def _receive_update_progress(self, progress: DownloadProgress) -> None:
        self.pending_update_progress = progress

    def _poll_update_progress(self) -> None:
        if not self._update_window_is_open():
            return
        if self.pending_update_results:
            kind, detail = self.pending_update_results.pop(0)
            if kind == "preparing":
                self._set_update_window_state("preparing_install", detail)
            elif kind == "cancelled":
                self._finish_update_cancelled()
                if self._update_window_is_open():
                    self.update_window.after(250, self._poll_update_progress)
                return
            elif kind == "failed":
                self._finish_update_failed(detail)
                if self._update_window_is_open():
                    self.update_window.after(250, self._poll_update_progress)
                return
            elif kind == "started":
                self._finish_updater_started(detail)
                return
        progress = self.pending_update_progress
        if progress is not None:
            now = time.monotonic()
            phase_changed = progress.phase != self.last_progress_phase
            if phase_changed or now - self.last_progress_refresh >= 1.0:
                self._render_update_progress(progress)
                self.last_progress_phase = progress.phase
                self.last_progress_refresh = now
        if self._update_window_is_open():
            self.update_window.after(250, self._poll_update_progress)

    def _render_update_progress(self, progress: DownloadProgress) -> None:
        if progress.phase == "downloading" and self.update_status != "downloading":
            self._set_update_window_state("downloading", self.update_latest_version)
        elif progress.phase in {"verifying", "verified"} and self.update_status == "downloading":
            self._set_update_window_state("verifying", self.update_latest_version)
        text = build_update_progress_text(progress)
        if self.update_progress_bar is not None:
            self.update_progress_bar.stop()
            self.update_progress_bar.configure(mode="indeterminate" if text.indeterminate else "determinate")
            if text.indeterminate:
                self.update_progress_bar.start()
            else:
                self.update_progress_bar.set(text.value)
        if self.update_percent_label is not None:
            self.update_percent_label.configure(text=text.percent)
        if self.update_downloaded_label is not None:
            self.update_downloaded_label.configure(text=text.downloaded)
        if self.update_speed_label is not None:
            self.update_speed_label.configure(text=text.speed)
        if self.update_remaining_label is not None:
            self.update_remaining_label.configure(text=text.remaining)

    def start_manual_update_check(self) -> None:
        if self.update_status in {"downloading", "verifying", "preparing_install", "updater_started"}:
            return
        if self.manual_update_check_running:
            return
        self.manual_update_check_running = True
        self.manual_update_info = None
        self._set_update_window_state("checking")
        threading.Thread(target=self._manual_update_check_worker, daemon=True).start()

    def _manual_update_check_worker(self) -> None:
        try:
            info = fetch_update_info_with_retry()
            self.root.after(0, lambda: self._finish_manual_update_check(info))
        except Exception as exc:
            self._log_update_check_failure(exc)
            message = f"{type(exc).__name__}: {exc}"
            self.root.after(0, lambda: self._finish_manual_update_check_failure(message))

    def _finish_manual_update_check(self, info: object) -> None:
        self.manual_update_check_running = False
        if self.update_status in {"downloading", "verifying", "preparing_install", "updater_started"}:
            return
        latest_version = str(getattr(info, "version"))
        if is_newer_version(latest_version, self.version):
            self.manual_update_info = info
            self._set_update_window_state(
                "available",
                latest_version,
                list(getattr(info, "notes")),
            )
        else:
            self._set_update_window_state("latest", latest_version)

    def _finish_manual_update_check_failure(self, message: str) -> None:
        self.manual_update_check_running = False
        if self.update_status in {"downloading", "verifying", "preparing_install", "updater_started"}:
            return
        self._set_update_window_state("failed", error=message)

    def start_manual_update(self) -> None:
        self.start_update_download()

    def start_update_download(self) -> None:
        info = self.manual_update_info
        if info is None:
            self.start_manual_update_check()
            return
        if not self.operation_gate.begin_update():
            self._set_update_window_state(
                "failed",
                error="整理任务或其他更新正在运行，请等待完成后再试。",
            )
            return
        self.update_cancel_event = threading.Event()
        self.pending_update_progress = None
        self.pending_update_results.clear()
        self.last_progress_phase = ""
        self.last_progress_refresh = 0.0
        self._reset_update_progress_display()
        self._show_update_overlay()
        self._set_update_window_state("downloading", str(getattr(info, "version")))
        self._raise_update_window()
        threading.Thread(
            target=self._download_and_start_update,
            args=(info,),
            daemon=True,
        ).start()

    def stop_update_download(self) -> None:
        if self.update_cancel_event is not None:
            self.update_cancel_event.set()
        if self.update_action_button is not None:
            self.update_action_button.configure(text="正在停止…", state="disabled")

    def offer_update(self, info: object) -> None:
        if self.update_status in {"downloading", "verifying", "preparing_install", "updater_started"}:
            return
        self.open_update_window(start_check=False)
        self.manual_update_check_running = False
        self.manual_update_info = info
        self._set_update_window_state(
            "available",
            str(getattr(info, "version")),
            list(getattr(info, "notes")),
        )
        self._raise_update_window()

    def _reset_update_progress_display(self) -> None:
        if self.update_progress_bar is not None:
            self.update_progress_bar.stop()
            self.update_progress_bar.configure(mode="determinate")
            self.update_progress_bar.set(0)
        if self.update_percent_label is not None:
            self.update_percent_label.configure(text="0%")
        if self.update_downloaded_label is not None:
            self.update_downloaded_label.configure(text="0 B")
        if self.update_speed_label is not None:
            self.update_speed_label.configure(text="0 B/s")
        if self.update_remaining_label is not None:
            self.update_remaining_label.configure(text="计算中")

    def _show_update_overlay(self) -> None:
        if self.update_overlay is not None:
            return
        overlay = ctk.CTkFrame(self.root, fg_color="#17212A", corner_radius=0)
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        ctk.CTkLabel(
            overlay,
            text="软件正在更新\n请在更新窗口中操作",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=22, weight="bold"),
            justify="center",
        ).place(relx=0.5, rely=0.5, anchor="center")
        overlay.lift()
        self.update_overlay = overlay

    def _release_update_lock(self) -> None:
        if self.update_overlay is not None:
            self.update_overlay.destroy()
            self.update_overlay = None
        self.update_cancel_event = None
        self.operation_gate.end_update()

    def _download_and_start_update(self, info: object) -> None:
        try:
            package = download_update(  # type: ignore[arg-type]
                info,
                cancel_event=self.update_cancel_event,
                progress_callback=self._receive_update_progress,
            )
            latest_version = str(getattr(info, "version"))
            self.pending_update_results.append(("preparing", latest_version))
            if not os.access(self.base_dir, os.W_OK):
                raise PermissionError(f"安装目录不可写：{self.base_dir}")
            updater_exe = self.base_dir / "updater.exe"
            updater_script = self.base_dir / "updater.py"
            restart = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__).resolve()
            if updater_exe.exists():
                temporary_updater = package.parent / "updater.exe"
                shutil.copy2(updater_exe, temporary_updater)
                command = [str(temporary_updater)]
            elif updater_script.exists():
                command = [sys.executable, str(updater_script)]
            else:
                raise FileNotFoundError("找不到 updater.exe 或 updater.py。")
            command.extend([
                "--package", str(package),
                "--install-dir", str(self.base_dir),
                "--parent-pid", str(os.getpid()),
                "--restart", str(restart),
            ])
            subprocess.Popen(command, cwd=self.base_dir, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.pending_update_results.append(("started", latest_version))
        except UpdateCancelled:
            self.pending_update_results.append(("cancelled", ""))
        except Exception as exc:
            self.pending_update_results.append(("failed", str(exc)))

    def _finish_update_cancelled(self) -> None:
        self.pending_update_progress = None
        self._release_update_lock()
        self._set_update_window_state("cancelled", self.update_latest_version)
        if self.update_status_label is not None:
            self.update_status_label.configure(text="更新已停止，未修改任何程序文件")

    def _finish_update_failed(self, message: str) -> None:
        self.pending_update_progress = None
        self._release_update_lock()
        self._set_update_window_state(
            "failed",
            self.update_latest_version,
            error=f"更新失败：{message}",
        )

    def _finish_updater_started(self, latest_version: str) -> None:
        self._set_update_window_state("updater_started", latest_version)
        self.root.after(100, self.root.destroy)

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
        card.grid_rowconfigure(1, weight=0)
        card.grid_rowconfigure(4, weight=1)
        parent.grid_rowconfigure(4, weight=1)
        ctk.CTkLabel(
            card,
            text="命令预览",
            text_color="#101820",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 8))
        self.command_preview = ctk.CTkTextbox(
            card,
            height=140,
            wrap="word",
            fg_color="#101820",
            text_color="#DCE6EE",
            border_width=0,
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.command_preview.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 8))
        self.bind_textbox_mousewheel(self.command_preview)
        ctk.CTkLabel(
            card,
            text="普通预览和复制命令不带 --yes；只有确认执行或撤销后才追加。",
            text_color="#667580",
            font=ctk.CTkFont(size=11),
        ).grid(row=2, column=0, sticky="w", padx=14, pady=(0, 8))

        ctk.CTkLabel(
            card,
            text="扫描预览",
            text_color="#101820",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).grid(row=3, column=0, sticky="w", padx=14, pady=(0, 8))
        table_frame = tk.Frame(card, bg="#FFFFFF")
        table_frame.grid(row=4, column=0, sticky="nsew", padx=14, pady=(0, 14))
        columns = (
            "序号",
            "原文件夹",
            "识别日期",
            "识别品类",
            "命中关键词",
            "单量",
            "数量",
            "动作",
            "目标名称",
            "状态",
            "原因",
        )
        self.preview_table = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.preview_table.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.preview_table.xview)
        self.preview_table.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.preview_table.bind("<MouseWheel>", self.on_preview_table_mousewheel)
        self.preview_table.bind("<Button-4>", self.on_preview_table_scroll_up)
        self.preview_table.bind("<Button-5>", self.on_preview_table_scroll_down)
        self.preview_table.bind("<Button-1>", self.on_preview_table_click, add="+")
        for column in columns:
            self.preview_table.heading(column, text=column)
            self.preview_table.column(
                column,
                width=PREVIEW_COLUMN_WIDTHS[column],
                minwidth=45,
                stretch=False,
            )
        self.preview_table.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)

    def on_scroll_frame_configure(self, _event: tk.Event) -> None:
        if self.main_canvas is not None:
            self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))

    def on_main_canvas_configure(self, event: tk.Event) -> None:
        if self.main_canvas is not None and self.scroll_window_id is not None:
            self.main_canvas.itemconfigure(self.scroll_window_id, width=event.width)

    def set_main_scroll_active(self, active: bool) -> None:
        self.main_scroll_active = active

    def is_pointer_in_main_scroll(self) -> bool:
        if self.scroll_container is None:
            return False
        pointer_x = self.scroll_container.winfo_pointerx()
        pointer_y = self.scroll_container.winfo_pointery()
        left = self.scroll_container.winfo_rootx()
        top = self.scroll_container.winfo_rooty()
        right = left + self.scroll_container.winfo_width()
        bottom = top + self.scroll_container.winfo_height()
        return left <= pointer_x <= right and top <= pointer_y <= bottom

    def on_main_mousewheel(self, event: tk.Event) -> None:
        if self.main_canvas is None or not self.is_pointer_in_main_scroll():
            return
        units = wheel_delta_to_units(int(event.delta))
        if units:
            self.main_canvas.yview_scroll(units, "units")

    def on_linux_scroll_up(self, _event: tk.Event) -> None:
        if self.main_canvas is not None and self.is_pointer_in_main_scroll():
            self.main_canvas.yview_scroll(-1, "units")

    def on_linux_scroll_down(self, _event: tk.Event) -> None:
        if self.main_canvas is not None and self.is_pointer_in_main_scroll():
            self.main_canvas.yview_scroll(1, "units")

    def on_preview_table_mousewheel(self, event: tk.Event) -> str:
        if self.preview_table is not None:
            units = wheel_delta_to_units(int(event.delta))
            if units:
                self.preview_table.yview_scroll(units, "units")
        return "break"

    def on_preview_table_scroll_up(self, _event: tk.Event) -> str:
        if self.preview_table is not None:
            self.preview_table.yview_scroll(-1, "units")
        return "break"

    def on_preview_table_scroll_down(self, _event: tk.Event) -> str:
        if self.preview_table is not None:
            self.preview_table.yview_scroll(1, "units")
        return "break"

    def on_preview_table_click(self, event: tk.Event) -> None:
        if self.preview_table is None:
            return
        if self.preview_table.identify_region(event.x, event.y) != "cell":
            return
        column_id = self.preview_table.identify_column(event.x)
        if not column_id:
            return
        try:
            column_index = int(column_id.removeprefix("#")) - 1
            if column_index < 0:
                return
            column = str(self.preview_table["columns"][column_index])
        except (ValueError, IndexError):
            return
        self.toggle_preview_column_width(column)

    def preview_table_font(self) -> tkfont.Font:
        font_spec = ttk.Style(self.root).lookup("Treeview", "font") or "TkDefaultFont"
        if isinstance(font_spec, str):
            try:
                return tkfont.nametofont(font_spec)
            except tk.TclError:
                pass
        return tkfont.Font(root=self.root, font=font_spec)

    def preview_column_measured_widths(self, column: str) -> list[int]:
        if self.preview_table is None:
            return []
        try:
            column_index = list(self.preview_table["columns"]).index(column)
        except ValueError:
            return []
        heading_text = str(self.preview_table.heading(column, "text"))
        texts = [heading_text]
        for item_id in self.preview_table.get_children():
            values = self.preview_table.item(item_id, "values")
            if column_index < len(values):
                texts.append(str(values[column_index]))
        font = self.preview_table_font()
        return [font.measure(text) for text in texts]

    def toggle_preview_column_width(self, column: str) -> None:
        if self.preview_table is None or column not in PREVIEW_COLUMN_WIDTHS:
            return
        self.preview_expanded_columns = toggle_preview_column(
            self.preview_expanded_columns,
            column,
        )
        if column in self.preview_expanded_columns:
            width = preview_expanded_width(
                PREVIEW_COLUMN_WIDTHS[column],
                self.preview_column_measured_widths(column),
            )
        else:
            width = PREVIEW_COLUMN_WIDTHS[column]
        self.preview_table.column(column, width=width, stretch=False)

    def reset_preview_column_widths(self) -> None:
        self.preview_expanded_columns.clear()
        if self.preview_table is None:
            return
        for column, width in PREVIEW_COLUMN_WIDTHS.items():
            self.preview_table.column(column, width=width, stretch=False)

    def bind_textbox_mousewheel(self, textbox: ctk.CTkTextbox) -> None:
        inner = getattr(textbox, "_textbox", None)
        target = inner if inner is not None else textbox
        target.bind("<MouseWheel>", lambda event: self.on_textbox_mousewheel(textbox, event))
        target.bind("<Button-4>", lambda _event: self.on_textbox_scroll(textbox, -1))
        target.bind("<Button-5>", lambda _event: self.on_textbox_scroll(textbox, 1))

    def on_textbox_mousewheel(self, textbox: ctk.CTkTextbox, event: tk.Event) -> str:
        units = wheel_delta_to_units(int(event.delta))
        if units:
            textbox.yview_scroll(units, "units")
        return "break"

    def on_textbox_scroll(self, textbox: ctk.CTkTextbox, units: int) -> str:
        textbox.yview_scroll(units, "units")
        return "break"

    def _build_action_bar(self, parent: ctk.CTkFrame) -> None:
        bar = ctk.CTkFrame(parent, fg_color="#FFFFFF", corner_radius=0, border_color="#D6DEE5", border_width=1)
        self.action_bar = bar
        bar.grid(row=1, column=0, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_columnconfigure(8, weight=1)
        ctk.CTkButton(
            bar,
            text="扫描预览",
            command=self.scan_preview,
            fg_color="#101820",
            hover_color="#24313C",
            width=112,
        ).grid(row=0, column=1, padx=(0, 10), pady=12)
        ctk.CTkButton(
            bar,
            text="打开报告",
            command=self.open_report,
            fg_color="#FFFFFF",
            text_color="#101820",
            border_color="#C7D0D8",
            border_width=1,
            hover_color="#E2E8EE",
            width=104,
        ).grid(row=0, column=2, padx=(0, 10), pady=12)
        ctk.CTkButton(
            bar,
            text="预览撤销",
            command=self.preview_undo,
            fg_color="#FFFFFF",
            text_color="#101820",
            border_color="#C7D0D8",
            border_width=1,
            hover_color="#E2E8EE",
            width=104,
        ).grid(row=0, column=3, padx=(0, 10), pady=12)
        ctk.CTkButton(
            bar,
            text="撤销上次整理",
            command=self.run_undo_last,
            fg_color="#FFFFFF",
            text_color="#9B3417",
            border_color="#D59B86",
            border_width=1,
            hover_color="#FFF1EA",
            width=132,
        ).grid(row=0, column=4, padx=(0, 10), pady=12)
        ctk.CTkButton(
            bar,
            text="生成命令",
            command=self.generate_command,
            fg_color="#FFFFFF",
            text_color="#101820",
            border_color="#C7D0D8",
            border_width=1,
            hover_color="#E2E8EE",
            width=104,
        ).grid(row=0, column=5, padx=(0, 10), pady=12)
        ctk.CTkButton(
            bar,
            text="复制命令",
            command=self.copy_command,
            fg_color="#FFFFFF",
            text_color="#101820",
            border_color="#C7D0D8",
            border_width=1,
            hover_color="#E2E8EE",
            width=104,
        ).grid(row=0, column=6, padx=(0, 10), pady=12)
        ctk.CTkButton(
            bar,
            text="后台运行",
            command=self.run_in_powershell,
            fg_color="#F05A28",
            hover_color="#C84418",
            width=136,
        ).grid(row=0, column=7, pady=12)

    def on_mode_changed(self, *_args: object) -> None:
        mode = self.run_mode.get()
        for value, button in self.mode_buttons.items():
            if value == mode:
                button.configure(fg_color="#F05A28", hover_color="#C84418", text_color="#FFFFFF")
            else:
                button.configure(fg_color="#1B2630", hover_color="#24313C", text_color="#DCE6EE")

        if mode == "apply":
            self.mode_badge.configure(text="Apply", fg_color="#FFE7D8", text_color="#9B3417")
            self.safety_status.configure(text=build_safety_status_text(mode, self.use_archive.get()), text_color="#FFE7D8")
            self.mode_description.configure(text="执行前请先查看 dry-run 结果；确认后启动器会追加一次性 --yes。")
            self.mode_help.configure(text="执行模式会移动、合并、重命名文件夹。点击运行时会先弹窗确认。")
            if self.archive_check is not None:
                self.archive_check.configure(state="normal")
        elif mode == "undo-last":
            self.use_archive.set(False)
            self.mode_badge.configure(text="Undo", fg_color="#FFE7D8", text_color="#9B3417")
            self.safety_status.configure(text=build_safety_status_text(mode, False), text_color="#FFE7D8")
            self.mode_description.configure(text="撤销只根据 organizer_run_log.json 记录执行，不根据文件名猜测。")
            self.mode_help.configure(text="撤销模式不使用 config.yaml，也不会启用压缩选项。")
            if self.archive_check is not None:
                self.archive_check.configure(state="disabled")
        else:
            self.mode_badge.configure(text="Dry Run", fg_color="#DFF3E8", text_color="#176342")
            self.safety_status.configure(text=build_safety_status_text(mode, False), text_color="#DFF3E8")
            self.mode_description.configure(text="先生成预览命令，确认计划后再执行真实整理。")
            self.mode_help.configure(text="预览模式只扫描并输出整理计划，不移动、不合并、不压缩任何文件。")
            if self.archive_check is not None:
                self.archive_check.configure(state="normal")
        self.update_path_status()
        self.auto_save_settings()

    def on_option_changed(self, *_args: object) -> None:
        self.on_mode_changed()

    def on_path_changed(self, *_args: object) -> None:
        self.update_path_status()
        self.auto_save_settings()

    def auto_save_settings(self) -> None:
        if getattr(self, "_initializing", False):
            return
        try:
            core_save_settings(self.settings_path, self.current_settings())
        except OSError:
            pass

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

    def clear_preview_table(self) -> None:
        self.reset_preview_column_widths()
        if self.preview_table is None:
            return
        for row_id in self.preview_table.get_children():
            self.preview_table.delete(row_id)

    def scan_preview(self) -> None:
        self.reset_preview_column_widths()
        root_value = clean_path_value(self.root_path.get())
        config_value = clean_path_value(self.config_path.get())
        if not root_value:
            messagebox.showerror(APP_TITLE, "要处理的文件夹路径不能为空。")
            return
        if config_value and not Path(config_value).is_file():
            messagebox.showerror(APP_TITLE, f"config.yaml 不存在：\n{config_value}")
            return
        try:
            rows = build_preview_rows(root_value, config_value or None)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"扫描预览失败：\n{exc}")
            return
        self.clear_preview_table()
        if self.preview_table is not None:
            for row in rows:
                self.preview_table.insert(
                    "",
                    "end",
                    values=(
                        row.sequence,
                        row.original_name,
                        row.detected_date,
                        row.detected_category,
                        row.matched_keyword,
                        row.orders,
                        row.quantity,
                        row.action,
                        row.target_name,
                        row.status,
                        row.reason,
                    ),
                )
        dry_run_settings = LauncherSettings(
            python_command=self.python_command.get().strip(),
            script_path=clean_path_value(self.script_path.get()),
            root_path=root_value,
            config_path=config_value,
            mode="dry-run",
            archive_enabled=False,
            open_result_folder=bool(self.open_result_folder.get()),
        )
        try:
            self.set_preview(build_command(dry_run_settings))
        except ValueError:
            pass
        self.save_settings(show_message=False)
        messagebox.showinfo(APP_TITLE, f"扫描预览完成，共 {len(rows)} 行。没有移动、重命名、压缩或删除文件。")

    def open_report(self) -> None:
        root_value = clean_path_value(self.root_path.get())
        if not root_value:
            messagebox.showwarning(APP_TITLE, "请先选择要处理的文件夹。")
            return
        report = find_latest_report(root_value)
        if report is None:
            messagebox.showinfo(APP_TITLE, "未找到整理报告，请先执行扫描预览或整理。")
            return
        try:
            os.startfile(report)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"无法打开整理报告：\n{exc}")

    def preview_undo(self) -> None:
        root_value = clean_path_value(self.root_path.get())
        if not root_value:
            messagebox.showwarning(APP_TITLE, "请先选择要处理的文件夹。")
            return
        ok, status = undo_log_status(root_value)
        self.run_mode.set("undo-last")
        command = self.build_command(include_yes=False)
        if command:
            self.set_preview(command)
        message_type = messagebox.showinfo if ok else messagebox.showwarning
        message_type(APP_TITLE, f"{status}\n\n预览撤销只生成命令，不追加 --yes，不执行真实撤销。")

    def run_undo_last(self) -> None:
        root_value = clean_path_value(self.root_path.get())
        ok, status = undo_log_status(root_value)
        if not ok:
            messagebox.showwarning(APP_TITLE, status)
            return
        previous_mode = self.run_mode.get()
        self.run_mode.set("undo-last")
        if not messagebox.askyesno(
            "确认撤销上次整理",
            "此操作将根据 organizer_run_log.json 撤销上次整理。\n"
            "不会猜测路径。\n"
            "如果源路径已存在，将跳过。\n"
            "是否继续？",
        ):
            self.run_mode.set(previous_mode)
            return
        command = self.build_command(include_yes=True)
        if not command:
            return
        self.set_preview(command)
        self.save_settings(show_message=False)
        self.start_background_command(command)

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
        self.start_background_command(command)

    def start_background_command(self, command: str) -> None:
        log_path = self.base_dir / LAUNCHER_OUTPUT_LOG
        if not self.operation_gate.begin_task():
            messagebox.showwarning(APP_TITLE, "更新或其他整理任务正在运行，请等待完成后再试。")
            return
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
            self.operation_gate.end_task()
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
            self.operation_gate.end_task()
            error_message = str(exc)
            self.root.after(0, lambda: messagebox.showerror(APP_TITLE, f"后台任务运行失败：\n{error_message}"))
            return

        self.operation_gate.end_task()
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
