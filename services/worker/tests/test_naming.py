from __future__ import annotations

import sys
import unittest
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER))

from naming import DEFAULT_TEMPLATE, TOKENS, TemplateError, compile_template, parse_with_source_root


class NamingTemplateTests(unittest.TestCase):
    def test_parses_default_with_ai(self) -> None:
        compiled = compile_template(DEFAULT_TEMPLATE)
        parsed = compiled.parse("pixiv/AI/R-18/画师-name-12345/85633671_p2-标题-含横线.jpg")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["AI"], "AI")
        self.assertEqual(parsed["age"], "R-18")
        self.assertEqual(parsed["user"], "画师-name")
        self.assertEqual(parsed["user_id"], 12345)
        self.assertEqual(parsed["id"], "85633671_p2")
        self.assertEqual(parsed["pid"], "85633671")
        self.assertEqual(parsed["p"], 2)
        self.assertEqual(parsed["title"], "标题-含横线")

    def test_optional_ai_segment_and_source_root(self) -> None:
        compiled = compile_template(DEFAULT_TEMPLATE)
        parsed = parse_with_source_root(compiled, "pixiv", "All Ages/user-99/123-title.png")
        self.assertEqual(parsed["age"], "All Ages")
        self.assertNotIn("AI", parsed)

    def test_rejects_adjacent_free_text(self) -> None:
        with self.assertRaises(TemplateError):
            compile_template("pixiv/{user}{title}/{id}")

    def test_accepts_every_documented_token(self) -> None:
        for token in TOKENS:
            compile_template(f"prefix/{{{token}}}")


if __name__ == "__main__":
    unittest.main()

