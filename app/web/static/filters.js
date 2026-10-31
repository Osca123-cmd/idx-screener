// Static-site screener: filter and sort the pre-rendered board in the browser.
(function () {
  const form = document.getElementById("filters");
  const tbody = document.querySelector("#board tbody");
  if (!form || !tbody) return;
  const rows = [...tbody.rows];
  const count = document.getElementById("shown-count");
  const board = document.getElementById("board");
  const empty = document.getElementById("no-match");

  const sorters = {
    score: (a, b) => b.dataset.score - a.dataset.score || a.dataset.ticker.localeCompare(b.dataset.ticker),
    rr: (a, b) => b.dataset.rr - a.dataset.rr || a.dataset.ticker.localeCompare(b.dataset.ticker),
    change: (a, b) => b.dataset.change - a.dataset.change || a.dataset.ticker.localeCompare(b.dataset.ticker),
    ticker: (a, b) => a.dataset.ticker.localeCompare(b.dataset.ticker),
  };

  function apply() {
    const f = Object.fromEntries(new FormData(form));
    const q = (f.q || "").trim().toUpperCase();
    let shown = 0;
    rows.forEach((r) => {
      const d = r.dataset;
      const setupOk = !f.setup || (f.setup === "triggered" ? d.setup === "Breakout" || d.setup === "Pullback" : d.setup === f.setup);
      const ok = (!f.min_score || +d.score >= +f.min_score) && setupOk &&
        (!f.trend || d.trend === f.trend) && (!f.valuation || d.valuation === f.valuation) &&
        (!f.sector || d.sector === f.sector) && (!q || d.ticker.includes(q) || d.name.includes(q));
      r.hidden = !ok;
      if (ok) shown++;
    });
    rows.slice().sort(sorters[f.sort] || sorters.score).forEach((r) => tbody.appendChild(r));
    count.textContent = shown;
    board.hidden = shown === 0;
    if (empty) empty.hidden = shown !== 0;
    const params = new URLSearchParams(Object.entries(f).filter(([, v]) => v));
    history.replaceState(null, "", params.toString() ? `?${params}` : location.pathname);
  }

  // restore filters from the URL so a filtered view can be bookmarked
  new URLSearchParams(location.search).forEach((v, k) => { if (form.elements[k]) form.elements[k].value = v; });
  form.addEventListener("change", apply);
  form.addEventListener("input", (e) => { if (e.target.type === "search") apply(); });
  apply();
})();
