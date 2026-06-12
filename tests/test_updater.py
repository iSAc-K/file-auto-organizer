import tempfile
import unittest
import zipfile
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
