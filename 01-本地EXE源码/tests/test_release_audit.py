from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT.parent / "04-构建与成品" / "dist"


class ReleaseAuditTests(unittest.TestCase):
    def test_portable_package_layout_is_complete_and_clean(self):
        if not PACKAGE.exists():
            self.skipTest("便携版尚未构建；运行 build.bat 后再执行发布包审计")
        required = [
            "A股分析系统.exe", "config.yaml", "README.md", "THIRD_PARTY_NOTICES.md",
            "frontend/index.html", "frontend/css/claude.css", "frontend/js/app.js",
            "frontend/login.html", "frontend/admin.html", "frontend/css/auth.css", "frontend/js/login.js", "frontend/js/admin.js",
            "frontend/css/global-motion.css", "frontend/js/motion.js",
            "frontend/js/vendor/echarts.min.js", "cache", "logs", "data/reports",
            ".env.example", "trading/config/settings.yaml", "trading/config/.env.example",
            "DEPLOY.md", "deploy/ym3861-backup.sh", "deploy/ym3861-backup.service", "deploy/ym3861-backup.timer",
        ]
        self.assertTrue(all((PACKAGE / item).exists() for item in required))
        # 便携目录也是运行目录，可保留用户自己的 .env、状态、缓存和日志；构建脚本不会分发这些内容。

    def test_packaged_frontend_matches_source(self):
        for relative in ("index.html", "login.html", "admin.html", "css/claude.css", "css/auth.css", "css/global-motion.css", "js/app.js", "js/login.js", "js/admin.js", "js/motion.js", "js/vendor/echarts.min.js"):
            self.assertEqual((ROOT / "frontend" / relative).read_bytes(), (PACKAGE / "frontend" / relative).read_bytes())

    def test_frontend_contract(self):
        html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        css = (ROOT / "frontend/css/claude.css").read_text(encoding="utf-8")
        js = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'class="tab(?: active)?" data-tab="([^"]+)"', html), ["overview", "picks", "limitup", "stock", "daily"])
        self.assertIn('id="globalSearchToggle"', html)
        self.assertNotIn('id="refreshBtn"', html)
        self.assertNotIn('refreshBtn', js)
        self.assertIn('class="bitcoin-icon"', html)
        self.assertIn('class="hero-next"', html)
        self.assertIn('["overview", "picks", "limitup", "stock", "daily"]', js)
        self.assertIn('id="view-picks"', html)
        self.assertIn("仅供学习研究，不构成投资建议", html)
        self.assertIn("loadDailyPicks", js)
        self.assertIn('id="logoutBtn"', html)
        self.assertIn("data-pick-detail", js)
        self.assertIn('id="marketFab"', html)
        self.assertIn('id="cryptoStreamStatus"', html)
        self.assertIn('id="tradingDeskTab"', html)
        self.assertIn("Binance 查询", html)
        for removed in ('tradeOrderForm', 'tradeAutoBtn', 'tradeSettingsForm', 'tradeUnlockBtn', 'tradeEmergencyBtn', 'tradeEmergencyCloseBtn', 'tradeSafetyDock'):
            self.assertNotIn(removed, html)
        self.assertIn('id="tradeBtcPrice"', html)
        self.assertIn('id="tradeEthPrice"', html)
        self.assertIn('new WebSocket(`${protocol}//${location.host}/ws/crypto`)', js)
        self.assertIn('document.addEventListener("visibilitychange"', js)
        self.assertIn("loadCryptoAnalysis()", js)
        self.assertIn("prefers-reduced-motion: reduce", css)
        self.assertIn("prefers-color-scheme: dark", css)
        self.assertIn("--duration-page: 240ms", css)
        self.assertNotIn(".view.active.view-enter", css)
        self.assertNotIn('nextView.classList.add("active", "view-enter")', js)
        self.assertIn("function chartTheme()", js)
        self.assertGreaterEqual(js.count("Promise.allSettled"), 4)
        motion_css = (ROOT / "frontend/css/global-motion.css").read_text(encoding="utf-8")
        motion_js = (ROOT / "frontend/js/motion.js").read_text(encoding="utf-8")
        for token in ("--ease-spring-soft", "--ease-spring-snappy", "--ease-out-smooth", "--ease-in-smooth"):
            self.assertIn(token, motion_css)
        for delay in (12, 28, 44, 60, 76, 92, 108):
            self.assertIn(f"animation-delay: {delay}ms", motion_css)
        self.assertIn("prefers-reduced-motion: reduce", motion_css)
        self.assertIn("prefers-color-scheme: dark", motion_css)
        self.assertIn("scale(0.97) translateY(1px)", motion_css)
        self.assertIn("backdrop-filter: saturate(150%) blur(18px)", motion_css)
        self.assertIn("--motion-glass-popover", motion_css)
        self.assertIn("::view-transition-old(root)", motion_css)
        self.assertIn(".ws-flash", motion_css)
        self.assertIn(".ripple-effect", motion_css)
        self.assertIn('document.addEventListener("pointerdown"', motion_js)
        self.assertIn('ripple.addEventListener("animationend"', motion_js)
        self.assertIn("window.smoothRender", motion_js)
        self.assertIn("window.transitionViews", motion_js)
        self.assertIn("window.motionNavigate", motion_js)
        self.assertIn("window.smoothInsertWSItem", motion_js)
        self.assertIn("document.startViewTransition", motion_js)
        self.assertIn("window.highlightNode", motion_js)
        self.assertIn("[data-auto-animate]", motion_js)
        self.assertIn("renderHtml(", js)
        self.assertIn("window.smoothRender", js)
        self.assertIn("window.highlightNode?.", js)
        self.assertNotIn("window.renderWithTransition(element", js)
        for tag in re.findall(r"<[^>]+data-auto-animate[^>]*>", html):
            self.assertRegex(tag, r'class="[^"]*\bstagger-in\b')
        for tag in re.findall(r'<section[^>]+class="[^"]*\bview\b[^"]*"[^>]*>', html):
            self.assertIn("stagger-in", tag)
        authored_css = "\n".join((ROOT / f"frontend/css/{name}").read_text(encoding="utf-8") for name in ("claude.css", "auth.css", "global-motion.css"))
        self.assertNotRegex(authored_css, r"(?<![-\w])(?:ease|ease-in-out|linear)(?![-\w])")
        for page in ("index.html", "login.html", "admin.html"):
            page_html = (ROOT / "frontend" / page).read_text(encoding="utf-8")
            self.assertIn('<meta name="view-transition" content="same-origin">', page_html)
            self.assertIn("/static/css/global-motion.css", page_html)
            self.assertIn("/static/js/motion.js", page_html)
            self.assertNotIn("unpkg.com", page_html)
            self.assertNotIn("/static/css/buttons.css", page_html)
            self.assertNotIn("/static/js/ripple.js", page_html)
        backend = (ROOT / "backend/main.py").read_text(encoding="utf-8")
        self.assertIn("script-src 'self';", backend)
        self.assertNotIn("unpkg.com", backend)
        for seconds in (1, 60, 300, 1200):
            self.assertIn(f'data-seconds="{seconds}"', html)

    def test_no_old_visual_or_hardcoded_user_path(self):
        content = "\n".join((ROOT / path).read_text(encoding="utf-8", errors="ignore") for path in (
            "frontend/index.html", "frontend/css/claude.css", "frontend/js/app.js",
            "backend/config.py", "desktop.py", "run.py",
        )).lower()
        for banned in ("ios26", "liquid glass", "毛玻璃", "uiverse", "orbs", "blurtext", "shinytext", "spotlightcard", "gsap", "anime.js"):
            self.assertNotIn(banned, content)
        self.assertNotIn("c:\\users\\ym1r", content)
        self.assertNotIn("a股分析系统-源码", content)

    def test_mobile_responsive_contract(self):
        html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        css = (ROOT / "frontend/css/claude.css").read_text(encoding="utf-8")
        for page in ("index.html", "login.html", "admin.html"):
            page_html = (ROOT / "frontend" / page).read_text(encoding="utf-8")
            self.assertIn('width=device-width, initial-scale=1.0, viewport-fit=cover', page_html)
        self.assertIn('class="header-actions"', html)
        self.assertIn('class="temp-reading"', html)
        self.assertIn(".search-menu.open .global-search", css)
        self.assertIn(".section-intro h1 .hero-next { display: block; }", css)
        self.assertIn("min-width: 0", css)
        self.assertIn("overflow-x: auto", css)
        self.assertIn("white-space: nowrap", css)
        self.assertIn("-webkit-overflow-scrolling: touch", css)
        self.assertIn("right: calc(16px + env(safe-area-inset-right))", css)
        self.assertIn("bottom: calc(84px + env(safe-area-inset-bottom))", css)
        self.assertIn("calc(96px + env(safe-area-inset-bottom))", css)
        self.assertNotRegex(css, r"\.temp-(?:value|tone)\s*\{[^}]*position:\s*absolute")


if __name__ == "__main__":
    unittest.main()
