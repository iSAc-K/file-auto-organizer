from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal


UPDATE_MANIFEST_URL = (
    "https://github.com/iSAc-K/file-auto-organizer/releases/latest/download/update.json"
)


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    download_url: str
    sha256: str
    notes: list[str]


ProgressPhase = Literal["downloading", "verifying", "verified"]


@dataclass(frozen=True)
class DownloadProgress:
    phase: ProgressPhase
    downloaded_bytes: int
    total_bytes: int | None
    elapsed_seconds: float
    average_bytes_per_second: float
    estimated_seconds_remaining: float | None


class UpdateCancelled(Exception):
    def __init__(self, path: Path):
        super().__init__("用户已停止更新。")
        self.path = path


def _raise_if_cancelled(
    cancel_event: threading.Event | None,
    path: Path,
) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise UpdateCancelled(path)


def _build_progress(
    phase: ProgressPhase,
    downloaded: int,
    total: int | None,
    elapsed: float,
) -> DownloadProgress:
    speed = downloaded / elapsed if elapsed > 0 else 0.0
    remaining = None
    if total is not None and speed > 0:
        remaining = max(0.0, (total - downloaded) / speed)
    return DownloadProgress(phase, downloaded, total, elapsed, speed, remaining)


def _report_progress(
    callback: Callable[[DownloadProgress], None] | None,
    progress: DownloadProgress,
) -> None:
    if callback is not None:
        callback(progress)


def _content_length(response: Any) -> int | None:
    value = response.headers.get("Content-Length")
    try:
        total = int(value)
    except (TypeError, ValueError):
        return None
    return total if total > 0 else None


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


def fetch_update_info(url: str = UPDATE_MANIFEST_URL, timeout: float = 15.0) -> UpdateInfo:
    request = urllib.request.Request(url, headers={"User-Agent": "WindowsFileOrganizer-Updater"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("更新清单顶层必须是对象。")
    return parse_update_manifest(data)


def fetch_update_info_with_retry(
    attempts: int = 3,
    retry_delay: float = 2.0,
    fetcher: Callable[[], UpdateInfo] = fetch_update_info,
    sleeper: Callable[[float], None] = time.sleep,
) -> UpdateInfo:
    if attempts < 1:
        raise ValueError("更新检查次数必须至少为 1。")
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return fetcher()
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                sleeper(retry_delay)
    assert last_error is not None
    raise last_error


def verify_sha256(
    path: Path,
    expected: str,
    cancel_event: threading.Event | None = None,
    progress_callback: Callable[[DownloadProgress], None] | None = None,
    total_bytes: int | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    digest = hashlib.sha256()
    verified_bytes = 0
    started_at = clock()
    _raise_if_cancelled(cancel_event, path)
    _report_progress(
        progress_callback,
        _build_progress("verifying", 0, total_bytes, 0.0),
    )
    _raise_if_cancelled(cancel_event, path)
    with path.open("rb") as file:
        while True:
            _raise_if_cancelled(cancel_event, path)
            chunk = file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            verified_bytes += len(chunk)
            elapsed = max(0.0, clock() - started_at)
            _report_progress(
                progress_callback,
                _build_progress(
                    "verifying",
                    verified_bytes,
                    total_bytes,
                    elapsed,
                ),
            )
            _raise_if_cancelled(cancel_event, path)
    return digest.hexdigest().casefold() == expected.strip().casefold()


def download_update(
    info: UpdateInfo,
    timeout: float = 60.0,
    cancel_event: threading.Event | None = None,
    progress_callback: Callable[[DownloadProgress], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    chunk_size: int = 1024 * 1024,
) -> Path:
    if chunk_size < 1:
        raise ValueError("下载块大小必须是正整数。")
    temp_dir = Path(tempfile.mkdtemp(prefix="file-organizer-update-"))
    target = temp_dir / "update.zip"
    try:
        request = urllib.request.Request(
            info.download_url,
            headers={"User-Agent": "WindowsFileOrganizer-Updater"},
        )
        started_at = clock()
        downloaded = 0
        total_bytes = None
        _raise_if_cancelled(cancel_event, target)
        with urllib.request.urlopen(request, timeout=timeout) as response, target.open("wb") as output:
            total_bytes = _content_length(response)
            while True:
                _raise_if_cancelled(cancel_event, target)
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                elapsed = max(0.0, clock() - started_at)
                _report_progress(
                    progress_callback,
                    _build_progress(
                        "downloading",
                        downloaded,
                        total_bytes,
                        elapsed,
                    ),
                )
                _raise_if_cancelled(cancel_event, target)
        if not verify_sha256(
            target,
            info.sha256,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
            total_bytes=total_bytes,
            clock=clock,
        ):
            raise ValueError("更新包 SHA-256 校验失败。")
        _raise_if_cancelled(cancel_event, target)
        elapsed = max(0.0, clock() - started_at)
        _report_progress(
            progress_callback,
            _build_progress(
                "verified",
                downloaded,
                total_bytes,
                elapsed,
            ),
        )
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return target
