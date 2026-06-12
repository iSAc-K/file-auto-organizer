import tempfile
import unittest
from pathlib import Path

from config_manager import (
    ConfigConflictError,
    merge_user_config,
    parse_batch_keywords,
    save_user_config,
)


OFFICIAL = {
    "category_priority": ["A", "B"],
    "categories": {
        "A": {"keywords": ["alpha", "shared-old"], "merge_enabled": True},
        "B": {"keywords": ["beta"], "merge_enabled": False},
    },
}


class ConfigManagerTests(unittest.TestCase):
    def test_merges_user_categories_official_overrides_and_order(self):
        user = {
            "category_order": ["C", "A", "B"],
            "categories": {
                "A": {
                    "added_keywords": ["extra"],
                    "disabled_keywords": ["shared-old"],
                    "merge_enabled": False,
                },
                "B": {"enabled": False},
                "C": {
                    "custom": True,
                    "enabled": True,
                    "merge_enabled": True,
                    "keywords": ["custom"],
                    "disabled_keywords": [],
                },
            },
        }

        merged = merge_user_config(OFFICIAL, user)

        self.assertEqual(merged["category_priority"], ["C", "A"])
        self.assertEqual(merged["categories"]["A"]["keywords"], ["alpha", "extra"])
        self.assertFalse(merged["categories"]["A"]["merge_enabled"])
        self.assertNotIn("B", merged["categories"])
        self.assertEqual(merged["categories"]["C"]["keywords"], ["custom"])

    def test_new_official_category_is_appended_to_saved_order(self):
        official = {
            **OFFICIAL,
            "category_priority": ["A", "B", "D"],
            "categories": {
                **OFFICIAL["categories"],
                "D": {"keywords": ["delta"], "merge_enabled": True},
            },
        }
        merged = merge_user_config(official, {"category_order": ["B", "A"]})
        self.assertEqual(merged["category_priority"], ["B", "A", "D"])

    def test_duplicate_enabled_keyword_across_categories_blocks_merge(self):
        user = {
            "categories": {
                "C": {
                    "custom": True,
                    "enabled": True,
                    "merge_enabled": True,
                    "keywords": ["alpha"],
                }
            }
        }
        with self.assertRaises(ConfigConflictError) as caught:
            merge_user_config(OFFICIAL, user)
        self.assertIn("alpha", str(caught.exception))
        self.assertIn("A", str(caught.exception))
        self.assertIn("C", str(caught.exception))

    def test_disabled_keyword_can_be_reused(self):
        user = {
            "categories": {
                "A": {"disabled_keywords": ["alpha"]},
                "C": {
                    "custom": True,
                    "enabled": True,
                    "merge_enabled": True,
                    "keywords": ["alpha"],
                },
            }
        }
        merged = merge_user_config(OFFICIAL, user)
        self.assertEqual(merged["categories"]["C"]["keywords"], ["alpha"])

    def test_disabled_custom_keyword_is_removed_from_effective_config(self):
        user = {
            "category_order": ["C"],
            "categories": {
                "C": {
                    "custom": True,
                    "enabled": True,
                    "merge_enabled": True,
                    "keywords": ["active", "disabled"],
                    "disabled_keywords": ["disabled"],
                }
            },
        }

        merged = merge_user_config({"category_priority": [], "categories": {}}, user)

        self.assertEqual(merged["categories"]["C"]["keywords"], ["active"])

    def test_batch_keywords_support_commas_chinese_commas_and_lines(self):
        self.assertEqual(
            parse_batch_keywords(" alpha, beta，gamma\nalpha \n"),
            ["alpha", "beta", "gamma"],
        )

    def test_save_user_config_is_valid_yaml_and_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "user_config.yaml"
            path.write_text("broken: [", encoding="utf-8")
            data = {"version": 1, "category_order": ["A"], "categories": {}}

            save_user_config(path, data)

            text = path.read_text(encoding="utf-8")
            self.assertIn("category_order", text)
            self.assertNotIn("broken", text)


if __name__ == "__main__":
    unittest.main()
