import importlib.util
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "python"
SIDECAR_PATH = PYTHON_DIR / "squaizer_sidecar.py"

spec = importlib.util.spec_from_file_location("squaizer_sidecar", SIDECAR_PATH)
assert spec and spec.loader
sidecar = importlib.util.module_from_spec(spec)
sys.modules["squaizer_sidecar"] = sidecar
spec.loader.exec_module(sidecar)


class SquaizerSidecarTests(unittest.TestCase):
    def compress(self, text, **kwargs):
        opts = {"mode": "regex", "min_savings": 0.0}
        opts.update(kwargs)
        return sidecar.compress(text, **opts)

    def test_deletes_obvious_fluff(self):
        original = "Please, um, basically help me write a very small summary."
        payload = self.compress(original)
        self.assertEqual(payload["status"], "compressed")
        compressed = payload["text"].lower()
        for removed in ("please", "um", "basically", "very"):
            self.assertIsNone(re.search(rf"(?<![a-z]){re.escape(removed)}(?![a-z])", compressed))
        self.assertIn("help me write", compressed)

    def test_deletes_sentence_initial_request_and_speaker_hedge(self):
        request = self.compress("Can you please basically fix this bug?")
        self.assertEqual(request["status"], "compressed")
        request_text = request["text"].lower()
        self.assertNotIn("can you", request_text)
        self.assertNotIn("please", request_text)
        self.assertIn("fix this bug", request_text)

        hedge = self.compress("I think basically fix this bug.")
        self.assertEqual(hedge["status"], "compressed")
        hedge_text = hedge["text"].lower()
        self.assertNotIn("i think", hedge_text)
        self.assertIn("fix this bug", hedge_text)

    def test_preserves_negation_constraints_paths_urls_quotes_and_numbers(self):
        original = "Please do not change /tmp/demo.txt, keep https://example.com, preserve \"exact text\", and return 3 items only."
        payload = self.compress(original)
        compressed = payload["text"]
        self.assertEqual(payload["status"], "compressed")
        for required in ("not", "/tmp/demo.txt", "https://example.com", "\"exact text\"", "3", "only"):
            self.assertIn(required, compressed)
        self.assertNotIn("Please", compressed)

    def test_preserves_code_fence_and_inline_code(self):
        code = "```python\nprint('please keep this')\n```"
        original = f"Please, basically review {code} and `do_not_change()` carefully."
        payload = self.compress(original)
        compressed = payload["text"]
        self.assertEqual(payload["status"], "compressed")
        self.assertIn(code, compressed)
        self.assertIn("`do_not_change()`", compressed)
        self.assertNotIn("basically", compressed.lower())

    def test_preserves_unterminated_fence_and_unfenced_function_call(self):
        unterminated = "Please review this code:\n```python\n# please keep this code"
        payload = self.compress(unterminated)
        self.assertEqual(payload["status"], "compressed")
        self.assertIn("# please keep this code", payload["text"])

        call = "Please review this code:\nplease()"
        payload = self.compress(call)
        self.assertEqual(payload["status"], "compressed")
        self.assertIn("please()", payload["text"])

    def test_preserves_windows_paths_with_spaces(self):
        original = "Please keep C:\\Program Files\\please\\app exactly."
        payload = self.compress(original)
        self.assertEqual(payload["status"], "compressed")
        self.assertIn("C:\\Program Files\\please\\app", payload["text"])

    def test_no_candidates_returns_original(self):
        original = "Implement the parser and preserve all existing behavior."
        payload = self.compress(original)
        self.assertEqual(payload["status"], "original")
        self.assertEqual(payload["text"], original)

    def test_preserves_mid_sentence_just(self):
        original = "Please use just this file and no other paths."
        payload = self.compress(original)
        self.assertEqual(payload["status"], "compressed")
        self.assertIn("just this file", payload["text"])
        self.assertIn("no", payload["text"])

    def test_cli_reads_stdin_and_emits_json(self):
        proc = subprocess.run(
            [sys.executable, str(PYTHON_DIR / "squaizer_sidecar.py"), "--mode", "regex", "--min-savings", "0"],
            input="Please, um, help with this.",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "compressed")
        self.assertIn("help", payload["text"])


if __name__ == "__main__":
    unittest.main()
