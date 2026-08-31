const { chromium } = require("playwright");

(async () => {
  const baseUrl = process.env.GUXI_URL || "http://127.0.0.1:8765";
  const browser = await chromium.launch({ headless: true, executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "no-preference" });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });

  await page.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 15000 });
  await page.waitForSelector("#view-overview.active");
  await page.waitForTimeout(500);
  if (await page.locator("#welcomeDialog").evaluate((el) => el.open)) await page.click("#welcomeDone");

  for (const name of ["stock", "limitup", "daily"]) {
    await page.click(`.tab[data-tab="${name}"]`);
    await page.waitForSelector(`#view-${name}.active`);
  }
  await page.click("#marketFab");
  await page.waitForSelector("#view-crypto.active");
  await page.waitForFunction(() => !document.querySelector("#cryptoStreamStatus")?.classList.contains("connecting"), null, { timeout: 12000 });
  const streamState = await page.locator("#cryptoStreamStatus").getAttribute("class");
  if (!/(connected|fallback)/.test(streamState)) throw new Error("实时行情既未连接也未进入降级模式：" + streamState);
  const streamHealth = await page.evaluate(() => fetch("/api/crypto/stream/status").then((r) => r.json()));
  if (streamHealth.subscriber_count !== 1) throw new Error("币圈页面存在重复订阅：" + streamHealth.subscriber_count);
  if ((await page.textContent("#marketFabLabel")) !== "A股") throw new Error("币圈返回按钮状态不正确");
  await page.click('#cryptoFrequency [data-seconds="300"]');
  if (!(await page.locator('#cryptoFrequency [data-seconds="300"]').getAttribute("class")).includes("active")) throw new Error("刷新频率未切换");
  await page.waitForTimeout(800);
  await page.screenshot({ path: "build/browser-crypto.png", fullPage: true });
  await page.click("#tradingDeskTab");
  await page.waitForSelector("#tradingPanel.active");
  await page.waitForFunction(() => document.querySelector("#tradeEnvironment")?.textContent.includes("Testnet"));
  await page.waitForFunction(() => document.querySelector("#tradeBtcPrice")?.textContent !== "--");
  if (await page.locator("#tradeSetup").isHidden()) throw new Error("无密钥时未显示本地配置说明");
  const tradingBootstrap = await page.evaluate(() => fetch("/api/trading/bootstrap").then((r) => r.json()));
  if (!tradingBootstrap.read_only || tradingBootstrap.write_token || JSON.stringify(tradingBootstrap).match(/api_secret|api_key/i)) throw new Error("Binance 只读查询边界不正确");
  for (const selector of ["#tradeOrderForm", "#tradeAutoBtn", "#tradeSettingsForm", "#tradeUnlockBtn", "#tradeEmergencyBtn", "#tradeEmergencyCloseBtn", "#tradeSafetyDock"]) {
    if (await page.locator(selector).count()) throw new Error("页面仍存在交易写入口：" + selector);
  }
  await page.waitForTimeout(800);
  await page.screenshot({ path: "build/browser-trading.png", fullPage: true });
  await page.click("#cryptoDashboardTab");
  await page.click("#marketFab");
  await page.waitForSelector("#view-daily.active");
  await page.waitForFunction(() => fetch("/api/crypto/stream/status").then((r) => r.json()).then((d) => d.subscriber_count === 0));

  await page.click("#aboutBtn");
  await page.waitForSelector("#aboutDialog[open]");
  if (!(await page.textContent("#aboutDialog")).includes("v1.0.0")) throw new Error("关于页缺少版本号");
  await page.keyboard.press("Escape");
  await page.keyboard.press("/");
  if ((await page.evaluate(() => document.activeElement?.id)) !== "globalSearch") throw new Error("搜索快捷键失效");

  const duplicates = await page.evaluate(() => {
    const ids = [...document.querySelectorAll("[id]")].map((el) => el.id);
    return ids.filter((id, i) => ids.indexOf(id) !== i);
  });
  if (duplicates.length) throw new Error("重复 ID: " + duplicates.join(","));
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  if (overflow) throw new Error("桌面布局存在横向溢出");

  await page.screenshot({ path: "build/browser-smoke.png", fullPage: true });

  const reduced = await browser.newPage({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce" });
  await reduced.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 15000 });
  await reduced.waitForTimeout(500);
  if (await reduced.locator("#welcomeDialog").evaluate((el) => el.open)) await reduced.click("#welcomeDone");
  if (!(await reduced.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches))) throw new Error("减少动态效果媒体查询未生效");
  await reduced.click('.tab[data-tab="stock"]');
  await reduced.waitForSelector("#view-stock.active");
  const reducedTransition = await reduced.locator("#view-stock").evaluate((el) => getComputedStyle(el).transitionDuration);
  if (parseFloat(reducedTransition) > 0.001) throw new Error("减少动态效果下仍有长过渡：" + reducedTransition);
  if (await reduced.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1)) throw new Error("移动端布局存在横向溢出");
  await reduced.close();

  const faulty = await browser.newPage({ viewport: { width: 1200, height: 800 } });
  await faulty.route("**/api/market/indices", (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "模拟数据源暂不可用" }) }));
  await faulty.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 15000 });
  await faulty.waitForSelector(".indices-card .module-retry");
  await faulty.waitForFunction(() => !document.querySelector("#breadthTotal")?.textContent.includes("--"));
  if (!(await faulty.textContent(".indices-card")).includes("模拟数据源暂不可用")) throw new Error("模块错误未显示中文原因");
  await faulty.close();
  await browser.close();
  if (errors.length) throw new Error("页面错误: " + [...new Set(errors)].join(" | "));
  console.log("browser smoke passed");
})().catch((error) => { console.error(error); process.exit(1); });
