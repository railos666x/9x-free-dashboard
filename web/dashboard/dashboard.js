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

// ── API helper with timeout + one retry on transient failures ───────
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
  } catch (e) {
    // A connection reset/refused usually means the server was mid-restart.
    // GETs retry once after a short pause so the page self-heals; POSTs
    // never auto-retry (orders must not be placed twice by accident).
    const isPost = String(opts.method || "GET").toUpperCase() === "POST";
    if (!isPost && !opts._retried) {
      await new Promise((r) => setTimeout(r, 1500));
      return api(path, { ...opts, _retried: true });
    }
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
const VIEWS = ["dashboard", "assets", "portfolio", "bots", "orders", "signals"];

function switchView(name) {
  if (!VIEWS.includes(name)) name = "dashboard";
  document.querySelectorAll(".nav-item").forEach((a) =>
    a.classList.toggle("active", a.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) =>
    v.classList.toggle("active", v.id === `view-${name}`));
  // Charts initialised inside a hidden view have zero size — resize on show.
  requestAnimationFrame(() => {
    [portValueChart, totalChart, sigChart, pnlChart].forEach((c) => c && c.resize());
  });
  if (name === "assets") loadWatchlist();
  if (name === "orders") {
    loadBotStatus();
    loadOrders();
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
    : esc(s.broker || "paper");
  const gateMin = s.arbitration_min_sources ?? 2;
  const gateLabel = gateMin === 1
    ? `test mode (single source, conf≥${s.arbitration_min_confidence ?? 45})`
    : "production (both sources)";
  const liveBlocked = !!s.live_single_source_blocked;
  const loopRunning = !!s.auto_loop_running;
  const loopLive = !!s.auto_loop_live;
  const loopLabel = !s.auto_loop
    ? "off (BOT_AUTO_LOOP=false)"
    : loopRunning ? (loopLive ? "running · LIVE (paper)" : "running · dry-run") : "stopped";
  const llmLbl = s.llm_providers == null ? "—"
    : s.llm_providers <= 0 ? "rule-based (add LLM key)"
    : `active (${s.llm_providers} provider${s.llm_providers > 1 ? "s" : ""})`;
  const stats = [
    ["Broker", brokerLabel],
    ["Source A", esc(s.signal_source_a || "math_engine")],
    ["Source B (proprietary)", s.proprietary_enabled ? "on" : "off — bot holds"],
    ["LLM overlay", llmLbl],
    ["Agreement gate", gateLabel],
    ["Live safety", liveBlocked ? "🛑 blocked (test gate)" : "ok"],
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
    loopBadge.textContent = loopRunning ? (loopLive ? "LIVE (paper)" : "DRY-RUN") : "OFF";
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
      ? (loopLive ? "Bot loop is running LIVE on the paper broker" : "Bot loop is running in dry-run (journals only)")
      : "Bot loop is stopped — Dry/Live test run one manual cycle";
  }
  const dryBtn = $("btnRunBotDry");
  const liveBtn = $("btnRunBotLive");
  if (dryBtn) dryBtn.classList.toggle("mode-active", loopRunning && !loopLive);
  if (liveBtn) liveBtn.classList.toggle("mode-active", loopRunning && loopLive);
  // Source-A picker: synced with the server, and locked while the loop runs.
  const sel = $("botSourceSelect");
  const applyBtn = $("btnApplySource");
  const srcMsg = $("botSourceMsg");
  if (sel) sel.value = s.signal_source_a || "math_engine";
  if (applyBtn) applyBtn.disabled = loopRunning;
  if (srcMsg) srcMsg.textContent = loopRunning ? "🔒 stop the loop to change source" : "";
  let cycleHtml = '<div class="muted">No cycle run yet — press “Dry test” or “Live test”.</div>';
  const c = s.last_cycle;
  if (c && c.blocked) {
    cycleHtml = `<div class="bot-cycle" style="color:var(--red,#ff5c5c);">🛑 ${esc(c.block_reason || "Live run blocked")}</div>`;
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

async function loadOrders() {
  try {
    const data = await api("/api/orders?limit=100&detail=1");
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

function renderOrders(orders, summary) {
  const b = $("ordersBody");
  if (!b) return;
  renderPnlDonut(summary);
  const strip = $("journalSummary");
  if (strip) {
    if (summary) {
      const pnlCls = summary.realized_pnl > 0 ? "up" : summary.realized_pnl < 0 ? "down" : "";
      const unCls = (summary.unrealized_pnl || 0) > 0 ? "up" : (summary.unrealized_pnl || 0) < 0 ? "down" : "";
      strip.innerHTML =
        `Open positions: <b>${summary.open_positions}</b> · ` +
        `Open mkt value: <b>$${fmt(summary.open_market_value, 2)}</b> · ` +
        `Dry-run rows: <b>${summary.dry_run}</b> · ` +
        `Realized PnL: <b class="${pnlCls}">${summary.realized_pnl > 0 ? "+" : ""}$${fmt(summary.realized_pnl, 2)}</b>` +
        ` · Unrealized: <b class="${unCls}">${(summary.unrealized_pnl || 0) > 0 ? "+" : ""}$${fmt(summary.unrealized_pnl || 0, 2)}</b>` +
        ` <button class="icon-btn" style="padding:2px 10px;font-size:12px;" onclick="reconcileBroker()" title="Sync journal statuses with the broker">⟳ Reconcile</button>`;
    } else {
      strip.innerHTML = "";
    }
  }
  if (!orders.length) {
    b.innerHTML = '<tr><td colspan="15" class="muted">No orders yet.</td></tr>';
    return;
  }
  b.innerHTML = orders.map((o) => {
    const long = o.side === "LONG";
    const pnl = o.pnl == null ? "—" : `${o.pnl > 0 ? "+" : ""}${fmt(o.pnl, 2)}`;
    const pnlCls = o.pnl > 0 ? "up" : o.pnl < 0 ? "down" : "";
    const unreal = o.unrealized_pnl == null ? "—"
      : `${o.unrealized_pnl > 0 ? "+" : ""}${fmt(o.unrealized_pnl, 2)}`;
    const unrealCls = o.unrealized_pnl > 0 ? "up" : o.unrealized_pnl < 0 ? "down" : "";
    const t = String(o.fill_time || o.created_at || "").slice(0, 19).replace("T", " ");
    const why = o.reasons
      ? `<span title="${esc(String(o.reasons))}">${esc(String(o.reasons).split(";")[0].trim())}${String(o.reasons).includes(";") ? " …" : ""}</span>`
      : "—";
    return `<tr>
      <td class="sym">${symOf(o.symbol)}</td>
      <td><span class="signal-badge ${long ? "badge-long" : "badge-short"}">${long ? "▲ LONG" : "▼ SHORT"}</span></td>
      <td>${fmt(o.qty, 4)}</td>
      <td>${fmtPrice(o.entry)}</td>
      <td>${fmtPrice(o.fill_price)}</td>
      <td>${o.exit_price != null ? fmtPrice(o.exit_price) : "—"}${o.exit_reason ? ` <span class="muted">(${esc(o.exit_reason)})</span>` : ""}</td>
      <td class="${pnlCls}">${pnl}</td>
      <td class="${unrealCls}">${unreal}</td>
      <td><span class="signal-badge ${ORDER_BADGES[o.status] || "badge-hold"}">${esc(o.status)}</span></td>
      <td class="muted">${esc(o.source)}</td>
      <td class="muted">${fmtPrice(o.current_price)}</td>
      <td class="muted">${o.market_value != null ? "$" + fmt(o.market_value, 2) : "—"}</td>
      <td class="spark-cell">${sparkline(o.spark)}</td>
      <td class="muted why-cell">${why}</td>
      <td class="muted">${esc(t)}</td>
    </tr>`;
  }).join("");
}

// ── Profit by currency (donut) ─────────────────────────────────────
let pnlChart = null;
function renderPnlDonut(summary) {
  const el = $("pnlDonut");
  const list = $("pnlBreakdown");
  const meta = $("pnlDonutMeta");
  if (!el || !summary) return;
  const rows = (summary.by_symbol || []).filter((r) => Math.abs(r.pnl) > 0.005);
  const total = summary.total_pnl || 0;
  if (meta) {
    const unreal = summary.unrealized_pnl || 0;
    meta.textContent = rows.length
      ? `${rows.length} currencies · realized ${summary.realized_pnl > 0 ? "+" : ""}$${fmt(summary.realized_pnl, 2)}`
        + ` · unrealized ${unreal > 0 ? "+" : ""}$${fmt(unreal, 2)}`
      : "";
  }
  if (!rows.length) {
    if (list) list.innerHTML = '<div class="muted">No realized or unrealized PnL yet — dry/live test trades to populate the donut.</div>';
    if (pnlChart) pnlChart.clear();
    return;
  }
  if (!pnlChart) pnlChart = echarts.init(el);
  const totalCls = total >= 0 ? "#2ecc8f" : "#ff5c5c";
  pnlChart.setOption({
    backgroundColor: "transparent",
    tooltip: {
      trigger: "item", backgroundColor: "#14161c", borderColor: "#24262e",
      textStyle: { color: "#e6e9f0" },
      formatter: (p) => `${p.name}: ${p.value > 0 ? "+" : ""}$${fmt(p.value, 0)} (${p.percent}% of gross)`,
    },
    title: {
      text: `${total >= 0 ? "+" : "−"}$${fmt(Math.abs(total), 0)}`,
      subtext: "Net PnL", left: "center", top: "37%",
      textStyle: { color: totalCls, fontSize: 22, fontWeight: 700 },
      subtextStyle: { color: "#8a94a6", fontSize: 11 },
    },
    series: [{
      type: "pie", radius: ["46%", "72%"], center: ["50%", "50%"],
      avoidLabelOverlap: true,
      itemStyle: { borderColor: "#121223", borderWidth: 2 },
      label: { color: "#8a94a6", fontSize: 11, formatter: "{b} {d}%" },
      labelLine: { lineStyle: { color: "#3a4152" } },
      data: rows.map((r) => ({
        name: symOf(r.symbol),
        value: Math.abs(r.pnl),
        itemStyle: { color: r.pnl >= 0 ? "#2ecc8f" : "#ff5c5c" },
      })),
    }],
  }, true);
  if (list) {
    list.innerHTML = rows.map((r) => {
      const pnlCls = r.pnl >= 0 ? "up" : "down";
      const sign = r.pnl >= 0 ? "+" : "";
      return `<div class="pnl-row">
        <span class="pnl-dot" style="background:${r.pnl >= 0 ? "#2ecc8f" : "#ff5c5c"}"></span>
        <span class="sym">${symOf(r.symbol)}</span>
        <span class="pnl-bar-wrap"><span class="pnl-bar" style="width:${Math.min(r.share, 100)}%;background:${r.pnl >= 0 ? "#2ecc8f" : "#ff5c5c"}"></span></span>
        <span class="pnl-val ${pnlCls}">${sign}$${fmt(r.pnl, 0)}</span>
        <span class="muted">${r.share}%</span>
        <span class="muted pnl-sub">${r.market_value > 0 ? "mkt $" + fmt(r.market_value, 0) : ""}</span>
      </div>`;
    }).join("");
  }
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
        strip.innerHTML += ` <span class="muted" style="color:var(--gold,#f0b90b)">· ⚠ ${res.foreign_count} foreign fill(s) not from this bot${syms ? `: ${syms}` : ""}</span>`;
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

async function runBotCycle(dryRun) {
  const id = dryRun ? "btnRunBotDry" : "btnRunBotLive";
  const btn = $(id);
  if (!btn) return;
  if (!dryRun) {
    const st = window.__botStatus || {};
    if (st.live_single_source_blocked) {
      alert(
        "Live test blocked by the kill-switch: the agreement gate is in " +
        "single-source test mode (ARBITRATION_MIN_SOURCES=1). Set " +
        "ALLOW_LIVE_SINGLE_SOURCE=true in .env to allow, or use " +
        "ARBITRATION_MIN_SOURCES=2 for the production gate."
      );
      return;
    }
    const ok = confirm(
      "Live test: this submits REAL orders to the configured broker " +
      `(${st.broker || "paper"}${st.alpaca_paper ? " paper" : ""}). ` +
      "Continue?"
    );
    if (!ok) return;
  }
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = dryRun ? "Running…" : "Placing orders…";
  try {
    await api("/api/bot/run", {
      method: "POST",
      body: JSON.stringify({ dry_run: !!dryRun }),
      timeout: 180000,
    });
    await Promise.all([loadBotStatus(), loadOrders()]);
  } catch (e) {
    const b = $("botStatusBody");
    if (b) b.innerHTML = `<div class="muted">Cycle failed — ${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = label;
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
  const sub = p.mode === "exchange"
    ? `Binance · ${esc(p.exchange_name || "connected")}`
    : p.mode === "alpaca"
      ? `${p.exchange_name || "Alpaca"} · cash ${fmtUSD(p.cash)} · buying power ${fmtUSD(p.buying_power)}`
        + (p.unrealized_pl != null ? ` · unrealized ${upDown(p.unrealized_pl)}` : "")
        + (p.account_pnl != null ? ` · account ${upDown(p.account_pnl)} (incl. fees)` : "")
        + (p.account_number ? ` · ${esc(p.account_number)}` : "")
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
    ? `<a class="btn-signal" title="Journal: ${esc(tr.side)} qty ${fmt(tr.qty, 4)} · ${esc(tr.status)}${tr.pnl != null ? ` · pnl ${tr.pnl > 0 ? "+" : ""}${fmt(tr.pnl, 2)}` : ""}" onclick="switchView('orders'); return false;">⏺ ${tr.status}</a>`
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

  if (titleEl) titleEl.textContent = `${symOf(d.symbol)} · ${d.interval}` + (sig && sig.direction !== "HOLD" ? (levels ? " — position levels" : " — signal levels") : "");
  const legend = legendEl;
  if (legend) {
    legend.innerHTML = sig && sig.direction !== "HOLD" && sig.entry
      ? `<b style="color:#fbbf24">Entry ${fmtPrice(sig.entry)}</b> · <b style="color:#ef4444">SL ${fmtPrice(sig.stop_loss)}</b> · <b style="color:#22c55e">TP ${fmtPrice(sig.take_profit)}</b>${levels ? " · open position levels" : " · click a signal card or search to switch asset"}`
      : "No levels for this asset — they appear here when a signal or position exists.";
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
    const interval = (intervalPicker && intervalPicker.value) || "1h";
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

// ── Boot ────────────────────────────────────────────────────────────
window.addEventListener("resize", () => {
  if (portValueChart) portValueChart.resize();
  if (totalChart) totalChart.resize();
  if (sigChart) sigChart.resize();
  if (ordChart) ordChart.resize();
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
bindOrdTfPills();
loadPortfolio();
loadWatchlist();
loadSignals();
loadOrders();
loadBotStatus();
setInterval(loadPortfolio, 60000);
setInterval(loadWatchlist, 120000);
setInterval(loadSignals, 60000);
setInterval(loadOrders, 15000);
setInterval(loadBotStatus, 15000);
