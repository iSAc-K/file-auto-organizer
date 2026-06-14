from pathlib import Path
import os
import tempfile
import unittest

from launcher_core import (
    LauncherSettings,
    OperationGate,
    PREVIEW_COLUMN_MAX_WIDTH,
    PREVIEW_COLUMN_PADDING,
    PREVIEW_COLUMN_WIDTHS,
    build_command,
    build_preview_rows,
    build_safety_status_text,
    build_update_status_text,
    build_update_progress_text,
    can_cancel_update,
    can_close_update_window,
    clean_path_value,
    find_latest_report,
    format_byte_count,
    format_download_speed,
    format_remaining_time,
    format_python_command,
    load_settings,
    ps_quote,
    preview_expanded_width,
    read_version,
    toggle_preview_column,
    undo_log_status,
    default_window_geometry,
    wheel_delta_to_units,
)
from update_manager import DownloadProgress


class LauncherCoreTests(unittest.TestCase):
    def test_build_update_progress_text_for_known_total(self):
        progress = DownloadProgress(
            phase="downloading",
            downloaded_bytes=5 * 1024 * 1024,
            total_bytes=20 * 1024 * 1024,
            elapsed_seconds=2.5,
            average_bytes_per_second=2 * 1024 * 1024,
            estimated_seconds_remaining=7.6,
        )

        text = build_update_progress_text(progress)

        self.assertEqual(text.downloaded, "5.0 MB / 20.0 MB")
        self.assertEqual(text.speed, "2.0 MB/s")
        self.assertEqual(text.remaining, "约 8 秒")
        self.assertEqual(text.percent, "25%")
        self.assertEqual(text.value, 0.25)
        self.assertFalse(text.indeterminate)

    def test_build_update_progress_text_for_unknown_total(self):
        progress = DownloadProgress(
            phase="downloading",
            downloaded_bytes=5 * 1024 * 1024,
            total_bytes=None,
            elapsed_seconds=2.5,
            average_bytes_per_second=2 * 1024 * 1024,
            estimated_seconds_remaining=None,
        )

        text = build_update_progress_text(progress)

        self.assertEqual(text.downloaded, "5.0 MB")
        self.assertEqual(text.speed, "2.0 MB/s")
        self.assertEqual(text.remaining, "计算中")
        self.assertEqual(text.percent, "下载中")
        self.assertEqual(text.value, 0)
        self.assertTrue(text.indeterminate)

    def test_update_progress_formats_byte_counts_and_speed(self):
        self.assertEqual(format_byte_count(0), "0 B")
        self.assertEqual(format_byte_count(1536), "1.5 KB")
        self.assertEqual(format_byte_count(5 * 1024 * 1024), "5.0 MB")
        self.assertEqual(format_download_speed(1536), "1.5 KB/s")

    def test_update_progress_promotes_kilobytes_that_round_to_one_megabyte(self):
        self.assertEqual(format_byte_count(1024 * 1024 - 1), "1.0 MB")

    def test_update_progress_promotes_megabytes_that_round_to_one_gigabyte(self):
        self.assertEqual(format_byte_count(1024 * 1024 * 1024 - 1), "1.0 GB")

    def test_update_progress_formats_remaining_time(self):
        self.assertEqual(format_remaining_time(None), "计算中")
        self.assertEqual(format_remaining_time(4.2), "约 5 秒")
        self.assertEqual(format_remaining_time(75), "约 2 分钟")

    def test_update_window_action_rules_match_status(self):
        for status in ("downloading", "verifying"):
            with self.subTest(status=status):
                self.assertTrue(can_cancel_update(status))
                self.assertFalse(can_close_update_window(status))

        for status in ("preparing_install", "updater_started"):
            with self.subTest(status=status):
                self.assertFalse(can_cancel_update(status))
                self.assertFalse(can_close_update_window(status))

        for status in ("checking", "available", "latest", "failed", "cancelled"):
            with self.subTest(status=status):
                self.assertFalse(can_cancel_update(status))
                self.assertTrue(can_close_update_window(status))

    def test_update_status_text_covers_manual_check_states(self):
        self.assertIn("正在检查", build_update_status_text("checking", "2.4.3"))
        self.assertIn("已是最新版本", build_update_status_text("latest", "2.4.3", "2.4.3"))
        available = build_update_status_text(
            "available",
            "2.4.3",
            "2.4.4",
            ["新增手动检查更新"],
        )
        self.assertIn("发现新版本", available)
        self.assertIn("2.4.4", available)
        self.assertIn("新增手动检查更新", available)
        self.assertIn("检查更新失败", build_update_status_text("failed", "2.4.3", error="网络超时"))
        self.assertIn("正在下载", build_update_status_text("downloading", "2.4.3", "2.4.4"))

    def test_update_status_text_covers_visual_update_states(self):
        verifying = build_update_status_text("verifying", "2.4.3", "2.4.4")
        cancelled = build_update_status_text("cancelled", "2.4.3", "2.4.4")
        preparing = build_update_status_text("preparing_install", "2.4.3", "2.4.4")
        started = build_update_status_text("updater_started", "2.4.3", "2.4.4")

        self.assertIn("校验", verifying)
        self.assertIn("已取消", cancelled)
        self.assertIn("准备安装", preparing)
        self.assertIn("更新程序", started)
        for text in (verifying, cancelled, preparing, started):
            self.assertIn("2.4.3", text)
            self.assertIn("2.4.4", text)

    def test_operation_gate_prevents_update_and_organizer_overlap(self):
        gate = OperationGate()

        self.assertTrue(gate.begin_update())
        self.assertFalse(gate.begin_task())
        gate.end_update()
        self.assertTrue(gate.begin_task())
        self.assertFalse(gate.begin_update())
        gate.end_task()
        self.assertTrue(gate.begin_update())

    def test_preview_column_widths_match_defaults(self):
        self.assertEqual(
            PREVIEW_COLUMN_WIDTHS,
            {
                "序号": 48,
                "原文件夹": 210,
                "识别日期": 82,
                "识别品类": 118,
                "命中关键词": 130,
                "单量": 60,
                "数量": 60,
                "动作": 72,
                "目标名称": 220,
                "状态": 78,
                "原因": 240,
            },
        )
        self.assertEqual(PREVIEW_COLUMN_MAX_WIDTH, 600)
        self.assertEqual(PREVIEW_COLUMN_PADDING, 24)

    def test_preview_expanded_width_keeps_default_for_short_text(self):
        self.assertEqual(preview_expanded_width(210, [24, 80, 120]), 210)
        self.assertEqual(preview_expanded_width(210, []), 210)

    def test_preview_expanded_width_adds_padding_to_longest_text(self):
        self.assertEqual(preview_expanded_width(210, [180, 240, 320]), 344)

    def test_preview_expanded_width_caps_at_maximum(self):
        self.assertEqual(preview_expanded_width(210, [700]), 600)

    def test_toggle_preview_column_allows_multiple_expanded_columns(self):
        expanded: set[str] = set()
        self.assertEqual(toggle_preview_column(expanded, "原因"), {"原因"})
        self.assertEqual(
            toggle_preview_column({"原因"}, "原文件夹"),
            {"原因", "原文件夹"},
        )

    def test_toggle_preview_column_collapses_only_clicked_column(self):
        self.assertEqual(
            toggle_preview_column({"原因", "原文件夹"}, "原因"),
            {"原文件夹"},
        )

    def test_toggle_preview_column_returns_new_set(self):
        expanded = {"原因"}

        updated = toggle_preview_column(expanded, "原文件夹")

        self.assertEqual(expanded, {"原因"})
        self.assertIsNot(updated, expanded)

    def test_ps_quote_escapes_single_quotes(self):
        self.assertEqual(ps_quote(r"D:\client's\root"), r"'D:\client''s\root'")

    def test_clean_path_value_strips_outer_quotes_and_spaces(self):
        self.assertEqual(clean_path_value('  "D:\\incoming"  '), r"D:\incoming")
        self.assertEqual(clean_path_value("  'D:\\incoming'  "), r"D:\incoming")

    def test_format_python_command_uses_call_operator_for_paths(self):
        command = format_python_command(r"C:\Program Files\Python\python.exe")
        self.assertEqual(command, r"& 'C:\Program Files\Python\python.exe'")

    def test_build_dry_run_command_has_no_yes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            script = Path(tmp) / "file_helper.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            config = Path(tmp) / "config.yaml"
            config.write_text("categories: {}\n", encoding="utf-8")

            settings = LauncherSettings(
                python_command="py",
                script_path=str(script),
                root_path=str(root),
                config_path=str(config),
                mode="dry-run",
                archive_enabled=False,
                open_result_folder=False,
            )

            normal_command = build_command(settings)
            mistakenly_confirmed_command = build_command(settings, include_yes=True)

        for command in (normal_command, mistakenly_confirmed_command):
            self.assertIn("--dry-run", command)
            self.assertIn("--config", command)
            self.assertNotIn("--yes", command)
            self.assertNotIn("Start-Process", command)

    def test_build_apply_archive_command_adds_yes_only_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            script = Path(tmp) / "file_helper.py"
            script.write_text("print('ok')\n", encoding="utf-8")

            settings = LauncherSettings(
                python_command="py",
                script_path=str(script),
                root_path=str(root),
                config_path="",
                mode="apply",
                archive_enabled=True,
                open_result_folder=True,
            )

            normal_command = build_command(settings)
            confirmed_command = build_command(settings, include_yes=True)

        self.assertIn("--apply", normal_command)
        self.assertIn("--archive", normal_command)
        self.assertNotIn("--yes", normal_command)
        self.assertIn("--yes", confirmed_command)
        self.assertIn("Start-Process", confirmed_command)

    def test_build_undo_command_omits_config_and_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            script = Path(tmp) / "file_helper.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            config = Path(tmp) / "config.yaml"
            config.write_text("categories: {}\n", encoding="utf-8")

            settings = LauncherSettings(
                python_command="py",
                script_path=str(script),
                root_path=str(root),
                config_path=str(config),
                mode="undo-last",
                archive_enabled=True,
                open_result_folder=False,
            )

            normal_command = build_command(settings)
            confirmed_command = build_command(settings, include_yes=True)

        self.assertIn("--undo-last", normal_command)
        self.assertNotIn("--yes", normal_command)
        self.assertIn("--yes", confirmed_command)
        for command in (normal_command, confirmed_command):
            self.assertNotIn("--config", command)
            self.assertNotIn("--archive", command)
            self.assertNotIn("--apply", command)
            self.assertNotIn("--dry-run", command)

    def test_load_settings_missing_or_damaged_file_returns_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            defaults = LauncherSettings(
                python_command="py",
                script_path="helper.py",
                root_path="",
                config_path="config.yaml",
                mode="dry-run",
                archive_enabled=False,
                open_result_folder=True,
            )
            missing = Path(tmp) / "launcher_settings.json"
            self.assertEqual(load_settings(missing, defaults), defaults)

            missing.write_text("{not json", encoding="utf-8")
            self.assertEqual(load_settings(missing, defaults), defaults)

    def test_build_safety_status_text_reflects_mode_and_archive(self):
        self.assertEqual(
            build_safety_status_text("dry-run", False),
            "当前模式：Dry Run｜不会修改文件｜不会压缩｜不会删除原件",
        )
        self.assertEqual(
            build_safety_status_text("apply", True),
            "当前模式：Apply｜需要确认｜冲突跳过｜不会覆盖已有目标｜压缩：开启｜同名压缩包存在时跳过",
        )
        self.assertEqual(
            build_safety_status_text("undo-last", True),
            "当前模式：Undo｜撤销：仅根据日志执行｜不会猜测路径｜不会覆盖已有路径",
        )

    def test_find_latest_report_prefers_newest_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = root / "整理报告.xlsx"
            newer_dir = root / "logs"
            newer_dir.mkdir()
            newer = newer_dir / "整理报告.xlsx"
            older.write_text("old", encoding="utf-8")
            newer.write_text("new", encoding="utf-8")
            os.utime(older, (100, 100))
            os.utime(newer, (200, 200))

            self.assertEqual(find_latest_report(root), newer)

    def test_undo_log_status_reports_missing_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, message = undo_log_status(Path(tmp))
            self.assertFalse(ok)
            self.assertIn("organizer_run_log.json", message)

    def test_build_preview_rows_uses_dry_run_plan_without_renaming(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            original = root / "0507-WZY-钢片军牌钥匙扣-13单18个"
            original.mkdir()
            config = Path(tmp) / "config.yaml"
            config.write_text(
                "categories:\n"
                "  钢片军牌钥匙扣:\n"
                "    keywords:\n"
                "      - 钢片军牌钥匙扣\n"
                "    merge_enabled: true\n"
                "category_priority:\n"
                "  - 钢片军牌钥匙扣\n",
                encoding="utf-8",
            )

            rows = build_preview_rows(root, config)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].sequence, "1")
            self.assertEqual(rows[0].original_name, original.name)
            self.assertEqual(rows[0].detected_date, "0507")
            self.assertEqual(rows[0].detected_category, "钢片军牌钥匙扣")
            self.assertEqual(rows[0].matched_keyword, "钢片军牌钥匙扣")
            self.assertEqual(rows[0].orders, "13")
            self.assertEqual(rows[0].quantity, "18")
            self.assertEqual(rows[0].status, "planned")
            self.assertTrue(original.exists())
            self.assertFalse((root / f"1-{original.name}").exists())

    def test_read_version_returns_first_version_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            version = Path(tmp) / "VERSION.txt"
            version.write_text("2.2\nrelease_date: 2026-05-28\n", encoding="utf-8")
            self.assertEqual(read_version(Path(tmp)), "2.2")

    def test_wheel_delta_to_units_converts_windows_mousewheel(self):
        self.assertEqual(wheel_delta_to_units(120), -1)
        self.assertEqual(wheel_delta_to_units(-120), 1)
        self.assertEqual(wheel_delta_to_units(240), -2)
        self.assertEqual(wheel_delta_to_units(0), 0)

    def test_default_window_geometry_caps_to_screen_with_minimum_target(self):
        self.assertEqual(default_window_geometry(1920, 1080), "1440x900")
        self.assertEqual(default_window_geometry(1366, 768), "1280x720")
        self.assertEqual(default_window_geometry(1200, 680), "1120x600")


if __name__ == "__main__":
    unittest.main()
