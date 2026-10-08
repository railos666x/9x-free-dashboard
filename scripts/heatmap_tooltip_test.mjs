/**
 * Behavioral test for the Trading Activity heatmap tooltip fix.
 * Extracts the REAL formatter from web/dashboard/dashboard.js (verbatim from disk)
 * and drives it against the same 180-day calendar construction the app builds.
 * Run: node scripts/_tmp_tooltip_test.mjs   (from cryptoautomation/)
 */
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../web/dashboard/dashboard.js", import.meta.url), "utf8");

function fmtUSD(n) {
  return "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// --- rebuild the app's calendar EXACTLY as dashboard.js does ----------------
const pnlMap = {
  "2026-03-20": 150.25, "2026-05-01": -80.5, "2026-08-17": -332.11,
};
let maxAbs = 1;
for (const h of Object.entries(pnlMap)) maxAbs = Math.max(maxAbs, Math.abs(h[1]));

const today = new Date();
const start = new Date(today);
start.setDate(start.getDate() - 180);
const allDays = [];
for (let d = new Date(start); d <= today; d.setDate(d.getDate() + 1)) {
  allDays.push(d.toISOString().slice(0, 10));
}

const data = [];
for (const dateStr of allDays) {
  const dt = new Date(dateStr + "T12:00:00Z");
  const dow = (dt.getDay() + 6) % 7;
  const weekNum = Math.floor((dt - start) / (7 * 864e5));
  const pnl = pnlMap[dateStr] || 0;
  const hasTrade = dateStr in pnlMap;
  data.push({
    value: [weekNum, dow, pnl],
    dayIndex: allDays.indexOf(dateStr),
    hasTrade,
  });
}
// What the chart actually receives after my series-data fix: the SAME objects.
const seriesData = data;

// --- extract the heatmap tooltip formatter verbatim -------------------------
const anchor = src.indexOf("heatmapChart.setOption");
if (anchor < 0) throw new Error("heatmapChart.setOption not found");
const marker = "formatter: (p) => {";
const mStart = src.indexOf(marker, anchor);
if (mStart < 0) throw new Error("heatmap tooltip formatter not found");

// find the matching closing brace of the arrow function body
let depth = 0, end = -1;
for (let j = mStart + marker.length - 1; j < src.length; j++) {
  const c = src[j];
  if (c === "{") depth++;
  else if (c === "}") { depth--; if (depth === 0) { end = j; break; } }
}
if (end < 0) throw new Error("formatter body not balanced");
const fnBody = src.slice(mStart + marker.length - 1, end + 1); // the { ... } block
const formatter = new Function("allDays", "fmtUSD", `return ((p) => ${fnBody});`)(allDays, fmtUSD);

let failures = 0;
const fail = (msg) => { failures++; console.error("  FAIL:", msg); };

// --- TEST 1: structural integrity of dayIndex ------------------------------
console.log(`calendar: ${allDays.length} days (${allDays[0]} .. ${allDays[allDays.length - 1]}), tz=${Intl.DateTimeFormat().resolvedOptions().timeZone}`);
if (data.length !== allDays.length) fail(`data length ${data.length} != allDays ${allDays.length}`);
const selfMapOk = data.every((d, i) => d.dayIndex === i && allDays[d.dayIndex] !== undefined);
console.log(`TEST 1 dayIndex integrity (every datum maps into allDays): ${selfMapOk ? "PASS" : "FAIL"}`);
if (!selfMapOk) fail("dayIndex integrity");

// --- TEST 2: NEW formatter returns the correct date for EVERY cell, fed
// the EXACT series data the chart now passes to ECharts (rich objects kept)
let wrongDates = 0;
for (let i = 0; i < seriesData.length; i++) {
  const html = formatter({ data: seriesData[i] }); // ECharts calls formatter(p) with p.data = datum
  const want = allDays[i];
  if (!html.startsWith(want)) { wrongDates++; if (wrongDates <= 3) fail(`cell ${i}: tooltip "${html.slice(0, 40)}" does not start with ${want}`); }
}
console.log(`TEST 2 new formatter correct date on all ${seriesData.length} cells: ${wrongDates === 0 ? "PASS" : `FAIL (${wrongDates} wrong)`}`);
if (wrongDates) fail("new formatter dates");

// --- TEST 3: OLD formula + STRIPPED data (the pre-fix series payload) --
const oldFormatter = (p) => {
  const v = p.data.value;
  return allDays[v[0] * 7 + v[1]][0]; // reading [0] off undefined = the old crash
};
const stripped = data.map((d) => [d.value[0], d.value[1], d.value[2]]); // old map() behavior
let oldThrew = 0, oldWrong = 0;
for (const arr of stripped) {
  try {
    oldFormatter({ data: { value: arr } });
  } catch { oldThrew++; continue; }
  const v = arr;
  if (allDays[v[0] * 7 + v[1]] !== allDays[stripped.indexOf(arr)]) oldWrong++;
}
console.log(`TEST 3 old formula on this machine: ${oldThrew} cells THROW (the 'reading 0' crash), ${oldWrong} silently misdated → old bug ${oldThrew + oldWrong > 0 ? "REPRODUCED (fix justified)" : "not triggered in this tz (fix still correct: date from dayIndex is exact)"}`);

// --- TEST 4: stray ECharts params must never throw --------------------------
const strays = [
  ["undefined", undefined], ["null", null], ["empty obj", {}],
  ["missing data", { value: [0, 0, 5] }], ["data null", { data: null }],
  ["empty value", { data: { value: [] } }], ["value[0]*7 out of range", { data: { value: [999, 9, 1] } }],
];
let strayThrew = 0;
for (const [name, p] of strays) {
  try {
    const out = formatter(p);
    console.log(`  stray "${name}" → ${JSON.stringify(out)}`);
  } catch (e) { strayThrew++; fail(`stray "${name}" threw: ${e.message}`); }
}
console.log(`TEST 4 stray params: ${strayThrew === 0 ? "PASS (0 throws)" : `FAIL (${strayThrew} throws)`}`);

// --- TEST 5: tooltip content correctness for a real trade day --------------
const tradeCell = seriesData.find((d) => d.hasTrade);
const html = formatter({ data: tradeCell });
const pnl = pnlMap[allDays[tradeCell.dayIndex]];
const ok = html.includes(allDays[tradeCell.dayIndex]) && html.includes(fmtUSD(pnl)) && (pnl < 0 ? html.includes("#ff5c5c") : html.includes("#2ecc8f"));
console.log(`TEST 5 real trade cell tooltip: ${ok ? "PASS" : "FAIL"} → ${html.replace(/<[^>]+>/g, "")}`);
if (!ok) fail("trade cell tooltip content");

// --- TEST 6: window starting mid-week (the case that breaks the old formula).
// Shift the 181-day window +2 days so it starts on a Wednesday; keep building
// weekNum from the (shifted) window start, exactly like renderHeatmap does.
{
  const start2 = new Date(start);
  start2.setDate(start2.getDate() + 2);
  const allDays2 = allDays.slice(2); // window shifted: first two days excluded
  const rich2 = [], strip2 = [];
  for (const dateStr of allDays2) {
    const dt = new Date(dateStr + "T12:00:00Z");
    const dow = (dt.getDay() + 6) % 7;
    const weekNum = Math.floor((dt - start2) / (7 * 864e5));
    rich2.push({ value: [weekNum, dow, 0], dayIndex: allDays.indexOf(dateStr) });
    strip2.push([weekNum, dow, 0]);
  }
  let newWrong = 0, oldWrong2 = 0, oldThrew2 = 0;
  for (let i = 0; i < rich2.length; i++) {
    if (!formatter({ data: rich2[i] }).startsWith(allDays2[i])) newWrong++;
    const v = strip2[i];
    const viaOld = allDays[v[0] * 7 + v[1]];
    if (viaOld === undefined) oldThrew2++;
    else if (viaOld !== allDays2[i]) oldWrong2++;
  }
  console.log(`TEST 6 Wednesday-start window (${allDays2.length} cells): NEW formatter wrong=${newWrong} ${newWrong === 0 ? "PASS" : "FAIL"}; OLD stripped-data formula: ${oldThrew2} throw + ${oldWrong2} misdated (fix required: ${oldThrew2 + oldWrong2 > 0 ? "YES" : "no"})`);
  if (newWrong !== 0) fail("new formatter on shifted window");
  if (oldThrew2 + oldWrong2 === 0) console.log("  NOTE: this tz/start combo happens to align — fix is still required for other starts");
}

console.log(failures === 0 ? "\nALL TESTS PASSED" : `\n${failures} FAILURES`);
process.exit(failures === 0 ? 0 : 1);
