from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import customtkinter as ctk

from launcher_gui import LauncherGui
from update_manager import DownloadProgress, UpdateCancelled


class LauncherGuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = ctk.CTk()
        cls.root.withdraw()
        with patch.object(LauncherGui, "check_for_updates_async"):
            cls.gui = LauncherGui(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.root.winfo_exists():
            cls.root.destroy()

    def setUp(self) -> None:
        self.info = SimpleNamespace(version="9.9.9", notes=["测试更新"])

    def tearDown(self) -> None:
        self.gui.update_status = "latest"
        if self.gui._update_window_is_open():
            self.gui.close_update_window()
        if self.gui.update_overlay is not None:
            self.gui._release_update_lock()
        self.gui.pending_update_progress = None
        self.gui.pending_update_results.clear()
        self.root.update()

    def spin_until(self, predicate, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Tk mainloop smoke test timed out")

    def open_window(self) -> None:
        with patch.object(self.gui, "start_manual_update_check"):
            self.gui.open_update_window()
        self.root.update()

    def test_update_window_is_single_instance(self):
        self.open_window()
        first = self.gui.update_window

        self.gui.open_update_window()
        self.root.update()

        self.assertIs(self.gui.update_window, first)

    def test_restart_resets_progress_display_to_zero(self):
        self.open_window()
        self.gui.manual_update_info = self.info
        self.gui.update_downloaded_label.configure(text="5.0 MB / 20.0 MB")
        self.gui.update_percent_label.configure(text="25%")

        with patch("launcher_gui.threading.Thread"):
            self.gui.start_update_download()
        self.root.update()

        self.assertEqual(self.gui.update_downloaded_label.cget("text"), "0 B")
        self.assertEqual(self.gui.update_percent_label.cget("text"), "0%")
        self.gui._release_update_lock()

    def test_automatic_check_does_not_replace_active_download(self):
        self.open_window()
        active_info = SimpleNamespace(version="8.8.8", notes=["active"])
        self.gui.manual_update_info = active_info
        self.gui._set_update_window_state("downloading", "8.8.8")

        self.gui.offer_update(self.info)
        self.root.update()

        self.assertIs(self.gui.manual_update_info, active_info)
        self.assertEqual(self.gui.update_latest_version, "8.8.8")

    def test_main_close_during_download_only_raises_update_window(self):
        self.open_window()
        self.gui._set_update_window_state("downloading", "9.9.9")

        with patch.object(self.gui, "_raise_update_window") as raised:
            self.gui.on_close()

        raised.assert_called_once_with()
        self.assertTrue(bool(self.root.winfo_exists()))

    def test_start_update_adds_overlay_and_cancel_releases_gate(self):
        self.open_window()
        cancelled = threading.Event()

        def fake_download(_info, cancel_event, progress_callback):
            progress_callback(
                DownloadProgress("downloading", 1024, 4096, 1.0, 1024.0, 3.0)
            )
            if not cancel_event.wait(2.0):
                raise TimeoutError("cancel event was not set")
            cancelled.set()
            raise UpdateCancelled(Path("unused.zip"))

        with patch("launcher_gui.download_update", side_effect=fake_download):
            self.gui.manual_update_info = self.info
            self.gui.start_update_download()
            self.spin_until(lambda: self.gui.update_overlay is not None)
            self.assertFalse(self.gui.operation_gate.begin_task())
            self.gui.stop_update_download()
            self.spin_until(lambda: self.gui.update_status == "cancelled")

        self.assertTrue(cancelled.is_set())
        self.assertIsNone(self.gui.update_overlay)
        self.assertTrue(self.gui.operation_gate.begin_task())
        self.gui.operation_gate.end_task()

    def test_preparing_install_disables_action_button(self):
        self.open_window()

        self.gui._set_update_window_state("preparing_install", "9.9.9")
        self.root.update()

        self.assertEqual(self.gui.update_action_button.cget("text"), "正在安装…")
        self.assertEqual(self.gui.update_action_button.cget("state"), "disabled")

    def test_close_protocol_keeps_window_open_while_downloading(self):
        self.open_window()
        window = self.gui.update_window
        self.gui._set_update_window_state("downloading", "9.9.9")

        self.gui.close_update_window()
        self.root.update()

        self.assertIs(self.gui.update_window, window)
        self.assertTrue(bool(window.winfo_exists()))

    def test_automatic_and_manual_discovery_share_download_entry(self):
        self.open_window()

        with (
            patch("launcher_gui.messagebox.askyesno", return_value=False) as ask,
            patch.object(self.gui, "start_update_download") as start,
        ):
            self.gui.offer_update(self.info)
            self.root.update()
            self.assertEqual(self.gui.update_status, "available")
            self.gui.update_action_button.invoke()
            self.assertEqual(start.call_count, 1)
        ask.assert_not_called()

        with patch.object(self.gui, "start_update_download") as start:
            self.gui._finish_manual_update_check(self.info)
            self.root.update()
            self.gui.update_action_button.invoke()
            self.assertEqual(start.call_count, 1)


if __name__ == "__main__":
    unittest.main()
