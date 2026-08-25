"""桌面启动器端口逻辑测试。"""
from __future__ import annotations

import socket
import threading
import unittest
from unittest.mock import Mock, patch

from backend import config
from desktop import DesktopApp, find_available_port


class DesktopLauncherTests(unittest.TestCase):
    def test_occupied_port_falls_forward(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind((config.HOST, 0))
            port = occupied.getsockname()[1]
            self.assertNotEqual(find_available_port(port, attempts=2), port)

    def test_tray_exit_is_idempotent_and_stops_server_first(self):
        desktop = DesktopApp.__new__(DesktopApp)
        desktop.server = Mock(should_exit=False, force_exit=False)
        desktop.thread = Mock()
        desktop.thread.is_alive.side_effect = [True, False, False]
        desktop.exit_event = threading.Event()
        desktop.shutdown_complete = threading.Event()
        desktop._stop_lock = threading.Lock()
        desktop._force_timer = None
        desktop.icon = Mock()

        with patch("desktop.threading.Timer") as timer:
            desktop.request_exit(desktop.icon)
            desktop.request_exit(desktop.icon)
            self.assertTrue(desktop.server.should_exit)
            desktop.icon.stop.assert_called_once()
            timer.assert_called_once()
            self.assertTrue(desktop.stop())
            self.assertTrue(desktop.shutdown_complete.is_set())

    def test_real_server_exit_releases_port(self):
        port = find_available_port(18765)
        desktop = DesktopApp(port)
        desktop.start(open_browser=False)
        icon = Mock()
        with patch("desktop.threading.Timer"):
            desktop.request_exit(icon)
            self.assertTrue(desktop.stop())
        self.assertEqual(find_available_port(port, attempts=1), port)

    def test_start_opens_default_browser_after_service_is_ready(self):
        desktop = DesktopApp.__new__(DesktopApp)
        desktop.port = 18765
        desktop.url = "http://127.0.0.1:18765"
        desktop.thread = Mock()
        with patch("desktop.wait_ready", return_value=True), patch("desktop.webbrowser.open") as open_browser:
            desktop.start(open_browser=True)
        desktop.thread.start.assert_called_once()
        open_browser.assert_called_once_with(desktop.url)


if __name__ == "__main__":
    unittest.main()
