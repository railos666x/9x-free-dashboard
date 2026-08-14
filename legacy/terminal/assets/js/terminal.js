/*
  cryptoautomation · terminal logic
  Polls the FastAPI JSON API (~5s) and renders:
    - watchlist rail (prices / % change / RSI)
    - 4-pane ECharts candlestick (price, volume, RSI, MACD)
    - news feed with sentiment badges + filters
    - stats row + Fear & Greed + health light
  Theme controls reuse AirX2's themes.js (imported below).
*/
import { initTheme, toggleMode, setAccent } from './themes.js';

/* ===== Config (mirrors config.py) ===== */
/* Mirrors config.py exactly (87 symbols, deduped, dead listings removed).
   Dead/nonexistent pairs (ICON, QLC, OMN, BLOB, BTCDOM) were removed — they
   could never load data and showed blank watchlist rows. */
const SYMBOLS = [
  "BTC/USDT","ETH/USDT","SOL/USDT","BNB/USDT","DOGE/USDT",
  "ADA/USDT","XRP/USDT","AVAX/USDT","DOT/USDT","NEAR/USDT",
  "ALGO/USDT","ATOM/USDT","EOS/USDT","TRX/USDT","LTC/USDT",
  "BCH/USDT","ETC/USDT","ICP/USDT","XLM/USDT","XMR/USDT",
  "FIL/USDT","HBAR/USDT","EGLD/USDT","THETA/USDT",
  "ARB/USDT","MATIC/USDT","OP/USDT",
  "AAVE/USDT","UNI/USDT","LINK/USDT","COMP/USDT","CRV/USDT",
  "SUSHI/USDT","SNX/USDT","YFI/USDT","1INCH/USDT","MKR/USDT",
  "BAND/USDT","KNC/USDT","ZRX/USDT","RUNE/USDT","LPT/USDT",
  "DYDX/USDT","TRB/USDT","OCEAN/USDT",
  "SHIB/USDT","FLOKI/USDT","PEPE/USDT","WIF/USDT","BONK/USDT",
  "SAND/USDT","MANA/USDT","AXS/USDT","ENJ/USDT","GALA/USDT",
  "ALICE/USDT","CHZ/USDT","BAT/USDT",
  "GRT/USDT","HNT/USDT","NEO/USDT","VET/USDT","IOTA/USDT",
  "STORJ/USDT","BLZ/USDT","CVC/USDT","CTSI/USDT","CELR/USDT",
  "KAVA/USDT","ONE/USDT","MASK/USDT",
  "DASH/USDT","KSM/USDT","SFP/USDT","COTI/USDT","CELO/USDT",
  "IOST/USDT","CKB/USDT",
  // ── Additional from watchlist ──
  "OG/USDT","BEL/USDT","OGN/USDT","SLP/USDT",
  "FIO/USDT","CREAM/USDT","MDT/USDT","IRIS/USDT","LUNA/USDT",
];
const INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"];
const POLL_MS = 5000;

const state = {
  symbol: SYMBOLS[0],
  interval: "15m",
  sentiment: "",
  reviewCoin: "",
  search: "",
  cat: "all",
  compact: false,
};

/* Category groupings for the sidebar tabs (mirrors config.py layout) */
const CATEGORIES = [
  { key: "all",  label: "All",   match: () => true },
  { key: "major", label: "Major", match: (s) => ["BTC/USDT","ETH/USDT","SOL/USDT","BNB/USDT","DOGE/USDT"].includes(s) },
  { key: "l1",   label: "Layer1", match: (s) => ["ADA/USDT","XRP/USDT","AVAX/USDT","DOT/USDT","NEAR/USDT","ALGO/USDT","ATOM/USDT","EOS/USDT","TRX/USDT","LTC/USDT","BCH/USDT","ETC/USDT","ICP/USDT","XLM/USDT","XMR/USDT","FIL/USDT","HBAR/USDT","EGLD/USDT","THETA/USDT"].includes(s) },
  { key: "l2",   label: "Layer2", match: (s) => ["ARB/USDT","MATIC/USDT","OP/USDT"].includes(s) },
  { key: "defi", label: "DeFi",  match: (s) => ["AAVE/USDT","UNI/USDT","LINK/USDT","COMP/USDT","CRV/USDT","SUSHI/USDT","SNX/USDT","YFI/USDT","1INCH/USDT","MKR/USDT","BAND/USDT","KNC/USDT","ZRX/USDT","RUNE/USDT","LPT/USDT","DYDX/USDT","TRB/USDT","OCEAN/USDT"].includes(s) },
  { key: "meme", label: "Meme",  match: (s) => ["SHIB/USDT","FLOKI/USDT","PEPE/USDT","WIF/USDT","BONK/USDT"].includes(s) },
  { key: "gaming", label: "Gaming", match: (s) => ["SAND/USDT","MANA/USDT","AXS/USDT","ENJ/USDT","GALA/USDT","ALICE/USDT","CHZ/USDT","BAT/USDT"].includes(s) },
  { key: "infra", label: "Infra", match: (s) => ["GRT/USDT","HNT/USDT","NEO/USDT","VET/USDT","IOTA/USDT","STORJ/USDT","BLZ/USDT","CVC/USDT","CTSI/USDT","CELR/USDT","KAVA/USDT","ONE/USDT","MASK/USDT"].includes(s) },
  { key: "other", label: "Other", match: (s) => !["BTC/USDT","ETH/USDT","SOL/USDT","BNB/USDT","DOGE/USDT","ADA/USDT","XRP/USDT","AVAX/USDT","DOT/USDT","NEAR/USDT","ALGO/USDT","ATOM/USDT","EOS/USDT","TRX/USDT","LTC/USDT","BCH/USDT","ETC/USDT","ICP/USDT","XLM/USDT","XMR/USDT","FIL/USDT","HBAR/USDT","EGLD/USDT","THETA/USDT","ARB/USDT","MATIC/USDT","OP/USDT","AAVE/USDT","UNI/USDT","LINK/USDT","COMP/USDT","CRV/USDT","SUSHI/USDT","SNX/USDT","YFI/USDT","1INCH/USDT","MKR/USDT","BAND/USDT","KNC/USDT","ZRX/USDT","RUNE/USDT","LPT/USDT","DYDX/USDT","TRB/USDT","OCEAN/USDT","SHIB/USDT","FLOKI/USDT","PEPE/USDT","WIF/USDT","BONK/USDT","SAND/USDT","MANA/USDT","AXS/USDT","ENJ/USDT","GALA/USDT","ALICE/USDT","CHZ/USDT","BAT/USDT","GRT/USDT","HNT/USDT","NEO/USDT","VET/USDT","IOTA/USDT","STORJ/USDT","BLZ/USDT","CVC/USDT","CTSI/USDT","CELR/USDT","KAVA/USDT","ONE/USDT","MASK/USDT"].includes(s) },
];

function symbolMatchesCat(s) {
  const cat = CATEGORIES.find((c) => c.key === state.cat) || CATEGORIES[0];
  return cat.match(s);
}
function symbolMatchesSearch(s) {
  if (!state.search) return true;
  return coinOf(s).toLowerCase().includes(state.search.toLowerCase());
}

let chart = null;
let lastSignals = {};  // symbol -> signal object (for chart overlay)
let lastCandles = null;

/* ===== Tiny helpers ===== */
const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const coinOf = (symbol) => symbol.split("/")[0];

function fmtPrice(v) {
  if (v == null || Number.isNaN(v)) return "—";
  if (v >= 1000) return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (v >= 1) return v.toFixed(2);
  return v.toFixed(6);
}
function fmtVol(v) {
  if (v == null || Number.isNaN(v)) return "—";
  if (v >= 1e9) return (v / 1e9).toFixed(2) + "B";
  if (v >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(2) + "K";
  return v.toFixed(0);
}
function timeAgo(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m";
  if (s < 86400) return Math.floor(s / 3600) + "h";
  return Math.floor(s / 86400) + "d";
}
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
async function fetchJSON(url, timeoutMs = 15000) {
  // Abort after timeoutMs so a hung endpoint can never leave a panel
  // stuck on "loading…" forever (seen with /api/signals, which used to
  // take minutes per refresh).
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    if (!res.ok) throw new Error(url + " -> " + res.status);
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

/* ===== Sidebar builders ===== */
function buildCatTabs() {
  const tabs = $("catTabs");
  tabs.innerHTML = CATEGORIES.map((c) =>
    `<button class="cat-tab${c.key === state.cat ? " active" : ""}" data-cat="${c.key}">
      ${c.label}<span class="cat-count">${SYMBOLS.filter(c.match).length}</span>
    </button>`).join("");
  tabs.addEventListener("click", (e) => {
    const btn = e.target.closest(".cat-tab");
    if (!btn) return;
    state.cat = btn.dataset.cat;
    tabs.querySelectorAll(".cat-tab").forEach((b) =>
      b.classList.toggle("active", b === btn));
    renderSymbolList();
  });
}

function renderSymbolList() {
  const list = $("symbolList");
  const shown = SYMBOLS.filter((s) => symbolMatchesCat(s) && symbolMatchesSearch(s));
  list.innerHTML = shown.map((s) =>
    `<button class="sym-row${s === state.symbol ? " active" : ""}" data-symbol="${esc(s)}">
      <span class="sym-name">${coinOf(s)}</span>
      <span class="sym-price">—</span>
      <span class="sym-chg">—</span>
      <canvas class="sym-spark" width="48" height="20"></canvas>
    </button>`).join("");
  if (!shown.length) {
    list.innerHTML = '<div class="text-muted" style="padding:12px 10px;font-size:0.8rem;">No coins match.</div>';
  }
}

function buildSymbolList() {
  renderSymbolList();
  const list = $("symbolList");
  list.addEventListener("click", (e) => {
    const btn = e.target.closest(".sym-row");
    if (!btn) return;
    state.symbol = btn.dataset.symbol;
    list.querySelectorAll(".sym-row").forEach((b) =>
      b.classList.toggle("active", b === btn));
    loadChart();
  });

  // Live search filter
  $("symbolSearch").addEventListener("input", (e) => {
    state.search = e.target.value.trim();
    renderSymbolList();
  });
}

function buildIntervalList() {
  const list = $("intervalList");
  list.innerHTML = INTERVALS.map((iv) =>
    `<button class="pill${iv === state.interval ? " active" : ""}" data-interval="${iv}">${iv}</button>`).join("");
  list.addEventListener("click", (e) => {
    const btn = e.target.closest(".pill");
    if (!btn) return;
    state.interval = btn.dataset.interval;
    list.querySelectorAll(".pill").forEach((b) =>
      b.classList.toggle("active", b === btn));
    loadChart();
  });
}

function buildReviewSelect() {
  const sel = $("reviewCoin");
  sel.innerHTML =
    `<option value="">All coins</option>` +
    SYMBOLS.map((s) => `<option value="${esc(s)}">${coinOf(s)}</option>`).join("");
  sel.addEventListener("change", () => {
    state.reviewCoin = sel.value;
    loadNews();
  });
}

/* ===== Theme wiring ===== */
function wireTheme() {
  $("modeBtn").addEventListener("click", () => {
    toggleMode();
    rerenderChart();
  });
  document.querySelectorAll(".accent-dot").forEach((btn) => {
    btn.addEventListener("click", () => {
      setAccent(btn.dataset.accent);
      rerenderChart();
    });
  });
  $("newsSentiment").addEventListener("change", (e) => {
    state.sentiment = e.target.value;
    loadNews();
  });
  $("compactBtn").addEventListener("click", () => {
    state.compact = !state.compact;
    document.body.classList.toggle("compact-mode", state.compact);
    $("compactBtn").innerHTML = state.compact
      ? '<i class="fa-solid fa-expand"></i> Normal'
      : '<i class="fa-solid fa-compress"></i> Compact';
  });
}

/* Keyboard shortcuts: 1-6 for intervals, arrows for symbols */
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  const map = { "1": "1m", "2": "5m", "3": "15m", "4": "1h", "5": "4h", "6": "1d" };
  if (map[e.key]) {
    state.interval = map[e.key];
    document.querySelectorAll("#intervalList .pill").forEach((b) =>
      b.classList.toggle("active", b.dataset.interval === state.interval));
    loadChart();
  }
  if (e.key === "ArrowUp" || e.key === "ArrowDown") {
    e.preventDefault();
    const rows = Array.from(document.querySelectorAll("#symbolList .sym-row"));
    if (!rows.length) return;
    const idx = rows.findIndex((r) => r.classList.contains("active"));
    const next = e.key === "ArrowUp" ? idx - 1 : idx + 1;
    if (next >= 0 && next < rows.length) {
      rows[next].click();
      rows[next].scrollIntoView({ block: "nearest" });
    }
  }
  if (e.key === "/") {
    e.preventDefault();
    $("symbolSearch").focus();
  }
});

/* ===== Data loading ===== */
async function loadAll() {
  loadChart().catch(() => {});
  loadWatchlist().catch(() => {});
  loadNews().catch(() => {});
  loadReview().catch(() => {});
  loadSignals().catch(() => {});
  loadMacro().catch(() => {});
  loadHealth();
  loadFearGreed().catch(() => {});
}

/* ===== Macro assets (gold + S&P 500) ===== */
let macroCharts = {};

async function loadMacro() {
  const r = await fetchJSON("/api/macro");
  r.symbols.forEach((s) => {
    const isGold = s.symbol.startsWith("XAU");
    const el = $(isGold ? "macroGold" : "macroSpx");
    const labelEl = $(isGold ? "macroGoldLabel" : "macroSpxLabel");
    if (!el) return;
    if (labelEl) {
      labelEl.textContent = `${s.name} · ${fmtPrice(s.price)} (${s.change > 0 ? "+" : ""}${s.change}%)`;
    }
    if (!macroCharts[s.symbol]) macroCharts[s.symbol] = echarts.init(el);
    macroCharts[s.symbol].setOption({
      backgroundColor: "transparent",
      grid: { left: 6, right: 6, top: 10, bottom: 4 },
      xAxis: { type: "category", show: false, data: s.times },
      yAxis: { type: "value", show: false, scale: true },
      tooltip: { trigger: "axis" },
      series: [{
        type: "line", data: s.close, showSymbol: false, smooth: true,
        lineStyle: { width: 1.5, color: isGold ? "#f0b90b" : "#4c9aff" },
        areaStyle: { opacity: 0.15 },
      }],
    });
  });
}

/* ===== AI market review ===== */
async function loadReview() {
  const r = await fetchJSON("/api/review?limit=40");

  const modeEl = $("reviewMode");
  modeEl.textContent = r.mode === "llm" ? "· " + (r.model || "LLM") : "· rules";
  const marketEl = $("reviewMarket");
  marketEl.textContent = "Market: " + (r.market || "—");
  marketEl.className =
    "review-market " + (r.market === "Bullish" ? "pos" : r.market === "Bearish" ? "neg" : "");

  const row = $("reviewRow");
  if (!r.coins || !r.coins.length) {
    row.innerHTML = '<div class="text-muted">Not enough news yet — scores appear as stories are collected.</div>';
    $("reviewReasons").innerHTML = "";
    return;
  }

  row.innerHTML = r.coins
    .map((c) => {
      const cls = c.stance === "bullish" ? "pos" : c.stance === "bearish" ? "neg" : "neu";
      const arrow = c.stance === "bullish" ? "▲" : c.stance === "bearish" ? "▼" : "◆";
      const reasons = (c.reasons || [])
        .map((x) => x.title)
        .filter(Boolean)
        .join("\n");
      return `<button class="review-chip ${cls}" data-coin="${esc(c.symbol)}" title="${esc(reasons)}">
        <span class="review-coin">${esc(coinOf(c.symbol))}</span>
        <span class="review-score">${arrow} ${c.score > 0 ? "+" : ""}${c.score}</span>
        <span class="review-stance">${c.stance}</span>
      </button>`;
    })
    .join("");

  row.querySelectorAll(".review-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const coin = chip.dataset.coin;
      state.reviewCoin = state.reviewCoin === coin ? "" : coin;
      const sel = $("reviewCoin");
      sel.value = state.reviewCoin;
      loadNews();
    });
  });

  const reasonsHtml = r.coins
    .slice(0, 3)
    .map((c) => {
      const top = (c.reasons || []).find((x) => x.title);
      if (!top) return "";
      const cls = top.sentiment === "bullish" ? "pos" : top.sentiment === "bearish" ? "neg" : "";
      return `<span class="review-reason ${cls}"><b>${esc(coinOf(c.symbol))}:</b> ${esc(top.title)}</span>`;
    })
    .filter(Boolean)
    .join("");
  $("reviewReasons").innerHTML = reasonsHtml
    ? `<div class="review-reasons">${reasonsHtml}</div>`
    : "";
}

/* ====================================================================
   Trading signals
   ==================================================================== */
function _tvSymbol(sym) { return "BINANCE:" + sym.replace("/", ""); }

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

function _fmtLvl(v) { return v != null ? "$" + fmtPrice(v) : "—"; }

function _tfDots(multi) {
  return ["1d","4h","1h","15m"]
    .map((tf) => {
      const lbl = (multi||{})[tf];
      const c = lbl==="BULLISH"?"dot-bull":lbl==="BEARISH"?"dot-bear":"dot-neut";
      return `<span class="tf-dot"><span class="dot ${c}"></span>${tf.toUpperCase()}</span>`;
    }).join("");
}

/* ── Factor score bar ── */
function _factorBar(factors) {
  if (!factors) return "";
  const names = ["trend","momentum","carry","reversal","value"];
  const labels = {trend:"Trend",momentum:"Mom",carry:"Carry",reversal:"Rev",value:"Value"};

  const items = names.map((k) => {
    const f = factors[k];
    if (!f) return "";
    const s = f.score;
    const cls = s > 0.05 ? "pos" : s < -0.05 ? "neg" : "neu";
    // Meter: center = 0, fill goes left (bear) or right (bull)
    const pct = Math.min(Math.abs(s), 1) * 50; // 0-50% of the meter
    const fillStyle = s > 0.05
      ? `left:50%;width:${pct}%`
      : s < -0.05
        ? `right:50%;width:${pct}%`
        : "display:none";
    return `<span class="factor-item">
      <span class="factor-label">${labels[k]}</span>
      <span class="factor-meter">
        <span class="factor-meter-center"></span>
        <span class="factor-meter-fill ${s>0.05?"bull":"bear"}" style="${fillStyle}"></span>
      </span>
      <span class="factor-val ${cls}">${s>0?"+":""}${(s*100).toFixed(0)}%</span>
    </span>`;
  }).filter(Boolean).join("");

  const comp = factors.composite;
  const compCls = comp > 0.05 ? "pos" : comp < -0.05 ? "neg" : "neu";
  const compLabel = comp > 0.15 ? "BULLISH" : comp < -0.15 ? "BEARISH" : "NEUTRAL";

  return `<div class="factor-bar">
    ${items}
    <span class="factor-composite">
      <span class="factor-composite-label">Composite</span>
      <span class="factor-val ${compCls}">${comp>0?"+":""}${(comp*100).toFixed(0)}%</span>
      <span class="factor-val ${compCls}">${compLabel}</span>
    </span>
  </div>`;
}

/* ── Math engine stats (regime, kelly, EV, posterior) ── */
function _mathStats(s) {
  const parts = [];
  if (s.regime) {
    const rLabel = s.regime.replace("trending_","Trend ").replace("ranging","Range");
    parts.push(`<span class="math-stat"><span class="math-stat-label">Regime</span><span class="regime-badge regime-${s.regime}">${rLabel}</span></span>`);
  }
  if (s.adx != null) {
    parts.push(`<span class="math-stat"><span class="math-stat-label">ADX</span><span class="math-stat-val">${s.adx}</span></span>`);
  }
  if (s.kelly_fraction != null && s.kelly_fraction > 0) {
    parts.push(`<span class="math-stat"><span class="math-stat-label">Kelly</span><span class="math-stat-val">${(s.kelly_fraction*100).toFixed(1)}%</span></span>`);
  }
  if (s.ev_per_unit != null) {
    const evCls = s.ev_per_unit > 0 ? "pos" : s.ev_per_unit < 0 ? "neg" : "neu";
    parts.push(`<span class="math-stat"><span class="math-stat-label">EV</span><span class="math-stat-val ${evCls}">${s.ev_per_unit>0?"+":""}${(s.ev_per_unit*100).toFixed(1)}%</span></span>`);
  }
  if (s.posterior_bull != null) {
    parts.push(`<span class="math-stat"><span class="math-stat-label">P(bull)</span><span class="math-stat-val">${(s.posterior_bull*100).toFixed(1)}%</span></span>`);
  }
  if (s.confluence != null) {
    parts.push(`<span class="math-stat"><span class="math-stat-label">Conf.</span><span class="math-stat-val">${(s.confluence*100).toFixed(0)}%</span></span>`);
  }
  return parts.length ? `<div class="math-stats">${parts.join("")}</div>` : "";
}

function _renderActiveSignal(s) {
  const dir = s.direction;
  const badge = dir==="LONG" ? "badge-long" : "badge-short";
  const arrow = dir==="LONG" ? "▲" : "▼";
  const tvUrl = "https://www.tradingview.com/chart/?symbol=" + _tvSymbol(s.symbol);
  const rr = (s.entry && s.stop_loss && s.take_profit)
    ? ((s.take_profit - s.entry) / (s.entry - s.stop_loss)).toFixed(1) : "—";
  const reasons = (s.reasons||[]).filter(r=>r.signal&&r.signal!=="HOLD").slice(0,4)
    .map(r=>`<span class="sig-reason">[${r.interval}] ${r.indicator} — ${r.detail}</span>`).join("");
  return `<div class="sig-active sig-${dir.toLowerCase()}" data-symbol="${esc(s.symbol)}">
    <div class="sig-active-head">
      <div class="sig-active-left">
        <span class="sig-coin">${esc(coinOf(s.symbol))}</span>
        <span class="signal-badge ${badge}">${arrow} ${dir}</span>
        <span class="sig-conf">${s.confidence}%</span>
      </div>
      <div class="sig-active-right">
        <a class="btn-signal" href="${esc(tvUrl)}" target="_blank" rel="noopener">
          <i class="fa-solid fa-arrow-up-right-from-square"></i> TV</a>
        <button class="btn-signal pine-copy" data-symbol="${esc(s.symbol)}">
          <i class="fa-regular fa-copy"></i> Pine</button>
      </div>
    </div>
    <div class="sig-levels-row">
      <span class="lvl"><span class="lvl-dot" style="background:var(--accent-yellow,#fbbf24)"></span>Entry ${_fmtLvl(s.entry)}</span>
      <span class="lvl"><span class="lvl-dot" style="background:var(--accent-red,#ef4444)"></span>SL ${_fmtLvl(s.stop_loss)}</span>
      <span class="lvl"><span class="lvl-dot" style="background:var(--accent-green,#22c55e)"></span>TP ${_fmtLvl(s.take_profit)}</span>
      <span class="lvl lvl-rr">R:R 1:${rr}</span>
    </div>
    <div class="sig-bottom">${_tfDots(s.multi_tf)}<div class="sig-conf-bar"><div class="conf-fill ${dir==="LONG"?"bull":"bear"}" style="width:${s.confidence}%"></div></div></div>
    ${_factorBar(s.factors)}
    ${_mathStats(s)}
    ${reasons ? `<div class="sig-reasons">${reasons}</div>` : ""}
  </div>`;
}

function _renderHoldSignal(s) {
  // Compact factor summary for hold signals
  let factorCompact = "";
  if (s.factors) {
    const f = s.factors;
    const comp = f.composite || 0;
    const cls = comp > 0.05 ? "pos" : comp < -0.05 ? "neg" : "neu";
    factorCompact = `<span class="factor-val ${cls}" style="font-size:0.68rem;margin-left:4px;">Factor: ${comp>0?"+":""}${(comp*100).toFixed(0)}%</span>`;
  }
  let regimeCompact = "";
  if (s.regime) {
    const rLabel = s.regime.replace("trending_","T").replace("ranging","R");
    regimeCompact = `<span class="regime-badge regime-${s.regime}" style="font-size:0.6rem;padding:1px 4px;">${rLabel}</span>`;
  }
  return `<div class="sig-hold" data-symbol="${esc(s.symbol)}">
    <span class="sig-coin-sm">${esc(coinOf(s.symbol))}</span>
    <span class="signal-badge badge-hold">◆ HOLD</span>
    <span class="lvl-sm">Entry ${_fmtLvl(s.entry)}</span>
    <span class="lvl-sm lvl-sl-sm">SL ${_fmtLvl(s.stop_loss)}</span>
    <span class="lvl-sm lvl-tp-sm">TP ${_fmtLvl(s.take_profit)}</span>
    ${regimeCompact}
    ${factorCompact}
    <span class="sig-conf-sm">${s.confidence}%</span>
    ${_tfDots(s.multi_tf)}
  </div>`;
}

async function loadSignals() {
  const grid = $("signalsGrid");
  const note = $("signalsNote");
  let r;
  try {
    r = await fetchJSON("/api/signals");
  } catch (err) {
    // Never leave the panel on a silent "loading…"; show a recoverable
    // state — the next poll will retry automatically.
    grid.innerHTML = '<div class="text-muted">⚠ Signals unavailable — retrying…</div>';
    note.textContent = "";
    return;
  }
  if (!r.signals||!r.signals.length) { grid.innerHTML='<div class="text-muted">No signal data yet.</div>'; return; }
  note.textContent = "news · " + (r.news_mode||"rule");

  lastSignals = {};
  r.signals.forEach(s => { lastSignals[s.symbol] = s; });

  const active = r.signals.filter(s => s.direction !== "HOLD");
  const holds  = r.signals.filter(s => s.direction === "HOLD");

  let html = "";
  if (active.length) html += `<div class="signals-active">${active.map(_renderActiveSignal).join("")}</div>`;
  if (holds.length)  html += `<div class="signals-hold-title">On hold</div><div class="signals-hold">${holds.map(_renderHoldSignal).join("")}</div>`;
  grid.innerHTML = html;

  // Click row -> switch chart + show levels
  grid.querySelectorAll("[data-symbol]").forEach(row => {
    row.addEventListener("click", e => {
      if (e.target.closest(".btn-signal")) return;
      const sym = row.dataset.symbol; if (!sym) return;
      state.symbol = sym;
      document.querySelectorAll("#symbolList .sym-row").forEach(b =>
        b.classList.toggle("active", b.dataset.symbol===sym));
      loadChart();
    });
  });
  // Pine copy
  grid.querySelectorAll(".pine-copy").forEach(btn => {
    btn.addEventListener("click", e => {
      e.stopPropagation();
      const sig = lastSignals[btn.dataset.symbol]; if (!sig) return;
      navigator.clipboard.writeText(_pineScript(sig)).then(()=>{
        btn.innerHTML='<i class="fa-solid fa-check"></i> Copied!';
        setTimeout(()=>{ btn.innerHTML='<i class="fa-regular fa-copy"></i> Pine'; },1500);
      });
    });
  });
}

/* ===== Data loaders ===== */
async function loadChart() {
  const qs = new URLSearchParams({
    symbol: state.symbol,
    interval: state.interval,
    limit: "400",
  });
  const d = await fetchJSON("/api/candles?" + qs);
  lastCandles = d;
  renderStats(d);
  renderChart(d);
}

function drawSparkline(canvas, prices, up) {
  if (!canvas || !prices || prices.length < 2) return;
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  const pad = 1.5;
  const min = Math.min(...prices), max = Math.max(...prices);
  const rng = max - min || 1;
  ctx.clearRect(0, 0, W, H);
  ctx.beginPath();
  prices.forEach((p, i) => {
    const x = pad + (i / (prices.length - 1)) * (W - pad * 2);
    const y = pad + (1 - (p - min) / rng) * (H - pad * 2);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  const color = up ? (cssVar("--accent-green") || "#10b981") : (cssVar("--accent-red") || "#f43f5e");
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.2;
  ctx.lineJoin = "round";
  ctx.stroke();
  // soft fill
  ctx.lineTo(pad + (W - pad * 2), H - pad);
  ctx.lineTo(pad, H - pad);
  ctx.closePath();
  ctx.globalAlpha = 0.12;
  ctx.fillStyle = color;
  ctx.fill();
  ctx.globalAlpha = 1;
}

async function loadWatchlist() {
  const qs = new URLSearchParams({ interval: state.interval });
  const w = await fetchJSON("/api/watchlist?" + qs);
  w.symbols.forEach((s) => {
    const row = document.querySelector(`#symbolList .sym-row[data-symbol="${esc(s.symbol)}"]`);
    if (!row) return;
    row.querySelector(".sym-price").textContent = fmtPrice(s.price);
    const chg = row.querySelector(".sym-chg");
    chg.textContent = (s.change >= 0 ? "+" : "") + s.change.toFixed(2) + "%";
    chg.className = "sym-chg " + (s.change >= 0 ? "pos" : "neg");
    drawSparkline(row.querySelector(".sym-spark"), s.spark, s.change >= 0);
  });
}

async function loadNews() {
  const qs = new URLSearchParams({ limit: "40" });
  if (state.sentiment) qs.set("sentiment", state.sentiment);
  if (state.reviewCoin) qs.set("symbol", state.reviewCoin);
  const n = await fetchJSON("/api/news?" + qs);
  const feed = $("newsFeed");
  if (!n.items.length) {
    feed.innerHTML = '<div class="text-muted">No stories match this filter.</div>';
    return;
  }
  const TAGS = {
    bullish: ["🟢 Bullish", "sent-bull"],
    bearish: ["🔴 Bearish", "sent-bear"],
    important: ["⭐ Important", "sent-imp"],
    neutral: ["⚪ Neutral", "sent-neu"],
  };
  feed.innerHTML = n.items.map((it) => {
    const [tag, cls] = TAGS[it.sentiment] || TAGS.neutral;
    const coins = (it.symbols || "").split(",").filter(Boolean).map((c) => c.trim());
    const meta = [esc(it.source || ""), timeAgo(it.published_at)]
      .filter(Boolean)
      .concat(coins.length ? [coins.slice(0, 4).join(", ")] : [])
      .join(" · ");
    return `<div class="news-item">
      <span class="news-tag ${cls}">${tag}</span>
      <a class="news-title" href="${esc(it.url)}" target="_blank" rel="noopener noreferrer">${esc(it.title)}</a>
      <div class="news-meta">${meta}</div>
    </div>`;
  }).join("");
}

async function loadHealth() {
  try {
    const h = await fetchJSON("/api/health");
    $("healthDot").className = "pulse-dot green";
    $("healthText").textContent = `live · ${h.candles.toLocaleString()} candles · ${h.news} news`;
  } catch {
    $("healthDot").className = "pulse-dot red";
    $("healthText").textContent = "server offline";
  }
}

async function loadFearGreed() {
  const fg = await fetchJSON("/api/fear-greed");
  $("fgValue").textContent = fg.value != null ? fg.value : "—";
  $("fgLabel").textContent = fg.label ? "Fear & Greed · " + fg.label : "Fear & Greed";
}

/* ===== Stats row ===== */
function renderStats(d) {
  const close = d.close;
  const last = close[close.length - 1];
  const prev = close[close.length - 2];
  const chg = prev ? ((last - prev) / prev) * 100 : 0;
  const i = close.length - 1;

  $("stLast").textContent = fmtPrice(last);
  $("stChange").textContent = (chg >= 0 ? "+" : "") + chg.toFixed(2) + "%";
  $("stChange").className = "stat-value monospace " + (chg >= 0 ? "pos" : "neg");
  $("stRsi").textContent = (d.rsi && d.rsi[i] != null) ? d.rsi[i].toFixed(1) : "—";
  $("stAtr").textContent = (d.atr && d.atr[i] != null) ? fmtPrice(d.atr[i]) : "—";
  $("stVol").textContent = (d.volume && d.volume[i] != null) ? fmtVol(d.volume[i]) : "—";
  const rng = (d.high && d.high[i] != null && d.low && d.low[i] != null) ? d.high[i] - d.low[i] : null;
  $("stRange").textContent = rng != null ? fmtPrice(rng) : "—";

  $("pageTitle").textContent = `${coinOf(d.symbol)} · ${d.interval}`;
  $("pageSub").textContent =
    d.count + " candles · last " + (d.times[d.times.length - 1] || "").replace("T", " ").replace("Z", "") + " UTC";
}

/* ===== ECharts ===== */
function renderChart(d) {
  if (!chart) return;
  const times = d.times;
  const up = cssVar("--accent-green") || "#10b981";
  const down = cssVar("--accent-red") || "#f43f5e";
  const lime = cssVar("--wise-lime") || "#9fe870";
  const blue = cssVar("--primary") || "#38bdf8";
  const muted = cssVar("--text-muted") || "#64748b";
  const subtle = cssVar("--border-subtle") || "rgba(255,255,255,0.08)";

  const kData = times.map((_, i) => [d.open[i], d.close[i], d.low[i], d.high[i]]);
  const volData = times.map((_, i) => ({
    value: d.volume[i],
    itemStyle: { color: d.close[i] >= d.open[i] ? up : down, opacity: 0.55 },
  }));

  const catAxis = (gridIndex, showLabel) => ({
    type: "category",
    data: times,
    gridIndex,
    boundaryGap: true,
    axisLine: { lineStyle: { color: subtle } },
    axisLabel: {
      show: showLabel,
      color: muted,
      fontSize: 10,
      formatter: (v) => (v ? v.slice(11, 16) : ""),
    },
    axisTick: { show: false },
    splitLine: { show: false },
  });
  const valAxis = (gridIndex, extra) => ({
    type: "value",
    gridIndex,
    scale: true,
    axisLine: { show: false },
    axisLabel: { color: muted, fontSize: 10 },
    splitLine: { lineStyle: { color: subtle } },
    ...extra,
  });
  const line = (name, data, color, gridIndex, extra) => ({
    name,
    type: "line",
    xAxisIndex: gridIndex,
    yAxisIndex: gridIndex,
    data,
    smooth: true,
    showSymbol: false,
    lineStyle: { width: 1, color },
    ...extra,
  });

  const kSeries = {
    name: "K",
    type: "candlestick",
    data: kData,
    itemStyle: { color: up, color0: down, borderColor: up, borderColor0: down },
  };

  // Chart overlay: entry / SL / TP lines when a signal is active for the current symbol.
  const sig = lastSignals[d.symbol];
  if (sig && sig.direction !== "HOLD" && sig.entry) {
    kSeries.markLine = {
      silent: true,
      symbol: "none",
      label: { show: true, fontSize: 10, color: "#f8fafc", position: "insideEndTop" },
      data: [
        { yAxis: sig.entry, name: "Entry",
          lineStyle: { color: "#fbbf24", type: "dashed", width: 2 },
          label: { formatter: "▲ Entry " + fmtPrice(sig.entry), color: "#fbbf24" } },
        { yAxis: sig.stop_loss, name: "SL",
          lineStyle: { color: "#ef4444", type: "solid", width: 1.5 },
          label: { formatter: "▼ SL " + fmtPrice(sig.stop_loss), color: "#ef4444" } },
        { yAxis: sig.take_profit, name: "TP",
          lineStyle: { color: "#22c55e", type: "solid", width: 1.5 },
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
      backgroundColor: cssVar("--bg-elevated") || "#111c30",
      borderColor: subtle,
      textStyle: { color: cssVar("--text-main") || "#f8fafc", fontSize: 11 },
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
          if (prev != null && prev !== 0)
            out += `  (${(((d.close[i] - prev) / prev) * 100).toFixed(2)}%)`;
          out += `<br/>Vol ${fmtVol(d.volume[i])}`;
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
          silent: true,
          symbol: "none",
          lineStyle: { type: "dashed", color: down, opacity: 0.6 },
          label: { show: false },
          data: [{ yAxis: 70 }, { yAxis: 30 }],
        },
      }),
      { name: "MACD hist", type: "bar", xAxisIndex: 3, yAxisIndex: 3, data: d.macd_hist, itemStyle: { opacity: 0.6 } },
      line("MACD", d.macd, blue, 3),
      line("Signal", d.macd_signal, up, 3),
    ],
  };
  chart.setOption(option, true);
}

function rerenderChart() {
  if (lastCandles) renderChart(lastCandles);
}

/* ===== Boot ===== */
window.addEventListener("DOMContentLoaded", () => {
  initTheme();
  buildCatTabs();
  buildSymbolList();
  buildIntervalList();
  buildReviewSelect();
  wireTheme();
  chart = echarts.init($("chart"));
  window.addEventListener("resize", () => chart && chart.resize());
  loadAll();
  setInterval(loadAll, POLL_MS);
});
