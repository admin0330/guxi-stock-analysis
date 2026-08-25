"""加密货币指标与接口边界测试（不访问网络）。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from backend.analysis import crypto
from backend.main import app


class CryptoAnalysisTests(unittest.TestCase):
    @patch("backend.analysis.crypto.crypto_data.kline")
    def test_rising_market_produces_indicators(self, kline):
        prices = [100 + index * 0.5 for index in range(80)]
        kline.return_value = pd.DataFrame({
            "time": pd.date_range("2026-01-01", periods=80, freq="h").astype(str),
            "open": prices, "high": [p + 1 for p in prices], "low": [p - 1 for p in prices],
            "close": prices, "volume": [1000] * 80, "source": ["test"] * 80,
        })

        result = crypto.analyze("BTC", "1h", 80)

        self.assertEqual(result["asset"], "BTC")
        self.assertGreaterEqual(result["score"], 60)
        self.assertIsNotNone(result["indicators"]["ma20"])
        self.assertEqual(len(result["kline"]), 80)

    def test_unknown_asset_returns_404(self):
        response = TestClient(app).get("/api/crypto/DOGE/analysis")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
