"""stock-open-api 数据源适配测试（不访问网络）。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from backend.data import fetcher


class StockOpenApiTests(unittest.TestCase):
    @patch("backend.data.fetcher.cache.set")
    @patch("backend.data.fetcher.cache.get", return_value=None)
    @patch("stock_open_api.api.eastmoney.company.get_company_info")
    def test_company_profile_is_normalized(self, get_company_info, _cache_get, cache_set):
        get_company_info.return_value = {
            "公司名称": "贵州茅台酒股份有限公司",
            "A股简称": "贵州茅台",
            "上市交易所": "上海证券交易所",
            "所属东财行业": "食品饮料-白酒",
            "上市日期": "2001-08-27",
            "雇员人数": 30000,
        }

        profile = fetcher.stock_open_api_company_profile("600519")

        get_company_info.assert_called_once_with("SH600519")
        self.assertEqual(profile["symbol"], "sh600519")
        self.assertEqual(profile["short_name"], "贵州茅台")
        self.assertEqual(profile["exchange"], "上海证券交易所")
        self.assertEqual(profile["employees"], 30000)
        cache_set.assert_called_once()

    @patch.object(fetcher.config, "STOCK_OPEN_API_ENABLED", False)
    def test_disabled_source_returns_empty_profile(self):
        self.assertEqual(fetcher.stock_open_api_company_profile("600519"), {})

    @patch("backend.data.fetcher.cache.get", return_value=None)
    @patch("stock_open_api.api.eastmoney.company.get_company_info", side_effect=RuntimeError("offline"))
    @patch.object(fetcher.config, "MAX_RETRIES", 0)
    def test_source_failure_does_not_break_analysis(self, _get_company_info, _cache_get):
        self.assertEqual(fetcher.stock_open_api_company_profile("600519"), {})

    @patch("backend.data.fetcher.cache.get")
    @patch("stock_open_api.api.eastmoney.company.get_company_info", side_effect=RuntimeError("offline"))
    @patch.object(fetcher.config, "MAX_RETRIES", 0)
    def test_source_failure_uses_stale_cache(self, _get_company_info, cache_get):
        stale = type("Stale", (), {
            "is_stale": True,
            "df": pd.DataFrame([{"symbol": "sh600519", "short_name": "贵州茅台"}]),
        })()
        cache_get.return_value = stale

        profile = fetcher.stock_open_api_company_profile("600519")

        self.assertEqual(profile["short_name"], "贵州茅台")


if __name__ == "__main__":
    unittest.main()
