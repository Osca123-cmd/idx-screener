// Daily chart with EMAs, volume, demand zones and the plan's entry / SL / TP levels drawn on it.
(function () {
  const el = document.getElementById("chart");
  if (!el || !window.LightweightCharts) return;
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  const plan = JSON.parse(el.dataset.plan || "{}");
  const zones = JSON.parse(el.dataset.zones || "[]");
  const fmt = (p) => Math.round(p).toLocaleString("id-ID");

  const chart = LightweightCharts.createChart(el, {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: v("--muted"), fontFamily: v("--mono") },
    grid: { vertLines: { color: v("--rule") }, horzLines: { color: v("--rule") } },
    rightPriceScale: { borderColor: v("--rule") },
    timeScale: { borderColor: v("--rule"), rightOffset: 6 },
    localization: { priceFormatter: fmt },
  });

  const candles = chart.addCandlestickSeries({
    upColor: v("--jade"), downColor: v("--crimson"), borderVisible: false,
    wickUpColor: v("--jade"), wickDownColor: v("--crimson"),
  });
  const vol = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  const ema = (color, width) => chart.addLineSeries({ color, lineWidth: width, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  const e20 = ema("#2f6fb3", 1), e50 = ema("#8a5cc2", 1), e200 = ema(v("--muted"), 2);

  fetch(`/api/chart/${el.dataset.ticker}`).then((r) => r.json()).then((d) => {
    candles.setData(d.candles);
    vol.setData(d.volume.map((b) => ({ time: b.time, value: b.value, color: b.up ? v("--jade-soft") : v("--crimson-soft") })));
    e20.setData(d.ema20); e50.setData(d.ema50); e200.setData(d.ema200);

    // Demand zones: shaded band from their base candle to the last bar.
    const last = d.candles[d.candles.length - 1].time;
    zones.forEach((z) => {
      const from = d.candles.find((c) => c.time >= (z.base_date || z.formed_date));
      if (!from) return;
      // The zone the plan trades from gets a stronger fill and a visible top edge.
      const isPlan = z.low <= plan.entry && plan.entry <= z.high;
      const fill = isPlan ? v("--jade-zone") : v("--jade-soft");
      const band = chart.addBaselineSeries({
        baseValue: { type: "price", price: z.low },
        topFillColor1: fill, topFillColor2: fill, topLineColor: isPlan ? v("--jade") : "transparent", lineWidth: 1,
        bottomFillColor1: "transparent", bottomFillColor2: "transparent", bottomLineColor: "transparent",
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      });
      band.setData([{ time: from.time, value: z.high }, { time: last, value: z.high }]);
    });

    const line = (price, color, title, style) => price && candles.createPriceLine({
      price, color, title, lineWidth: 2, lineStyle: style ?? LightweightCharts.LineStyle.Solid, axisLabelVisible: true,
    });
    line(plan.tp2, v("--amber"), "TP2", LightweightCharts.LineStyle.Dashed);
    line(plan.tp1, v("--amber"), "TP1");
    line(plan.entry, v("--jade"), "Entry");
    line(plan.sl, v("--crimson"), "SL");
    chart.timeScale().setVisibleLogicalRange({ from: d.candles.length - 130, to: d.candles.length + 5 });
  });
})();
