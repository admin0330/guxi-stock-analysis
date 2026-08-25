import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from backend import config
from backend.analysis import daily


class ProgressiveDailyTests(unittest.TestCase):
    @patch.object(daily.fetcher, "mem_set")
    @patch.object(daily.fetcher, "mem_get", return_value=None)
    @patch.object(daily.market_an, "overview")
    def test_market_brief_is_independent(self, overview, _mem_get, mem_set):
        overview.return_value = {
            "indices": [{"close": 3200, "change_pct": 1.2}],
            "breadth": {"total": 5000, "up": 3000, "down": 2000, "up_ratio": 0.6},
            "volume": {"amount_yi": 12000},
            "temperature": {"temperature": 62, "label": "偏热", "tone": "均衡"},
        }

        result = daily.market_brief()

        self.assertEqual(result["temperature"]["temperature"], 62)
        self.assertIn("3200", result["market_line"])
        self.assertIn("均衡", result["conclusion"])
        mem_set.assert_called_once()

    def test_daily_archive_roundtrip(self):
        report = {
            "date": "2026-08-24",
            "market_line": "市场摘要",
            "temperature": {"temperature": 50},
        }
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(config, "REPORT_DIR", Path(temp)), \
                patch.object(daily, "daily_report", return_value=report):
            daily.archive_report()
            self.assertEqual(daily.historical_report("2026-08-24"), report)
            self.assertEqual(daily.report_history(30)[0]["date"], "2026-08-24")


if __name__ == "__main__":
    unittest.main()
