# 配置说明

## 配置来源与优先级

应用读取顺序为：系统/进程环境变量优先，其次是 `config.yaml` 中映射的本地参数，最后才补充 `.env` 中尚未设置的变量。

- 源码模式：`.env` 位于项目根目录的 `03-本地运行数据/.env`。
- Windows 便携版：`.env` 位于 `A股分析系统.exe` 同目录。
- systemd：使用 `/etc/guxi/guxi.env` 注入环境变量。

仓库只跟踪 `.env.example`。所有凭据字段保持空值，真实 `.env` 被 `.gitignore` 排除。

## 敏感环境变量

| 变量 | 用途 | 仓库模板 |
|---|---|---|
| `BINANCE_API_KEY` | Binance API Key | 空 |
| `BINANCE_API_SECRET` | Binance API Secret | 空 |
| `TELEGRAM_BOT_TOKEN` | Telegram 通知令牌 | 空 |
| `TELEGRAM_CHAT_ID` | Telegram 接收方 | 空 |
| `ADMIN_USERNAME` | 可选的首次管理员初始化 | 空或省略 |
| `ADMIN_PASSWORD` | 可选的首次管理员密码 | 空或省略 |

推荐通过 `python -m backend.auth create-admin [用户名]` 交互式创建管理员，避免密码进入文件或命令历史。

## 登录与服务器变量

| 变量 | 默认/要求 | 说明 |
|---|---|---|
| `AUTH_ENABLED` | `true` | 是否启用登录 |
| `PUBLIC_BASE_URL` | 本地地址；公网必填 | 公网完整 HTTPS 地址 |
| `ALLOWED_HOSTS` | 本地地址；公网必填 | 允许的 Host，逗号分隔 |
| `TRUSTED_PROXIES` | `127.0.0.1,::1` | 可被信任的反向代理 |
| `SESSION_COOKIE_SECURE` | HTTPS 时为 `true` | 仅通过 HTTPS 发送 Cookie |
| `SESSION_MAX_AGE` | `604800` | Session 最长秒数 |
| `SESSION_IDLE_TIMEOUT` | `86400` | Session 空闲超时秒数 |
| `PASSWORD_MIN_LENGTH` | `10` | 密码最短长度 |
| `PASSWORD_REQUIRE_MIXED` | `true` | 要求多类字符组合 |
| `LOGIN_MAX_FAILURES` | `5` | 登录失败锁定阈值 |

## 运行变量

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `GUXI_PORT` | `8765` | 本地首选端口 |
| `GUXI_OPEN_BROWSER` | `true` | 启动后打开浏览器 |
| `GUXI_DATA_DIR` | 本地运行数据目录 | 数据、缓存和日志根目录 |
| `GUXI_LOG_LEVEL` | `INFO` | 日志等级 |
| `GUXI_LOG_DAYS` | `14` | 日志保留天数 |
| `AUTH_DB_PATH` | 数据目录下的 SQLite | 登录数据库路径 |
| `TRADING_SETTINGS_PATH` | `trading/config/settings.yaml` | 交易公开参数文件 |
| `BINANCE_TESTNET` | `true` | 是否使用 Binance Futures Testnet |
| `STOCK_OPEN_API_ENABLED` | `true` | 是否启用公司资料补充源 |
| `PUBLIC_DATA_REFRESH_ENABLED` | `true` | 是否在后台持续预热固定公共行情页面 |
| `PUBLIC_DATA_REFRESH_SECONDS` | `30` | 两轮预热之间的等待秒数，范围 10–3600 |

缓存、后台预热、超时、重试、币圈刷新和每日关注池参数可通过 `CACHE_TTL_*`、`PUBLIC_DATA_REFRESH_*`、`REQUEST_TIMEOUT`、`MAX_RETRIES`、`CRYPTO_*`、`DAILY_PICK_*` 环境变量覆盖；本地常用值已写入 `01-本地EXE源码/config.yaml`。

## `config.yaml`

该文件只保存非敏感运行参数，例如端口、缓存时间、请求超时、日志等级和每日关注池规则。不要把 API Key、密码或 Token 写入 YAML。

## `trading/config/settings.yaml`

该文件保存交易对、策略、杠杆与风控参数，不保存交易所凭据。安全默认值为：

- `enable_auto_trade: false`
- `allow_mainnet: false`
- 最大杠杆与仓位限制启用
- API Key/Secret 只从环境变量读取

## 提交前检查

```powershell
git status --short
git ls-files | Select-String -Pattern '(^|/)\.env$|\.(pem|key|pfx|p12)$'
git grep -n -I -E 'BEGIN (RSA|OPENSSH|EC|PRIVATE) KEY|BINANCE_API_(KEY|SECRET)=.+'
```

如任何真实凭据曾进入 Git，即使随后删除也必须立即撤销并重新生成，同时清理整个 Git 历史。
