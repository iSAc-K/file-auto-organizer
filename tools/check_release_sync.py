#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check whether a foldered release contains current source-side companion files."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_FILES = ("file_helper.py", "config.yaml", "README.md", "VERSION.txt")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查发布文件夹是否和当前源码配套文件一致")
    parser.add_argument("--release", required=True, help="发布文件夹路径，例如 .\\Windows文件整理助手-v1.2")
    parser.add_argument(
        "--source",
        default=str(Path(__file__).resolve().parents[1]),
        help="源码根目录，默认使用本脚本所在仓库根目录",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        default=list(DEFAULT_FILES),
        help="要检查的相对文件路径，默认检查 file_helper.py config.yaml README.md VERSION.txt",
    )
    return parser.parse_args(argv)


def check_files(source_root: Path, release_root: Path, files: Iterable[str]) -> tuple[int, list[str]]:
    lines: list[str] = []
    issue_count = 0
    for relative in files:
        source_path = source_root / relative
        release_path = release_root / relative
        if not source_path.exists() or not source_path.is_file():
            lines.append(f"{relative}\t缺失")
            issue_count += 1
            continue
        if not release_path.exists() or not release_path.is_file():
            lines.append(f"{relative}\t缺失")
            issue_count += 1
            continue
        if sha256_file(source_path) == sha256_file(release_path):
            lines.append(f"{relative}\t一致")
        else:
            lines.append(f"{relative}\t不一致")
            issue_count += 1
    return issue_count, lines


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    source_root = Path(args.source).expanduser().resolve()
    release_root = Path(args.release).expanduser().resolve()

    if not source_root.exists() or not source_root.is_dir():
        print(f"错误：源码目录不存在：{source_root}")
        return 2
    if not release_root.exists() or not release_root.is_dir():
        print(f"错误：发布文件夹不存在：{release_root}")
        return 2

    print("========== 发布包同步检查 ==========")
    print(f"源码目录：{source_root}")
    print(f"发布目录：{release_root}")
    issue_count, lines = check_files(source_root, release_root, args.files)
    for line in lines:
        print(f"- {line}")
    if issue_count:
        print(f"检查完成：发现 {issue_count} 个缺失或不一致文件。")
        return 1
    print("检查完成：发布文件夹配套文件和源码一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
