from __future__ import annotations

import argparse
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

import customtkinter as ctk


PRESERVED_NAMES = {
    "user_config.yaml",
    "launcher_settings.json",
    "rename_log.csv",
    "organizer_run_log.json",
    "launcher_run_output.log",
}

InstallPhase = Literal["waiting", "backing_up", "installing", "rolling_back", "complete"]


@dataclass(frozen=True)
class InstallProgress:
    phase: InstallPhase
    completed_files: int
    total_files: int
    current_file: str = ""


class UpdateInstallError(Exception):
    def __init__(
        self,
        install_error: Exception,
        backup_dir: Path,
        rollback_error: Exception | None = None,
    ) -> None:
        super().__init__(str(install_error))
        self.install_error = install_error
        self.backup_dir = backup_dir
        self.rollback_error = rollback_error


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    for member in members:
        path = Path(member.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"更新包包含不安全路径：{member.filename}")
    return members


def apply_update_package(
    package: Path,
    install_dir: Path,
    progress_callback: Callable[[InstallProgress], None] | None = None,
) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    backup_dir = Path(tempfile.mkdtemp(prefix="file-organizer-backup-"))
    extract_dir = Path(tempfile.mkdtemp(prefix="file-organizer-extract-"))
    replaced: list[Path] = []
    cleanup_backup = False

    def report(phase: InstallPhase, done: int, total: int, current: Path | None = None) -> None:
        if progress_callback is not None:
            progress_callback(
                InstallProgress(
                    phase=phase,
                    completed_files=done,
                    total_files=total,
                    current_file=str(current or ""),
                )
            )

    try:
        with zipfile.ZipFile(package) as archive:
            members = _safe_members(archive)
            archive.extractall(extract_dir, members)
        children = list(extract_dir.iterdir())
        source_root = children[0] if len(children) == 1 and children[0].is_dir() else extract_dir
        sources = [
            source
            for source in source_root.rglob("*")
            if source.is_file()
            and source.relative_to(source_root).parts[0] not in PRESERVED_NAMES
        ]
        total = len(sources)
        for index, source in enumerate(sources, start=1):
            relative = source.relative_to(source_root)
            target = install_dir / relative
            if target.exists():
                backup = backup_dir / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            report("backing_up", index, total, relative)
        for index, source in enumerate(sources, start=1):
            relative = source.relative_to(source_root)
            target = install_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            replaced.append(relative)
            shutil.copy2(source, target)
            report("installing", index, total, relative)
        report("complete", total, total)
        cleanup_backup = True
    except ValueError:
        cleanup_backup = True
        raise
    except Exception as install_error:
        rollback_error: Exception | None = None
        rollback_total = len(replaced)
        try:
            for index, relative in enumerate(reversed(replaced), start=1):
                report("rolling_back", index - 1, rollback_total, relative)
                target = install_dir / relative
                backup = backup_dir / relative
                if backup.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target)
                elif target.exists():
                    target.unlink()
                report("rolling_back", index, rollback_total, relative)
            cleanup_backup = True
        except Exception as error:
            rollback_error = error
        raise UpdateInstallError(install_error, backup_dir, rollback_error) from install_error
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)
        if cleanup_backup:
            shutil.rmtree(backup_dir, ignore_errors=True)


def wait_for_process(pid: int, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.25)
    raise TimeoutError("等待主程序退出超时。")


class UpdaterWindow:
    POLL_MS = 100

    def __init__(
        self,
        root: ctk.CTk,
        package: Path,
        install_dir: Path,
        parent_pid: int,
        restart: Path,
    ) -> None:
        self.root = root
        self.package = package
        self.install_dir = install_dir
        self.parent_pid = parent_pid
        self.restart = restart
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.active = True
        ctk.set_appearance_mode("light")
        self.root.title("正在更新 Windows 文件整理助手")
        self.root.geometry("480x320")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self.root.after(self.POLL_MS, self._poll_events)
        threading.Thread(target=self._worker, daemon=True).start()

    def _build_ui(self) -> None:
        container = ctk.CTkFrame(self.root, fg_color="#F5F7FA", corner_radius=0)
        container.pack(fill="both", expand=True)
        self.stage_label = ctk.CTkLabel(
            container,
            text="正在等待主程序关闭",
            text_color="#101820",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        self.stage_label.pack(pady=(36, 10))
        self.description_label = ctk.CTkLabel(
            container,
            text="请勿关闭此窗口或电脑。",
            text_color="#5F6B76",
            font=ctk.CTkFont(size=14),
        )
        self.description_label.pack(pady=(0, 24))
        self.progress_bar = ctk.CTkProgressBar(
            container,
            width=380,
            height=14,
            progress_color="#F05A28",
            fg_color="#DDE3E8",
        )
        self.progress_bar.pack()
        self.progress_bar.set(0)
        self.counter_label = ctk.CTkLabel(
            container,
            text="准备中",
            text_color="#5F6B76",
            font=ctk.CTkFont(size=13),
        )
        self.counter_label.pack(pady=(12, 18))
        self.close_button = ctk.CTkButton(
            container,
            text="关闭",
            width=120,
            state="disabled",
            command=self.root.destroy,
        )
        self.close_button.pack()

    def _worker(self) -> None:
        try:
            self.events.put(("progress", InstallProgress("waiting", 0, 0)))
            wait_for_process(self.parent_pid)
            apply_update_package(
                self.package,
                self.install_dir,
                progress_callback=lambda event: self.events.put(("progress", event)),
            )
            self.events.put(("success", None))
        except UpdateInstallError as error:
            self.events.put(("install_error", error))
        except Exception as error:
            self.events.put(("error", error))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self._render_progress(payload)
                elif kind == "success":
                    self._render_success()
                elif kind == "install_error":
                    self._render_install_error(payload)
                elif kind == "error":
                    self._render_error(str(payload))
        except queue.Empty:
            pass
        if self.root.winfo_exists():
            self.root.after(self.POLL_MS, self._poll_events)

    def _render_progress(self, payload: object) -> None:
        if not isinstance(payload, InstallProgress):
            return
        titles = {
            "waiting": "正在等待主程序关闭",
            "backing_up": "正在备份当前版本",
            "installing": "正在安装新版本",
            "rolling_back": "安装失败，正在恢复原版本",
            "complete": "更新安装完成",
        }
        self.stage_label.configure(text=titles[payload.phase])
        value = payload.completed_files / payload.total_files if payload.total_files > 0 else 0
        self.progress_bar.set(min(1.0, max(0.0, value)))
        if payload.total_files > 0:
            self.counter_label.configure(
                text=f"{payload.completed_files} / {payload.total_files}  {payload.current_file}"
            )
        else:
            self.counter_label.configure(text="准备中")

    def _render_success(self) -> None:
        self.active = False
        self.stage_label.configure(text="更新完成，即将重新启动")
        self.description_label.configure(text="新版本已经安装完成。")
        self.progress_bar.set(1)
        self.counter_label.configure(text="100%")
        self.root.after(1000, self._restart_and_close)

    def _restart_and_close(self) -> None:
        try:
            subprocess.Popen([str(self.restart)], cwd=self.install_dir)
        except Exception as error:
            self._render_error(f"更新已完成，但重新启动失败：{error}")
            return
        self.root.destroy()

    def _render_install_error(self, payload: object) -> None:
        if not isinstance(payload, UpdateInstallError):
            self._render_error(str(payload))
            return
        if payload.rollback_error is None:
            message = f"安装失败，已恢复原版本。\n{payload.install_error}"
        else:
            message = (
                "安装和自动恢复均失败。\n"
                f"安装错误：{payload.install_error}\n"
                f"恢复错误：{payload.rollback_error}\n"
                f"备份目录：{payload.backup_dir}"
            )
        self._render_error(message)

    def _render_error(self, message: str) -> None:
        self.active = False
        self.stage_label.configure(text="更新失败")
        self.description_label.configure(text=message, wraplength=410)
        self.counter_label.configure(text="")
        self.close_button.configure(state="normal")

    def _on_close(self) -> None:
        if not self.active:
            self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Windows 文件整理助手更新器")
    parser.add_argument("--package", required=True)
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--restart", required=True)
    args = parser.parse_args(argv)
    root = ctk.CTk()
    UpdaterWindow(
        root=root,
        package=Path(args.package),
        install_dir=Path(args.install_dir),
        parent_pid=args.parent_pid,
        restart=Path(args.restart),
    )
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
