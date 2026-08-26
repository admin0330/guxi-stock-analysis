"""单元测试永不加载开发机真实交易凭据。"""
import os
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

os.environ["BINANCE_API_KEY"] = ""
os.environ["BINANCE_API_SECRET"] = ""
os.environ["BINANCE_TESTNET"] = "true"
os.environ["AUTH_ENABLED"] = "false"
os.environ["PUBLIC_DATA_REFRESH_ENABLED"] = "false"
