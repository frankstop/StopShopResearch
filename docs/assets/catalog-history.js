(() => {
  "use strict";

  const base = "data/catalog-history/";
  const state = { manifest: null, items: [], filtered: [], page: 1, pageSize: 50, shardCache: new Map() };
  const $ = (selector) => document.querySelector(selector);
  const elements = {
    loading: $("#loading"), workspace: $("#catalog-workspace"), body: $("#catalog-body"),
    count: $("#result-count"), pageStatus: $("#page-status"), previous: $("#previous-page"), next: $("#next-page"),
    query: $("#query"), category: $("#category"), status: $("#status"), sort: $("#sort"), reset: $("#reset-filters"),
    export: $("#export-csv"), dialog: $("#item-dialog"), detail: $("#item-detail"), close: $("#close-dialog")
  };

  const money = (value) => value == null ? "—" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
  const changeText = (value) => value == null ? "—" : `${value > 0 ? "+" : ""}${money(value)}`;
  const compactId = (value) => value.startsWith("http") ? value.replace(/^https?:\/\//, "") : `UPC ${value}`;
  const normal = (value) => String(value || "").normalize("NFKD").toLowerCase();

  function setText(id, value) { const node = document.getElementById(id); if (node) node.textContent = value; }

  function svgNode(name, attributes = {}) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  }

  function sparkline(values, label) {
    const svg = svgNode("svg", { class: "spark", viewBox: "0 0 112 36", role: "img", "aria-label": label });
    const observed = values.map((value, index) => ({ value, index })).filter((row) => row.value != null);
    if (!observed.length) return svg;
    const prices = observed.map((row) => row.value), low = Math.min(...prices), high = Math.max(...prices), span = Math.max(high - low, 0.5);
    const x = (index) => values.length === 1 ? 56 : 4 + index / (values.length - 1) * 104;
    const y = (value) => 31 - (value - low) / span * 26;
    let segment = [];
    values.forEach((value, index) => {
      if (value == null) {
        if (segment.length > 1) svg.append(svgNode("polyline", { points: segment.join(" ") }));
        segment = [];
      } else segment.push(`${x(index).toFixed(1)},${y(value).toFixed(1)}`);
    });
    if (segment.length > 1) svg.append(svgNode("polyline", { points: segment.join(" ") }));
    observed.forEach((row) => svg.append(svgNode("circle", { cx: x(row.index), cy: y(row.value), r: 2.5 })));
    return svg;
  }

  function readUrl() {
    const params = new URLSearchParams(location.search);
    elements.query.value = params.get("q") || "";
    elements.category.value = params.get("category") || "";
    elements.status.value = params.get("status") || "";
    elements.sort.value = params.get("sort") || "name";
    state.page = Math.max(1, Number(params.get("page")) || 1);
    return params.get("item");
  }

  function writeUrl({ item, push = false } = {}) {
    const params = new URLSearchParams();
    if (elements.query.value.trim()) params.set("q", elements.query.value.trim());
    if (elements.category.value) params.set("category", elements.category.value);
    if (elements.status.value) params.set("status", elements.status.value);
    if (elements.sort.value !== "name") params.set("sort", elements.sort.value);
    if (state.page > 1) params.set("page", state.page);
    if (item) params.set("item", item);
    const url = `${location.pathname}${params.size ? `?${params}` : ""}`;
    history[push ? "pushState" : "replaceState"]({}, "", url);
  }

  function applyFilters({ resetPage = false } = {}) {
    if (resetPage) state.page = 1;
    const query = normal(elements.query.value.trim());
    const category = elements.category.value;
    const status = elements.status.value;
    state.filtered = state.items.filter((item) => {
      const haystack = normal([item.name, item.id, item.brand, ...(item.categories || [])].join(" "));
      return (!query || haystack.includes(query)) && (!category || item.categories.includes(category)) && (!status || item.status === status);
    });
    const sorters = {
      name: (a, b) => a.name.localeCompare(b.name),
      price_asc: (a, b) => (a.current_price ?? Infinity) - (b.current_price ?? Infinity),
      price_desc: (a, b) => (b.current_price ?? -Infinity) - (a.current_price ?? -Infinity),
      change_up: (a, b) => (b.latest_change ?? -Infinity) - (a.latest_change ?? -Infinity),
      change_down: (a, b) => (a.latest_change ?? Infinity) - (b.latest_change ?? Infinity),
      first_seen: (a, b) => b.first_seen.localeCompare(a.first_seen),
      last_seen: (a, b) => b.last_seen.localeCompare(a.last_seen)
    };
    state.filtered.sort(sorters[elements.sort.value] || sorters.name);
    const pages = Math.max(1, Math.ceil(state.filtered.length / state.pageSize));
    state.page = Math.min(state.page, pages);
    renderTable();
    writeUrl();
  }

  function cell(label, content) {
    const td = document.createElement("td"); td.dataset.label = label;
    if (content instanceof Node) td.append(content); else td.textContent = content;
    return td;
  }

  function renderTable() {
    elements.body.replaceChildren();
    const start = (state.page - 1) * state.pageSize;
    const pageItems = state.filtered.slice(start, start + state.pageSize);
    const fragment = document.createDocumentFragment();
    pageItems.forEach((item) => {
      const row = document.createElement("tr");
      const product = document.createElement("div"); product.className = "product";
      const name = document.createElement("strong"); name.textContent = item.name;
      const id = document.createElement("small"); id.textContent = compactId(item.id);
      const meta = document.createElement("small"); meta.textContent = [item.brand, item.categories[0]].filter(Boolean).join(" · ") || "Uncategorized";
      product.append(name, id, meta);
      const price = cell("Current", money(item.current_price)); price.className = "price";
      const change = cell("Change", changeText(item.latest_change)); change.className = item.latest_change > 0 ? "change-up" : item.latest_change < 0 ? "change-down" : "";
      const status = document.createElement("span"); status.className = `status ${item.status}`; status.textContent = item.status;
      const history = sparkline(item.trend, `${item.name} price history from ${item.first_seen} to ${item.last_seen}`);
      const button = document.createElement("button"); button.type = "button"; button.className = "view-button"; button.textContent = "View history";
      button.addEventListener("click", () => openItem(item.id, true));
      row.append(cell("Item", product), price, change, cell("Range", `${money(item.minimum_price)}–${money(item.maximum_price)}`), cell("Status", status), cell("Observed", `${item.observations}/${state.manifest.snapshot_count}`), cell("History", history), cell("Details", button));
      fragment.append(row);
    });
    elements.body.append(fragment);
    const total = state.filtered.length;
    elements.count.textContent = `${total.toLocaleString()} item${total === 1 ? "" : "s"}`;
    elements.pageStatus.textContent = total ? `Showing ${start + 1}–${Math.min(start + state.pageSize, total)} of ${total.toLocaleString()}` : "No matching items";
    elements.previous.disabled = state.page <= 1;
    elements.next.disabled = start + state.pageSize >= total;
    $("#empty-results").hidden = Boolean(pageItems.length);
  }

  async function openItem(itemId, push = false) {
    const summary = state.items.find((item) => item.id === itemId);
    if (!summary) return;
    elements.detail.innerHTML = '<div class="loading">Loading complete item history…</div>';
    elements.dialog.showModal();
    writeUrl({ item: itemId, push });
    try {
      if (!state.shardCache.has(summary.shard)) {
        const response = await fetch(`${base}items/${summary.shard}.json`);
        if (!response.ok) throw new Error("History shard unavailable");
        state.shardCache.set(summary.shard, await response.json());
      }
      const shard = state.shardCache.get(summary.shard);
      const observations = shard.items[itemId].map((row) => Object.fromEntries(shard.observation_fields.map((field, index) => [field, row[index]])));
      renderDetail(summary, { observations });
    } catch (error) {
      elements.detail.innerHTML = `<div class="empty">${error.message}. The catalog index remains available.</div>`;
    }
  }

  function detailMetric(label, value) {
    const node = document.createElement("div"); node.className = "detail-metric";
    const small = document.createElement("span"); small.textContent = label;
    const strong = document.createElement("strong"); strong.textContent = value;
    node.append(small, strong); return node;
  }

  function detailChart(observations, itemName) {
    const wrapper = document.createElement("div"); wrapper.className = "detail-chart";
    const width = 820, height = 280, left = 54, right = 18, top = 22, bottom = 46;
    const observedByDate = new Map(observations.map((row) => [row.date, row]));
    const timeline = state.manifest.snapshot_dates.map((date) => observedByDate.get(date) || { date, price: null, regular_price: null });
    const values = observations.flatMap((row) => [row.price, row.regular_price]).filter((value) => value != null);
    const low = Math.min(...values), high = Math.max(...values), padding = Math.max((high - low) * .12, .5), min = Math.max(0, low - padding), max = high + padding;
    const x = (index) => timeline.length === 1 ? width / 2 : left + index / (timeline.length - 1) * (width - left - right);
    const y = (value) => top + (max - value) / Math.max(max - min, 1) * (height - top - bottom);
    const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": `${itemName} price history` });
    svg.append(svgNode("line", { class: "axis", x1: left, y1: height - bottom, x2: width - right, y2: height - bottom }));
    for (const [field, className] of [["price", "series"], ["regular_price", "regular"]]) {
      let segment = [];
      timeline.forEach((row, index) => {
        if (row[field] == null) {
          if (segment.length > 1) svg.append(svgNode("polyline", { class: className, points: segment.join(" ") }));
          segment = [];
        } else segment.push(`${x(index).toFixed(1)},${y(row[field]).toFixed(1)}`);
      });
      if (segment.length > 1) svg.append(svgNode("polyline", { class: className, points: segment.join(" ") }));
    }
    timeline.forEach((row, index) => {
      if (row.price == null) return;
      const circle = svgNode("circle", { cx: x(index), cy: y(row.price), r: 4 });
      const title = svgNode("title"); title.textContent = `${row.date}: ${money(row.price)}`; circle.append(title); svg.append(circle);
    });
    timeline.forEach((row, index) => {
      if (timeline.length > 12 && index % Math.ceil(timeline.length / 8) !== 0 && index !== timeline.length - 1) return;
      const label = svgNode("text", { x: x(index), y: height - 17, "text-anchor": "middle" }); label.textContent = row.date.slice(5); svg.append(label);
    });
    const highLabel = svgNode("text", { x: 4, y: top + 4 }); highLabel.textContent = money(high); svg.append(highLabel);
    const lowLabel = svgNode("text", { x: 4, y: height - bottom + 4 }); lowLabel.textContent = money(low); svg.append(lowLabel);
    wrapper.append(svg); return wrapper;
  }

  function renderDetail(summary, detail) {
    elements.detail.replaceChildren();
    const head = document.createElement("div"); head.className = "dialog-head";
    const titleBox = document.createElement("div"); const title = document.createElement("h2"); title.id = "detail-title"; title.textContent = summary.name;
    const identity = document.createElement("p"); identity.textContent = compactId(summary.id); titleBox.append(title, identity); head.append(titleBox, elements.close);
    const body = document.createElement("div"); body.className = "dialog-body";
    const actions = document.createElement("div"); actions.className = "detail-actions";
    const copy = document.createElement("button"); copy.className = "button"; copy.textContent = "Copy item link";
    copy.addEventListener("click", async () => { await navigator.clipboard.writeText(location.href); copy.textContent = "Link copied"; });
    actions.append(copy);
    if (summary.id.startsWith("http")) { const source = document.createElement("a"); source.className = "button"; source.href = summary.id; source.target = "_blank"; source.rel = "noreferrer"; source.textContent = "Open source item"; actions.append(source); }
    const metrics = document.createElement("div"); metrics.className = "detail-metrics";
    metrics.append(detailMetric("Current", money(summary.current_price)), detailMetric("Historical low", money(summary.minimum_price)), detailMetric("Historical high", money(summary.maximum_price)), detailMetric("Observed", `${summary.observations}/${state.manifest.snapshot_count} snapshots`));
    body.append(actions, metrics, detailChart(detail.observations, summary.name));
    const tableWrap = document.createElement("div"); tableWrap.className = "detail-table";
    const table = document.createElement("table"); table.innerHTML = "<thead><tr><th>Date</th><th>Observed price</th><th>Regular price</th><th>Availability</th><th>Categories</th></tr></thead>";
    const tbody = document.createElement("tbody");
    detail.observations.slice().reverse().forEach((observation) => { const row = document.createElement("tr"); row.append(cell("Date", observation.date), cell("Observed price", money(observation.price)), cell("Regular price", money(observation.regular_price)), cell("Availability", observation.availability || "Observed"), cell("Categories", observation.categories.join(", ") || "—")); tbody.append(row); });
    table.append(tbody); tableWrap.append(table); body.append(tableWrap); elements.detail.append(head, body);
  }

  function exportCsv() {
    const quote = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
    const rows = [["item_id","name","brand","categories","status","current_price","latest_change","minimum_price","maximum_price","first_seen","last_seen","observations"], ...state.filtered.map((item) => [item.id,item.name,item.brand,item.categories.join(" | "),item.status,item.current_price,item.latest_change,item.minimum_price,item.maximum_price,item.first_seen,item.last_seen,item.observations])];
    const blob = new Blob([rows.map((row) => row.map(quote).join(",")).join("\n")], { type: "text/csv;charset=utf-8" });
    const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = `${state.manifest.retailer.toLowerCase().replaceAll(/[^a-z0-9]+/g,"-")}-catalog-history.csv`; link.click(); URL.revokeObjectURL(link.href);
  }

  async function init() {
    try {
      const [manifestResponse, indexResponse] = await Promise.all([fetch(`${base}manifest.json`), fetch(`${base}catalog-index.json`)]);
      if (!manifestResponse.ok || !indexResponse.ok) throw new Error("Catalog history data is unavailable");
      state.manifest = await manifestResponse.json();
      const indexPayload = await indexResponse.json();
      state.items = indexPayload.items.map((row) => Object.fromEntries(indexPayload.item_fields.map((field, index) => [field, row[index]])));
      setText("metric-all", state.manifest.unique_items.toLocaleString()); setText("metric-current", state.manifest.current_items.toLocaleString()); setText("metric-missing", state.manifest.missing_items.toLocaleString()); setText("metric-snapshots", state.manifest.snapshot_count.toLocaleString());
      setText("freshness", `${state.manifest.date_start} through ${state.manifest.date_end}`);
      state.manifest.categories.forEach((category) => { const option = document.createElement("option"); option.value = category.name; option.textContent = `${category.name} (${category.items.toLocaleString()})`; elements.category.append(option); });
      elements.loading.hidden = true; elements.workspace.hidden = false;
      const deepLink = readUrl(); applyFilters(); if (deepLink) openItem(deepLink);
    } catch (error) { elements.loading.textContent = `${error.message}. Try the weekly analysis or source repository instead.`; }
  }

  [elements.query, elements.category, elements.status, elements.sort].forEach((element) => element.addEventListener(element === elements.query ? "input" : "change", () => applyFilters({ resetPage: true })));
  elements.reset.addEventListener("click", () => { elements.query.value = ""; elements.category.value = ""; elements.status.value = ""; elements.sort.value = "name"; applyFilters({ resetPage: true }); });
  elements.previous.addEventListener("click", () => { state.page -= 1; renderTable(); writeUrl(); scrollTo({ top: elements.workspace.offsetTop - 10, behavior: "smooth" }); });
  elements.next.addEventListener("click", () => { state.page += 1; renderTable(); writeUrl(); scrollTo({ top: elements.workspace.offsetTop - 10, behavior: "smooth" }); });
  elements.export.addEventListener("click", exportCsv);
  elements.close.addEventListener("click", () => elements.dialog.close());
  elements.dialog.addEventListener("close", () => writeUrl());
  addEventListener("popstate", () => { const item = readUrl(); applyFilters(); if (item) openItem(item); else if (elements.dialog.open) elements.dialog.close(); });
  init();
})();
