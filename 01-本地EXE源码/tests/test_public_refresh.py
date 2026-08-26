import unittest
from unittest.mock import patch

from backend.data import public_refresh


class PublicRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_failed_job_does_not_stop_the_others(self):
        called = []

        def succeeds():
            called.append("ok")

        def fails():
            called.append("failed")
            raise RuntimeError("offline")

        with patch.object(public_refresh, "_JOBS", (("成功项", succeeds), ("失败项", fails))):
            self.assertEqual(await public_refresh.refresh_once(), (1, 1))
        self.assertCountEqual(called, ["ok", "failed"])


if __name__ == "__main__":
    unittest.main()
