# 股析 v1.0.0

股析是一套可本地运行或部署到 Linux 服务器的 A 股与加密资产分析系统。后端使用 FastAPI，前端使用原生 HTML、CSS、JavaScript 与内置 ECharts，并提供 Windows 便携版打包入口。

> 本系统仅供学习研究与信息参考，不构成投资建议。行情可能延迟、缺失或因上游变化暂不可用；加密资产和杠杆交易可能造成全部本金损失。

## 功能

- 市场总览：主要指数、涨跌分布、成交额、北向资金与市场温度。
- 个股透视：代码/名称搜索、技术面、基本面、资金面和 K 线。
- 每日关注池：按成交活跃度、趋势与技术指标生成本地候选结果。
- 涨停复盘与小白日报：情绪、连板、行业、涨跌榜、板块和历史快照。
- 加密资产看板：BTC/ETH 行情、K 线、MA、MACD、RSI、BOLL 与 WebSocket 实时价格。
- Binance Futures 交易台：Testnet/Mainnet 状态、账户、仓位、订单、策略和风控；默认 Testnet、自动交易关闭、Mainnet 关闭。
- 登录与用户管理：服务端 Session、CSRF、防暴力破解、管理员后台和用户数据隔离。
- Windows 桌面入口：启动本地服务、自动打开浏览器并提供托盘退出。
- 公共数据预热：服务运行期间持续刷新固定页面缓存，访问首页、日报、涨停、关注池和默认币圈分析时优先读取热缓存。

## 技术组成

- Python 3.10+、FastAPI、Uvicorn
- pandas、NumPy、AKShare、PyArrow、TA
- 原生 Web 前端、ECharts、WebSocket
- SQLite 本地状态与审计数据
- PyInstaller Windows 单文件打包

## 仓库结构

```text
.
├── 01-本地EXE源码/
│   ├── backend/             API、鉴权、行情和分析逻辑
│   ├── frontend/            页面、样式、脚本和品牌资源
│   ├── trading/             交易所连接、策略、风控和审计
│   ├── tests/               单元、集成和浏览器冒烟测试
│   ├── run.py               本地源码入口
│   └── build.bat            Windows 便携版打包入口
├── deploy/                  通用 systemd、Nginx 和环境模板
├── 05-项目文档/             第三方许可与验收记录
├── CONFIGURATION.md         配置项与敏感信息边界
├── DEPLOY_LOCAL.md          本地运行和 Windows 打包
└── DEPLOY_SERVER.md         通用 Linux 服务器部署
```

运行数据、数据库、日志、缓存、真实 `.env`、私钥、生产服务器资料和构建产物均被排除，不属于仓库内容。

## 快速开始

```powershell
cd 01-本地EXE源码
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m backend.auth create-admin admin
python run.py
```

打开 <http://127.0.0.1:8765>。不自动打开浏览器时运行 `python run.py --no-browser`。

## 部署与配置

- [本地运行与 Windows 便携版](DEPLOY_LOCAL.md)
- [Linux 服务器部署](DEPLOY_SERVER.md)
- [完整配置说明](CONFIGURATION.md)

仓库只提供空值凭据模板。真实配置应写入被 Git 忽略的本机 `.env` 或服务器 `/etc/guxi/guxi.env`，不能写入源码、Issue、日志或聊天记录。

## 安全默认值

- 本地服务只监听 `127.0.0.1`。
- Binance Futures 默认使用 Testnet。
- 自动交易和 Mainnet 默认关闭。
- 密钥只由后端从环境变量读取，不返回前端。
- 登录密码使用 scrypt 哈希；Session 数据库只保存令牌摘要。
- 写 API 校验 CSRF，生产环境应启用 Secure/HttpOnly/SameSite Cookie。

## 主要接口

| 路径 | 说明 |
|---|---|
| `GET /api/health` | 服务版本与健康状态 |
| `GET /api/market/*` | 指数、宽度、成交额与市场温度 |
| `GET /api/stock/*` | 搜索、个股资料、技术面、资金面与 K 线 |
| `GET /api/limitup/*` | 涨停情绪与涨停池 |
| `GET /api/daily-picks*` | 每日关注池、历史和规则 |
| `GET /api/daily/*` | 日报模块与历史快照 |
| `GET /api/crypto/*`、`WS /ws/crypto` | 加密资产分析与实时行情 |
| `/api/trading/*` | 交易状态、账户、仓位、订单、策略与风控 |
| `/api/auth/*`、`/api/admin/*` | 登录、会话和用户管理 |

开发模式可在登录后访问 `/docs` 查看完整 OpenAPI 文档。

## 验证

```powershell
cd 01-本地EXE源码
python -m compileall -q backend trading desktop.py run.py
python -m pytest -q
node --check frontend/js/app.js
```

浏览器冒烟脚本需要先启动服务，再运行 `node tests/browser_smoke.js`。

## 数据与隐私

源码模式的数据库、缓存、日志和用户状态默认写入项目根目录的 `03-本地运行数据/`；便携版写入 EXE 同目录。上述路径均不提交 Git。交易审计库不会保存 API Secret，但仍属于本地私有数据。

第三方软件许可摘要见 [THIRD_PARTY_NOTICES.md](05-项目文档/THIRD_PARTY_NOTICES.md)。当前仓库尚未附带开源许可证；公开可见不等于自动授予复制、修改或再分发许可。
