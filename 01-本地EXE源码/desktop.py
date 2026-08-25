# -*- coding: utf-8 -*-
"""股析桌面启动器：FastAPI、浏览器与 Windows 托盘。"""
from __future__ import annotations

import ctypes
import logging
import os
import socket
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn
from PIL import Image, ImageDraw

from backend import config
from backend.main import app

logger = logging.getLogger(__name__)


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((config.HOST, port))
            return True
        except OSError:
            return False


def find_available_port(preferred: int, attempts: int = 20) -> int:
    """从首选端口开始寻找可用端口。"""
    for port in range(preferred, preferred + attempts):
        if _port_available(port):
            return port
    raise RuntimeError(f"端口 {preferred}–{preferred + attempts - 1} 均被占用")


def wait_ready(port: int, timeout: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://{config.HOST}:{port}/api/health", timeout=1) as response:  # noqa: S310
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def _show_error(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(0, message, "股析启动失败", 0x10)
    except Exception:
        print(message)


def _tray_image() -> Image.Image:
    path = config.FRONTEND_DIR / "assets" / "brand" / "minimal-cat.png"
    if path.exists():
        return Image.open(path).convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
    image = Image.new("RGBA", (64, 64), "#1c1917")
    draw = ImageDraw.Draw(image)
    draw.ellipse((12, 14, 52, 54), fill="#f9f6f1")
    return image


class DesktopApp:
    def __init__(self, port: int):
        self.port = port
        self.url = f"http://{config.HOST}:{port}"
        # windowed EXE 没有 stdout/stderr；禁用 Uvicorn 默认控制台日志配置。
        uvicorn_config = uvicorn.Config(
            app, host=config.HOST, port=port, log_level="info", access_log=False, log_config=None,
        )
        self.server = uvicorn.Server(uvicorn_config)
        self.thread = threading.Thread(target=self.server.run, name="guxi-server", daemon=True)
        self.exit_event = threading.Event()
        self.shutdown_complete = threading.Event()
        self._stop_lock = threading.Lock()
        self._force_timer: threading.Timer | None = None
        self.icon = None

    def start(self, open_browser: bool = True) -> None:
        self.thread.start()
        if not wait_ready(self.port):
            self.stop()
            raise RuntimeError("本地服务启动超时，请查看日志后重试")
        if open_browser:
            webbrowser.open(self.url)

    def open(self, *_args) -> None:
        webbrowser.open(self.url)

    def stop(self, timeout: float = 3.0) -> bool:
        """幂等停止服务和托盘；返回是否已完成优雅退出。"""
        with self._stop_lock:
            self.exit_event.set()
            self.server.should_exit = True
            if self.icon is not None:
                try:
                    self.icon.stop()
                except Exception:
                    pass
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=timeout)
            if self.thread.is_alive():
                self.server.force_exit = True
                self.thread.join(timeout=0.5)
        stopped = not self.thread.is_alive()
        if stopped:
            self.shutdown_complete.set()
            if self._force_timer is not None:
                self._force_timer.cancel()
        return stopped

    def request_exit(self, icon=None, _item=None) -> None:
        """托盘退出顺序：通知 Uvicorn → 移除图标 → 主线程收尾。"""
        if self.exit_event.is_set():
            return
        self.exit_event.set()
        self.server.should_exit = True
        try:
            (icon or self.icon).stop()
        except Exception:
            pass
        self._force_timer = threading.Timer(3.0, self._force_exit_if_needed)
        self._force_timer.daemon = True
        self._force_timer.start()

    def _force_exit_if_needed(self) -> None:
        if not self.shutdown_complete.is_set():
            logger.error("优雅退出超时，强制结束进程")
            os._exit(0)

    def run_tray(self) -> None:
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("打开分析界面", self.open, default=True),
            pystray.MenuItem("退出", self.request_exit),
        )
        self.icon = pystray.Icon("guxi", _tray_image(), "股析 · 本地市场分析", menu)
        try:
            self.icon.run()
        finally:
            self.stop()


def main(enable_tray: bool = True, open_browser: bool | None = None) -> int:
    if open_browser is None:
        open_browser = config.AUTO_OPEN_BROWSER
    try:
        # 重复启动时优先复用默认端口上的已有股析实例。
        if not _port_available(config.PORT) and wait_ready(config.PORT, timeout=1.5):
            if open_browser:
                webbrowser.open(f"http://{config.HOST}:{config.PORT}")
            return 0
        desktop = DesktopApp(find_available_port(config.PORT))
        logger.info("本地服务地址: %s", desktop.url)
        desktop.start(open_browser=open_browser)
        if enable_tray:
            desktop.run_tray()
        else:
            try:
                while desktop.thread.is_alive() and not desktop.exit_event.is_set():
                    desktop.thread.join(timeout=1)
            except KeyboardInterrupt:
                desktop.stop()
        return 0
    except Exception as exc:
        logger.exception("桌面程序启动失败")
        _show_error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
