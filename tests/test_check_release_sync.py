from pathlib import Path
import tempfile
import unittest

from tools.check_release_sync import check_files


class CheckReleaseSyncTests(unittest.TestCase):
    def test_check_files_reports_match_mismatch_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            release = root / "release"
            source.mkdir()
            release.mkdir()
            (source / "same.txt").write_text("same\n", encoding="utf-8")
            (release / "same.txt").write_text("same\n", encoding="utf-8")
            (source / "different.txt").write_text("source\n", encoding="utf-8")
            (release / "different.txt").write_text("release\n", encoding="utf-8")
            (source / "missing.txt").write_text("missing\n", encoding="utf-8")

            issue_count, lines = check_files(source, release, ["same.txt", "different.txt", "missing.txt"])

        self.assertEqual(issue_count, 2)
        self.assertIn("一致: same.txt", lines)
        self.assertIn("不一致: different.txt", lines)
        self.assertIn("缺失发布文件: missing.txt", lines)


if __name__ == "__main__":
    unittest.main()
