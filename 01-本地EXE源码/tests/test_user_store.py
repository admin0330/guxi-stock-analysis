import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.data import user_store


class UserStoreTests(unittest.TestCase):
    def test_state_persists_and_rejects_unknown_values(self):
        with tempfile.TemporaryDirectory() as temp:
            state_file = Path(temp) / "用户状态.json"
            with patch.object(user_store.config, "USER_STATE_FILE", state_file):
                state = user_store.update({
                    "watchlist": ["sh600519", "bad", "sh600519"],
                    "last_page": "stock",
                    "crypto_refresh_seconds": 300,
                    "unknown": "ignored",
                })
                self.assertEqual(state["watchlist"], ["sh600519"])
                self.assertEqual(user_store.load()["last_page"], "stock")
                self.assertEqual(user_store.load()["crypto_refresh_seconds"], 300)
                self.assertNotIn("unknown", user_store.load())

    def test_watchlist_add_remove_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(user_store.config, "USER_STATE_FILE", Path(temp) / "state.json"):
                user_store.add_watch("sz000001")
                user_store.add_watch("sz000001")
                self.assertEqual(user_store.load()["watchlist"], ["sz000001"])
                user_store.remove_watch("sz000001")
                self.assertEqual(user_store.load()["watchlist"], [])

    def test_authenticated_users_have_isolated_state(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(user_store.config, "USER_STATE_DIR", Path(temp)):
            user_store.add_watch("sh600519", 11)
            user_store.add_watch("sz000001", 12)
            self.assertEqual(user_store.load(11)["watchlist"], ["sh600519"])
            self.assertEqual(user_store.load(12)["watchlist"], ["sz000001"])
            user_store.delete_user_state(11)
            self.assertEqual(user_store.load(11)["watchlist"], [])


if __name__ == "__main__":
    unittest.main()
