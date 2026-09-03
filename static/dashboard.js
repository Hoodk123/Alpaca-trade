"use strict";

const POLL_SECONDS = parseInt(document.body.dataset.poll || "5", 10) || 5;
const STALE_MINUTES = parseInt(document.body.dataset.stale || "15", 10) || 15;

function el(id) { return document.getElementById(id); }
function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function ageText(e) {
  const as_of = e.data_as_of;
  if (!as_of) return "n/a";
  if (e.market_open) {
    return `${e.data_age_minutes}m ago`;
  }
  let day = "?";
  try {
    const d = new Date(as_of);
    if (!isNaN(d)) {
      day = d.toLocaleDateString(undefined, { weekday: "short", month: "2-digit", day: "2-digit" });
    }
  } catch (_) { /* keep "?" */ }
  return `closed - ${day} close`;
}

function marketBadge(rows) {
  // Prefer the live market API state, else fall back to the latest row.
  return fetch("/api/market")
    .then((r) => (r.ok ? r.json() : null))
    .then((m) => {
      const isOpen = m && typeof m.is_open === "boolean" ? m.is_open : (rows.length ? rows[0].market_open : false);
      const badge = el("market-badge");
      if (isOpen) {
        badge.className = "badge badge-open";
        badge.textContent = "Market: OPEN";
      } else {
        badge.className = "badge badge-closed";
        badge.textContent = "Market: CLOSED — showing last close";
      }
    })
    .catch(() => { /* leave as-is */ });
}

function render(rows) {
  const tbody = el("decision-body");
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">No decisions yet — run agent.py first.</td></tr>';
    el("latest-decision").textContent = "";
    el("stat-total").textContent = "0";
    el("stat-last-buy").textContent = "—";
    el("stat-last-sell").textContent = "—";
    return;
  }

  let html = "";
  for (const e of rows) {
    const staleCell = e.stale ? ' class="stale"' : "";
    const decidedTrade = (e.action === "BUY" || e.action === "SELL");
    const executed = decidedTrade && !!e.order_id;

    // BUY/SELL that didn't execute (below-confidence, already-pending, etc.)
    // get an outlined badge + a small explanation instead of solid green/red.
    let actionCell;
    if (decidedTrade && !executed) {
      const shortNote = e.note ? ` (not executed — ${e.note})` : " (not executed)";
      actionCell = `<span class="action action-outlined action-${esc(e.action)}" title="${esc(e.note || 'not executed')}">${esc(e.action)}</span><span class="note-text">${esc(shortNote)}</span>`;
    } else {
      actionCell = `<span class="action action-${esc(e.action)}">${esc(e.action)}</span>`;
    }

    html += "<tr>"
      + `<td>${esc(e.timestamp)}</td>`
      + `<td>${esc(e.symbol)}</td>`
      + `<td>${actionCell}</td>`
      + `<td>${esc(e.confidence)}</td>`
      + `<td>${esc(e.price)}</td>`
      + `<td>${e.market_open ? "OPEN" : "CLOSED"}</td>`
      + `<td${staleCell}>${esc(ageText(e))}</td>`
      + `<td>${esc(e.order_id)}</td>`
      + "</tr>";
  }
  tbody.innerHTML = html;

  el("latest-decision").textContent = `Latest decision: ${rows[0].timestamp}`;
  el("stat-total").textContent = rows.length;
  el("stat-last-buy").textContent = (rows.find((r) => r.action === "BUY") || {}).symbol || "—";
  el("stat-last-sell").textContent = (rows.find((r) => r.action === "SELL") || {}).symbol || "—";
}

async function poll() {
  try {
    // Pull any newly appended lines into the app cache, then fetch the view.
    await fetch("/api/refresh");
    const res = await fetch("/api/decisions");
    if (!res.ok) return;
    const data = await res.json();
    render(data.entries || []);
    await marketBadge(data.entries || []);
  } catch (_) { /* transient; next poll retries */ }
}

poll();
setInterval(poll, POLL_SECONDS * 1000);
