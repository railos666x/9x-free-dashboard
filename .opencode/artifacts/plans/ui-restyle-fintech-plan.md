# UI Restyle Plan — SATS Dashboard → Modern Fintech Look

*Created 2026-08-19 · Scope: full visual restyle, all views kept, zero behavior change*

## 1. Goal & references

Restyle `web/dashboard/` (the only UI, served by FastAPI at `/`) to a **modern fintech-app look** — Robinhood/Revolut class: dark, card-based, big numbers, restrained color, clean data tables — plus solid dashboard UX (clear hierarchy: one hero number → key stats → charts → tables).

References given by user:
- A screenshot ("modern like that")
- Robinhood/Revolut fintech style (confirmed)
- Full restyle, **same views** (confirmed)
- UX Planet "Dashboard that works" guide principles: clear hierarchy, scannable KPIs, consistent cards, semantic color, honest states

## 2. Tech-stack decision

**Keep vanilla JS + ECharts + plain CSS. No framework, no build step.**

| Option | Verdict | Why |
|---|---|---|
| **A. Vanilla restyle (this plan)** | ✅ Recommended | Look is a design-system job: colors, type, spacing, radii, chart theming. `dashboard.js` (2,355 lines, 20 endpoints, live polling) stays untouched except ~20 color constants. Zero build, zero new deps, zero risk to a live trading UI. Iteration loop = edit CSS, refresh. |
| B. React/Next or SvelteKit + Vite rewrite | ❌ Not now | Multi-day re-implementation of 2,355 lines across 8 views + new serving path in `server.py` + regression risk on a trading surface. Modern-tooling benefits (HMR, components) don't outweigh risk/time at this stage. Revisit (SvelteKit) only if UI scope keeps growing (e.g. real-time streaming, editor features). |

This honors "any language as long as it's fast": fastest path is no language change.

## 3. Design system (new `:root` tokens)

Dark, layered, one accent. Numbers always tabular.

```css
:root {
  /* Surfaces (layered elevation, no heavy shadows) */
  --bg:        #0a0c10;   /* page */
  --surface:   #10131a;   /* cards */
  --surface-2: #161a23;   /* nested: tables, inputs, pills track */
  --border:        rgba(255,255,255,.07);
  --border-strong: rgba(255,255,255,.14);

  /* Text */
  --text:  #f2f5f9;
  --muted: #98a2b3;
  --faint: #667085;

  /* Semantic (used ONLY for money & direction) */
  --green: #0ecb81;   /* positive PnL, LONG, success — Robinhood green */
  --red:   #f6465d;   /* negative PnL, SHORT */
  --gold:  #f5b50a;   /* warnings, warnings-only */
  --accent:#0ecb81;   /* brand accent = green (fintech: green = brand) */
  --accent-soft: rgba(14,203,129,.12);
  --info:  #4c9aff;   /* non-money chart series only */
}
```

Typography & geometry:
- Font stack: `Inter, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif` (system-loaded; no webfont download — keeps it fast/offline-safe)
- **Hero number** (`total-value`, portfolio value): 44–56px, weight 600, tight `-0.02em` tracking
- All numeric cells: `font-variant-numeric: tabular-nums`
- Labels: 11px, uppercase, `letter-spacing: .06em`, `--muted`
- Radii: cards **16px**, controls **10px**, pills **999px**, modal **20px**
- Spacing: 8px grid; card padding 20px; card→card gap 16px
- Elevation: 1px `--border` only; hover → `--border-strong` + slight translateY(-1px) on interactive cards; no box-shadows except the server-dot glow
- `page-bg`: two very soft radial gradients (accent 4% opacity top-left, info 3% mid-right) over `--bg` — subtle depth, static

### Component specs

| Component (existing classes) | New look |
|---|---|
| `card` / `card-head` / `card-title` | surface, 16px radius, 1px border, 20px padding; title 13px 600 |
| `stat-card` label/value/sub | 11px uppercase muted label; 22–24px 650 tabular value (green/red only when PnL); 12px sub |
| `asset-cards` (4 top cards) | surface cards, icon chip with 12% tint, value 20px tabular, 12px change chip (green/red soft pill) |
| `tf-pill` / `mini-tab` | **segmented control**: track `--surface-2` radius 10, active pill `--surface` + text, inactive muted; 12px |
| `signal-badge` / `badge-long/short/hold` | soft-tint pills: 14% bg + 90% text; LONG green, SHORT red, HOLD neutral gray |
| `ticker-pill` | 12px, radius 999, price tabular, change colored; subtle green/red flash on update (0.6s transition) |
| `crypto-table` / `orders-table` | sticky header 11px uppercase muted; 44px rows; hover row `--surface-2`; numbers right-aligned tabular; `why-cell` 12px muted, 2-line clamp |
| `spark` (SVG sparkline) | 60×22, stroke 1.5, green/red from `UP`/`DOWN`, no fill (or 8% gradient) |
| `dist-bar` / `dist-seg` | 8px radius bar, 2px gaps between segments, legend dots 8px |
| `btn-primary` | accent green bg, black text 600 (Robinhood CTA), radius 10, hover brighten 6% |
| `btn-warn` | transparent, 1px `--red` border, red text |
| `nav-item` | 13px, radius 10, active = `--accent-soft` bg + green text; inactive muted, hover `--surface-2` |
| `topbar` | 56px, `--bg` 80% + backdrop-blur 8px, 1px border-bottom |
| `server-dot` | 8px, green glow when ok / red when down (kept) |
| `modal` | backdrop `rgba(0,0,0,.6)` + blur 4px; modal surface 20px radius, 1px border-strong |
| `muted` | `--muted` |
| Charts (ECharts) | themed in §5 |
| States | loading = 3-line shimmer skeleton (CSS-only); empty = centered 13px muted with icon; reconnect badge = gold soft pill |

Accessibility: contrast ≥ 4.5:1 for body text; focus-visible rings on buttons/inputs (2px accent); respects `prefers-reduced-motion` (disable flashes/hovers).

## 4. Per-view notes (structure & JS untouched)

| View | Visual focus |
|---|---|
| Dashboard (`view-dashboard`) | Hero `Total assets` number up top with delta chip; `stats-grid` → uniform 7 stat cards; portfolio/total area charts get green gradient fills; radar + heatmap keep shape, new palette |
| Assets (`view-assets`) | Full-bleed table card; search + segmented TF; sparkline column; change column colored chips |
| My portfolio (`view-portfolio`) | Holdings list rows with asset icon chips; balance card with distribution bar |
| Bots (`view-bots`) | Controls row (picker + TF + Run button); backtest results as metric chips (`bt-metrics`) + verdict banner (green/red tint) |
| Orders (`view-orders`) | Bot status grid → key/value tiles; journal table with PnL coloring; donut pair in `dual-donut-wrap` with broker dots; live trades rows with SL/TP mini chips |
| AI Test (`view-aitest`) | Pipeline layers as status pills (`ai-layer-pill`): pass=green tint, blocked=red tint, off=neutral; circuit breaker level meter; test-trades table |
| Signals (`view-signals`) | Signal cards: direction ribbon (green/red left border 3px), confidence meter, reasons 12px muted; chart pane with level dots |
| Learn (`view-learn`) | Topic chips as pills; markdown body 14px/1.6, examples in `--surface-2` blocks |

## 5. ECharts theming (JS — color-only edits)

Keep every `setOption` structure; replace colors:

1. Consts at `dashboard.js:9-11`:
   - `ACCENT = "#4c9aff"` → `"#0ecb81"`
   - `UP = "#2ecc8f"` → `"#0ecb81"`
   - `DOWN = "#ff5c5c"` → `"#f6465d"`
2. Tooltip style (≈lines 679, 953, 992): bg `#14161c` → `#161a23`, border `#24262e` → `rgba(255,255,255,.08)`, text `#e6e9f0` → `#f2f5f9`
3. Axis/split lines (≈957–1003): axis `#24262e` → `rgba(255,255,255,.06)`, labels `#8a94a6` → `#98a2b3`, splitLine → `rgba(255,255,255,.05)` (kept)
4. Series: portfolio/total area → gradient `rgba(14,203,129,.30)→0` (green, Robinhood style); bars (967) → `#0ecb81`/`#4c9aff` alternate, radius kept; donut PnL (675,702) → `UP`/`DOWN` consts; donut border `#121223` → `#10131a`; heatmap level colors (2107) → red→gold→green ramp; tooltip PnL (2390) → `UP`/`DOWN`
5. Sparkline SVG (541–543) already uses `var(--green/--red)` — flows from tokens automatically
6. Any remaining hexes: replace with the const/var equivalents (full list captured during execution by grepping `#[0-9a-f]{6}` in `dashboard.js`)

Asset-distribution palette (`state.portfolio.distribution[].color`) — refresh to a 6-color set that reads well on dark: `#0ecb81 #4c9aff #f5b50a #a78bfa #2dd4bf #f6465d`.

## 6. File changes (exact)

| File | Change | Risk |
|---|---|---|
| `web/dashboard/dashboard.css` | **Full rewrite** (~960 lines → similar). Same selector set: every class currently defined must exist in the new file (extracted list kept as checklist; verified by regex diff before/after). | Low — CSS only |
| `web/dashboard/dashboard.js` | Color constants (lines 9–11) + ~15 hardcoded hex swaps in chart options + distribution palette. **No structural/logic changes** (polling, handlers, `innerHTML` templates, all element IDs intact). | Low — cosmetic strings |
| `web/dashboard/index.html` | **No changes** (all classes it uses stay). Optional: add `<meta name="color-scheme" content="dark">`. | None |

**Invariants (must hold, verified):**
- All element `id`s unchanged (JS queries them)
- All class names unchanged (~250 known; diff-verified)
- Inline `onclick` handlers, ECharts instance names, `fetch` endpoints (20) unchanged
- `node --check web/dashboard/dashboard.js` passes

## 7. Execution order

1. Backups already implicit via git (files committed at 6aa6b9a state? — check `git status`; uncommitted changes exist: keep current versions as base, work in place; rollback = `git checkout -- web/dashboard/` only reverts committed state, so first snapshot current CSS/JS to `web/dashboard/.backup-pre-restyle/` as safety copy)
2. Rewrite `dashboard.css` section by section: tokens → layout (topbar/sidebar/content) → primitives (cards, pills, badges, buttons, inputs) → tables → per-view blocks → charts/heatmap/donut → modal → responsive (≤1100px: sidebar → icon rail; ≤800px: rows stack)
3. Apply JS color swaps (grep-verified, zero other edits)
4. Optional 1-line HTML meta add
5. Verify (§8) → visual pass with user (user opens http://localhost:8501 and confirms each view; iterate)

## 8. Verification

1. `node --check web/dashboard/dashboard.js`
2. Selector-parity check: `grep -o` class list from old vs new CSS → diff empty
3. Start server; `GET /` returns HTML referencing `/dash-assets/*`; static assets 200 (no 404s in console-level check via curl status codes)
4. Smoke endpoints still 200: `/api/health /api/watchlist /api/signals /api/orders /api/bot/status /api/test-trades`
5. Manual visual checklist (8 views + modal + loading/empty states) with user at :8501
6. `git diff --stat` reviewed before any commit (commit only if user asks)

## 9. Out of scope / later phases

- No framework migration (revisit only if UI scope grows: WebSocket streaming, live order editing, mobile-native shell)
- No API changes, no new views, no JS behavior changes
- Light theme: out of scope (dark-first fintech reference)
- Possible later: CSS custom-property theming toggle (dark/light) once stable

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| A missed class name breaks a view's styling | Selector-parity diff gate (step 8.2) before declaring done |
| Hardcoded chart color missed → mismatched palette | Grep all hex literals post-edit; visual pass per view |
| Responsive regressions | Width breakpoints in §7.4; manual check at 1440/1024/768 |
| User's screenshot style differs from my interpretation | Design tokens isolated in `:root` (block in §3) — a look-change after first pass is a 10-line token swap, not a rework |