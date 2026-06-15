import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from updater import InstallProgress, UpdateInstallError, apply_update_package


class UpdaterTests(unittest.TestCase):
    def test_reports_backup_install_and_complete_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = root / "install"
            install.mkdir()
            (install / "program.txt").write_text("old", encoding="utf-8")
            package = root / "update.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("program.txt", "new")
                archive.writestr("extra.txt", "extra")
            events: list[InstallProgress] = []

            apply_update_package(package, install, progress_callback=events.append)

            phases = [event.phase for event in events]
            self.assertIn("backing_up", phases)
            self.assertIn("installing", phases)
            self.assertLess(
                max(index for index, phase in enumerate(phases) if phase == "backing_up"),
                min(index for index, phase in enumerate(phases) if phase == "installing"),
            )
            self.assertEqual(phases[-1], "complete")
            self.assertEqual(events[-1].completed_files, events[-1].total_files)

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
                with self.assertRaises(UpdateInstallError) as caught:
                    apply_update_package(package, install)

            self.assertIn("simulated interrupted copy", str(caught.exception.install_error))
            self.assertIsNone(caught.exception.rollback_error)
            self.assertFalse(caught.exception.backup_dir.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "old-complete-content")

    def test_failed_rollback_reports_error_and_preserves_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = root / "install"
            install.mkdir()
            target = install / "program.txt"
            target.write_text("old", encoding="utf-8")
            package = root / "update.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("program.txt", "new")
            events: list[InstallProgress] = []
            real_copy2 = __import__("shutil").copy2
            install_failed = False

            def failing_install_and_restore(source, destination, *args, **kwargs):
                nonlocal install_failed
                source_path = Path(source)
                destination_path = Path(destination)
                if source_path.name == "program.txt" and destination_path == target:
                    if not install_failed:
                        install_failed = True
                        target.write_text("partial", encoding="utf-8")
                        raise OSError("install copy failed")
                    raise OSError("rollback copy failed")
                return real_copy2(source, destination, *args, **kwargs)

            with patch("updater.shutil.copy2", side_effect=failing_install_and_restore):
                with self.assertRaises(UpdateInstallError) as caught:
                    apply_update_package(package, install, progress_callback=events.append)

            error = caught.exception
            self.assertIn("rolling_back", [event.phase for event in events])
            self.assertTrue(error.backup_dir.exists())
            self.assertIsNotNone(error.rollback_error)
            self.assertEqual(target.read_text(encoding="utf-8"), "partial")
            __import__("shutil").rmtree(error.backup_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
