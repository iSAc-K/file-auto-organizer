import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from update_manager import (
    UpdateInfo,
    fetch_update_info_with_retry,
    is_newer_version,
    parse_update_manifest,
    verify_sha256,
)


class UpdateManagerTests(unittest.TestCase):
    def test_semantic_version_comparison(self):
        self.assertTrue(is_newer_version("2.4.0", "2.3"))
        self.assertTrue(is_newer_version("2.10", "2.9.9"))
        self.assertFalse(is_newer_version("2.3.0", "2.3"))
        self.assertFalse(is_newer_version("2.2.9", "2.3"))

    def test_manifest_requires_https_zip_and_sha256(self):
        info = parse_update_manifest(
            {
                "version": "2.4.0",
                "download_url": "https://example.com/app.zip",
                "sha256": "a" * 64,
                "notes": ["新增配置页"],
            }
        )
        self.assertEqual(info, UpdateInfo("2.4.0", "https://example.com/app.zip", "a" * 64, ["新增配置页"]))
        with self.assertRaises(ValueError):
            parse_update_manifest({"version": "2.4", "download_url": "http://bad/app.zip", "sha256": "x"})

    def test_verify_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "update.zip"
            path.write_bytes(b"payload")
            expected = hashlib.sha256(b"payload").hexdigest()
            self.assertTrue(verify_sha256(path, expected))
            self.assertFalse(verify_sha256(path, "0" * 64))

    def test_utf8_bom_manifest_can_be_decoded(self):
        payload = b"\xef\xbb\xbf" + json.dumps(
            {
                "version": "2.4.1",
                "download_url": "https://example.com/app.zip",
                "sha256": "a" * 64,
                "notes": [],
            }
        ).encode("utf-8")
        info = parse_update_manifest(json.loads(payload.decode("utf-8-sig")))
        self.assertEqual(info.version, "2.4.1")

    def test_update_check_retries_transient_failures(self):
        calls = []
        sleeps = []
        expected = UpdateInfo("2.4.2", "https://example.com/app.zip", "a" * 64, [])

        def fetcher():
            calls.append(1)
            if len(calls) < 3:
                raise TimeoutError("temporary timeout")
            return expected

        result = fetch_update_info_with_retry(
            attempts=3,
            retry_delay=0.25,
            fetcher=fetcher,
            sleeper=sleeps.append,
        )

        self.assertEqual(result, expected)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [0.25, 0.25])

    def test_update_check_raises_after_all_attempts_fail(self):
        calls = []

        def fetcher():
            calls.append(1)
            raise TimeoutError("still unavailable")

        with self.assertRaisesRegex(TimeoutError, "still unavailable"):
            fetch_update_info_with_retry(
                attempts=2,
                retry_delay=0,
                fetcher=fetcher,
                sleeper=lambda _delay: None,
            )
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
