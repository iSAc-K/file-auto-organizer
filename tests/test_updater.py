import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from updater import apply_update_package


class UpdaterTests(unittest.TestCase):
    def test_replaces_program_files_and_preserves_user_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = root / "install"
            install.mkdir()
            (install / "file_helper.py").write_text("old", encoding="utf-8")
            (install / "user_config.yaml").write_text("mine", encoding="utf-8")
            package = root / "update.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("file_helper.py", "new")
                archive.writestr("user_config.yaml", "overwrite")

            apply_update_package(package, install)

            self.assertEqual((install / "file_helper.py").read_text(encoding="utf-8"), "new")
            self.assertEqual((install / "user_config.yaml").read_text(encoding="utf-8"), "mine")

    def test_rejects_zip_slip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "update.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("../outside.txt", "bad")
            with self.assertRaises(ValueError):
                apply_update_package(package, root / "install")
            self.assertFalse((root / "outside.txt").exists())

    def test_restores_current_file_when_copy_fails_after_partial_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = root / "install"
            install.mkdir()
            target = install / "file_helper.py"
            target.write_text("old-complete-content", encoding="utf-8")
            package = root / "update.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("file_helper.py", "new-content")

            real_copy2 = __import__("shutil").copy2
            failed_once = False

            def failing_copy(source, destination, *args, **kwargs):
                nonlocal failed_once
                source_path = Path(source)
                destination_path = Path(destination)
                if not failed_once and source_path.name == "file_helper.py" and destination_path == target:
                    failed_once = True
                    destination_path.write_text("partial", encoding="utf-8")
                    raise OSError("simulated interrupted copy")
                return real_copy2(source, destination, *args, **kwargs)

            with patch("updater.shutil.copy2", side_effect=failing_copy):
                with self.assertRaisesRegex(OSError, "simulated interrupted copy"):
                    apply_update_package(package, install)

            self.assertEqual(target.read_text(encoding="utf-8"), "old-complete-content")


if __name__ == "__main__":
    unittest.main()
