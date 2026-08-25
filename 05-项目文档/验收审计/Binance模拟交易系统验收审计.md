# Binance 模拟交易系统验收审计

- 交易环境：Binance USDⓈ-M Futures Testnet（默认且强制安全回退）
- REST：原生 HMAC-SHA256 签名、短超时、重试、服务器时间校准
- WebSocket：BTC/ETH ticker、资金费率、5 分钟 K 线，以及账户订单/成交/仓位推送
- 交易：市价/限价、改单、撤单、杠杆、Reduce-Only 平仓；市价开仓后独立提交硬止损与止盈
- 安全：密钥只从 `.env` 读取，网页和日志不回显；主网仍需配置开关、会话解锁和逐单确认
- 自动验收：`56 passed`（交易、风控、API、余额币种、紧急停止、托盘退出、端口释放、前端与便携目录）
- 在线公共链路：REST ticker/K 线/交易规则通过；WebSocket 已收到 BTCUSDT、ETHUSDT，K 线缓存各 240 根
- 历史便携 EXE 已在 2026-08-26 源码清理中移入 Windows 回收站；当前交付以 `04-构建与成品/dist/A股分析系统.exe` 为准
- 真实账户读取：主要稳定币余额 `5000 USDC`；当前 USDT 交易保证金 `0`
- SHA-256：`A6FC1821EFBBF170B21E816F66C189C1499B3BABE913AE0FD2EF9EC1E1A87DC5`
- 实盘式模拟验收：配置 Testnet Key 后执行：

```powershell
$env:BINANCE_TESTNET_ACCEPTANCE="RUN_LIVE_TESTNET"
cd 01-本地EXE源码
python tests/binance_testnet_acceptance.py
```

当前未写入 Binance Testnet 密钥，因此私有账户与真实模拟订单验收需在填入新密钥后执行。
