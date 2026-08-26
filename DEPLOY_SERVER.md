# Linux 服务器部署

以下为通用 Debian/Ubuntu + systemd + Nginx 部署方式。示例使用 `example.com`、系统用户 `guxi`、应用目录 `/srv/guxi`；部署前按实际环境替换域名。

## 1. 安装与目录

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip build-essential nginx certbot python3-certbot-nginx
sudo adduser --system --group --home /srv/guxi --shell /usr/sbin/nologin guxi
sudo install -d -o guxi -g guxi -m 0750 /srv/guxi/shared
sudo install -d -o root -g guxi -m 0750 /etc/guxi
sudo -u guxi git clone https://github.com/admin0330/guxi-stock-analysis.git /srv/guxi/current
sudo -u guxi python3 -m venv /srv/guxi/current/.venv
sudo -u guxi /srv/guxi/current/.venv/bin/pip install -r '/srv/guxi/current/01-本地EXE源码/requirements.txt'
sudo -u guxi cp '/srv/guxi/current/01-本地EXE源码/trading/config/settings.yaml' /srv/guxi/shared/trading-settings.yaml
```

## 2. 服务器环境文件

```bash
sudo install -o root -g guxi -m 0640 /srv/guxi/current/deploy/guxi.env.example /etc/guxi/guxi.env
sudoedit /etc/guxi/guxi.env
```

必须填写 `PUBLIC_BASE_URL` 和 `ALLOWED_HOSTS`。API Key、Secret、Token 均可保持空值；真实凭据只写在 `/etc/guxi/guxi.env`，不能写入仓库。

交互式创建管理员：

```bash
cd '/srv/guxi/current/01-本地EXE源码'
sudo -u guxi env GUXI_DATA_DIR=/srv/guxi/shared AUTH_DB_PATH=/srv/guxi/shared/data/auth.sqlite3 /srv/guxi/current/.venv/bin/python -m backend.auth create-admin admin
```

## 3. systemd

```bash
sudo install -o root -g root -m 0644 /srv/guxi/current/deploy/guxi.service /etc/systemd/system/guxi.service
sudo systemctl daemon-reload
sudo systemctl enable --now guxi.service
sudo systemctl status guxi.service --no-pager
sudo sh /srv/guxi/current/deploy/verify-deployment.sh
```

应用只监听 `127.0.0.1:8000`，不应直接开放该端口到公网。
只要 `guxi.service` 保持运行，应用就会持续预热公共行情缓存；访问页面时优先使用已准备的数据。

## 4. Nginx 与 HTTPS

先确认域名已解析到服务器，并通过 Nginx 默认站点获取证书；再复制模板，将所有 `example.com` 替换为实际域名：

```bash
sudo certbot certonly --webroot -w /var/www/html -d example.com
sudo cp /srv/guxi/current/deploy/nginx.conf.example /etc/nginx/sites-available/guxi
sudoedit /etc/nginx/sites-available/guxi
sudo ln -sfn /etc/nginx/sites-available/guxi /etc/nginx/sites-enabled/guxi
sudo nginx -t
sudo systemctl reload nginx
```

确认公网只开放 80/443，WebSocket `/ws/crypto` 与 `/api/trading/ws` 可正常升级，Cookie 包含 Secure、HttpOnly、SameSite=Lax。

## 5. 更新

更新前备份 `/srv/guxi/shared` 与 `/etc/guxi/guxi.env`，然后执行：

```bash
cd /srv/guxi/current
sudo -u guxi git pull --ff-only
sudo -u guxi .venv/bin/pip install -r '01-本地EXE源码/requirements.txt'
sudo systemctl restart guxi.service
sudo sh deploy/verify-deployment.sh
```

## 6. 上线检查

- [ ] 仓库与服务器环境文件中没有真实凭据
- [ ] `/etc/guxi/guxi.env` 权限为 `0640 root:guxi`
- [ ] `PUBLIC_BASE_URL`、`ALLOWED_HOSTS` 和证书域名一致
- [ ] 应用只监听 `127.0.0.1:8000`
- [ ] 未登录业务 API 返回 401，管理员和普通用户权限正确
- [ ] Mainnet 与自动交易保持关闭，除非已完成独立风险验收
- [ ] 数据目录和环境文件已有加密备份及恢复验证
