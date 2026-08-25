import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from backend import config
from backend.data import cache


class RuntimeTests(unittest.TestCase):
    def test_env_file_does_not_override_system_environment(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GUXI_PORT": "9000"}, clear=True):
            path = Path(tmp) / ".env"
            path.write_text("GUXI_PORT=8765\nCRYPTO_PRIMARY_SOURCE='okx'\n", encoding="utf-8")
            config._load_env(path)
            self.assertEqual(os.environ["GUXI_PORT"], "9000")
            self.assertEqual(os.environ["CRYPTO_PRIMARY_SOURCE"], "okx")

    def test_cache_roundtrip_stale_and_prefix_clear(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(cache, "CACHE_DIR", Path(tmp)):
            frame = pd.DataFrame({"close": [10.5]})
            cache.set("stock_600519", frame)
            cache.set("market_spot", frame)
            self.assertEqual(cache.get("stock_600519", 60).iloc[0]["close"], 10.5)

            meta = next(Path(tmp).glob("*stock_600519.json"))
            info = json.loads(meta.read_text(encoding="utf-8"))
            info["ts"] = time.time() - 100
            meta.write_text(json.dumps(info), encoding="utf-8")
            self.assertIsNone(cache.get("stock_600519", 1))
            self.assertTrue(cache.get("stock_600519", 1, allow_stale=True).is_stale)
            self.assertEqual(cache.clear("stock_"), 1)
            self.assertIsNotNone(cache.get("market_spot", 60))

    def test_legacy_cache_without_suffix_is_read_and_cleared(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(cache, "CACHE_DIR", Path(tmp)):
            path = Path(tmp) / cache._safe_key("legacy_item")
            pd.DataFrame({"v": [1]}).to_parquet(path, index=False)
            self.assertEqual(cache.get("legacy_item", 60).iloc[0]["v"], 1)
            self.assertEqual(cache.clear(), 1)


if __name__ == "__main__":
    unittest.main()
