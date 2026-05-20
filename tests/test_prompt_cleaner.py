import importlib.util
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEANER_PATH = ROOT / "python" / "prompt_cleaner.py"

spec = importlib.util.spec_from_file_location("prompt_cleaner", CLEANER_PATH)
assert spec and spec.loader
prompt_cleaner = importlib.util.module_from_spec(spec)
sys.modules["prompt_cleaner"] = prompt_cleaner
spec.loader.exec_module(prompt_cleaner)


class PromptCleanerTests(unittest.TestCase):
    def clean(self, text, **kwargs):
        return prompt_cleaner.clean(text, min_savings=kwargs.get("min_savings", 0.0))

    def test_removes_obvious_fluff(self):
        result = self.clean("Please, um, basically write a very small summary.")
        self.assertEqual(result["status"], "cleaned")
        cleaned = result["text"].lower()
        for removed in ("please", "um", "basically", "very"):
            self.assertIsNone(re.search(rf"(?<![a-z]){re.escape(removed)}(?![a-z])", cleaned))
        self.assertIn("write a small summary", cleaned)

    def test_removes_sentence_initial_framing(self):
        result = self.clean("Can you please fix this bug?")
        self.assertEqual(result["status"], "cleaned")
        self.assertEqual(result["text"], "fix this bug?")

    def test_preserves_negation_paths_urls_quotes_and_numbers(self):
        text = 'Please do not change /tmp/demo.txt, keep https://example.com, preserve "exact text", and return 3 items only.'
        result = self.clean(text)
        self.assertEqual(result["status"], "cleaned")
        for required in ("not", "/tmp/demo.txt", "https://example.com", '"exact text"', "3", "only"):
            self.assertIn(required, result["text"])
        self.assertNotIn("Please", result["text"])

    def test_preserves_code_and_inline_code(self):
        code = "```python\nprint('please keep this')\n```"
        text = f"Please review {code} and `please_keep_this()` carefully."
        result = self.clean(text)
        self.assertEqual(result["status"], "cleaned")
        self.assertIn(code, result["text"])
        self.assertIn("`please_keep_this()`", result["text"])

    def test_preserves_identifier_and_function_like_text(self):
        text = "Please review please_keep_this and please()."
        result = self.clean(text)
        self.assertEqual(result["status"], "cleaned")
        self.assertIn("please_keep_this", result["text"])
        self.assertIn("please()", result["text"])

    def test_no_safe_deletions_returns_original(self):
        text = "Implement the parser and preserve all existing behavior."
        result = self.clean(text)
        self.assertEqual(result["status"], "original")
        self.assertEqual(result["text"], text)

    def test_cli_health(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "python" / "prompt_cleaner.py"), "--health"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        self.assertTrue(json.loads(proc.stdout)["ok"])

    def test_cli_clean(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "python" / "prompt_cleaner.py"), "--min-savings", "0"],
            input="Please, um, help with this.",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        result = json.loads(proc.stdout)
        self.assertEqual(result["status"], "cleaned")
        self.assertEqual(result["text"], "help with this.")


if __name__ == "__main__":
    unittest.main()
