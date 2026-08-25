"""直接 API 解析与并发分页测试（不访问网络）。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.data import fetcher


class FastFetcherTests(unittest.TestCase):
    @patch("backend.data.fetcher.get_json")
    def test_tencent_daily_parser(self, get_json):
        get_json.return_value = {
            "data": {"sh600519": {"qfqday": [
                ["2026-08-20", "1400", "1410", "1420", "1390", "12345"],
                ["2026-08-21", "1410", "1430", "1440", "1400", "23456"],
            ]}}
        }
        frame = fetcher._tencent_daily("sh600519", "qfq", 320)
        self.assertEqual(list(frame.columns), ["date", "open", "close", "high", "low", "volume"])
        self.assertEqual(frame.iloc[-1]["close"], 1430)

    @patch("backend.data.fetcher.get_json")
    def test_sina_snapshot_pages_are_combined(self, get_json):
        def response(url, params=None, **_kwargs):
            if url.endswith("StockCount"):
                return "1000"
            page = int(params["page"])
            return [{
                "code": f"{page:02d}{index:04d}", "name": "测试", "trade": "10",
                "pricechange": 0.1, "changepercent": 1, "settlement": 9.9,
                "open": 9.9, "high": 10.1, "low": 9.8, "volume": 100,
                "amount": 1000, "ticktime": "15:00:00",
            } for index in range(100)]
        get_json.side_effect = response
        frame = fetcher._sina_market_spot()
        self.assertEqual(len(frame), 1000)
        self.assertIn("涨跌幅", frame.columns)

    @patch("backend.data.fetcher.get_json")
    def test_limit_pool_parser(self, get_json):
        get_json.return_value = {"data": {"pool": [{
            "c": "600000", "n": "浦发银行", "p": 12340, "zdp": 10,
            "amount": 100, "ltsz": 200, "tshare": 300, "hs": 2,
            "fund": 50, "fbt": 93001, "lbt": 145900, "zbc": 1,
            "lbc": 2, "hybk": "银行", "zttj": {"days": 2, "ct": 2},
        }]}}
        frame = fetcher._pool_frame("zt", "20260821")
        self.assertEqual(frame.iloc[0]["名称"], "浦发银行")
        self.assertEqual(frame.iloc[0]["最新价"], 12.34)
        self.assertEqual(frame.iloc[0]["连板数"], 2)


if __name__ == "__main__":
    unittest.main()
