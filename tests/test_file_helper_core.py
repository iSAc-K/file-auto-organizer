import copy
import csv
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import file_helper
from file_helper import (
    DEFAULT_CONFIG,
    PlanGroup,
    REPORT_NAME,
    RUN_LOG_NAME,
    WorkItem,
    absolute_text,
    apply_plan,
    build_history_source_item,
    build_history_snapshot,
    build_plan,
    check_config_diagnostics,
    compress_groups,
    create_apply_run,
    deep_merge,
    detect_dates,
    find_last_undoable_run,
    is_generated_merge_folder_name,
    load_run_log,
    main,
    make_operation,
    safe_write_run_log,
    write_organize_report,
    undo_last,
    update_run_status,
    validate_config,
    zip_path_for_folder,
)


def test_config(overrides=None):
    config = copy.deepcopy(DEFAULT_CONFIG)
    config = deep_merge(
        config,
        {
            "category_priority": ["军牌钥匙扣", "钢片军牌钥匙扣", "军牌项链"],
            "categories": {
                "军牌钥匙扣": {
                    "keywords": ["钥匙扣", "军牌钥匙扣"],
                    "merge_enabled": True,
                },
                "钢片军牌钥匙扣": {
                    "keywords": ["钢片军牌钥匙扣"],
                    "merge_enabled": True,
                },
                "军牌项链": {
                    "keywords": ["军牌项链"],
                    "merge_enabled": True,
                },
            },
            "do_not_merge_keywords": ["样品", "返工", "异常"],
            "naming": {
                "single_keep_original": True,
                "single_template": "{seq}-{clean_original_name}",
                "merged_template": "{seq_range}-{date}-{category}-{orders}单-{quantity}个",
                "custom_text": "",
                "merge_name": "",
            },
            "inner_folder_naming": {"template": "{seq}-{original_name}"},
            "quantity_detection": {"source": "outer_folder_name_only"},
            "conflict": {"target_exists": "skip"},
            "fallback": {
                "unknown_date": "未知日期",
                "unknown_category": "未知产品",
                "default_orders_per_folder": 1,
                "default_quantity_per_order": 1,
            },
        },
    )
    if overrides:
        config = deep_merge(config, overrides)
    validate_config(config)
    return config


def mkdir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=False)
    return path


def read_log_rows(log_path: Path):
    with log_path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_config(path: Path, body: str) -> Path:
    path.write_text(body.strip() + "\n", encoding="utf-8")
    return path


class FileHelperCoreTests(unittest.TestCase):
    def test_build_history_snapshot_aggregates_final_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mkdir(root / "0501 军牌钥匙扣 1单2个")
            mkdir(root / "0505 军牌钥匙扣 2单3个")

            _items, groups = build_plan(root, test_config(), "apply", root / "rename_log.csv")
            snapshot = build_history_snapshot(groups)

        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(len(snapshot["results"]), 1)
        result = snapshot["results"][0]
        group = groups[0]
        self.assertEqual(result["result_id"], "result-1")
        self.assertEqual(
            set(result.keys()),
            {
                "result_id",
                "final_name",
                "target_path",
                "source_items",
                "merged",
                "date",
                "category",
                "orders",
                "quantity",
                "matched_keywords",
                "status",
                "error_reason",
            },
        )
        self.assertEqual(result["final_name"], group.final_name)
        self.assertEqual(result["target_path"], str(group.target_path.resolve()))
        self.assertEqual(result["date"], "0501-0505")
        self.assertEqual(result["category"], "军牌钥匙扣")
        self.assertEqual(result["orders"], 3)
        self.assertEqual(result["quantity"], 5)
        self.assertEqual(result["matched_keywords"], ["军牌钥匙扣"])
        self.assertTrue(result["merged"])
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["error_reason"], "")
        self.assertEqual(
            [item["original_name"] for item in result["source_items"]],
            ["0501 军牌钥匙扣 1单2个", "0505 军牌钥匙扣 2单3个"],
        )

    def test_build_history_snapshot_marks_single_group_not_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mkdir(root / "0507 军牌项链 1单2个")

            _items, groups = build_plan(root, test_config(), "apply", root / "rename_log.csv")
            snapshot = build_history_snapshot(groups)

        result = snapshot["results"][0]
        self.assertFalse(result["merged"])
        self.assertEqual(result["orders"], 1)
        self.assertEqual(result["quantity"], 2)
        self.assertEqual(result["source_items"][0]["source_type"], "folder")

    def test_build_history_source_item_uses_archive_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = WorkItem(
                source_type="archive",
                original_name="0507 军牌项链 1单2个",
                current_path=root / "extracted",
                root=root,
                archive_path=root / "source.zip",
            )

            source_item = build_history_source_item(item)

        self.assertEqual(source_item["original_name"], item.original_name)
        self.assertNotIn("source_name", source_item)
        self.assertEqual(source_item["source_path"], absolute_text(item.archive_path))

    def test_build_history_snapshot_assigns_unique_result_ids_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            groups = []
            for index in range(2):
                item = WorkItem("folder", f"source-{index}", root / f"source-{index}", root)
                groups.append(
                    PlanGroup(
                        items=[item],
                        is_merge=False,
                        sequence_range=str(index + 1),
                        date_label=f"050{index + 1}",
                        category=f"category-{index}",
                        orders=1,
                        quantity=1,
                        final_name=f"result-{index}",
                        target_path=root / f"result-{index}",
                        naming_template="",
                        reason="test",
                    )
                )

            snapshot = build_history_snapshot(groups)

        result_ids = [result["result_id"] for result in snapshot["results"]]
        self.assertEqual(result_ids, ["result-1", "result-2"])
        self.assertEqual(len(result_ids), len(set(result_ids)))

    def test_build_history_snapshot_deduplicates_keywords_in_source_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            items = [
                WorkItem("folder", f"source-{index}", root / f"source-{index}", root)
                for index in range(4)
            ]
            for item, keyword in zip(items, ["甲", "乙", "甲", ""]):
                item.detection.matched_keyword = keyword
            group = PlanGroup(
                items=items,
                is_merge=True,
                sequence_range="1~4",
                date_label="0501-0504",
                category="测试品类",
                orders=4,
                quantity=4,
                final_name="result",
                target_path=root / "result",
                naming_template="",
                reason="test",
            )

            snapshot = build_history_snapshot([group])

        self.assertEqual(snapshot["results"][0]["matched_keywords"], ["甲", "乙"])

    def test_build_history_snapshot_requires_multiple_sources_for_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = WorkItem("folder", "source", root / "source", root)
            group = PlanGroup(
                items=[item],
                is_merge=True,
                sequence_range="1",
                date_label="0507",
                category="测试品类",
                orders=1,
                quantity=2,
                final_name="result",
                target_path=root / "result",
                naming_template="",
                reason="test",
            )

            snapshot = build_history_snapshot([group])

        self.assertFalse(snapshot["results"][0]["merged"])

    def test_create_apply_run_persists_snapshot_and_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mkdir(root / "0507 history source")
            _items, groups = build_plan(root, test_config(), "apply", root / "rename_log.csv")

            _data, run = create_apply_run(root, root / RUN_LOG_NAME, groups)

        self.assertEqual(run["mode"], "apply")
        self.assertEqual(run["history_snapshot"], build_history_snapshot(groups))

    def test_update_history_result_persists_status_and_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mkdir(root / "0507 history source")
            _items, groups = build_plan(root, test_config(), "apply", root / "rename_log.csv")
            data, run = create_apply_run(root, root / RUN_LOG_NAME, groups)

            file_helper.update_history_result(
                root / RUN_LOG_NAME,
                data,
                run,
                "result-1",
                "skipped",
                "target exists",
            )

            saved = load_run_log(root / RUN_LOG_NAME)["runs"][0]["history_snapshot"]["results"][0]

        self.assertEqual(saved["status"], "skipped")
        self.assertEqual(saved["error_reason"], "target exists")

    def test_run_log_keeps_only_latest_one_hundred_apply_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_log_path = Path(tmp) / RUN_LOG_NAME
            runs = [
                {
                    "run_id": f"run-{index}",
                    "root": str(Path(tmp).resolve()),
                    "time": f"2026-06-15 00:{index:02d}:00",
                    "status": "success",
                    "operations": [],
                }
                for index in range(101)
            ]

            safe_write_run_log(run_log_path, {"runs": runs})

            saved = load_run_log(run_log_path)["runs"]

        self.assertEqual(len(saved), 100)
        self.assertEqual(saved[0]["run_id"], "run-1")
        self.assertEqual(saved[-1]["run_id"], "run-100")

    def test_run_log_pruning_preserves_non_apply_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_log_path = Path(tmp) / RUN_LOG_NAME
            runs = [{"run_id": "dry-run", "mode": "dry-run"}]
            runs.extend(
                {"run_id": f"apply-{index}", "mode": "apply"}
                for index in range(101)
            )

            safe_write_run_log(run_log_path, {"runs": runs})

            saved = load_run_log(run_log_path)["runs"]

        self.assertEqual(len(saved), 101)
        self.assertEqual(saved[0]["run_id"], "dry-run")
        self.assertEqual(saved[1]["run_id"], "apply-1")
        self.assertEqual(saved[-1]["run_id"], "apply-100")

    def test_safe_write_run_log_failure_preserves_existing_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_log_path = Path(tmp) / RUN_LOG_NAME
            original = {"runs": [{"run_id": "original", "mode": "apply"}]}
            safe_write_run_log(run_log_path, original)

            with patch("file_helper.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(file_helper.RunLogWriteError):
                    safe_write_run_log(
                        run_log_path,
                        {"runs": [{"run_id": "replacement", "mode": "apply"}]},
                    )

            saved = load_run_log(run_log_path)
            tmp_exists = run_log_path.with_name(file_helper.RUN_LOG_TMP_NAME).exists()

        self.assertEqual(saved, original)
        self.assertFalse(tmp_exists)

    def test_append_operation_persists_partial_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = mkdir(root / "source")
            target = root / "target"
            group = PlanGroup(
                items=[WorkItem("folder", source.name, source, root)],
                is_merge=False,
                sequence_range="1",
                date_label="",
                category="",
                orders=1,
                quantity=1,
                final_name=target.name,
                target_path=target,
                naming_template="",
                reason="test",
            )
            run_log_path = root / RUN_LOG_NAME
            data, run = create_apply_run(root, run_log_path, [group])

            file_helper.append_run_operation(
                run_log_path,
                data,
                run,
                make_operation("move", source, target),
            )

            saved_run = load_run_log(run_log_path)["runs"][0]

        self.assertEqual(saved_run["status"], "partial")
        self.assertEqual(len(saved_run["operations"]), 1)

    def test_append_operation_write_failure_restores_memory_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_log_path = root / RUN_LOG_NAME
            run = {"status": "running", "operations": []}
            data = {"runs": [run]}

            with patch(
                "file_helper.safe_write_run_log",
                side_effect=file_helper.RunLogWriteError("write failed"),
            ):
                with self.assertRaises(file_helper.RunLogWriteError):
                    file_helper.append_run_operation(
                        run_log_path,
                        data,
                        run,
                        {"action": "move"},
                    )

        self.assertEqual(run["status"], "running")
        self.assertEqual(run["operations"], [])

    def test_generated_merge_folder_requires_explicit_merge_sequence_separator(self):
        cases = {
            "06-03-HYX-NP图片项链-6单-13个": False,
            "1~3-0603-NP图片项链-10单-20个": True,
            "1～3-0603-NP图片项链-10单-20个": True,
            "1-3-0603-NP图片项链-10单-20个": False,
            "1+2+3-0603-NP图片项链-10单-20个": True,
        }

        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(is_generated_merge_folder_name(name), expected)

    def test_detects_hyphenated_date_at_start_of_original_name(self):
        dates, label, sources = detect_dates(
            "06-03-HYX-NP图片项链-6单-13个",
            "未知日期",
        )

        self.assertEqual(dates, ["0603"])
        self.assertEqual(label, "0603")
        self.assertEqual(sources, ["outer_folder_name_only"])

    def test_detects_compact_date_after_numeric_sequence_prefix(self):
        dates, label, sources = detect_dates(
            "10-0603-产品名-1单-1个",
            "未知日期",
        )

        self.assertEqual(dates, ["0603"])
        self.assertEqual(label, "0603")
        self.assertEqual(sources, ["outer_folder_name_only"])

    def test_detection_uses_outer_folder_name_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = mkdir(root / "0507 普通文件夹 2单3个")
            (source / "0508 钢片军牌钥匙扣 9单9个.txt").write_text("inner", encoding="utf-8")

            items, groups = build_plan(root, test_config(), "dry-run", root / "rename_log.csv")

        item = next(item for item in items if item.original_name == "0507 普通文件夹 2单3个")
        self.assertEqual(item.detection.date_label, "0507")
        self.assertEqual(item.detection.category, "未知产品")
        self.assertEqual(item.detection.orders, 2)
        self.assertEqual(item.detection.quantity, 3)
        self.assertEqual(item.detection.matched_keyword, "")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].final_name, "1-0507 普通文件夹 2单3个")

    def test_longest_keyword_wins_category_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mkdir(root / "0507 钢片军牌钥匙扣 1单2个")

            items, groups = build_plan(root, test_config(), "dry-run", root / "rename_log.csv")

        item = items[0]
        self.assertEqual(item.detection.category, "钢片军牌钥匙扣")
        self.assertEqual(item.detection.matched_keyword, "钢片军牌钥匙扣")
        self.assertEqual(groups[0].category, "钢片军牌钥匙扣")

    def test_do_not_merge_keyword_only_forces_single_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mkdir(root / "0501 军牌钥匙扣 1单1个")
            mkdir(root / "0502 军牌钥匙扣 1单1个")
            mkdir(root / "0503 军牌钥匙扣 样品 1单1个")

            items, groups = build_plan(root, test_config(), "dry-run", root / "rename_log.csv")

        sample = next(item for item in items if "样品" in item.original_name)
        sample_group = next(group for group in groups if sample in group.items)
        merged_group = next(group for group in groups if group.is_merge)
        self.assertIsNotNone(sample.sequence_number)
        self.assertEqual(sample.detection.category, "军牌钥匙扣")
        self.assertEqual(sample.detection.do_not_merge_hits, ["样品"])
        self.assertFalse(sample_group.is_merge)
        self.assertEqual(len(merged_group.items), 2)

    def test_already_processed_folder_is_skipped_without_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mkdir(root / "1~2-0501-军牌钥匙扣-2单-2个")

            items, groups = build_plan(root, test_config(), "dry-run", root / "rename_log.csv")

        self.assertEqual(len(groups), 0)
        self.assertTrue(items[0].skip_reason)
        self.assertIsNone(items[0].sequence_number)

    def test_apply_skips_existing_target_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_one = mkdir(root / "0507 军牌项链 1单2个")
            source_two = mkdir(root / "0507 军牌项链 1单2个 副本")
            (source_one / "source.txt").write_text("source", encoding="utf-8")
            (source_two / "source.txt").write_text("source", encoding="utf-8")
            target = mkdir(root / "1~2-0507-军牌项链-2单-4个")
            (target / "existing.txt").write_text("existing", encoding="utf-8")
            log_path = root / "rename_log.csv"
            run_log_path = root / RUN_LOG_NAME

            _items, groups = build_plan(root, test_config(), "apply", log_path)
            run_log_data, run = create_apply_run(root, run_log_path, groups)
            completed, failed_count = apply_plan(groups, log_path, run_log_path, run_log_data, run)

            self.assertEqual(completed, [])
            self.assertEqual(failed_count, 0)
            self.assertTrue(source_one.exists())
            self.assertTrue(source_two.exists())
            self.assertEqual((target / "existing.txt").read_text(encoding="utf-8"), "existing")
            saved_run = load_run_log(run_log_path)["runs"][0]
            self.assertEqual(saved_run["operations"], [])
            result = saved_run["history_snapshot"]["results"][0]
            self.assertEqual(result["status"], "skipped")
            self.assertIn("conflict.target_exists=skip", result["error_reason"])
            rows = read_log_rows(log_path)
            self.assertTrue(any(row["action"] == "skip" and row["status"] == "skipped" for row in rows))

    def test_apply_plan_marks_completed_group_success_in_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mkdir(root / "0507 completed source")
            log_path = root / "rename_log.csv"
            run_log_path = root / RUN_LOG_NAME
            _items, groups = build_plan(root, test_config(), "apply", log_path)
            run_log_data, run = create_apply_run(root, run_log_path, groups)

            completed, failed_count = apply_plan(
                groups,
                log_path,
                run_log_path,
                run_log_data,
                run,
            )

            result = load_run_log(run_log_path)["runs"][0]["history_snapshot"]["results"][0]

        self.assertEqual(failed_count, 0)
        self.assertEqual(completed, groups)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["error_reason"], "")

    def test_apply_plan_marks_failed_result_by_result_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing_source = root / "missing-source"
            valid_source = mkdir(root / "valid-source")
            groups = [
                PlanGroup(
                    items=[WorkItem("folder", source.name, source, root)],
                    is_merge=False,
                    sequence_range=str(index),
                    date_label="",
                    category="",
                    orders=1,
                    quantity=1,
                    final_name=f"target-{index}",
                    target_path=root / f"target-{index}",
                    naming_template="",
                    reason="test",
                )
                for index, source in enumerate((missing_source, valid_source), start=1)
            ]
            run_log_path = root / RUN_LOG_NAME
            data, run = create_apply_run(root, run_log_path, groups)

            completed, failed_count = apply_plan(
                groups,
                root / "rename_log.csv",
                run_log_path,
                data,
                run,
            )

            results = load_run_log(run_log_path)["runs"][0]["history_snapshot"]["results"]

        self.assertEqual(failed_count, 1)
        self.assertEqual(completed, [groups[1]])
        self.assertEqual(results[0]["result_id"], "result-1")
        self.assertEqual(results[0]["status"], "failed")
        self.assertTrue(results[0]["error_reason"])
        self.assertEqual(results[1]["result_id"], "result-2")
        self.assertEqual(results[1]["status"], "success")

    def test_first_rename_operation_write_failure_rolls_back_and_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_one = mkdir(root / "source-one")
            source_two = mkdir(root / "source-two")
            groups = [
                PlanGroup(
                    items=[WorkItem("folder", source.name, source, root)],
                    is_merge=False,
                    sequence_range=str(index),
                    date_label="",
                    category="",
                    orders=1,
                    quantity=1,
                    final_name=f"target-{index}",
                    target_path=root / f"target-{index}",
                    naming_template="",
                    reason="test",
                )
                for index, source in enumerate((source_one, source_two), start=1)
            ]
            run_log_path = root / RUN_LOG_NAME
            data, run = create_apply_run(root, run_log_path, groups)

            with patch(
                "file_helper.safe_write_run_log",
                side_effect=file_helper.RunLogWriteError("write failed"),
            ):
                with self.assertRaises(file_helper.RunLogWriteError):
                    apply_plan(
                        groups,
                        root / "rename_log.csv",
                        run_log_path,
                        data,
                        run,
                    )

            saved_run = load_run_log(run_log_path)["runs"][0]

            self.assertFalse((root / "target-1").exists())
            self.assertTrue(source_one.exists())
            self.assertTrue(source_two.exists())
            self.assertFalse((root / "target-2").exists())
            self.assertEqual(saved_run["operations"], [])
            self.assertEqual(saved_run["status"], "running")

    def test_create_dir_operation_write_failure_removes_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_one = mkdir(root / "source-one")
            source_two = mkdir(root / "source-two")
            target = root / "merged-target"
            group = PlanGroup(
                items=[
                    WorkItem("folder", source.name, source, root, inner_name=f"{index}-{source.name}")
                    for index, source in enumerate((source_one, source_two), start=1)
                ],
                is_merge=True,
                sequence_range="1~2",
                date_label="",
                category="",
                orders=2,
                quantity=2,
                final_name=target.name,
                target_path=target,
                naming_template="",
                reason="test",
            )
            run_log_path = root / RUN_LOG_NAME
            data, run = create_apply_run(root, run_log_path, [group])

            with patch(
                "file_helper.safe_write_run_log",
                side_effect=file_helper.RunLogWriteError("write failed"),
            ):
                with self.assertRaises(file_helper.RunLogWriteError):
                    apply_plan(
                        [group],
                        root / "rename_log.csv",
                        run_log_path,
                        data,
                        run,
                    )

            target_exists = target.exists()
            source_one_exists = source_one.exists()
            source_two_exists = source_two.exists()

        self.assertFalse(target_exists)
        self.assertTrue(source_one_exists)
        self.assertTrue(source_two_exists)

    def test_failed_rename_rollback_reports_both_errors_in_human_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = mkdir(root / "source")
            target = root / "target"
            group = PlanGroup(
                items=[WorkItem("folder", source.name, source, root)],
                is_merge=False,
                sequence_range="1",
                date_label="",
                category="",
                orders=1,
                quantity=1,
                final_name=target.name,
                target_path=target,
                naming_template="",
                reason="test",
            )
            log_path = root / "rename_log.csv"
            run_log_path = root / RUN_LOG_NAME
            data, run = create_apply_run(root, run_log_path, [group])

            with patch(
                "file_helper.safe_write_run_log",
                side_effect=file_helper.RunLogWriteError("save failed"),
            ):
                with patch("file_helper.shutil.move", side_effect=OSError("rollback failed")):
                    with self.assertRaises(file_helper.RunLogWriteError) as raised:
                        apply_plan(
                            [group],
                            log_path,
                            run_log_path,
                            data,
                            run,
                        )

            rows = read_log_rows(log_path)

        self.assertIn("save failed", str(raised.exception))
        self.assertIn("rollback failed", str(raised.exception))
        self.assertTrue(
            any(
                row["status"] == "failed"
                and "save failed" in row["error_message"]
                and "rollback failed" in row["error_message"]
                for row in rows
            )
        )

    def test_archive_operation_write_failure_deletes_created_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = mkdir(root / "target")
            (target / "file.txt").write_text("content", encoding="utf-8")
            group = PlanGroup(
                items=[],
                is_merge=False,
                sequence_range="1",
                date_label="",
                category="",
                orders=1,
                quantity=1,
                final_name=target.name,
                target_path=target,
                naming_template="",
                reason="test",
            )
            run_log_path = root / RUN_LOG_NAME
            run = {"status": "partial", "operations": [{"action": "move"}]}
            data = {"runs": [run]}

            with patch(
                "file_helper.safe_write_run_log",
                side_effect=file_helper.RunLogWriteError("write failed"),
            ):
                with self.assertRaises(file_helper.RunLogWriteError):
                    compress_groups(
                        [group],
                        root / "rename_log.csv",
                        run_log_path,
                        data,
                        run,
                    )

            zip_exists = zip_path_for_folder(target).exists()

        self.assertFalse(zip_exists)

    def test_final_status_write_failure_leaves_partial_run_undoable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = mkdir(root / "source")
            target = root / "target"
            group = PlanGroup(
                items=[WorkItem("folder", source.name, source, root)],
                is_merge=False,
                sequence_range="1",
                date_label="",
                category="",
                orders=1,
                quantity=1,
                final_name=target.name,
                target_path=target,
                naming_template="",
                reason="test",
            )
            run_log_path = root / RUN_LOG_NAME
            data, run = create_apply_run(root, run_log_path, [group])
            source.rename(target)
            file_helper.append_run_operation(
                run_log_path,
                data,
                run,
                make_operation("move", source, target),
            )

            with patch(
                "file_helper.safe_write_run_log",
                side_effect=file_helper.RunLogWriteError("final status failed"),
            ):
                with self.assertRaises(file_helper.RunLogWriteError):
                    file_helper.update_run_status(run_log_path, data, run, "success")

            saved_data = load_run_log(run_log_path)

        self.assertEqual(saved_data["runs"][0]["status"], "partial")
        self.assertIsNotNone(find_last_undoable_run(saved_data, root))

    def test_main_log_write_failure_stops_later_groups_and_returns_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_one = mkdir(root / "0501 first source")
            source_two = mkdir(root / "0502 second source")
            original_safe_write = file_helper.safe_write_run_log
            write_count = 0

            def fail_after_run_creation(run_log_path, data):
                nonlocal write_count
                write_count += 1
                if write_count == 1:
                    return original_safe_write(run_log_path, data)
                raise file_helper.RunLogWriteError("write failed")

            with patch("file_helper.safe_write_run_log", side_effect=fail_after_run_creation):
                result = main(["--root", str(root), "--apply", "--yes"])

            self.assertEqual(result, 1)
            self.assertTrue(source_one.exists())
            self.assertFalse((root / "1-0501 first source").exists())
            self.assertTrue(source_two.exists())
            self.assertFalse((root / "2-0502 second source").exists())

    def test_undo_uses_run_log_and_does_not_overwrite_existing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_before = mkdir(root / "0507 军牌项链 1单2个")
            target_after = mkdir(root / "1-0507 军牌项链 1单2个")
            (target_after / "moved.txt").write_text("moved", encoding="utf-8")
            run_log_path = root / RUN_LOG_NAME
            run = {
                "run_id": "test",
                "root": str(root.resolve()),
                "time": "2026-05-28 00:00:00",
                "status": "success",
                "undone": False,
                "undo_time": "",
                "undo_status": "",
                "operations": [make_operation("move", source_before, target_after)],
            }
            safe_write_run_log(run_log_path, {"runs": [run]})

            result = undo_last(root, root / "rename_log.csv", run_log_path, confirmed=True)

            self.assertEqual(result, 0)
            self.assertTrue(source_before.exists())
            self.assertTrue(target_after.exists())
            data = load_run_log(run_log_path)
            self.assertTrue(data["runs"][0]["undone"])
            self.assertEqual(data["runs"][0]["undo_status"], "failed")
            rows = read_log_rows(root / "rename_log.csv")
            self.assertTrue(any("回退目标已存在" in row["error_message"] for row in rows))

    def test_undo_final_status_write_failure_returns_one_and_logs_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_before = root / "source"
            target_after = mkdir(root / "target")
            run_log_path = root / RUN_LOG_NAME
            run = {
                "run_id": "test",
                "mode": "apply",
                "root": str(root.resolve()),
                "time": "2026-06-15 00:00:00",
                "status": "partial",
                "undone": False,
                "undo_time": "",
                "undo_status": "",
                "operations": [make_operation("move", source_before, target_after)],
            }
            safe_write_run_log(run_log_path, {"runs": [run]})
            output = io.StringIO()

            with patch(
                "file_helper.update_run_undo_status",
                side_effect=file_helper.RunLogWriteError("undo status failed"),
            ):
                with contextlib.redirect_stdout(output):
                    result = undo_last(
                        root,
                        root / "rename_log.csv",
                        run_log_path,
                        confirmed=True,
                    )

            rows = read_log_rows(root / "rename_log.csv")

        self.assertEqual(result, 1)
        self.assertTrue(any(row["action"] == "undo_error" for row in rows))
        self.assertIn("undo status failed", output.getvalue())

    def test_compress_groups_skips_existing_zip_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = mkdir(root / "0507 军牌项链 1单2个")
            (source / "source.txt").write_text("source", encoding="utf-8")
            log_path = root / "rename_log.csv"
            run_log_path = root / RUN_LOG_NAME

            _items, groups = build_plan(root, test_config(), "apply", log_path)
            run_log_data, run = create_apply_run(root, run_log_path, groups)
            completed, failed_count = apply_plan(groups, log_path, run_log_path, run_log_data, run)
            update_run_status(run_log_path, run_log_data, run, "success")
            zip_path = zip_path_for_folder(completed[0].target_path)
            zip_path.write_text("existing zip placeholder", encoding="utf-8")

            compress_failures = compress_groups(completed, log_path, run_log_path, run_log_data, run)

            self.assertEqual(failed_count, 0)
            self.assertEqual(compress_failures, 0)
            self.assertEqual(zip_path.read_text(encoding="utf-8"), "existing zip placeholder")
            operations = load_run_log(run_log_path)["runs"][0]["operations"]
            self.assertFalse(any(operation["action"] == "archive_create" for operation in operations))
            rows = read_log_rows(log_path)
            self.assertTrue(any(row["action"] == "zip" and row["status"] == "skipped" for row in rows))

    def test_dry_run_report_contains_specific_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mkdir(root / "0507 普通文件夹 2单3个")
            mkdir(root / "0501 军牌钥匙扣 1单1个")
            mkdir(root / "0502 军牌钥匙扣 1单1个")
            conflict_target = mkdir(root / "1~2-0501-0502-军牌钥匙扣-2单-2个")
            (conflict_target / "existing.txt").write_text("existing", encoding="utf-8")
            source = mkdir(root / "0507 军牌项链 1单2个")
            (source / "source.txt").write_text("source", encoding="utf-8")
            (root / "3-0507 军牌项链 1单2个.zip").write_text("existing zip placeholder", encoding="utf-8")

            items, groups = build_plan(root, test_config(), "dry-run", root / "rename_log.csv", archive_enabled=True)
            report_path = write_organize_report(root, items, groups, "dry-run", True)
            self.assertTrue(report_path.exists())

            content = report_path.read_bytes().decode("utf-8", errors="ignore")

        self.assertEqual(report_path.name, REPORT_NAME)
        self.assertIn("未识别品类", content)
        self.assertIn("目标冲突", content)
        self.assertIn("压缩包冲突", content)

    def test_main_dry_run_writes_report_without_real_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = mkdir(root / "0507 普通文件夹 2单3个")

            result = main(["--root", str(root), "--dry-run"])
            report_path = root / REPORT_NAME

            self.assertEqual(result, 0)
            self.assertTrue(source.exists())
            self.assertTrue(report_path.exists())
            content = report_path.read_bytes().decode("utf-8", errors="ignore")

        self.assertIn("计划执行", content)
        self.assertIn("未识别品类", content)

    def test_report_does_not_overwrite_existing_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / REPORT_NAME
            existing.write_text("keep me", encoding="utf-8")
            mkdir(root / "0507 普通文件夹 2单3个")

            result = main(["--root", str(root), "--dry-run"])

            self.assertEqual(result, 0)
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep me")
            self.assertTrue((root / "整理报告-001.xlsx").exists())

    def test_apply_archive_conflict_report_marks_not_compressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = mkdir(root / "0507 军牌项链 1单2个")
            (source / "source.txt").write_text("source", encoding="utf-8")
            log_path = root / "rename_log.csv"
            run_log_path = root / RUN_LOG_NAME

            _items, groups = build_plan(root, test_config(), "apply", log_path)
            run_log_data, run = create_apply_run(root, run_log_path, groups)
            completed, _failed_count = apply_plan(groups, log_path, run_log_path, run_log_data, run)
            zip_path = zip_path_for_folder(completed[0].target_path)
            zip_path.write_text("existing zip placeholder", encoding="utf-8")
            compression_status = {}
            compress_groups(completed, log_path, run_log_path, run_log_data, run, compression_status)
            report_path = write_organize_report(root, _items, groups, "apply", True, test_config(), completed, compression_status)
            content = report_path.read_bytes().decode("utf-8", errors="ignore")

        self.assertIn("压缩包冲突", content)
        self.assertIn("<t>否</t>", content)

    def test_check_config_valid_config_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = write_config(
                Path(tmp) / "config.yaml",
                """
category_priority:
  - A
categories:
  A:
    keywords:
      - AAA
    merge_enabled: true
do_not_merge_keywords:
  - 样品
naming:
  single_keep_original: true
  single_template: "{seq}-{clean_original_name}"
  merged_template: "{seq_range}-{date}-{category}-{orders}单-{quantity}个"
inner_folder_naming:
  template: "{seq}-{original_name}"
sequence:
  enabled: true
  scope: all_extracted_folders
  sort_by: name
  merged_range_style: min_max
quantity_detection:
  source: outer_folder_name_only
conflict:
  target_exists: skip
already_processed:
  enabled: true
  action: skip
  patterns: []
fallback:
  unknown_date: "未知日期"
  unknown_category: "未知产品"
  default_orders_per_folder: 1
  default_quantity_per_order: 1
""",
            )
            exit_code, diagnostics = check_config_diagnostics(config_path)

        self.assertEqual(exit_code, 0)
        self.assertTrue(any(level == "OK" for level, _message in diagnostics))

    def test_check_config_detects_duplicate_keyword_and_containment_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = write_config(
                Path(tmp) / "config.yaml",
                """
category_priority:
  - A
  - B
categories:
  A:
    keywords:
      - 钥匙扣
      - 钥匙扣
    merge_enabled: true
  B:
    keywords:
      - 钥匙扣
      - 钢片军牌钥匙扣
    merge_enabled: true
do_not_merge_keywords:
  - 样品
naming:
  single_template: "{seq}-{clean_original_name}"
  merged_template: "{seq_range}-{date}-{category}-{orders}单-{quantity}个"
inner_folder_naming:
  template: "{seq}-{original_name}"
sequence:
  sort_by: name
  merged_range_style: min_max
quantity_detection:
  source: outer_folder_name_only
conflict:
  target_exists: skip
already_processed:
  enabled: true
  patterns: []
fallback:
  unknown_date: "未知日期"
  unknown_category: "未知产品"
  default_orders_per_folder: 1
  default_quantity_per_order: 1
""",
            )
            exit_code, diagnostics = check_config_diagnostics(config_path)
            messages = "\n".join(message for _level, message in diagnostics)

        self.assertEqual(exit_code, 1)
        self.assertIn("重复关键词", messages)
        self.assertIn("关键词包含关系", messages)
        self.assertIn("同一分类内部重复关键词", messages)

    def test_check_config_missing_required_keys_is_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = write_config(Path(tmp) / "config.yaml", "categories: {}")
            exit_code, diagnostics = check_config_diagnostics(config_path)
            messages = "\n".join(message for _level, message in diagnostics)

        self.assertEqual(exit_code, 1)
        self.assertIn("缺少必需配置项：category_priority", messages)

    def test_test_name_outputs_detection_and_longest_keyword(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text("categories: {}\n", encoding="utf-8")
            config = test_config()
            # Use the real loader path by writing the focused config with PyYAML-compatible text.
            config_path = write_config(
                config_path,
                """
category_priority:
  - 军牌钥匙扣
  - 钢片军牌钥匙扣
categories:
  军牌钥匙扣:
    keywords:
      - 钥匙扣
    merge_enabled: true
  钢片军牌钥匙扣:
    keywords:
      - 钢片军牌钥匙扣
    merge_enabled: true
do_not_merge_keywords:
  - 样品
naming:
  single_template: "{seq}-{clean_original_name}"
  merged_template: "{seq_range}-{date}-{category}-{orders}单-{quantity}个"
inner_folder_naming:
  template: "{seq}-{original_name}"
sequence:
  sort_by: name
  merged_range_style: min_max
quantity_detection:
  source: outer_folder_name_only
conflict:
  target_exists: skip
already_processed:
  enabled: true
  patterns:
    - '^\\d+~\\d+-.+-\\d+单-\\d+个$'
fallback:
  unknown_date: "未知日期"
  unknown_category: "未知产品"
  default_orders_per_folder: 1
  default_quantity_per_order: 1
""",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(["test-name", "0507-WZY-样品-钢片军牌钥匙扣-13单18个", "--config", str(config_path)])
            text = output.getvalue()

        self.assertEqual(result, 0)
        self.assertIn("日期：0507", text)
        self.assertIn("品类：钢片军牌钥匙扣", text)
        self.assertIn("命中关键词：钢片军牌钥匙扣", text)
        self.assertIn("单量：13", text)
        self.assertIn("数量：18", text)
        self.assertIn("禁止合并：是", text)
        self.assertIn("采用最长关键词优先", text)

    def test_test_name_marks_already_processed_format(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["test-name", "1~2-0507-军牌钥匙扣-2单-2个"])
        text = output.getvalue()

        self.assertEqual(result, 0)
        self.assertIn("已处理格式：是", text)


if __name__ == "__main__":
    unittest.main()
