import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest

from file_helper import (
    DEFAULT_CONFIG,
    RUN_LOG_NAME,
    apply_plan,
    build_plan,
    compress_groups,
    create_apply_run,
    deep_merge,
    load_run_log,
    make_operation,
    safe_write_run_log,
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


class FileHelperCoreTests(unittest.TestCase):
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
            run_log_data, run = create_apply_run(root, run_log_path)
            completed, failed_count = apply_plan(groups, log_path, run_log_path, run_log_data, run)

            self.assertEqual(completed, [])
            self.assertEqual(failed_count, 0)
            self.assertTrue(source_one.exists())
            self.assertTrue(source_two.exists())
            self.assertEqual((target / "existing.txt").read_text(encoding="utf-8"), "existing")
            self.assertEqual(load_run_log(run_log_path)["runs"][0]["operations"], [])
            rows = read_log_rows(log_path)
            self.assertTrue(any(row["action"] == "skip" and row["status"] == "skipped" for row in rows))

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

    def test_compress_groups_skips_existing_zip_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = mkdir(root / "0507 军牌项链 1单2个")
            (source / "source.txt").write_text("source", encoding="utf-8")
            log_path = root / "rename_log.csv"
            run_log_path = root / RUN_LOG_NAME

            _items, groups = build_plan(root, test_config(), "apply", log_path)
            run_log_data, run = create_apply_run(root, run_log_path)
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


if __name__ == "__main__":
    unittest.main()
