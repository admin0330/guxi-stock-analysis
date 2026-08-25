import asyncio
import json
import unittest
from unittest.mock import patch

from backend.data.crypto_stream import BACKOFF_SECONDS, CryptoStream, parse_binance_message


class CryptoStreamTests(unittest.IsolatedAsyncioTestCase):
    def test_parses_combined_mini_ticker(self):
        ticker = parse_binance_message(json.dumps({"stream": "btcusdt@miniTicker", "data": {
            "E": 1_700_000_000_000, "s": "BTCUSDT", "c": "110", "o": "100",
            "h": "115", "l": "95", "v": "12", "q": "1234",
        }}))
        self.assertEqual(ticker["asset"], "BTC")
        self.assertEqual(ticker["price"], 110)
        self.assertAlmostEqual(ticker["change_24h"], 10)
        self.assertEqual(ticker["event_time"], 1_700_000_000_000)
        self.assertEqual(BACKOFF_SECONDS, (1, 2, 5, 10, 20, 30))

    async def test_rest_fallback_publishes_lightweight_quote(self):
        stream = CryptoStream()
        stream.status = "fallback"
        stream._stop = asyncio.Event()
        row = {"asset": "BTC", "price": 100, "change_24h": 1, "source": "OKX", "updated_at": "now"}
        with patch("backend.data.crypto_stream.crypto.realtime_snapshot", return_value=[row]):
            task = asyncio.create_task(stream._fallback_loop())
            await asyncio.sleep(2.05)
            stream._stop.set()
            await task
        self.assertEqual(stream.latest["BTC"]["price"], 100)
        self.assertTrue(stream.latest["BTC"]["fallback"])

    async def test_subscriber_gets_snapshot_and_shutdown_clears_tasks(self):
        stream = CryptoStream()
        async def wait_forever():
            while stream._stop is None:
                await asyncio.sleep(0)
            await stream._stop.wait()
        stream._ws_loop = wait_forever
        stream._fallback_loop = wait_forever
        await stream.start()
        queue = stream.subscribe()
        self.assertEqual((await queue.get())["type"], "snapshot")
        await stream.stop()
        self.assertFalse(stream._tasks)
        self.assertEqual(stream.status, "disconnected")


if __name__ == "__main__":
    unittest.main()
