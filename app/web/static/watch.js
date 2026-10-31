// Static-site watchlist: plans live in this browser's localStorage; status is replayed from chart JSON.
(function () {
  const KEY = "papan-swing-watchlist";

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { return {}; }
  }
  function save(list) {
    try { localStorage.setItem(KEY, JSON.stringify(list)); return true; } catch (e) { return false; }
  }

  // Same rules as app/watch.py: same-bar SL+TP counts as SL, stop moves to entry after TP1.
  function status(plan, bars) {
    let entered = null, tp1 = null;
    for (const b of bars) {
      if (!entered) {
        if (b.low > plan.entry) continue;
        entered = b.time;
      }
      if (!tp1) {
        if (b.low <= plan.sl) return { status: "SL kena", date: b.time };
        if (b.high >= plan.tp1) {
          tp1 = b.time;
          if (b.high >= plan.tp2) return { status: "TP2 kena", date: b.time };
        } else if (plan.zone_low && b.close < plan.zone_low) {
          return { status: "Zona invalid", date: b.time };
        }
        continue;
      }
      if (b.high >= plan.tp2) return { status: "TP2 kena", date: b.time };
      if (b.low <= plan.entry) break;
    }
    if (tp1) return { status: "TP1 kena", date: tp1 };
    if (entered) return { status: "Entry tersentuh", date: entered };
    return { status: "Menunggu entry", date: null };
  }

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];
  const dateId = (s) => { if (!s) return "–"; const [y, m, d] = s.split("-"); return `${+d} ${MONTHS[+m - 1]} ${y}`; };
  const price = (v) => (v == null ? "–" : Math.round(v).toLocaleString("id-ID"));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pillClass = (s) => (s.startsWith("TP") ? "win" : s === "SL kena" || s === "Zona invalid" ? "loss" : s === "Entry tersentuh" ? "live" : "");

  // --- stock page: save / remove button ---------------------------------------
  const btn = document.getElementById("watch-btn");
  if (btn) {
    const t = btn.dataset.ticker;
    const paint = () => {
      const on = !!load()[t];
      btn.textContent = on ? "Hapus dari watchlist" : "Simpan plan ke watchlist";
      btn.classList.toggle("ghost", on);
    };
    btn.addEventListener("click", () => {
      const list = load();
      if (list[t]) delete list[t];
      else list[t] = { ...JSON.parse(btn.dataset.plan), added_at: new Date().toISOString() };
      if (!save(list)) btn.textContent = "Browser ini memblokir penyimpanan lokal";
      else paint();
    });
    paint();
  }

  // --- watchlist page ----------------------------------------------------------
  const tbody = document.getElementById("wl-rows");
  if (!tbody) return;
  const latest = JSON.parse(document.getElementById("latest").textContent);
  const links = JSON.parse(document.getElementById("links").textContent);
  const list = load();
  const tickers = Object.keys(list).sort();
  document.getElementById("wl-empty").hidden = tickers.length > 0;
  document.getElementById("wl-board").hidden = tickers.length === 0;

  tickers.forEach(async (t) => {
    const plan = list[t];
    const tr = document.createElement("tr");
    tbody.appendChild(tr);
    let st = { status: "Memuat…", date: null }, close = null;
    try {
      const data = await fetch(links.chart.replace("__T__", t)).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });
      const bars = data.candles.filter((b) => b.time > plan.as_of);
      st = status(plan, bars);
      close = data.candles.length ? data.candles[data.candles.length - 1].close : null;
    } catch (e) {
      st = { status: "Data tidak tersedia", date: null };
    }
    const now = latest[t];
    const name = now ? `<a class="tick" href="${links.stock.replace("__T__", t)}">${esc(t)}<small>${esc(plan.name)}</small></a>`
                     : `<span class="tick">${esc(t)}<small>${esc(plan.name)}</small></span>`;
    tr.innerHTML = `
      <td>${name}</td>
      <td><span class="status-pill ${pillClass(st.status)}">${esc(st.status)}</span>${st.date ? `<br><small class="muted">${dateId(st.date)}</small>` : ""}</td>
      <td class="n">${price(close)}</td>
      <td>${dateId(plan.as_of)}</td>
      <td>${esc(plan.setup_type)}</td>
      <td class="n">${price(plan.entry)}</td>
      <td class="n down">${price(plan.sl)}</td>
      <td class="n">${price(plan.tp1)}</td>
      <td class="n muted">${price(plan.tp2)}</td>
      <td>${now ? `<b class="mono">${now.score}</b> <span class="trend ${esc(now.trend)}">${esc(now.trend)}</span>` : '<span class="muted">tidak di scan</span>'}</td>
      <td><button class="btn ghost" data-remove="${esc(t)}">Hapus</button></td>`;
  });

  tbody.addEventListener("click", (e) => {
    const t = e.target.dataset && e.target.dataset.remove;
    if (!t) return;
    const l = load(); delete l[t]; save(l);
    e.target.closest("tr").remove();
    if (!tbody.rows.length) { document.getElementById("wl-empty").hidden = false; document.getElementById("wl-board").hidden = true; }
  });
})();
