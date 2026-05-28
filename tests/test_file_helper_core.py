from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

import file_helper


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
