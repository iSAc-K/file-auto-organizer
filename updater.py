from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path


PRESERVED_NAMES = {
    "user_config.yaml",
    "launcher_settings.json",
    "rename_log.csv",
    "organizer_run_log.json",
    "launcher_run_output.log",
}


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    for member in members:
        path = Path(member.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"更新包包含不安全路径：{member.filename}")
    return members


def apply_update_package(package: Path, install_dir: Path) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    backup_dir = Path(tempfile.mkdtemp(prefix="file-organizer-backup-"))
    extract_dir = Path(tempfile.mkdtemp(prefix="file-organizer-extract-"))
    replaced: list[Path] = []
    try:
        with zipfile.ZipFile(package) as archive:
            members = _safe_members(archive)
            archive.extractall(extract_dir, members)
        children = [child for child in extract_dir.iterdir()]
        source_root = children[0] if len(children) == 1 and children[0].is_dir() else extract_dir
        for source in source_root.rglob("*"):
            if source.is_dir():
                continue
            relative = source.relative_to(source_root)
            if relative.parts[0] in PRESERVED_NAMES:
                continue
            target = install_dir / relative
            if target.exists():
                backup = backup_dir / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            replaced.append(relative)
    except Exception:
        for relative in reversed(replaced):
            target = install_dir / relative
            backup = backup_dir / relative
            if backup.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
            elif target.exists():
                target.unlink()
        raise
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Windows 文件整理助手更新器")
    parser.add_argument("--package", required=True)
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--restart", required=True)
    args = parser.parse_args(argv)
    wait_for_process(args.parent_pid)
    apply_update_package(Path(args.package), Path(args.install_dir))
    subprocess.Popen([args.restart], cwd=args.install_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
