import hashlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from update_manager import (
    DownloadProgress,
    UpdateInfo,
    UpdateCancelled,
    download_update,
    fetch_update_info_with_retry,
    is_newer_version,
    parse_update_manifest,
    verify_sha256,
)


class FakeResponse:
    def __init__(self, payload: bytes, content_length: str | None = None):
        self.stream = io.BytesIO(payload)
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def read(self, size: int) -> bytes:
        return self.stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class ControlledClock:
    def __init__(self, *values: float):
        self.values = iter(values)
        self.last = values[-1]

    def __call__(self) -> float:
        self.last = next(self.values, self.last)
        return self.last


class FailingReader:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size: int) -> bytes:
        raise RuntimeError("read failed")


class DeferredCancelEvent:
    def __init__(self):
        self.armed = False
        self.checks_after_arm = 0

    def set(self) -> None:
        self.armed = True

    def is_set(self) -> bool:
        if not self.armed:
            return False
        self.checks_after_arm += 1
        return self.checks_after_arm >= 3


class UpdateManagerTests(unittest.TestCase):
    def download_directory(self, root: str) -> Path:
        directory = Path(root) / "download"
        directory.mkdir()
        return directory

    def test_semantic_version_comparison(self):
        self.assertTrue(is_newer_version("2.4.0", "2.3"))
        self.assertTrue(is_newer_version("2.10", "2.9.9"))
        self.assertFalse(is_newer_version("2.3.0", "2.3"))
        self.assertFalse(is_newer_version("2.2.9", "2.3"))

    def test_manifest_requires_https_zip_and_sha256(self):
        info = parse_update_manifest(
            {
                "version": "2.4.0",
                "download_url": "https://example.com/app.zip",
                "sha256": "a" * 64,
                "notes": ["新增配置页"],
            }
        )
        self.assertEqual(info, UpdateInfo("2.4.0", "https://example.com/app.zip", "a" * 64, ["新增配置页"]))
        with self.assertRaises(ValueError):
            parse_update_manifest({"version": "2.4", "download_url": "http://bad/app.zip", "sha256": "x"})

    def test_verify_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "update.zip"
            path.write_bytes(b"payload")
            expected = hashlib.sha256(b"payload").hexdigest()
            self.assertTrue(verify_sha256(path, expected))
            self.assertFalse(verify_sha256(path, "0" * 64))

    def test_download_reports_total_average_speed_and_remaining_time(self):
        payload = b"x" * 12
        info = UpdateInfo(
            "2.5.0",
            "https://example.com/update.zip",
            hashlib.sha256(payload).hexdigest(),
            [],
        )
        events: list[DownloadProgress] = []
        clock = ControlledClock(10.0, 11.0, 12.0, 14.0)

        with tempfile.TemporaryDirectory() as tmp:
            download_dir = self.download_directory(tmp)
            with (
                patch(
                    "update_manager.urllib.request.urlopen",
                    return_value=FakeResponse(payload, str(len(payload))),
                ),
                patch("update_manager.tempfile.mkdtemp", return_value=str(download_dir)),
            ):
                path = download_update(
                    info,
                    progress_callback=events.append,
                    clock=clock,
                    chunk_size=4,
                )

            download_events = [event for event in events if event.phase == "downloading"]
            self.assertEqual([event.downloaded_bytes for event in download_events], [4, 8, 12])
            self.assertEqual(
                [event.average_bytes_per_second for event in download_events],
                [4.0, 4.0, 3.0],
            )
            self.assertEqual(
                [event.estimated_seconds_remaining for event in download_events],
                [2.0, 1.0, 0.0],
            )
            self.assertEqual(download_events[-1].total_bytes, 12)
            self.assertEqual(download_events[-1].elapsed_seconds, 4.0)
            self.assertEqual(download_events[-1].average_bytes_per_second, 3.0)
            self.assertEqual(download_events[-1].estimated_seconds_remaining, 0.0)
            self.assertEqual(events[-1].phase, "verified")
            self.assertTrue(download_dir.exists())
            path.unlink()
            download_dir.rmdir()

        self.assertFalse(Path(tmp).exists())

    def test_download_initial_clock_error_removes_temp_dir_and_propagates(self):
        payload = b"payload"
        info = UpdateInfo(
            "2.5.0",
            "https://example.com/update.zip",
            hashlib.sha256(payload).hexdigest(),
            [],
        )

        def fail_clock() -> float:
            raise RuntimeError("clock failed")

        with tempfile.TemporaryDirectory() as tmp:
            download_dir = self.download_directory(tmp)
            with patch(
                "update_manager.tempfile.mkdtemp",
                return_value=str(download_dir),
            ):
                with self.assertRaisesRegex(RuntimeError, "clock failed"):
                    download_update(info, clock=fail_clock)

            self.assertFalse(download_dir.exists())

        self.assertFalse(Path(tmp).exists())

    def test_download_without_content_length_reports_unknown_total(self):
        payload = b"payload"
        info = UpdateInfo(
            "2.5.0",
            "https://example.com/update.zip",
            hashlib.sha256(payload).hexdigest(),
            [],
        )
        events: list[DownloadProgress] = []

        with tempfile.TemporaryDirectory() as tmp:
            download_dir = self.download_directory(tmp)
            with (
                patch(
                    "update_manager.urllib.request.urlopen",
                    return_value=FakeResponse(payload),
                ),
                patch("update_manager.tempfile.mkdtemp", return_value=str(download_dir)),
            ):
                path = download_update(info, progress_callback=events.append)

            self.assertTrue(
                all(
                    event.total_bytes is None
                    for event in events
                    if event.phase == "downloading"
                )
            )
            path.unlink()
            download_dir.rmdir()

        self.assertFalse(Path(tmp).exists())

    def test_non_positive_or_invalid_content_length_reports_unknown_total(self):
        payload = b"payload"
        info = UpdateInfo(
            "2.5.0",
            "https://example.com/update.zip",
            hashlib.sha256(payload).hexdigest(),
            [],
        )

        for content_length in ("0", "-1", "invalid"):
            with self.subTest(content_length=content_length):
                events: list[DownloadProgress] = []
                with tempfile.TemporaryDirectory() as tmp:
                    download_dir = self.download_directory(tmp)
                    with (
                        patch(
                            "update_manager.urllib.request.urlopen",
                            return_value=FakeResponse(payload, content_length),
                        ),
                        patch(
                            "update_manager.tempfile.mkdtemp",
                            return_value=str(download_dir),
                        ),
                    ):
                        path = download_update(info, progress_callback=events.append)

                    download_events = [
                        event for event in events if event.phase == "downloading"
                    ]
                    self.assertIsNone(download_events[-1].total_bytes)
                    path.unlink()
                    download_dir.rmdir()

                self.assertFalse(Path(tmp).exists())

    def test_download_cancel_deletes_partial_archive(self):
        payload = b"x" * 20
        info = UpdateInfo(
            "2.5.0",
            "https://example.com/update.zip",
            hashlib.sha256(payload).hexdigest(),
            [],
        )
        cancel = threading.Event()

        def cancel_after_first_chunk(event: DownloadProgress) -> None:
            if event.phase == "downloading" and event.downloaded_bytes >= 4:
                cancel.set()

        with tempfile.TemporaryDirectory() as tmp:
            download_dir = self.download_directory(tmp)
            with (
                patch(
                    "update_manager.urllib.request.urlopen",
                    return_value=FakeResponse(payload, str(len(payload))),
                ),
                patch("update_manager.tempfile.mkdtemp", return_value=str(download_dir)),
            ):
                with self.assertRaises(UpdateCancelled) as caught:
                    download_update(
                        info,
                        cancel_event=cancel,
                        progress_callback=cancel_after_first_chunk,
                        chunk_size=4,
                    )

            self.assertFalse(caught.exception.path.exists())
            self.assertFalse(download_dir.exists())

        self.assertFalse(Path(tmp).exists())

    def test_verification_cancel_deletes_complete_archive(self):
        payload = b"x" * 20
        info = UpdateInfo(
            "2.5.0",
            "https://example.com/update.zip",
            hashlib.sha256(payload).hexdigest(),
            [],
        )
        cancel = threading.Event()

        def cancel_during_verify(event: DownloadProgress) -> None:
            if event.phase == "verifying":
                cancel.set()

        with tempfile.TemporaryDirectory() as tmp:
            download_dir = self.download_directory(tmp)
            with (
                patch(
                    "update_manager.urllib.request.urlopen",
                    return_value=FakeResponse(payload, str(len(payload))),
                ),
                patch("update_manager.tempfile.mkdtemp", return_value=str(download_dir)),
            ):
                with self.assertRaises(UpdateCancelled) as caught:
                    download_update(
                        info,
                        cancel_event=cancel,
                        progress_callback=cancel_during_verify,
                        chunk_size=4,
                    )

            self.assertFalse(caught.exception.path.exists())
            self.assertFalse(download_dir.exists())

        self.assertFalse(Path(tmp).exists())

    def test_sha256_failure_deletes_downloaded_archive(self):
        payload = b"payload"
        info = UpdateInfo(
            "2.5.0",
            "https://example.com/update.zip",
            "0" * 64,
            [],
        )

        with tempfile.TemporaryDirectory() as tmp:
            download_dir = self.download_directory(tmp)
            with (
                patch(
                    "update_manager.urllib.request.urlopen",
                    return_value=FakeResponse(payload, str(len(payload))),
                ),
                patch("update_manager.tempfile.mkdtemp", return_value=str(download_dir)),
            ):
                with self.assertRaises(ValueError):
                    download_update(info)

            self.assertFalse(download_dir.exists())

        self.assertFalse(Path(tmp).exists())

    def test_cancel_after_final_verifying_event_skips_verified_and_removes_temp_dir(self):
        payload = b"payload"
        info = UpdateInfo(
            "2.5.0",
            "https://example.com/update.zip",
            hashlib.sha256(payload).hexdigest(),
            [],
        )
        cancel = DeferredCancelEvent()
        events: list[DownloadProgress] = []

        def cancel_after_final_verifying(event: DownloadProgress) -> None:
            events.append(event)
            if (
                event.phase == "verifying"
                and event.downloaded_bytes == len(payload)
            ):
                cancel.set()

        with tempfile.TemporaryDirectory() as tmp:
            download_dir = self.download_directory(tmp)
            with (
                patch(
                    "update_manager.urllib.request.urlopen",
                    return_value=FakeResponse(payload, str(len(payload))),
                ),
                patch("update_manager.tempfile.mkdtemp", return_value=str(download_dir)),
            ):
                with self.assertRaises(UpdateCancelled):
                    download_update(
                        info,
                        cancel_event=cancel,
                        progress_callback=cancel_after_final_verifying,
                    )

            self.assertNotIn("verified", [event.phase for event in events])
            self.assertFalse(download_dir.exists())

        self.assertFalse(Path(tmp).exists())

    def test_cancel_during_final_clock_skips_verified_and_removes_temp_dir(self):
        payload = b"payload"
        info = UpdateInfo(
            "2.5.0",
            "https://example.com/update.zip",
            hashlib.sha256(payload).hexdigest(),
            [],
        )
        cancel = threading.Event()
        events: list[DownloadProgress] = []
        clock_calls = 0

        def cancel_on_final_clock() -> float:
            nonlocal clock_calls
            clock_calls += 1
            if clock_calls == 5:
                cancel.set()
            return float(clock_calls)

        with tempfile.TemporaryDirectory() as tmp:
            download_dir = self.download_directory(tmp)
            with (
                patch(
                    "update_manager.urllib.request.urlopen",
                    return_value=FakeResponse(payload, str(len(payload))),
                ),
                patch("update_manager.tempfile.mkdtemp", return_value=str(download_dir)),
            ):
                with self.assertRaises(UpdateCancelled) as caught:
                    download_update(
                        info,
                        cancel_event=cancel,
                        progress_callback=events.append,
                        clock=cancel_on_final_clock,
                    )

            self.assertEqual(clock_calls, 5)
            self.assertNotIn("verified", [event.phase for event in events])
            self.assertFalse(caught.exception.path.exists())
            self.assertFalse(download_dir.exists())

        self.assertFalse(Path(tmp).exists())

    def test_verify_sha256_reports_before_and_during_reading(self):
        payload = b"x" * (1024 * 1024 + 4)
        events: list[DownloadProgress] = []

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "update.zip"
            path.write_bytes(payload)
            result = verify_sha256(
                path,
                hashlib.sha256(payload).hexdigest(),
                progress_callback=events.append,
                total_bytes=len(payload),
                clock=ControlledClock(5.0, 6.0, 7.0),
            )

        self.assertTrue(result)
        self.assertEqual([event.phase for event in events], ["verifying"] * 3)
        self.assertEqual(
            [event.downloaded_bytes for event in events],
            [0, 1024 * 1024, len(payload)],
        )

    def test_verify_sha256_callback_error_preserves_archive_and_propagates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "update.zip"
            path.write_bytes(b"payload")

            def fail_progress(_event: DownloadProgress) -> None:
                raise RuntimeError("progress failed")

            with self.assertRaisesRegex(RuntimeError, "progress failed"):
                verify_sha256(
                    path,
                    hashlib.sha256(b"payload").hexdigest(),
                    progress_callback=fail_progress,
                )

            self.assertTrue(path.exists())

    def test_verify_sha256_initial_clock_error_preserves_archive_and_propagates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "update.zip"
            path.write_bytes(b"payload")

            def fail_clock() -> float:
                raise RuntimeError("clock failed")

            with self.assertRaisesRegex(RuntimeError, "clock failed"):
                verify_sha256(
                    path,
                    hashlib.sha256(b"payload").hexdigest(),
                    clock=fail_clock,
                )

            self.assertTrue(path.exists())

    def test_verify_sha256_read_error_preserves_archive_and_propagates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "update.zip"
            path.write_bytes(b"payload")

            with (
                patch.object(Path, "open", return_value=FailingReader()),
                self.assertRaisesRegex(RuntimeError, "read failed"),
            ):
                verify_sha256(
                    path,
                    hashlib.sha256(b"payload").hexdigest(),
                )

            self.assertTrue(path.exists())

    def test_verify_sha256_zero_or_backward_clock_reports_zero_speed(self):
        payload = b"x" * (1024 * 1024 + 4)

        for times in ((10.0, 10.0, 10.0), (10.0, 9.0, 8.0)):
            with self.subTest(times=times):
                events: list[DownloadProgress] = []
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "update.zip"
                    path.write_bytes(payload)
                    self.assertTrue(
                        verify_sha256(
                            path,
                            hashlib.sha256(payload).hexdigest(),
                            progress_callback=events.append,
                            total_bytes=len(payload),
                            clock=ControlledClock(*times),
                        )
                    )

                self.assertEqual(
                    [event.elapsed_seconds for event in events],
                    [0.0, 0.0, 0.0],
                )
                self.assertEqual(
                    [event.average_bytes_per_second for event in events],
                    [0.0, 0.0, 0.0],
                )
                self.assertEqual(
                    [event.estimated_seconds_remaining for event in events],
                    [None, None, None],
                )

    def test_utf8_bom_manifest_can_be_decoded(self):
        payload = b"\xef\xbb\xbf" + json.dumps(
            {
                "version": "2.4.1",
                "download_url": "https://example.com/app.zip",
                "sha256": "a" * 64,
                "notes": [],
            }
        ).encode("utf-8")
        info = parse_update_manifest(json.loads(payload.decode("utf-8-sig")))
        self.assertEqual(info.version, "2.4.1")

    def test_update_check_retries_transient_failures(self):
        calls = []
        sleeps = []
        expected = UpdateInfo("2.4.2", "https://example.com/app.zip", "a" * 64, [])

        def fetcher():
            calls.append(1)
            if len(calls) < 3:
                raise TimeoutError("temporary timeout")
            return expected

        result = fetch_update_info_with_retry(
            attempts=3,
            retry_delay=0.25,
            fetcher=fetcher,
            sleeper=sleeps.append,
        )

        self.assertEqual(result, expected)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [0.25, 0.25])

    def test_update_check_raises_after_all_attempts_fail(self):
        calls = []

        def fetcher():
            calls.append(1)
            raise TimeoutError("still unavailable")

        with self.assertRaisesRegex(TimeoutError, "still unavailable"):
            fetch_update_info_with_retry(
                attempts=2,
                retry_delay=0,
                fetcher=fetcher,
                sleeper=lambda _delay: None,
            )
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
