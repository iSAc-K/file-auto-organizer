from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

import file_helper


class GeneratedMergeFolderNameTests(unittest.TestCase):
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
                self.assertEqual(
                    file_helper.is_generated_merge_folder_name(name),
                    expected,
                )


class DateDetectionTests(unittest.TestCase):
    def test_detects_hyphenated_date_at_start_of_original_name(self):
        dates, label, sources = file_helper.detect_dates(
            "06-03-HYX-NP图片项链-6单-13个",
            "未知日期",
        )

        self.assertEqual(dates, ["0603"])
        self.assertEqual(label, "0603")
        self.assertEqual(sources, ["outer_folder_name_only"])

    def test_detects_compact_date_after_numeric_sequence_prefix(self):
        dates, label, sources = file_helper.detect_dates(
            "10-0603-产品名-1单-1个",
            "未知日期",
        )

        self.assertEqual(dates, ["0603"])
        self.assertEqual(label, "0603")
        self.assertEqual(sources, ["outer_folder_name_only"])


class FileHelperCliCoreTests(unittest.TestCase):
    def test_check_config_command_validates_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_text(
                "categories:\n"
                "  测试品类:\n"
                "    keywords:\n"
                "      - 测试品类\n"
                "    merge_enabled: false\n",
                encoding="utf-8",
            )

            output = StringIO()
            with redirect_stdout(output):
                result = file_helper.main(["check-config", "--config", str(config)])

        self.assertEqual(result, 0)
        self.assertIn("配置检查通过", output.getvalue())

    def test_test_name_command_prints_detection_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_text(
                "categories:\n"
                "  钢片军牌钥匙扣:\n"
                "    keywords:\n"
                "      - 钢片军牌钥匙扣\n"
                "    merge_enabled: true\n",
                encoding="utf-8",
            )

            output = StringIO()
            with redirect_stdout(output):
                result = file_helper.main(
                    [
                        "test-name",
                        "0507-WZY-钢片军牌钥匙扣-13单18个",
                        "--config",
                        str(config),
                    ]
                )

        text = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("日期：0507", text)
        self.assertIn("品类：钢片军牌钥匙扣", text)
        self.assertIn("数量：13单18个", text)


if __name__ == "__main__":
    unittest.main()
