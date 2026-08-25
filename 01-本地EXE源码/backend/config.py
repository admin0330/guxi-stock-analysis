"""系统配置：路径、缓存、超时等。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# PyInstaller 内嵌资源、源码目录与运行数据目录分开处理。
FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else ROOT
PROJECT_DIR = APP_DIR if FROZEN else ROOT.parent
RUNTIME_DIR = APP_DIR if FROZEN else PROJECT_DIR / "03-本地运行数据"


def _load_env(path: Path) -> None:
    """加载简单 KEY=VALUE 配置；系统环境变量优先。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key and (not value or value[:1] not in "\"'" or value[-1:] == value[:1]):
            os.environ.setdefault(key, value[1:-1] if len(value) >= 2 and value[:1] in "\"'" else value)


def _load_yaml(path: Path) -> None:
    """读取便携目录中的扁平 YAML 配置，环境变量仍具有最高优先级。"""
    if not path.exists():
        return
    try:
        import yaml

        values = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except Exception:
        return
    mapping = {
        "port": "GUXI_PORT",
        "open_browser": "GUXI_OPEN_BROWSER",
        "cache_ttl_short": "CACHE_TTL_SHORT",
        "cache_ttl_daily": "CACHE_TTL_DAILY",
        "cache_ttl_static": "CACHE_TTL_STATIC",
        "request_timeout": "REQUEST_TIMEOUT",
        "max_retries": "MAX_RETRIES",
        "crypto_refresh_seconds": "CRYPTO_REFRESH_SECONDS",
        "crypto_primary_source": "CRYPTO_PRIMARY_SOURCE",
        "log_retention_days": "GUXI_LOG_DAYS",
        "log_level": "GUXI_LOG_LEVEL",
        "daily_pick_top_k": "DAILY_PICK_TOP_K",
        "daily_pick_pool_size": "DAILY_PICK_POOL_SIZE",
        "daily_pick_min_amount": "DAILY_PICK_MIN_AMOUNT",
        "daily_pick_min_listing_days": "DAILY_PICK_MIN_LISTING_DAYS",
        "daily_pick_exclude_growth_boards": "DAILY_PICK_EXCLUDE_GROWTH_BOARDS",
    }
    for key, env_name in mapping.items():
        if key in values:
            value = values[key]
            os.environ.setdefault(env_name, str(value).lower() if isinstance(value, bool) else str(value))


def _env_int(name: str, default: int, minimum: int = 0, maximum: int = 86400) -> int:
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() not in {"0", "false", "no", "off"}


CONFIG_FILE = APP_DIR / "config.yaml"
ENV_FILE = RUNTIME_DIR / ".env"
_load_yaml(CONFIG_FILE)
_load_env(ENV_FILE)

# 默认全部写入应用目录，复制整个文件夹即可迁移；环境变量可显式覆盖。
DATA_DIR = Path(os.getenv("GUXI_DATA_DIR", RUNTIME_DIR))
if not DATA_DIR.is_absolute():
    DATA_DIR = APP_DIR / DATA_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 本地数据缓存目录
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
USER_STATE_FILE = DATA_DIR / "user-state.json"
REPORT_DIR = DATA_DIR / "data" / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
DAILY_PICK_DIR = DATA_DIR / "data" / "daily-picks"
DAILY_PICK_DIR.mkdir(parents=True, exist_ok=True)
AUTH_DB_FILE = Path(os.getenv("AUTH_DB_PATH", DATA_DIR / "data" / "auth.sqlite3"))
if not AUTH_DB_FILE.is_absolute():
    AUTH_DB_FILE = DATA_DIR / AUTH_DB_FILE
AUTH_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
USER_STATE_DIR = DATA_DIR / "data" / "user-state"
USER_STATE_DIR.mkdir(parents=True, exist_ok=True)
FRONTEND_DIR = APP_DIR / "frontend" if (APP_DIR / "frontend").exists() else ROOT / "frontend"

HOST = "127.0.0.1"
PORT = _env_int("GUXI_PORT", 8765, 1024, 65535)
AUTO_OPEN_BROWSER = _env_bool("GUXI_OPEN_BROWSER")

# 网站登录与公网部署。会话令牌只保存在 HttpOnly Cookie，数据库仅保存令牌哈希。
AUTH_ENABLED = _env_bool("AUTH_ENABLED", True)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", f"http://{HOST}:{PORT}").rstrip("/")
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "guxi_session").strip() or "guxi_session"
SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", PUBLIC_BASE_URL.lower().startswith("https://"))
SESSION_MAX_AGE = _env_int("SESSION_MAX_AGE", 7 * 86400, 900, 30 * 86400)
SESSION_IDLE_TIMEOUT = _env_int("SESSION_IDLE_TIMEOUT", 24 * 3600, 300, 7 * 86400)
PASSWORD_MIN_LENGTH = _env_int("PASSWORD_MIN_LENGTH", 10, 8, 128)
PASSWORD_REQUIRE_MIXED = _env_bool("PASSWORD_REQUIRE_MIXED", True)
LOGIN_MAX_FAILURES = _env_int("LOGIN_MAX_FAILURES", 5, 3, 20)
LOGIN_WINDOW_SECONDS = _env_int("LOGIN_WINDOW_SECONDS", 15 * 60, 60, 86400)
LOGIN_LOCK_SECONDS = _env_int("LOGIN_LOCK_SECONDS", 15 * 60, 60, 86400)
ALLOWED_HOSTS = {item.strip().lower() for item in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,::1,testserver").split(",") if item.strip()}
TRUSTED_PROXIES = {item.strip() for item in os.getenv("TRUSTED_PROXIES", "127.0.0.1,::1").split(",") if item.strip()}

# 缓存有效期（秒）：盘中短、盘后长
CACHE_TTL_SHORT = _env_int("CACHE_TTL_SHORT", 30, 10, 3600)
CACHE_TTL_DAILY = _env_int("CACHE_TTL_DAILY", 6 * 3600)
CACHE_TTL_STATIC = _env_int("CACHE_TTL_STATIC", 24 * 3600)

# 请求超时与重试
REQUEST_TIMEOUT = _env_int("REQUEST_TIMEOUT", 5, 3, 30)
MAX_RETRIES = _env_int("MAX_RETRIES", 2, 0, 10)
try:
    RETRY_BACKOFF = max(0.1, min(30.0, float(os.getenv("RETRY_BACKOFF", "1.5"))))
except ValueError:
    RETRY_BACKOFF = 1.5

# 加密货币公开行情（BTC / ETH）
CRYPTO_REFRESH_SECONDS = _env_int("CRYPTO_REFRESH_SECONDS", 60, 10, 3600)
CRYPTO_TTL_TICKER = _env_int("CRYPTO_TTL_TICKER", 30)
CRYPTO_TTL_KLINE = _env_int("CRYPTO_TTL_KLINE", 60)
CRYPTO_PRIMARY_SOURCE = os.getenv("CRYPTO_PRIMARY_SOURCE", "binance").strip().lower()

# stock-open-api 公司资料源。设为 0/false/no/off 可关闭，关闭后其余分析不受影响。
STOCK_OPEN_API_ENABLED = _env_bool("STOCK_OPEN_API_ENABLED")

# 每日关注池：先从活跃股缩池，再并行精算日线，避免全市场逐股慢爬。
DAILY_PICK_TOP_K = _env_int("DAILY_PICK_TOP_K", 8, 5, 10)
DAILY_PICK_POOL_SIZE = _env_int("DAILY_PICK_POOL_SIZE", 48, 20, 120)
DAILY_PICK_MIN_AMOUNT = _env_int("DAILY_PICK_MIN_AMOUNT", 100_000_000, 10_000_000, 10_000_000_000)
DAILY_PICK_MIN_LISTING_DAYS = _env_int("DAILY_PICK_MIN_LISTING_DAYS", 120, 30, 320)
DAILY_PICK_EXCLUDE_GROWTH_BOARDS = _env_bool("DAILY_PICK_EXCLUDE_GROWTH_BOARDS", False)

LOG_RETENTION_DAYS = _env_int("GUXI_LOG_DAYS", 14, 1, 365)
LOG_LEVEL = os.getenv("GUXI_LOG_LEVEL", "INFO").strip().upper()

# 主要指数代码（新浪格式）
INDEX_SYMBOLS = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000688": "科创50",
    "sh000300": "沪深300",
    "sh000905": "中证500",
    "bj899050": "北证50",
}

# 全市场快照的日涨跌统计用
SPOT_COLUMNS = ["代码", "名称", "最新价", "涨跌额", "涨跌幅", "昨收", "今开", "最高", "最低",
                "成交量", "成交额", "时间戳"]

APP_TITLE = "A股分析系统"
APP_VERSION = "1.0.0"
