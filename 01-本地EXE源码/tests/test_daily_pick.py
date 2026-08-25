import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from backend.analysis import daily_pick


class DailyPickTests(unittest.TestCase):
    def test_filters_scores_explains_and_reuses_daily_cache(self):
        spot = pd.DataFrame([
            {"代码": "600001", "名称": "示例股份", "最新价": 14, "涨跌幅": 2, "成交额": 500_000_000, "成交量": 10_000_000},
            {"代码": "600002", "名称": "ST风险", "最新价": 8, "涨跌幅": 1, "成交额": 900_000_000, "成交量": 10_000_000},
            {"代码": "600003", "名称": "低流动", "最新价": 6, "涨跌幅": 1, "成交额": 1_000_000, "成交量": 100_000},
        ])
        history = pd.DataFrame({
            "date": pd.date_range("2025-01-01", periods=140).date,
            "open": 10, "high": 15, "low": 9, "close": [10 + i * .03 for i in range(140)], "volume": 1_000_000,
        })
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(daily_pick.config, "DAILY_PICK_DIR", Path(folder)), \
             patch.object(daily_pick.config, "DAILY_PICK_MIN_LISTING_DAYS", 120), \
             patch.object(daily_pick.fetcher, "market_spot", return_value=spot) as market, \
             patch.object(daily_pick.fetcher, "stock_daily", return_value=history), \
             patch.object(daily_pick, "_latest_trade_date", return_value="2026-08-25"):
            first = daily_pick.generate()
            second = daily_pick.generate()
            self.assertEqual([row["code"] for row in first["items"]], ["600001"])
            self.assertTrue(first["items"][0]["reasons"])
            self.assertEqual(first["strategy"], "daily_score_v1")
            self.assertFalse(first["cached"])
            self.assertTrue(second["cached"])
            self.assertEqual(market.call_count, 1)
            self.assertEqual(daily_pick.history()[0]["date"], "2026-08-25")

    def test_rules_are_explicit_and_compliant(self):
        result = daily_pick.rules()
        self.assertEqual(sum(result["weights"].values()), 100)
        self.assertIn("不构成投资建议", result["disclaimer"])
        self.assertTrue(any("ST" in item for item in result["filters"]))


if __name__ == "__main__":
    unittest.main()
