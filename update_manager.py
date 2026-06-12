from __future__ import annotations

import hashlib
import json
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


UPDATE_MANIFEST_URL = (
    "https://github.com/iSAc-K/file-auto-organizer/releases/latest/download/update.json"
)


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    download_url: str
    sha256: str
    notes: list[str]


def _version_tuple(value: str) -> tuple[int, ...]:
    cleaned = value.strip().lower().removeprefix("v")
    parts = cleaned.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError(f"无效版本号：{value}")
    return tuple(int(part) for part in parts)


def is_newer_version(latest: str, current: str) -> bool:
    left = list(_version_tuple(latest))
    right = list(_version_tuple(current))
    width = max(len(left), len(right))
    return tuple(left + [0] * (width - len(left))) > tuple(right + [0] * (width - len(right)))


def parse_update_manifest(data: dict[str, Any]) -> UpdateInfo:
    version = str(data.get("version", "")).strip()
    download_url = str(data.get("download_url", "")).strip()
    sha256 = str(data.get("sha256", "")).strip().lower()
    notes = data.get("notes", [])
    _version_tuple(version)
    if not download_url.startswith("https://") or not download_url.lower().endswith(".zip"):
        raise ValueError("更新地址必须是 HTTPS ZIP。")
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise ValueError("更新清单 SHA-256 无效。")
    if isinstance(notes, str):
        notes = [notes]
    if not isinstance(notes, list):
        raise ValueError("更新说明必须是列表。")
    return UpdateInfo(version, download_url, sha256, [str(note) for note in notes])


def fetch_update_info(url: str = UPDATE_MANIFEST_URL, timeout: float = 5.0) -> UpdateInfo:
    request = urllib.request.Request(url, headers={"User-Agent": "WindowsFileOrganizer-Updater"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("更新清单顶层必须是对象。")
    return parse_update_manifest(data)


def verify_sha256(path: Path, expected: str) -> bool:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().casefold() == expected.strip().casefold()


def download_update(info: UpdateInfo, timeout: float = 60.0) -> Path:
    target = Path(tempfile.mkdtemp(prefix="file-organizer-update-")) / "update.zip"
    request = urllib.request.Request(info.download_url, headers={"User-Agent": "WindowsFileOrganizer-Updater"})
    with urllib.request.urlopen(request, timeout=timeout) as response, target.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
    if not verify_sha256(target, info.sha256):
        target.unlink(missing_ok=True)
        raise ValueError("更新包 SHA-256 校验失败。")
    return target
