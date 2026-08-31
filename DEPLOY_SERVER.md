# Linux 服务器部署

公网实例为 `ym3861.cn`，当前部署目录为 `/srv/ym3861-app`，systemd 服务为
`ym3861-app.service`，应用只监听 `127.0.0.1:8000`。详细迁移、Cloudflare、Nginx、
备份和 Komari 替换步骤见 [`02-服务器部署/DEPLOY.md`](02-服务器部署/DEPLOY.md)。

## 当前发布方式

从 `01-本地EXE源码` 打包时排除 `.env`、数据库、缓存和日志，再上传到服务器新 release：

```bash
RELEASE_ID="$(date -u +%Y%m%d-%H%M%S)"
RELEASE_DIR="/srv/ym3861-app/releases/$RELEASE_ID"
sudo install -d -o root -g ymapp -m 0750 "$RELEASE_DIR"
sudo tar -xzf /tmp/ym3861-app-release.tgz -C "$RELEASE_DIR"
sudo python3 -m venv "$RELEASE_DIR/.venv"
sudo "$RELEASE_DIR/.venv/bin/pip" install -r "$RELEASE_DIR/requirements.txt"
sudo ln -sfn "$RELEASE_DIR" /srv/ym3861-app/current
sudo systemctl restart ym3861-app
sudo sh /srv/ym3861-app/current/deploy/verify-deployment.sh http://127.0.0.1:8000
```

生产环境变量只放在 `/etc/ym3861-app/ym3861-app.env`，权限为 `0640 root:ymapp`。
首次管理员创建后立即删除 `ADMIN_USERNAME`、`ADMIN_PASSWORD` 两个初始化变量；真实
Binance Key 只用于余额、持仓、挂单、成交和行情查询，交易所侧必须关闭交易、提现和转账权限。

## HTTPS、反代与备份

- Cloudflare DNS 指向服务器，SSL 使用 `Full (strict)`，源站使用有效证书。
- Nginx 反代 `/stock`、API 和 WebSocket；不直接暴露 `8000`。
- `ym3861-backup.timer` 每日备份共享数据和环境文件，默认保留 7 份；备份包必须加密复制到服务器外。
- 不缓存 `/api/*`、`/ws/*`、`/login` 和 `/admin`。

## TeamSpeak 保留规则

部署只允许重启 `ym3861-app` 和 reload Nginx。不能停止或重建 `teamspeak3-server`，必须保留：

- `9987/UDP`：语音
- `30033/TCP`：文件传输

启用 UFW 时明确放行上述两个端口、SSH `22/TCP`、HTTP `80/TCP` 和 HTTPS `443/TCP`，并在新 SSH 会话及 `docker ps` 中复核 TeamSpeak 仍为 `Up`。

## 上线检查

- [ ] `/stock` 未登录返回 303，业务 API 未登录返回 401
- [ ] 登录 Cookie 为 Secure、HttpOnly、SameSite=Lax
- [ ] admin 可管理用户，普通用户不能访问 `/admin`
- [ ] Binance 仅查询；下单、撤单、改单、平仓、自动交易和紧急停止路由不存在
- [ ] `ym3861-backup.timer` 已启用并有可校验备份
- [ ] TeamSpeak、邮件箱、Nginx、Fail2ban 均保持运行
- [ ] Komari 仅在备份并完成应用验收后，按精确资源删除
