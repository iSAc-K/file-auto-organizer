from pathlib import Path
import tempfile
import unittest

from launcher_core import (
    LauncherSettings,
    build_command,
    clean_path_value,
    format_python_command,
    ps_quote,
)


class LauncherCoreTests(unittest.TestCase):
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

            command = build_command(settings)

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


if __name__ == "__main__":
    unittest.main()
