(() => {
  "use strict";

  const fmtUSD = (n) =>
    n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD" });
  const fmtPct = (n) => (n == null ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`);
  const fmtQty = (n) => (n == null ? "—" : n.toLocaleString("en-US", { maximumFractionDigits: 4 }));
  const fmtDate = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleString("en-US", {
      month: "short", day: "numeric", year: "numeric",
      hour: "numeric", minute: "2-digit",
    });
  };

  // ---- tabs ----
  const tabs = document.querySelectorAll(".tab");
  const panels = {
    overview: document.getElementById("panel-overview"),
    history: document.getElementById("panel-history"),
  };
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => { t.classList.remove("active"); t.setAttribute("aria-selected", "false"); });
      tab.classList.add("active");
      tab.setAttribute("aria-selected", "true");
      Object.values(panels).forEach((p) => p.classList.remove("active"));
      panels[tab.dataset.tab].classList.add("active");
    });
  });

  // ---- overview ----
  function renderEquityChart(history) {
    const svg = document.getElementById("equity-chart");
    if (!history.length) return;
    const values = history.map((h) => h.equity);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    const w = 600, h = 160, pad = 4;

    const points = history.map((pt, i) => {
      const x = (i / (history.length - 1 || 1)) * (w - pad * 2) + pad;
      const y = h - pad - ((pt.equity - min) / range) * (h - pad * 2);
      return [x, y];
    });

    const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(2)},${p[1].toFixed(2)}`).join(" ");
    const areaPath = `${linePath} L${points[points.length - 1][0].toFixed(2)},${h} L${points[0][0].toFixed(2)},${h} Z`;

    const rising = values[values.length - 1] >= values[0];
    const strokeColor = rising ? "var(--green)" : "var(--red)";

    svg.innerHTML = `
      <defs>
        <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${strokeColor}" stop-opacity="0.25" />
          <stop offset="100%" stop-color="${strokeColor}" stop-opacity="0" />
        </linearGradient>
      </defs>
      <path d="${areaPath}" fill="url(#equityFill)" stroke="none"></path>
      <path d="${linePath}" fill="none" stroke="${strokeColor}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"></path>
    `;
  }

  function renderPositions(positions) {
    const tbody = document.querySelector("#positions-table tbody");
    if (!positions.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty-state">No holdings.</td></tr>`;
      return;
    }
    tbody.innerHTML = positions.map((p) => {
      const gain = (p.current_price - p.avg_cost) * p.quantity;
      const gainPct = p.avg_cost ? ((p.current_price - p.avg_cost) / p.avg_cost) * 100 : 0;
      const cls = gain >= 0 ? "pos" : "neg";
      return `
        <tr>
          <td><strong>${p.symbol}</strong></td>
          <td>${p.name}</td>
          <td class="num">${fmtQty(p.quantity)}</td>
          <td class="num">${fmtUSD(p.avg_cost)}</td>
          <td class="num">${fmtUSD(p.current_price)}</td>
          <td class="num">${fmtUSD(p.market_value)}</td>
          <td class="num ${cls}">${fmtUSD(gain)} (${fmtPct(gainPct)})</td>
        </tr>`;
    }).join("");
  }

  async function loadOverview() {
    const res = await fetch("/api/account");
    if (!res.ok) return;
    const data = await res.json();
    const a = data.account;

    document.getElementById("equity-value").textContent = fmtUSD(a.equity);
    const changeEl = document.getElementById("equity-change");
    changeEl.textContent = `${fmtUSD(a.day_change)} (${fmtPct(a.day_change_pct)}) today`;
    changeEl.className = `card-sub ${a.day_change >= 0 ? "positive" : "negative"}`;

    document.getElementById("buying-power-value").textContent = fmtUSD(a.buying_power);
    document.getElementById("total-return-value").textContent = fmtUSD(a.total_return);
    const retSub = document.getElementById("total-return-sub");
    retSub.textContent = `${fmtPct(a.total_return_pct)} all time`;
    retSub.className = `card-sub ${a.total_return >= 0 ? "positive" : "negative"}`;

    document.getElementById("account-number-value").textContent = a.account_number;
    document.getElementById("account-type-value").textContent = a.account_type;

    renderEquityChart(data.equity_history);
    renderPositions(data.positions);
  }

  // ---- decision history ----
  const state = { offset: 0, limit: 40, total: 0 };

  function actionPill(action) {
    return `<span class="action-pill action-${action}">${action}</span>`;
  }

  function confidenceBar(conf) {
    const pct = Math.round(conf * 100);
    return `
      <span class="confidence-bar">
        <span class="confidence-track"><span class="confidence-fill" style="width:${pct}%"></span></span>
        <span>${pct}%</span>
      </span>`;
  }

  function renderDecisionRows(decisions, append) {
    const tbody = document.querySelector("#decisions-table tbody");
    if (!append) tbody.innerHTML = "";
    if (!decisions.length && !append) {
      tbody.innerHTML = `<tr><td colspan="8" class="empty-state">No agent decisions match these filters.</td></tr>`;
      return;
    }
    const rowsHtml = decisions.map((d) => `
      <tr>
        <td>${fmtDate(d.run_at)}</td>
        <td><strong>${d.symbol}</strong></td>
        <td>${actionPill(d.action)}</td>
        <td class="num">${d.quantity != null ? fmtQty(Math.abs(d.quantity)) : "—"}</td>
        <td class="num">${d.price != null ? fmtUSD(d.price) : "—"}</td>
        <td class="num">${confidenceBar(d.confidence)}</td>
        <td class="order-status">${d.order_status.replace(/_/g, " ")}</td>
        <td class="rationale-cell">${d.rationale}</td>
      </tr>`).join("");
    tbody.insertAdjacentHTML("beforeend", rowsHtml);
  }

  function currentFilters() {
    const symbol = document.getElementById("filter-symbol").value;
    const action = document.getElementById("filter-action").value;
    const start = document.getElementById("filter-start").value;
    const end = document.getElementById("filter-end").value;
    const params = new URLSearchParams();
    if (symbol) params.set("symbol", symbol);
    if (action) params.set("action", action);
    if (start) params.set("start", `${start}T00:00:00Z`);
    if (end) params.set("end", `${end}T23:59:59Z`);
    return params;
  }

  async function loadDecisions({ append = false } = {}) {
    if (!append) state.offset = 0;
    const params = currentFilters();
    params.set("limit", state.limit);
    params.set("offset", state.offset);

    const res = await fetch(`/api/decisions?${params.toString()}`);
    if (!res.ok) return;
    const data = await res.json();
    state.total = data.total;
    state.offset = data.offset + data.decisions.length;

    renderDecisionRows(data.decisions, append);

    const countEl = document.getElementById("decision-count");
    const shown = append ? Math.min(state.offset, state.total) : data.decisions.length;
    countEl.textContent = `Showing ${shown.toLocaleString()} of ${state.total.toLocaleString()} decisions`;

    const loadMoreWrap = document.getElementById("load-more-wrap");
    loadMoreWrap.classList.toggle("hidden", state.offset >= state.total);
  }

  async function loadMeta() {
    const res = await fetch("/api/meta");
    if (!res.ok) return;
    const data = await res.json();
    const symbolSel = document.getElementById("filter-symbol");
    data.symbols.forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s; opt.textContent = s;
      symbolSel.appendChild(opt);
    });
    const actionSel = document.getElementById("filter-action");
    data.actions.forEach((a) => {
      const opt = document.createElement("option");
      opt.value = a; opt.textContent = a[0].toUpperCase() + a.slice(1);
      actionSel.appendChild(opt);
    });
  }

  ["filter-symbol", "filter-action", "filter-start", "filter-end"].forEach((id) => {
    document.getElementById(id).addEventListener("change", () => loadDecisions());
  });
  document.getElementById("filter-clear").addEventListener("click", () => {
    document.getElementById("filter-symbol").value = "";
    document.getElementById("filter-action").value = "";
    document.getElementById("filter-start").value = "";
    document.getElementById("filter-end").value = "";
    loadDecisions();
  });
  document.getElementById("load-more").addEventListener("click", () => loadDecisions({ append: true }));

  // ---- data source badge ----
  // The robinhood-trading MCP connector isn't authorized in this environment,
  // so the dashboard runs on seeded sample data (robinhood_dashboard/seed.py).
  // Swap this badge to "Live" once real data is wired up via robinhood_source.py.
  function setDataSourceBadge() {
    const badge = document.getElementById("data-source-badge");
    badge.textContent = "Sample data";
    badge.className = "badge badge-sample";
    badge.title = "robinhood-trading MCP connector not yet authorized — showing seeded sample data.";
  }

  setDataSourceBadge();
  loadOverview();
  loadMeta().then(loadDecisions);
})();
