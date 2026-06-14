# Visual Update Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a visible, cancellable download workflow with one-second average-speed updates, lock the main application during updates, and show non-cancellable installation/rollback progress in `updater.exe`.

**Architecture:** `update_manager.py` owns download, verification, progress events, and cancellation. `launcher_core.py` owns pure formatting and state rules that can be unit-tested without Tk. `launcher_gui.py` renders the update window, unifies automatic/manual update entry points, and applies a blocking overlay to the main window. `updater.py` keeps file replacement and rollback logic while adding progress callbacks and a small installer window.

**Tech Stack:** Python 3.14, `urllib.request`, `threading.Event`, Tkinter/CustomTkinter, `unittest`, PyInstaller `--onedir`

---

## File Map

- Modify `update_manager.py`: progress event model, cancellation exception, cancellable download and cancellable SHA-256 verification.
- Modify `launcher_core.py`: update-state types, byte/speed/time formatting, progress presentation model, close/cancel rules.
- Modify `launcher_gui.py`: redesigned update window, one-second UI refresh, stop/restart actions, main-window blocking overlay, unified automatic/manual update flow.
- Modify `updater.py`: install progress callbacks, rollback result details, non-cancellable installer GUI, restart behavior.
- Modify `updater.spec`: bundle CustomTkinter assets for the installer window.
- Modify `README.md`: describe visible progress, cancellation boundary, UI lock, installer window, and failure behavior.
- Modify `tests/test_update_manager.py`: deterministic download, speed, cancellation, verification, cleanup tests.
- Modify `tests/test_launcher_core.py`: formatting and state-rule tests.
- Modify `tests/test_updater.py`: install progress, rollback success/failure, preserved files, and backup-path tests.

### Task 1: Add Deterministic Progress Formatting And State Rules

**Files:**
- Modify: `launcher_core.py`
- Test: `tests/test_launcher_core.py`

- [ ] **Step 1: Write failing tests for byte, speed, remaining-time, and state rules**

Add imports and tests:

```python
from launcher_core import (
    can_cancel_update,
    can_close_update_window,
    format_byte_count,
    format_download_speed,
    format_remaining_time,
)


def test_update_progress_formatters_are_stable():
    assert format_byte_count(0) == "0 B"
    assert format_byte_count(1536) == "1.5 KB"
    assert format_byte_count(5 * 1024 * 1024) == "5.0 MB"
    assert format_download_speed(2 * 1024 * 1024) == "2.0 MB/s"
    assert format_remaining_time(None) == "计算中"
    assert format_remaining_time(4.2) == "约 5 秒"
    assert format_remaining_time(75) == "约 2 分钟"


def test_update_cancel_and_close_rules_match_install_boundary():
    for state in ("downloading", "verifying"):
        assert can_cancel_update(state)
        assert not can_close_update_window(state)
    for state in ("preparing_install", "updater_started"):
        assert not can_cancel_update(state)
        assert not can_close_update_window(state)
    for state in ("available", "latest", "failed", "cancelled"):
        assert can_close_update_window(state)
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
python -m unittest tests.test_launcher_core.LauncherCoreTests.test_update_progress_formatters_are_stable tests.test_launcher_core.LauncherCoreTests.test_update_cancel_and_close_rules_match_install_boundary -v
```

Expected: import errors because the new helpers do not exist.

- [ ] **Step 3: Implement the pure formatting and state helpers**

Add to `launcher_core.py`:

```python
UpdateStatus = Literal[
    "checking",
    "latest",
    "available",
    "downloading",
    "verifying",
    "cancelled",
    "failed",
    "preparing_install",
    "updater_started",
]


def format_byte_count(value: int) -> str:
    size = max(0, int(value))
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def format_download_speed(bytes_per_second: float) -> str:
    return f"{format_byte_count(max(0, int(bytes_per_second)))}/s"


def format_remaining_time(seconds: float | None) -> str:
    if seconds is None:
        return "计算中"
    rounded = max(0, int(seconds + 0.999))
    if rounded < 60:
        return f"约 {rounded} 秒"
    return f"约 {(rounded + 59) // 60} 分钟"


def can_cancel_update(status: str) -> bool:
    return status in {"downloading", "verifying"}


def can_close_update_window(status: str) -> bool:
    return status in {"checking", "latest", "available", "cancelled", "failed"}
```

Update `build_update_status_text()` so it covers `verifying`, `cancelled`, `preparing_install`, and `updater_started`.

- [ ] **Step 4: Run launcher-core tests**

Run:

```powershell
python -m unittest tests.test_launcher_core -v
```

Expected: all launcher-core tests pass.

- [ ] **Step 5: Commit the pure state layer**

```powershell
git add launcher_core.py tests/test_launcher_core.py
git commit -m "feat: define visual update state rules"
```

### Task 2: Implement Cancellable Download And Verification Progress

**Files:**
- Modify: `update_manager.py`
- Test: `tests/test_update_manager.py`

- [ ] **Step 1: Write failing tests for progress metrics**

Add a fake response and deterministic clock:

```python
import io
import threading
from unittest.mock import patch

from update_manager import DownloadProgress, UpdateCancelled, download_update


class FakeResponse:
    def __init__(self, payload: bytes, content_length: bool = True):
        self.stream = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))} if content_length else {}

    def read(self, size: int) -> bytes:
        return self.stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_download_reports_total_average_speed_and_remaining_time():
    payload = b"x" * 12
    info = UpdateInfo("2.5.0", "https://example.com/update.zip", hashlib.sha256(payload).hexdigest(), [])
    events: list[DownloadProgress] = []
    times = iter([10.0, 11.0, 12.0, 13.0, 14.0])

    with patch("update_manager.urllib.request.urlopen", return_value=FakeResponse(payload)):
        path = download_update(
            info,
            progress_callback=events.append,
            clock=lambda: next(times),
            chunk_size=4,
        )

    try:
        download_events = [event for event in events if event.phase == "downloading"]
        assert download_events[-1].downloaded_bytes == 12
        assert download_events[-1].total_bytes == 12
        assert download_events[-1].average_bytes_per_second == 3.0
        assert download_events[-1].estimated_seconds_remaining == 0.0
        assert events[-1].phase == "verified"
    finally:
        path.unlink(missing_ok=True)
        path.parent.rmdir()
```

- [ ] **Step 2: Write failing tests for unknown size, download cancellation, and verification cancellation**

```python
def test_download_without_content_length_reports_unknown_total():
    payload = b"payload"
    info = UpdateInfo("2.5.0", "https://example.com/update.zip", hashlib.sha256(payload).hexdigest(), [])
    events = []
    with patch(
        "update_manager.urllib.request.urlopen",
        return_value=FakeResponse(payload, content_length=False),
    ):
        path = download_update(info, progress_callback=events.append)
    try:
        assert any(event.total_bytes is None for event in events if event.phase == "downloading")
    finally:
        path.unlink(missing_ok=True)
        path.parent.rmdir()


def test_download_cancel_deletes_partial_archive():
    payload = b"x" * 20
    info = UpdateInfo("2.5.0", "https://example.com/update.zip", hashlib.sha256(payload).hexdigest(), [])
    cancel = threading.Event()

    def cancel_after_first_chunk(event):
        if event.phase == "downloading" and event.downloaded_bytes >= 4:
            cancel.set()

    with patch("update_manager.urllib.request.urlopen", return_value=FakeResponse(payload)):
        with self.assertRaises(UpdateCancelled) as caught:
            download_update(
                info,
                cancel_event=cancel,
                progress_callback=cancel_after_first_chunk,
                chunk_size=4,
            )
    assert not caught.exception.path.exists()


def test_verification_cancel_deletes_complete_archive():
    payload = b"x" * 20
    info = UpdateInfo("2.5.0", "https://example.com/update.zip", hashlib.sha256(payload).hexdigest(), [])
    cancel = threading.Event()

    def cancel_during_verify(event):
        if event.phase == "verifying":
            cancel.set()

    with patch("update_manager.urllib.request.urlopen", return_value=FakeResponse(payload)):
        with self.assertRaises(UpdateCancelled) as caught:
            download_update(
                info,
                cancel_event=cancel,
                progress_callback=cancel_during_verify,
                chunk_size=4,
            )
    assert not caught.exception.path.exists()
```

- [ ] **Step 3: Run the update-manager tests and verify they fail**

Run:

```powershell
python -m unittest tests.test_update_manager -v
```

Expected: failures because `DownloadProgress`, `UpdateCancelled`, and callback parameters do not exist.

- [ ] **Step 4: Implement the progress model and cancellation exception**

Add:

```python
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
```

Add helper functions:

```python
def _raise_if_cancelled(cancel_event: threading.Event | None, path: Path) -> None:
    if cancel_event is not None and cancel_event.is_set():
        path.unlink(missing_ok=True)
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
```

- [ ] **Step 5: Extend `verify_sha256()` and `download_update()`**

Use these signatures:

```python
def verify_sha256(
    path: Path,
    expected: str,
    cancel_event: threading.Event | None = None,
    progress_callback: Callable[[DownloadProgress], None] | None = None,
    total_bytes: int | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
```

```python
def download_update(
    info: UpdateInfo,
    timeout: float = 60.0,
    cancel_event: threading.Event | None = None,
    progress_callback: Callable[[DownloadProgress], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    chunk_size: int = 1024 * 1024,
) -> Path:
```

Requirements for the implementation:

- Parse `Content-Length` as a positive integer or use `None`.
- Report a `downloading` event after every written chunk.
- Report `verifying` before and during SHA-256 reading.
- Report `verified` only after a matching digest.
- On cancellation or any exception, delete the temporary ZIP.
- Preserve the existing default behavior when no callback or cancellation event is supplied.

- [ ] **Step 6: Run update-manager tests**

Run:

```powershell
python -m unittest tests.test_update_manager -v
```

Expected: all update-manager tests pass.

- [ ] **Step 7: Commit download progress and cancellation**

```powershell
git add update_manager.py tests/test_update_manager.py
git commit -m "feat: report and cancel update downloads"
```

### Task 3: Build The Visual Update Window And Main-Window Lock

**Files:**
- Modify: `launcher_gui.py`
- Modify: `launcher_core.py`
- Test: `tests/test_launcher_core.py`

- [ ] **Step 1: Add failing tests for presentation data**

Add a pure presentation helper test:

```python
from launcher_core import build_update_progress_text


def test_update_progress_text_contains_stable_download_metrics():
    text = build_update_progress_text(
        downloaded_bytes=5 * 1024 * 1024,
        total_bytes=20 * 1024 * 1024,
        average_bytes_per_second=2 * 1024 * 1024,
        estimated_seconds_remaining=7.4,
    )
    assert text.downloaded == "5.0 MB / 20.0 MB"
    assert text.speed == "2.0 MB/s"
    assert text.remaining == "约 8 秒"
    assert text.percent_text == "25%"
    assert text.progress_value == 0.25
    assert not text.indeterminate


def test_update_progress_text_handles_unknown_total():
    text = build_update_progress_text(4096, None, 1024, None)
    assert text.downloaded == "4.0 KB"
    assert text.percent_text == "下载中"
    assert text.progress_value == 0.0
    assert text.indeterminate
```

- [ ] **Step 2: Run the focused presentation tests and verify they fail**

Run:

```powershell
python -m unittest tests.test_launcher_core.LauncherCoreTests.test_update_progress_text_contains_stable_download_metrics tests.test_launcher_core.LauncherCoreTests.test_update_progress_text_handles_unknown_total -v
```

Expected: import error because `build_update_progress_text` does not exist.

- [ ] **Step 3: Implement `UpdateProgressText`**

Add:

```python
@dataclass(frozen=True)
class UpdateProgressText:
    downloaded: str
    speed: str
    remaining: str
    percent_text: str
    progress_value: float
    indeterminate: bool


def build_update_progress_text(
    downloaded_bytes: int,
    total_bytes: int | None,
    average_bytes_per_second: float,
    estimated_seconds_remaining: float | None,
) -> UpdateProgressText:
    if total_bytes is None or total_bytes <= 0:
        return UpdateProgressText(
            downloaded=format_byte_count(downloaded_bytes),
            speed=format_download_speed(average_bytes_per_second),
            remaining=format_remaining_time(None),
            percent_text="下载中",
            progress_value=0.0,
            indeterminate=True,
        )
    value = min(1.0, max(0.0, downloaded_bytes / total_bytes))
    return UpdateProgressText(
        downloaded=f"{format_byte_count(downloaded_bytes)} / {format_byte_count(total_bytes)}",
        speed=format_download_speed(average_bytes_per_second),
        remaining=format_remaining_time(estimated_seconds_remaining),
        percent_text=f"{round(value * 100)}%",
        progress_value=value,
        indeterminate=False,
    )
```

- [ ] **Step 4: Replace the current compact update window**

In `launcher_gui.py`, expand the window to approximately `560x500` and create:

- Version header.
- Four stage labels.
- `CTkProgressBar`.
- Percent label.
- Downloaded-size label.
- Average-speed label.
- Remaining-time label.
- Status/error description label.
- One primary action button.

Store widget references on `LauncherGui`:

```python
self.update_progress_bar: ctk.CTkProgressBar | None = None
self.update_percent_label: ctk.CTkLabel | None = None
self.update_size_label: ctk.CTkLabel | None = None
self.update_speed_label: ctk.CTkLabel | None = None
self.update_remaining_label: ctk.CTkLabel | None = None
self.update_stage_labels: dict[str, ctk.CTkLabel] = {}
self.update_cancel_event: threading.Event | None = None
self.pending_progress: DownloadProgress | None = None
self.last_progress_render_at = 0.0
self.update_status = "checking"
```

- [ ] **Step 5: Implement stage rendering and one-second progress refresh**

Add methods:

```python
def set_update_status(self, status: str, *, message: str = "") -> None:
    self.update_status = status
    if self.update_status_label is not None:
        self.update_status_label.configure(
            text=message or build_update_status_text(status, self.version)
        )
    stage_order = ["downloading", "verifying", "preparing_install", "updater_started"]
    active_index = stage_order.index(status) if status in stage_order else -1
    for index, stage in enumerate(stage_order):
        label = self.update_stage_labels.get(stage)
        if label is None:
            continue
        if active_index >= 0 and index < active_index:
            label.configure(text_color="#176342")
        elif index == active_index:
            label.configure(text_color="#F05A28")
        else:
            label.configure(text_color="#7B8790")
    if self.update_action_button is not None:
        if status == "available":
            self.update_action_button.configure(
                text="立即更新",
                state="normal",
                command=self.start_update_download,
            )
        elif can_cancel_update(status):
            self.update_action_button.configure(
                text="停止更新",
                state="normal",
                command=self.stop_update_download,
            )
        elif status in {"cancelled", "failed"}:
            self.update_action_button.configure(
                text="重新开始更新",
                state="normal",
                command=self.start_update_download,
            )
        else:
            self.update_action_button.configure(
                text="正在安装…",
                state="disabled",
                command=lambda: None,
            )


def queue_update_progress(self, progress: DownloadProgress) -> None:
    self.pending_progress = progress


def refresh_update_progress(self) -> None:
    progress = self.pending_progress
    if progress is not None and self._update_window_is_open():
        now = time.monotonic()
        phase_changed = progress.phase != self.last_rendered_progress_phase
        if phase_changed or now - self.last_progress_render_at >= 1.0:
            presentation = build_update_progress_text(
                progress.downloaded_bytes,
                progress.total_bytes,
                progress.average_bytes_per_second,
                progress.estimated_seconds_remaining,
            )
            self.update_size_label.configure(text=presentation.downloaded)
            self.update_speed_label.configure(text=presentation.speed)
            self.update_remaining_label.configure(text=presentation.remaining)
            self.update_percent_label.configure(text=presentation.percent_text)
            if presentation.indeterminate:
                self.update_progress_bar.configure(mode="indeterminate")
                self.update_progress_bar.start()
            else:
                self.update_progress_bar.stop()
                self.update_progress_bar.configure(mode="determinate")
                self.update_progress_bar.set(presentation.progress_value)
            if progress.phase == "verifying":
                self.set_update_status("verifying", message="正在校验更新包完整性…")
            elif progress.phase == "verified":
                self.set_update_status("preparing_install", message="校验完成，正在准备安装…")
            self.last_rendered_progress_phase = progress.phase
            self.last_progress_render_at = now
    self.root.after(250, self.refresh_update_progress)
```

Rules:

- Poll pending progress every 250 ms.
- Update speed/remaining labels only when at least one second elapsed or phase changed.
- Use determinate mode when total is known.
- Use indeterminate mode when total is unknown.
- Stop indeterminate animation when leaving download state.
- Render completed/current/future stage colors from the approved palette.

- [ ] **Step 6: Implement the blocking overlay**

Add:

```python
def lock_main_window_for_update(self) -> None:
    self.update_overlay = ctk.CTkFrame(
        self.root,
        fg_color="#101820",
        corner_radius=0,
    )
    self.update_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
    ctk.CTkLabel(
        self.update_overlay,
        text="软件正在更新\\n请在更新窗口中操作",
        text_color="#FFFFFF",
        font=ctk.CTkFont(size=22, weight="bold"),
    ).place(relx=0.5, rely=0.5, anchor="center")
    self.update_window.lift()


def unlock_main_window_after_update(self) -> None:
    if self.update_overlay is not None:
        self.update_overlay.destroy()
        self.update_overlay = None
```

Update `on_close()` so an active update raises the update window instead of closing the application.

- [ ] **Step 7: Unify automatic and manual update entry points**

Replace `offer_update()` confirmation behavior:

- Call `open_update_window(info=info)`.
- Set status to `available`.
- Do not display `messagebox.askyesno`.
- Reuse the same `start_update_download()` method for automatic and manual checks.

Implement:

```python
def start_update_download(self) -> None:
    if self.manual_update_info is None:
        return
    if not self.operation_gate.begin_update():
        self.set_update_status("failed", message="整理任务或其他更新正在运行，请等待完成后再试。")
        return
    self.update_cancel_event = threading.Event()
    self.lock_main_window_for_update()
    self.set_update_status("downloading")
    threading.Thread(target=self._download_update_worker, daemon=True).start()


def stop_update_download(self) -> None:
    if self.update_cancel_event is not None and can_cancel_update(self.update_status):
        self.update_cancel_event.set()
```

- [ ] **Step 8: Handle cancellation, failure, and install boundary**

In the worker:

- Pass `cancel_event` and `queue_update_progress` to `download_update()`.
- Catch `UpdateCancelled`, set state `cancelled`, unlock the main window, and call `operation_gate.end_update()`.
- Catch other exceptions, set state `failed`, unlock, and release the gate.
- After verification, set state `preparing_install`, disable cancellation, check installation directory writability, start the updater, then set `updater_started` and close the main application.
- If updater launch fails, remove the lock and restore the retry button.

- [ ] **Step 9: Add GUI smoke tests using a real Tk main loop**

Create a test helper in `tests/test_launcher_gui_smoke.py` that:

- Creates `LauncherGui` with a withdrawn root.
- Replaces network functions with deterministic fakes.
- Opens the update window twice and asserts a single instance.
- Starts a fake update and asserts the overlay exists.
- Calls stop, waits for cancellation, and asserts overlay removal and gate release.
- Sets `preparing_install` and asserts the action button is disabled.
- Calls the window close protocol during download and asserts the window still exists.

Use `root.after()` polling and a hard timeout; never use `time.sleep()` on the Tk main thread.

- [ ] **Step 10: Run launcher tests and smoke tests**

Run:

```powershell
python -m unittest tests.test_launcher_core tests.test_launcher_gui_smoke -v
python -m py_compile launcher_gui.py launcher_core.py
```

Expected: all tests pass and syntax compilation succeeds.

- [ ] **Step 11: Commit the main update experience**

```powershell
git add launcher_gui.py launcher_core.py tests/test_launcher_core.py tests/test_launcher_gui_smoke.py
git commit -m "feat: visualize and cancel update downloads"
```

### Task 4: Add Installer Progress And Rollback Reporting

**Files:**
- Modify: `updater.py`
- Modify: `updater.spec`
- Test: `tests/test_updater.py`

- [ ] **Step 1: Write failing tests for install progress**

Add:

```python
from updater import InstallProgress, apply_update_package


def test_apply_update_reports_backup_install_and_complete_phases():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        install = root / "install"
        install.mkdir()
        (install / "program.txt").write_text("old", encoding="utf-8")
        package = root / "update.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("program.txt", "new")
            archive.writestr("extra.txt", "extra")
        events: list[InstallProgress] = []

        apply_update_package(package, install, progress_callback=events.append)

        phases = [event.phase for event in events]
        assert "backing_up" in phases
        assert "installing" in phases
        assert phases[-1] == "complete"
        assert events[-1].completed_files == events[-1].total_files
```

- [ ] **Step 2: Write failing tests for rollback progress and backup retention**

```python
def test_failed_install_reports_rollback_and_backup_path_when_rollback_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        install = root / "install"
        install.mkdir()
        target = install / "program.txt"
        target.write_text("old", encoding="utf-8")
        package = root / "update.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("program.txt", "new")
        events: list[InstallProgress] = []
        real_copy2 = __import__("shutil").copy2
        install_failed = False

        def failing_install_and_restore(source, destination, *args, **kwargs):
            nonlocal install_failed
            source_path = Path(source)
            destination_path = Path(destination)
            if source_path.name == "program.txt" and destination_path == target:
                if not install_failed:
                    install_failed = True
                    target.write_text("partial", encoding="utf-8")
                    raise OSError("install copy failed")
                raise OSError("rollback copy failed")
            return real_copy2(source, destination, *args, **kwargs)

        with patch("updater.shutil.copy2", side_effect=failing_install_and_restore):
            with self.assertRaises(UpdateInstallError) as caught:
                apply_update_package(package, install, progress_callback=events.append)

        assert "rolling_back" in [event.phase for event in events]
        assert caught.exception.backup_dir.exists()
        assert caught.exception.rollback_error is not None
        assert target.read_text(encoding="utf-8") == "partial"
```

The fake must fail once while copying the package into the install directory and again while restoring the backup.

- [ ] **Step 3: Run updater tests and verify they fail**

Run:

```powershell
python -m unittest tests.test_updater -v
```

Expected: failures because progress models and `UpdateInstallError` do not exist.

- [ ] **Step 4: Implement install progress and structured errors**

Add:

```python
InstallPhase = Literal[
    "waiting",
    "backing_up",
    "installing",
    "rolling_back",
    "complete",
]


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
    ):
        super().__init__(str(install_error))
        self.install_error = install_error
        self.backup_dir = backup_dir
        self.rollback_error = rollback_error
```

Extend:

```python
def apply_update_package(
    package: Path,
    install_dir: Path,
    progress_callback: Callable[[InstallProgress], None] | None = None,
) -> None:
```

Requirements:

- Build the source-file list before copying so total count is stable.
- Report backup and install progress for each file.
- Report rollback progress in reverse replacement order.
- Delete backup only after complete success or successful rollback.
- Preserve backup when rollback itself fails.
- Raise `UpdateInstallError` with both errors and backup path.

- [ ] **Step 5: Run updater core tests**

Run:

```powershell
python -m unittest tests.test_updater -v
```

Expected: all updater tests pass.

- [ ] **Step 6: Build the installer GUI**

In `updater.py`, add `UpdaterWindow` using CustomTkinter:

- `480x320`, non-resizable.
- Title `正在更新 Windows 文件整理助手`.
- Stage title, description, progress bar, file counter.
- Window close protocol disabled during waiting/install/rollback.
- Worker thread calls `wait_for_process()` and `apply_update_package()`.
- Worker queues `InstallProgress`; Tk polls and renders it.
- Success displays `更新完成，即将重新启动`, waits 1 second, launches `--restart`, and closes.
- Failure after successful rollback displays the error and enables `关闭`.
- Failure with rollback failure displays both errors plus backup directory.

Keep the command-line arguments unchanged:

```text
--package
--install-dir
--parent-pid
--restart
```

- [ ] **Step 7: Bundle installer UI dependencies**

Change `updater.spec` to collect CustomTkinter exactly as the main app spec does:

```python
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
tmp_ret = collect_all("customtkinter")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]
```

Pass those lists into `Analysis`.

- [ ] **Step 8: Run updater syntax and behavior tests**

Run:

```powershell
python -m py_compile updater.py
python -m unittest tests.test_updater -v
```

Expected: syntax succeeds and all updater tests pass.

- [ ] **Step 9: Commit installer visualization**

```powershell
git add updater.py updater.spec tests/test_updater.py
git commit -m "feat: show installer and rollback progress"
```

### Task 5: Update Documentation And Run Integrated Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update user documentation**

Document:

- Automatic and manual checks open the same update window.
- Download progress, size, average speed, and remaining time refresh every second.
- `停止更新` is available only during download and verification.
- Stopping deletes the temporary ZIP and does not modify program files.
- The main window is locked during an active update.
- Installation cannot be stopped.
- `updater.exe` shows backup, install, rollback, and restart status.
- Preserved user files and log files.

- [ ] **Step 2: Run the full test suite and syntax checks**

Run:

```powershell
python -m unittest discover -s tests -v
python -m py_compile file_helper.py launcher_gui.py launcher_core.py update_manager.py updater.py config_manager.py
git diff --check
```

Expected: all tests pass, syntax checks succeed, and `git diff --check` reports no errors.

- [ ] **Step 3: Run source GUI smoke tests**

Run:

```powershell
py launcher_gui.py
```

Verify manually on a disposable run:

- Update window matches the approved visual hierarchy.
- Progress numbers refresh once per second.
- Main-window overlay blocks interaction.
- Stop returns to `更新已停止`.
- Restart begins at 0%.
- Automatic and manual checks reuse one window.

Do not click `立即更新` against the currently installed workspace during this source smoke test; use the fake-network smoke test for download behavior.

- [ ] **Step 4: Commit documentation**

```powershell
git add README.md
git commit -m "docs: explain visual update workflow"
```

### Task 6: Build And Validate The Windows Release Artifacts

**Files:**
- Generated: `dist/Windows文件整理助手/`
- Generated: `dist/updater.exe`
- Generated: `Windows文件整理助手-v2.4.5/`
- Generated: `Windows-file-organizer-v2.4.5.zip`
- Generated: `update.json`

- [ ] **Step 1: Re-run verification immediately before packaging**

Run:

```powershell
python -m unittest discover -s tests
python -m py_compile file_helper.py launcher_gui.py launcher_core.py update_manager.py updater.py config_manager.py
```

Expected: zero failures.

- [ ] **Step 2: Build without user site-packages**

Run:

```powershell
$env:PYTHONPATH = (Resolve-Path '.\.tools\pyinstaller_deps').Path
python -s -m PyInstaller --noconfirm --clean Windows文件整理助手.spec
python -s -m PyInstaller --noconfirm --clean updater.spec
```

Expected: both builds exit with code 0. Confirm `_internal` does not contain unrelated `numpy` or `PIL` directories unless a future intentional dependency requires them.

- [ ] **Step 3: Assemble the foldered release**

Create a new versioned folder containing:

```text
Windows文件整理助手.exe
_internal\
updater.exe
file_helper.py
config_manager.py
config.default.yaml
user_config.yaml
updater.py
README.md
VERSION.txt
```

Use `user_config.example.yaml` as the packaged `user_config.yaml`.

- [ ] **Step 4: Check release-file synchronization**

Run:

```powershell
python tools\check_release_sync.py --release .\Windows文件整理助手-v2.4.5
```

Expected: every checked companion file reports `一致`.

- [ ] **Step 5: Test packaged windows**

Launch the packaged main EXE and verify:

- Main window opens.
- Update window opens.
- Update controls render correctly.

Launch `updater.exe` only through a disposable test harness with a temporary install directory and dummy parent process. Verify its progress window appears and completes without touching the real release folder.

- [ ] **Step 6: Create and validate the release ZIP**

Create the ZIP with the versioned release folder as the single top-level directory. Generate `update.json` with the new version, release download URL, SHA-256, and Chinese release notes.

Run:

```powershell
python -c "import json; from pathlib import Path; from update_manager import parse_update_manifest, verify_sha256; data=json.loads(Path('update.json').read_text(encoding='utf-8-sig')); info=parse_update_manifest(data); archive=Path('Windows-file-organizer-v2.4.5.zip'); print(info.version, verify_sha256(archive, info.sha256))"
```

Expected: `2.4.5 True`.

- [ ] **Step 7: Perform a real temporary upgrade**

Use `apply_update_package()` on a temporary install tree containing:

- An old `VERSION.txt`.
- A custom `user_config.yaml`.
- A custom `launcher_settings.json`.
- Sample log files.

Expected:

- Version changes to the new version.
- Both EXEs and `_internal` exist.
- User configuration, settings, and logs remain byte-for-byte unchanged.

- [ ] **Step 8: Publish only after explicit user request**

Before GitHub publication:

- Verify current branch, remote, authentication, tag absence, and Release absence.
- Commit only source, tests, docs, and version metadata.
- Do not commit release folders, ZIP files, `update.json`, or unrelated `package-lock.json`.
- Push `main` and the annotated version tag.
- Create the GitHub Release with the ZIP and `update.json`.
- Read `releases/latest/download/update.json` and verify the public version and SHA-256.
