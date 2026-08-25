# -*- coding: utf-8 -*-
"""股析开发模式启动脚本。

用法:
    python run.py            # 启动后自动打开浏览器
    python run.py --no-browser   # 不打开浏览器
"""
from __future__ import annotations

import sys
from desktop import main as desktop_main


def main() -> int:
    return desktop_main(enable_tray=False, open_browser=False if "--no-browser" in sys.argv else None)


if __name__ == "__main__":
    sys.exit(main())
