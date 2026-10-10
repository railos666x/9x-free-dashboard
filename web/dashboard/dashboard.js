/* cryptoautomation — Dashboard
 * Sellimor-style layout wired to the backend API. No framework: plain
 * fetch + ECharts, with defensive error handling everywhere.
 */
"use strict";

const $ = (id) => document.getElementById(id);

const ACCENT = "#0ecb81";
const UP = "#0ecb81";
const DOWN = "#f6465d";

const fmt = (n, d = 2) => {
  if (n === null || n === undefined || !isFinite(n)) return "—";
  return n.toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 });
};
const fmtUSD = (n) => (n === null || n === undefined || !isFinite(n) ? "—" : `$${fmt(n)}`);
const fmtMoney = (n) => (n === null || n === undefined || !isFinite(n) ? "—"
  : n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

// ── Broker account cards (Portfolio) ───────────────────────────────
// ── Connection diagnostics (/api/broker-health) ────────────────────
function _ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

async function loadBrokerHealth() {
  const el = $("brokerHealth");
  if (!el) return;
  try {
    const { mode, brokers } = await api("/api/broker-health");
    const meta = $("brokerHealthMeta");
    if (meta) meta.textContent = `${mode} · ${brokers.filter(b => b.ok).length}/${brokers.length} connected`;
    el.innerHTML = brokers.map((b) => {
      const dot = b.ok ? "bh-ok" : "bh-bad";
      const rec = b.last_reconcile || {};
      const recTxt = rec.at
        ? `${_ago(rec.at)}${rec.ok ? "" : " \u26A0 failed"}${rec.updated ? ` \u00b7 ${rec.updated} change(s)` : ""}`
        : "never";
      const recCls = rec.at ? (rec.ok ? "" : "bh-rec-bad") : "bh-rec-never";
      return `<div class="bh-row">
        <span class="bh-dot ${dot}" title="${b.ok ? "connected" : esc(b.error)}"></span>
        <span class="bh-name"><span class="broker-dot" style="background:${b.color}"></span> ${esc(b.name)}</span>
        <span class="bh-fp" title="account/key in use">${esc(b.fingerprint || "\u2014")}${b.detail ? ` <span class=\"muted\">\u00b7 ${esc(b.detail)}</span>` : ""}</span>
        <span class="bh-lat ${b.latency_ms > 2000 ? "bh-rec-bad" : ""}" title="probe latency">${b.ok ? b.latency_ms + " ms" : "\u2014"}</span>
        <span class="bh-rec ${recCls}" title="last journal reconciliation">sync: ${recTxt}</span>
        ${b.error ? `<span class="bh-err" title="${esc(b.error)}">\u26A0 ${esc(b.error)}</span>` : ""}
      </div>`;
    }).join("");
  } catch (e) {
    el.innerHTML = `<div class="muted">Diagnostics unavailable — ${esc(e.message)}</div>`;
  }
}

async function loadBrokerAccounts() {
  const el = $("brokerAccounts");
  if (!el) return;
  try {
    const { accounts } = await api("/api/broker-accounts");
    const badgeOnly = (b) => _ltBrokerBadge(b).replace(/<span class="lt-broker-name">.*?<\/span>/, "");
    el.innerHTML = (accounts || []).map((a) => {
      const badge = badgeOnly(a.broker);
      if (a.error) {
        return `<div class="ba-card ba-error">
          <div class="ba-head">${badge}<span class="ba-name">${esc(a.name || a.broker)}</span><span class="ba-type">offline</span></div>
          <div class="ba-error-msg">⚠ ${esc(a.error)}</div>
        </div>`;
      }
      const pnl = a.floating_pnl || 0;
      const rows = (a.details || []).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
      return `<div class="ba-card">
        <div class="ba-head">${badge}<span class="ba-name">${esc(a.name || "")}</span><span class="ba-type">${esc(a.account_type || "")}</span></div>
        <div class="ba-main">
          <span class="ba-balance">${fmtUSD(a.equity != null ? a.equity : a.balance)}</span>
          <span class="ba-currency">${esc(a.currency || "")}</span>
          ${pnl && Math.abs(pnl) >= 0.005 ? `<span class="ba-pnl ${pnl > 0 ? "up" : "down"}">${pnl > 0 ? "+" : ""}${fmt(pnl, 2)}</span>` : ""}
        </div>
        <dl class="ba-details">${rows}</dl>
      </div>`;
    }).join("") || '<div class="muted">No brokers configured</div>';
    const meta = $("brokerAccountsMeta");
    if (meta) meta.textContent = `${(accounts || []).length} connected · ${new Date().toLocaleTimeString()}`;
  } catch (e) {
    el.innerHTML = `<div class="muted">Broker accounts unavailable — ${esc(e.message || e)}</div>`;
  }
}
const fmtCompact = (n) => {
  if (n === null || n === undefined || !isFinite(n)) return "—";
  const a = Math.abs(n);
  if (a >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `$${(n / 1e3).toFixed(2)}K`;
  return `$${fmt(n)}`;
};
const symOf = (symbol) => {
  // For crypto (ending in /USDT or USDT), show base currency only
  const s = String(symbol == null ? "" : symbol).toUpperCase();
  if (!s) return "";
  if (s.endsWith("/USDT") || s.endsWith("USDT")) {
    return s.split("/")[0].replace("USDT", "");
  }
  // For forex/commodities/indices, show full symbol
  return symbol;
};
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Price formatting that keeps significant digits for micro-priced coins (SHIB etc).
// Professional inline-SVG icon from the sprite in index.html (no emoji).
function icon(name, cls = "") {
  return `<svg class="ic ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

const fmtPrice = (n) => {
  if (n === null || n === undefined || !isFinite(n)) return "—";
  if (n !== 0 && Math.abs(n) < 0.001) return n.toLocaleString("en-US", { maximumSignificantDigits: 6 });
  if (n >= 1000) return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (n >= 1) return n.toLocaleString("en-US", { maximumFractionDigits: 4 });
  return n.toLocaleString("en-US", { maximumSignificantDigits: 6 });
};

// ── API helper with timeout + one retry on transient failures ───────
// Consecutive failed polls flip a visible "Reconnecting…" badge in the
// topbar (server restart / brief downtime) instead of silent console
// noise. Any successful response clears it.
let _connFailures = 0;
const RECONNECT_SHOW_AFTER = 2;   // show the badge after this many misses

function _setReconnecting(show) {
  const b = $("reconnectBadge");
  if (b) b.hidden = !show;
}

async function api(path, opts = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeout || 20000);
  try {
    const res = await fetch(path, {
      signal: ctrl.signal,
      cache: "no-store",   // fresh trades must never hide behind browser cache
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    if (!res.ok) throw new Error(`${path} → ${res.status}`);
    const data = await res.json();
    _connFailures = 0;                       // any success = we're back
    _setReconnecting(false);
    return data;
  } catch (e) {
    // A connection reset/refused usually means the server was mid-restart.
    // GETs retry once after a short pause so the page self-heals; POSTs
    // never auto-retry (orders must not be placed twice by accident).
    const isPost = String(opts.method || "GET").toUpperCase() === "POST";
    if (!isPost && !opts._retried) {
      await new Promise((r) => setTimeout(r, 1500));
      return api(path, { ...opts, _retried: true });
    }
    _connFailures += 1;
    if (_connFailures >= RECONNECT_SHOW_AFTER) _setReconnecting(true);
    throw e;
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
  ordSymbol: null,        // selected orders-chart symbol (defaults to open position)
  tableInterval: "1h",   // Assets table timeframe
};

// ── View switching (sidebar) ────────────────────────────────────────
const VIEWS = ["dashboard", "assets", "portfolio", "bots", "orders", "signals", "learn", "aitest", "forex"];

function switchView(name) {
  if (!VIEWS.includes(name)) name = "dashboard";
  document.querySelectorAll(".nav-item").forEach((a) =>
    a.classList.toggle("active", a.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) =>
    v.classList.toggle("active", v.id === `view-${name}`));
  // Charts initialised inside a hidden view have zero size — resize on show.
  requestAnimationFrame(() => {
    [portValueChart, totalChart, sigChart, ...pnlCharts.values()].forEach((c) => c && c.resize());
  });
  if (name === "assets") loadWatchlist();
  if (name === "orders") {
    loadBotStatus();
    loadOrders();
    loadLiveTrades();
    if (ordChart) ordChart.resize();
    if (pendingOrdCandles) { renderOrdChart(pendingOrdCandles); pendingOrdCandles = null; }
    else if (state.ordSymbol) loadOrdChart();
  }
  if (name === "signals") {
    loadSignals();
    if (sigChart) sigChart.resize();
    if (pendingSigCandles) { renderSigChart(pendingSigCandles); pendingSigCandles = null; }
    else if (state.sigSymbol) loadSigChart();
  }
  if (name === "learn") loadTutor();
  if (name === "forex") loadForex();
}

// ── Forex view (backend/forex.py via /api/forex/overview) ───────────
let _fxData = null;                 // last overview payload
let _fxCarryMap = {};               // pair → favored side

async function loadForex() {
  try {
    const d = await api("/api/forex/overview");
    if (!d || d.ok === false) throw new Error(d && d.error || "unavailable");
    _fxData = d;
    _fxCarryMap = {};
    (d.carry || []).forEach((c) => { _fxCarryMap[c.pair] = c.favored_side; });
    renderFxSession(d);
    renderFxStrength(d);
    renderFxExposure(d);
    renderFxPairs(d);
  } catch (e) {
    ["fxSessionBody", "fxStrengthBody", "fxExposureBody", "fxPairsBody"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = `<div class="muted">FX data unavailable — ${esc(e.message)}</div>`;
    });
  }
}

function renderFxSession(d) {
  const s = d.session || {};
  const liq = Math.round((s.liquidity || 0) * 100);
  const nextIn = s.weekend ? "weekend — market closed" : `${s.hours_to_next}h to ${s.next_session}`;
  $("fxSessionMeta").textContent = `${s.label || "?"} · ${liq}% liquidity · ${nextIn}`;
  const sessions = ["asian", "london", "overlap", "ny", "dead"];
  const labels = { asian: "Asian", london: "London", overlap: "London+NY", ny: "New York", dead: "Dead zone" };
  $("fxSessionBody").innerHTML = `
    <div class="fx-sessions">${sessions.map((k) => `
      <div class="fx-session-pill ${k === s.session ? "active" : ""}">
        <b>${labels[k]}</b><span class="muted">${labels[k] === s.label ? " now" : ""}</span>
      </div>`).join("")}</div>
    <div class="muted" style="margin-top:10px">${esc(s.note || "")}</div>
    <div class="fx-usd ${Math.abs(d.usd_index_24h || 0) < 0.15 ? "muted" : (d.usd_index_24h > 0 ? "fx-pos" : "fx-neg")}">
      Synthetic USD index (24h): <b>${d.usd_index_24h == null ? "n/a" : (d.usd_index_24h > 0 ? "+" : "") + d.usd_index_24h + "%"}</b>
      <span class="muted">· real ICE weights over the 6 majors</span>
    </div>`;
}

function _fxBar(score, max) {
  const w = max ? Math.min(100, Math.abs(score) / max * 100) : 0;
  const cls = score >= 0 ? "fx-bar-pos" : "fx-bar-neg";
  return `<div class="fx-bar"><div class="fx-bar-fill ${cls}" style="width:${w}%"></div></div>`;
}

function renderFxStrength(d) {
  const rows = (d.strength || []).slice(0, 10);
  $("fxStrengthMeta").textContent = rows.length ? "top ± movers" : "";
  if (!rows.length) { $("fxStrengthBody").innerHTML = '<div class="muted">No 24h data yet.</div>'; return; }
  const max = Math.max(...rows.map((r) => Math.abs(r.score)), 0.01);
  $("fxStrengthBody").innerHTML = rows.map((r) => `
    <div class="fx-strength-row">
      <b>${r.currency}</b>
      ${_fxBar(r.score, max)}
      <span class="muted">${r.score > 0 ? "+" : ""}${r.score.toFixed(2)}</span>
    </div>`).join("");
}

function renderFxExposure(d) {
  const ex = d.exposure || {};
  const entries = Object.entries(ex.by_currency || {}).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  if (!entries.length) { $("fxExposureBody").innerHTML = '<div class="muted">No open FX positions.</div>'; return; }
  const max = Math.max(...entries.map(([, v]) => Math.abs(v)), 1);
  $("fxExposureBody").innerHTML = entries.map(([c, v]) => `
    <div class="fx-strength-row">
      <b>${c}</b>
      ${_fxBar(v, max)}
      <span class="muted">net ${v > 0 ? "+" : ""}${v}</span>
    </div>`).join("") +
    (ex.concentration >= 3 ? `<div class="fx-warn">Correlated risk: net ${ex.top_currency} exposure of ${ex.top_net} across open positions.</div>` : "");
}

function renderFxPairs(d) {
  $("fxPairsMeta").textContent = `${d.pairs_with_data}/${d.pairs_total} pairs with data`;
  const withData = (d.pairs || []).filter((p) => p.has_data);
  const rest = (d.pairs || []).filter((p) => !p.has_data);
  const trendCls = { bullish: "fx-pos", bearish: "fx-neg", neutral: "muted" };
  const momCls = { bull: "fx-pos", bear: "fx-neg", neutral: "muted" };
  $("fxPairsBody").innerHTML = withData.map((p) => `
    <tr>
      <td><b>${p.symbol}</b></td>
      <td>${fmtPrice(p.last)}</td>
      <td class="${(p.chg_24h || 0) >= 0 ? "fx-pos" : "fx-neg"}">${p.chg_24h == null ? "" : (p.chg_24h > 0 ? "+" : "") + p.chg_24h.toFixed(2)}%</td>
      <td class="${trendCls[p.trend] || "muted"}">${p.trend}</td>
      <td class="${momCls[p.momentum] || "muted"}">${p.momentum}</td>
      <td>${p.rsi}</td>
      <td>${p.atr_pct == null ? "" : p.atr_pct + "%"}</td>
      <td class="muted">${(p.sessions || []).join(", ")}</td>
      <td class="muted">${_fxCarryMap[p.symbol] || ""}</td>
    </tr>`).join("") +
    rest.map((p) => `<tr><td><b>${p.symbol}</b></td><td colspan="8" class="muted">no candle data yet</td></tr>`).join("");
}

// ── Orders view: bot status + trade journal ─────────────────────────
const ORDER_BADGES = {
  filled: "badge-long", open: "badge-long", closed: "badge-hold",
  dry_run: "badge-hold", cancelled: "badge-hold", error: "badge-short",
};

async function loadBotStatus() {
  const dot = $("serverDot");
  try {
    const s = await api("/api/bot/status");
    window.__botStatus = s;
    renderBotStatus(s);
    if (dot) { dot.classList.add("ok"); dot.classList.remove("down"); dot.title = "Backend connected"; }
  } catch (e) {
    // keep last render; status is best-effort — but show the backend state.
    if (dot) { dot.classList.add("down"); dot.classList.remove("ok"); dot.title = "Backend unreachable — is the server running?"; }
  }
}

function renderBotStatus(s) {
  /* ── Trader flow strip (2026-10-07): answers "is the bot doing anything"
     on every view — loop state, age of the last cycle, what it did, how
     many orders are open. Stale (>2× cadence) turns the loop dot amber. */
  const strip = $("flowStrip");
  if (strip) {
    strip.style.display = "flex";
    const dot = $("fsLoopDot");
    const loopRunning = !!s.auto_loop_running;
    const loopLive = !!s.auto_loop_live;
    let loopTxt = loopRunning ? (loopLive ? "LIVE · demo accs" : "dry-run") : "STOPPED";
    let dotCls = "fs-dot";
    const ranAt = s.last_run_at ? s.last_run_at * 1000 : 0;
    const age = ranAt ? (Date.now() - ranAt) / 1000 : Infinity;
    if (loopRunning) {
      // Normal cycles finish in ~45-90s; beyond 2× cadence nothing has landed
      // for a while — amber, so a stuck loop is visible without opening cards.
      dotCls += age > 2 * (s.loop_seconds || 300) ? " stale" : " live";
    } else {
      dotCls += " down";
    }
    if (dot) dot.className = dotCls;
    const loopState = $("fsLoopState");
    if (loopState) loopState.textContent = loopTxt;
    const ageEl = $("fsCycleAge");
    if (ageEl) {
      ageEl.textContent = !ranAt ? "never"
        : age < 120 ? `${Math.max(0, Math.round(age))}s ago`
        : age < 7200 ? `${Math.round(age / 60)}m ago`
        : `${Math.round(age / 3600)}h ago`;
    }
    const c = s.last_cycle;
    const actEl = $("fsCycleActions");
    if (actEl) {
      actEl.textContent = c
        ? `+${(c.executed || []).length} / ${(c.closed || []).length} closed / ${(c.errors || []).length} err`
        : "—";
      actEl.title = c && (c.held || []).length
        ? `${(c.held || []).length} signals held this cycle`
        : (c ? "no signals held this cycle" : "");
    }
    const ooEl = $("fsOpenOrders");
    if (ooEl) ooEl.textContent = String(s.open_orders ?? 0);
  }
  renderAiSignals(s);

  const body = $("botStatusBody");
  if (!body) return;
  const meta = $("botStatusMeta");
  if (meta) {
    const c = s.last_cycle;
    meta.textContent = c
      ? `· last run ${c.elapsed_s}s · ${(c.executed || []).length} executed · ${(c.closed || []).length} closed`
      : "· never run";
  }
  const lastMode = s.last_cycle
    ? (s.last_cycle.dry_run ? "dry-run" : "live")
    : "";
  if (meta && lastMode) meta.textContent += ` · mode: ${lastMode}`;
  const brokerLabel = s.broker === "alpaca"
    ? `alpaca${s.alpaca_paper ? " (paper)" : " (LIVE)"}`
    : esc(s.broker || "hybrid");
  const gateMin = s.arbitration_min_sources ?? 2;
  const gateLabel = gateMin === 1
    ? `test mode (single source, conf≥${s.arbitration_min_confidence ?? 45})`
    : "production (both sources)";
  const liveBlocked = !!s.live_single_source_blocked;
  const loopRunning = !!s.auto_loop_running;
  const loopLive = !!s.auto_loop_live;
  const loopLabel = !s.auto_loop
    ? "off (BOT_AUTO_LOOP=false)"
    : loopRunning ? (loopLive ? "running · LIVE · demo accs" : "running · dry-run") : "stopped";
  const llmLbl = s.llm_providers == null ? "—"
    : s.llm_providers <= 0 ? "rule-based (add LLM key)"
    : `active (${s.llm_providers} provider${s.llm_providers > 1 ? "s" : ""})`;
  const stats = [
    ["Broker", brokerLabel],
    ["Source", esc(s.signal_source_a || "ai_news")],
    ["Source B (proprietary)", s.proprietary_enabled ? "on (legacy)" : "retired — AI/news only"],
    ["LLM overlay", llmLbl],
    ["Agreement gate", gateLabel],
    ["Live safety", liveBlocked ? `${icon("alert")} blocked (test gate)` : "ok"],
    ["Auto loop", loopLabel],
    ["Loop cadence", `${s.loop_seconds}s`],
    ["Open positions", String(s.open_orders ?? 0)],
    ["News gate (7d)", '<span id="newsGateStats">…</span>'],
  ];
  // News-gate history: vetoes / confirms / neutrals over the last 7 days
  // (see reviewer.verify_trade) — refreshes with the status poll.
  api("/api/news/stats?days=7").then((ns) => {
    const el = $("newsGateStats");
    if (el) el.textContent = ns && ns.total
      ? `${ns.veto} vetoed · ${ns.confirm} confirmed · ${ns.neutral} neutral`
      : "no decisions yet";
  }).catch(() => {});
  // Auto-loop badge + Start/Stop button.
  const loopBadge = $("botLoopBadge");
  if (loopBadge) {
    loopBadge.textContent = loopRunning ? (loopLive ? "LIVE · REALORDERS" : "DRY-RUN") : "OFF";
    loopBadge.className = "signal-badge " + (loopRunning && loopLive ? "badge-long" : "badge-hold");
  }
  const toggle = $("btnToggleLoop");
  if (toggle) {
    toggle.textContent = loopRunning ? "Stop" : "Start";
    toggle.className = loopRunning ? "btn-warn" : "btn-primary";
    toggle.disabled = false;
  }
  // Mode badge on the Dry/Live test buttons + active highlight.
  const modeBadge = $("botModeBadge");
  if (modeBadge) {
    modeBadge.textContent = loopRunning ? (loopLive ? "AUTO: LIVE" : "AUTO: DRY") : "AUTO: OFF";
    modeBadge.className = "signal-badge " + (loopRunning && loopLive ? "badge-long" : "badge-hold");
    modeBadge.title = loopRunning
      ? (loopLive ? "Bot loop is running LIVE — every trade is a real order on the configured account (demo accounts until real credentials exist)" : "Bot loop is running in dry-run (previews only — no orders)")
      : "Bot loop is stopped — Run cycle now sends one manual cycle";
  }
  const liveBtn = $("btnRunBotLive");
  if (liveBtn) liveBtn.classList.toggle("mode-active", loopRunning && loopLive);
  // Source-A picker: synced with the server, and locked while the loop runs.
  const sel = $("botSourceSelect");
  const applyBtn = $("btnApplySource");
  const srcMsg = $("botSourceMsg");
  if (sel) sel.value = s.signal_source_a || "ai_news";
  if (applyBtn) applyBtn.disabled = loopRunning;
  if (srcMsg) srcMsg.innerHTML = loopRunning ? `${icon("lock", "ic-sm")} stop the loop to change source` : "";
  const cycleHtml = '<div class="muted">No cycle run yet — press “Run cycle now”.</div>';
  const c = s.last_cycle;
  if (c && c.blocked) {
    cycleHtml = `<div class="bot-cycle" style="color:var(--red,#ff5c5c);">${icon("alert")} ${esc(c.block_reason || "Live run blocked")}</div>`;
  } else if (c) {
    const modeBadge = c.dry_run
      ? '<span class="signal-badge badge-hold">dry-run</span>'
      : '<span class="signal-badge badge-long">live</span>';
    const execRows = (c.executed || []).map((e) => {
      const why = (e.reasons && e.reasons.length)
        ? `<div class="cycle-why">${e.reasons.map((r) => `· ${esc(r)}`).join("<br>")}</div>`
        : "";
      const dir = e.direction === "LONG" ? "▲ LONG" : "▼ SHORT";
      return `<div class="cycle-trade">
        <span class="sym">${symOf(e.symbol)}</span>
        <span class="signal-badge ${e.direction === "LONG" ? "badge-long" : "badge-short"}">${dir}</span>
        <span>qty ${fmt(e.qty, 4)}</span>
        <span class="muted">${esc(String(e.status || ""))}</span>
        <span class="muted">src: ${esc((e.sources || []).join(" + "))}</span>
        ${why}
      </div>`;
    }).join("");
    const heldRows = (c.held || []).slice(0, 5).map((h) => {
      const why = (h.detail && h.detail.message) ? ` — ${esc(h.detail.message)}` : "";
      const ab = (h.a && h.b && h.a !== h.b) ? ` (A=${h.a} · B=${h.b})` : "";
      return `<div class="cycle-trade muted"><span class="sym">${symOf(h.symbol)}</span> held: ${esc(h.reason || "")}${why}${ab}</div>`;
    }).join("");
    cycleHtml = `<div class="bot-cycle">
      ${modeBadge}
      <span class="sig-conf up">${(c.executed || []).length} executed</span>
      <span class="sig-conf down">${(c.errors || []).length} errors</span>
      <span>${(c.held || []).length} held</span>
      <span>${(c.closed || []).length} closed</span>
      <span class="muted">${c.elapsed_s}s · ${esc(c.ran_at || "")}</span>
    </div>
    ${execRows ? `<div class="cycle-detail"><div class="cycle-detail-title">Why the system traded:</div>${execRows}</div>` : ""}
    ${heldRows ? `<div class="cycle-detail muted"><div class="cycle-detail-title">Held back (top ${Math.min(5, (c.held || []).length)}):</div>${heldRows}</div>` : ""}`;
  }
  // Held-by-reason breakdown (from the last cycle — the auto-loop now
  // surfaces its cycles here too, so this is live, not just manual runs).
  const heldBox = $("heldReasons");
  if (heldBox) {
    const held = (c && c.held) || [];
    const REASON_LABELS = {
      not_on_broker: "asset not on broker",
      no_short_on_spot: "SHORT on spot broker (long-only)",
      no_agreement: "sources disagree / no signal",
      no_signal_a: "source A quiet",
      duplicate: "already traded today",
      max_positions: "max open positions reached",
      cross_source: "price cross-check failed",
      data: "data-quality gate",
      intent: "verification gate",
      news_veto: "news vetoed (opposing headlines / big move expected)",
    };
    if (!held.length) {
      heldBox.innerHTML = "";
    } else {
      const byReason = {};
      for (const h of held) (byReason[h.reason] ||= []).push(h.symbol);
      const rows = Object.entries(byReason)
        .sort((a, b) => b[1].length - a[1].length)
        .map(([reason, syms]) => {
          const label = REASON_LABELS[reason] || reason;
          const shown = syms.slice(0, 8).map(esc).join(", ");
          const more = syms.length > 8 ? ` … +${syms.length - 8} more` : "";
          return `<div class="held-reason">` +
            `<span class="held-reason-n">${syms.length}</span>` +
            `<span class="held-reason-k">${esc(label)}</span>` +
            `<span class="held-reason-s">${shown}${more}</span></div>`;
        }).join("");
      heldBox.innerHTML =
        `<div class="held-reasons-title">Held by reason · last cycle ${(c.held || []).length} symbols</div>${rows}`;
    }
  }
  body.innerHTML =
    `<div class="bot-status-grid">${stats
      .map(([k, v]) => `<div class="bot-stat"><span class="bot-stat-k">${k}</span><span class="bot-stat-v">${v}</span></div>`)
      .join("")}</div>` +
    `<div class="bot-cycle-row">${cycleHtml}</div>`;
}

let lastOrders = [];   // latest journal rows (for the Orders chart levels)

// Closed-trades period filter: presets (today / 7d / 30d / all) or a custom
// from→to window. Applies to the Profit-by-currency donut, the every-closed-
// trade list and the journal table.
const tradesRange = { preset: "7d", from: null, to: null };

function _rangeQuery() {
  const q = new URLSearchParams({ limit: "500", detail: "1" });
  if (tradesRange.from) q.set("from", tradesRange.from);
  if (tradesRange.to) q.set("to", tradesRange.to);
  return q.toString();
}

// The closed-trades panel (donut + every-closed-trade list) is a PERIOD of
// closed trades: window by EXIT time (when the trade actually closed), not
// created time. Open rows have no exit_time and drop out automatically.
function _closedRangeQuery() {
  const q = new URLSearchParams({ limit: "500", detail: "1", status: "closed" });
  if (tradesRange.from) q.set("exitFrom", tradesRange.from);
  if (tradesRange.to) q.set("exitTo", tradesRange.to);
  return q.toString();
}

function _periodLabel() {
  const r = tradesRange;
  if (r.preset === "today") return "today";
  if (r.preset === "7d") return "last 7 days";
  if (r.preset === "30d") return "last 30 days";
  if (r.preset === "all") return "all time";
  return `${r.from || "…"} → ${r.to || "now"}`;
}

function applyTradePreset(preset) {
  tradesRange.preset = preset;
  const now = new Date();
  const iso = (d) => d.toISOString().slice(0, 10);
  if (preset === "today") { tradesRange.from = iso(now); tradesRange.to = null; }
  else if (preset === "7d") { tradesRange.from = iso(new Date(now.getTime() - 7 * 864e5)); tradesRange.to = null; }
  else if (preset === "30d") { tradesRange.from = iso(new Date(now.getTime() - 30 * 864e5)); tradesRange.to = null; }
  else { tradesRange.from = null; tradesRange.to = null; }
  document.querySelectorAll("#tradeRangePills .tf-pill").forEach((b) => b.classList.toggle("active", b.dataset.range === preset));
  const f = $("tradeFrom"), t = $("tradeTo");
  if (f) f.value = tradesRange.from || "";
  if (t) t.value = tradesRange.to || "";
  const meta = $("tradeRangeMeta");
  if (meta) meta.textContent = `· ${_periodLabel()}`;
  // Refresh EVERYTHING scoped to the period — loadOrders alone left the
  // closed-trades list + donut on the old window ("no trades of today
  // even after trying buttons", 2026-10-06).
  loadOrders();
  loadClosedTrades();
}

function applyCustomRange() {
  const f = $("tradeFrom"), t = $("tradeTo");
  tradesRange.preset = "custom";
  tradesRange.from = (f && f.value) || null;
  tradesRange.to = (t && t.value) || null;
  document.querySelectorAll("#tradeRangePills .tf-pill").forEach((b) => b.classList.remove("active"));
  const meta = $("tradeRangeMeta");
  if (meta) meta.textContent = `· ${_periodLabel()}`;
  loadOrders();
  loadClosedTrades();
}

function bindTradeRange() {
  document.querySelectorAll("#tradeRangePills .tf-pill").forEach((b) => {
    b.addEventListener("click", () => applyTradePreset(b.dataset.range));
  });
  const apply = $("btnApplyTradeRange");
  if (apply) apply.addEventListener("click", applyCustomRange);
  ["tradeFrom", "tradeTo"].forEach((id) => {
    const el = $(id);
    if (el) el.addEventListener("change", applyCustomRange);
  });
}

async function loadOrders() {
  try {
    const data = await api("/api/orders?" + _rangeQuery());
    lastOrders = data.orders || [];
    renderOrders(lastOrders, data.summary);
    // Default the position chart to the first open/filled position.
    if (!state.ordSymbol) {
      const open = lastOrders.find((o) => (o.status === "filled" || o.status === "open") && o.symbol);
      state.ordSymbol = (open && open.symbol) || "BTC/USDT";
      const inp = $("ordInput");
      if (inp) inp.value = state.ordSymbol;
    }
    loadOrdChart();
  } catch (e) {
    const b = $("ordersBody");
    if (b) b.innerHTML = `<tr><td colspan="15" class="muted">Orders unavailable — ${esc(e.message)}</td></tr>`;
  }
  loadClosedTrades(); // keep the period panel in sync with every poll/preset
}

// Closed-trades panel: donut + every-closed-trade list, windowed by the
// trade's EXIT time (see _closedRangeQuery).
async function loadClosedTrades() {
  try {
    // Donut + list both honor the selected period. REALIZED PnL comes from
    // closed trades windowed by exit time; UNREALIZED PnL comes from
    // currently-open positions and is always "now" (open trades have no
    // exit time, so they can't sit inside a historical window). The
    // registry must be loaded first so the donut panes exist; if it
    // hasn't resolved yet, render the panes from the fallback list.
    if (!_brokers && !document.querySelector(".dual-donut-wrap .donut-pane")) renderBrokerDonutPanes();
    const [data, donutData] = await Promise.all([
      api("/api/orders?" + _closedRangeQuery()),  // closed in window (also feeds the list)
      api("/api/orders?limit=500&detail=1"),      // everything (open rows → unrealized)
    ]);
    renderPnlDonut(_synthDonutSummary(data.orders || [], donutData.orders || []));
    renderClosedTrades(data.orders || []);
  } catch (e) {
    const box = $("closedTradesList");
    if (box) box.innerHTML = `<div class="muted">Closed trades unavailable — ${esc(e.message)}</div>`;
  }
}

// ── Live trades panel (fast, polled separately from the heavy journal) ──
let _livePollStarted = false;

async function loadLiveTrades() {
  try {
    const d = await api("/api/positions");
    renderLiveTrades(d.positions || []);
  } catch (e) {
    const b = $("liveTradesBody");
    if (b) b.innerHTML = `<div class="muted">Live trades unavailable — ${esc(e.message)}</div>`;
  }
}

function _ltBroker(p) {
  // Which broker holds this position: the journal's placement record wins
  // (real source of truth), then the position's own source field, then the
  // routing heuristic (crypto→Binance, else MT5/Exness). Registry-aware:
  // a source string matches if any registry key is a substring of it.
  const j = lastOrders.find((o) => o.symbol === p.symbol
    && (o.status === "filled" || o.status === "open") && o.side === p.side);
  if (j) return _brokerOf(j);
  const src = (p.source || "").toLowerCase();
  for (const b of _brokerList()) {
    if (src.includes(b.key) || (b.key === "mt5" && (src.includes("mt5") || src.includes("exness")))) return b.key;
  }
  return _brokerOf(p);
}

function _ltBrokerBadge(broker) {
  const m = _brokerMeta(broker);
  return `<span class="broker-tag" style="background:${m.color}22;color:${m.color};border:1px solid ${m.color}55" title="Placed on ${m.name}">${m.letter}</span><span class="lt-broker-name">${m.name}</span>`;
}

function renderLiveTrades(positions) {
  const b = $("liveTradesBody"), meta = $("liveTradesMeta");
  if (!b) return;
  if (meta) {
    const byBroker = positions.reduce((acc, p) => { const k = _ltBroker(p); acc[k] = (acc[k] || 0) + 1; return acc; }, {});
    const parts = Object.entries(byBroker).map(([k, n]) => `${_brokerMeta(k).name} ${n}`);
    // Naked-leg census: software-only or unprotected legs die with the bot.
    // Unknown (manual venue positions) are counted separately — they are
    // not provably protected, so "all venue-protected" would overclaim.
    const naked = positions.filter((p) => p.protection === "software-only"
      || p.protection === "none").length;
    const unknown = positions.filter((p) => p.protection === "unknown").length;
    let prot = "";
    if (naked) prot = ` · ${naked} NAKED`;
    else if (positions.length) {
      prot = " · all venue-protected";
      if (unknown) prot += ` · ${unknown} unknown`;
    }
    meta.textContent = `${positions.length} open · ${parts.join(" · ") || "—"}${prot} · ${new Date().toLocaleTimeString()}`;
  }
  if (!positions.length) {
    b.innerHTML = '<div class="muted">No open positions — the bot is flat.</div>';
    return;
  }
  // Protection badge per leg (2026-10-06): venue OCO bracket vs MT5-attached
  // SL/TP vs software-monitoring-only (naked when the bot is offline).
  const protBadge = (p) => {
    const ocoTip = p.oco_ref ? ` (list ${p.oco_ref})` : "";
    return {
      "venue-oco": `<span class="prot-badge prot-venue" title="Exchange-side OCO bracket resting${ocoTip}">OCO</span>`,
      "venue-sltp": `<span class="prot-badge prot-venue" title="SL/TP attached at the broker (MT5 basket)">MT5 SL/TP</span>`,
      "software-only": `<span class="prot-badge prot-soft" title="Software monitoring only — NO venue bracket; naked whenever the bot is offline">SW ONLY</span>`,
      "none": `<span class="prot-badge prot-none" title="No stop/protection on this leg at all">NAKED</span>`,
      "unknown": `<span class="prot-badge prot-soft" title="Manual venue position — protection state not visible to the journal">?</span>`,
    }[p.protection || "none"] || "";
  };
  b.innerHTML = positions.map((p) => {
    const long = p.side === "LONG";
    const up = p.unrealized_pnl == null ? null : p.unrealized_pnl >= 0;
    const dist = (lvl) => (lvl != null && p.current_price) ? (lvl - p.current_price) / p.current_price * 100 : null;
    const slD = dist(p.stop_loss), tpD = dist(p.take_profit);
    return `<div class="live-trade">
      <div class="lt-head">
        <span class="sym">${symOf(p.symbol)}</span>
        <span class="signal-badge ${long ? "badge-long" : "badge-short"}">${long ? "▲ LONG" : "▼ SHORT"}</span>
        ${_ltBrokerBadge(_ltBroker(p))}
        ${protBadge(p)}
        <span class="lt-upnl ${up === null ? "" : up ? "up" : "down"}">${p.unrealized_pnl == null ? "—" : `${p.unrealized_pnl > 0 ? "+" : ""}${fmt(p.unrealized_pnl, 2)}`}</span>
      </div>
      <div class="lt-grid">
        <span class="muted">qty ${fmt(p.qty, 4)}</span>
        <span class="muted">entry ${fmtPrice(p.entry)}</span>
        <span class="muted">now ${fmtPrice(p.current_price)}</span>
        ${slD != null ? `<span class="lt-sl">SL ${fmtPrice(p.stop_loss)} <span class="lvl-dist ${long === (slD < 0) ? "down" : "up"}">${slD > 0 ? "+" : ""}${slD.toFixed(1)}%</span></span>` : ""}
        ${tpD != null ? `<span class="lt-tp">TP ${fmtPrice(p.take_profit)} <span class="lvl-dist ${long === (tpD > 0) ? "up" : "down"}">${tpD > 0 ? "+" : ""}${tpD.toFixed(1)}%</span></span>` : ""}
        <span class="muted">opened ${esc(String(p.created_at || "").slice(0, 16).replace("T", " "))}</span>
      </div>
    </div>`;
  }).join("");
}

function startLivePolling() {
  if (_livePollStarted) return;
  _livePollStarted = true;
  setInterval(loadLiveTrades, 10000);   // lean endpoint — safe to poll
  startAlertPolling();                  // real-time alerts (15s)
}

/* ── Real-time alerts (2026-10-06) ─────────────────────────────────────
   Between daily digests the trader had NO push channel: a leg losing its
   stop, the loss guard refusing entries, or the digest thread dying were
   visible only on the next manual look. /api/alerts (15s poll) feeds:
     - a PERSISTENT banner while any critical alert is active (naked leg
       with no levels, loss guard BLOCKED) — dismissed per alert-id and
       re-shown if the id CHANGES (new incident ≠ acknowledged one),
     - transient toasts for warnings (software-only legs, digest fail). */
const _alertState = { seen: {}, dismissed: {}, bannerIds: [] };

function startAlertPolling() {
  if (_alertState.started) return;
  _alertState.started = true;
  pollAlerts();
  setInterval(pollAlerts, 15000);
}

async function pollAlerts() {
  let d;
  try { d = await api("/api/alerts"); } catch { return; }  // offline → silent
  const alerts = d.alerts || [];
  const host = $("toastHost"), banner = $("alertBanner");
  if (!host || !banner) return;

  for (const a of alerts) {
    if (_alertState.seen[a.id] || _alertState.dismissed[a.id]) continue;
    _alertState.seen[a.id] = true;
    if (a.severity === "critical") continue;  // criticals go to the banner
    toast(`[!] ${a.msg}`, "warn");
  }
  for (const id of Object.keys(_alertState.seen)) {
    if (!alerts.some((a) => a.id === id)) {
      delete _alertState.seen[id];           // condition cleared
      delete _alertState.dismissed[id];      // re-arm for a future repeat
    }
  }

  const criticals = alerts.filter((a) => a.severity === "critical");
  _alertState.bannerIds = criticals.map((a) => a.id);
  // Dismiss must STICK while the incident is active: only a NEW critical id
  // (or the condition clearing and re-appearing) may bring the banner back.
  const visibleCriticals = criticals.filter((a) => !_alertState.dismissed[a.id]);
  if (visibleCriticals.length) {
    banner.style.display = "flex";
    banner.className = "alert-banner" + (criticals.some((a) => a.kind === "loss_guard") ? " guard" : "");
    banner.innerHTML =
      `<span class="ab-icon">&#9888;</span>` +
      `<div class="ab-body">` + criticals.map((a) =>
        `<div class="ab-line"><b>${esc(a.symbol || a.kind)}</b> ${esc(a.msg)}</div>`).join("") +
      `</div>` +
      `<button class="ab-dismiss" title="Hide until this clears">Dismiss</button>`;
    banner.querySelector(".ab-dismiss").onclick = () => {
      for (const a of criticals) _alertState.dismissed[a.id] = true;
      banner.style.display = "none";
    };
  } else if (_alertState.bannerIds.length === 0 ||
             criticals.length === visibleCriticals.length) {
    banner.style.display = "none";
  }
}

function toast(msg, kind) {
  const host = $("toastHost");
  if (!host) return;
  const el = document.createElement("div");
  el.className = `toast ${kind === "warn" ? "toast-warn" : "toast-info"}`;
  el.textContent = msg;
  host.appendChild(el);
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 400); }, 8000);
}

/* ── Orders view: big position chart (entry / SL / TP from the journal) ── */
let ordChart = null;
let pendingOrdCandles = null;
let ordInterval = "1h";

function _ordLevels(symbol) {
  const o = lastOrders.find((x) => x.symbol === symbol
    && (x.status === "filled" || x.status === "open")
    && (x.entry || x.fill_price));
  if (!o) return null;
  return {
    direction: o.side === "LONG" ? "LONG" : "SHORT",
    entry: o.entry || o.fill_price,
    stop_loss: o.stop_loss || null,
    take_profit: o.take_profit || null,
  };
}

function renderOrdChart(d) {
  const el = $("ordChart");
  if (!el) return;
  if (!ordChart) ordChart = echarts.init(el);
  renderCandleChart(d, ordChart, $("ordChartTitle"), $("ordLevelLegend"), _ordLevels(d.symbol));
  bindLevelDragging(ordChart, "orders");
}

/* ── TradingView-style draggable Entry / SL / TP levels ─────────────
   Hover a level line → it becomes grabbable; drag vertically → a preview
   label shows the candidate price; release → POST /api/positions/levels
   persists to the journal and pushes SL/TP to the real MT5 position.
   Signal-chart drags only preview (no position to amend) unless the
   symbol matches an open position. */
const _drag = { active: false, chart: null, view: null, key: null, y0: 0, price0: 0, price: 0, order: null };

function _draggableLevels(view) {
  if (view === "orders") {
    const o = lastOrders.find((x) => x.symbol === state.ordSymbol
      && (x.status === "filled" || x.status === "open"));
    return o ? { order: o, levels: _ordLevels(o.symbol) || {} } : null;
  }
  const o = lastOrders.find((x) => x.symbol === state.sigSymbol
    && (x.status === "filled" || x.status === "open"));
  const sig = (state.signals || []).find((s) => s.symbol === state.sigSymbol) || {};
  return { order: o || null, levels: o ? (_ordLevels(o.symbol) || {}) : { entry: sig.entry, stop_loss: sig.stop_loss, take_profit: sig.take_profit } };
}

function bindLevelDragging(chart, view) {
  const el = chart.getDom();
  if (el._lvlDragBound) return;
  el._lvlDragBound = true;
  // NOTE: no overlay div — a full-chart overlay blocked tooltips and zoom.
  // Events bubble off the canvas to `el`, so proximity-detect there and
  // only grab when the pointer is within 6px of a level line.
  const hit = (evt) => {
    const ctx = _draggableLevels(view);
    if (!ctx || !ctx.levels) return null;
    const rect = el.getBoundingClientRect();
    const y = evt.clientY - rect.top;
    for (const [key, val] of Object.entries(ctx.levels)) {
      if (val == null) continue;
      const py = chart.convertToPixel({ yAxisIndex: 0 }, val);
      if (py != null && Math.abs(py - y) <= 6) return { key, val, order: ctx.order };
    }
    return null;
  };
  el.addEventListener("mousemove", (evt) => {
    if (_drag.active) return;
    el.style.cursor = hit(evt) ? "ns-resize" : "";
  });
  el.addEventListener("mouseleave", () => { if (!_drag.active) el.style.cursor = ""; });
  el.addEventListener("mousedown", (evt) => {
    const h = hit(evt);
    if (!h) return;
    evt.preventDefault();
    evt.stopPropagation();
    _drag.active = true; _drag.chart = chart; _drag.view = view;
    _drag.key = h.key; _drag.order = h.order;
    _drag.y0 = evt.clientY; _drag.price0 = h.val; _drag.price = h.val;
  });
  window.addEventListener("mousemove", (evt) => {
    if (!_drag.active || _drag.chart !== chart) return;
    const p = _priceFromClientY(chart, el, evt.clientY);
    if (p == null) return;
    _drag.price = p;
    _drawDragPreview(chart, { key: _drag.key, price: p });
  });
  window.addEventListener("mouseup", async (evt) => {
    if (!_drag.active || _drag.chart !== chart) return;
    const drag = { ..._drag };
    _drag.active = false;
    _clearDragPreview(chart);
    const moved = Math.abs(drag.price - drag.price0) / drag.price0 > 0.0002;
    if (!moved || drag.price == null || !isFinite(drag.price)) { _refreshChartLevels(chart, view); return; }
    const body = { symbol: drag.view === "orders" ? state.ordSymbol : state.sigSymbol,
                   stop_loss: drag.key === "stop_loss" ? drag.price : undefined,
                   take_profit: drag.key === "take_profit" ? drag.price : undefined,
                   entry: drag.key === "entry" ? drag.price : undefined,
                   order_id: drag.order ? drag.order.id : null };
    try {
      const res = await api("/api/positions/levels", { method: "POST", body: JSON.stringify(body) });
      _levelToast(`SL/TP ${res.broker_push ? "saved + pushed to MT5" : "saved to journal"}`);
    } catch (e) {
      _levelToast(`Level save failed — ${e.message}`, true);
    }
    if (view === "orders") loadOrders(); else loadSigChart();
  });
}

let _lvlToastTimer = null;
function _levelToast(msg, isErr) {
  let t = document.getElementById("lvlToast");
  if (!t) { t = document.createElement("div"); t.id = "lvlToast"; document.body.appendChild(t); }
  t.textContent = msg;
  t.className = "lvl-toast show" + (isErr ? " err" : "");
  clearTimeout(_lvlToastTimer);
  _lvlToastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

function _dragMarkline(key, price, color) {
  return { yAxis: price, name: "drag:" + key,
           lineStyle: { color, type: "dashed", width: 2, opacity: 0.95 },
           label: { show: true, formatter: `${key === "entry" ? "◆ Entry" : key === "stop_loss" ? "▼ SL" : "▲ TP"} ${fmtPrice(price)} (drop to save)`,
                    color, fontSize: 11, position: "insideEndTop" },
           silent: true };
}
// convertFromPixel returns an array for category axes — normalise to scalar.
function _priceFromClientY(chart, el, clientY) {
  const rect = el.getBoundingClientRect();
  const v = chart.convertFromPixel({ yAxisIndex: 0 }, clientY - rect.top);
  const p = Array.isArray(v) ? v[1] : v;
  return (p == null || !isFinite(p)) ? null : p;
}

function _drawDragPreview(chart, preview) {
  const opt = chart.getOption();
  if (!opt || !opt.series || !opt.series[0]) return;
  const k = JSON.parse(JSON.stringify(opt.series[0]));
  const others = (k.markLine?.data || []).filter((m) => !String(m.name || "").startsWith("drag:"));
  const colors = { entry: "#f59e0b", stop_loss: "#ef4444", take_profit: "#22c55e" };
  k.markLine = { silent: true, symbol: "none", animation: false,
                 data: [...others, _dragMarkline(preview.key, preview.price, colors[preview.key] || "#38bdf8")] };
  chart.setOption({ series: [{ id: opt.series[0].id, markLine: k.markLine }] });
}

function _clearDragPreview(chart) {
  try { _refreshChartLevels(chart); } catch (_) {}
}

function _refreshChartLevels(chart, view) {
  const el = chart.getDom();
  if (el && el.id === "ordChart") loadOrdChart(); else if (el && el.id === "sigChart") loadSigChart();
}

async function loadOrdChart() {
  const el = $("ordChart");
  if (!el) return;
  const sym = state.ordSymbol || "BTC/USDT";
  try {
    const qs = new URLSearchParams({ symbol: sym, interval: ordInterval, limit: "400" });
    const d = await api("/api/candles?" + qs);
    if (el.clientWidth === 0) { pendingOrdCandles = d; return; }  // view hidden — render on show
    renderOrdChart(d);
  } catch (e) {
    const legend = $("ordLevelLegend");
    if (legend) legend.textContent = `Chart unavailable — ${e.message}`;
  }
}

function bindOrdTfPills() {
  document.querySelectorAll("#ordTfPills .tf-pill").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll("#ordTfPills .tf-pill").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      ordInterval = b.dataset.interval;
      loadOrdChart();
    });
  });
}

function sparkline(values, w = 90, h = 28) {
  if (!values || values.length < 2) return '<span class="muted">—</span>';
  const min = Math.min(...values), max = Math.max(...values);
  const rng = (max - min) || 1;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * (w - 2) + 1;
    const y = h - 2 - ((v - min) / rng) * (h - 4);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const up = values[values.length - 1] >= values[0];
  const color = up ? "var(--green, #2ecc8f)" : "var(--red, #ff5c5c)";
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" class="spark">` +
    `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5"/></svg>`;
}

function renderClosedTrades(orders) {
  const box = $("closedTradesList"), meta = $("closedTradesMeta");
  if (!box) return;
  const closed = orders.filter((o) => o.status === "closed");
  if (meta) meta.textContent = `${closed.length} trade${closed.length === 1 ? "" : "s"} \u00b7 ${_periodLabel()}`;
  if (!closed.length) {
    box.innerHTML = '<div class="muted">No closed trades in this period.</div>';
    return;
  }
  const shown = closed.slice(0, 200);
  const more = closed.length > shown.length ? `<div class="muted" style="margin-top:8px">\u2026 +${closed.length - shown.length} more (widen the date range to see them)</div>` : "";
  box.innerHTML = shown.map((o) => {
    const long = o.side === "LONG";
    const pnlCls = o.pnl > 0 ? "up" : o.pnl < 0 ? "down" : "";
    const exit = o.exit_price != null ? fmtPrice(o.exit_price) : "\u2014";
    const t = String(o.exit_time || o.created_at || "").slice(0, 16).replace("T", " ");
    const broker = _brokerOf(o);
    const brokerTag = _brokerTagHtml(broker);
    return `<div class="closed-trade">
      ${brokerTag}
      <span class="sym">${symOf(o.symbol)}</span>
      <span class="signal-badge ${long ? "badge-long" : "badge-short"}">${long ? "\u25B2" : "\u25BC"}</span>
      <span class="muted">${fmt(o.qty, 2)}</span>
      <span class="muted">${fmtPrice(o.entry)} \u2192 ${exit}${o.exit_reason ? ` <span class="muted">(${esc(o.exit_reason)})</span>` : ""}</span>
      <span class="ct-pnl ${pnlCls}">${o.pnl > 0 ? "+" : ""}${fmt(o.pnl, 2)}</span>
      <span class="muted">${esc(t)}</span>
    </div>`;
  }).join("") + more;
}

function renderOrders(orders, summary) {
  // Journal table + strip only — the closed-trades donut/list are rendered
  // by loadClosedTrades() on the exit-time window (see _closedRangeQuery).
  const b = $("ordersBody");
  if (!b) return;
  const strip = $("journalSummary");
  if (strip) {
    if (summary) {
      const pnlCls = summary.realized_pnl > 0 ? "up" : summary.realized_pnl < 0 ? "down" : "";
      const unCls = (summary.unrealized_pnl || 0) > 0 ? "up" : (summary.unrealized_pnl || 0) < 0 ? "down" : "";
      const sign = (v) => (v > 0 ? "+" : v < 0 ? "\u2212" : "");
      strip.innerHTML =
        `<div class="jstat"><span class="jstat-label">Open positions</span><span class="jstat-value">${summary.open_positions}</span></div>` +
        `<div class="jstat"><span class="jstat-label">Open mkt value</span><span class="jstat-value">$${fmtMoney(summary.open_market_value)}</span></div>` +
        `<div class="jstat"><span class="jstat-label">Unrealized PnL</span><span class="jstat-value ${unCls}">${sign(summary.unrealized_pnl || 0)}$${fmtMoney(Math.abs(summary.unrealized_pnl || 0))}</span></div>` +
        `<div class="jstat"><span class="jstat-label">Realized PnL</span><span class="jstat-value ${pnlCls}">${sign(summary.realized_pnl)}$${fmtMoney(Math.abs(summary.realized_pnl))}</span></div>` +
        (summary.dry_run ? `<div class="jstat"><span class="jstat-label">Dry-run</span><span class="jstat-value">${summary.dry_run}</span></div>` : "");
    } else {
      strip.innerHTML = "";
    }
  }
  if (!orders.length) {
    // Honest empty state (2026-10-07): the journal is windowed by OPEN time,
    // so "today" legitimately shows zero rows when no NEW trade was opened
    // today — but positions may still have CLOSED today (SL/TP/time_decay
    // close them from the MT5 manage loop). "No orders yet" read as "the bot
    // did nothing", which doomed trust. Say what the empty actually means.
    const _pl = _periodLabel();
    b.innerHTML = `<tr><td colspan="13" class="muted">No new trades OPENED ${_pl === "today" ? "today" : `in ${_pl}`}`
      + ` — check “Closed trades” below for exits in this window (exits are listed by CLOSE time).</td></tr>`;
    return;
  }
  b.innerHTML = orders.map((o) => {
    const long = o.side === "LONG";
    const pnl = o.pnl == null ? "\u2014" : `${o.pnl > 0 ? "+" : ""}${fmt(o.pnl, 2)}`;
    const pnlCls = o.pnl > 0 ? "up" : o.pnl < 0 ? "down" : "";
    const t = String(o.fill_time || o.created_at || "").slice(0, 19).replace("T", " ");
    const sl = o.stop_loss != null ? fmtPrice(o.stop_loss) : "\u2014";
    const tp = o.take_profit != null ? fmtPrice(o.take_profit) : "\u2014";
    const broker = _brokerOf(o);
    const brokerTag = _brokerTagHtml(broker);
    // Why column: show full reason with tooltip
    const reasons = String(o.reasons || "");
    const whyShort = reasons ? reasons.split(";")[0].trim().slice(0, 60) : "\u2014";
    const whyFull = reasons ? esc(reasons.replace(/;/g, " \u00b7 ")) : "\u2014";
    const why = `<span class="why-text" title="${whyFull}">${esc(whyShort)}${reasons.length > 60 ? " \u2026" : ""}</span>`;
    // Status with error tooltip
    const statusBadge = `<span class="signal-badge ${ORDER_BADGES[o.status] || "badge-hold"}"${o.error ? ` title="${esc(o.error)}"` : ""}>${esc(o.status)}</span>`;
    const exitCell = o.exit_price != null 
      ? `${fmtPrice(o.exit_price)}${o.exit_reason ? ` <span class="muted">(${esc(o.exit_reason)})</span>` : ""}`
      : "\u2014";
    return `<tr>
      <td class="sym">${symOf(o.symbol)}</td>
      <td><span class="signal-badge ${long ? "badge-long" : "badge-short"}">${long ? "\u25B2 LONG" : "\u25BC SHORT"}</span></td>
      <td class="num">${fmt(o.qty, 4)}</td>
      <td class="num">${fmtPrice(o.entry)}</td>
      <td class="num muted">${sl}</td>
      <td class="num muted">${tp}</td>
      <td class="num">${exitCell}</td>
      <td class="num ${pnlCls}">${pnl}</td>
      <td>${statusBadge}</td>
      <td>${brokerTag}</td>
      <td class="why-cell">${why}</td>
      <td class="muted time-cell">${esc(t)}</td>
      <td class="act-cell">${_rowActionHtml(o)}</td>
    </tr>`;
  }).join("");
}

/* ── AI signals: what the bot asked for this cycle ────────────────
   The AI decides and the gates dispose. Every row is the AI's OWN opinion
   for one symbol (direction, confidence, the levels it proposed, the
   6-layer scores) plus the gate that decided its fate, so a trader can TAKE
   a trade the AI asked for through the same manual/execution path. */
const AI_GATE_LABELS = {
  no_signal_ai: "AI had no opinion (HOLD)",
  data: "data gate — candles stale or thin",
  loss_guard: "loss guard blocked new entries",
  circuit_breaker: "circuit breaker at full stop",
  not_on_broker: "not tradable on this broker",
  no_short_on_spot: "spot broker cannot short",
  cross_source: "cross-source price disagreed",
  intent: "intent re-gate failed",
  session: "session filter",
  caps: "exposure caps reached",
  cooldown: "re-entry cooldown",
  duplicate: "already open",
};

let _lastAiSignals = [];

function _aiGateLabel(o) {
  if (o.status === "executed") return "taken by the bot";
  if (!o.gate) return "available now";
  return AI_GATE_LABELS[o.gate] || o.gate;
}

function renderAiSignals(s) {
  const box = $("aiSigBody");
  const meta = $("aiSigMeta");
  if (!box) return;
  const c = s && s.last_cycle;
  const ops = (c && Array.isArray(c.opinions)) ? c.opinions : null;
  if (!ops) {
    if (meta) meta.textContent = "no cycle summary yet";
    box.innerHTML = '<div class="muted">Waiting for the bot to finish a cycle — the AI\'s opinions for every symbol appear here.</div>';
    return;
  }
  _lastAiSignals = ops;
  const age = s.last_run_at ? (Date.now() / 1000 - s.last_run_at) : Infinity;
  const stale = age > 2 * (s.loop_seconds || 300);
  if (meta) {
    meta.textContent = ops.length
      ? `${ops.length} opinion${ops.length === 1 ? "" : "s"} · ${c.opinions_executed || 0} taken · ${c.opinions_held || 0} held · as of ${_ago(s.last_run_at)}`
      : `no AI opinion this cycle · as of ${_ago(s.last_run_at)}`;
  }
  if (!ops.length) {
    box.innerHTML = '<div class="muted">The AI had no opinion above its confidence floor this cycle — quiet by design, nothing to take.</div>';
    return;
  }
  box.innerHTML = (stale
      ? `<div class="ai-sig-stale">This summary is ${_ago(s.last_run_at)} old — the proposed levels may have moved since. Run a cycle to refresh before taking one.</div>`
      : "")
    + ops.map((o, i) => {
      const long = o.direction === "LONG";
      const held = o.status !== "executed";
      const lv = [
        o.entry ? `entry ${fmtPrice(o.entry)}` : "market entry",
        o.stop_loss ? `SL ${fmtPrice(o.stop_loss)}`
          : (o.stop_loss_pct ? `SL ≈−${fmt(o.stop_loss_pct * 100, 1)}%` : "SL from ATR"),
        o.take_profit ? `TP ${fmtPrice(o.take_profit)}`
          : (o.take_profit_pct ? `TP ≈+${fmt(o.take_profit_pct * 100, 1)}%` : "TP from ATR"),
      ].join(" · ");
      const chips = [
        o.confirm_score ? `confirm ${o.confirm_score}` : null,
        o.dxy_score ? `DXY ${o.dxy_score}` : null,
        o.smc_score ? `SMC ${o.smc_score}` : null,
        o.regime || null, o.session || null,
      ].filter(Boolean).map((t) => `<span class="ai-chip">${esc(t)}</span>`).join("");
      const why = (o.reasons || []).slice(0, 2).map((r) => esc(String(r).slice(0, 80))).join(" · ");
      const full = esc((o.reasons || []).join(" · "));
      return `<div class="ai-sig-row${held ? "" : " ai-sig-done"}">
        <span class="sym">${symOf(o.symbol)}</span>
        <span class="signal-badge ${long ? "badge-long" : "badge-short"}">${long ? "▲" : "▼"} ${esc(o.direction)}</span>
        <span class="ai-sig-conf" title="AI confidence">${fmt(o.confidence, 0)}%</span>
        <span class="ai-sig-lv">${lv}</span>
        <span class="ai-sig-chips">${chips}</span>
        <span class="ai-sig-gate ${held ? "held" : "taken"}">${esc(_aiGateLabel(o))}</span>
        ${held ? `<button type="button" class="btn-take" data-take="${i}" title="Load this trade into the manual panel">Take this</button>` : ""}
        ${why ? `<span class="ai-sig-why muted" title="${full}">${why}</span>` : ""}
      </div>`;
    }).join("");
}

// Load an AI signal into the manual panel: same symbol, side and levels the AI
// proposed. The trader still presses Place — the AI's opinion is an input to a
// decision, not a licence to fire.
function takeAiSignal(i) {
  const o = _lastAiSignals[i];
  if (!o) return;
  const card = document.querySelector(".manual-card");
  if (card) card.scrollIntoView({ block: "center", behavior: "smooth" });
  if (manPicker) manPicker.select(o.symbol);
  _manSetSide(o.direction);
  const set = (id, v) => { const el = $(id); if (el) el.value = (v == null || v === "") ? "" : String(v); };
  set("manEntry", o.entry);
  set("manSL", o.stop_loss);
  set("manTP", o.take_profit);
  set("manQty", "");
  _manSetMsg(`Loaded the AI's ${o.direction} ${symOf(o.symbol)} (confidence ${fmt(o.confidence, 0)}%)`
    + `${o.entry ? ` at ${fmtPrice(o.entry)}` : " at market"} — check the levels, then place.`, "ok");
}

/* ── Manual trading ──────────────────────────────────────────────
   A trader must be able to ACT from the dashboard, not only watch the AI
   trade. POST /api/manual/order and POST /api/manual/close/{id} ride the
   bot's own execution path server-side (routing, risk sizing with the
   trader's SL/TP, the R/R floor, balance precheck, venue-side OCO / SL-TP
   protection, and the trade journal). Closing goes through close_position,
   so the bracket is stood down and PnL is realized. */
let manPicker = null;
let _manSide = "LONG";

// POST helper that SURFACES the server's error text (the shared api() helper
// throws on status only, which would hide "LONG stop_loss must be BELOW the
// entry price"). Never auto-retries: an order must not be placed twice by a
// silent retry.
async function _manPost(path, payload) {
  const res = await fetch(path, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  let body = null;
  try { body = await res.json(); } catch (_) { /* non-JSON body */ }
  if (!res.ok) {
    const msg = (body && (body.error || body.detail)) || `HTTP ${res.status}`;
    throw new Error(Array.isArray(msg) ? (msg[0] && msg[0].msg) || "invalid request" : msg);
  }
  return body || {};
}

function _manSetMsg(text, kind) {
  const el = $("manMsg");
  if (!el) return;
  el.textContent = text || "";
  el.className = "mt-msg " + (kind === "err" ? "mt-err" : kind === "ok" ? "mt-ok" : "muted");
}

function _manNum(id) {
  const el = $(id);
  if (!el) return null;
  const v = String(el.value == null ? "" : el.value).trim();
  if (!v) return null;
  const n = Number(v);
  return isFinite(n) ? n : null;
}

function _manSymbol() {
  return (manPicker && manPicker.value) || state.ordSymbol || "BTC/USDT";
}

/* Only a live position can be closed — pending/rejected rows show nothing. */
function _rowActionHtml(o) {
  if (o.status !== "filled" && o.status !== "open") return '<span class="muted">—</span>';
  return `<button type="button" class="btn-close-row" data-close-id="${o.id}"
    title="Close ${esc(symOf(o.symbol))} #${o.id} at market through the bot's close path">Close</button>`;
}

/* One place that owns the selected side: the toggle, its aria state and the
   Place button's own label move together. The button saying "Place SHORT
   order" is the guard against the one bug that costs real money here — a
   panel that LOOKS long while the request is short. */
function _manSetSide(side) {
  _manSide = side === "SHORT" ? "SHORT" : "LONG";
  const wrap = $("manSide");
  if (wrap) {
    wrap.querySelectorAll(".mt-side-btn").forEach((x) => {
      const on = x.dataset.side === _manSide;
      x.classList.toggle("active", on);
      x.setAttribute("aria-checked", on ? "true" : "false");
    });
  }
  const lbl = $("manPlaceLabel");
  if (lbl) lbl.textContent = `Place ${_manSide} order`;
}

function bindManualTrade() {
  const sideWrap = $("manSide");
  if (sideWrap) {
    sideWrap.addEventListener("click", (e) => {
      const b = e.target.closest(".mt-side-btn");
      if (b) _manSetSide(b.dataset.side);
    });
  }
  // "Take this" on an AI signal loads it into this panel.
  const aiBody = $("aiSigBody");
  if (aiBody) {
    aiBody.addEventListener("click", (e) => {
      const b = e.target.closest("[data-take]");
      if (b) takeAiSignal(Number(b.dataset.take));
    });
  }
  // Close buttons live inside rows that re-render every poll — delegate.
  const body = $("ordersBody");
  if (body) {
    body.addEventListener("click", (e) => {
      const b = e.target.closest("[data-close-id]");
      if (b) closeManualOrder(Number(b.dataset.closeId), b);
    });
  }
  // Enter inside any manual field places the order (a trader's keyboard flow).
  ["manQty", "manEntry", "manSL", "manTP"].forEach((id) => {
    const el = $(id);
    if (el) el.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); placeManualOrder(); }
    });
  });
}

async function placeManualOrder() {
  const btn = $("btnPlaceManual");
  const symbol = _manSymbol();
  const dry = !!($("manDry") && $("manDry").checked);
  if (!symbol) { _manSetMsg("Pick an asset first.", "err"); return; }
  const payload = {
    symbol,
    side: _manSide,
    qty: _manNum("manQty"),
    entry: _manNum("manEntry"),
    stopLoss: _manNum("manSL"),
    takeProfit: _manNum("manTP"),
    dry,
  };
  // Mirror the server's bracket rule BEFORE the round trip, and cover the
  // half it does not check: a stop on the wrong side of the entry silently
  // inverts the risk of the trade, and a take-profit behind the entry books
  // a "win" at a loss. The button label and this check are the two places
  // the side can be contradicted, and both are loud on purpose.
  if (payload.entry && payload.stopLoss) {
    const wrong = _manSide === "LONG" ? payload.stopLoss >= payload.entry
                                       : payload.stopLoss <= payload.entry;
    if (wrong) {
      _manSetMsg(`A ${_manSide} stop must sit ${_manSide === "LONG" ? "BELOW" : "ABOVE"} the entry — `
        + `${fmtPrice(payload.stopLoss)} is on the wrong side of ${fmtPrice(payload.entry)}. Nothing sent.`, "err");
      return;
    }
  }
  if (payload.entry && payload.takeProfit) {
    const wrong = _manSide === "LONG" ? payload.takeProfit <= payload.entry
                                       : payload.takeProfit >= payload.entry;
    if (wrong) {
      _manSetMsg(`A ${_manSide} target must sit ${_manSide === "LONG" ? "ABOVE" : "BELOW"} the entry — `
        + `${fmtPrice(payload.takeProfit)} would bank a ${_manSide} loss as a win. Nothing sent.`, "err");
      return;
    }
  }
  if (btn) { btn.disabled = true; }   // double-click must not double-place    _manSetMsg(dry ? `Checking sizing for ${symOf(symbol)} (no order — preview only)…` : `Placing ${_manSide} ${symOf(symbol)} on the account…`);
  try {
    const res = await _manPost("/api/manual/order", payload);
    const o = res.order || {};
    const ref = o.id == null ? "" : ` #${o.id}`;   // dry-run previews have no row id
    _manSetMsg(
      `${dry ? "Sizing preview" : "Order placed on the account"} —${ref || ""} `
      + `${symOf(o.symbol || symbol)} ${o.side || _manSide} qty ${fmt(o.qty, 4)} @ ${fmtPrice(o.entry || o.fill_price)}`
      + (o.stop_loss ? ` · SL ${fmtPrice(o.stop_loss)}` : "")
      + (o.take_profit ? ` · TP ${fmtPrice(o.take_profit)}` : "")
      + (res.duplicate ? " · already placed in this minute — not duplicated" : "")
      + (dry ? " · nothing sent to a broker, no trade row" : ""),
      "ok");
    toast(`${dry ? "Sizing preview" : "Order placed"}: ${symOf(o.symbol || symbol)} ${o.side || _manSide}${ref}`, "info");
    loadOrders();
    loadLiveTrades();
  } catch (e) {
    _manSetMsg(`Rejected — ${e.message}`, "err");
    toast(`Manual order rejected — ${e.message}`, "warn");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function closeManualOrder(id, btn) {
  const o = (lastOrders || []).find((x) => x.id === id);
  const label = o ? `${symOf(o.symbol)} ${o.side} #${id}` : `#${id}`;
  if (!window.confirm(`Close ${label} at market?\n\nThis exits through the bot's close path: the venue bracket is stood down and PnL is realized.`)) return;
  const prev = btn ? btn.textContent : null;
  if (btn) { btn.disabled = true; btn.textContent = "Closing…"; }
  _manSetMsg(`Closing ${label}…`);
  try {
    const res = await _manPost(`/api/manual/close/${id}`, {});
    const pnl = res.pnl;
    _manSetMsg(
      `Closed ${label} — PnL ${pnl == null ? "—" : `${pnl > 0 ? "+" : ""}${fmt(pnl, 2)}`}`
      + (res.reason ? ` (${res.reason})` : ""),
      "ok");
    toast(`Closed ${label}`, "info");
    loadOrders();
    loadLiveTrades();
  } catch (e) {
    _manSetMsg(`Close failed — ${e.message}`, "err");
    toast(`Close failed — ${e.message}`, "warn");
    if (btn) { btn.disabled = false; btn.textContent = prev || "Close"; }
  }
}

// ── Profit by currency (donut per broker — driven by the registry) ──
const pnlCharts = new Map();  // brokerKey → echarts instance
// Broker registry — mirrored from the backend (/api/broker-accounts → brokers[])
// so the UI needs ZERO edits when an account is added server-side. The static
// list below is only the pre-fetch fallback.
const _BROKER_FALLBACK = [
  { key: "mt5", name: "Exness · MT5", letter: "M", color: "#0ecb81" },
  { key: "binance", name: "Binance", letter: "B", color: "#f5b50a" },
  { key: "alpaca", name: "Alpaca (paper)", letter: "A", color: "#ffd03e" },
];
let _brokers = null;  // live registry once fetched
function _brokerList() { return _brokers || _BROKER_FALLBACK; }
function _brokerMeta(key) {
  return _brokerList().find((b) => b.key === key)
    || { key, name: key, letter: (key[0] || "?").toUpperCase(), color: "#8a94a6" };
}
function _brokerTagHtml(broker, extra) {
  const m = _brokerMeta(broker);
  return `<span class="broker-tag" style="background:${m.color}22;color:${m.color};border:1px solid ${m.color}55" title="${m.name}">${m.letter}</span><span class="lt-broker-name">${m.name}</span>`;
}
async function loadBrokerRegistry() {
  try {
    const { brokers } = await api("/api/broker-accounts");
    if (Array.isArray(brokers) && brokers.length) {
      _brokers = brokers.map((b) => ({ key: b.key, name: b.name, letter: b.letter, color: b.color }));
      renderBrokerDonutPanes();  // new account → new donut pane without code changes
    }
  } catch (_) { /* fallback list already in place */ }
}
function renderBrokerDonutPanes() {
  const wrap = document.querySelector(".dual-donut-wrap");
  if (!wrap) return;
  wrap.innerHTML = _brokerList().map((b) => `
    <div class="donut-pane">
      <div class="donut-pane-label"><span class="broker-dot" style="background:${b.color};box-shadow:0 0 4px ${b.color}88"></span> ${b.name}</div>
      <div id="pnlDonut_${b.key}" class="chart pnl-donut"></div>
    </div>`).join("");
  // stale chart instances point at replaced DOM nodes — drop them so the
  // next renderPnlDonut() re-inits on the fresh elements
  pnlCharts.clear();
}

function _brokerOf(order) {
  // Which broker a journal row belongs to. Registry-driven: an exact source
  // match on any registry key wins; legacy rows with source "hybrid"/empty
  // fall back to the routing heuristic (crypto→binance, else mt5). New
  // accounts need no change here — their journal rows carry their key.
  const src = (order.source || "").toLowerCase();
  if (src && src !== "hybrid") {
    const exact = _brokerList().find((b) => b.key === src);
    if (exact) return exact.key;
    if (src.includes("binance")) return "binance";
    if (src.includes("mt5") || src.includes("exness")) return "mt5";
    if (src.includes("alpaca")) return "alpaca";
  }
  // Routing heuristic for legacy/unattributed rows
  const sym = (order.symbol || "").toUpperCase();
  if (sym.endsWith("USDT") || sym.endsWith("/USDT")) return "binance";
  return "mt5";
}

// Shade-varied palette so adjacent slices are distinguishable (all-wins used
// to be one flat green with 16 external labels — unreadable).
function _pnlShade(i, pnl) {
  const greens = ["#16a34a", "#22c55e", "#2ecc8f", "#48caa8", "#6dd6bb", "#93e2cf", "#b9ece0"];
  const reds = ["#dc2626", "#ef4444", "#ff5c5c", "#ff7d7d", "#ff9e9e", "#ffbcbc", "#ffd6d6"];
  const pal = pnl >= 0 ? greens : reds;
  return pal[i % pal.length];
}

// Dark text on light shades, white on dark ones (inside ring labels).
function _txtColorFor(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return "#0b1020";
  const n = parseInt(m[1], 16);
  const lum = 0.2126 * ((n >> 16) & 255) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255);
  return lum > 150 ? "#0b1020" : "#ffffff";
}

function _renderOneDonut(chart, el, rows, total, label, colorOf) {
  if (!rows.length) {
    // Keep the card informative instead of a blank canvas (Binance side). 
    if (!chart) chart = echarts.init(el);
    chart.setOption({
      backgroundColor: "transparent",
      title: {
        text: "$0", subtext: `${label} — no trades in period`,
        left: "center", top: "37%",
        textStyle: { color: "#8a94a6", fontSize: 18, fontWeight: 700 },
        subtextStyle: { color: "#8a94a6", fontSize: 10 },
      },
      series: [],
    }, true);
    return chart;
  }
  if (!chart) {
    chart = echarts.init(el);
  }
  const totalCls = total >= 0 ? "#2ecc8f" : "#ff5c5c";
  // Group micro-slices into one neutral "Other" slice for the RING ONLY
  // (the breakdown list below still lists every currency). Without this,
  // 20+ tiny slices render as an unreadable fan of dark slivers at the
  // top of the ring — minAngle keeps them visible but they pile up into
  // black spokes (screenshot 2026-10-06).
  const gross = rows.reduce((a, r) => a + Math.abs(r.pnl), 0);
  let vis = rows;
  if (rows.length > 6 && gross > 0) {
    const big = rows.filter((r) => Math.abs(r.pnl) / gross >= 0.03);
    const small = rows.filter((r) => Math.abs(r.pnl) / gross < 0.03);
    if (small.length > 1 && big.length) {
      vis = big.concat({
        symbol: `other:${small.length}`,
        name: `Other \u00d7${small.length}`,
        pnl: small.reduce((a, r) => a + r.pnl, 0),
        _otherCount: small.length,
      });
    }
  }
  chart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "item", backgroundColor: "#14161c", borderColor: "#24262e",
      textStyle: { color: "#e6e9f0" },
      formatter: (p) => {
        const v = (p.data && p.data.pnl != null) ? p.data.pnl : p.value;
        return `${p.name}: ${v >= 0 ? "+" : "\u2212"}$${fmt(Math.abs(v), 0)} (${p.percent}% of gross)`;
      },
    },
    title: {
      text: `${total >= 0 ? "+" : "\u2212"}$${fmt(Math.abs(total), 0)}`,
      // Single-slice ring: the only currency's name belongs in the center
      // (an "X 100%" label inside a full circle is noise).
      subtext: vis.length === 1 ? `${vis[0].name || _compName(vis[0])} \u00b7 ${label}` : label,
      left: "center", top: "38%",
      textStyle: { color: totalCls, fontSize: 20, fontWeight: 700 },
      subtextStyle: { color: "#8a94a6", fontSize: 10 },
    },
    series: [{
      type: "pie", radius: ["44%", "70%"], center: ["50%", "50%"],
      avoidLabelOverlap: true,
      minAngle: 2, // keep sub-2% slivers visible instead of vanishing hairlines
      // A one-slice pie is a full circle — a border paints an ugly seam
      // notch at 12 o'clock; multi-slice rings keep the separation.
      itemStyle: { borderColor: "#121223", borderWidth: vis.length === 1 ? 0 : 2 },
      // Many-asset readability: no external labels at all (with 10+ currencies
      // the leader lines crossed into an unreadable web). Slices ≥7% carry a
      // compact percent INSIDE the ring; symbol names live in the tooltip and
      // the breakdown list below, which doubles as the legend.
      label: {
        position: "inside", fontSize: 11, fontWeight: 600,
        color: "#ffffff",   // per-slice override lives on the data item
        // Dark outline separates the label from its slice whatever the
        // shade — red-on-red / navy-on-green unreadable cases (screenshot
        // 2026-10-06) are impossible with a border.
        textBorderColor: "#121223", textBorderWidth: 2,
        // Percent ONLY, and only when the slice has room (2026-10-08). Names
        // inside the ring collided with each other and with their own slice
        // (a red "BTC 11%" on the red loss slice was unreadable) — the legend
        // below carries every name, value and share, and the tooltip has the
        // rest. A small slice simply stays a clean colour.
        formatter: (p) => {
          if (p.percent < 9 || p.percent >= 99.5) return "";  // full-circle: name is in the center
          return `${Math.round(p.percent)}%`;
        },
      },
      labelLine: { show: false },
      data: vis.map((r) => {
        // "Other" gets a quiet grey — it is aggregation, not a position.
        const col = r._otherCount ? "#3a4150"
          : (colorOf && colorOf(symOf(r.symbol), r.kind)) || (r.pnl >= 0 ? "#2ecc8f" : "#ff5c5c");
        return {
          name: r.name || _compName(r),
          value: Math.abs(r.pnl),
          pnl: r.pnl,
          itemStyle: { color: col },
          // Per-item label colour: a series-level color callback is not
          // honoured for pie labels here, so a red slice kept painting its
          // percent red-on-red (screenshot 2026-10-08). The shade decides
          // contrast, not the series.
          label: { color: _txtColorFor(col) },
        };
      }),
    }],
  }, true);
  return chart;
}

// Build the donut summary from two fetches: closed trades already windowed
// by exit time (realized) + all rows (open rows contribute unrealized).
//
// Realized and unrealized are NEVER netted into one slice (2026-10-08).
// The old version summed them per symbol (`pnl = realized + unrealized`), so
// an open winner silently erased a closed loser: live, BTC's realized
// -$104.75 sat behind the open leg's +$826.57 floating gain and the ring
// showed a single green +$721.82 slice — a loss visible in "Every closed
// trade" existed nowhere on the chart. Each symbol now contributes up to
// two independently-signed components, so a loss is always drawn as a loss.
function _synthDonutSummary(closedRows, allRows) {
  const comps = new Map();   // "SYM|kind" → component row
  const touch = (sym, kind, source) => {
    const key = `${sym}|${kind}`;
    let r = comps.get(key);
    if (!r) {
      r = { symbol: sym, kind, pnl: 0, share: 0, n: 0, source: source || "", market_value: 0 };
      comps.set(key, r);
    }
    if (source && !r.source) r.source = source;
    return r;
  };
  for (const r of allRows) {
    if (r.status !== "filled" && r.status !== "open") continue;
    const u = Number(r.unrealized_pnl || 0);
    if (Math.abs(u) < 0.005) continue;    // nothing floating → no slice
    const row = touch(r.symbol, "unrealized", r.source);
    row.pnl += u;
    row.n += 1;
    row.market_value = Math.max(row.market_value, Number(r.market_value || 0));
  }
  for (const r of closedRows) {
    const v = Number(r.pnl || 0);
    if (Math.abs(v) < 0.005) continue;    // scratch trades stay off the ring
    const row = touch(r.symbol, "realized", r.source);
    row.pnl += v;
    row.n += 1;
  }
  const rows = [...comps.values()];
  const gross = rows.reduce((a, r) => a + Math.abs(r.pnl), 0);
  for (const r of rows) r.share = Math.round(Math.abs(r.pnl) / (gross || 1) * 1000) / 10;
  rows.sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl));
  const sumKind = (kind) => Math.round(rows.filter((r) => r.kind === kind)
    .reduce((a, r) => a + r.pnl, 0) * 100) / 100;
  const realized = sumKind("realized");
  const unrealized = sumKind("unrealized");
  // Per-symbol NET is kept as well — that is the honest portfolio view, but
  // it is never what the ring plots.
  const bySym = {};
  for (const r of rows) {
    const e = bySym[r.symbol] || (bySym[r.symbol] = {
      symbol: r.symbol, pnl: 0, realized: 0, unrealized: 0, source: r.source,
    });
    e.pnl += r.pnl;
    e[r.kind] += r.pnl;
  }
  return {
    orders: allRows.length,
    realized_pnl: realized,
    unrealized_pnl: unrealized,
    total_pnl: Math.round((realized + unrealized) * 100) / 100,
    components: rows,
    by_symbol: Object.values(bySym).sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl)),
  };
}

// One label per component everywhere (ring, tooltip, legend) so the pieces
// are always distinguishable: two positions in BTC are two rows, and the
// open one says so.
function _compName(r) {
  return r.kind === "unrealized" ? `${symOf(r.symbol)} \u00b7 open` : symOf(r.symbol);
}

function renderPnlDonut(summary) {
  const list = $("pnlBreakdown");
  const meta = $("pnlDonutMeta");
  if (!summary) return;

  const allRows = (summary.components || []).filter((r) => Math.abs(r.pnl) > 0.005);
  const total = summary.total_pnl || 0;
  const realized = summary.realized_pnl || 0;
  const unreal = summary.unrealized_pnl || 0;
  const signed = (v, d = 2) => `${v > 0 ? "+" : v < 0 ? "\u2212" : ""}$${fmt(Math.abs(v), d)}`;
  const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");

  if (meta) meta.textContent = allRows.length ? `\u00b7 ${_periodLabel()}` : "";

  if (!allRows.length) {
    if (list) list.innerHTML = '<div class="muted">No realized or unrealized PnL yet \u2014 dry/live test trades to populate the donut.</div>';
    for (const c of pnlCharts.values()) c.clear();
    return;
  }

  // Split rows by broker — one donut per registry account, never mixed.
  // Brokers with no rows in the period still render their empty state, so
  // the card layout is stable and every account is always visible.
  const byBroker = {};
  for (const b of _brokerList()) byBroker[b.key] = [];
  for (const r of allRows) {
    const b = _brokerOf(r);
    (byBroker[b] || (byBroker[b] = [])).push(r);
  }
  const totals = {};
  for (const b of Object.keys(byBroker)) totals[b] = byBroker[b].reduce((a, r) => a + r.pnl, 0);

  // One consistent shade per component per broker donut, so the legend dots
  // double as the ring's key (the key includes the kind — a closed and an
  // open position in the same symbol are different slices).
  const colorMap = {};
  const counters = {};
  for (const r of allRows) {
    const b = _brokerOf(r);
    const c = counters[b] || (counters[b] = { g: 0, r: 0 });
    const i = r.pnl >= 0 ? c.g++ : c.r++;
    colorMap[`${b}:${symOf(r.symbol)}:${r.kind}`] = _pnlShade(i, r.pnl);
  }
  const colorFor = (b, sym, kind) => colorMap[`${b}:${sym}:${kind}`] || null;

  for (const b of _brokerList()) {
    const el = $(`pnlDonut_${b.key}`);
    if (!el) continue;  // pane not rendered yet (pre-registry fallback HTML)
    const existing = pnlCharts.get(b.key);
    pnlCharts.set(b.key, _renderOneDonut(existing, el, byBroker[b.key] || [], totals[b.key] || 0, `${b.name} PnL`, (sym, kind) => colorFor(b.key, sym, kind)));
  }

  // KPI strip above the rings: the two figures can never hide inside each
  // other again — a losing realized total shows here even when the period
  // net is positive (the reference design's headline tiles, same idea).
  if (list) {
    list.innerHTML = "";
    const kpis = document.createElement("div");
    kpis.className = "pnl-kpis";
    kpis.innerHTML =
      `<div class="pnl-kpi"><span class="pnl-kpi-label">Realized</span>`
      + `<span class="pnl-kpi-val ${cls(realized)}">${signed(realized)}</span>`
      + `<span class="pnl-kpi-sub">closed in ${_periodLabel()}</span></div>`
      + `<div class="pnl-kpi"><span class="pnl-kpi-label">Unrealized</span>`
      + `<span class="pnl-kpi-val ${cls(unreal)}">${signed(unreal)}</span>`
      + `<span class="pnl-kpi-sub">open right now</span></div>`
      + `<div class="pnl-kpi"><span class="pnl-kpi-label">Net PnL</span>`
      + `<span class="pnl-kpi-val ${cls(total)}">${signed(total)}</span>`
      + `<span class="pnl-kpi-sub">realized + open</span></div>`;
    list.appendChild(kpis);
    const legend = document.createElement("div");
    legend.className = "pnl-legend";
    legend.innerHTML = allRows.map((r) => {
      const pnlCls = r.pnl >= 0 ? "up" : "down";
      const sign = r.pnl >= 0 ? "+" : "\u2212";
      const broker = _brokerOf(r);
      const brokerTag = _brokerTagHtml(broker);
      return `<div class="pnl-row">
        <span class="pnl-dot" style="background:${colorFor(broker, symOf(r.symbol), r.kind) || (r.pnl >= 0 ? "#2ecc8f" : "#ff5c5c")}"></span>
        ${brokerTag}
        <span class="sym">${_compName(r)}</span>
        <span class="pnl-kind pnl-kind-${r.kind}">${r.kind === "unrealized" ? "open" : "closed"}</span>
        <span class="pnl-bar-wrap"><span class="pnl-bar" style="width:${Math.min(r.share, 100)}%;background:${r.pnl >= 0 ? "#2ecc8f" : "#ff5c5c"}"></span></span>
        <span class="pnl-val ${pnlCls}">${sign}$${fmt(Math.abs(r.pnl), 2)}</span>
        <span class="muted">${r.share}%</span>
        <span class="muted pnl-sub">${r.n > 1 ? `${r.n} trades` : ""}</span>
      </div>`;
    }).join("")
      + `<div class="pnl-recon muted">Ring = every closed trade in ${_periodLabel()} (green win / red loss) plus each open position's floating PnL \u2014 kept apart, never netted, so a loss cannot hide behind a winner.</div>`;
    list.appendChild(legend);
  }
}

// Quiet background reconcile: broker-side closes (manual Exness exits, hit
// stops/TPs, a wiped testnet) reach the journal without anyone pressing the
// button. Only refreshes when reconciliation actually changed something.
let _lastAutoReconcile = 0;
async function autoReconcileTick() {
  if (Date.now() - _lastAutoReconcile < 55000) return;
  _lastAutoReconcile = Date.now();
  try {
    const res = await api("/api/bot/reconcile", { method: "POST" });
    if (res && ((res.count || 0) > 0 || (res.foreign_count || 0) > 0)) {
      await loadOrders();
    }
  } catch (_) { /* quiet — the next tick retries */ }
}

async function reconcileBroker() {
  try {
    const res = await api("/api/bot/reconcile", { method: "POST" });
    const strip = $("journalSummary");
    if (strip) {
      const msg = res.error ? `Reconcile failed — ${esc(res.error)}`
        : `Reconciled ${res.count || 0} order(s) with the broker`;
      strip.innerHTML += ` <span class="muted">· ${msg}</span>`;
      // Fills on the broker that OUR journal didn't place (manual / another
      // tool / earlier config) — flagged instead of silently ignored.
      if (res.foreign_count) {
        const syms = (res.foreign || []).slice(0, 6).map((f) => symOf(f.symbol)).join(", ");
        strip.innerHTML += ` <span class="muted" style="color:var(--gold,#f0b90b)">· ${icon("alert", "ic-sm")} ${res.foreign_count} foreign fill(s) not from this bot${syms ? `: ${syms}` : ""}</span>`;
      }
    }
    await loadOrders();
  } catch (e) {
    const strip = $("journalSummary");
    if (strip) strip.innerHTML += ` <span class="muted">· Reconcile failed — ${esc(e.message)}</span>`;
  }
}

async function toggleBotLoop() {
  const btn = $("btnToggleLoop");
  const st = window.__botStatus || {};
  const running = !!st.auto_loop_running;
  if (!btn) return;
  btn.disabled = true;
  try {
    const res = await api("/api/bot/loop", {
      method: "POST",
      body: JSON.stringify({ running: !running }),
    });
    await loadBotStatus();
    if (res && res.message) console.log("bot loop:", res.message);
  } catch (e) {
    alert("Failed to toggle bot loop: " + e.message);
  } finally {
    btn.disabled = false;
  }
}

async function applyBotSource() {
  const sel = $("botSourceSelect");
  const msg = $("botSourceMsg");
  const btn = $("btnApplySource");
  if (!sel || !msg || !btn) return;
  btn.disabled = true;
  msg.textContent = "applying…";
  try {
    const res = await api("/api/bot/source", {
      method: "POST",
      body: JSON.stringify({ source: sel.value }),
    });
    msg.textContent = res.ok
      ? `source A → ${esc(res.source)} ✓${res.warning ? " · " + esc(res.warning) : ""}`
      : `✗ ${esc(res.error || "failed")}`;
    if (res.ok) loadBotStatus();
  } catch (e) {
    msg.textContent = "✗ " + e.message;
  } finally {
    btn.disabled = false;
  }
}

async function runBotCycle() {
  const btn = $("btnRunBotLive");
  if (!btn) return;
  const st = window.__botStatus || {};
  if (st.live_single_source_blocked) {
    alert(
      "Blocked by the kill-switch: the agreement gate is in " +
      "single-source test mode (ARBITRATION_MIN_SOURCES=1). Set " +
      "ALLOW_LIVE_SINGLE_SOURCE=true in .env to allow, or use " +
      "ARBITRATION_MIN_SOURCES=2 for the production gate."
    );
    return;
  }
  const ok = confirm(
    "Run one cycle: every signal the AI agrees to will be placed as a REAL " +
    `order on the configured account (${st.broker || "hybrid"}). Continue?`
  );
  if (!ok) return;
  btn.disabled = true;
  const label = btn.innerHTML;                       // keep the icon — restore with innerHTML
  btn.innerHTML = "Sending orders…";
  try {
    await api("/api/bot/run", {
      method: "POST",
      body: JSON.stringify({ dry_run: false }),
      timeout: 180000,
    });
    await Promise.all([loadBotStatus(), loadOrders()]);
  } catch (e) {
    const b = $("botStatusBody");
    if (b) b.innerHTML = `<div class="muted">Cycle failed — ${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = label;
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
  const upDown = (v) => `<span class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "+" : ""}$${fmt(v, 2)}</span>`;
  const sub = p.mode === "multi"
    ? (p.accounts || []).map((a) =>
        `<span class="ba-acc">${esc(a.label)}${a.equity != null ? ` ${fmtUSD(a.equity)}` : ""}</span>`).join(" · ")
      + (p.unrealized_pl ? ` · unrealized ${upDown(p.unrealized_pl)}` : "")
      + ` · ${p.accounts.length} accounts`
    : p.mode === "exchange"
    ? `Binance · ${esc(p.exchange_name || "connected")}`
    : p.mode === "alpaca"
      ? `${p.exchange_name || "Alpaca"} · cash ${fmtUSD(p.cash)} · buying power ${fmtUSD(p.buying_power)}`
        + (p.unrealized_pl != null ? ` · unrealized ${upDown(p.unrealized_pl)}` : "")
        + (p.account_pnl != null ? ` · account ${upDown(p.account_pnl)} (incl. fees)` : "")
        + (p.account_number ? ` · ${esc(p.account_number)}` : "")
      : p.mode === "mt5"
      ? `${esc(p.exchange_name || "MT5")} · equity ${fmtUSD(p.equity || p.total_usd)} · free margin ${fmtUSD(p.cash)}`
        + (p.unrealized_pl ? ` · unrealized ${upDown(p.unrealized_pl)}` : "")
      : "Demo portfolio · prices from live market data";
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
    bs.innerHTML = sub;
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
  // Bot-tradeable assets first, watch-only after — every known asset shows.
  const ranked = [...state.watchlist].sort(
    (a, b) => (b.tradable ? 1 : 0) - (a.tradable ? 1 : 0)
  );
  const rows = ranked
    .filter((w) => !f || w.symbol.toLowerCase().includes(f) || symOf(w.symbol).toLowerCase().includes(f))
    .slice(0, 100)
    .map((w) => {
      const up = w.change >= 0;
      const col = up ? UP : DOWN;
      return `<tr class="${w.tradable ? "" : "watch-row"}">
        <td><span class="asset-icon" style="background:var(--card2)">${w.symbol.slice(0, 1)}</span> <b>${symOf(w.symbol)}</b> <span class="muted">${w.symbol.split("/")[1]}</span>${w.tradable ? "" : " <span class=\"watch-badge\">watch</span>"}</td>
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

let lastOrderBySym = {};   // symbol → latest journal order (trades shown on signal cards)

async function loadSignals() {
  try {
    const [data, orders] = await Promise.all([
      api("/api/signals", { timeout: 60000 }),
      api("/api/orders?limit=100&detail=1").catch(() => null),
    ]);
    lastOrderBySym = {};
    for (const o of (orders && orders.orders) || []) {
      if (!lastOrderBySym[o.symbol]) lastOrderBySym[o.symbol] = o;
    }
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

function _newsBadge(s) {
  const nv = s.news_verdict && s.news_verdict.verdict;
  const sc = s.news_score != null ? (s.news_score > 0 ? "+" : "") + s.news_score : "";
  const msg = s.news_verdict && s.news_verdict.message ? esc(s.news_verdict.message) : "";
  if (nv === "confirm") return `<span class="signal-badge badge-long" title="News confirms (${sc}): ${msg}">NEWS ✓</span>`;
  if (nv === "veto") return `<span class="signal-badge badge-short" title="News vetoes (${sc}): ${msg}">NEWS ✗</span>`;
  if (nv) return `<span class="signal-badge badge-hold" title="News neutral (${sc}): ${msg}">NEWS −</span>`;
  return "";
}

function sigCard(s) {
  const dir = s.direction;
  const long = dir === "LONG";
  const arrow = long ? "▲" : "▼";
  const rr = s.risk_reward != null && isFinite(s.risk_reward) ? `1:${s.risk_reward}` : "—";
  const tr = lastOrderBySym[s.symbol];
  const tradeChip = tr
    ? `<a class="btn-signal" title="Journal: ${esc(tr.side)} qty ${fmt(tr.qty, 4)} · ${esc(tr.status)}${tr.pnl != null ? ` · pnl ${tr.pnl > 0 ? "+" : ""}${fmt(tr.pnl, 2)}` : ""}" onclick="switchView('orders'); return false;">${icon("book", "ic-sm")} ${tr.status}</a>`
    : "";
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
        ${_newsBadge(s)}
      </div>
      <div class="sig-card-right">
        ${tradeChip}
        <a class="btn-signal" href="${esc(tvUrl)}" target="_blank" rel="noopener">${icon("external", "ic-sm")} TV</a>
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
    ${s.news_score != null
      ? `<span class="signal-badge badge-hold" title="News score ${s.news_score > 0 ? "+" : ""}${s.news_score}">N ${s.news_score > 0 ? "+" : ""}${s.news_score}</span>`
      : ""}
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
  const el = $("sigChart");
  if (!el) return;
  if (!sigChart) sigChart = echarts.init(el);
  renderCandleChart(d, sigChart, $("sigChartTitle"), $("sigLevelLegend"));
  bindLevelDragging(sigChart, "signals");
}

/* Shared candlestick renderer: candles + SMA/BB + volume/RSI/MACD and
   the Entry / SL / TP overlay. `levels` ({direction, entry, stop_loss,
   take_profit}) overrides the /api/signals lookup — used by the Orders
   view to draw an open position's levels. */
function renderCandleChart(d, chart, titleEl, legendEl, levels) {
  if (!chart) return;
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

  // Overlay the Entry / SL / TP levels on the price pane.
  const sig = levels || lastSignals.find((s) => s.symbol === d.symbol);
  if (sig && sig.direction !== "HOLD" && sig.entry) {
    const ml = [];
    if (sig.entry != null) {
      ml.push({ yAxis: sig.entry, name: "Entry", lineStyle: { color: "#fbbf24", type: "dashed", width: 2 },
                label: { formatter: "▲ Entry " + fmtPrice(sig.entry), color: "#fbbf24" } });
    }
    if (sig.stop_loss != null) {
      ml.push({ yAxis: sig.stop_loss, name: "SL", lineStyle: { color: "#ef4444", type: "solid", width: 1.5 },
                label: { formatter: "▼ SL " + fmtPrice(sig.stop_loss), color: "#ef4444" } });
    }
    if (sig.take_profit != null) {
      ml.push({ yAxis: sig.take_profit, name: "TP", lineStyle: { color: "#22c55e", type: "solid", width: 1.5 },
                label: { formatter: "▲ TP " + fmtPrice(sig.take_profit), color: "#22c55e" } });
    }
    kSeries.markLine = {
      silent: true, symbol: "none",
      label: { show: true, fontSize: 10, color: "#f8fafc", position: "insideEndTop" },
      data: ml,
    };
  }

  // Usable levels even without a signal/position: auto S/R from recent swing
  // pivots (a pivot = a bar higher/lower than k bars on each side). The two
  // nearest supports below price and resistances above get drawn.
  const autoLvls = [];
  if (!(sig && sig.direction !== "HOLD" && sig.entry)) {
    let last = null;
    for (let i = d.close.length - 1; i >= 0; i--) { if (d.close[i] != null) { last = d.close[i]; break; } }
    if (last != null && d.high && d.low && d.high.length === d.low.length && d.high.length >= 7) {
      const k = 3, sup = [], res = [];
      for (let i = k; i < d.high.length - k; i++) {
        let ph = true, pl = true;
        for (let j = i - k; j <= i + k; j++) {
          if (j === i) continue;
          if (d.high[j] >= d.high[i]) ph = false;
          if (d.low[j] <= d.low[i]) pl = false;
          if (!ph && !pl) break;
        }
        if (ph && d.high[i] > last) res.push(d.high[i]);
        if (pl && d.low[i] < last) sup.push(d.low[i]);
      }
      sup.sort((a, b) => b - a); // nearest support first
      res.sort((a, b) => a - b); // nearest resistance first
      // Merge near-duplicate pivots: pivots within 0.15% of an already-kept
      // level are noise (R1 77,347 vs R2 77,370 drew overlapping lines).
      const keep = (arr) => {
        const out = [];
        for (const p of arr) {
          if (!out.some((q) => Math.abs(p - q) / q < 0.0015)) out.push(p);
          if (out.length === 2) break;
        }
        return out;
      };
      keep(sup).forEach((p, n) => autoLvls.push({ yAxis: p, name: "S" + (n + 1) }));
      keep(res).forEach((p, n) => autoLvls.push({ yAxis: p, name: "R" + (n + 1) }));
    }
    if (autoLvls.length) {
      kSeries.markLine = {
        silent: true, symbol: "none",
        lineStyle: { color: "#38bdf8", type: "dashed", width: 1, opacity: 0.75 },
        label: { show: true, fontSize: 10, color: "#38bdf8", position: "insideEndTop", formatter: (p) => `${p.name} ${fmtPrice(p.value)}` },
        data: autoLvls,
      };
    }
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
  chart.setOption(option, true);

  if (titleEl) titleEl.textContent = `${symOf(d.symbol)} · ${d.interval}` + (sig && sig.direction !== "HOLD" ? (levels ? " — position levels" : " — signal levels") : (autoLvls.length ? " — auto S/R" : ""));
  const legend = legendEl;
  if (legend) {
    legend.innerHTML = sig && sig.direction !== "HOLD" && sig.entry
      ? `<b style="color:#fbbf24">Entry ${fmtPrice(sig.entry)}</b> · <b style="color:#ef4444">SL ${fmtPrice(sig.stop_loss)}</b> · <b style="color:#22c55e">TP ${fmtPrice(sig.take_profit)}</b>${levels ? " · open position levels" : " · click a signal card or search to switch asset"}`
      : (autoLvls.length
        ? `Auto S/R from recent swings — ${autoLvls.map((l) => `<b style="color:#38bdf8">${l.name} ${fmtPrice(l.yAxis)}</b>`).join(" · ")} · signals/positions override with Entry/SL/TP`
        : "No signals, positions or swing levels yet — levels appear here as data loads.");
  }
}

// ── Searchable asset picker (shared component) ──────────────────────
const QUICK_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "DOGE/USDT"];
const BACKTEST_INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"];
let botPicker = null;  // backtest panel
let sigPicker = null;  // signals panel
let ordPicker = null;  // orders position chart
let intervalPicker = null;  // backtest timeframe

/* Simple custom dropdown (replaces the native <select> so the dark theme
   stays consistent). Same look as the asset picker's list. */
function createSelectPicker(cfg) {
  // cfg: { rootId, valueId, listId, options, initial, onSelect }
  const root = $(cfg.rootId);
  const list = $(cfg.listId);
  const valueEl = $(cfg.valueId);
  if (!root || !list || !valueEl) return null;
  const st = { value: cfg.initial, active: 0 };

  function render() {
    list.innerHTML = cfg.options.map((opt, i) =>
      `<div class="ap-row${st.value === opt ? " sel" : ""}" data-i="${i}" role="option">
        <span class="ap-sym">${opt}</span>
      </div>`
    ).join("");
    list.hidden = false;
    root.setAttribute("aria-expanded", "true");
  }
  function close() {
    list.hidden = true;
    root.setAttribute("aria-expanded", "false");
  }
  function select(opt) {
    st.value = opt;
    valueEl.textContent = opt;
    close();
    if (cfg.onSelect) cfg.onSelect(opt);
  }

  root.querySelector(".ap-input-row").addEventListener("click", (e) => {
    e.stopPropagation();
    if (list.hidden) render(); else close();
  });
  root.querySelector(".ap-input-row").addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); if (list.hidden) render(); }
    else if (e.key === "Escape") close();
    else if (e.key === "Enter" && !list.hidden) {
      e.preventDefault();
      select(cfg.options[st.active] ?? st.value);
    }
  });
  list.addEventListener("mousedown", (e) => {
    const row = e.target.closest(".ap-row");
    if (row) { e.preventDefault(); select(cfg.options[+row.dataset.i]); }
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(`#${cfg.rootId}`)) close();
  });
  return { get value() { return st.value; }, select };
}

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
      list.innerHTML = '<div class="ap-empty">No matching assets</div>';
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
    const interval = (intervalPicker && intervalPicker.value) || "1h";
    const r = await api(`/api/backtest?symbol=${encodeURIComponent(symbol)}&interval=${interval}&limit=9000`, { timeout: 60000 });
    // Server may fall back to 1h for macro symbols (gold/FX have no
    // sub-hour candles) — surface that banner FIRST so "zero trades" is
    // never mistaken for a broken engine.
    const note = r.data_note ? `<div class="bt-note">${esc(r.data_note)}</div>` : "";
    box.innerHTML = note + btMetricsHtml(r) + btHealthHtml(r) + btTradesHtml(r);
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
loadBrokerAccounts();
loadBrokerRegistry();
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
    } else if (state.portfolio.mode === "multi") {
      $("btnConnect").textContent = `${state.portfolio.accounts.length} accounts ✓`;
      $("btnConnect").classList.add("connected");
    } else if (state.portfolio.mode === "alpaca") {
      $("btnConnect").textContent = state.portfolio.account_number
        ? `Alpaca ${state.portfolio.exchange_name.includes("paper") ? "paper" : "live"} ✓`
        : "Alpaca ✓";
      $("btnConnect").classList.add("connected");
      // The Net-PnL donut is priced off Alpaca's live marks — refresh it the
      // moment the portfolio snapshot arrives so it never lags the account.
      loadOrders();
    }
  } catch (e) {
    console.warn("portfolio:", e);
  }
}

// ── Learn view: trading tutor + why-this-trade ─────────────────────
let tutorTopics = [];
let tutorTopic = "";

async function loadTutor() {
  try {
    const d = await api("/api/tutor");
    tutorTopics = d.topics || [];
    renderTutorTopics();
    loadTradeExplain();
    if (!tutorTopic && tutorTopics.length) showTutorTopic(tutorTopics[0].topic);
  } catch (e) {
    const box = $("tutorBody");
    if (box) box.innerHTML = `<div class="muted">Tutor unavailable — ${esc(e.message)}</div>`;
  }
}

function renderTutorTopics() {
  const list = $("tutorTopicsList");
  if (!list) return;
  const basic = tutorTopics.filter((t) => t.category === "basic");
  const adv = tutorTopics.filter((t) => t.category === "advanced");
  const chips = (items) => items.map((t) =>
    `<button class="ap-chip ${t.topic === tutorTopic ? "active" : ""}" data-topic="${esc(t.topic)}" title="${esc(t.title)}">${esc(t.title)}</button>`
  ).join("");
  list.innerHTML =
    `<div class="sig-sec-label">Basics</div><div class="tutor-chips">${chips(basic) || '<span class="muted">—</span>'}</div>` +
    `<div class="sig-sec-label" style="margin-top:10px">Advanced</div><div class="tutor-chips">${chips(adv) || '<span class="muted">—</span>'}</div>`;
  list.querySelectorAll("[data-topic]").forEach((b) => {
    b.addEventListener("click", () => showTutorTopic(b.dataset.topic));
  });
}

async function showTutorTopic(topic) {
  tutorTopic = topic;
  renderTutorTopics();
  const box = $("tutorBody");
  if (!box) return;
  box.innerHTML = '<div class="muted">Loading…</div>';
  try {
    const d = await api("/api/tutor?topic=" + encodeURIComponent(topic));
    if (!d.ok) { box.innerHTML = `<div class="muted">${esc(d.error || "unknown topic")}</div>`; return; }
    box.innerHTML =
      `<div class="tutor-title">${esc(d.title)} <span class="signal-badge badge-hold">${esc(d.category)}</span></div>` +
      `<div class="tutor-body-text">${esc(d.explanation).replace(/\n\n/g, "<br><br>").replace(/\n/g, "<br>")}</div>` +
      (d.example ? `<div class="tutor-example"><b>Example</b> — ${esc(d.example)}</div>` : "") +
      `<div class="tutor-why"><b>Why it matters</b> — ${esc(d.why_it_matters)}</div>`;
  } catch (e) {
    box.innerHTML = `<div class="muted">Tutor error — ${esc(e.message)}</div>`;
  }
}

async function loadTradeExplain() {
  const box = $("tradeExplainBody");
  if (!box) return;
  try {
    const d = await api("/api/tutor/trade");
    if (!d.ok) { box.innerHTML = `<div class="muted">${esc(d.message || "—")}</div>`; return; }
    let held = "";
    if (d.held_top && d.held_top.length) {
      held = `<div class="held-reasons-title" style="margin-top:8px">Why symbols were skipped last cycle</div>` +
        d.held_top.map((h) =>
          `<div class="held-reason"><span class="held-reason-n">${h.count}</span><span class="held-reason-k">${esc(h.reason)}</span></div>`
        ).join("");
    }
    box.innerHTML = `<div class="tutor-why">${esc(d.headline)}<br>${esc(d.why)}</div>${held}`;
  } catch (e) {
    box.innerHTML = `<div class="muted">Explain unavailable — ${esc(e.message)}</div>`;
  }
}

// ── AI Test view (paper trades + confirmation pipeline) ────────────
async function loadAiTest() {
  try {
    const [layers, cb, testTrades, feedback] = await Promise.all([
      api("/api/ai-layers").catch(() => null),
      api("/api/circuit-breaker").catch(() => null),
      api("/api/test-trades?limit=50").catch(() => null),
      api("/api/feedback/calibration?days=30").catch(() => null),
    ]);
    renderAiLayers(layers);
    renderCircuitBreaker(cb);
    renderTestTrades(testTrades);
    renderFeedback(feedback);
  } catch (e) {
    console.warn("loadAiTest failed:", e);
  }
}

function renderAiLayers(d) {
  const body = $("aiLayersBody");
  const meta = $("aiLayersMeta");
  if (!body) return;
  if (!d) { body.innerHTML = '<div class="muted">Unavailable</div>'; return; }
  meta.textContent = `Mode: ${d.ai_mode || "?"}`;
  const layers = d.layers || {};
  const session = d.session || {};
  const risk = d.risk || {};
  const layerList = [
    { name: "News Sentiment", key: "news_sentiment", icon: "📰" },
    { name: "Freshness Gate", key: "freshness", icon: "🕐" },
    { name: "Calendar Guard", key: "calendar_guard", icon: "📅" },
    { name: "AI Confirm (6-factor)", key: "ai_confirm", icon: "🎯" },
    { name: "DXY Master Filter", key: "dxy_filter", icon: "💵" },
    { name: "Smart Money (OB/FVG)", key: "smart_money", icon: "🏦" },
  ];
  let html = '<div class="ai-pipeline">';
  for (const l of layerList) {
    const cfg = layers[l.key] || {};
    const on = cfg.enabled !== false;
    const cls = on ? "layer-on" : "layer-off";
    html += `<div class="ai-layer-pill ${cls}">` +
      `<span>${l.icon} ${l.name}</span>` +
      `<span class="layer-status">${on ? "ON" : "OFF"}</span></div>`;
  }
  // Session + risk info
  const sess = session.current || {};
  html += '</div><div style="margin-top:12px;display:flex;gap:16px;flex-wrap:wrap">';
  html += `<div class="muted">Session: <b>${esc(sess.session || "?")}</b> (mult: ${session.multiplier || 1.0})</div>`;
  html += `<div class="muted">Trailing SL: <b>${risk.trailing_stop ? "ON" : "OFF"}</b></div>`;
  html += `<div class="muted">Dynamic TP: <b>${risk.partial_tp ? "ON" : "OFF"}</b></div>`;
  html += `<div class="muted">Max hold: <b>${risk.max_hold_bars || 12} bars</b></div>`;
  html += '</div>';
  body.innerHTML = html;
}

function renderCircuitBreaker(d) {
  const body = $("circuitBreakerBody");
  const meta = $("cbMeta");
  if (!body) return;
  if (!d) { body.innerHTML = '<div class="muted">Unavailable</div>'; return; }
  const s = d.state || {};
  const h = d.health || {};
  const levelColors = ["#2ecc8f", "#f0b429", "#ff8c42", "#ff5c5c", "#8b0000"];
  const level = s.level || 0;
  const color = levelColors[Math.min(level, 4)];
  meta.innerHTML = `<span style="color:${color}">Level ${level}</span>`;
  let html = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">';
  html += `<div>Drawdown: <b>${fmt(s.drawdown_pct, 2)}%</b></div>`;
  html += `<div>Risk mult: <b>${fmt(s.risk_multiplier, 2)}x</b></div>`;
  html += `<div>Consec. losses: <b>${s.consecutive_losses || 0}</b></div>`;
  html += `<div>Daily PnL: <b>${fmt(s.daily_pnl, 2)}</b></div>`;
  html += `<div>Equity: <b>${fmt(s.current_equity, 0)}</b></div>`;
  html += `<div>Peak: <b>${fmt(s.peak_equity, 0)}</b></div>`;
  html += '</div>';
  if (h.verdict) {
    html += `<div style="margin-top:8px" class="muted">Strategy: <b>${esc(h.verdict)}</b>`;
    if (h.rolling_wr !== undefined) html += ` (WR: ${fmt(h.rolling_wr * 100, 1)}%)`;
    html += '</div>';
  }
  const ex = d.exposure || {};
  if (ex.warning) {
    html += `<div style="margin-top:4px;color:#ff5c5c">⚠ ${esc(ex.warning)}</div>`;
  }
  body.innerHTML = html;
}

function renderTestTrades(d) {
  const body = $("testTradesBody");
  const badge = $("testTradeModeBadge");
  const stats = $("testTradeStats");
  if (!body) return;
  if (!d) { body.innerHTML = '<tr><td colspan="14" class="muted">No data</td></tr>'; return; }
  const s = d.stats || {};
  if (badge) badge.textContent = `WR: ${fmt((s.win_rate || 0) * 100, 1)}%`;
  if (badge) badge.className = `signal-badge ${(s.win_rate || 0) >= 0.6 ? "badge-long" : "badge-hold"}`;
  if (stats) stats.textContent = `${s.total || 0} trades | PnL: ${fmt(s.total_pnl, 2)}`;
  const trades = d.trades || [];
  if (!trades.length) {
    body.innerHTML = '<tr><td colspan="14" class="muted">No shadow-book rows yet. (These are ANALYSIS-ONLY rows: they simulate how the AI&#39;s calls would have done — the real orders are in the Orders journal.)</td></tr>';
    return;
  }
  body.innerHTML = trades.slice(0, 50).map((t) => {
    const pnlCls = (t.pnl || 0) > 0 ? "pnl-pos" : (t.pnl || 0) < 0 ? "pnl-neg" : "";
    const outCls = t.outcome === "win" ? "badge-long" : t.outcome === "loss" ? "badge-short" : "badge-hold";
    return `<tr>` +
      `<td class="muted">${esc((t.ran_at || "").slice(0, 16))}</td>` +
      `<td><b>${esc(t.symbol || "")}</b></td>` +
      `<td class="${t.direction === "LONG" ? "sig-long" : "sig-short"}">${esc(t.direction || "")}</td>` +
      `<td>${fmtPrice(t.entry_price)}</td>` +
      `<td>${fmtPrice(t.stop_loss)}</td>` +
      `<td>${fmtPrice(t.take_profit)}</td>` +
      `<td>${t.exit_price ? fmtPrice(t.exit_price) : "—"}</td>` +
      `<td class="${pnlCls}">${t.pnl !== null && t.pnl !== undefined ? fmt(t.pnl, 4) : "—"}</td>` +
      `<td>${t.confirm_score || 0}</td>` +
      `<td>${t.dxy_score || 0}</td>` +
      `<td>${t.smc_score || 0}</td>` +
      `<td class="muted">${esc(t.regime || "—")}</td>` +
      `<td class="muted">${esc(t.session || "—")}</td>` +
      `<td><span class="signal-badge ${outCls}">${esc(t.outcome || "open")}</span></td>` +
      `</tr>`;
  }).join("");
}

function renderFeedback(d) {
  const body = $("feedbackBody");
  const meta = $("feedbackMeta");
  if (!body) return;
  if (!d || d.error) {
    body.innerHTML = `<div class="muted">${d && d.error === "no_closed_trades" ? "No closed trades yet — need data to calibrate" : "Unavailable"}</div>`;
    return;
  }
  meta.textContent = `${d.total_trades || 0} trades | WR: ${fmt((d.win_rate || 0) * 100, 1)}%`;
  const recs = d.recommendations || [];
  let html = '';
  if (recs.length) {
    html += '<div style="margin-bottom:8px">';
    for (const r of recs.slice(0, 5)) {
      const isStrong = r.startsWith("STRONG");
      html += `<div style="color:${isStrong ? UP : DOWN};font-size:0.85em">${esc(r)}</div>`;
    }
    html += '</div>';
  }
  // Factor breakdown
  const factors = d.by_factor || {};
  if (Object.keys(factors).length) {
    html += '<div style="font-size:0.85em">';
    for (const [factor, buckets] of Object.entries(factors)) {
      html += `<div style="margin-top:4px"><b>${esc(factor)}</b>: `;
      const parts = Object.entries(buckets).map(([k, v]) =>
        `${k}=${fmt((v.win_rate || 0) * 100, 0)}%(${v.total})`
      );
      html += parts.join(" | ") + '</div>';
    }
    html += '</div>';
  }
  body.innerHTML = html || '<div class="muted">Need more trades for calibration data.</div>';
}

// ── Trade Stats (TradinJournal-style dashboard cards + radar + heatmap) ─
let radarChart = null;
let heatmapChart = null;

function _statColor(el, val) {
  const v = parseFloat(val);
  if (!isFinite(v)) return;
  el.classList.toggle("up", v > 0);
  el.classList.toggle("down", v < 0);
}

function _streakLabel(type, n) {
  if (type === "win") return `${n} win${n > 1 ? "s" : ""}`;
  if (type === "loss") return `${n} loss${n > 1 ? "es" : ""}`;
  return "—";
}

function _pfLabel(pf) {
  if (pf >= 2.0) return "Excellent";
  if (pf >= 1.5) return "Good";
  if (pf >= 1.0) return "Even";
  return "Losing";
}

async function loadTradeStats() {
  try {
    const d = await api("/api/trade-stats");
    renderTradeStats(d);
  } catch (e) {
    // silent — dashboard works without stats
  }
}

function renderTradeStats(d) {
  // Stat cards
  const todayEl = $("statTodayPnl");
  if (todayEl) {
    const v = todayEl.querySelector(".stat-value");
    v.textContent = `${d.today_pnl >= 0 ? "+" : ""}${fmtUSD(d.today_pnl)}`;
    _statColor(v, d.today_pnl);
    const sub = todayEl.querySelector(".stat-sub");
    sub.textContent = new Date().toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric" });
  }
  const balEl = $("statBalance");
  if (balEl) {
    balEl.querySelector(".stat-value").textContent = fmtUSD(d.balance);
    // Spell out the derivation so this can never be mistaken for the broker's
    // equity again (the tile used to read "Broker account" while showing a
    // journal figure 130k below the real equity): seed + realized P&L. The
    // seed is whatever the balance is minus the closed P&L — no extra config.
    const sub = balEl.querySelector(".stat-sub");
    if (sub && isFinite(d.balance) && isFinite(d.total_pnl)) {
      const seed = d.balance - d.total_pnl;
      sub.textContent = `${fmtUSD(seed)} seed \u00b7 ${d.total_pnl >= 0 ? "+" : "\u2212"}${fmtUSD(Math.abs(d.total_pnl))} closed`;
      sub.title = "Journal-derived: opening balance plus realized (closed) P&L. "
        + "This is not the broker equity — see the Balance card for that.";
    }
  }
  const totalEl = $("statTotalPnl");
  if (totalEl) {
    const v = totalEl.querySelector(".stat-value");
    v.textContent = `${d.total_pnl >= 0 ? "+" : ""}${fmtUSD(d.total_pnl)}`;
    _statColor(v, d.total_pnl);
    const sub = totalEl.querySelector(".stat-sub");
    sub.textContent = `${d.total_trades} trades · ${d.win_rate}% win rate`;
  }
  const streakEl = $("statStreak");
  if (streakEl) {
    const v = streakEl.querySelector(".stat-value");
    v.textContent = d.current_streak;
    v.classList.toggle("up", d.streak_type === "win");
    v.classList.toggle("down", d.streak_type === "loss");
    const sub = streakEl.querySelector(".stat-sub");
    const fire = d.streak_type === "win" && d.current_streak >= 3 ? " 🔥" : "";
    sub.textContent = _streakLabel(d.streak_type, d.current_streak) + fire;
    if (d.streak_type === "win" && d.current_streak >= 3) sub.textContent += " — You're on fire!";
  }
  const pfEl = $("statProfitFactor");
  if (pfEl) {
    pfEl.querySelector(".stat-value").textContent = d.profit_factor.toFixed(2);
    const sub = pfEl.querySelector(".stat-sub");
    sub.textContent = _pfLabel(d.profit_factor);
  }
  const bwEl = $("statBiggestWin");
  if (bwEl) {
    const v = bwEl.querySelector(".stat-value");
    v.textContent = `+${fmtUSD(d.biggest_win)}`;
    v.classList.add("up");
  }
  const bdEl = $("statBestDay");
  if (bdEl) {
    const v = bdEl.querySelector(".stat-value");
    v.textContent = `${d.best_day >= 0 ? "+" : ""}${fmtUSD(d.best_day)}`;
    _statColor(v, d.best_day);
  }

  // Radar chart
  renderRadar(d.radar, d.trade_score);

  // Calendar heatmap
  renderHeatmap(d.heatmap, d);
}

function renderRadar(radar, score) {
  const el = $("radarChart");
  if (!el) return;
  if (!radarChart) radarChart = echarts.init(el);
  const indicators = Object.keys(radar).map((k) => ({ name: k, max: 100 }));
  const values = Object.values(radar);
  const scoreEl = $("radarScore");
  if (scoreEl) scoreEl.textContent = score;
  const labelEl = $("radarLabel");
  if (labelEl) {
    const adj = score >= 70 ? "Strong" : score >= 50 ? "Good" : score >= 30 ? "Needs work" : "Developing";
    labelEl.textContent = `${adj} — your overall trading performance`;
  }
  radarChart.setOption({
    backgroundColor: "transparent",
    radar: {
      indicator: indicators,
      shape: "polygon",
      radius: "65%",
      axisName: { color: "#8a94a6", fontSize: 11 },
      splitArea: { areaStyle: { color: ["rgba(76,154,255,0.02)", "rgba(76,154,255,0.04)", "rgba(76,154,255,0.06)", "rgba(76,154,255,0.08)"] } },
      splitLine: { lineStyle: { color: "rgba(255,255,255,0.06)" } },
      axisLine: { lineStyle: { color: "rgba(255,255,255,0.08)" } },
    },
    series: [{
      type: "radar",
      data: [{
        value: values,
        areaStyle: { color: "rgba(76,154,255,0.15)" },
        lineStyle: { color: ACCENT, width: 2 },
        itemStyle: { color: ACCENT },
      }],
    }],
  }, true);
}

function renderHeatmap(heatmap, d) {
  const el = $("heatmapChart");
  if (!el) return;
  if (!heatmapChart) heatmapChart = echarts.init(el);
  const meta = $("heatmapMeta");
  if (meta) meta.textContent = `${d.total_trades} trades in ${new Date().getFullYear()}`;

  // Build a date->pnl map
  const pnlMap = {};
  let maxAbs = 1;
  for (const h of heatmap) {
    pnlMap[h.date] = h.pnl;
    maxAbs = Math.max(maxAbs, Math.abs(h.pnl));
  }

  // Generate all dates in the range (last 180 days)
  const today = new Date();
  const start = new Date(today);
  start.setDate(start.getDate() - 180);
  const allDays = [];
  for (let d = new Date(start); d <= today; d.setDate(d.getDate() + 1)) {
    allDays.push(d.toISOString().slice(0, 10));
  }

  // Build week x day matrix for ECharts calendar-like heatmap
  // ECharts heatmap: [weekIndex, dayOfWeek, value]
  const dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  // Week columns for the x axis (one per week row spanned by the 180-day window,
  // labeled by the window-relative Monday of each column, e.g. "8/31")
  const weekCount = Math.ceil(allDays.length / 7);
  const weekLabels = Array.from({ length: weekCount }, (_, w) => {
    const d = new Date(start);
    d.setDate(d.getDate() + w * 7);
    return `${d.getMonth() + 1}/${d.getDate()}`;
  });
  const data = [];
  for (const dateStr of allDays) {
    const dt = new Date(dateStr + "T12:00:00Z");
    const dow = (dt.getDay() + 6) % 7; // 0=Mon, 6=Sun
    const weekNum = Math.floor((dt - start) / (7 * 864e5));
    const pnl = pnlMap[dateStr] || 0;
    const hasTrade = dateStr in pnlMap;
    // Color: green for profit, red for loss, dim for no trade
    let color = "rgba(255,255,255,0.03)"; // no trade
    if (hasTrade) {
      const intensity = Math.min(Math.abs(pnl) / maxAbs, 1);
      if (pnl > 0) {
        color = `rgba(46,204,143,${0.2 + intensity * 0.6})`;
      } else if (pnl < 0) {
        color = `rgba(255,72,72,${0.2 + intensity * 0.6})`;
      } else {
        color = "rgba(255,255,255,0.08)";
      }
    }
    data.push({
      value: [weekNum, dow, pnl],
      // true index into allDays (weekNum is only for x-position; do NOT
      // reconstruct date as weekNum*7+dow — it misaligns after any partial
      // first week and reads past the array end → tooltip TypeError)
      dayIndex: allDays.indexOf(dateStr),
      itemStyle: { color, borderColor: "transparent", borderWidth: 1 },
      hasTrade,
    });
  }

  heatmapChart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      formatter: (p) => {
        // Defensive: some tooltip triggers pass params without a data datum
        // (p.data undefined / value missing) — never let the tooltip throw.
        const v = (p && p.data && p.data.value) || (p && p.value) || [];
        if (!v.length && !(p && p.data && p.data.dayIndex != null)) return "";
        const dayIdx = p.data && p.data.dayIndex != null ? p.data.dayIndex : v[0] * 7 + v[1];
        const date = allDays[dayIdx] || "";
        const pnl = v[2];
        if (pnl == null) return date;
        return `${date}<br/>P&L: <b style="color:${pnl >= 0 ? "#2ecc8f" : "#ff5c5c"}">${pnl >= 0 ? "+" : ""}${fmtUSD(pnl)}</b>`;
      },
      backgroundColor: "#14161c",
      borderColor: "#24262e",
      textStyle: { color: "#e6e9f0" },
    },
    grid: { left: 30, right: 8, top: 4, bottom: 16 },
    xAxis: {
      type: "category",
      // x = week columns. (Was dayNames: a 7-category axis silently dropped
      // every week past index 6 — only 49 of 181 days ever rendered.)
      data: weekLabels,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: "#8a94a6", fontSize: 9, interval: 2 },
      splitLine: { show: false },
    },
    yAxis: {
      type: "category",
      data: dayNames,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: "#8a94a6", fontSize: 10 },
      splitLine: { show: false },
      inverse: true,
    },
    series: [{
      type: "heatmap",
      // Keep the rich datum objects (dayIndex/hasTrade/itemStyle). Mapping to
      // bare [week,dow,pnl] arrays strips dayIndex, forcing the tooltip back
      // onto the week*7+dow formula — only correct when the window starts Mon.
      data: data,
      itemStyle: { borderColor: "transparent", borderWidth: 2, borderRadius: 3 },
      emphasis: { itemStyle: { borderColor: "#4c9aff", borderWidth: 1 } },
    }],
    visualMap: {
      show: false,
      min: -maxAbs,
      max: maxAbs,
      inRange: {
        color: ["rgba(255,72,72,0.7)", "rgba(255,72,72,0.2)", "rgba(255,255,255,0.03)", "rgba(46,204,143,0.2)", "rgba(46,204,143,0.7)"],
      },
    },
  }, true);

  // Bottom stats
  const statsEl = $("heatmapStats");
  if (statsEl) {
    statsEl.innerHTML = `
      <div><span class="hs-val">${d.trading_days}</span> <span class="hs-label">Trading Days</span></div>
      <div><span class="hs-val">${d.current_streak}</span> <span class="hs-label">Current Streak</span></div>
      <div><span class="hs-val">${d.longest_streak}</span> <span class="hs-label">Longest Streak</span></div>
      <div><span class="hs-val ${d.total_pnl >= 0 ? "up" : "down"}">${d.total_pnl >= 0 ? "+" : ""}${fmtUSD(d.total_pnl)}</span> <span class="hs-label">Total P&L</span></div>
    `;
  }
}

// ── Boot ────────────────────────────────────────────────────────────
window.addEventListener("resize", () => {
  if (portValueChart) portValueChart.resize();
  if (totalChart) totalChart.resize();
  if (sigChart) sigChart.resize();
  if (ordChart) ordChart.resize();
  for (const c of pnlCharts.values()) c.resize();
  if (radarChart) radarChart.resize();
  if (heatmapChart) heatmapChart.resize();
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
// The bot trades gold/FX/oil/indices on MT5 too — surface them on the
// backtest quick chips (full searchable list comes from /api/watchlist).
botPicker.setQuick(["BTC/USDT", "ETH/USDT", "XAU/USD", "EUR/USD", "WTI/USD"]);
intervalPicker = createSelectPicker({
  rootId: "intervalPicker", valueId: "intervalValue", listId: "intervalList",
  options: BACKTEST_INTERVALS, initial: "1h",
  onSelect: () => {},
});
sigPicker = createAssetPicker({
  inputId: "sigInput", listId: "sigList", quickId: "sigQuick", initial: "BTC/USDT",
  onSelect: (sym) => { state.sigSymbol = sym; loadSigChart(); highlightSigCard(); },
});
ordPicker = createAssetPicker({
  inputId: "ordInput", listId: "ordList", quickId: "ordQuick", initial: "BTC/USDT",
  onSelect: (sym) => { state.ordSymbol = sym; loadOrdChart(); },
});
// Manual-trade panel (Orders view): same searchable picker, same universe
// (crypto + the MT5 gold/FX/oil/indices sleeve).
manPicker = createAssetPicker({
  inputId: "manInput", listId: "manList", quickId: "manQuick", initial: "BTC/USDT",
  onSelect: () => { _manSetMsg(""); },
});
if (manPicker) manPicker.setQuick(["BTC/USDT", "ETH/USDT", "XAU/USD", "EUR/USD", "WTI/USD"]);
bindManualTrade();
bindOrdTfPills();
bindTradeRange();
startLivePolling();
loadPortfolio();
loadBrokerAccounts();
loadBrokerHealth();
loadBrokerRegistry();
loadWatchlist();
loadSignals();
applyTradePreset(tradesRange.preset); // windows the journal/donut BEFORE first fetch (was: unlabeled all-time first paint)
loadBotStatus();
loadLiveTrades();
setInterval(loadPortfolio, 60000);
setInterval(loadBrokerAccounts, 60000);
setInterval(loadBrokerHealth, 60000);
setInterval(loadWatchlist, 120000);
setInterval(loadSignals, 60000);
setInterval(loadOrders, 15000);
setInterval(autoReconcileTick, 60000);
setInterval(loadBotStatus, 15000);
loadTutor();
loadAiTest();
setInterval(loadAiTest, 30000);
loadTradeStats();
setInterval(loadTradeStats, 30000);
loadForex();
setInterval(loadForex, 60000);
