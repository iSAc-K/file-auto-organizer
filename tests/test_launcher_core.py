from pathlib import Path
import json
import os
import tempfile
import unittest

import launcher_core
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
    def write_history_log(self, root: Path, runs: object) -> Path:
        path = root / "organizer_run_log.json"
        path.write_text(
            json.dumps({"runs": runs}, ensure_ascii=False),
            encoding="utf-8-sig",
        )
        return path

    def complete_history_run(self, **overrides):
        result = {
            "result_id": "result-1",
            "final_name": "1~2-0501-0502-军牌-3单-5个",
            "target_path": r"D:\orders\1~2-0501-0502-军牌-3单-5个",
            "source_items": [
                {
                    "original_name": "0501 军牌 1单2个",
                    "source_type": "folder",
                    "source_path": r"D:\orders\0501 军牌 1单2个",
                },
                {
                    "original_name": "0502 军牌 2单3个.zip",
                    "source_type": "archive",
                    "source_path": r"D:\orders\0502 军牌 2单3个.zip",
                },
            ],
            "merged": True,
            "date": "0501-0502",
            "category": "军牌",
            "orders": 3,
            "quantity": 5,
            "matched_keywords": ["军牌", "金属军牌"],
            "status": "success",
            "error_reason": "",
        }
        run = {
            "run_id": "run-1",
            "mode": "apply",
            "time": "2026-06-15 12:00:00",
            "root": r"D:\orders",
            "status": "success",
            "history_snapshot": {
                "schema_version": 1,
                "results": [result],
            },
        }
        run.update(overrides)
        return run

    def test_parse_history_run_builds_immutable_complete_model(self):
        run = launcher_core.parse_history_run(self.complete_history_run())

        self.assertEqual(run.run_id, "run-1")
        self.assertEqual(run.status_text, "成功")
        self.assertTrue(run.has_complete_details)
        self.assertIsInstance(run.results, tuple)
        result = run.results[0]
        self.assertEqual(result.orders, 3)
        self.assertEqual(result.quantity, 5)
        self.assertEqual(result.status_text, "成功")
        self.assertIsInstance(result.source_items, tuple)
        self.assertIsInstance(result.matched_keywords, tuple)
        self.assertEqual(result.matched_keywords, ("军牌", "金属军牌"))
        with self.assertRaises(Exception):
            run.run_id = "changed"

    def test_parse_legacy_running_run_has_no_guessed_details(self):
        run = launcher_core.parse_history_run(
            {
                "run_id": "legacy",
                "time": "2026-06-15 11:00:00",
                "root": r"D:\orders",
                "status": "running",
            }
        )

        self.assertEqual(run.status_text, "执行中断")
        self.assertFalse(run.has_complete_details)
        self.assertEqual(run.results, ())
        self.assertEqual(launcher_core.LEGACY_HISTORY_TEXT, "旧版记录，详情不完整")

    def test_parse_pending_result_maps_interrupted_status(self):
        result = self.complete_history_run()["history_snapshot"]["results"][0]
        result["status"] = "pending"

        parsed = launcher_core.parse_history_result(result)

        self.assertEqual(parsed.status_text, "执行中断")

    def test_schema_v1_rejects_invalid_run_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for status in ("", "custom", "pending", "skipped"):
                with self.subTest(status=status):
                    self.write_history_log(
                        root,
                        [self.complete_history_run(status=status)],
                    )

                    state = launcher_core.load_apply_history(root)

                    self.assertEqual(state.runs, ())
                    self.assertIn("organizer_run_log.json", state.error)

    def test_schema_v1_rejects_invalid_result_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for status in ("", "custom", "running", "partial"):
                with self.subTest(status=status):
                    run = self.complete_history_run()
                    run["history_snapshot"]["results"][0]["status"] = status
                    self.write_history_log(root, [run])

                    state = launcher_core.load_apply_history(root)

                    self.assertEqual(state.runs, ())
                    self.assertIn("organizer_run_log.json", state.error)

    def test_legacy_run_without_snapshot_rejects_invalid_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_run = self.complete_history_run(status="")
            legacy_run.pop("history_snapshot")
            self.write_history_log(root, [legacy_run])

            state = launcher_core.load_apply_history(root)

            self.assertEqual(state.runs, ())
            self.assertIn("organizer_run_log.json", state.error)

    def test_load_apply_history_missing_file_is_empty_and_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            state = launcher_core.load_apply_history(root)

            self.assertEqual(state, launcher_core.ApplyHistoryState(runs=()))
            self.assertEqual(
                launcher_core.EMPTY_HISTORY_TEXT,
                "暂无执行历史，完成一次执行整理后会显示在这里",
            )
            self.assertFalse((root / "organizer_run_log.json").exists())

    def test_load_apply_history_empty_runs_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_history_log(root, [])

            state = launcher_core.load_apply_history(root)

            self.assertEqual(state.runs, ())
            self.assertEqual(state.error, "")

    def test_load_apply_history_damaged_json_returns_path_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "organizer_run_log.json"
            path.write_text("{bad json", encoding="utf-8")

            state = launcher_core.load_apply_history(root)

            self.assertEqual(state.runs, ())
            self.assertIn("organizer_run_log.json 无法读取", state.error)
            self.assertIn(str(path.resolve()), state.error)

    def test_load_apply_history_requires_object_and_runs_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "organizer_run_log.json"
            for payload in ([], {"runs": {}}):
                with self.subTest(payload=payload):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    state = launcher_core.load_apply_history(root)
                    self.assertEqual(state.runs, ())
                    self.assertIn("organizer_run_log.json 无法读取", state.error)

    def test_single_bad_result_discards_all_runs_with_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = self.complete_history_run(run_id="good")
            bad = self.complete_history_run(run_id="bad")
            bad["history_snapshot"]["results"][0]["orders"] = "not-an-int"
            self.write_history_log(root, [good, bad])

            state = launcher_core.load_apply_history(root)

            self.assertEqual(state.runs, ())
            self.assertIn("organizer_run_log.json 无法读取", state.error)

    def test_invalid_snapshot_field_types_discard_all_runs(self):
        mutations = {
            "merged string": lambda run: run["history_snapshot"]["results"][0].__setitem__(
                "merged", "false"
            ),
            "orders string": lambda run: run["history_snapshot"]["results"][0].__setitem__(
                "orders", "3"
            ),
            "result text number": lambda run: run["history_snapshot"]["results"][0].__setitem__(
                "final_name", 123
            ),
            "source text number": lambda run: run["history_snapshot"]["results"][0][
                "source_items"
            ][0].__setitem__("original_name", 123),
            "keyword non-string": lambda run: run["history_snapshot"]["results"][0].__setitem__(
                "matched_keywords", ["军牌", 123]
            ),
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for label, mutate in mutations.items():
                with self.subTest(label=label):
                    bad = self.complete_history_run(run_id="bad")
                    mutate(bad)
                    self.write_history_log(
                        root,
                        [self.complete_history_run(run_id="good"), bad],
                    )

                    state = launcher_core.load_apply_history(root)

                    self.assertEqual(state.runs, ())
                    self.assertIn("organizer_run_log.json 无法读取", state.error)

    def test_all_history_text_fields_require_strings(self):
        fields = (
            ("run", "run_id"),
            ("run", "time"),
            ("run", "root"),
            ("run", "status"),
            ("result", "result_id"),
            ("result", "final_name"),
            ("result", "target_path"),
            ("result", "date"),
            ("result", "category"),
            ("result", "status"),
            ("result", "error_reason"),
            ("source", "original_name"),
            ("source", "source_type"),
            ("source", "source_path"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for location, field in fields:
                with self.subTest(location=location, field=field):
                    bad = self.complete_history_run()
                    if location == "run":
                        bad[field] = 123
                    elif location == "result":
                        bad["history_snapshot"]["results"][0][field] = 123
                    else:
                        bad["history_snapshot"]["results"][0]["source_items"][0][field] = 123
                    self.write_history_log(root, [bad])

                    state = launcher_core.load_apply_history(root)

                    self.assertEqual(state.runs, ())
                    self.assertIn("organizer_run_log.json 无法读取", state.error)

    def test_all_schema_v1_fields_are_required(self):
        fields = (
            ("run", "run_id"),
            ("run", "time"),
            ("run", "root"),
            ("run", "status"),
            ("result", "result_id"),
            ("result", "final_name"),
            ("result", "target_path"),
            ("result", "source_items"),
            ("result", "merged"),
            ("result", "date"),
            ("result", "category"),
            ("result", "orders"),
            ("result", "quantity"),
            ("result", "matched_keywords"),
            ("result", "status"),
            ("result", "error_reason"),
            ("source", "original_name"),
            ("source", "source_type"),
            ("source", "source_path"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for location, field in fields:
                with self.subTest(location=location, field=field):
                    bad = self.complete_history_run()
                    if location == "run":
                        bad.pop(field)
                    elif location == "result":
                        bad["history_snapshot"]["results"][0].pop(field)
                    else:
                        bad["history_snapshot"]["results"][0]["source_items"][0].pop(field)
                    self.write_history_log(root, [bad])

                    state = launcher_core.load_apply_history(root)

                    self.assertEqual(state.runs, ())
                    self.assertIn("organizer_run_log.json 无法读取", state.error)

    def test_counts_require_exact_integer_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for field in ("orders", "quantity"):
                for value in (True, 3.0, "3"):
                    with self.subTest(field=field, value=value):
                        bad = self.complete_history_run()
                        bad["history_snapshot"]["results"][0][field] = value
                        self.write_history_log(root, [bad])

                        state = launcher_core.load_apply_history(root)

                        self.assertEqual(state.runs, ())
                        self.assertIn("organizer_run_log.json 无法读取", state.error)

    def test_merged_requires_exact_boolean_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for value in (0, 1, "false"):
                with self.subTest(value=value):
                    bad = self.complete_history_run()
                    bad["history_snapshot"]["results"][0]["merged"] = value
                    self.write_history_log(root, [bad])

                    state = launcher_core.load_apply_history(root)

                    self.assertEqual(state.runs, ())
                    self.assertIn("organizer_run_log.json 无法读取", state.error)

    def test_non_object_run_discards_all_runs_with_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_history_log(root, [self.complete_history_run(), []])

            state = launcher_core.load_apply_history(root)

            self.assertEqual(state.runs, ())
            self.assertIn("organizer_run_log.json 无法读取", state.error)

    def test_non_integral_numbers_are_invalid_history_counts(self):
        result = self.complete_history_run()["history_snapshot"]["results"][0]
        result["quantity"] = 2.5

        with self.assertRaises(ValueError):
            launcher_core.parse_history_result(result)

    def test_unsupported_snapshot_schema_is_legacy(self):
        run_data = self.complete_history_run()
        run_data["history_snapshot"]["schema_version"] = 2

        run = launcher_core.parse_history_run(run_data)

        self.assertFalse(run.has_complete_details)
        self.assertEqual(run.results, ())

    def test_non_exact_integer_snapshot_schema_is_legacy(self):
        for schema_version in (True, 1.0, "1"):
            with self.subTest(schema_version=schema_version):
                run_data = self.complete_history_run()
                run_data["history_snapshot"]["schema_version"] = schema_version

                run = launcher_core.parse_history_run(run_data)

                self.assertFalse(run.has_complete_details)
                self.assertEqual(run.results, ())

    def test_snapshot_results_must_be_list_when_schema_supported(self):
        run_data = self.complete_history_run()
        run_data["history_snapshot"]["results"] = {}

        with self.assertRaises(ValueError):
            launcher_core.parse_history_run(run_data)

    def test_load_apply_history_filters_explicit_non_apply_and_keeps_missing_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_apply = self.complete_history_run(run_id="old-apply")
            dry_run = self.complete_history_run(run_id="dry", mode="dry-run")
            legacy_apply = self.complete_history_run(run_id="legacy-apply")
            legacy_apply.pop("mode")
            self.write_history_log(root, [old_apply, dry_run, legacy_apply])

            state = launcher_core.load_apply_history(root)

            self.assertEqual(
                [run.run_id for run in state.runs],
                ["legacy-apply", "old-apply"],
            )

    def test_non_string_mode_discards_all_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_history_log(
                root,
                [self.complete_history_run(run_id="good"), self.complete_history_run(mode=True)],
            )

            state = launcher_core.load_apply_history(root)

            self.assertEqual(state.runs, ())
            self.assertIn("organizer_run_log.json 无法读取", state.error)

    def test_unknown_string_mode_discards_all_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for mode in ("bogus", ""):
                with self.subTest(mode=mode):
                    self.write_history_log(
                        root,
                        [
                            self.complete_history_run(run_id="good"),
                            self.complete_history_run(run_id="bad", mode=mode),
                        ],
                    )

                    state = launcher_core.load_apply_history(root)

                    self.assertEqual(state.runs, ())
                    self.assertIn("organizer_run_log.json 无法读取", state.error)

    def test_valid_non_apply_mode_is_filtered_without_hiding_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_history_log(
                root,
                [
                    self.complete_history_run(run_id="apply"),
                    self.complete_history_run(run_id="dry", mode="dry-run"),
                    self.complete_history_run(run_id="undo", mode="undo-last"),
                ],
            )

            state = launcher_core.load_apply_history(root)

            self.assertEqual([run.run_id for run in state.runs], ["apply"])
            self.assertEqual(state.error, "")

    def test_filtered_run_still_requires_valid_top_level_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            invalid_dry_run = self.complete_history_run(mode="dry-run")
            invalid_dry_run["run_id"] = 123
            self.write_history_log(
                root,
                [self.complete_history_run(run_id="apply"), invalid_dry_run],
            )

            state = launcher_core.load_apply_history(root)

            self.assertEqual(state.runs, ())
            self.assertIn("organizer_run_log.json 无法读取", state.error)

    def test_file_helper_snapshot_round_trips_through_history_loader(self):
        from file_helper import PlanGroup, WorkItem, build_history_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = WorkItem("folder", "0501 军牌 1单2个", root / "first", root)
            second = WorkItem("archive", "0502 军牌 2单3个.zip", root / "second", root)
            first.detection.matched_keyword = "军牌"
            second.detection.matched_keyword = "金属军牌"
            group = PlanGroup(
                items=[first, second],
                is_merge=True,
                sequence_range="1~2",
                date_label="0501-0502",
                category="军牌",
                orders=3,
                quantity=5,
                final_name="1~2-0501-0502-军牌-3单-5个",
                target_path=root / "1~2-0501-0502-军牌-3单-5个",
                naming_template="",
                reason="test",
            )
            snapshot = build_history_snapshot([group])
            snapshot["results"][0]["status"] = "success"
            self.write_history_log(
                root,
                [
                    {
                        "run_id": "producer-run",
                        "mode": "apply",
                        "time": "2026-06-15 12:00:00",
                        "root": str(root.resolve()),
                        "status": "success",
                        "history_snapshot": snapshot,
                    }
                ],
            )

            state = launcher_core.load_apply_history(root)

            result = state.runs[0].results[0]
            self.assertEqual(state.error, "")
            self.assertEqual(result.final_name, group.final_name)
            self.assertEqual(result.target_path, str(group.target_path.resolve()))
            self.assertEqual(result.orders, group.orders)
            self.assertEqual(result.quantity, group.quantity)
            self.assertEqual(result.matched_keywords, ("军牌", "金属军牌"))
            self.assertEqual(
                tuple(item.original_name for item in result.source_items),
                (first.original_name, second.original_name),
            )

    def test_source_items_and_matched_keywords_must_be_lists(self):
        result = self.complete_history_run()["history_snapshot"]["results"][0]
        for field in ("source_items", "matched_keywords"):
            with self.subTest(field=field):
                invalid = dict(result)
                invalid[field] = ()
                with self.assertRaises(ValueError):
                    launcher_core.parse_history_result(invalid)

    def test_history_parse_helpers_require_objects(self):
        for helper in (
            launcher_core.parse_history_source_item,
            launcher_core.parse_history_result,
            launcher_core.parse_history_run,
        ):
            with self.subTest(helper=helper.__name__):
                with self.assertRaises(ValueError):
                    helper([])

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

    def test_build_update_progress_text_treats_non_positive_total_as_unknown(self):
        for total_bytes in (0, -1):
            with self.subTest(total_bytes=total_bytes):
                progress = DownloadProgress(
                    phase="downloading",
                    downloaded_bytes=5 * 1024 * 1024,
                    total_bytes=total_bytes,
                    elapsed_seconds=2.5,
                    average_bytes_per_second=2 * 1024 * 1024,
                    estimated_seconds_remaining=7.6,
                )

                text = build_update_progress_text(progress)

                self.assertEqual(text.downloaded, "5.0 MB")
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
