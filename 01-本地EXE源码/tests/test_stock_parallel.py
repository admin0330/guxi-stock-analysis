"""个股分模块接口与轻量数据路径测试（不访问网络）。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from backend.analysis import stock
from backend.main import app


class StockParallelTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("backend.api.routes.stock_an.technical_report")
    def test_technical_endpoint_has_independent_contract(self, report):
        report.return_value = {
            "dimension": "technical", "symbol": "sh600519", "code": "600519",
            "score": 31, "max_score": 40, "data": {"trend": "多头"},
            "positives": ["趋势向上"], "risks": [],
        }

        response = self.client.get("/api/stock/600519/technical")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["dimension"], "technical")
        self.assertEqual(response.json()["score"], 31)

    @patch("backend.analysis.stock.fetcher.market_spot", side_effect=AssertionError("不应拉取全市场快照"))
    @patch("backend.analysis.stock.fetcher.stock_daily")
    @patch("backend.analysis.stock.fetcher.stock_financial_abstract", return_value=pd.DataFrame())
    @patch("backend.analysis.stock.fetcher.stock_financial_indicator")
    @patch("backend.analysis.stock.fetcher.stock_open_api_company_profile", return_value={})
    def test_fundamental_pb_reuses_single_stock_daily(
        self, _profile, financial_indicator, _abstract, stock_daily, _market_spot,
    ):
        financial_indicator.return_value = pd.DataFrame([{"每股净资产(元)": "10"}])
        stock_daily.return_value = pd.DataFrame([{"close": 25.0}])

        result = stock.fundamental_analysis("600519")

        self.assertEqual(result["pb_approx"], 2.5)
        stock_daily.assert_called_once_with("sh600519")


if __name__ == "__main__":
    unittest.main()
