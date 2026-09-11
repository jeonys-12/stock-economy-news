from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

try:
    import openai  # noqa: F401
except ModuleNotFoundError:
    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = object
    sys.modules["openai"] = openai_stub

import generate_ai_briefing as briefing


class BriefingPayloadTests(unittest.TestCase):
    def test_compact_stock_data_removes_collection_metadata_and_empty_values(self) -> None:
        original_watchlist = briefing.WATCHLIST
        briefing.WATCHLIST = ["테스트"]
        try:
            result = briefing.compact_stock_data({
                "stocks": {
                    "테스트": {
                        "code": "000001",
                        "sector": "테스트업",
                        "quantitative": {"score": 12, "available_dimensions": 2},
                        "market": {
                            "current_price": 1000,
                            "valuation": {"per": 10, "source_url": "https://example.com", "pbr": None},
                            "investor_flow": {"foreign_net_buy_10d_shares": 5, "fetched_at": "now"},
                        },
                    }
                }
            })
        finally:
            briefing.WATCHLIST = original_watchlist

        row = result["테스트"]
        self.assertEqual(row["market"]["valuation"], {"per": 10})
        self.assertEqual(row["market"]["investor_flow"], {"foreign_net_buy_10d_shares": 5})
        self.assertNotIn("source_url", str(row))
        self.assertNotIn("fetched_at", str(row))
        self.assertNotIn("None", str(row))

    def test_structured_output_schema_is_strict(self) -> None:
        schema = briefing.BRIEFING_SCHEMA
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["required"], ["daily", "weekly"])
        for period in (schema["properties"]["daily"], schema["properties"]["weekly"]):
            self.assertFalse(period["additionalProperties"])
            self.assertEqual(period["properties"]["drivers"]["maxItems"], 4)
            self.assertEqual(period["properties"]["risks"]["maxItems"], 4)


if __name__ == "__main__":
    unittest.main()
