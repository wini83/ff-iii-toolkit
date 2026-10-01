import unittest

from scripts.extract_release_notes import extract_release_notes


class ReleaseNotesTests(unittest.TestCase):
    def test_extracts_only_selected_release(self):
        changelog = """# Changelog

## v3.2.0 (2026-10-02)

### Fix

- New fix.

## v3.1.0 (2026-10-01)

### Feat

- Firefly status.

## v3.0.1 (2026-09-13)

- Previous fix.
"""
        self.assertEqual(
            extract_release_notes(changelog, "v3.1.0"),
            "### Feat\n\n- Firefly status.\n",
        )

    def test_final_section_and_undated_heading(self):
        self.assertEqual(
            extract_release_notes("## v3.0.0\n\n- Initial release.", "v3.0.0"),
            "- Initial release.\n",
        )

    def test_tag_is_matched_exactly(self):
        with self.assertRaisesRegex(ValueError, "No changelog section"):
            extract_release_notes("## v3.1.00 (2026-10-01)\n- Other.", "v3.1.0")

    def test_missing_section_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "No changelog section"):
            extract_release_notes("# Changelog", "v3.1.0")

    def test_empty_section_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "is empty"):
            extract_release_notes("## v3.1.0 (2026-10-01)\n\n## v3.0.0\n- Old.", "v3.1.0")


if __name__ == "__main__":
    unittest.main()
