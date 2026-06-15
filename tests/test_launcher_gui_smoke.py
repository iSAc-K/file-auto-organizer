from __future__ import annotations

import tempfile
import threading
import unittest
from queue import Empty
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import customtkinter as ctk

from launcher_core import (
    ApplyHistoryState,
    EMPTY_HISTORY_TEXT,
    HistoryRun,
    SETTINGS_NAME,
    app_base_dir,
)
from launcher_gui import LauncherGui
from update_manager import DownloadProgress, UpdateCancelled


class LauncherGuiSmokeTests(unittest.TestCase):
    TIMEOUT_MS = 2500

    @classmethod
    def setUpClass(cls) -> None:
        cls.settings_temp_dir = tempfile.TemporaryDirectory()
        cls.app_settings_path = app_base_dir() / SETTINGS_NAME
        cls.app_settings_snapshot = cls._read_settings_snapshot(cls.app_settings_path)
        cls.root = ctk.CTk()
        cls.root.withdraw()
        with patch.object(LauncherGui, "check_for_updates_async"):
            cls.gui = LauncherGui(cls.root)
        cls.gui.settings_path = Path(cls.settings_temp_dir.name) / SETTINGS_NAME
        cls.root.after(0, lambda: cls.root.after(10, cls.root.quit))
        cls.root.mainloop()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.root.winfo_exists():
            cls.root.after(300, cls.root.destroy)
            cls.root.mainloop()
        cls.settings_temp_dir.cleanup()

    @staticmethod
    def _read_settings_snapshot(path: Path) -> tuple[bool, bytes]:
        return path.exists(), path.read_bytes() if path.exists() else b""

    def setUp(self) -> None:
        self.saved_gui_state = {
            "python_command": self.gui.python_command.get(),
            "script_path": self.gui.script_path.get(),
            "root_path": self.gui.root_path.get(),
            "config_path": self.gui.config_path.get(),
            "run_mode": self.gui.run_mode.get(),
            "use_archive": bool(self.gui.use_archive.get()),
            "open_result_folder": bool(self.gui.open_result_folder.get()),
            "active_page": self.gui.active_page,
        }
        self.info = SimpleNamespace(version="9.9.9", notes=["测试更新"])
        self.gui.app_closing = False
        self.gui._ensure_ui_poll()

    def tearDown(self) -> None:
        def cleanup() -> None:
            self.gui.config_dirty = False
            self.gui.python_command.set(self.saved_gui_state["python_command"])
            self.gui.script_path.set(self.saved_gui_state["script_path"])
            self.gui.root_path.set(self.saved_gui_state["root_path"])
            self.gui.config_path.set(self.saved_gui_state["config_path"])
            self.gui.use_archive.set(self.saved_gui_state["use_archive"])
            self.gui.open_result_folder.set(self.saved_gui_state["open_result_folder"])
            if self.saved_gui_state["active_page"] == "config":
                self.gui.show_config_page()
            elif self.saved_gui_state["active_page"] == "history":
                with patch(
                    "launcher_gui.load_apply_history",
                    return_value=self.gui.history_state,
                ):
                    self.gui.show_history_page()
            else:
                self.gui.show_task_page(self.saved_gui_state["run_mode"])
            self.gui._set_update_status("latest")
            if self.gui.update_overlay is not None:
                self.gui._release_update_lock()
            self.gui.manual_update_info = None
            while True:
                try:
                    self.gui.ui_event_queue.get_nowait()
                except Empty:
                    break
            self.root.quit()

        self.root.after_idle(cleanup)
        self._run_mainloop_with_timeout()

    def _run_mainloop_with_timeout(self) -> None:
        timed_out = False

        def fail_timeout() -> None:
            nonlocal timed_out
            timed_out = True
            self.root.quit()

        timeout_id = self.root.after(self.TIMEOUT_MS, fail_timeout)
        self.root.mainloop()
        try:
            self.root.after_cancel(timeout_id)
        except Exception:
            pass
        if timed_out:
            self.fail("Tk mainloop smoke test timed out")

    def run_until(self, predicate) -> None:
        def poll() -> None:
            if predicate():
                self.root.quit()
            else:
                self.root.after(10, poll)

        self.root.after_idle(poll)
        self._run_mainloop_with_timeout()

    def run_action(self, action) -> None:
        error: BaseException | None = None

        def invoke() -> None:
            nonlocal error
            try:
                action()
            except BaseException as exc:
                error = exc
            finally:
                self.root.after(1, self.root.quit)

        self.root.after(0, invoke)
        self._run_mainloop_with_timeout()
        if error is not None:
            raise error

    def open_window(self) -> None:
        with patch.object(self.gui, "start_manual_update_check"):
            self.run_action(self.gui.open_update_window)

    def assert_app_settings_unchanged(self) -> None:
        self.assertEqual(
            self._read_settings_snapshot(self.app_settings_path),
            self.app_settings_snapshot,
        )

    def test_history_navigation_does_not_write_app_base_settings(self):
        self.gui.root_path.set("C:/history-root")

        with patch(
            "launcher_gui.load_apply_history",
            return_value=ApplyHistoryState(runs=()),
        ):
            self.run_action(self.gui.show_history_page)
            self.run_action(lambda: self.gui.show_task_page("dry-run"))

        self.assert_app_settings_unchanged()
        self.assertTrue(
            self.gui.settings_path.is_relative_to(Path(self.settings_temp_dir.name))
        )
        self.assertTrue(self.gui.settings_path.exists())

    def test_navigation_color_helper_sets_only_requested_page_active(self):
        self.gui._set_navigation_colors("history")

        self.assertEqual(self.gui.history_nav_button.cget("fg_color"), "#F05A28")
        self.assertEqual(self.gui.config_nav_button.cget("fg_color"), "#1B2630")
        for button in self.gui.mode_buttons.values():
            self.assertEqual(button.cget("fg_color"), "#1B2630")

    def test_history_page_starts_hidden_and_entering_without_root_shows_empty_state(self):
        self.gui.root_path.set("")
        self.assertEqual(self.gui.history_page.winfo_manager(), "")

        with patch("launcher_gui.load_apply_history") as loader:
            self.run_action(self.gui.show_history_page)

        loader.assert_not_called()
        self.assertEqual(self.gui.active_page, "history")
        self.assertEqual(self.gui.history_page.winfo_manager(), "grid")
        self.assertEqual(self.gui.task_center.winfo_manager(), "")
        self.assertEqual(self.gui.history_detail_message.cget("text"), EMPTY_HISTORY_TEXT)

    def test_history_page_reloads_every_time_it_is_entered(self):
        self.gui.root_path.set("C:/history-root")

        with patch(
            "launcher_gui.load_apply_history",
            return_value=ApplyHistoryState(runs=()),
        ) as loader:
            self.run_action(self.gui.show_history_page)
            self.run_action(lambda: self.gui.show_task_page("dry-run"))
            self.run_action(self.gui.show_history_page)

        self.assertEqual(loader.call_count, 2)
        self.assertEqual(
            [args.args for args in loader.call_args_list],
            [("C:/history-root",), ("C:/history-root",)],
        )

    def test_history_empty_state_uses_exact_message(self):
        self.gui.root_path.set("C:/history-root")

        with patch(
            "launcher_gui.load_apply_history",
            return_value=ApplyHistoryState(runs=()),
        ):
            self.run_action(self.gui.show_history_page)

        self.assertEqual(self.gui.history_detail_message.cget("text"), EMPTY_HISTORY_TEXT)
        self.assertEqual(self.gui.history_list.get_children(), ())

    def test_history_error_state_shows_complete_error(self):
        self.gui.root_path.set("C:/history-root")
        error = "organizer_run_log.json 无法读取：C:/history-root/organizer_run_log.json：bad json"

        with patch(
            "launcher_gui.load_apply_history",
            return_value=ApplyHistoryState(runs=(), error=error),
        ):
            self.run_action(self.gui.show_history_page)

        self.assertEqual(self.gui.history_detail_message.cget("text"), error)

    def test_history_runs_show_summary_without_default_selection(self):
        self.gui.root_path.set("C:/history-root")
        run = HistoryRun(
            run_id="run-1",
            time="2026-06-15 10:30:00",
            root="C:/history-root",
            status="success",
            status_text="成功",
            has_complete_details=True,
            results=(),
        )

        with patch(
            "launcher_gui.load_apply_history",
            return_value=ApplyHistoryState(runs=(run,)),
        ):
            self.run_action(self.gui.show_history_page)

        items = self.gui.history_list.get_children()
        self.assertEqual(len(items), 1)
        self.assertEqual(
            self.gui.history_list.item(items[0], "values"),
            ("2026-06-15 10:30:00", "C:/history-root", "成功"),
        )
        self.assertEqual(self.gui.history_list.selection(), ())
        self.assertIs(self.gui.history_runs_by_item[items[0]], run)
        self.assertEqual(self.gui.history_detail_message.cget("text"), "请选择一条执行记录")

    def test_cancelled_config_discard_does_not_enter_history(self):
        self.run_action(self.gui.show_config_page)
        self.gui.config_dirty = True

        with (
            patch.object(self.gui, "confirm_discard_config_changes", return_value=False),
            patch("launcher_gui.load_apply_history") as loader,
        ):
            self.run_action(self.gui.show_history_page)

        loader.assert_not_called()
        self.assertEqual(self.gui.active_page, "config")
        self.assertEqual(self.gui.config_page.winfo_manager(), "grid")
        self.assertEqual(self.gui.history_page.winfo_manager(), "")

    def test_task_and_config_pages_hide_history(self):
        with patch(
            "launcher_gui.load_apply_history",
            return_value=ApplyHistoryState(runs=()),
        ):
            self.run_action(self.gui.show_history_page)
        self.run_action(lambda: self.gui.show_task_page("apply"))
        self.assertEqual(self.gui.history_page.winfo_manager(), "")
        self.assertEqual(self.gui.task_center.winfo_manager(), "grid")

        with patch(
            "launcher_gui.load_apply_history",
            return_value=ApplyHistoryState(runs=()),
        ):
            self.run_action(self.gui.show_history_page)
        self.run_action(self.gui.show_config_page)
        self.assertEqual(self.gui.history_page.winfo_manager(), "")
        self.assertEqual(self.gui.config_page.winfo_manager(), "grid")

    def test_history_navigation_colors_are_mutually_exclusive(self):
        with patch(
            "launcher_gui.load_apply_history",
            return_value=ApplyHistoryState(runs=()),
        ):
            self.run_action(self.gui.show_history_page)

        self.assertEqual(self.gui.history_nav_button.cget("fg_color"), "#F05A28")
        self.assertEqual(self.gui.config_nav_button.cget("fg_color"), "#1B2630")
        for button in self.gui.mode_buttons.values():
            self.assertEqual(button.cget("fg_color"), "#1B2630")

        self.run_action(self.gui.show_config_page)
        self.assertEqual(self.gui.config_nav_button.cget("fg_color"), "#F05A28")
        self.assertEqual(self.gui.history_nav_button.cget("fg_color"), "#1B2630")

        self.run_action(lambda: self.gui.show_task_page("undo-last"))
        self.assertEqual(self.gui.history_nav_button.cget("fg_color"), "#1B2630")
        self.assertEqual(self.gui.config_nav_button.cget("fg_color"), "#1B2630")
        self.assertEqual(self.gui.mode_buttons["undo-last"].cget("fg_color"), "#F05A28")

    def test_update_window_is_single_instance(self):
        self.open_window()
        first = self.gui.update_window

        self.run_action(self.gui.open_update_window)

        self.assertIs(self.gui.update_window, first)

    def test_restart_resets_progress_display_to_zero(self):
        self.open_window()
        self.gui.manual_update_info = self.info
        self.gui.update_downloaded_label.configure(text="5.0 MB / 20.0 MB")
        self.gui.update_percent_label.configure(text="25%")

        with patch("launcher_gui.threading.Thread"):
            self.run_action(self.gui.start_update_download)

        self.assertEqual(self.gui.update_downloaded_label.cget("text"), "0 B")
        self.assertEqual(self.gui.update_percent_label.cget("text"), "0%")
        self.gui._release_update_lock()

    def test_automatic_check_does_not_replace_active_download(self):
        self.open_window()
        active_info = SimpleNamespace(version="8.8.8", notes=["active"])
        self.gui.manual_update_info = active_info
        self.gui._set_update_window_state("downloading", "8.8.8")

        self.run_action(lambda: self.gui.offer_update(self.info))

        self.assertIs(self.gui.manual_update_info, active_info)
        self.assertEqual(self.gui.update_latest_version, "8.8.8")

    def test_main_close_during_download_only_raises_update_window(self):
        self.open_window()
        self.gui._set_update_window_state("downloading", "9.9.9")

        with patch.object(self.gui, "_raise_update_window") as raised:
            self.run_action(self.gui.on_close)

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
            self.run_action(self.gui.start_update_download)
            self.run_until(lambda: self.gui.update_overlay is not None)
            self.assertFalse(self.gui.operation_gate.begin_task())
            self.assertIs(self.root.grab_current(), self.gui.update_window)
            self.run_action(self.gui.stop_update_download)
            self.run_until(lambda: self.gui.update_status == "cancelled")

        self.assertTrue(cancelled.is_set())
        self.assertIsNone(self.gui.update_overlay)
        self.assertIsNone(self.root.grab_current())
        self.assertTrue(self.gui.operation_gate.begin_task())
        self.gui.operation_gate.end_task()

    def test_completed_download_and_stop_interleave_at_uncancellable_boundary(self):
        self.open_window()
        download_returned = threading.Event()
        allow_worker_to_continue = threading.Event()

        def fake_download(_info, cancel_event, progress_callback):
            download_returned.set()
            if not allow_worker_to_continue.wait(2.0):
                raise TimeoutError("worker was not released")
            return Path("update.zip")

        with (
            patch("launcher_gui.download_update", side_effect=fake_download),
            patch("launcher_gui.os.access", return_value=False),
        ):
            self.gui.manual_update_info = self.info
            self.run_action(self.gui.start_update_download)
            self.assertTrue(download_returned.wait(2.0))
            allow_worker_to_continue.set()
            self.run_until(lambda: self.gui.update_status == "preparing_install")
            cancel_event = self.gui.update_cancel_event
            self.run_action(self.gui.stop_update_download)

        self.assertEqual(self.gui.update_status, "preparing_install")
        self.assertFalse(cancel_event.is_set())
        self.assertIs(self.root.grab_current(), self.gui.update_window)

    def test_preparing_install_disables_action_button(self):
        self.open_window()

        self.run_action(
            lambda: self.gui._set_update_window_state("preparing_install", "9.9.9")
        )

        self.assertEqual(self.gui.update_action_button.cget("text"), "正在安装…")
        self.assertEqual(self.gui.update_action_button.cget("state"), "disabled")

    def test_failed_update_button_says_restart_update(self):
        self.open_window()

        self.run_action(lambda: self.gui._set_update_window_state("failed"))

        self.assertEqual(self.gui.update_action_button.cget("text"), "重新开始更新")

    def test_overlay_uses_exact_dark_color(self):
        self.run_action(self.gui._show_update_overlay)

        self.assertEqual(self.gui.update_overlay.cget("fg_color"), "#101820")

    def test_close_protocol_keeps_window_open_while_downloading(self):
        self.open_window()
        window = self.gui.update_window
        self.gui._set_update_window_state("downloading", "9.9.9")

        self.run_action(self.gui.close_update_window)

        self.assertIs(self.gui.update_window, window)
        self.assertTrue(bool(window.winfo_exists()))

    def test_automatic_and_manual_discovery_share_download_entry(self):
        self.open_window()

        with (
            patch("launcher_gui.messagebox.askyesno", return_value=False) as ask,
            patch.object(self.gui, "start_update_download") as start,
        ):
            self.run_action(lambda: self.gui.offer_update(self.info))
            self.assertEqual(self.gui.update_status, "available")
            self.run_action(self.gui.update_action_button.invoke)
            self.assertEqual(start.call_count, 1)
        ask.assert_not_called()

        with patch.object(self.gui, "start_update_download") as start:
            self.run_action(lambda: self.gui._finish_manual_update_check(self.info))
            self.run_action(self.gui.update_action_button.invoke)
            self.assertEqual(start.call_count, 1)

    def test_background_check_workers_never_call_tk(self):
        auto_token = self.gui._next_update_check_generation()
        manual_token = self.gui._next_update_check_generation()

        with (
            patch("launcher_gui.fetch_update_info_with_retry", return_value=self.info),
            patch.object(self.root, "after", side_effect=AssertionError("Tk API from worker")),
        ):
            auto = threading.Thread(
                target=self.gui._check_for_updates_worker,
                args=(auto_token,),
            )
            manual = threading.Thread(
                target=self.gui._manual_update_check_worker,
                args=(manual_token,),
            )
            auto.start()
            manual.start()
            auto.join(2.0)
            manual.join(2.0)

        self.assertFalse(auto.is_alive())
        self.assertFalse(manual.is_alive())

    def test_background_check_failures_never_call_tk(self):
        auto_token = self.gui._next_update_check_generation()
        manual_token = self.gui._next_update_check_generation()

        with (
            patch(
                "launcher_gui.fetch_update_info_with_retry",
                side_effect=OSError("network failed"),
            ),
            patch.object(self.gui, "_log_update_check_failure"),
            patch.object(self.root, "after", side_effect=AssertionError("Tk API from worker")),
        ):
            auto = threading.Thread(
                target=self.gui._check_for_updates_worker,
                args=(auto_token,),
            )
            manual = threading.Thread(
                target=self.gui._manual_update_check_worker,
                args=(manual_token,),
            )
            auto.start()
            manual.start()
            auto.join(2.0)
            manual.join(2.0)

        self.assertFalse(auto.is_alive())
        self.assertFalse(manual.is_alive())

    def test_download_worker_failure_never_calls_tk(self):
        self.open_window()
        self.gui.update_cancel_event = threading.Event()

        with (
            patch("launcher_gui.download_update", return_value=Path("update.zip")),
            patch("launcher_gui.os.access", return_value=True),
            patch("launcher_gui.Path.exists", return_value=True),
            patch("launcher_gui.shutil.copy2"),
            patch("launcher_gui.subprocess.Popen", side_effect=OSError("launch failed")),
            patch.object(self.root, "after", side_effect=AssertionError("Tk API from worker")),
        ):
            worker = threading.Thread(
                target=self.gui._download_and_start_update,
                args=(self.info,),
            )
            worker.start()
            worker.join(2.0)

        self.assertFalse(worker.is_alive())
        self.assertFalse(self.gui.ui_event_queue.empty())

    def test_old_success_and_failure_check_results_are_ignored(self):
        self.open_window()
        old_token = self.gui._next_update_check_generation()
        latest_token = self.gui._next_update_check_generation()
        latest_info = SimpleNamespace(version="9.9.8", notes=["latest"])
        old_info = SimpleNamespace(version="9.9.7", notes=["old"])

        self.gui._enqueue_ui_event("check_success", latest_token, latest_info, True)
        self.gui._enqueue_ui_event("check_success", old_token, old_info, True)
        self.gui._enqueue_ui_event("check_failure", old_token, "old failure", True)
        self.run_until(lambda: self.gui.manual_update_info is latest_info)

        self.assertIs(self.gui.manual_update_info, latest_info)
        self.assertEqual(self.gui.update_status, "available")

    def test_newer_auto_check_clears_superseded_manual_check_latch(self):
        self.open_window()
        manual_started = threading.Event()
        release_manual = threading.Event()
        auto_started = threading.Event()
        release_auto = threading.Event()
        latest_info = SimpleNamespace(version=self.gui.version, notes=[])
        stale_manual_info = SimpleNamespace(version="9.9.9", notes=["stale manual"])

        def fetch():
            if not manual_started.is_set():
                manual_started.set()
                if not release_manual.wait(2.0):
                    raise TimeoutError("manual check was not released")
                return stale_manual_info
            auto_started.set()
            if not release_auto.wait(2.0):
                raise TimeoutError("auto check was not released")
            return latest_info

        with patch("launcher_gui.fetch_update_info_with_retry", side_effect=fetch):
            self.run_action(self.gui.start_manual_update_check)
            self.assertTrue(manual_started.wait(2.0))
            self.run_action(self.gui.check_for_updates_async)
            self.assertTrue(auto_started.wait(2.0))
            release_manual.set()
            self.run_until(lambda: not self.gui.manual_update_check_running)
            self.assertEqual(self.gui.update_status, "checking")
            release_auto.set()
            self.run_until(lambda: self.gui.update_status == "latest")

        self.assertFalse(self.gui.manual_update_check_running)
        self.assertIsNone(self.gui.manual_update_info)
        self.assertEqual(self.gui.update_status, "latest")

    def test_failed_latest_check_clears_stale_download_info(self):
        self.open_window()
        token = self.gui._next_update_check_generation()
        self.gui.manual_update_info = self.info

        self.gui._enqueue_ui_event("check_failure", token, "network failed", True)
        self.run_until(lambda: self.gui.update_status == "failed")

        self.assertIsNone(self.gui.manual_update_info)

    def test_manual_check_result_after_update_window_close_is_dropped(self):
        self.open_window()
        token = self.gui._next_update_check_generation()
        self.gui._set_update_window_state("checking")
        self.run_action(self.gui.close_update_window)

        self.gui._enqueue_ui_event("check_success", token, self.info, True)
        self.run_action(self.gui._poll_ui_events)

        self.assertIsNone(self.gui.manual_update_info)
        self.assertFalse(self.gui._update_window_is_open())

    def test_check_result_after_download_start_is_ignored(self):
        self.open_window()
        token = self.gui._next_update_check_generation()
        stale_info = SimpleNamespace(version="9.9.7", notes=["stale"])
        self.gui.manual_update_info = self.info

        with patch("launcher_gui.threading.Thread"):
            self.run_action(self.gui.start_update_download)
        self.gui._enqueue_ui_event("check_success", token, stale_info, True)
        self.run_action(self.gui._poll_ui_events)

        self.assertEqual(self.gui.update_status, "downloading")
        self.assertIs(self.gui.manual_update_info, self.info)
        self.gui._release_update_lock()

    def test_ui_events_are_dropped_after_app_closing(self):
        self.run_action(self.gui._prepare_app_close)
        self.gui._enqueue_ui_event("check_success", 999, self.info, True)

        self.gui._poll_ui_events()

        self.assertTrue(self.gui.ui_event_queue.empty())
        self.assertIsNone(self.gui.manual_update_info)
        self.assertIsNone(self.gui.ui_poll_after_id)
        self.gui.app_closing = False

    def test_ui_poll_is_scheduled_once_and_not_rescheduled_after_close(self):
        self.assertIsNotNone(self.gui.ui_poll_after_id)
        current_id = self.gui.ui_poll_after_id

        self.gui._ensure_ui_poll()
        self.assertEqual(self.gui.ui_poll_after_id, current_id)

        self.gui.app_closing = True
        self.gui._poll_ui_events()
        self.assertIsNone(self.gui.ui_poll_after_id)
        self.gui.app_closing = False
        self.gui._ensure_ui_poll()

    def test_ui_poll_uses_approved_250ms_interval(self):
        if self.gui.ui_poll_after_id is not None:
            self.root.after_cancel(self.gui.ui_poll_after_id)
            self.gui.ui_poll_after_id = None

        with patch.object(self.root, "after", return_value="poll-id") as after:
            self.gui._ensure_ui_poll()

        after.assert_called_once_with(250, self.gui._poll_ui_events)
        self.gui.ui_poll_after_id = None


if __name__ == "__main__":
    unittest.main()
