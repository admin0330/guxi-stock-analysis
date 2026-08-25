# 本地部署说明

## 1. 环境

- Windows 10/11
- Python 3.10+
- 可访问公开行情接口的网络

## 2. 源码运行

```powershell
cd 01-本地EXE源码
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m backend.auth create-admin admin
python run.py
```

不自动打开浏览器：

```powershell
python run.py --no-browser
```

打开 <http://127.0.0.1:8765>；API 文档在 `/docs`。如果启用登录，首次管理员凭据只在本机未提交的 `.env` 中设置。

## 3. 配置

源码模式默认从项目根目录的 `03-本地运行数据/.env` 读取环境变量；需要配置时，可将 `01-本地EXE源码/.env.example` 复制为该路径后再按需填写。便携版则把 `dist/.env.example` 复制为 EXE 同目录的 `.env`。模板中的值全部为空；不需要交易、通知或管理员初始化时可以保持为空。`03-本地运行数据` 和真实 `.env` 被 `.gitignore` 排除，禁止提交。

已有运行环境需要补建管理员时：

```powershell
cd 01-本地EXE源码
python -m backend.auth create-admin admin
```

命令会交互式读取密码，不会把密码写入命令历史。完整变量、YAML 配置和优先级见 [CONFIGURATION.md](CONFIGURATION.md)。

交易模块默认是 Binance Futures Testnet，自动交易和 Mainnet 均关闭。只有在本机确认风险、填写 Testnet 凭据并完成测试后，才使用交易功能；不要把任何密钥写入源码、配置模板或 Git 历史。

## 4. Windows 便携版

```powershell
cd 01-本地EXE源码
build.bat
```

脚本会在项目根目录生成 `04-构建与成品/dist`，中间文件放在 `04-构建与成品/build`。这两个目录是生成物，不纳入 Git；可将生成的目录复制到另一台 Windows 电脑运行。

便携版首次运行前，将 `dist/.env.example` 复制为 `dist/.env`；如需登录，建议使用交互命令创建管理员，或仅在首次启动时临时填写管理员初始化字段。创建成功后立即清空初始化密码。

## 5. 本地数据目录

- `03-本地运行数据/data/`：登录、用户状态、日报和交易审计数据库。
- `03-本地运行数据/cache/`：可重新获取的行情缓存。
- `03-本地运行数据/logs/`：应用日志。
- `04-构建与成品/`：PyInstaller 中间文件与便携版成品。

这些目录不会上传 Git。备份或迁移时应单独加密处理运行数据，不能把它们复制进仓库。

## 6. 验证

```powershell
cd 01-本地EXE源码
python -m compileall -q backend trading desktop.py run.py
python -m pytest -q
```

浏览器冒烟测试需要 Node.js，并在服务启动后运行：

```powershell
node tests/browser_smoke.js
```

## 7. 常见问题

- 启动后无法登录：先运行管理员创建命令，并确认 `AUTH_ENABLED` 未关闭。
- 端口被占用：程序会从 `8765` 起寻找可用端口，实际地址会写入日志。
- 行情暂不可用：检查网络和上游接口状态；系统会优先返回可用缓存。
- 交易台未连接：只有本机 `.env` 同时设置 API Key 和 Secret 后才连接私有账户接口。

## 8. 安全边界

以下内容永远不应进入提交：`.env`、API Key/Secret、Telegram Token、管理员密码、私钥、数据库、缓存、日志、用户状态和 EXE 构建目录。若凭据曾经出现在本机配置或日志中，应先撤销并重新生成，再继续使用。
