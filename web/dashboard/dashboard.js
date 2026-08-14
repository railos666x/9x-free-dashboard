/* cryptoautomation — Dashboard
 * Sellimor-style layout wired to the backend API. No framework: plain
 * fetch + ECharts, with defensive error handling everywhere.
 */
"use strict";

const $ = (id) => document.getElementById(id);

const ACCENT = "#4c9aff";
const UP = "#2ecc8f";
const DOWN = "#ff5c5c";

const fmt = (n, d = 2) => {
  if (n === null || n === undefined || !isFinite(n)) return "—";
  return n.toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 });
};
const fmtUSD = (n) => (n === null || n === undefined || !isFinite(n) ? "—" : `$${fmt(n)}`);
const fmtCompact = (n) => {
  if (n === null || n === undefined || !isFinite(n)) return "—";
  const a = Math.abs(n);
  if (a >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `$${(n / 1e3).toFixed(2)}K`;
  return `$${fmt(n)}`;
};
const symOf = (symbol) => symbol.split("/")[0];
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Price formatting that keeps significant digits for micro-priced coins (SHIB etc).
const fmtPrice = (n) => {
  if (n === null || n === undefined || !isFinite(n)) return "—";
  if (n !== 0 && Math.abs(n) < 0.001) return n.toLocaleString("en-US", { maximumSignificantDigits: 6 });
  if (n >= 1000) return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (n >= 1) return n.toLocaleString("en-US", { maximumFractionDigits: 4 });
  return n.toLocaleString("en-US", { maximumSignificantDigits: 6 });
};

// ── API helper with timeout ─────────────────────────────────────────
async function api(path, opts = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeout || 20000);
  try {
    const res = await fetch(path, {
      signal: ctrl.signal,
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    if (!res.ok) throw new Error(`${path} → ${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

// ── Shared state ────────────────────────────────────────────────────
const state = {
  watchlist: [],      // {symbol, price, change, rsi, volume, spark}
  portfolio: null,    // full /api/portfolio payload
  tfDays: 7,          // selected "Total assets" range (days of history)
  tableFilter: "",
  portfolioTab: "coin",
  btSymbol: "BTC/USDT", // selected backtest symbol
  sigSymbol: "BTC/USDT", // selected signals-chart symbol
  tableInterval: "1h",   // Assets table timeframe
};

// ── View switching (sidebar) ────────────────────────────────────────
const VIEWS = ["dashboard", "assets", "portfolio", "bots", "signals"];

function switchView(name) {
  if (!VIEWS.includes(name)) name = "dashboard";
  document.querySelectorAll(".nav-item").forEach((a) =>
    a.classList.toggle("active", a.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) =>
    v.classList.toggle("active", v.id === `view-${name}`));
  // Charts initialised inside a hidden view have zero size — resize on show.
  requestAnimationFrame(() => {
    [portValueChart, totalChart].forEach((c) => c && c.resize());
  });
  if (name === "assets") loadWatchlist();
  if (name === "signals") {
    loadSignals();
    if (sigChart) sigChart.resize();
    if (pendingSigCandles) { renderSigChart(pendingSigCandles); pendingSigCandles = null; }
    else if (state.sigSymbol) loadSigChart();
  }
}

function bindSidebar() {
  document.querySelectorAll(".nav-item[data-view]").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      switchView(a.dataset.view);
    });
  });
}

// ── Top bar: ticker pills ───────────────────────────────────────────
function renderTickers() {
  const box = $("tickerPills");
  if (!box) return;
  const pills = ["BTC/USDT", "ETH/USDT"]
    .map((sym) => state.watchlist.find((w) => w.symbol === sym))
    .filter(Boolean)
    .map((w) => {
      const up = w.change >= 0;
      return `<button class="ticker-pill" title="Open Assets">
        <span class="tp-icon">${symOf(w.symbol).slice(0, 1)}</span>
        <span class="tp-sym">${symOf(w.symbol)}</span>
        <span class="tp-price">${fmtUSD(w.price)}</span>
        <span class="tp-chg" style="color:${up ? UP : DOWN}">${up ? "+" : ""}${w.change}%</span>
      </button>`;
    })
    .join("");
  box.innerHTML = pills || '<span class="muted">Loading…</span>';
}

// ── Asset cards (top 4 holdings) ────────────────────────────────────
function renderAssetCards() {
  const box = $("assetCards");
  if (!box || !state.portfolio) return;
  const items = state.portfolio.items.slice(0, 4);
  box.innerHTML = items
    .map((it) => {
      const w = state.watchlist.find((x) => x.symbol === it.symbol);
      const chg = w ? w.change : null;
      const chgCls = chg === null ? "" : chg >= 0 ? "up" : "down";
      const chgTxt = chg === null ? "—" : `${chg >= 0 ? "+" : ""}${chg}%`;
      return `<div class="asset-card">
        <div class="asset-head">
          <div class="asset-icon">${it.name.slice(0, 1)}</div>
          <div>
            <div class="asset-name">${it.name}</div>
            <div class="asset-sub">${it.symbol.replace("/", " ")}</div>
          </div>
        </div>
        <div class="asset-value">${fmtUSD(it.value)}</div>
        <div class="asset-foot">
          <span>${fmt(it.amount, 4)} ${it.name}</span>
          <span class="${chgCls}">${chgTxt}</span>
        </div>
      </div>`;
    })
    .join("");
}

// ── Portfolio value (monthly bars) ──────────────────────────────────
let portValueChart = null;
function renderPortValue() {
  const el = $("portValueChart");
  if (!el || !state.portfolio) return;
  if (!portValueChart) portValueChart = echarts.init(el);
  const hist = state.portfolio.history || [];
  if (!hist.length) {
    portValueChart.setOption({ title: { text: "Not enough history", textStyle: { color: "#8a94a6", fontSize: 13 } } }, true);
    return;
  }
  const byMonth = new Map();
  for (const h of hist) byMonth.set(h.date.slice(0, 7), h); // end-of-month value
  const months = [...byMonth].map(([m, h]) => ({
    label: new Date(Date.UTC(2000, parseInt(m.split("-")[1], 10) - 1, 1))
      .toLocaleString("en-US", { month: "short" }),
    value: h.value,
  }));
  if (months.length > 14) months.splice(0, months.length - 14);
  portValueChart.setOption({
    backgroundColor: "transparent",
    grid: { left: 8, right: 8, top: 16, bottom: 0, containLabel: true },
    tooltip: {
      trigger: "axis",
      backgroundColor: "#14161c", borderColor: "#24262e", textStyle: { color: "#e6e9f0" },
      formatter: (p) => `${p[0].axisValue}<br/><b>${fmtUSD(p[0].value)}</b>`,
    },
    xAxis: {
      type: "category", data: months.map((m) => m.label), axisLine: { lineStyle: { color: "#24262e" } },
      axisTick: { show: false }, axisLabel: { color: "#8a94a6", fontSize: 10 },
    },
    yAxis: {
      type: "value", splitLine: { lineStyle: { color: "rgba(255,255,255,.05)" } },
      axisLabel: { color: "#8a94a6", fontSize: 10, formatter: (v) => fmtCompact(v) },
    },
    series: [{
      type: "bar", data: months.map((m, i) => ({
        value: m.value,
        itemStyle: { color: i % 2 === 0 ? "#4c9aff" : "#2dd4bf", borderRadius: [4, 4, 0, 0] },
      })),
      barWidth: "55%",
    }],
  }, true);
}

// ── Total assets (line, range-selectable) ───────────────────────────
let totalChart = null;
function renderTotalAssets() {
  const el = $("totalAssetsChart");
  if (!el) return;
  if (!totalChart) totalChart = echarts.init(el);
  const hist = (state.portfolio && state.portfolio.history) || [];
  if (!hist.length) {
    totalChart.setOption({ title: { text: "Not enough history", textStyle: { color: "#8a94a6", fontSize: 13 } } }, true);
    return;
  }
  const latest = hist[hist.length - 1].value;
  $("totalValue").textContent = fmtUSD(latest);
  const sliced = hist.slice(-state.tfDays);
  totalChart.setOption({
    backgroundColor: "transparent",
    grid: { left: 8, right: 8, top: 16, bottom: 0, containLabel: true },
    tooltip: {
      trigger: "axis", backgroundColor: "#14161c", borderColor: "#24262e", textStyle: { color: "#e6e9f0" },
      formatter: (p) => `${p[0].axisValue}<br/><b>${fmtUSD(p[0].value)}</b>`,
    },
    xAxis: {
      type: "category",
      data: sliced.map((h) => h.date.slice(5)), // MM-DD
      axisLine: { lineStyle: { color: "#24262e" } }, axisTick: { show: false },
      axisLabel: { color: "#8a94a6", fontSize: 10 },
    },
    yAxis: {
      type: "value", splitLine: { lineStyle: { color: "rgba(255,255,255,.05)" } },
      axisLabel: { color: "#8a94a6", fontSize: 10, formatter: (v) => fmtCompact(v) },
    },
    series: [{
      type: "line", data: sliced.map((h) => h.value), smooth: true, symbol: "none",
      lineStyle: { color: ACCENT, width: 2 },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: "rgba(76,154,255,.35)" },
          { offset: 1, color: "rgba(76,154,255,0)" },
        ]),
      },
    }],
  }, true);
}

// ── Portfolio list (coin or exchange grouping) ──────────────────────
function portfolioRows(items) {
  return items
    .map((it) => {
      const color = (state.portfolio.distribution.find((d) => d.symbol === it.symbol) || {}).color || ACCENT;
      return `<div class="portfolio-row">
        <span class="asset-icon" style="background:${color}22;border-color:${color}66;color:${color}">${it.name.slice(0, 1)}</span>
        <span class="pf-name">${it.name} <small>${it.symbol.replace("/", " · ")}</small></span>
        <span class="pf-value">${fmtUSD(it.value)}</span>
      </div>`;
    })
    .join("");
}

function renderPortfolioLists() {
  if (!state.portfolio) return;
  const items = state.portfolio.items;
  const grouped = state.portfolioTab === "exchange"
    ? [{ label: state.portfolio.exchange_name || "Binance", items }]
    : items.map((it) => ({ label: it.name, items: [it] }));
  const html = grouped
    .map((g) => `<div class="pf-group"><div class="pf-group-label">${g.label} · ${fmtUSD(g.items.reduce((s, i) => s + i.value, 0))}</div>${portfolioRows(g.items)}</div>`)
    .join("");
  const list = $("portfolioList");
  const full = $("portfolioListFull");
  if (list) list.innerHTML = html || '<div class="muted">No holdings</div>';
  if (full) full.innerHTML = html || '<div class="muted">No holdings</div>';
}

function bindPortfolioTabs() {
  document.querySelectorAll(".mini-tab[data-tab]").forEach((b) => {
    b.addEventListener("click", () => {
      b.parentElement.querySelectorAll(".mini-tab").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      state.portfolioTab = b.dataset.tab;
      renderPortfolioLists();
    });
  });
}

// ── Balance + distribution bar ──────────────────────────────────────
function renderBalance() {
  if (!state.portfolio) return;
  const p = state.portfolio;
  const sub = p.mode === "exchange" ? `Binance · ${p.exchange_name || "connected"}` : "Demo portfolio · prices from live market data";
  const dist = p.distribution || [];
  const barHtml = dist.map((d) => `<div class="dist-seg" style="width:${d.pct}%;background:${d.color}"></div>`).join("");
  const legendHtml = dist
    .map((d) => `<span><span class="dot" style="background:${d.color}"></span>${symOf(d.symbol)} ${d.pct}%</span>`)
    .join("");
  for (const [bv, bs, bar, legend] of [
    [$("balanceValue"), $("balanceSub"), $("distBar"), $("distLegend")],
    [$("balanceValue2"), $("balanceSub2"), $("distBar2"), $("distLegend2")],
  ]) {
    if (!bv) continue;
    bv.textContent = `${fmtUSD(p.total_usd)} USD`;
    bs.textContent = sub;
    bar.innerHTML = barHtml;
    legend.innerHTML = legendHtml;
  }
}

// ── All-crypto table (Assets view) ──────────────────────────────────
function sparkSVG(data, color) {
  if (!data || data.length < 2) return "";
  const min = Math.min(...data), max = Math.max(...data);
  const range = max - min || 1;
  const w = 96, h = 28;
  const pts = data.map((v, i) =>
    `${((i / (data.length - 1)) * w).toFixed(1)},${(h - 3 - ((v - min) / range) * (h - 6)).toFixed(1)}`
  ).join(" ");
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline fill="none" stroke="${color}" stroke-width="1.5" points="${pts}"/></svg>`;
}

function renderTable() {
  const body = $("cryptoTableBody");
  if (!body) return;
  const f = state.tableFilter.toLowerCase();
  const rows = state.watchlist
    .filter((w) => !f || w.symbol.toLowerCase().includes(f) || symOf(w.symbol).toLowerCase().includes(f))
    .slice(0, 100)
    .map((w) => {
      const up = w.change >= 0;
      const col = up ? UP : DOWN;
      return `<tr>
        <td><span class="asset-icon" style="background:var(--card2)">${w.symbol.slice(0, 1)}</span> <b>${symOf(w.symbol)}</b> <span class="muted">${w.symbol.split("/")[1]}</span></td>
        <td>${fmtUSD(w.price)}</td>
        <td style="color:${col}">${up ? "+" : ""}${w.change}%</td>
        <td class="muted">${w.rsi != null && isFinite(w.rsi) ? fmt(w.rsi, 1) : "—"}</td>
        <td class="muted">${fmtCompact(w.volume)}</td>
        <td>${sparkSVG(w.spark, col)}</td>
      </tr>`;
    })
    .join("");
  body.innerHTML = rows || `<tr><td colspan="6" class="muted">No data yet</td></tr>`;
}
function filterTable() {
  state.tableFilter = $("tableSearch").value;
  renderTable();
}
function focusSearch() {
  switchView("assets");
  const s = $("tableSearch");
  if (s) s.focus();
}

// ── Signals (Signals view) ──────────────────────────────────────────
let lastSignals = [];
let sigChart = null;
let pendingSigCandles = null;
let sigInterval = "1h";

function _pineScript(s) {
  if (!s.entry) return "";
  return `//@version=6
indicator("${s.symbol} ${s.direction}", overlay=true, max_lines_count=5)
entry  = input.float(${s.entry}, "Entry")
sl     = input.float(${s.stop_loss}, "Stop Loss")
tp     = input.float(${s.take_profit}, "Take Profit")
plot(entry, "Entry",  color.new(color.yellow, 0), 2, plot.style_linebr)
plot(sl,    "SL",     color.new(color.red, 0),    1, plot.style_linebr)
plot(tp,    "TP",     color.new(color.green, 0),  1, plot.style_linebr)
label.new(bar_index, entry, "ENTRY " + str.tostring(entry), color=color.yellow, textcolor=color.black, style=label.style_label_left, size=size.small)
label.new(bar_index, sl,    "SL "    + str.tostring(sl),    color=color.red,    textcolor=color.white, style=label.style_label_left, size=size.small)
label.new(bar_index, tp,    "TP "    + str.tostring(tp),    color=color.green,  textcolor=color.white, style=label.style_label_left, size=size.small)`;
}

const _indName = (name) => String(name || "").split("@")[0].toUpperCase();

async function loadSignals() {
  try {
    const data = await api("/api/signals", { timeout: 60000 });
    renderSignals(data.signals || []);
  } catch (e) {
    const list = $("signalsList");
    if (list) list.innerHTML = `<div class="muted">Signals unavailable — ${e.message}</div>`;
  }
}

function renderSignals(signals) {
  const list = $("signalsList");
  if (!list) return;
  const meta = $("signalsMeta");
  lastSignals = signals;
  const active = signals
    .filter((s) => s.direction === "LONG" || s.direction === "SHORT")
    .sort((a, b) => Math.abs(b.confidence || 0) - Math.abs(a.confidence || 0));
  const holds = signals.filter((s) => s.direction !== "LONG" && s.direction !== "SHORT");
  if (meta) meta.textContent = `${signals.length} coins · ${active.length} active`;
  if (!signals.length) { list.innerHTML = '<div class="muted">No signal data yet.</div>'; return; }

  const html =
    (active.length
      ? `<div class="sig-sec-label">Active · ${active.length}</div>
         <div class="sig-cards">${active.map(sigCard).join("")}</div>`
      : `<div class="muted">No active signals right now — pick any asset on the left.</div>`) +
    (holds.length
      ? `<details class="sig-holds"${active.length ? "" : " open"}>
           <summary>On hold · ${holds.length}</summary>
           <div class="sig-hold-list">${holds.map(sigHoldRow).join("")}</div>
         </details>`
      : "");
  list.innerHTML = html;

  // Quick chips mirror the current active signals (fall back to majors).
  if (sigPicker) sigPicker.setQuick(active.length ? active.map((s) => s.symbol) : null);

  list.querySelectorAll("[data-symbol]").forEach((el) => {
    el.addEventListener("click", (e) => {
      if (e.target.closest("a,button")) return;
      const sym = el.dataset.symbol;
      if (sym) sigPicker.select(sym);
    });
  });
  list.querySelectorAll(".pine-copy").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const sig = lastSignals.find((s) => s.symbol === btn.dataset.symbol);
      if (!sig) return;
      navigator.clipboard.writeText(_pineScript(sig)).then(() => {
        btn.textContent = "✓ Copied";
        setTimeout(() => { btn.textContent = "Pine"; }, 1500);
      });
    });
  });
  highlightSigCard();
}

function sigCard(s) {
  const dir = s.direction;
  const long = dir === "LONG";
  const arrow = long ? "▲" : "▼";
  const rr = s.risk_reward != null && isFinite(s.risk_reward) ? `1:${s.risk_reward}` : "—";
  const tvUrl = "https://www.tradingview.com/chart/?symbol=BINANCE:" + s.symbol.replace("/", "");
  const w = state.watchlist.find((x) => x.symbol === s.symbol);
  const last = w && w.price != null ? w.price : null;
  const distPct = (lvl) => (last && lvl != null && last !== 0) ? (lvl - last) / last * 100 : null;
  const lvlHTML = (label, lvl, color) => {
    const d = distPct(lvl);
    let dist = "";
    if (d !== null && Math.abs(d) >= 0.05) {           // hide when price sits on the level
      const fav = long ? d : -d;                        // positive = toward the target
      const arrowDir = (label === "TP") === long ? "↑" : "↓";
      dist = ` <span class="lvl-dist ${fav > 0 ? "up" : "down"}">${arrowDir} ${Math.abs(d).toFixed(1)}%</span>`;
    }
    return `<span class="lvl"><span class="lvl-dot" style="background:${color}"></span>${label} ${fmtPrice(lvl)}${dist}</span>`;
  };
  const reasons = (s.reasons || []).filter((r) => r.signal && r.signal !== "HOLD").slice(0, 3)
    .map((r) => `<span class="sig-reason">${_indName(r.indicator)} ${r.signal} · ${esc(r.detail)}</span>`).join("");
  return `<div class="sig-card sig-${dir.toLowerCase()}" data-symbol="${esc(s.symbol)}">
    <div class="sig-card-head">
      <div class="sig-card-left">
        <span class="sig-coin">${symOf(s.symbol)}</span>
        <span class="signal-badge badge-${long ? "long" : "short"}">${arrow} ${dir}</span>
        <span class="sig-conf ${long ? "up" : "down"}">${s.confidence != null ? s.confidence : "—"}%</span>
      </div>
      <div class="sig-card-right">
        <a class="btn-signal" href="${esc(tvUrl)}" target="_blank" rel="noopener">↗ TV</a>
        <button class="btn-signal pine-copy" data-symbol="${esc(s.symbol)}">Pine</button>
      </div>
    </div>
    <div class="sig-levels-row">
      ${lvlHTML("Entry", s.entry, "#fbbf24")}
      ${lvlHTML("SL", s.stop_loss, "#ef4444")}
      ${lvlHTML("TP", s.take_profit, "#22c55e")}
      <span class="lvl lvl-rr">R:R ${rr}</span>
    </div>
    ${_factorBar(s.factors)}
    ${_mathStats(s)}
    ${reasons ? `<div class="sig-reasons">${reasons}</div>` : ""}
  </div>`;
}

function sigHoldRow(s) {
  let factorCompact = "";
  if (s.factors) {
    const comp = s.factors.composite || 0;
    const cls = comp > 0.05 ? "pos" : comp < -0.05 ? "neg" : "neu";
    factorCompact = `<span class="factor-val ${cls}" style="font-size:11px;margin-left:4px;">Factor ${comp > 0 ? "+" : ""}${(comp * 100).toFixed(0)}%</span>`;
  }
  let regimeCompact = "";
  if (s.regime) {
    const rLabel = s.regime.replace("trending_", "T ").replace("ranging", "R");
    regimeCompact = `<span class="regime-badge regime-${s.regime}" style="font-size:10px;padding:1px 5px;">${rLabel}</span>`;
  }
  return `<div class="sig-hold-row" data-symbol="${esc(s.symbol)}">
    <span class="sig-coin-sm">${symOf(s.symbol)}</span>
    <span class="signal-badge badge-hold">◆ HOLD</span>
    <span class="lvl-sm">Entry ${fmtPrice(s.entry)}</span>
    <span class="lvl-sm lvl-sl-sm">SL ${fmtPrice(s.stop_loss)}</span>
    <span class="lvl-sm lvl-tp-sm">TP ${fmtPrice(s.take_profit)}</span>
    ${regimeCompact}
    ${factorCompact}
    <span class="sig-conf-sm">${s.confidence != null ? s.confidence : "—"}%</span>
  </div>`;
}

/* ── Factor score bar (from the legacy terminal) ── */
function _factorBar(factors) {
  if (!factors) return "";
  const names = ["trend", "momentum", "carry", "reversal", "value"];
  const labels = { trend: "Trend", momentum: "Mom", carry: "Carry", reversal: "Rev", value: "Value" };
  const items = names.map((k) => {
    const f = factors[k];
    if (!f) return "";
    const s = f.score;
    const cls = s > 0.05 ? "pos" : s < -0.05 ? "neg" : "neu";
    const pct = Math.min(Math.abs(s), 1) * 50;          // 0-50% of the meter
    const fillStyle = s > 0.05
      ? `left:50%;width:${pct}%`
      : s < -0.05
        ? `right:50%;width:${pct}%`
        : "display:none";
    return `<span class="factor-item">
      <span class="factor-label">${labels[k]}</span>
      <span class="factor-meter">
        <span class="factor-meter-center"></span>
        <span class="factor-meter-fill ${s > 0.05 ? "bull" : "bear"}" style="${fillStyle}"></span>
      </span>
      <span class="factor-val ${cls}">${s > 0 ? "+" : ""}${(s * 100).toFixed(0)}%</span>
    </span>`;
  }).filter(Boolean).join("");
  const comp = factors.composite;
  const compCls = comp > 0.05 ? "pos" : comp < -0.05 ? "neg" : "neu";
  const compLabel = comp > 0.15 ? "BULLISH" : comp < -0.15 ? "BEARISH" : "NEUTRAL";
  return `<div class="factor-bar">
    ${items}
    <span class="factor-composite">
      <span class="factor-composite-label">Composite</span>
      <span class="factor-val ${compCls}">${comp > 0 ? "+" : ""}${(comp * 100).toFixed(0)}%</span>
      <span class="factor-val ${compCls}">${compLabel}</span>
    </span>
  </div>`;
}

/* ── Math engine stats (regime, kelly, EV, posterior) ── */
function _mathStats(s) {
  const parts = [];
  if (s.regime) {
    const rLabel = s.regime.replace("trending_", "Trend ").replace("ranging", "Range");
    parts.push(`<span class="math-stat"><span class="math-stat-label">Regime</span><span class="regime-badge regime-${s.regime}">${rLabel}</span></span>`);
  }
  if (s.adx != null) {
    parts.push(`<span class="math-stat"><span class="math-stat-label">ADX</span><span class="math-stat-val">${s.adx}</span></span>`);
  }
  if (s.kelly_fraction != null && s.kelly_fraction > 0) {
    parts.push(`<span class="math-stat"><span class="math-stat-label">Kelly</span><span class="math-stat-val">${(s.kelly_fraction * 100).toFixed(1)}%</span></span>`);
  }
  if (s.ev_per_unit != null) {
    const evCls = s.ev_per_unit > 0 ? "pos" : s.ev_per_unit < 0 ? "neg" : "neu";
    parts.push(`<span class="math-stat"><span class="math-stat-label">EV</span><span class="math-stat-val ${evCls}">${s.ev_per_unit > 0 ? "+" : ""}${(s.ev_per_unit * 100).toFixed(1)}%</span></span>`);
  }
  if (s.posterior_bull != null) {
    parts.push(`<span class="math-stat"><span class="math-stat-label">P(bull)</span><span class="math-stat-val">${(s.posterior_bull * 100).toFixed(1)}%</span></span>`);
  }
  if (s.confluence != null) {
    parts.push(`<span class="math-stat"><span class="math-stat-label">Conf.</span><span class="math-stat-val">${(s.confluence * 100).toFixed(0)}%</span></span>`);
  }
  return parts.length ? `<div class="math-stats">${parts.join("")}</div>` : "";
}

function highlightSigCard() {
  document.querySelectorAll("#signalsList [data-symbol]").forEach((el) =>
    el.classList.toggle("selected", el.dataset.symbol === state.sigSymbol));
}

function bindSigTfPills() {
  document.querySelectorAll("#sigTfPills .tf-pill").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll("#sigTfPills .tf-pill").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      sigInterval = b.dataset.interval;
      loadSigChart();
    });
  });
}

/* ── Signals chart with Entry / SL / TP overlay ── */
async function loadSigChart() {
  const el = $("sigChart");
  if (!el) return;
  try {
    const qs = new URLSearchParams({ symbol: state.sigSymbol, interval: sigInterval, limit: "400" });
    const d = await api("/api/candles?" + qs);
    if (el.clientWidth === 0) { pendingSigCandles = d; return; }  // view hidden — render on show
    renderSigChart(d);
  } catch (e) {
    const legend = $("sigLevelLegend");
    if (legend) legend.textContent = `Chart unavailable — ${e.message}`;
  }
}

function renderSigChart(d) {
  if (!sigChart) sigChart = echarts.init($("sigChart"));
  const times = d.times;
  const up = "#22c55e", down = "#ef4444", blue = "#3d7bff", lime = "#9fe870";
  const muted = "#8a93ad", subtle = "rgba(255,255,255,0.08)";
  const kData = times.map((_, i) => [d.open[i], d.close[i], d.low[i], d.high[i]]);
  const volData = times.map((_, i) => ({
    value: d.volume[i],
    itemStyle: { color: d.close[i] >= d.open[i] ? up : down, opacity: 0.55 },
  }));

  const catAxis = (gridIndex, showLabel) => ({
    type: "category", data: times, gridIndex, boundaryGap: true,
    axisLine: { lineStyle: { color: subtle } },
    axisLabel: { show: showLabel, color: muted, fontSize: 10, formatter: (v) => (v ? v.slice(11, 16) : "") },
    axisTick: { show: false }, splitLine: { show: false },
  });
  const valAxis = (gridIndex, extra) => ({
    type: "value", gridIndex, scale: true,
    axisLine: { show: false }, axisLabel: { color: muted, fontSize: 10 },
    splitLine: { lineStyle: { color: subtle } }, ...extra,
  });
  const line = (name, data, color, gridIndex, extra) => ({
    name, type: "line", xAxisIndex: gridIndex, yAxisIndex: gridIndex, data,
    smooth: true, showSymbol: false, lineStyle: { width: 1, color }, ...extra,
  });

  const kSeries = {
    name: "K", type: "candlestick", data: kData,
    itemStyle: { color: up, color0: down, borderColor: up, borderColor0: down },
  };

  // Overlay the signal's Entry / SL / TP levels on the price pane.
  const sig = lastSignals.find((s) => s.symbol === d.symbol);
  if (sig && sig.direction !== "HOLD" && sig.entry) {
    kSeries.markLine = {
      silent: true, symbol: "none",
      label: { show: true, fontSize: 10, color: "#f8fafc", position: "insideEndTop" },
      data: [
        { yAxis: sig.entry, name: "Entry", lineStyle: { color: "#fbbf24", type: "dashed", width: 2 },
          label: { formatter: "▲ Entry " + fmtPrice(sig.entry), color: "#fbbf24" } },
        { yAxis: sig.stop_loss, name: "SL", lineStyle: { color: "#ef4444", type: "solid", width: 1.5 },
          label: { formatter: "▼ SL " + fmtPrice(sig.stop_loss), color: "#ef4444" } },
        { yAxis: sig.take_profit, name: "TP", lineStyle: { color: "#22c55e", type: "solid", width: 1.5 },
          label: { formatter: "▲ TP " + fmtPrice(sig.take_profit), color: "#22c55e" } },
      ],
    };
  }

  const option = {
    animation: false,
    backgroundColor: "transparent",
    textStyle: { color: muted },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross", link: [{ xAxisIndex: "all" }], label: { backgroundColor: "#1e293b" } },
      backgroundColor: "#111c30", borderColor: subtle,
      textStyle: { color: "#f8fafc", fontSize: 11 },
      formatter: (params) => {
        const p = params[0];
        if (!p) return "";
        const i = p.dataIndex;
        const t = (times[i] || "").replace("T", " ").replace("Z", "");
        let out = `<b>${t}</b>`;
        if (p.seriesType === "candlestick") {
          out += `<br/>O ${fmtPrice(d.open[i])}  H ${fmtPrice(d.high[i])}`;
          out += `<br/>L ${fmtPrice(d.low[i])}  C ${fmtPrice(d.close[i])}`;
          const prev = d.close[i - 1];
          if (prev != null && prev !== 0) out += `  (${(((d.close[i] - prev) / prev) * 100).toFixed(2)}%)`;
          out += `<br/>Vol ${fmtCompact(d.volume[i])}`;
        } else {
          params.forEach((q) => {
            let v = q.value;
            if (v && typeof v === "object") v = v.value;
            out += `<br/>${q.marker}${q.seriesName}: ${v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(2)}`;
          });
        }
        return out;
      },
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 10, right: 18, top: 12, height: "46%", containLabel: true },
      { left: 10, right: 18, top: "57%", height: "11%", containLabel: true },
      { left: 10, right: 18, top: "71%", height: "11%", containLabel: true },
      { left: 10, right: 18, top: "85%", height: "12%", containLabel: true },
    ],
    xAxis: [catAxis(0, false), catAxis(1, false), catAxis(2, false), catAxis(3, true)],
    yAxis: [valAxis(0), valAxis(1, { min: 0 }), valAxis(2, { min: 0, max: 100, splitNumber: 4 }), valAxis(3)],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1, 2, 3], start: 70, end: 100 },
      { type: "slider", xAxisIndex: [0, 1, 2, 3], start: 70, end: 100, bottom: 2, height: 16 },
    ],
    series: [
      kSeries,
      line("SMA 20", d.sma20, blue, 0),
      line("SMA 50", d.sma50, lime, 0),
      line("BB upper", d.bb_up, muted, 0, { lineStyle: { type: "dashed", width: 1, opacity: 0.6 } }),
      line("BB mid", d.bb_mid, muted, 0, { lineStyle: { type: "dotted", width: 1, opacity: 0.6 } }),
      line("BB lower", d.bb_low, muted, 0, { lineStyle: { type: "dashed", width: 1, opacity: 0.6 } }),
      { name: "Vol", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: volData },
      line("RSI", d.rsi, lime, 2, {
        markLine: {
          silent: true, symbol: "none",
          lineStyle: { type: "dashed", color: down, opacity: 0.6 }, label: { show: false },
          data: [{ yAxis: 70 }, { yAxis: 30 }],
        },
      }),
      { name: "MACD hist", type: "bar", xAxisIndex: 3, yAxisIndex: 3, data: d.macd_hist, itemStyle: { opacity: 0.6 } },
      line("MACD", d.macd, blue, 3),
      line("Signal", d.macd_signal, up, 3),
    ],
  };
  sigChart.setOption(option, true);

  const t = $("sigChartTitle");
  if (t) t.textContent = `${symOf(d.symbol)} · ${d.interval}` + (sig && sig.direction !== "HOLD" ? " — signal levels" : "");
  const legend = $("sigLevelLegend");
  if (legend) {
    legend.innerHTML = sig && sig.direction !== "HOLD" && sig.entry
      ? `<b style="color:#fbbf24">Entry ${fmtPrice(sig.entry)}</b> · <b style="color:#ef4444">SL ${fmtPrice(sig.stop_loss)}</b> · <b style="color:#22c55e">TP ${fmtPrice(sig.take_profit)}</b> · click a signal card or search to switch asset`
      : "No active signal for this asset — levels appear here when one exists.";
  }
}

// ── Searchable asset picker (shared component) ──────────────────────
const QUICK_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "DOGE/USDT"];
let botPicker = null;  // backtest panel
let sigPicker = null;  // signals panel

function createAssetPicker(cfg) {
  // cfg: { inputId, listId, quickId, onSelect, initial }
  const inp = $(cfg.inputId);
  if (!inp) return null;
  const st = {
    query: "",
    active: 0,
    rows: [],   // rendered rows matching the current query
    value: cfg.initial || QUICK_SYMBOLS[0],
    quick: QUICK_SYMBOLS,
  };

  function render() {
    const list = $(cfg.listId);
    if (!list) return;
    const q = st.query.trim().toLowerCase();
    const matches = state.watchlist.filter((w) => {
      if (!q) return true;
      return symOf(w.symbol).toLowerCase().includes(q) || w.symbol.toLowerCase().includes(q);
    });
    const shown = matches.slice(0, 50);
    st.rows = shown;
    if (!shown.length) {
      list.innerHTML = '<div class="ap-empty">No matching assets — crypto pairs only</div>';
      list.hidden = false;
      return;
    }
    list.innerHTML = shown.map((w, i) => {
      const chg = w.change;
      const chgCls = chg == null ? "" : chg >= 0 ? "up" : "down";
      const chgTxt = chg == null ? "—" : `${chg >= 0 ? "+" : ""}${chg}%`;
      return `<div class="ap-row" data-i="${i}">
        <span class="ap-sym">${symOf(w.symbol)}</span>
        <span class="ap-pair">${w.symbol}</span>
        <span class="ap-price">${fmtUSD(w.price)}</span>
        <span class="ap-chg ${chgCls}">${chgTxt}</span>
      </div>`;
    }).join("");
    active(0);
    list.hidden = false;
  }

  function active(i) {
    const rows = Array.from(document.querySelectorAll(`#${cfg.listId} .ap-row`));
    st.active = Math.max(0, Math.min(i, rows.length - 1));
    rows.forEach((r, j) => r.classList.toggle("sel", j === st.active));
    rows[st.active]?.scrollIntoView({ block: "nearest" });
  }

  function close() {
    const list = $(cfg.listId);
    if (list) list.hidden = true;
  }

  function renderQuick() {
    const box = $(cfg.quickId);
    if (!box) return;
    box.innerHTML = st.quick.map((sym) =>
      `<button class="ap-chip${st.value === sym ? " active" : ""}" data-sym="${sym}">${symOf(sym)}</button>`
    ).join("");
  }

  function select(symbol) {
    st.value = symbol;
    st.query = "";
    inp.value = symOf(symbol);
    close();
    renderQuick();
    if (cfg.onSelect) cfg.onSelect(symbol);
  }

  function setQuick(syms) {
    st.quick = syms && syms.length ? syms.slice(0, 5) : QUICK_SYMBOLS;
    renderQuick();
  }

  function refresh() {
    renderQuick();
    const list = $(cfg.listId);
    if (list && !list.hidden) render();
  }

  inp.addEventListener("focus", (e) => { e.target.select(); render(); });
  inp.addEventListener("input", () => { st.query = inp.value; render(); });
  inp.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); active(st.active + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active(st.active - 1); }
    else if (e.key === "Enter") {
      e.preventDefault();
      const row = st.rows[st.active];
      if (row) select(row.symbol);
    }
    else if (e.key === "Escape") close();
  });
  inp.addEventListener("blur", () => setTimeout(close, 150));
  document.addEventListener("click", (e) => {
    const row = e.target.closest(`#${cfg.listId} .ap-row`);
    if (row) { select(st.rows[+row.dataset.i].symbol); return; }
    const chip = e.target.closest(`#${cfg.quickId} .ap-chip`);
    if (chip) { select(chip.dataset.sym); return; }
    if (!e.target.closest(".asset-picker")) close();
  });

  renderQuick();
  select(st.value);
  return { select, refresh, setQuick, get value() { return st.value; } };
}

async function runBacktest() {
  const btn = $("btnRunBacktest");
  const box = $("btResults");
  if (!btn || !box) return;
  btn.disabled = true;
  btn.textContent = "Running…";
  box.innerHTML = '<div class="muted">Backtesting…</div>';
  try {
    const symbol = state.btSymbol;
    const interval = $("btInterval").value;
    const r = await api(`/api/backtest?symbol=${encodeURIComponent(symbol)}&interval=${interval}&limit=9000`, { timeout: 60000 });
    box.innerHTML = btMetricsHtml(r) + btHealthHtml(r) + btTradesHtml(r);
  } catch (e) {
    box.innerHTML = `<div class="muted">Backtest failed — ${e.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Run backtest";
  }
}

function btMetricsHtml(r) {
  const pct = (v, digits = 2) => (v == null || !isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`);
  const ci = Array.isArray(r.win_rate_ci) ? `(${(r.win_rate_ci[0] * 100).toFixed(1)}–${(r.win_rate_ci[1] * 100).toFixed(1)}%)` : "";
  const wr = r.win_rate != null ? `${(r.win_rate * 100).toFixed(1)}% ${ci}` : "—";
  const pf = r.profit_factor != null ? r.profit_factor.toFixed(2) : "—";
  const mc = r.monte_carlo || {};
  const wf = r.walk_forward || {};
  const exits = r.exit_analysis || {};
  const metric = (label, value, cls = "") =>
    `<div class="bt-metric"><div class="bm-label">${label}</div><div class="bm-value ${cls}">${value}</div></div>`;
  const returnCls = r.total_return_pct > 0 ? "up" : r.total_return_pct < 0 ? "down" : "";
  const wfCls = (wf.mc_lean_pontiff_check || "").includes("WARNING") ? "down" : "up";
  return `<div class="bt-metrics">
    ${metric("Total return", pct(r.total_return_pct), returnCls)}
    ${metric("Win rate", wr)}
    ${metric("Trades", r.total_trades ?? "—")}
    ${metric("Profit factor", pf)}
    ${metric("Sharpe", r.sharpe_ratio != null ? r.sharpe_ratio.toFixed(2) : "—")}
    ${metric("Max drawdown", pct(-(r.max_drawdown_pct ?? 0)))}
    ${metric("Annualised", pct(r.annualised_return_pct), returnCls)}
    ${metric("MC P(profit)", mc.prob_profit != null ? `${(mc.prob_profit * 100).toFixed(0)}%` : "—", mc.prob_profit > 0.7 ? "up" : "down")}
  </div>
  <div class="bt-wf"><b>Walk-forward:</b> ${wf.mc_lean_pontiff_check || "n/a"} — OOS WR ${wf.oos_win_rate != null ? (wf.oos_win_rate * 100).toFixed(1) + "%" : "n/a"} (decay ${wf.wr_decay_pct ?? "n/a"}%) · ${r.total_bars} bars · ${r.symbol || ""} ${r.interval || ""}</div>
  <div class="bt-exits">Exits: ${Object.entries(exits).map(([k, v]) => `${k} ${v.count} (${v.pct}%)`).join(" · ") || "—"}</div>
  <div class="muted" style="margin-top:8px">Strategy v2 mean-reversion, net of fees + slippage. Not financial advice.</div>`;
}

/* ── Health check: which stats were used, which are weak ── */
function btHealthHtml(r) {
  const rows = [];
  const pct = (v, d = 1) => (v == null || !isFinite(v) ? null : (v * 100).toFixed(d) + "%");
  const g3 = (v, pass, check) => (v >= pass ? "pass" : v >= check ? "check" : "fail");   // higher is better
  const g3l = (v, pass, check) => (v <= pass ? "pass" : v <= check ? "check" : "fail");  // lower is better
  const add = (label, value, target, grade) => { if (value !== null && value !== undefined) rows.push({ label, value, target, grade }); };

  // Targets match the project's strategy gates (DIRECTIVE-001: WR ≥ 50% AND PF ≥ 1.1
  // to qualify; long-term goal is the 60–70% WR band).
  if (r.win_rate != null) add("Win rate", pct(r.win_rate), "≥ 60%", g3(r.win_rate, 0.60, 0.50));
  if (r.profit_factor != null && isFinite(r.profit_factor)) add("Profit factor", r.profit_factor.toFixed(2), "≥ 1.1", g3(r.profit_factor, 1.1, 1.0));
  if (r.sharpe_ratio != null && isFinite(r.sharpe_ratio)) add("Sharpe", r.sharpe_ratio.toFixed(2), "≥ 1.0", g3(r.sharpe_ratio, 1.0, 0.0));
  if (r.win_rate != null && r.avg_win != null && r.avg_loss != null) {
    const exp = r.win_rate * r.avg_win - (1 - r.win_rate) * r.avg_loss;  // expectancy per trade
    add("Expectancy/trade", `${exp > 0 ? "+" : ""}$${fmt(Math.abs(exp), 2)}`, "> $0", exp > 0 ? "pass" : "fail");
  }
  if (r.max_drawdown_pct != null) add("Max drawdown", "-" + Math.abs(r.max_drawdown_pct).toFixed(1) + "%", "≤ 10%", g3l(Math.abs(r.max_drawdown_pct), 10, 20));
  if (r.annualised_return_pct != null && isFinite(r.annualised_return_pct)) {
    add("Annualised", `${r.annualised_return_pct > 0 ? "+" : ""}${r.annualised_return_pct.toFixed(1)}%`, "> 0%",
      r.annualised_return_pct > 0 ? "pass" : r.annualised_return_pct > -10 ? "check" : "fail");
  }
  const mc = r.monte_carlo || {};
  if (mc.prob_profit != null) add("MC P(profit)", (mc.prob_profit * 100).toFixed(0) + "%", "≥ 70%", g3(mc.prob_profit, 0.7, 0.5));
  const wf = r.walk_forward || {};
  if (wf.wr_decay_pct != null) add("WF OOS decay", wf.wr_decay_pct + "pp", "≤ 10pp", g3l(wf.wr_decay_pct, 10, 26));
  if (r.total_trades != null) add("Sample", r.total_trades + " trades", "≥ 30", g3(r.total_trades, 30, 10));

  const fails = rows.filter((x) => x.grade === "fail").length;
  const checks = rows.filter((x) => x.grade === "check").length;
  let verdict, cls;
  if (r.total_trades != null && r.total_trades < 10) { verdict = "Too few trades — results unreliable"; cls = "weak"; }
  else if (fails === 0 && checks === 0) { verdict = "Strong — keep these parameters"; cls = "strong"; }
  else if (fails <= 1) { verdict = "Borderline — review the flagged stats"; cls = "border"; }
  else { verdict = "Weak — revise parameters (red stats)"; cls = "weak"; }

  const legend = "Stats used: WR ≥ 60% · PF ≥ 1.1 · Sharpe ≥ 1.0 · DD ≤ 10% · MC ≥ 70% · WF decay ≤ 10pp · ≥ 30 trades";
  return `<div class="bt-health ${cls}">
    <div class="bt-health-head">
      <span class="bt-verdict">● ${verdict}</span>
      <span class="muted">${legend}</span>
    </div>
    <div class="bt-health-grid">
      ${rows.map((x) => `<div class="bh-item ${x.grade}">
        <span class="bh-name">${x.label}</span>
        <span class="bh-value">${x.value}</span>
        <span class="bh-target">${x.target}</span>
        <span class="bh-chip ${x.grade}">${x.grade.toUpperCase()}</span>
      </div>`).join("")}
    </div>
  </div>`;
}

/* ── Trade-by-trade list for a finished backtest ── */
const fmtDT = (iso) => {
  if (!iso) return "—";
  const s = String(iso).replace("T", " ").replace("Z", "").slice(0, 16);
  return s.startsWith("1970") ? "—" : s;
};
const EXIT_LABEL = { tp: "TP", sl: "SL", timeout: "Timeout", signal_flip: "Flip" };

function btTradesHtml(r) {
  const list = Array.isArray(r.trade_list) ? [...r.trade_list].reverse() : [];
  if (!list.length) {
    return `<details class="bt-trades"><summary>Trades · 0</summary>
      <div class="muted" style="padding:8px 0">No closed trades in this window.</div></details>`;
  }
  const wins = list.filter((t) => t.pnl > 0).length;
  const net = list.reduce((a, t) => a + (t.pnl || 0), 0);
  const pct = (v) => (v == null || !isFinite(v) ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
  const usd = (v) => (v == null || !isFinite(v) ? "—" : `${v > 0 ? "+" : ""}$${fmt(v, 2)}`);
  const rows = list.map((t, i) => {
    const dir = (t.direction || "LONG").toLowerCase();
    const pnl = t.pnl;
    const pnlCls = pnl > 0 ? "pos" : pnl < 0 ? "neg" : "";
    const exitLbl = EXIT_LABEL[t.exit_reason] || t.exit_reason || "—";
    const exitCls = t.exit_reason === "tp" ? "ok" : t.exit_reason === "sl" ? "bad" : "";
    return `<tr class="${pnlCls}">
      <td class="td-idx">${list.length - i}</td>
      <td><span class="sig-dir ${dir}">${t.direction === "SHORT" ? "SHORT" : "LONG"}</span></td>
      <td>${fmtDT(t.entry_time)}</td>
      <td>${fmtDT(t.exit_time)}</td>
      <td class="td-px">${fmtPrice(t.entry)}</td>
      <td class="td-px">${fmtPrice(t.exit)}</td>
      <td class="td-pnl ${pnlCls}">${usd(pnl)}</td>
      <td class="td-pnl ${pnlCls}">${pct(t.pnl_pct)}</td>
      <td class="td-bars">${t.hold_bars ?? "—"}</td>
      <td><span class="td-exit ${exitCls}">${exitLbl}</span></td>
    </tr>`;
  }).join("");
  return `<details class="bt-trades" open>
    <summary>Trades · ${list.length} <span class="muted">(${wins} W / ${list.length - wins} L · net ${usd(net)})</span></summary>
    <div class="bt-trades-scroll">
      <table class="bt-trade-table">
        <thead><tr>
          <th>#</th><th>Dir</th><th>Entry time</th><th>Exit time</th>
          <th class="num">Entry</th><th class="num">Exit</th>
          <th class="num">P&amp;L</th><th class="num">P&amp;L %</th>
          <th class="num">Bars</th><th>Exit</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  </details>`;
}

// ── Connect-exchange modal ──────────────────────────────────────────
const IPS = "192.26.9.10 193.26.9.14 194.26.9.18";

function openConnect() { $("connectModal").style.display = "grid"; }
function closeConnect() { $("connectModal").style.display = "none"; }
function copyIPs() {
  if (navigator.clipboard) navigator.clipboard.writeText(IPS);
}
async function submitConnect() {
  const btn = $("btnConnectSubmit");
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = "Connecting…";
  try {
    const res = await api("/api/connect-exchange", {
      method: "POST",
      body: JSON.stringify({
        name: $("keyName").value || "My Binance",
        api_key: $("keyApi").value.trim(),
        api_secret: $("keySecret").value.trim(),
      }),
    });
    if (res.ok) {
      await loadPortfolio();
      closeConnect();
      $("btnConnect").textContent = "Connected ✓";
      $("btnConnect").classList.add("connected");
    } else {
      const w = document.querySelector(".modal .warn");
      w.textContent = res.error || "Connection failed";
      w.style.color = DOWN;
    }
  } catch (e) {
    const w = document.querySelector(".modal .warn");
    w.textContent = "Request failed — is the server running?";
    w.style.color = DOWN;
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

// ── Range pills ─────────────────────────────────────────────────────
function bindTfPills() {
  document.querySelectorAll(".tf-pill").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll(".tf-pill").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      state.tfDays = parseInt(b.dataset.range, 10) || 7;
      renderTotalAssets();
    });
  });
}

// ── Loaders ─────────────────────────────────────────────────────────
async function loadWatchlist() {
  try {
    const data = await api(`/api/watchlist?interval=${state.tableInterval}`);
    state.watchlist = data.symbols || [];
    renderTickers();
    renderTable();
    if (botPicker) botPicker.refresh();
    if (sigPicker) sigPicker.refresh();
    renderAssetCards();
    if (lastSignals.length) renderSignals(lastSignals); // distances depend on live price
  } catch (e) {
    console.warn("watchlist:", e);
  }
}

function bindTableTf() {
  document.querySelectorAll("#tableTfPills .tf-pill").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll("#tableTfPills .tf-pill").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      state.tableInterval = b.dataset.interval;
      loadWatchlist();
    });
  });
}

async function loadPortfolio() {
  try {
    state.portfolio = await api("/api/portfolio");
    renderAssetCards();
    renderPortValue();
    renderTotalAssets();
    renderPortfolioLists();
    renderBalance();
    if (state.portfolio.mode === "exchange") {
      $("btnConnect").textContent = "Connected ✓";
      $("btnConnect").classList.add("connected");
    }
  } catch (e) {
    console.warn("portfolio:", e);
  }
}

// ── Boot ────────────────────────────────────────────────────────────
window.addEventListener("resize", () => {
  if (portValueChart) portValueChart.resize();
  if (totalChart) totalChart.resize();
  if (sigChart) sigChart.resize();
});

bindSidebar();
bindTfPills();
bindPortfolioTabs();
bindSigTfPills();
bindTableTf();
botPicker = createAssetPicker({
  inputId: "apInput", listId: "apList", quickId: "apQuick", initial: "BTC/USDT",
  onSelect: (sym) => { state.btSymbol = sym; },
});
sigPicker = createAssetPicker({
  inputId: "sigInput", listId: "sigList", quickId: "sigQuick", initial: "BTC/USDT",
  onSelect: (sym) => { state.sigSymbol = sym; loadSigChart(); highlightSigCard(); },
});
loadPortfolio();
loadWatchlist();
loadSignals();
setInterval(loadPortfolio, 60000);
setInterval(loadWatchlist, 120000);
setInterval(loadSignals, 60000);
