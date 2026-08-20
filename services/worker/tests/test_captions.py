from __future__ import annotations

import sys
import unittest
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER))

from captions import assemble_caption, fallback_from_wd, normalize_tag, parse_llm_json


class CaptionTests(unittest.TestCase):
    def test_normalizes_anima_tags_and_order(self) -> None:
        result = assemble_caption({
            "quality_meta_year_safety": ["HighRes", "score_7", "SAFE"],
            "subject_count": ["1Girl"],
            "characters": ["Other_Character"],
            "series": ["Some_Series"],
            "artists": ["Artist_Name"],
            "general": ["looking_at_viewer", "HighRes"],
            "natural_language": None,
            "warnings": [],
        }, "character", "charx001")
        self.assertEqual(
            result.text,
            "highres, score_7, safe, 1girl, charx001, other character, some series, @artist name, looking at viewer",
        )

    def test_style_trigger_is_artist_and_hybrid_warns_on_short_text(self) -> None:
        result = assemble_caption({"general": ["city"], "natural_language": "One sentence.", "warnings": []}, "style", "styx001")
        self.assertTrue(result.text.startswith("@styx001, city."))
        self.assertEqual(result.status, "needs_review")

    def test_score_is_only_underscore_exception(self) -> None:
        self.assertEqual(normalize_tag("score_9"), "score_9")
        self.assertEqual(normalize_tag("long_hair"), "long hair")

    def test_fallback_is_reviewable(self) -> None:
        result = fallback_from_wd({
            "rating": [{"tag": "explicit", "confidence": 1}],
            "character": [],
            "general": [{"tag": "1girl", "confidence": 1}, {"tag": "long_hair", "confidence": .9}],
        }, "character", "charx001")
        self.assertIn("explicit, 1girl, charx001, long hair", result.text)
        self.assertEqual(result.status, "needs_review")

    def test_parses_fenced_json(self) -> None:
        self.assertEqual(parse_llm_json('```json\n{"general": []}\n```'), {"general": []})


if __name__ == "__main__":
    unittest.main()

