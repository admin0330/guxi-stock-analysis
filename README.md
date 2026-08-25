# 股析：A 股与加密资产分析系统

这是一个可在 Windows 本地运行的 FastAPI + 原生 Web 前端分析系统，包含 A 股行情分析、每日关注池、涨停复盘、加密资产看板，以及默认关闭的 Binance Futures Testnet 交易模块。

## 仓库内容

- `01-本地EXE源码/`：后端、前端、交易模块、测试和 Windows 打包脚本。
- `DEPLOY_LOCAL.md`：源码运行、配置和便携版打包方式。
- `05-项目文档/`：第三方许可和验收记录。

本仓库只发布源码和本地部署说明，不包含本机运行数据、日志、缓存、数据库、构建产物、服务器部署记录或真实 `.env` 文件。

## 快速开始

```powershell
cd 01-本地EXE源码
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

服务默认地址：<http://127.0.0.1:8765>。

完整步骤见 [DEPLOY_LOCAL.md](DEPLOY_LOCAL.md)。

> 本系统仅供学习研究和信息参考，不构成投资建议。交易模块默认使用 Testnet 且自动交易关闭；不要在仓库或聊天中提交任何 API Key、Secret、Token 或管理员密码。
