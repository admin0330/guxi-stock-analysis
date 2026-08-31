/* ══════════════════════════════════════════════════════════════
   股析 · A股分析系统 — 前端逻辑
   数据来自后端 /api/* 接口
   ══════════════════════════════════════════════════════════════ */
"use strict";

const API = ""; // 同源部署

/* ---------- 工具 ---------- */
const $ = (id) => document.getElementById(id);
const fmt = {
  pct: (v) => (v === null || v === undefined ? "--" : (v > 0 ? "+" : "") + v.toFixed(2) + "%"),
  num: (v, d = 2) => (v === null || v === undefined ? "--" : v.toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d })),
  yi: (v) => (v === null || v === undefined ? "--" : v.toFixed(2) + " 亿"),
};
const pctClass = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
const escapeHtml = (value) => String(value ?? "--").replace(/[&<>"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
}[char]));
function renderHtml(target, html, afterUpdate) {
  const element = typeof target === "string" ? $(target) : target;
  if (!element) return null;
  const update = () => {
    element.innerHTML = html;
    afterUpdate?.(element);
  };
  return typeof window.smoothRender === "function"
    ? window.smoothRender(element, update)
    : update();
}
const toast = (msg, isError = false) => {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (isError ? " error" : "");
  setTimeout(() => (t.className = "toast"), 2600);
};

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  const slow = setTimeout(() => {
    if ($("loadingDock").classList.contains("show")) $("loadingLabel").textContent = "数据源响应较慢，正在尝试缓存…";
  }, 2500);
  try {
    const method = (options.method || "GET").toUpperCase();
    const headers = options.body ? { "Content-Type": "application/json", ...(options.headers || {}) } : { ...(options.headers || {}) };
    if (!["GET", "HEAD", "OPTIONS"].includes(method) && state.csrfToken) headers["X-CSRF-Token"] = state.csrfToken;
    const res = await fetch(API + path, { ...options, method, headers, credentials: "same-origin", signal: controller.signal });
    if (res.status === 401) {
      const target = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
      if (typeof window.motionNavigate === "function") window.motionNavigate(target, { replace: true });
      else location.replace(target);
      throw new Error("登录已失效，请重新登录");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try { const j = await res.json(); detail = j.detail || detail; } catch (e) { /* ignore */ }
      throw new Error(detail);
    }
    return res.json();
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超过 8 秒，已停止等待；请稍后刷新");
    throw error;
  } finally {
    clearTimeout(timeout);
    clearTimeout(slow);
  }
}

// 数据分段到达时重播一次轻量反馈，避免整页重排或引入额外动画库。
function pulseData(targets) {
  const list = Array.isArray(targets) ? targets : [targets];
  list.forEach((target, index) => {
    const el = typeof target === "string" ? $(target) : target;
    if (!el) return;
    clearTimeout(el.__dataPulseTimer);
    el.classList.remove("data-arrived");
    el.style.setProperty("--data-delay", `${index * 45}ms`);
    void el.offsetWidth;
    el.classList.add("data-arrived");
    el.__dataPulseTimer = setTimeout(() => {
      el.classList.remove("data-arrived");
      el.style.removeProperty("--data-delay");
    }, 900 + index * 45);
  });
}

/* ---------- 全局状态 ---------- */
const state = {
  indexChart: null,
  stockChart: null,
  cryptoChart: null,
  ladderChart: null,
  indChart: null,
  indexRequestId: 0,
  currentIndex: "sh000001",
  stockRequestId: 0,
  cryptoAsset: "BTC",
  cryptoInterval: "1h",
  cryptoLoaded: false,
  cryptoAuto: true,
  cryptoTimer: null,
  cryptoRefreshSeconds: 60,
  cryptoNextAt: 0,
  cryptoCountdownTimer: null,
  cryptoSocket: null,
  cryptoSocketTimer: null,
  cryptoSocketAttempts: 0,
  cryptoTickers: {},
  cryptoOverview: {},
  cryptoPriceTimer: null,
  cryptoPanel: "dashboard",
  tradingLoaded: false,
  tradingSettings: {},
  tradingStatus: {},
  tradingSocket: null,
  tradingSocketTimer: null,
  tradingSocketAttempts: 0,
  tradingHistory: "orders",
  activeView: "overview",
  lastAshareView: "overview",
  loaded: { limitup: false, picks: false, daily: false, crypto: false },
  overview: {},
  currentStock: null,
  watchlist: [],
  authUser: null,
  csrfToken: "",
  lastIndexDate: null,
  limitDate: "",
};
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

const saveState = (patch) => api("/api/user/state", { method: "PATCH", body: JSON.stringify(patch) }).catch(() => {});

function markFresh(target, data = {}) {
  const el = typeof target === "string" ? $(target) : target;
  const head = el?.querySelector?.(".card-head");
  if (!head) return;
  let tag = head.querySelector(".freshness");
  if (!tag) { tag = document.createElement("span"); tag.className = "freshness muted"; head.appendChild(tag); }
  tag.textContent = `${data.stale ? "缓存数据（可能不是最新）" : "更新于"} ${new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
}

function moduleError(target, message, retry) {
  const el = typeof target === "string" ? $(target) : target;
  if (!el) return;
  let box = el.querySelector(".module-error-note");
  if (!box) { box = document.createElement("div"); box.className = "module-error-note"; el.appendChild(box); }
  renderHtml(box, `<span>${escapeHtml(message)}</span><button type="button" class="module-retry">重试</button>`, (updatedBox) => {
    updatedBox.querySelector("button").addEventListener("click", retry, { once: true });
  });
}

/* ---------- 轻量加载反馈 ---------- */
const loadingUI = { ticket: 0, total: 0, done: 0, hideTimer: null };

function beginLoading(total, label = "正在同步市场数据") {
  const ticket = ++loadingUI.ticket;
  loadingUI.total = Math.max(1, total);
  loadingUI.done = 0;
  clearTimeout(loadingUI.hideTimer);
  $("loadingDock").classList.add("show");
  $("loadingLabel").textContent = label;
  $("loadingCount").textContent = "0%";
  return ticket;
}

function completeLoading(ticket, label = "正在同步市场数据") {
  if (ticket !== loadingUI.ticket) return;
  loadingUI.done = Math.min(loadingUI.total, loadingUI.done + 1);
  const pct = Math.round((loadingUI.done / loadingUI.total) * 100);
  $("loadingLabel").textContent = label;
  $("loadingCount").textContent = pct + "%";
  if (loadingUI.done >= loadingUI.total) {
    loadingUI.hideTimer = setTimeout(() => {
      $("loadingDock").classList.remove("show");
    }, 420);
  }
}

/* ---------- 时钟 ---------- */
function tickClock() {
  const now = new Date();
  $("clock").querySelector(".clock-time").textContent =
    now.toTimeString().slice(0, 8);
  $("clockDate").textContent = now.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", weekday: "long" });
  $("footerTime").textContent = "更新于 " + now.toLocaleString("zh-CN");
  updateMarketSession(now);
}
function updateMarketSession(now = new Date()) {
  const el = $("marketSession");
  const mins = now.getHours() * 60 + now.getMinutes();
  const weekday = now.getDay();
  const today = now.toISOString().slice(0, 10);
  let text = "已收盘 · 当前为收盘数据", cls = "closed";
  if (weekday === 0 || weekday === 6) text = "周末休市 · 当前为最近收盘数据";
  else if (mins < 570) text = "未开盘 · 当前为最近收盘数据";
  else if (mins >= 690 && mins < 780) text = "午间休市 · 当前为上午数据";
  else if ((mins >= 570 && mins < 690) || (mins >= 780 && mins < 900)) {
    if (state.lastIndexDate && state.lastIndexDate !== today) text = "休市日 · 当前为最近收盘数据";
    else { text = "交易中 · 行情持续更新"; cls = "trading"; }
  }
  el.className = `market-status ${cls}`;
  el.querySelector("span").textContent = text;
}
setInterval(tickClock, 1000);
tickClock();

/* ---------- Tab 切换 ---------- */
const tabs = document.querySelectorAll(".tab");
const tabsRoot = $("tabs");
const underline = document.querySelector(".tab-underline");
const marketFab = $("marketFab");
let navTicket = 0;
function syncTabIndicator() {
  const active = document.querySelector(".tab.active");
  if (!active || !underline) return;
  underline.style.width = active.offsetWidth + "px";
  underline.style.transform = `translate3d(${active.offsetLeft}px, 0, 0)`;
}
function switchTab(name) {
  const current = state.activeView;
  if (current === name) return;
  const ticket = ++navTicket;
  const currentView = current ? $("view-" + current) : null;
  const nextView = $("view-" + name);
  if (!nextView) return;
  if (name !== "crypto") state.lastAshareView = name;
  if (current === "crypto") {
    clearTimeout(state.cryptoTimer);
    clearInterval(state.cryptoCountdownTimer);
    disconnectCryptoStream("实时行情已暂停");
    disconnectTradingStream();
  }
  tabs.forEach((t) => t.classList.toggle("active", name !== "crypto" && t.dataset.tab === name));
  tabsRoot.classList.toggle("crypto-mode", name === "crypto");
  document.body.classList.toggle("crypto-mode", name === "crypto");
  $("marketFabLabel").textContent = name === "crypto" ? "A股" : "币圈";
  marketFab.setAttribute("aria-label", name === "crypto" ? "返回A股页面" : "切换到币圈看板");
  syncTabIndicator();

  const activate = () => {
    if (ticket !== navTicket) return;
    document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
    nextView.classList.add("active");
    state.activeView = name;
    saveState({ last_page: name });
    requestAnimationFrame(() => {
      if (ticket !== navTicket) return;
      [state.indexChart, state.stockChart, state.cryptoChart, state.ladderChart, state.indChart].forEach((chart) => chart?.resize());
    });
    if (name === "limitup" && !state.loaded.limitup) loadLimitup();
    if (name === "picks" && !state.loaded.picks) loadDailyPicks();
    if (name === "daily" && !state.loaded.daily) loadDaily();
    if (name === "crypto") {
      if (state.cryptoPanel === "trading") openTradingDesk();
      else {
        connectCryptoStream();
        state.loaded.crypto ? scheduleCryptoRefresh() : loadCrypto();
      }
    }
  };

  if (typeof window.transitionViews === "function") window.transitionViews(currentView, nextView, activate, { native: false });
  else activate();
}
tabs.forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));
marketFab.addEventListener("click", () => switchTab(state.activeView === "crypto" ? state.lastAshareView : "crypto"));
window.addEventListener("resize", () => {
  [state.indexChart, state.stockChart, state.cryptoChart, state.ladderChart, state.indChart].forEach((c) => c && c.resize());
  syncTabIndicator();
});

/* ---------- ECharts 主题 ---------- */
const cssColor = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
function chartTheme() {
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  return {
    ink: cssColor("--text-primary", "#1c1917"),
    dim: cssColor("--text-tertiary", "#78716c"),
    border: cssColor("--border-strong", "#d4cdc2"),
    grid: cssColor("--divider", "rgba(87,83,78,0.12)"),
    accent: cssColor("--accent", "#d97757"),
    surface: cssColor("--bg-card", "#ffffff"),
    zoom: cssColor("--bg-muted", "rgba(243,239,232,0.92)"),
    zoomFill: cssColor("--accent-soft", "rgba(217,119,87,0.16)"),
    up: cssColor("--up", "#c4554a"),
    down: cssColor("--down", "#3d8b6e"),
    ma: [cssColor("--accent", "#d97757"), dark ? "#d7ad72" : "#9a7244", cssColor("--flat", "#78716c")],
  };
}
function baseOption() {
  const theme = chartTheme();
  return {
    backgroundColor: "transparent",
    animation: !reduceMotion.matches,
    textStyle: { color: theme.dim, fontFamily: "Segoe UI, PingFang SC, Microsoft YaHei, sans-serif" },
    grid: { left: 12, right: 16, top: 30, bottom: 8, containLabel: true },
  };
}

/* ═══════════ 01 市场总览 ═══════════ */
async function loadOverview() {
  const ticket = beginLoading(6, "正在接收市场信号");
  state.overview = {};

  const run = async (promise, onData, label, onError) => {
    try {
      const data = await promise;
      if (!data.error) onData(data);
    } catch (e) {
      console.warn("渐进加载失败:", label, e);
      onError?.(e);
    } finally {
      completeLoading(ticket, label);
    }
  };

  const indicesTask = run(
    api("/api/market/indices"),
    (d) => {
      state.overview.indices = d.indices || [];
      renderIndices(state.overview.indices);
      markFresh(document.querySelector(".indices-card"), d);
      refreshOverviewSummary();
    },
    "指数已到达",
    (e) => { renderHtml("indicesGrid", `<p class="module-inline-error">主要指数暂不可用：${escapeHtml(e.message)}</p>`); moduleError(document.querySelector(".indices-card"), e.message, loadOverview); }
  );
  const breadthTask = run(
    api("/api/market/breadth"),
    (d) => {
      state.overview.breadth = d;
      renderBreadth(d);
      markFresh("breadthCard", d);
      refreshOverviewSummary();
    },
    "涨跌分布已到达",
    (e) => { $("breadthTotal").textContent = "涨跌分布暂不可用：" + e.message; moduleError("breadthCard", e.message, loadOverview); }
  );
  const volumeTask = run(
    api("/api/market/volume"),
    (d) => {
      state.overview.volume = d;
      renderVolume(d);
      markFresh("breadthCard", d);
      refreshOverviewSummary();
    },
    "成交额已到达",
    (e) => { $("volTag").textContent = "成交额暂不可用：" + e.message; moduleError("breadthCard", e.message, loadOverview); }
  );
  const hsgtTask = run(
    api("/api/market/hsgt"),
    (d) => { renderHsgt(d); markFresh("summaryCard", d); },
    "北向资金已到达",
    (e) => { $("hsgtNote").hidden = false; $("hsgtNote").textContent = "北向资金暂不可用：" + e.message; moduleError("summaryCard", e.message, loadOverview); }
  );
  const initialChartSymbol = state.currentIndex;
  const chartTask = run(
    api("/api/market/index/" + initialChartSymbol),
    (d) => { if (state.currentIndex === initialChartSymbol) renderIndexData(d); },
    "指数走势已到达",
    (e) => { $("indexChart").dataset.error = `指数走势暂不可用：${e.message}`; moduleError(document.querySelector(".chart-card"), e.message, () => loadIndexChart(state.currentIndex)); }
  );
  const temperatureTask = run(
    api("/api/market/temperature"),
    (d) => {
      state.overview.temperature = d;
      renderTemperature(d);
      markFresh("tempCard", d);
      refreshOverviewSummary();
    },
    "市场温度已到达",
    (e) => { $("tempTone").textContent = "市场温度暂不可用：" + e.message; moduleError("tempCard", e.message, loadOverview); }
  );

  await Promise.allSettled([indicesTask, breadthTask, volumeTask, hsgtTask, chartTask, temperatureTask]);
}

function renderTemperature(temp = {}) {
  $("tempValue").textContent = temp.temperature ?? "--";
  $("tempBadge").textContent = temp.label || "--";
  $("tempBadge").className = "badge " + (temp.label === "火热" ? "hot" : temp.label === "冰点" ? "cold" : "");
  $("tempTone").textContent = "建议 · " + (temp.tone || "--");
  animateGauge(temp.temperature ?? 0);
  pulseData(["tempCard", "tempValue", "tempBadge"]);
}

function renderBreadth(b = {}) {
  $("breadthTotal").textContent = "共 " + (b.total ?? "--") + " 只";
  $("bUp").textContent = b.up ?? "--";
  $("bDown").textContent = b.down ?? "--";
  $("bFlat").textContent = b.flat ?? "--";
  $("bLimit").textContent = (b.limit_up ?? "--") + " / " + (b.limit_down ?? "--");
  const total = b.total || 1;
  animateBar("bUpFill", (b.up / total) * 100);
  animateBar("bDownFill", (b.down / total) * 100);
  animateBar("bFlatFill", (b.flat / total) * 100);
  animateBar("bLimitFill", Math.min(100, ((b.limit_up || 0) + (b.limit_down || 0)) * 3));
  pulseData(["breadthCard", "bUp", "bDown", "bFlat", "bLimit"]);
}

function renderVolume(v = {}) {
  if (!v.amount_yi) return;
  $("volAmount").textContent = fmt.num(v.amount_yi, 0);
  const tag = $("volTag");
  if (v.partial === "deep") {
    tag.textContent = "深市";
    tag.style.color = "var(--gold-dim)";
  } else {
    tag.textContent = v.amount_yi >= 10000 ? "放量" : "缩量";
    tag.style.color = v.amount_yi >= 10000 ? "var(--up)" : "var(--down)";
  }
  pulseData(["volAmount", "volTag"]);
}

function renderHsgt(hsgt = {}) {
  if (hsgt.note) {
    $("hsgtNote").hidden = false;
    $("hsgtNote").textContent = "🧭 " + hsgt.note;
  } else {
    $("hsgtNote").hidden = true;
  }
  pulseData("summaryCard");
}

function refreshOverviewSummary() {
  const { indices, breadth, volume, temperature } = state.overview;
  const parts = [];
  if (indices?.length) {
    const top = indices[0];
    parts.push("上证指数 " + fmt.num(top.close) + " 点，" + fmt.pct(top.change_pct) + "。");
  }
  if (breadth?.total) {
    parts.push("上涨 " + breadth.up + " 家、下跌 " + breadth.down + " 家。");
  }
  if (temperature?.temperature != null) {
    parts.push("市场温度 " + temperature.temperature + " 分（" + (temperature.label || "计算中") + "）。");
  }
  if (volume?.amount_yi) {
    parts.push(volume.partial === "deep"
      ? "深市成交 " + fmt.num(volume.amount_yi, 0) + " 亿元，两市合计加载中。"
      : "两市成交 " + fmt.num(volume.amount_yi, 0) + " 亿元。");
  }
  $("overviewSummary").textContent = parts.join(" ") || "正在接收市场信号…";
  $("overviewTime").textContent = "实时拼接 · " + new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  pulseData(["overviewSummary", "overviewTime"]);
}

function animateGauge(pct) {
  const arc = $("gaugeArc");
  const len = 251.3;
  const offset = len - (len * pct) / 100;
  arc.style.strokeDashoffset = offset;
  arc.style.stroke = pct >= 70 ? "var(--up)" : pct >= 40 ? "var(--gold)" : "var(--down)";
}

function animateBar(id, width) {
  const el = $(id);
  requestAnimationFrame(() => (el.style.width = Math.max(0, Math.min(100, width)) + "%"));
}

function renderIndices(indices) {
  const grid = $("indicesGrid");
  renderHtml(grid, indices.map((idx, i) => `
    <div class="index-tile" data-sym="${idx.code}" style="animation-delay:${i * 0.04}s">
      <div class="it-name">${idx.name}</div>
      <div class="it-price ${pctClass(idx.change_pct)}">${fmt.num(idx.close)}</div>
      <div class="it-chg ${pctClass(idx.change_pct)}">${fmt.pct(idx.change_pct)}</div>
      <div class="it-date">${idx.date || ""}</div>
    </div>`).join(""), (updatedGrid) => {
    updatedGrid.querySelectorAll(".index-tile").forEach((tile) =>
      tile.addEventListener("click", () => {
        const sym = tile.dataset.sym;
        setIndexChip(sym);
        loadIndexChart(sym);
      })
    );
    updatedGrid.querySelector(`[data-sym="${state.currentIndex}"]`)?.classList.add("active");
    pulseData(updatedGrid);
  });
  state.lastIndexDate = indices.find((item) => item.date)?.date || state.lastIndexDate;
  updateMarketSession();
}

function setIndexChip(sym) {
  state.currentIndex = sym;
  document.querySelectorAll("#indexSwitch .chip").forEach((c) => {
    c.classList.toggle("active", c.dataset.sym === sym);
  });
  document.querySelectorAll(".index-tile").forEach((tile) => {
    tile.classList.toggle("active", tile.dataset.sym === sym);
  });
  const selected = document.querySelectorAll(`[data-sym="${sym}"].active`);
  selected.forEach((el) => {
    el.classList.remove("switch-active");
    void el.offsetWidth;
    el.classList.add("switch-active");
    setTimeout(() => el.classList.remove("switch-active"), 560);
  });
}
document.querySelectorAll("#indexSwitch .chip").forEach((c) =>
  c.addEventListener("click", () => { setIndexChip(c.dataset.sym); loadIndexChart(c.dataset.sym); })
);

function renderIndexData(d) {
  if (d.error) return;
  renderKline("indexChart", d.kline || [], d.name, "index");
  const t = d.technicals || {};
  const tags = [
    ["MA5", t.ma5], ["MA10", t.ma10], ["MA20", t.ma20], ["MA60", t.ma60],
    ["量比5/20", t.vol_ratio_5_20], ["20日涨幅", t.trend_20d ? t.trend_20d.toFixed(2) + "%" : null],
  ].filter((x) => x[1] !== null && x[1] !== undefined);
  renderHtml("indexTechTags", tags.map(([n, v]) => '<span class="tech-tag">' + n + ' <b>' + v + '</b></span>').join(""));
  const card = $("indexChart").closest(".chart-card");
  markFresh(card, d);
  card.classList.remove("chart-switching", "chart-swapped");
  requestAnimationFrame(() => {
    state.indexChart?.resize();
    card.classList.add("chart-swapped");
    setTimeout(() => card.classList.remove("chart-swapped"), 720);
  });
  pulseData("indexTechTags");
}

async function loadIndexChart(sym) {
  const requestId = ++state.indexRequestId;
  const card = $("indexChart").closest(".chart-card");
  card.classList.add("chart-switching");
  card.setAttribute("aria-busy", "true");
  try {
    const d = await api("/api/market/index/" + sym);
    if (requestId !== state.indexRequestId) return;
    if (d.error) { toast(d.error, true); return; }
    renderIndexData(d);
  } catch (e) {
    if (requestId === state.indexRequestId) {
      toast("指数K线加载失败：" + e.message, true);
      moduleError(card, e.message, () => loadIndexChart(sym));
    }
  } finally {
    if (requestId === state.indexRequestId) {
      card.classList.remove("chart-switching");
      card.removeAttribute("aria-busy");
    }
  }
}

function renderKline(elId, k, name, mode) {
  const el = $(elId);
  const chart = state[mode + "Chart"] || (state[mode + "Chart"] = echarts.init(el));
  const theme = chartTheme();
  const dates = k.map((x) => x.date);
  const ohlc = k.map((x) => [x.open, x.close, x.low, x.high]);
  const vols = k.map((x, i) => ({ value: x.volume, itemStyle: { color: x.close >= x.open ? "rgba(204,120,92,0.55)" : "rgba(59,135,120,0.48)" } }));
  // 前端计算均线
  const closes = k.map((x) => x.close);
  const calcMA = (n) => closes.map((_, i) => {
    if (i < n - 1) return null;
    const seg = closes.slice(i - n + 1, i + 1);
    return +(seg.reduce((a, b) => a + b, 0) / n).toFixed(2);
  });

  // 默认显示最近 N 根，避免 K 线过密
  const TOTAL = k.length;
  const DEFAULT_VISIBLE = 60;           // 默认可见根数
  const startPct = TOTAL > DEFAULT_VISIBLE
    ? Math.round((1 - DEFAULT_VISIBLE / TOTAL) * 100)
    : 0;

  const opt = {
    ...baseOption(),
    animation: !reduceMotion.matches,
    animationDuration: 350,
    animationDurationUpdate: 250,
    animationEasing: "cubicOut",
    animationEasingUpdate: "cubicInOut",
    tooltip: {
      trigger: "axis", axisPointer: { type: "cross", crossStyle: { color: theme.dim } },
      backgroundColor: theme.surface, borderColor: theme.border,
      textStyle: { color: theme.ink, fontFamily: "Consolas, monospace", fontSize: 12 },
    },
    legend: { data: ["K线", "MA5", "MA10", "MA20", "成交量"], textStyle: { color: theme.dim, fontSize: 11 }, top: 0, right: 0 },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 10, right: 16, top: 30, height: mode === "stock" ? "58%" : "62%", containLabel: true },
      { left: 10, right: 16, top: mode === "stock" ? "76%" : "78%", height: "13%", containLabel: true },
    ],
    xAxis: [
      { type: "category", data: dates, boundaryGap: true, axisLine: { lineStyle: { color: theme.border } }, axisLabel: { color: theme.dim, fontSize: 10 }, axisTick: { show: false } },
      { type: "category", gridIndex: 1, data: dates, axisLabel: { show: false }, axisTick: { show: false }, axisLine: { lineStyle: { color: theme.border } } },
    ],
    yAxis: [
      { scale: true, splitLine: { lineStyle: { color: theme.grid } }, axisLabel: { color: theme.dim, fontSize: 10 } },
      { gridIndex: 1, splitNumber: 2, splitLine: { show: false }, axisLabel: { show: false } },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], start: startPct, end: 100 },
      { type: "slider", xAxisIndex: [0, 1], bottom: 0, height: 16, borderColor: theme.border, backgroundColor: theme.zoom, fillerColor: theme.zoomFill, handleStyle: { color: theme.accent }, textStyle: { color: theme.dim, fontSize: 10 } },
    ],
    series: [
      {
        name: "K线", type: "candlestick", data: ohlc,
        barWidth: "62%",                 // K线占类目宽度 62%，留出间隙便于分辨
        itemStyle: { color: theme.up, color0: theme.down, borderColor: theme.up, borderColor0: theme.down },
      },
      ...["MA5", "MA10", "MA20"].map((ma, mi) => ({
        name: ma, type: "line", data: calcMA([5, 10, 20][mi]), smooth: true, symbol: "none",
        lineStyle: { width: 1.2, color: (theme.ma || ["#0071e3", "#ff9f0a", "#5e5ce6"])[mi] },
        animationDelay: 110 + mi * 70,
      })),
      {
        name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: vols,
        animationDelay: 180,
      },
    ],
  };
  chart.setOption(opt, true);
  requestAnimationFrame(() => chart.resize());
}

/* ═══════════ 02 个股透视 ═══════════ */
const searchInput = $("stockSearch");
const suggestBox = $("searchSuggest");
const stockSearchButton = $("stockSearchBtn");
const globalSearch = $("globalSearch");
const globalSuggest = $("globalSuggest");
const searchMenu = $("searchMenu");
const globalSearchToggle = $("globalSearchToggle");
let searchTimer = null;

function setGlobalSearchOpen(open) {
  searchMenu.classList.toggle("open", open);
  globalSearchToggle.setAttribute("aria-expanded", String(open));
  if (open) requestAnimationFrame(() => globalSearch.focus());
  else globalSuggest.classList.remove("open");
}

globalSearchToggle.addEventListener("click", () => setGlobalSearchOpen(!searchMenu.classList.contains("open")));

function suggestionRows(items) {
  return items.slice(0, 8).map((r) => `
    <div class="suggest-item" data-sym="${escapeHtml(r.symbol)}">
      <span class="si-name">${escapeHtml(r.name)}</span>
      <span class="si-code">${escapeHtml(r.code)}</span>
      <span class="si-price ${pctClass(r.pct)}">${r.price ?? "--"} ${fmt.pct(r.pct)}</span>
    </div>`).join("");
}

function rememberSearch(symbol, name = "", code = "") {
  const recent = [{ symbol, name, code: code || symbol.slice(-6) }, ...(state.recentSearches || []).filter((r) => r.symbol !== symbol)].slice(0, 10);
  state.recentSearches = recent;
  saveState({ recent_searches: recent });
}

function openStock(symbol, name = "", code = "") {
  rememberSearch(symbol, name, code);
  globalSuggest.classList.remove("open");
  globalSearch.value = "";
  setGlobalSearchOpen(false);
  switchTab("stock");
  loadStock(symbol);
}

function bindSuggestions(box) {
  box.querySelectorAll(".suggest-item").forEach((item) => item.addEventListener("click", () => {
    openStock(item.dataset.sym, item.querySelector(".si-name")?.textContent, item.querySelector(".si-code")?.textContent);
  }));
}

globalSearch.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = globalSearch.value.trim();
  if (!q) { globalSuggest.classList.remove("open"); return; }
  searchTimer = setTimeout(async () => {
    try {
      const rows = (await api("/api/stock/search?q=" + encodeURIComponent(q))).results || [];
      renderHtml(globalSuggest, suggestionRows(rows) || '<div class="muted" style="padding:10px">未找到匹配股票</div>', bindSuggestions);
      globalSuggest.classList.add("open");
    } catch (error) { renderHtml(globalSuggest, `<div class="module-inline-error" style="padding:10px">搜索失败：${escapeHtml(error.message)}</div>`); globalSuggest.classList.add("open"); }
  }, 220);
});
globalSearch.addEventListener("focus", () => {
  if (globalSearch.value || !state.recentSearches?.length) return;
  renderHtml(globalSuggest, '<div class="muted" style="padding:6px 10px">最近搜索</div>' + suggestionRows(state.recentSearches), bindSuggestions);
  globalSuggest.classList.add("open");
});
globalSearch.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && globalSuggest.querySelector(".suggest-item")) globalSuggest.querySelector(".suggest-item").click();
});

function submitStockSearch() {
  const q = searchInput.value.trim();
  if (!q) {
    searchInput.focus();
    toast("请输入股票代码或名称");
    return;
  }
  suggestBox.classList.remove("open");
  loadStock(q);
}

stockSearchButton.addEventListener("click", submitStockSearch);

searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = searchInput.value.trim();
  if (!q) { suggestBox.classList.remove("open"); return; }
  searchTimer = setTimeout(async () => {
    try {
      const d = await api("/api/stock/search?q=" + encodeURIComponent(q));
      const items = d.results || [];
      if (!items.length) { suggestBox.classList.remove("open"); return; }
      renderHtml(suggestBox, suggestionRows(items), (updatedBox) => {
        updatedBox.querySelectorAll(".suggest-item").forEach((it) =>
          it.addEventListener("click", () => { updatedBox.classList.remove("open"); openStock(it.dataset.sym, it.querySelector(".si-name")?.textContent, it.querySelector(".si-code")?.textContent); })
        );
      });
      suggestBox.classList.add("open");
    } catch (e) { /* 搜索失败静默 */ }
  }, 260);
});
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    submitStockSearch();
  }
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search-row")) suggestBox.classList.remove("open");
  if (!e.target.closest(".search-menu")) setGlobalSearchOpen(false);
});

async function loadWatchlist() {
  const box = $("watchlist");
  try {
    const data = await api("/api/watchlist");
    state.watchlist = (data.items || []).map((item) => item.symbol);
    renderHtml(box, (data.items || []).map((item) => `<button class="watch-item" data-sym="${item.symbol}" type="button"><span>${escapeHtml(item.name)}</span><small>${item.code}</small><b class="${pctClass(item.pct)}">${fmt.pct(item.pct)}</b><span class="watch-remove" title="移除">×</span></button>`).join("") || '<span class="muted">还没有自选股。分析个股后可一键加入。</span>', (updatedBox) => {
      updatedBox.querySelectorAll(".watch-item").forEach((item) => item.addEventListener("click", (event) => {
        if (event.target.closest(".watch-remove")) removeWatch(item.dataset.sym);
        else openStock(item.dataset.sym);
      }));
    });
    syncWatchButton();
  } catch (error) { renderHtml(box, `<span class="module-inline-error">自选加载失败：${escapeHtml(error.message)}</span> <button class="module-retry" type="button">重试</button>`, (updatedBox) => updatedBox.querySelector("button")?.addEventListener("click", loadWatchlist)); }
}

function syncWatchButton() {
  $("watchCurrentBtn").textContent = state.currentStock && state.watchlist.includes(state.currentStock) ? "移出自选" : "加入自选";
  $("watchCurrentBtn").disabled = !state.currentStock;
}
async function removeWatch(symbol) { await api(`/api/watchlist/${encodeURIComponent(symbol)}`, { method: "DELETE" }); await loadWatchlist(); toast("已移出自选"); }
$("watchCurrentBtn").addEventListener("click", async () => {
  if (!state.currentStock) return;
  try {
    if (state.watchlist.includes(state.currentStock)) await removeWatch(state.currentStock);
    else { await api(`/api/watchlist/${encodeURIComponent(state.currentStock)}`, { method: "POST" }); await loadWatchlist(); toast("已加入自选"); }
  } catch (error) { toast("自选操作失败：" + error.message, true); }
});
$("copyStockBtn").addEventListener("click", async () => {
  if (!state.currentStock) return;
  const text = `${$("sName").textContent}（${$("sCode").textContent}）\n${$("sSummary").textContent}\n技术面 ${$("sdTechV").textContent}/40 · 基本面 ${$("sdFundV").textContent}/35 · 资金面 ${$("sdMoneyV").textContent}/25\n仅供学习参考，不构成投资建议。`;
  try { await navigator.clipboard.writeText(text); toast("个股摘要已复制"); } catch { toast("复制失败，请手动选择摘要", true); }
});

function stockMetricSkeleton(count = 6) {
  return Array.from({ length: count }, () => '<div class="metric metric-skeleton"></div>').join("");
}

function resetStockReport(query) {
  $("sName").textContent = "正在分析";
  $("sCode").textContent = query;
  $("sLabel").textContent = "并行加载";
  $("sLabel").className = "stock-label";
  $("sScore").textContent = "--";
  $("sConfidence").textContent = "等待三个维度";
  $("scoreRing").style.setProperty("--p", 0);
  $("sSummary").textContent = "技术面、基本面、资金面与 K 线正在并行加载，先完成的内容会先显示。";
  [["sdTech", "sdTechV"], ["sdFund", "sdFundV"], ["sdMoney", "sdMoneyV"]].forEach(([bar, value]) => {
    $(bar).style.width = "0%";
    $(value).textContent = "--";
  });
  [["stockTechnicalCard", "sTechMetrics", "sTrend"], ["stockFundamentalCard", "sFundMetrics", "sReportPeriod"], ["stockMoneyCard", "sMoneyMetrics", "sFundTag"]].forEach(([card, metrics, badge]) => {
    $(card).classList.add("is-loading");
    $(card).classList.remove("has-error");
    renderHtml(metrics, stockMetricSkeleton());
    $(badge).textContent = "加载中…";
    $(badge).className = "badge neutral";
  });
  $("stockChartCard").classList.add("is-loading");
  $("stockChartCard").classList.remove("has-error");
  delete $("stockChart").dataset.error;
  state.stockChart?.clear();
  renderHtml("sPros", "<li>等待分析结果</li>");
  renderHtml("sCons", "<li>等待分析结果</li>");
}

function renderModuleError(cardId, targetId, message, symbol) {
  const card = $(cardId);
  card.classList.remove("is-loading");
  card.classList.add("has-error");
  renderHtml(targetId, `<div class="module-error"><span>${escapeHtml(message)}</span><button class="module-retry" type="button">重新加载</button></div>`, (updatedTarget) => {
    updatedTarget.querySelector(".module-retry")?.addEventListener("click", () => loadStock(symbol));
  });
}

function stockVerdict(score) {
  if (score >= 75) return ["强势", "较高"];
  if (score >= 60) return ["偏强", "中等"];
  if (score >= 45) return ["中性", "中等"];
  if (score >= 30) return ["偏弱", "中等"];
  return ["弱势", "较高"];
}

function updateStockAggregate(parts, allSettled = false) {
  const reports = [parts.technical, parts.fundamental, parts.fund].filter(Boolean);
  const name = parts.fundamental?.name || parts.fund?.name || parts.code;
  $("sName").textContent = name || parts.code;
  $("sCode").textContent = parts.code;

  const positives = reports.flatMap((item) => item.positives || []);
  const risks = reports.flatMap((item) => item.risks || []);
  renderHtml("sPros", positives.map((item) => `<li>${escapeHtml(item)}</li>`).join("") || "<li>暂无显著亮点</li>");
  renderHtml("sCons", risks.map((item) => `<li>${escapeHtml(item)}</li>`).join("") || "<li>暂无显著风险</li>");

  if (reports.length === 3) {
    const score = reports.reduce((sum, item) => sum + (item.score || 0), 0);
    const [label, confidence] = stockVerdict(score);
    $("sScore").textContent = score;
    $("sConfidence").textContent = "置信度 " + confidence;
    $("scoreRing").style.setProperty("--p", Math.max(0, Math.min(100, score)));
    $("sLabel").textContent = label;
    $("sSummary").textContent = `${name}（${parts.code}）综合评分 ${score} 分，当前判断为「${label}」。` +
      (positives.length ? ` 亮点：${positives.slice(0, 3).join("；")}。` : "") +
      (risks.length ? ` 风险：${risks.slice(0, 3).join("；")}。` : "");
  } else {
    $("sLabel").textContent = allSettled ? "部分完成" : `已完成 ${reports.length}/3`;
    $("sSummary").textContent = allSettled
      ? `已完成 ${reports.length} 个分析维度，其余模块暂不可用；已返回内容仍可独立参考。`
      : `已返回 ${reports.length}/3 个分析维度，其余内容继续加载中。`;
  }
  pulseData(["sScore", "sSummary", "sPros", "sCons"]);
}

async function resolveStockQuery(query) {
  const value = query.trim();
  if (/^(sh|sz|bj)?\d{6}$/i.test(value)) return value;
  const result = await api("/api/stock/search?q=" + encodeURIComponent(value));
  const rows = result.results || [];
  const exact = rows.find((item) => item.name === value || item.code === value);
  if (!exact && !rows.length) throw new Error("未找到匹配的股票，请检查代码或名称");
  return (exact || rows[0]).symbol;
}

async function loadStock(query) {
  const requestId = ++state.stockRequestId;
  const report = $("stockReport");
  const ph = $("stockPlaceholder");
  let symbol;
  try {
    symbol = await resolveStockQuery(query);
  } catch (e) {
    toast("个股搜索失败：" + e.message, true);
    return;
  }
  if (requestId !== state.stockRequestId) return;

  const code = symbol.replace(/^(sh|sz|bj)/i, "");
  state.currentStock = symbol.toLowerCase();
  rememberSearch(state.currentStock, /^(?:sh|sz|bj)?\d{6}$/i.test(query) ? "" : query, code);
  syncWatchButton();
  const parts = { code };
  const ticket = beginLoading(4, "正在并行拆解个股信号");
  ph.style.display = "none";
  report.hidden = false;
  resetStockReport(code);

  const loadDimension = async (key, path, onData, cardId, targetId, label) => {
    try {
      const data = await api(path);
      if (requestId !== state.stockRequestId) return;
      parts[key] = data;
      onData(data.data || {});
      $(cardId).classList.remove("is-loading", "has-error");
      markFresh(cardId, data);
      const dim = key === "technical" ? ["sdTech", "sdTechV"] : key === "fundamental" ? ["sdFund", "sdFundV"] : ["sdMoney", "sdMoneyV"];
      $(dim[0]).style.width = Math.max(0, Math.min(100, data.score / data.max_score * 100)) + "%";
      $(dim[1]).textContent = data.score ?? "--";
      updateStockAggregate(parts);
      pulseData(targetId);
    } catch (e) {
      if (requestId === state.stockRequestId) renderModuleError(cardId, targetId, `${label}加载失败：${e.message}`, symbol);
    } finally {
      completeLoading(ticket, label + "已完成");
    }
  };

  const jobs = [
    loadDimension("technical", `/api/stock/${encodeURIComponent(symbol)}/technical`, (data) => {
      renderTechMetrics(data);
      $("sTrend").textContent = data.trend || "--";
      $("sTrend").className = "badge " + (data.trend === "多头" ? "hot" : data.trend === "空头" ? "cold" : "neutral");
    }, "stockTechnicalCard", "sTechMetrics", "技术面"),
    loadDimension("fundamental", `/api/stock/${encodeURIComponent(symbol)}/fundamental`, (data) => {
      renderFundMetrics(data);
      $("sReportPeriod").textContent = data.report_period ? "报告期 " + data.report_period : "数据已返回";
    }, "stockFundamentalCard", "sFundMetrics", "基本面"),
    loadDimension("fund", `/api/stock/${encodeURIComponent(symbol)}/fund`, (data) => {
      renderMoneyMetrics(data);
      $("sFundTag").textContent = data.net > 0 ? "净流入" : data.net < 0 ? "净流出" : "数据已返回";
      $("sFundTag").className = "badge " + (data.net > 0 ? "hot" : data.net < 0 ? "cold" : "neutral");
    }, "stockMoneyCard", "sMoneyMetrics", "资金面"),
    (async () => {
      try {
        const data = await api(`/api/stock/${encodeURIComponent(symbol)}/kline?limit=120`);
        if (requestId !== state.stockRequestId) return;
        renderKline("stockChart", data.kline || [], parts.fundamental?.name || parts.fund?.name || code, "stock");
        $("stockChartCard").classList.remove("is-loading", "has-error");
        markFresh("stockChartCard", data);
        pulseData("stockChart");
      } catch (e) {
        if (requestId === state.stockRequestId) {
          $("stockChartCard").classList.remove("is-loading");
          $("stockChartCard").classList.add("has-error");
          $("stockChart").dataset.error = `K线加载失败：${e.message}`;
        }
      } finally {
        completeLoading(ticket, "K线走势已完成");
      }
    })(),
  ];

  await Promise.allSettled(jobs);
  if (requestId === state.stockRequestId) updateStockAggregate(parts, true);
}

function metric(label, value, cls = "") {
  return `<div class="metric"><div class="m-label">${label}</div><div class="m-value ${cls}">${value}</div></div>`;
}

function renderTechMetrics(t) {
  const ma = t.ma || {};
  const macd = t.macd || {};
  const kdj = t.kdj || {};
  const items = [
    metric("现价", fmt.num(t.price), pctClass(t.chg20)),
    metric("MA5 / MA20", `${fmt.num(ma.ma5)} / ${fmt.num(ma.ma20)}`),
    metric("MACD DIF / DEA", `${fmt.num(macd.dif, 3)} / ${fmt.num(macd.dea, 3)}`, macd.golden_cross ? "pos" : "neg"),
    metric("KDJ K / D / J", `${kdj.k ?? "--"} / ${kdj.d ?? "--"} / ${kdj.j ?? "--"}`),
    metric("RSI(14)", t.rsi14 ?? "--", t.rsi14 > 70 ? "pos" : t.rsi14 < 30 ? "neg" : ""),
    metric("量比", t.vol_ratio ?? "--"),
    metric("20日涨跌", fmt.pct(t.chg20), pctClass(t.chg20)),
    metric("年内位置", t.pos_in_year != null ? t.pos_in_year.toFixed(0) + "%" : "--"),
  ];
  renderHtml("sTechMetrics", items.join(""));
}

function renderFundMetrics(f) {
  const profile = f.company_profile || {};
  const items = [
    metric("ROE", f.roe != null ? f.roe.toFixed(2) + "%" : "--", f.roe >= 15 ? "pos" : f.roe < 0 ? "neg" : ""),
    metric("EPS", f.eps != null ? f.eps.toFixed(2) + " 元" : "--", f.eps < 0 ? "neg" : ""),
    metric("营收增速", fmt.pct(f.revenue_yoy), pctClass(f.revenue_yoy)),
    metric("净利增速", fmt.pct(f.profit_yoy), pctClass(f.profit_yoy)),
    metric("毛利率", f.gross_margin != null ? f.gross_margin.toFixed(2) + "%" : "--"),
    metric("负债率", f.debt_ratio != null ? f.debt_ratio.toFixed(2) + "%" : "--", f.debt_ratio > 70 ? "neg" : ""),
    metric("每股净资产", f.bps != null ? f.bps.toFixed(2) + " 元" : "--"),
    metric("PB(近似)", f.pb_approx ?? "--"),
  ];
  if (profile.source) {
    const industry = String(profile.eastmoney_industry || profile.csrc_industry || "--").replaceAll("-", " · ");
    items.push(
      metric("所属行业", escapeHtml(industry), "meta"),
      metric("上市交易所", escapeHtml(profile.exchange), "meta"),
      metric("所属地区", escapeHtml(profile.region), "meta"),
      metric("上市日期", escapeHtml(profile.listing_date), "meta"),
    );
  }
  renderHtml("sFundMetrics", items.join(""));
}

function renderMoneyMetrics(fa) {
  const net = fa.net;
  const items = [
    metric("净流入", net != null ? fmt.num(net / 1e8) + " 亿" : "--", pctClass(net)),
    metric("流入", fa.inflow != null ? fmt.num(fa.inflow / 1e8) + " 亿" : "--"),
    metric("流出", fa.outflow != null ? fmt.num(fa.outflow / 1e8) + " 亿" : "--"),
    metric("换手率", fa.turnover != null ? fa.turnover.toFixed(2) + "%" : "--"),
  ];
  renderHtml("sMoneyMetrics", items.join(""));
}

/* ═══════════ 03 涨停复盘 ═══════════ */
async function loadLimitup() {
  const ticket = beginLoading(2, "正在并行载入涨停复盘");
  const parts = {};
  const query = state.limitDate ? `?trade_date=${state.limitDate}` : "";
  if (!state.loaded.limitup) {
    ["limitSentimentCard", "limitLadderCard", "limitIndustryCard", "limitLeadersCard", "limitConclusionCard"].forEach((id) => $(id).classList.add("module-loading"));
    renderHtml($("ltTable").querySelector("tbody"), '<tr class="table-skeleton"><td colspan="8"><span></span></td></tr>');
    $("ltConclusion").textContent = "情绪与涨停结构正在并行读取，先返回的模块会先显示。";
  }

  const refreshConclusion = () => {
    const s = parts.sentiment;
    const p = parts.pool;
    if (s && p) {
      const top = Object.entries(p.industry_dist || {})[0];
      $("ltConclusion").textContent = `${s.trade_date} 涨停 ${s.limit_up} 家、炸板 ${s.zha_ban} 家（炸板率 ${s.zha_rate ?? "--"}%），最高连板 ${s.max_streak} 板，情绪温度 ${s.temperature} 分，当前阶段：${s.phase}。` + (top ? ` 涨停最集中的行业为「${top[0]}」（${top[1]} 家）。` : "");
    } else {
      $("ltConclusion").textContent = s ? "情绪指标已返回，涨停结构继续加载中。" : "涨停结构已返回，情绪指标继续加载中。";
    }
    $("limitConclusionCard").classList.remove("module-loading");
    pulseData("limitConclusionCard");
  };

  const sentimentTask = api("/api/limitup/sentiment" + query).then((d) => {
    parts.sentiment = d;
    $("ltPhase").textContent = d.phase || "--";
    $("ltPhase").className = "badge " + (d.temperature >= 60 ? "hot" : d.temperature < 40 ? "cold" : "");
    $("ltTemp").textContent = d.temperature ?? "--";
    $("ltStageText").textContent = "情绪阶段 · " + (d.phase || "--");
    $("ltTempFill").style.width = (d.temperature ?? 0) + "%";
    $("ltMaxStreak").textContent = d.max_streak ?? "--";
    $("ltUpCount").textContent = d.limit_up ?? "--";
    $("ltZbCount").textContent = d.zha_ban ?? "--";
    $("ltZbRate").textContent = d.zha_rate != null ? d.zha_rate.toFixed(1) + "%" : "--";
    $("ltPromo").textContent = d.promotion_rate != null ? d.promotion_rate.toFixed(1) + "%" : "--";
    $("limitSentimentCard").classList.remove("module-loading");
    markFresh("limitSentimentCard", d);
    pulseData(["limitSentimentCard", "limitStats"]);
    refreshConclusion();
  }).catch((error) => {
    $("ltStageText").textContent = "情绪指标暂不可用：" + error.message;
    $("limitSentimentCard").classList.remove("module-loading");
    moduleError("limitSentimentCard", error.message, loadLimitup);
  }).finally(() => completeLoading(ticket, "情绪指标已完成"));

  const poolTask = api("/api/limitup/pool" + query).then((d) => {
    if (d.error) throw new Error(d.error);
    parts.pool = d;
    delete $("ladderChart").dataset.error;
    delete $("indChart").dataset.error;
    $("ltMaxStreak").textContent = d.max_streak ?? "--";
    $("ltEarly").textContent = d.early_seal_count ?? "--";
    const inds = Object.entries(d.industry_dist || {});
    $("ltTopInd").textContent = inds.length ? inds[0][0] : "--";
    $("ltDate").textContent = d.trade_date || "";
    renderLadder(d.ladder || {});
    renderIndChart(inds.slice(0, 8));
    const tb = $("ltTable").querySelector("tbody");
    renderHtml(tb, (d.leaders || []).map((r, i) => `
      <tr>
        <td class="mono muted">${i + 1}</td>
        <td class="mono">${r.code}</td>
        <td>${r.name}</td>
        <td><span class="streak-chip ${r.streak >= 3 ? "top" : ""}">${r.streak}板</span></td>
        <td class="mono ${pctClass(r.pct)}">${fmt.pct(r.pct)}</td>
        <td class="mono">${fmt.num(r.seal_money_yi)}</td>
        <td class="muted">${r.industry || "--"}</td>
        <td class="mono muted">${r.first_seal || "--"}</td>
      </tr>`).join("") || '<tr><td colspan="8" class="muted" style="text-align:center">暂无数据</td></tr>');
    ["limitLadderCard", "limitIndustryCard", "limitLeadersCard"].forEach((id) => $(id).classList.remove("module-loading", "is-loading"));
    ["limitLadderCard", "limitIndustryCard", "limitLeadersCard"].forEach((id) => markFresh(id, d));
    pulseData(["limitLadderCard", "limitIndustryCard", "limitLeadersCard", "limitStats"]);
    refreshConclusion();
  }).catch((error) => {
    ["limitLadderCard", "limitIndustryCard", "limitLeadersCard"].forEach((id) => $(id).classList.remove("module-loading", "is-loading"));
    renderHtml($("ltTable").querySelector("tbody"), `<tr><td colspan="8"><p class="module-inline-error">涨停池暂不可用：${escapeHtml(error.message)}</p></td></tr>`);
    $("ladderChart").dataset.error = `连板梯队加载失败：${error.message}`;
    $("indChart").dataset.error = `行业分布加载失败：${error.message}`;
    moduleError("limitLeadersCard", error.message, loadLimitup);
  }).finally(() => completeLoading(ticket, "涨停结构已完成"));

  await Promise.allSettled([sentimentTask, poolTask]);
  state.loaded.limitup = Boolean(parts.sentiment || parts.pool);
}

function setLimitDate(kind, value = "") {
  const date = new Date();
  if (kind === "yesterday") date.setDate(date.getDate() - 1);
  state.limitDate = kind === "custom" ? value.replaceAll("-", "") : kind === "today" ? "" : date.toISOString().slice(0, 10).replaceAll("-", "");
  document.querySelectorAll("[data-limit-date]").forEach((button) => button.classList.toggle("active", button.dataset.limitDate === kind));
  state.loaded.limitup = false;
  loadLimitup();
}
document.querySelectorAll("[data-limit-date]").forEach((button) => button.addEventListener("click", () => setLimitDate(button.dataset.limitDate)));
$("limitDate").addEventListener("change", (event) => event.target.value && setLimitDate("custom", event.target.value));

function renderLadder(ladder) {
  const el = $("ladderChart");
  const chart = state.ladderChart || (state.ladderChart = echarts.init(el));
  const theme = chartTheme();
  const levels = Object.keys(ladder).map(Number).filter((n) => n > 0);
  if (!levels.length) levels.push(1);
  const data = levels.map((l) => ladder[String(l)] ?? 0);
  chart.setOption({
    ...baseOption(),
    tooltip: { trigger: "axis", backgroundColor: theme.surface, borderColor: theme.border, textStyle: { color: theme.ink } },
    xAxis: { type: "category", data: levels.map((l) => l + "板"), axisLine: { lineStyle: { color: theme.border } }, axisLabel: { color: theme.dim, fontSize: 11 }, axisTick: { show: false } },
    yAxis: { type: "value", minInterval: 1, splitLine: { lineStyle: { color: theme.grid } }, axisLabel: { color: theme.dim, fontSize: 10 } },
    series: [{
      type: "bar", data, barWidth: "46%",
      itemStyle: {
        borderRadius: [5, 5, 0, 0],
        color: theme.accent,
      },
      label: { show: true, position: "top", color: theme.ink, fontFamily: "Consolas, monospace" },
    }],
  }, true);
}

function renderIndChart(inds) {
  const el = $("indChart");
  const chart = state.indChart || (state.indChart = echarts.init(el));
  const theme = chartTheme();
  const names = inds.map(([n]) => n).reverse();
  const vals = inds.map(([, v]) => v).reverse();
  chart.setOption({
    ...baseOption(),
    tooltip: { trigger: "axis", backgroundColor: theme.surface, borderColor: theme.border, textStyle: { color: theme.ink } },
    xAxis: { type: "value", minInterval: 1, splitLine: { lineStyle: { color: theme.grid } }, axisLabel: { color: theme.dim, fontSize: 10 } },
    yAxis: { type: "category", data: names, axisLine: { lineStyle: { color: theme.border } }, axisLabel: { color: theme.dim, fontSize: 11 }, axisTick: { show: false } },
    series: [{
      type: "bar", data: vals, barWidth: "55%",
      itemStyle: {
        borderRadius: [0, 5, 5, 0],
        color: theme.accent,
      },
      label: { show: true, position: "right", color: theme.ink, fontFamily: "Consolas, monospace" },
    }],
  }, true);
}

/* ═══════════ 04 每日关注池 ═══════════ */
function renderDailyPicks(data) {
  $("pickDate").textContent = data.trade_date || "--";
  $("pickStrategy").textContent = data.strategy || "daily_score_v1";
  $("pickNotice").textContent = data.notice || (data.cached ? "当日缓存 · 快速读取" : "公开行情 · 规则评分");
  $("pickGeneratedAt").textContent = data.generated_at ? `生成于 ${String(data.generated_at).replace("T", " ")}` : "生成时间未知";
  const rows = data.items || [];
  renderHtml("pickList", rows.length ? rows.map((row, index) => `
    <article class="card pick-card module-reveal">
      <div class="pick-card-head">
        <div><span class="pick-rank">关注 ${String(index + 1).padStart(2, "0")}</span><div class="pick-identity"><h2>${escapeHtml(row.name)}</h2><span class="pick-code">${escapeHtml(row.code)}</span></div><div class="pick-quote"><b>${tradeNum(row.price)}</b><span class="${pctClass(row.pct)}">${fmt.pct(row.pct)}</span><small class="muted">成交 ${tradeNum(row.amount)} 亿</small></div></div>
        <div class="pick-score"><b>${tradeNum(row.score, 1)}</b><span>综合评分 / 100</span></div>
      </div>
      <div class="pick-card-detail" data-pick-detail="${index}"><div class="pick-detail-skeleton skeleton"></div></div>
      <div class="pick-actions"><button class="secondary-button" type="button" data-pick-stock="${escapeHtml(row.symbol)}" data-name="${escapeHtml(row.name)}" data-code="${escapeHtml(row.code)}">进入个股透视</button><button class="secondary-button" type="button" data-pick-watch="${escapeHtml(row.symbol)}" ${state.watchlist.includes(row.symbol) ? "disabled" : ""}>${state.watchlist.includes(row.symbol) ? "已在自选" : "加入自选"}</button></div>
    </article>`).join("") : '<div class="card"><p class="muted">当前数据不足，暂未形成符合规则的关注池。</p></div>', (pickList) => {
  rows.forEach((row, index) => setTimeout(() => {
    const detail = pickList.querySelector(`[data-pick-detail="${index}"]`);
    if (!detail) return;
    renderHtml(detail, `
      <div class="pick-dimensions">
        <div><span>趋势 / 40</span><b>${tradeNum(row.trend_score, 1)}</b></div>
        <div><span>动量 / 25</span><b>${tradeNum(row.momentum_score, 1)}</b></div>
        <div><span>流动性 / 20</span><b>${tradeNum(row.liquidity_score, 1)}</b></div>
        <div><span>风险 / 15</span><b>${tradeNum(row.risk_score, 1)}</b></div>
      </div>
      <ul class="pick-reasons">${(row.reasons || []).map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>
      <div class="pick-risk-row">${(row.risk_tags || []).length ? row.risk_tags.map((tag) => `<span class="pick-risk-tag">${escapeHtml(tag)}</span>`).join("") : '<span class="pick-risk-tag neutral">未触发额外风险标签</span>'}</div>`);
    detail.classList.add("module-reveal");
  }, reduceMotion.matches ? 0 : 60 + index * 45));
  pickList.querySelectorAll("[data-pick-stock]").forEach((button) => button.addEventListener("click", () => openStock(button.dataset.pickStock, button.dataset.name, button.dataset.code)));
  pickList.querySelectorAll("[data-pick-watch]").forEach((button) => button.addEventListener("click", async () => {
    try {
      await api(`/api/watchlist/${encodeURIComponent(button.dataset.pickWatch)}`, { method: "POST" });
      await loadWatchlist();
      button.textContent = "已在自选";
      button.disabled = true;
      toast("已加入自选");
    } catch (error) { toast("加入自选失败：" + error.message, true); }
  }));
  pulseData(["pickStatusCard", "pickList"]);
  });
}

async function loadPickHistory() {
  try {
    const data = await api("/api/daily-picks/history?days=20");
    const select = $("pickHistory");
    const current = select.value;
    renderHtml(select, '<option value="">最近交易日</option>' + (data.items || []).map((item) => `<option value="${item.date}">${item.date} · ${item.count} 只</option>`).join(""), () => { select.value = current; });
  } catch { /* 没有历史时保留默认入口 */ }
}

async function loadDailyPicks(refresh = false, selectedDate = "") {
  const ticket = beginLoading(2, "正在生成每日关注池");
  if (!state.loaded.picks || refresh || selectedDate) {
    renderHtml("pickList", Array.from({ length: 3 }, () => '<article class="card pick-card module-loading"><div class="pick-skeleton skeleton"></div></article>').join(""));
  }
  const query = selectedDate ? `?date=${encodeURIComponent(selectedDate)}` : refresh ? "?refresh=true" : "";
  const listTask = api("/api/daily-picks" + query).then((data) => {
    renderDailyPicks(data);
    state.loaded.picks = true;
  }).catch((error) => {
    renderHtml("pickList", `<div class="card"><p class="module-inline-error">每日关注池暂不可用：${escapeHtml(error.message)}</p><button class="module-retry" id="pickRetry" type="button">重试</button></div>`, () => {
      $("pickRetry")?.addEventListener("click", () => loadDailyPicks(refresh, selectedDate));
    });
  }).finally(() => completeLoading(ticket, "关注池列表已完成"));
  const historyTask = loadPickHistory().finally(() => completeLoading(ticket, "历史记录已完成"));
  await Promise.allSettled([listTask, historyTask]);
  if (!selectedDate) loadPickHistory();
}

async function showPickRules() {
  const dialog = $("pickRulesDialog");
  dialog.showModal();
  try {
    const data = await api("/api/daily-picks/rules");
    const labels = { trend: "趋势", momentum: "动量", liquidity: "流动性", risk: "风险" };
    renderHtml("pickRulesContent", `
      <p>策略版本 <b class="mono">${escapeHtml(data.strategy)}</b> · 默认展示 ${data.top_k} 只 · 初选池 ${data.pool_size} 只</p>
      <div class="pick-rules-grid">${Object.entries(data.weights || {}).map(([key, value]) => `<div class="pick-rule-weight"><b>${value}</b><span>${labels[key] || key}</span></div>`).join("")}</div>
      <div class="pick-rule-section"><h3>硬性排除</h3><ul>${(data.filters || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>
      <div class="pick-rule-section"><h3>评分维度</h3><ul>${(data.dimensions || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>`);
  } catch (error) { renderHtml("pickRulesContent", `<p class="module-inline-error">规则暂不可用：${escapeHtml(error.message)}</p>`); }
}

$("pickHistory").addEventListener("change", (event) => loadDailyPicks(false, event.target.value));
$("pickRulesBtn").addEventListener("click", showPickRules);

/* ═══════════ 05 小白日报 ═══════════ */
async function loadDaily() {
  const ticket = beginLoading(5, "正在并行载入小白日报");
  let succeeded = 0;
  const finish = (label) => completeLoading(ticket, label);
  const reveal = (ids) => {
    ids.forEach((id) => $(id).classList.remove("module-loading"));
    pulseData(ids);
  };
  if (!state.loaded.daily) {
    ["dailyHeadCard", "dailyLimitCard", "dailyHsgtCard", "dailyTempCard", "dailyGainersCard", "dailyLosersCard", "dailyBoardsCard", "dailyConclusionCard"].forEach((id) => $(id).classList.add("module-loading"));
    ["dGainers", "dLosers"].forEach((id) => renderHtml($(id).querySelector("tbody"), '<tr class="table-skeleton"><td colspan="6"><span></span></td></tr>'));
    renderHtml("dBoards", '<div class="metric-skeleton"></div><div class="metric-skeleton"></div><div class="metric-skeleton"></div>');
  }

  const fillTable = (id, rows, degraded) => {
    renderHtml($(id).querySelector("tbody"), rows.map((r, i) => `
      <tr>
        <td class="mono muted">${i + 1}</td>
        <td class="mono">${escapeHtml(r.代码 || r.code || "--")}</td>
        <td>${escapeHtml(r.名称 || r.name || "--")}</td>
        <td class="mono">${r.最新价 ?? r.price ?? "--"}</td>
        <td class="mono ${pctClass(r.涨跌幅 ?? r.pct)}">${fmt.pct(r.涨跌幅 ?? r.pct)}</td>
        <td class="mono muted">${r.成交额 != null ? fmt.num(r.成交额) : "--"}</td>
      </tr>`).join("") || `<tr><td colspan="6" class="muted" style="text-align:center">暂无数据${degraded ? "（降级模式）" : ""}</td></tr>`);
  };

  const marketTask = api("/api/daily/market").then((d) => {
    succeeded += 1;
    $("dDate").textContent = d.date || "--";
    $("dMarketLine").textContent = d.market_line || "--";
    const t = d.temperature || {};
    $("dTempNote").textContent = `市场温度 ${t.temperature ?? "--"} 分（${t.label || "--"}），建议采取「${t.tone || "--"}」策略。`;
    $("dConclusion").textContent = d.conclusion || "--";
    reveal(["dailyHeadCard", "dailyTempCard", "dailyConclusionCard"]);
    ["dailyHeadCard", "dailyTempCard", "dailyConclusionCard"].forEach((id) => markFresh(id, d));
  }).catch((error) => {
    $("dMarketLine").textContent = "市场摘要暂不可用：" + error.message;
    $("dTempNote").textContent = "市场温度暂不可用，不影响其他日报模块。";
    $("dConclusion").textContent = "部分行情暂未返回，请参考已经显示的模块。";
    reveal(["dailyHeadCard", "dailyTempCard", "dailyConclusionCard"]);
    moduleError("dailyHeadCard", error.message, loadDaily);
  }).finally(() => finish("市场摘要已完成"));

  const ranksTask = api("/api/daily/ranks").then((d) => {
    succeeded += 1;
    fillTable("dGainers", d.gainers || [], d.degraded);
    fillTable("dLosers", d.losers || [], d.degraded);
    reveal(["dailyGainersCard", "dailyLosersCard"]);
    ["dailyGainersCard", "dailyLosersCard"].forEach((id) => markFresh(id, d));
  }).catch((error) => {
    ["dGainers", "dLosers"].forEach((id) => renderHtml($(id).querySelector("tbody"), `<tr><td colspan="6"><p class="module-inline-error">涨跌榜暂不可用：${escapeHtml(error.message)}</p></td></tr>`));
    reveal(["dailyGainersCard", "dailyLosersCard"]);
    moduleError("dailyGainersCard", error.message, loadDaily);
  }).finally(() => finish("涨跌榜已完成"));

  const boardsTask = api("/api/daily/boards").then((d) => {
    succeeded += 1;
    const boards = d.boards || [];
    renderHtml("dBoards", boards.map((b) => `
      <div class="board-item">
        <div class="b-name">${escapeHtml(b.board)}</div>
        <div class="b-pct ${pctClass(b.pct)}">${fmt.pct(b.pct)}</div>
        <div class="b-amount">${b.amount != null ? fmt.num(b.amount) + " 亿" : "--"}</div>
      </div>`).join("") || '<div class="muted">暂无板块数据</div>');
    reveal(["dailyBoardsCard"]);
    markFresh("dailyBoardsCard", d);
  }).catch((error) => {
    renderHtml("dBoards", `<p class="module-inline-error">热门板块暂不可用：${escapeHtml(error.message)}</p>`);
    reveal(["dailyBoardsCard"]);
    moduleError("dailyBoardsCard", error.message, loadDaily);
  }).finally(() => finish("热门板块已完成"));

  const hsgtTask = api("/api/market/hsgt").then((d) => {
    succeeded += 1;
    $("dHsgt").textContent = d.note || "北向资金数据暂不可用";
    reveal(["dailyHsgtCard"]);
    markFresh("dailyHsgtCard", d);
  }).catch((error) => {
    $("dHsgt").textContent = "北向资金暂不可用：" + error.message;
    reveal(["dailyHsgtCard"]);
    moduleError("dailyHsgtCard", error.message, loadDaily);
  }).finally(() => finish("北向资金已完成"));

  const sentimentTask = api("/api/limitup/sentiment").then((d) => {
    succeeded += 1;
    $("dZtNote").textContent = `今天有 ${d.limit_up ?? "--"} 只股票涨停（最多连板 ${d.max_streak ?? "--"} 板），炸板 ${d.zha_ban ?? "--"} 只。当前情绪阶段：${d.phase || "--"}。`;
    reveal(["dailyLimitCard"]);
    markFresh("dailyLimitCard", d);
  }).catch((error) => {
    $("dZtNote").textContent = "涨停速览暂不可用：" + error.message;
    reveal(["dailyLimitCard"]);
    moduleError("dailyLimitCard", error.message, loadDaily);
  }).finally(() => finish("涨停速览已完成"));

  await Promise.allSettled([marketTask, ranksTask, boardsTask, hsgtTask, sentimentTask]);
  state.loaded.daily = succeeded > 0;
  if (succeeded) api("/api/daily/archive", { method: "POST" }).then(loadDailyHistory).catch(() => {});
}

async function loadDailyHistory() {
  try {
    const data = await api("/api/daily/history?days=30");
    const select = $("dailyHistory");
    const current = select.value;
    renderHtml(select, '<option value="">今天</option>' + (data.items || []).map((item) => `<option value="${item.date}">${item.date}</option>`).join(""), () => { select.value = current; });
  } catch { /* 本地无历史时保持今天 */ }
}

function renderHistoricalDaily(d) {
  const fill = (id, rows) => {
    renderHtml($(id).querySelector("tbody"), (rows || []).map((r, i) => `<tr><td class="mono muted">${i + 1}</td><td class="mono">${escapeHtml(r.代码)}</td><td>${escapeHtml(r.名称)}</td><td class="mono">${r.最新价 ?? "--"}</td><td class="mono ${pctClass(r.涨跌幅)}">${fmt.pct(r.涨跌幅)}</td><td class="mono muted">${r.成交额 ?? "--"}</td></tr>`).join("") || '<tr><td colspan="6" class="muted">暂无数据</td></tr>');
  };
  $("dDate").textContent = d.date || "--";
  $("dMarketLine").textContent = d.market_line || "--";
  $("dTempNote").textContent = `市场温度 ${d.temperature?.temperature ?? "--"} 分（${d.temperature?.label || "--"}）。`;
  $("dConclusion").textContent = d.conclusion || "--";
  $("dHsgt").textContent = d.hsgt_note || "当日无可用北向数据";
  $("dZtNote").textContent = d.zt_note || "当日无可用涨停速览";
  fill("dGainers", d.gainers); fill("dLosers", d.losers);
  renderHtml("dBoards", (d.hot_boards || []).map((b) => `<div class="board-item"><div class="b-name">${escapeHtml(b.board)}</div><div class="b-pct ${pctClass(b.pct)}">${fmt.pct(b.pct)}</div><div class="b-amount">${b.amount ?? "--"} 亿</div></div>`).join("") || '<span class="muted">暂无板块数据</span>');
  pulseData(["dailyHeadCard", "dailyTempCard", "dailyGainersCard", "dailyLosersCard", "dailyBoardsCard"]);
}
$("dailyHistory").addEventListener("change", async (event) => {
  if (!event.target.value) { loadDaily(); return; }
  try { renderHistoricalDaily(await api("/api/daily/history/" + event.target.value)); }
  catch (error) { toast("历史日报读取失败：" + error.message, true); }
});
$("archiveDailyBtn").addEventListener("click", async () => {
  try { const d = await api("/api/daily/archive", { method: "POST" }); await loadDailyHistory(); toast(`已保存 ${d.date} 日报`); }
  catch (error) { toast("日报保存失败：" + error.message, true); }
});

/* ═══════════ 05 加密货币 ═══════════ */
const compactUsd = new Intl.NumberFormat("zh-CN", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 2 });
const priceUsd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const streamLabels = {
  connecting: "实时连接中", connected: "实时已连接", fallback: "REST 降级", disconnected: "实时已断开",
};

function setCryptoStreamStatus(status, label = streamLabels[status]) {
  const el = $("cryptoStreamStatus");
  el.className = `crypto-stream-status ${status}`;
  el.querySelector("span").textContent = label || streamLabels.disconnected;
  $("cryptoStreamRetry").hidden = status !== "disconnected";
  if (status === "disconnected" && state.cryptoTickers[state.cryptoAsset]) {
    $("cSource").textContent = `缓存价格 · ${state.cryptoTickers[state.cryptoAsset].source}`;
  }
}

function renderCryptoQuote(row, realtime = false) {
  if (!row) return;
  const previous = state.cryptoTickers[row.asset]?.price;
  $("cPrice").textContent = priceUsd.format(row.price);
  $("cPrice").className = "mono";
  if (realtime && previous && previous !== row.price && !reduceMotion.matches) {
    $("cPrice").classList.add(row.price > previous ? "price-tick-up" : "price-tick-down");
    clearTimeout(state.cryptoPriceTimer);
    state.cryptoPriceTimer = setTimeout(() => $("cPrice").classList.remove("price-tick-up", "price-tick-down"), 220);
  }
  $("cChange").textContent = fmt.pct(row.change_24h);
  $("cChange").className = "mono " + pctClass(row.change_24h);
  $("cVolume").textContent = compactUsd.format(row.quote_volume_24h || 0);
  $("cMarketCap").textContent = row.market_cap ? compactUsd.format(row.market_cap) : "--";
  $("cRange").textContent = `高 ${priceUsd.format(row.high_24h)} · 低 ${priceUsd.format(row.low_24h)}`;
  $("cAssetName").textContent = `${row.asset} / USDT`;
  $("cSource").textContent = `${realtime ? "实时推送" : "数据源"} ${row.source}${row.stale ? " · 过期缓存" : ""}`;
  $("cUpdated").textContent = row.updated_at ? `更新 ${new Date(row.updated_at).toLocaleTimeString("zh-CN")}` : "行情已更新";
}

function applyCryptoTicker(ticker, status = "connected") {
  if (!ticker?.asset) return;
  const supporting = state.cryptoOverview[ticker.asset] || {};
  if (ticker.asset === state.cryptoAsset) {
    renderCryptoQuote({ ...supporting, ...ticker }, true);
    window.highlightNode?.($("cPrice"));
  }
  state.cryptoTickers[ticker.asset] = ticker;
  setCryptoStreamStatus(status);
}

function disconnectCryptoStream(label = "实时已断开") {
  clearTimeout(state.cryptoSocketTimer);
  state.cryptoSocketTimer = null;
  const socket = state.cryptoSocket;
  state.cryptoSocket = null;
  if (socket) {
    socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
    socket.close();
  }
  setCryptoStreamStatus("disconnected", label);
}

function connectCryptoStream() {
  if (state.activeView !== "crypto" || document.hidden || [WebSocket.CONNECTING, WebSocket.OPEN].includes(state.cryptoSocket?.readyState)) return;
  clearTimeout(state.cryptoSocketTimer);
  setCryptoStreamStatus("connecting");
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${location.host}/ws/crypto`);
  state.cryptoSocket = socket;
  socket.onopen = () => { state.cryptoSocketAttempts = 0; };
  socket.onmessage = (event) => {
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.type === "snapshot") {
      (message.assets || []).forEach((ticker) => {
        state.cryptoTickers[ticker.asset] = ticker;
        if (ticker.asset === state.cryptoAsset) {
          renderCryptoQuote({ ...(state.cryptoOverview[ticker.asset] || {}), ...ticker }, true);
          window.highlightNode?.($("cPrice"));
        }
      });
      setCryptoStreamStatus(message.status || "connecting");
    } else if (message.type === "ticker") {
      applyCryptoTicker(message.data, message.status);
    } else if (message.type === "status") {
      setCryptoStreamStatus(message.status || "disconnected");
    }
  };
  socket.onerror = () => socket.close();
  socket.onclose = () => {
    if (state.cryptoSocket !== socket) return;
    state.cryptoSocket = null;
    setCryptoStreamStatus("disconnected");
    if (state.activeView !== "crypto" || document.hidden) return;
    const delays = [1, 2, 5, 10, 20, 30];
    const delay = delays[Math.min(state.cryptoSocketAttempts++, delays.length - 1)];
    state.cryptoSocketTimer = setTimeout(connectCryptoStream, delay * 1000);
  };
}

function cryptoSkeleton(resetQuote = false) {
  if (resetQuote) ["cPrice", "cChange", "cVolume", "cMarketCap"].forEach((id) => $(id).textContent = "--");
  $("cLabel").textContent = "加载中…";
  $("cInterpretation").textContent = "正在加载技术指标…";
  renderHtml("cMaMetrics", stockMetricSkeleton(3));
  renderHtml("cMomentumMetrics", stockMetricSkeleton(3));
  renderHtml("cBollMetrics", stockMetricSkeleton(3));
  $("cryptoChartCard").classList.add("is-loading");
}

function renderCryptoOverview(data) {
  const row = (data.assets || []).find((item) => item.asset === state.cryptoAsset);
  if (!row) throw new Error(`${state.cryptoAsset} 行情缺失`);
  (data.assets || []).forEach((item) => { state.cryptoOverview[item.asset] = item; });
  renderCryptoQuote({ ...row, ...(state.cryptoTickers[row.asset] || {}) }, Boolean(state.cryptoTickers[row.asset]));
  markFresh("cryptoChartCard", row);
  pulseData(["cPrice", "cChange", "cVolume", "cMarketCap"]);
}

function renderCryptoAnalysis(data) {
  renderKline("cryptoChart", data.kline || [], data.asset, "crypto");
  $("cryptoChartCard").classList.remove("is-loading", "has-error");
  delete $("cryptoChart").dataset.error;
  $("cChartAsset").textContent = data.asset;
  $("cScore").textContent = data.score ?? "--";
  $("cLabel").textContent = data.label || "--";
  $("cLabel").className = "badge " + (data.label === "偏强" ? "hot" : data.label === "偏弱" ? "cold" : "neutral");
  $("cAnalysisSource").textContent = `${data.source || "--"} · ${data.interval}${data.stale ? " · 过期缓存" : ""}`;
  $("cInterpretation").textContent = data.interpretation || "暂无技术解读";
  const indicators = data.indicators || {};
  const macd = indicators.macd || {};
  const boll = indicators.boll || {};
  renderHtml("cMaMetrics", [metric("MA5", fmt.num(indicators.ma5)), metric("MA10", fmt.num(indicators.ma10)), metric("MA20", fmt.num(indicators.ma20))].join(""));
  renderHtml("cMomentumMetrics", [
    metric("MACD DIF", fmt.num(macd.dif, 4)),
    metric("MACD DEA", fmt.num(macd.dea, 4)),
    metric("RSI(14)", indicators.rsi14 ?? "--", indicators.rsi14 > 70 ? "up" : indicators.rsi14 < 30 ? "down" : ""),
  ].join(""));
  renderHtml("cBollMetrics", [metric("上轨", fmt.num(boll.upper)), metric("中轨", fmt.num(boll.mid)), metric("下轨", fmt.num(boll.lower))].join(""));
  pulseData(["cryptoChart", "cScore", "cInterpretation", "cMaMetrics", "cMomentumMetrics", "cBollMetrics"]);
}

function scheduleCryptoRefresh() {
  clearTimeout(state.cryptoTimer);
  clearInterval(state.cryptoCountdownTimer);
  const frequencyLabel = ({ 1: "1 秒", 60: "1 分钟", 300: "5 分钟", 1200: "20 分钟" })[state.cryptoRefreshSeconds] || `${state.cryptoRefreshSeconds} 秒`;
  $("cryptoAutoLabel").textContent = state.cryptoAuto ? `指标每 ${frequencyLabel}刷新` : "指标刷新已暂停";
  $("cryptoAutoBtn").textContent = state.cryptoAuto ? "暂停指标刷新" : "恢复指标刷新";
  $("cryptoNextRefresh").textContent = state.cryptoAuto ? "准备计时" : "--";
  if (!state.cryptoAuto || state.activeView !== "crypto" || document.hidden) return;
  state.cryptoNextAt = Date.now() + state.cryptoRefreshSeconds * 1000;
  const tick = () => { $("cryptoNextRefresh").textContent = `下次刷新 ${Math.max(0, Math.ceil((state.cryptoNextAt - Date.now()) / 1000))} 秒`; };
  tick();
  state.cryptoCountdownTimer = setInterval(tick, 1000);
  state.cryptoTimer = setTimeout(async () => {
    clearInterval(state.cryptoCountdownTimer);
    if (state.activeView === "crypto" && !document.hidden) await loadCryptoAnalysis();
    scheduleCryptoRefresh();
  }, state.cryptoRefreshSeconds * 1000);
}

async function loadCryptoAnalysis(force = false, ticket = null) {
  const asset = state.cryptoAsset;
  const suffix = force ? "&refresh=true" : "";
  try {
    const data = await api(`/api/crypto/${asset}/analysis?interval=${state.cryptoInterval}&limit=240${suffix}`);
    if (asset === state.cryptoAsset) renderCryptoAnalysis(data);
    if (ticket) completeLoading(ticket, "技术分析已到达");
    return true;
  } catch (error) {
    if (asset !== state.cryptoAsset) return false;
    $("cryptoChartCard").classList.remove("is-loading");
    $("cryptoChartCard").classList.add("has-error");
    $("cryptoChart").dataset.error = `技术分析加载失败：${error.message}`;
    $("cInterpretation").textContent = "技术分析暂不可用，请稍后重试。";
    moduleError("cryptoChartCard", error.message, () => loadCryptoAnalysis(true));
    if (ticket) completeLoading(ticket, "技术分析暂不可用");
    return false;
  }
}

async function loadCrypto(force = false) {
  const ticket = beginLoading(2, `正在加载 ${state.cryptoAsset} 行情`);
  let succeeded = 0;
  cryptoSkeleton(!state.cryptoTickers[state.cryptoAsset] && !state.loaded.crypto);
  const overviewTask = api(`/api/crypto/overview?refresh=${force}`).then((data) => {
    succeeded += 1;
    renderCryptoOverview(data);
    completeLoading(ticket, "行情快照已到达");
  }).catch((error) => {
    toast("加密行情加载失败：" + error.message, true);
    moduleError(document.querySelector(".crypto-market-grid"), error.message, () => loadCrypto(true));
    completeLoading(ticket, "行情快照暂不可用");
  });
  const analysisTask = loadCryptoAnalysis(force, ticket).then((ok) => { if (ok) succeeded += 1; });
  await Promise.allSettled([overviewTask, analysisTask]);
  state.cryptoLoaded = true;
  state.loaded.crypto = succeeded > 0;
  scheduleCryptoRefresh();
}

document.querySelectorAll("#cryptoAssetSwitch .asset-button").forEach((button) => button.addEventListener("click", () => {
  state.cryptoAsset = button.dataset.asset;
  document.querySelectorAll("#cryptoAssetSwitch .asset-button").forEach((item) => item.classList.toggle("active", item === button));
  const ticker = state.cryptoTickers[state.cryptoAsset];
  if (ticker) renderCryptoQuote({ ...(state.cryptoOverview[state.cryptoAsset] || {}), ...ticker }, true);
  loadCrypto();
}));
document.querySelectorAll("#cryptoIntervalSwitch .chip").forEach((button) => button.addEventListener("click", () => {
  state.cryptoInterval = button.dataset.interval;
  document.querySelectorAll("#cryptoIntervalSwitch .chip").forEach((item) => item.classList.toggle("active", item === button));
  loadCrypto();
}));
$("cryptoRefreshBtn").addEventListener("click", () => loadCrypto(true));
$("cryptoStreamRetry").addEventListener("click", () => {
  disconnectCryptoStream();
  connectCryptoStream();
  api("/api/crypto/overview?refresh=true").then(renderCryptoOverview).catch(() => {});
});
$("cryptoAutoBtn").addEventListener("click", () => {
  state.cryptoAuto = !state.cryptoAuto;
  scheduleCryptoRefresh();
});
document.querySelectorAll("#cryptoFrequency [data-seconds]").forEach((button) => button.addEventListener("click", () => {
  state.cryptoRefreshSeconds = Number(button.dataset.seconds);
  document.querySelectorAll("#cryptoFrequency [data-seconds]").forEach((item) => item.classList.toggle("active", item === button));
  saveState({ crypto_refresh_seconds: state.cryptoRefreshSeconds });
  scheduleCryptoRefresh();
}));

/* ═══════════ Binance 只读查询 ═══════════ */
const tradeNum = (value, digits = 2) => Number.isFinite(Number(value)) ? fmt.num(Number(value), digits) : "--";
const tradeOrderStatus = (value) => ({ New: "挂单中", Created: "已创建", Submitted: "已提交", PendingSubmit: "正在提交", SubmitUnknown: "结果待确认", Untriggered: "待触发", PartiallyFilled: "部分成交", Filled: "已成交", Cancelled: "已撤销", Rejected: "已拒绝", Deactivated: "已失效" })[value] || value || "未知";

function setTradeStatus(id, text, stateClass = "") {
  const el = $(id);
  el.className = `status-pill ${stateClass}`;
  const dot = document.createElement("i");
  el.replaceChildren(dot, document.createTextNode(text));
}

function renderTradingStatus(data) {
  state.tradingStatus = data;
  $("tradeEnvironment").textContent = data.environment || "Binance 只读查询";
  $("tradeSetup").hidden = Boolean(data.credentials_configured);
  $("tradeMode").textContent = "只读查询";
  $("tradeAccountMode").textContent = data.credentials_configured ? "已配置 · 只读" : "未配置";
  setTradeStatus("tradeApiStatus", `API ${data.api_status || "--"}`, data.api_status === "已连接" ? "ok" : data.api_status === "异常" ? "bad" : "");
  const ws = data.public_ws || "--";
  const wsLabel = ({ connected: "已连接", connecting: "连接中", reconnecting: "重连中", disconnected: "已断开" })[ws] || ws;
  const privateWs = data.private_ws;
  const privateLabel = ({ connected: "已连接", connecting: "连接中", reconnecting: "重连中", disconnected: "已断开" })[privateWs] || privateWs;
  setTradeStatus("tradeWsStatus", `WS 行情${wsLabel}${data.credentials_configured ? ` · 私有${privateLabel}` : ""}`, ws === "connected" && (!data.credentials_configured || privateWs === "connected") ? "ok" : ws === "disconnected" || privateWs === "disconnected" ? "bad" : "");
}

function renderTradeAccount(data) {
  const asset = data.balance_asset || "USDT";
  $("tradeEquity").textContent = tradeNum(data.equity);
  $("tradeAvailable").textContent = tradeNum(data.available_balance);
  $("tradeUnrealised").textContent = tradeNum(data.unrealised_pnl);
  $("tradeEquityAsset").textContent = asset;
  $("tradeAvailableAsset").textContent = asset;
  $("tradeUnrealisedAsset").textContent = asset;
  $("tradeUnrealised").className = `mono ${pctClass(Number(data.unrealised_pnl || 0))}`;
  pulseData(["tradeEquity", "tradeAvailable", "tradeUnrealised"]);
}

function renderTradePositions(data) {
  const rows = data.items || data.positions || [];
  $("tradePositionsTime").textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN")}`;
  renderHtml("tradePositionsBody", rows.length ? rows.map((row) => `<tr>
    <td class="mono">${escapeHtml(row.symbol)}</td><td class="${row.side === "Buy" ? "up" : "down"}">${row.side === "Buy" ? "多" : "空"}</td>
    <td class="mono">${escapeHtml(row.size)}</td><td class="mono">${tradeNum(row.avgPrice)}</td><td class="mono">${tradeNum(row.markPrice)}</td>
    <td class="mono ${pctClass(Number(row.unrealisedPnl || 0))}">${tradeNum(row.unrealisedPnl)}</td>
  </tr>`).join("") : '<tr><td colspan="6" class="muted">当前没有持仓</td></tr>');
  pulseData("tradePositionsBody");
}

function renderTradeHistory(data, kind = state.tradingHistory) {
  const rows = data.items || [];
  if (kind === "orders") {
    renderHtml("tradeHistoryHead", "<tr><th>时间</th><th>交易对</th><th>方向</th><th>类型</th><th>数量</th><th>价格</th><th>状态</th></tr>");
    renderHtml("tradeHistoryBody", rows.length ? rows.map((row) => `<tr><td>${escapeHtml(String(row.updated_at || "").slice(0, 19).replace("T", " "))}</td><td class="mono">${escapeHtml(row.symbol)}</td><td>${row.side === "Buy" ? "买入" : "卖出"}</td><td>${row.order_type === "Market" ? "市价" : row.order_type === "Limit" ? "限价" : escapeHtml(row.order_type)}</td><td class="mono">${tradeNum(row.qty, 4)}</td><td class="mono">${tradeNum(row.price)}</td><td>${escapeHtml(tradeOrderStatus(row.status))}</td></tr>`).join("") : '<tr><td colspan="7" class="muted">暂无当前挂单</td></tr>');
  } else {
    renderHtml("tradeHistoryHead", "<tr><th>时间</th><th>交易对</th><th>方向</th><th>数量</th><th>成交价</th><th>手续费</th><th>已实现盈亏</th></tr>");
    renderHtml("tradeHistoryBody", rows.length ? rows.map((row) => `<tr><td>${escapeHtml(String(row.executed_at || "").slice(0, 19).replace("T", " "))}</td><td class="mono">${escapeHtml(row.symbol)}</td><td>${row.side === "Buy" ? "买入" : "卖出"}</td><td class="mono">${tradeNum(row.qty, 4)}</td><td class="mono">${tradeNum(row.price)}</td><td class="mono">${tradeNum(row.fee, 4)}</td><td class="mono ${pctClass(Number(row.closed_pnl || 0))}">${tradeNum(row.closed_pnl)}</td></tr>`).join("") : '<tr><td colspan="7" class="muted">暂无成交记录</td></tr>');
  }
  pulseData("tradeHistoryBody");
}

function renderTradeLogs(data) {
  const rows = data.items || [];
  renderHtml("tradeLogs", rows.length ? rows.map((row) => `<div class="trade-log-row ${String(row.level).toLowerCase()}"><time>${escapeHtml(String(row.created_at || "").slice(0, 19).replace("T", " "))}</time><span>${escapeHtml(row.kind)}</span><p>${escapeHtml(row.message)}</p></div>`).join("") : '<p class="muted">暂无运行记录</p>');
  pulseData("tradeLogs");
}

async function tradingBootstrap() {
  const data = await api("/api/trading/bootstrap");
  state.tradingSettings = data.settings || {};
  return data;
}

const tradeHistoryPath = () => state.tradingHistory === "orders" ? "/api/trading/orders?open_only=true" : "/api/trading/trades?today=true";

async function loadTradingDesk() {
  if (!Object.keys(state.tradingSettings).length) await tradingBootstrap();
  const accountTask = state.tradingSettings.credentials_configured
    ? api("/api/trading/account").then((data) => { renderTradeAccount(data); renderTradePositions(data); }).catch((error) => toast("账户读取失败：" + error.message, true))
    : api("/api/trading/positions").then(renderTradePositions);
  const tasks = [
    api("/api/trading/status").then(renderTradingStatus),
    accountTask,
    api(tradeHistoryPath()).then((data) => renderTradeHistory(data)),
    api("/api/trading/logs?limit=80").then(renderTradeLogs),
  ];
  await Promise.allSettled(tasks);
  state.tradingLoaded = true;
}

function disconnectTradingStream() {
  clearTimeout(state.tradingSocketTimer);
  const socket = state.tradingSocket;
  state.tradingSocket = null;
  if (socket) { socket.onclose = null; socket.close(); }
}

function connectTradingStream() {
  if (state.activeView !== "crypto" || state.cryptoPanel !== "trading" || document.hidden || [WebSocket.CONNECTING, WebSocket.OPEN].includes(state.tradingSocket?.readyState)) return;
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${location.host}/api/trading/ws`);
  state.tradingSocket = socket;
  socket.onopen = () => { state.tradingSocketAttempts = 0; setTradeStatus("tradeWsStatus", "WS 已连接", "ok"); };
  socket.onmessage = (event) => {
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.type === "connection") setTradeStatus("tradeWsStatus", `WS 行情${{ connected: "已连接", connecting: "连接中", reconnecting: "重连中", disconnected: "已断开" }[message.public] || message.public || "--"}${state.tradingSettings.credentials_configured ? ` · 私有${{ connected: "已连接", connecting: "连接中", reconnecting: "重连中", disconnected: "已断开" }[message.private] || message.private || "--"}` : ""}`, message.public === "connected" && (!state.tradingSettings.credentials_configured || message.private === "connected") ? "ok" : "");
    if (message.type === "snapshot") {
      setTradeStatus("tradeWsStatus", `WS 行情${{ connected: "已连接", connecting: "连接中", reconnecting: "重连中", disconnected: "已断开" }[message.public_ws] || message.public_ws || "--"}${state.tradingSettings.credentials_configured ? ` · 私有${{ connected: "已连接", connecting: "连接中", reconnecting: "重连中", disconnected: "已断开" }[message.private_ws] || message.private_ws || "--"}` : ""}`, message.public_ws === "connected" && (!state.tradingSettings.credentials_configured || message.private_ws === "connected") ? "ok" : "");
      (message.tickers || []).forEach((ticker) => {
        const target = ticker.symbol === "BTCUSDT" ? "tradeBtcPrice" : ticker.symbol === "ETHUSDT" ? "tradeEthPrice" : "";
        if (target) $(target).textContent = tradeNum(ticker.price);
      });
    }
    if (["order", "execution", "position", "wallet"].includes(message.type)) loadTradingDesk();
    if (message.type === "ticker" && message.data?.symbol) {
      const target = message.data.symbol === "BTCUSDT" ? "tradeBtcPrice" : message.data.symbol === "ETHUSDT" ? "tradeEthPrice" : "";
      if (target) { $(target).textContent = tradeNum(message.data.price); window.highlightNode?.($(target)); }
    }
  };
  socket.onerror = () => socket.close();
  socket.onclose = () => {
    if (state.tradingSocket !== socket) return;
    state.tradingSocket = null;
    setTradeStatus("tradeWsStatus", "WS 已断开", "bad");
    if (state.activeView !== "crypto" || state.cryptoPanel !== "trading" || document.hidden) return;
    const delays = [1, 2, 5, 10, 20, 30];
    state.tradingSocketTimer = setTimeout(connectTradingStream, delays[Math.min(state.tradingSocketAttempts++, delays.length - 1)] * 1000);
  };
}

function switchCryptoPanel(name) {
  if (state.cryptoPanel === name) return;
  const previousPanel = state.cryptoPanel;
  state.cryptoPanel = name;
  const currentPanel = $(previousPanel === "dashboard" ? "cryptoDashboardPanel" : "tradingPanel");
  const targetPanel = $(name === "dashboard" ? "cryptoDashboardPanel" : "tradingPanel");
  const updatePanels = () => ["dashboard", "trading"].forEach((panelName) => {
    const panel = $(panelName === "dashboard" ? "cryptoDashboardPanel" : "tradingPanel");
    const active = panelName === name;
    panel.hidden = !active;
    panel.classList.toggle("active", active);
  });
  if (typeof window.transitionViews === "function") window.transitionViews(currentPanel, targetPanel, updatePanels, { native: false });
  else updatePanels();
  $("cryptoDashboardTab").classList.toggle("active", name === "dashboard");
  $("tradingDeskTab").classList.toggle("active", name === "trading");
  $("cryptoDashboardTab").setAttribute("aria-selected", String(name === "dashboard"));
  $("tradingDeskTab").setAttribute("aria-selected", String(name === "trading"));
  if (name === "trading") { disconnectCryptoStream("交易台已接管实时连接"); openTradingDesk(); }
  else { disconnectTradingStream(); connectCryptoStream(); scheduleCryptoRefresh(); setTimeout(() => state.cryptoChart?.resize(), 20); }
}

function openTradingDesk() {
  loadTradingDesk().catch((error) => toast("交易台加载失败：" + error.message, true));
  connectTradingStream();
}

$("cryptoDashboardTab").addEventListener("click", () => switchCryptoPanel("dashboard"));
$("tradingDeskTab").addEventListener("click", () => switchCryptoPanel("trading"));
$("tradeRefreshBtn").addEventListener("click", () => loadTradingDesk());
document.querySelectorAll("[data-trade-table]").forEach((button) => button.addEventListener("click", () => {
  state.tradingHistory = button.dataset.tradeTable;
  document.querySelectorAll("[data-trade-table]").forEach((item) => item.classList.toggle("active", item === button));
  api(tradeHistoryPath()).then((data) => renderTradeHistory(data)).catch((error) => toast("记录读取失败：" + error.message, true));
}));

document.addEventListener("visibilitychange", () => {
  if (state.activeView !== "crypto") return;
  if (document.hidden) {
    clearTimeout(state.cryptoTimer);
    clearInterval(state.cryptoCountdownTimer);
    disconnectCryptoStream("后台已暂停");
    disconnectTradingStream();
  } else {
    if (state.cryptoPanel === "trading") connectTradingStream();
    else { connectCryptoStream(); scheduleCryptoRefresh(); }
  }
});

/* ---------- 快捷键、关于与启动 ---------- */
document.addEventListener("keydown", (event) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName);
  if (event.key === "/" && !typing) { event.preventDefault(); setGlobalSearchOpen(true); }
  if (event.key === "Escape") { setGlobalSearchOpen(false); suggestBox.classList.remove("open"); }
  if (!typing && /^[1-5]$/.test(event.key)) switchTab(["overview", "picks", "limitup", "stock", "daily"][Number(event.key) - 1]);
});

$("aboutBtn").addEventListener("click", async () => {
  $("aboutDialog").showModal();
  try {
    const h = await api("/api/health");
    $("healthStatus").textContent = `服务正常 · v${h.version}` + (h.last_data_success ? ` · 最近数据成功 ${new Date(h.last_data_success).toLocaleString("zh-CN")}` : " · 等待首次数据请求");
  } catch (error) { $("healthStatus").textContent = "服务检查失败：" + error.message; }
});
$("welcomeDone").addEventListener("click", () => saveState({ welcomed: true }));
$("logoutBtn").addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); }
  finally {
    if (typeof window.motionNavigate === "function") window.motionNavigate("/login", { replace: true });
    else location.replace("/login");
  }
});

async function bootstrap() {
  try {
    const auth = await api("/api/auth/me");
    state.authUser = auth.user;
    state.csrfToken = auth.csrf_token || "";
    $("accountName").textContent = auth.user.username;
    $("accountAdmin").hidden = auth.user.role !== "admin";
  } catch { return; }
  let prefs = {};
  try { prefs = await api("/api/user/state"); } catch { /* 使用默认设置 */ }
  state.recentSearches = prefs.recent_searches || [];
  state.cryptoRefreshSeconds = [1, 60, 300, 1200].includes(prefs.crypto_refresh_seconds) ? prefs.crypto_refresh_seconds : 60;
  document.querySelectorAll("#cryptoFrequency [data-seconds]").forEach((item) => item.classList.toggle("active", Number(item.dataset.seconds) === state.cryptoRefreshSeconds));
  loadOverview();
  loadWatchlist();
  loadPickHistory();
  loadDailyHistory();
  const page = ["overview", "stock", "limitup", "picks", "daily", "crypto"].includes(prefs.last_page) ? prefs.last_page : "overview";
  if (page !== "overview") switchTab(page);
  if (!prefs.welcomed) setTimeout(() => $("welcomeDialog").showModal(), 360);
  setTimeout(syncTabIndicator, 100);
}
bootstrap();
