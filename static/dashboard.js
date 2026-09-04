"use strict";

const POLL_SECONDS = parseInt(document.body.dataset.poll || "5", 10) || 5;
const STALE_MINUTES = parseInt(document.body.dataset.stale || "15", 10) || 15;
const HARD_STOP = parseFloat(document.body.dataset.hardStop);   // -8
const STOP_BUFFER = parseFloat(document.body.dataset.stopBuffer) || 2; // 2pp

let equityChart = null;
let lastPayload = null;       // JSON snapshot of the previous /api/status render
let pendingButtons = new Set(); // buttons currently disabled while a POST runs

function el(id) { return document.getElementById(id); }

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtMoney(v, digits) {
  if (v == null || isNaN(v)) return "—";
  return "$" + Number(v).toLocaleString("en-US", {
    minimumFractionDigits: digits == null ? 2 : digits,
    maximumFractionDigits: digits == null ? 2 : digits,
  });
}

function clsForSign(v) {
  if (v == null || isNaN(v) || v === 0) return "neutral";
  return v > 0 ? "positive" : "negative";
}

function signed(v, digits) {
  if (v == null || isNaN(v)) return "—";
  const s = Number(v).toFixed(digits == null ? 2 : digits);
  return (v > 0 ? "+" : "") + s;
}

// ---------- section rendering ----------
function renderMarket(m) {
  const badge = el("market-badge");
  if (m && typeof m.is_open === "boolean") {
    if (m.is_open) {
      badge.className = "badge badge-open";
      badge.textContent = "Market: OPEN";
    } else {
      badge.className = "badge badge-closed";
      badge.textContent = "Market: CLOSED";
    }
  } else {
    badge.className = "badge badge-loading";
    badge.textContent = "Market: unknown";
  }
  const times = el("market-times");
  if (m && m.is_open) {
    times.textContent = `Next close: ${fmtTime(m.next_close)}`;
  } else if (m) {
    times.textContent = `Next open: ${fmtTime(m.next_open)}`;
  } else {
    times.textContent = "";
  }
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return String(iso).slice(11, 16);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) +
    " · " + d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function renderAgent(paused) {
  const badge = el("agent-badge");
  const btn = el("pause-btn");
  if (paused) {
    badge.className = "badge badge-paused";
    badge.textContent = "Agent: Paused";
    btn.textContent = "Resume";
    btn.classList.remove("btn-secondary");
    btn.classList.add("btn-primary");
  } else {
    badge.className = "badge badge-running";
    badge.textContent = "Agent: Running";
    btn.textContent = "Pause";
    btn.classList.remove("btn-primary");
    btn.classList.add("btn-secondary");
  }
  btn.disabled = false;
}

function renderAccount(account, returns) {
  setText("acct-cash", account && account.cash != null ? fmtMoney(account.cash) : "—");
  setText("acct-portfolio", account && account.portfolio_value != null ? fmtMoney(account.portfolio_value) : "—");
  setText("acct-buying", account && account.buying_power != null ? fmtMoney(account.buying_power) : "—");

  const t = (returns && returns.total) || {};
  const tEl = el("acct-total");
  tEl.textContent = t.value != null ? fmtMoney(t.value) : "—";
  tEl.className = "card-value " + clsForSign(t.value);
  el("acct-total-pct").textContent = t.pct != null ? `${signed(t.pct, 3)}% since baseline` : "since baseline";

  const d = (returns && returns.today) || {};
  const dEl = el("acct-today");
  dEl.textContent = d.value != null ? fmtMoney(d.value) : "—";
  dEl.className = "card-value " + clsForSign(d.value);
  el("acct-today-pct").textContent = d.pct != null ? `${signed(d.pct, 3)}% vs prior close` : "vs prior close";
}

function setText(id, txt) {
  const node = el(id);
  if (node) node.textContent = txt;
}

function renderGoal(goal) {
  const g = goal || {};
  const goalPct = g.goal_pct;
  const achieved = !!g.achieved;
  const status = el("goal-status");
  const fill = el("goal-progress");

  // Progress = current return% / goal%. We get current return from the account
  // render call, so pass it via a module field set in renderStatus.
  const currentReturn = goalCurrentReturn(); // returns number or NaN
  if (goalPct != null && !isNaN(currentReturn)) {
    let pct = goalPct > 0 ? (currentReturn / goalPct) * 100 : 0;
    pct = Math.max(0, Math.min(100, pct));
    fill.style.width = pct + "%";
  } else {
    fill.style.width = "0%";
  }

  if (goalPct == null) {
    status.textContent = "No goal set yet.";
    status.className = "goal-status";
  } else if (achieved) {
    status.textContent = `Goal reached today (${goalPct}%) — positions closed.`;
    status.className = "goal-status achieved";
  } else {
    status.textContent = `In progress — auto-closes all positions when return hits ${goalPct}%.`;
    status.className = "goal-status";
  }
}

// Holds the latest Total Return % so the goal progress bar can reference it.
let _lastReturnPct = NaN;

function goalCurrentReturn() {
  return _lastReturnPct;
}

function renderPositions(positions, cfg) {
  const tbody = el("positions-body");
  if (!positions || !positions.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">No open positions.</td></tr>';
    return;
  }
  let html = "";
  const hard = (cfg && cfg.hard_stop_loss_pct != null) ? cfg.hard_stop_loss_pct : HARD_STOP;
  const buffer = (cfg && cfg.stop_warning_buffer_pct != null) ? cfg.stop_warning_buffer_pct : STOP_BUFFER;
  for (const p of positions) {
    const plpc = p.unrealized_plpc;
    const plpcEl = plpc != null ? `${signed(plpc, 2)}%` : "—";
    const plpcCls = clsForSign(plpc);
    const warn = (plpc != null && hard != null) && (plpc - hard) <= buffer && plpc <= 0;
    const warnBadge = warn ? '<span class="stop-warn-badge" title="Within ' + buffer + 'pp of hard stop-loss">⚠ near stop</span>' : "";
    const uid = p.symbol + "-liq";
    html += "<tr>"
      + `<td>${esc(p.symbol)}</td>`
      + `<td>${esc(p.qty)}</td>`
      + `<td>${p.avg_entry_price != null ? fmtMoney(p.avg_entry_price, 2) : "—"}</td>`
      + `<td>${p.current_price != null ? fmtMoney(p.current_price, 2) : "—"}</td>`
      + `<td>${p.market_value != null ? fmtMoney(p.market_value, 2) : "—"}</td>`
      + `<td class="${plpcCls}">${p.unrealized_pl != null ? fmtMoney(p.unrealized_pl, 2) : "—"}</td>`
      + `<td class="${plpcCls}">${plpcEl}${warnBadge}</td>`
      + `<td><button class="btn btn-danger-outline btn-sm" id="${esc(uid)}" data-symbol="${esc(p.symbol)}">Liquidate</button></td>`
      + "</tr>";
  }
  tbody.innerHTML = html;
  tbody.querySelectorAll("button[data-symbol]").forEach((btn) => {
    btn.addEventListener("click", () => liquidate(btn.dataset.symbol, btn));
  });
}

function macdCell(h) {
  if (h == null || isNaN(h)) return "—";
  const cls = h >= 0 ? "macd-up" : "macd-down";
  const arrow = h >= 0 ? "▲" : "▼";
  return `<span class="${cls}">${arrow} ${Number(h).toFixed(3)}</span>`;
}

function trendCell(trend) {
  const t = (trend || "").toLowerCase();
  if (t === "mixed" || t === "") return `<span class="chip off"></span><span class="chip off"></span> <span class="note-text">Mixed</span>`;
  const chips = `<span class="chip ${t === "above both" ? "on" : "off"}"></span><span class="chip ${t === "below both" ? "on" : "off"}"></span>`;
  const label = t === "above both" ? "Above both" : "Below both";
  return `${chips} <span class="note-text">${label}</span>`;
}

function ageText(e) {
  const as_of = e.data_as_of;
  if (!as_of) return "n/a";
  if (e.market_open) return `${e.data_age_minutes}m ago`;
  let day = "?";
  try {
    const d = new Date(as_of);
    if (!isNaN(d)) day = d.toLocaleDateString(undefined, { weekday: "short", month: "2-digit", day: "2-digit" });
  } catch (_) {}
  return `closed - ${day} close`;
}

function marketCell(e) {
  const chipClass = e.market_open ? "chip-market open" : "chip-market closed";
  const chipLabel = e.market_open ? "Open" : "Closed";
  return `<span class="${chipClass}">${chipLabel}</span><span class="note-text">${esc(ageText(e))}</span>`;
}

// --- pagination state for the scan table ---
let _allScans = [];     // full dataset (newest first) from /api/status
let _page = 0;
let _pageSize = 15;

function renderScans(range) {
  const tbody = el("scan-body");
  if (!range.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty">No decisions yet — run the agent first.</td></tr>';
    hidePagination();
    return;
  }
  let html = "";
  for (const e of range) {
    const staleCell = e.stale ? ' class="stale"' : "";
    const decidedTrade = (e.action === "BUY" || e.action === "SELL");
    const executed = decidedTrade && !!e.order_id;
    let actionCell;
    if (decidedTrade && !executed) {
      const shortNote = e.note ? ` (${e.note})` : " (not executed)";
      actionCell = `<span class="action action-outlined action-${esc(e.action)}">${esc(e.action)}</span><span class="note-text">${esc(shortNote)}</span>`;
    } else {
      actionCell = `<span class="action action-${esc(e.action)}">${esc(e.action)}</span>`;
    }
    html += "<tr>"
      + `<td>${esc(e.timestamp)}</td>`
      + `<td>${esc(e.symbol)}</td>`
      + `<td>${actionCell}</td>`
      + `<td>${esc(e.confidence)}</td>`
      + `<td>${esc(e.price)}</td>`
      + `<td>${marketCell(e)}</td>`
      + `<td>${e.rsi_14 != null ? Number(e.rsi_14).toFixed(1) : "—"}</td>`
      + `<td>${macdCell(e.macd_hist)}</td>`
      + `<td>${trendCell(e.sma_trend)}</td>`
      + `<td>${esc(e.order_id)}</td>`
      + "</tr>";
  }
  tbody.innerHTML = html;
}

function updateScanPagination() {
  const total = _allScans.length;
  const pageCount = Math.max(1, Math.ceil(total / _pageSize));
  if (_page >= pageCount) _page = pageCount - 1;
  const start = _page * _pageSize;
  const end = Math.min(start + _pageSize, total);
  renderScans(_allScans.slice(start, end));

  const pag = el("scan-pagination");
  if (total <= _pageSize) {
    pag.hidden = true;
    return;
  }
  pag.hidden = false;
  el("pg-info").textContent = `${total} entries · showing ${start + 1}–${end}`;
  el("pg-page").textContent = `Page ${_page + 1} of ${pageCount}`;
  el("pg-prev").disabled = _page <= 0;
  el("pg-next").disabled = _page >= pageCount - 1;
}

function hidePagination() {
  if (el("scan-pagination")) el("scan-pagination").hidden = true;
}

function setScanData(rows) {
  const changed = JSON.stringify(rows) !== JSON.stringify(_allScans);
  _allScans = rows;
  updateScanPagination();
  return changed;
}

function renderEquity(points) {
  const canvas = el("equity-chart");
  if (!canvas) return;

  // Fewer than 2 points and Chart.js can't draw a line — show a friendly note
  // instead of silently rendering a blank chart.
  if (!points || points.length < 2) {
    hideChart(points && points.length === 1);
    return;
  }
  showChart();

  const labels = [];
  const data = [];
  for (const pt of points || []) {
    labels.push(new Date(pt.timestamp).toLocaleDateString(undefined, { month: "short", day: "numeric" }));
    data.push(pt.equity);
  }
  if (!equityChart) {
    const ctx = canvas.getContext("2d");
    equityChart = new Chart(ctx, {
      type: "line",
      data: { labels, datasets: [{ label: "Portfolio Value", data, borderColor: "#3182ce", backgroundColor: "rgba(49,130,206,.12)", fill: true, tension: .3, pointRadius: 0 }] },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { y: { ticks: { callback: (v) => "$" + v } } },
      },
    });
  } else {
    equityChart.data.labels = labels;
    equityChart.data.datasets[0].data = data;
    equityChart.update();
  }
}

function chartNote(message) {
  let note = el("equity-note");
  if (!note) {
    note = document.createElement("div");
    note.id = "equity-note";
    note.className = "chart-note";
    el("equity-chart").parentElement.appendChild(note);
  }
  note.textContent = message;
}

function hideChart(hasOnePoint) {
  const canvas = el("equity-chart");
  if (equityChart) { equityChart.destroy(); equityChart = null; }
  canvas.style.display = "none";
  const msg = hasOnePoint
    ? "Not enough equity history yet — chart will appear after a few trading sessions."
    : "Not enough equity history yet — check back after a few trading sessions.";
  chartNote(msg);
}

function showChart() {
  const canvas = el("equity-chart");
  canvas.style.display = "block";
  const note = el("equity-note");
  if (note) note.remove();
}

// ---------- POST helpers with loading state ----------
async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return res.json();
}

async function withLoading(btn, fn) {
  if (!btn || pendingButtons.has(btn)) return;
  pendingButtons.add(btn);
  const original = btn.textContent;
  btn.disabled = true;
  btn.dataset.orig = original;
  btn.textContent = "…";
  try {
    await fn();
  } finally {
    pendingButtons.delete(btn);
    // Only restore the original text if the button is still showing our loading
    // placeholder. If a render() already updated it meanwhile (e.g. renderAgent
    // flipping Pause/Resume after refresh()), leave that newer text alone so it
    // doesn't get stomped back to the pre-click value.
    btn.disabled = false;
    if (btn.textContent === "…") {
      btn.textContent = original;
    }
  }
}

async function liquidate(symbol, btn) {
  if (!confirm(`Liquidate your entire ${symbol} position?`)) return;
  await withLoading(btn, async () => {
    await postJSON(`/liquidate/${encodeURIComponent(symbol)}`);
    await refresh();
  });
}

async function setGoal() {
  const input = el("goal-input");
  const val = parseFloat(input.value);
  const btn = el("goal-btn");
  if (isNaN(val) || val <= 0) { alert("Enter a positive target % first."); return; }
  await withLoading(btn, async () => {
    await postJSON("/api/goal", { goal_pct: val });
    await refresh();
  });
}

async function togglePause() {
  const btn = el("pause-btn");
  const currently = btn.textContent === "Resume" ? true : false; // running -> pause
  await withLoading(btn, async () => {
    await postJSON("/api/pause", { paused: !currently });
    await refresh();
  });
}

// ---------- /api/status polling with diff-based rendering ----------
function sectionEqual(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function renderStatus(data) {
  const prev = lastPayload || {};

  if (!sectionEqual(data.market, prev.market)) renderMarket(data.market);
  if (!sectionEqual(data.paused, prev.paused)) renderAgent(!!data.paused);

  renderAccount(data.account, data.returns);
  if (data.returns && data.returns.total && data.returns.total.pct != null) {
    _lastReturnPct = data.returns.total.pct;
  } else {
    _lastReturnPct = NaN;
  }

  // Goal progress bar depends on the live return% too, so always refresh it
  // (it tracks the current return even when the goal object is unchanged).
  renderGoal(data.goal);

  // Positions re-render on data change; liquidate buttons must stay wired, so
  // always rebuild them when the table changed.
  if (!sectionEqual(data.positions, prev.positions)) renderPositions(data.positions, data.config);
  // Scan table is paginated client-side over the full set; only re-render when
  // the underlying rows changed so the current page doesn't flicker.
  if (!sectionEqual(data.scans, prev.scans)) setScanData(data.scans);

  // Equity curve data doesn't come through /api/status (separate endpoint),
  // fetched once on load + periodically.
  lastPayload = data;
}

async function refresh() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) return;
    const data = await res.json();
    renderStatus(data);
  } catch (_) { /* transient; next poll retries */ }
}

async function loadEquity() {
  try {
    const res = await fetch("/api/equity-history");
    // DEBUG: log the raw backend response so we can see exactly what
    // get_portfolio_history() returns (point count / errors) when the account
    // is new or the chart renders blank.
    if (!res.ok) {
      console.warn("equity-history responded", res.status, await res.text().catch(() => ""));
      return;
    }
    const data = await res.json();
    console.log("/api/equity-history raw response:", data);
    renderEquity((data && data.points) || []);
  } catch (e) {
    console.warn("loadEquity error:", e);
  }
}

function init() {
  el("pause-btn").addEventListener("click", togglePause);
  el("goal-btn").addEventListener("click", setGoal);

  const prev = el("pg-prev");
  const next = el("pg-next");
  const size = el("pg-size");
  if (prev) prev.addEventListener("click", () => { _page--; updateScanPagination(); });
  if (next) next.addEventListener("click", () => { _page++; updateScanPagination(); });
  if (size) size.addEventListener("change", (ev) => { _pageSize = parseInt(ev.target.value, 10); _page = 0; updateScanPagination(); });

  refresh().then(() => {
    renderMarket(lastPayload && lastPayload.market);
    renderAgent(lastPayload && lastPayload.paused);
  });
  setInterval(refresh, POLL_SECONDS * 1000);
  loadEquity();
}

init();
