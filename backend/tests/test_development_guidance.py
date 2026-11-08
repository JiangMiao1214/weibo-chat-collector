import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.development_guidance import END, START, read_development_reminder


class DevelopmentGuidanceTests(unittest.TestCase):
    def test_project_policy_is_readable(self):
        reminder = read_development_reminder()
        self.assertIn("TDD", reminder)
        self.assertIn("先写失败测试", reminder)
        self.assertNotIn("五步实施范围", reminder)

    def test_only_marked_block_is_printed_and_changed_file_is_reread(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.md"
            for content in ("先红后绿", "先测试再修改"):
                path.write_text(f"不显示\n{START}\n{content}\n{END}\n不显示", encoding="utf-8")
                reminder = read_development_reminder(path)
                self.assertIn(content, reminder)
                self.assertNotIn("不显示", reminder)

    def test_invalid_block_raises_instead_of_silent_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.md"
            for content in ("", f"{START}{END}", f"{END}x{START}", f"{START}x{START}{END}"):
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        read_development_reminder(path)
