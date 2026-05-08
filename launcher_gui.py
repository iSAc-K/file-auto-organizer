from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "Windows 文件整理助手"
SETTINGS_NAME = "launcher_settings.json"


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


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


class LauncherGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.minsize(900, 600)

        self.base_dir = app_base_dir()
        self.settings_path = self.base_dir / SETTINGS_NAME
        self.defaults = self.default_settings()
        settings = self.load_settings()

        self.python_command = tk.StringVar(value=settings["python_command"])
        self.script_path = tk.StringVar(value=settings["script_path"])
        self.root_path = tk.StringVar(value=settings["root_path"])
        self.config_path = tk.StringVar(value=settings["config_path"])
        self.run_mode = tk.StringVar(value=settings["mode"])
        self.use_archive = tk.BooleanVar(value=bool(settings["archive_enabled"]))
        self.open_result_folder = tk.BooleanVar(value=bool(settings["open_result_folder"]))
        self.archive_check: ttk.Checkbutton | None = None

        self._build_ui()
        self.run_mode.trace_add("write", self.on_mode_changed)
        self.on_mode_changed()

    def default_settings(self) -> dict[str, object]:
        default_script = self.base_dir / "file_helper.py"
        default_config = self.base_dir / "config.yaml"
        return {
            "python_command": "py",
            "script_path": str(default_script) if default_script.exists() else "",
            "root_path": "",
            "config_path": str(default_config) if default_config.exists() else "",
            "mode": "dry-run",
            "archive_enabled": False,
            "open_result_folder": True,
        }

    def load_settings(self) -> dict[str, object]:
        settings = dict(self.defaults)
        if not self.settings_path.exists():
            return settings
        try:
            with self.settings_path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("设置文件顶层必须是对象。")
        except Exception as exc:
            messagebox.showwarning(APP_TITLE, f"launcher_settings.json 已损坏，已使用默认设置。\n{exc}")
            return settings
        for key in settings:
            if key in loaded:
                settings[key] = loaded[key]
        if settings.get("mode") not in {"dry-run", "apply", "undo-last"}:
            settings["mode"] = "dry-run"
        return settings

    def current_settings(self) -> dict[str, object]:
        return {
            "python_command": self.python_command.get().strip(),
            "script_path": clean_path_value(self.script_path.get()),
            "root_path": clean_path_value(self.root_path.get()),
            "config_path": clean_path_value(self.config_path.get()),
            "mode": self.run_mode.get(),
            "archive_enabled": bool(self.use_archive.get()),
            "open_result_folder": bool(self.open_result_folder.get()),
        }

    def save_settings(self, show_message: bool = True) -> None:
        try:
            with self.settings_path.open("w", encoding="utf-8", newline="\n") as f:
                json.dump(self.current_settings(), f, ensure_ascii=False, indent=2)
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

    def apply_settings(self, settings: dict[str, object]) -> None:
        self.python_command.set(str(settings["python_command"]))
        self.script_path.set(str(settings["script_path"]))
        self.root_path.set(str(settings["root_path"]))
        self.config_path.set(str(settings["config_path"]))
        self.run_mode.set(str(settings["mode"]))
        self.use_archive.set(bool(settings["archive_enabled"]))
        self.open_result_folder.set(bool(settings["open_result_folder"]))
        self.set_preview("")

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=16)
        main.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)

        ttk.Label(main, text="Python 命令").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(main, textvariable=self.python_command).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Label(
            main,
            text=r"示例：py、python、C:\Users\kt\AppData\Local\Programs\Python\Python313\python.exe",
            foreground="#555555",
        ).grid(row=1, column=1, sticky="w")

        ttk.Label(main, text="file_helper.py 路径").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(main, textvariable=self.script_path).grid(row=2, column=1, sticky="ew", pady=6)
        ttk.Button(main, text="选择脚本", command=self.select_script).grid(row=2, column=2, padx=(8, 0), pady=6)

        ttk.Label(main, text="要处理的文件夹路径").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Entry(main, textvariable=self.root_path).grid(row=3, column=1, sticky="ew", pady=6)
        ttk.Button(main, text="选择文件夹", command=self.select_root_folder).grid(row=3, column=2, padx=(8, 0), pady=6)

        ttk.Label(main, text="config.yaml 路径").grid(row=4, column=0, sticky="w", pady=6)
        ttk.Entry(main, textvariable=self.config_path).grid(row=4, column=1, sticky="ew", pady=6)
        ttk.Button(main, text="选择配置文件", command=self.select_config).grid(row=4, column=2, padx=(8, 0), pady=6)

        ttk.Label(main, text="运行模式").grid(row=5, column=0, sticky="w", pady=6)
        mode_frame = ttk.Frame(main)
        mode_frame.grid(row=5, column=1, sticky="w", pady=6)
        ttk.Radiobutton(mode_frame, text="预览模式（--dry-run）", variable=self.run_mode, value="dry-run").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(mode_frame, text="执行模式（--apply）", variable=self.run_mode, value="apply").grid(row=0, column=1, sticky="w", padx=(18, 0))
        ttk.Radiobutton(mode_frame, text="撤销上次整理（--undo-last）", variable=self.run_mode, value="undo-last").grid(row=0, column=2, sticky="w", padx=(18, 0))

        ttk.Label(main, text="可选项").grid(row=6, column=0, sticky="w", pady=6)
        options_frame = ttk.Frame(main)
        options_frame.grid(row=6, column=1, columnspan=2, sticky="w", pady=6)
        self.archive_check = ttk.Checkbutton(
            options_frame,
            text="整理完成后压缩最终文件夹",
            variable=self.use_archive,
        )
        self.archive_check.grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            options_frame,
            text="处理完成后打开结果目录",
            variable=self.open_result_folder,
        ).grid(row=0, column=1, sticky="w", padx=(18, 0))

        ttk.Label(main, text="命令预览").grid(row=7, column=0, sticky="nw", pady=(14, 6))
        self.command_preview = tk.Text(main, height=7, wrap="word")
        self.command_preview.grid(row=7, column=1, columnspan=2, sticky="nsew", pady=(14, 6))
        main.rowconfigure(7, weight=1)

        button_frame = ttk.Frame(main)
        button_frame.grid(row=8, column=1, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(button_frame, text="生成命令", command=self.generate_command).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(button_frame, text="复制命令", command=self.copy_command).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(button_frame, text="在 PowerShell 中运行", command=self.run_in_powershell).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(button_frame, text="保存设置", command=self.save_settings).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(button_frame, text="清空已保存路径", command=self.clear_settings).grid(row=0, column=4)

    def on_mode_changed(self, *_args: object) -> None:
        if self.archive_check is None:
            return
        if self.run_mode.get() == "undo-last":
            self.use_archive.set(False)
            self.archive_check.state(["disabled"])
        else:
            self.archive_check.state(["!disabled"])

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

    def validate_inputs(self) -> tuple[Path, Path, Path | None] | None:
        if not self.python_command.get().strip():
            messagebox.showerror(APP_TITLE, "Python 命令不能为空。")
            return None

        script_value = clean_path_value(self.script_path.get())
        if not script_value:
            messagebox.showerror(APP_TITLE, "file_helper.py 路径不能为空。")
            return None
        script_path = Path(script_value)
        if not script_path.exists() or not script_path.is_file():
            messagebox.showerror(APP_TITLE, f"file_helper.py 不存在：\n{script_path}")
            return None

        root_value = clean_path_value(self.root_path.get())
        if not root_value:
            messagebox.showerror(APP_TITLE, "要处理的文件夹路径不能为空。")
            return None
        root_path = Path(root_value)
        if not root_path.exists() or not root_path.is_dir():
            messagebox.showerror(APP_TITLE, f"要处理的文件夹不存在：\n{root_path}")
            return None

        if self.run_mode.get() == "undo-last":
            return script_path, root_path, None

        config_value = clean_path_value(self.config_path.get())
        config_path = Path(config_value) if config_value else None
        if config_path and (not config_path.exists() or not config_path.is_file()):
            messagebox.showerror(APP_TITLE, f"config.yaml 不存在：\n{config_path}")
            return None

        return script_path, root_path, config_path

    def build_command(self, include_yes: bool = False) -> str | None:
        validated = self.validate_inputs()
        if validated is None:
            return None

        script_path, root_path, config_path = validated
        parts = [
            format_python_command(self.python_command.get()),
            ps_quote(script_path),
            "--root",
            ps_quote(root_path),
        ]

        mode = self.run_mode.get()
        if mode != "undo-last" and config_path is not None:
            parts.extend(["--config", ps_quote(config_path)])

        if mode == "apply":
            parts.append("--apply")
            if self.use_archive.get():
                parts.append("--archive")
        elif mode == "undo-last":
            parts.append("--undo-last")
        else:
            parts.append("--dry-run")

        if include_yes:
            parts.append("--yes")

        command = " ".join(parts)
        if self.open_result_folder.get():
            command += f"; if ($LASTEXITCODE -eq 0) {{ Start-Process -FilePath {ps_quote(root_path)} }}"
        return command

    def set_preview(self, command: str) -> None:
        self.command_preview.delete("1.0", tk.END)
        self.command_preview.insert("1.0", command)

    def get_preview(self) -> str:
        return self.command_preview.get("1.0", tk.END).strip()

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
        messagebox.showinfo(APP_TITLE, "命令已复制")

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
        if mode in {"apply", "undo-last"}:
            if self.build_command() is None:
                return
            if not self.confirm_real_run():
                return
            command = self.build_command(include_yes=True)
            if not command:
                return
            self.set_preview(command)
            self.save_settings(show_message=False)
        else:
            command = self.generate_command()
        if not command:
            return
        self.save_settings(show_message=False)
        try:
            subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoExit",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ]
            )
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"无法启动 PowerShell：\n{exc}")


def main() -> None:
    root = tk.Tk()
    LauncherGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
