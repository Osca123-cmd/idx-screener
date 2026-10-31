# IDX Swing Screener — Spec & Rencana Implementasi

## Context

User ingin web untuk menganalisa saham IDX yang berpotensi naik, idealnya terintegrasi dengan Stockbit.
**Stockbit tidak punya API publik** — akses yang ada hanya reverse-engineering endpoint privat (rawan putus,
berisiko melanggar ToS), jadi tidak dipakai. Stockbit tetap jadi tempat eksekusi order manual; web ini adalah
mesin screening + valuasi pribadi.

Keputusan yang sudah disepakati:
- **Tool pribadi**, tanpa login/billing, jalan **lokal di laptop** (Windows).
- **Swing trading (hari–minggu)** + **label Undervalue / Wajar / Overvalue**.
- **Data gratis dulu**: Yahoo Finance (`.JK`) via `yfinance`, lewat lapisan adapter supaya nanti bisa diganti Sectors.app.
- **Stack**: Python 3.12 (sudah terpasang) + FastAPI + Jinja + HTMX + SQLite; chart pakai Lightweight Charts.
- **Mesin**: skor checklist berbasis aturan 0–100 + setup entry/SL/TP bertahap + valuasi relatif (PER/PBV vs median
  sektor & histori sendiri) + Graham Number pembanding + backtest hit-rate di versi pertama.
- Output mengikuti disiplin trading user (memory `feedback_trading_zone_entry_discipline`): verdict trend eksplisit,
  entry 60–75% dalam zona lebar + scale-in 30/40/30, buffer SL lebih lebar di zona sempit, TP1 = hambatan nyata
  terdekat lalu TP2 lanjutan, jangan menyimpulkan dari candle yang belum close, zona invalid ditandai.

Lokasi proyek: **`C:\Users\muhammad arif\idx-screener`** (folder baru, repo git sendiri; belum ada).

## Struktur proyek

```
idx-screener/
  pyproject.toml            fastapi, uvicorn, jinja2, yfinance, pandas, numpy, pyyaml, pytest
  config.yaml               semua ambang (likuiditas, bobot skor, batas valuasi, jam scan)
  data/universe.csv         ±950 emiten: ticker, nama, sektor, is_financial (seed dari idx.co.id)
  docs/specs/2026-10-31-idx-screener-design.md   salinan spec ini
  app/
    config.py               load config.yaml → dataclass
    data/base.py            Protocol PriceSource (get_ohlcv batch, incremental) & FundamentalSource
    data/yahoo.py           implementasi yfinance: yf.download batch (auto_adjust), .info per ticker + throttle + retry 3x
    universe.py             baca universe.csv, map ke simbol Yahoo (BBCA → BBCA.JK)
    store.py                SQLite schema + akses (lihat Data model)
    indicators.py           EMA, RSI14, MACD, ATR14, avg volume/value 20, OBV
    structure.py            swing high/low (fractal), verdict NAIK/TURUN/SIDEWAYS, demand zone + validitas, resistance list
    ticks.py                fraksi harga IDX + round_to_tick
    setup.py                Breakout / Pullback → entry zone, entry, scale-in, SL, TP1, TP2, R:R, warnings
    scoring.py              filter awal + checklist → skor 0–100 + reasons[]
    valuation.py            median sektor PER/PBV, rasio, label, persentil histori, Graham, value-trap flag
    backtest.py             replay skor/setup 2 tahun → hit-rate TP1-sebelum-SL dalam 20 bar, avg R, n
    jobs.py                 CLI: daily_scan, weekly_fundamentals, backtest; lock file anti-dobel
    web/main.py             FastAPI routes
    web/templates/          base, screener, stock_detail, watchlist, backtest, partials (HTMX)
    web/static/             app.js (render chart Lightweight Charts dari JSON), app.css
  tests/                    unit + fixtures (CSV/JSON rekaman Yahoo) + smoke
  scripts/register_task.ps1 daftar Windows Task Scheduler (16:30 WIB hari kerja; Sabtu 08:00 fundamental)
```

## Data model (SQLite `data/screener.db`)

- `prices(ticker, date, open, high, low, close, volume)` PK(ticker,date)
- `fundamentals(ticker, fetched_at, eps, bvps, per, pbv, roe, sector, market_cap)`
- `valuation_snapshots(ticker, week, per, pbv)` — membangun histori untuk persentil
- `scans(id, scan_date, started_at, finished_at, status, n_ok, n_failed, n_stale, note)`
- `scan_results(scan_id, ticker, score, trend, setup_type, zone_low, zone_high, entry, sl, tp1, tp2, rr, valuation_label, valuation_reason, graham, flags_json, reasons_json)`
- `scan_errors(scan_id, ticker, stage, message)`
- `watchlist(ticker, added_at, plan_json)` — plan disalin saat ditambahkan; status dihitung ulang tiap scan
- `backtest_runs(id, run_at, params_json, results_json)`

## Aturan (default di config.yaml)

**Filter awal** (tidak lolos = tidak diskor): avg nilai transaksi 20h ≥ Rp 5 M; harga ≥ 50; ada bar dalam 3 hari bursa
terakhir; ≥ 200 bar histori.

**Skor 0–100**
| Komponen | Poin |
|---|---|
| Trend 30 | close>EMA200 (10) · EMA20>EMA50>EMA200 (10) · HH/HL swing harian (10) |
| Momentum 20 | RSI14 50–70 (15; >70 → 5 + flag overbought) · MACD hist naik (5) |
| Volume 20 | vol ≥ 2× avg20 di candle hijau (15) · OBV naik 10 hari (5) |
| Trigger 20 | Breakout (close > high 20 bar sebelumnya) atau Pullback ke demand zone valid dalam uptrend |
| Ruang 10 | (TP1−entry)/(entry−SL) ≥ 1.5 (10) · ≥ 1 (5) |

Verdict trend: NAIK jika 3 syarat trend terpenuhi, TURUN jika cerminannya (close<EMA200, EMA20<EMA50<EMA200, LH/LL), selain itu SIDEWAYS.

**Setup**
- Demand zone = candle basis (bearish/konsolidasi) sebelum impuls naik ≥ 2×ATR yang menembus swing high; invalid begitu ada close di bawah zone_low. Tiap retest menurunkan confidence (flag `retest_n`).
- Breakout: zona = [level breakout − 0.5×ATR, level breakout] (area retest).
- Entry: zona > 1.5×ATR → kedalaman 65% (rentang 60–75%) + scale-in 30/40/30; selain itu tengah zona.
- SL: zone_low − 0.5×ATR; zona < 1×ATR → buffer 1×ATR + flag `narrow_zone_stophunt`.
- TP1 = resistance nyata terdekat di atas entry (swing high, cluster konsolidasi, angka bulat); TP2 = resistance berikutnya atau 2R. TP1 < 1R → flag `room_tight`.
- Semua harga di-round ke fraksi IDX: <200→1, 200–<500→2, 500–<2000→5, 2000–<5000→10, ≥5000→25.

**Valuasi**
- Median sektor PER & PBV dari universe (buang PER ≤ 0 dan > 100).
- Rasio = saham ÷ median; non-keuangan: rata-rata rasio PER & PBV; keuangan (`is_financial`): rasio PBV saja.
- < 0.75 Undervalue · 0.75–1.25 Wajar · > 1.25 Overvalue; selalu dengan reason teks ("PBV 0.8 vs median sektor 1.4").
- EPS ≤ 0 → "N/A (rugi)". Fundamental hilang → "Data tidak tersedia" (tidak menebak).
- ≥ 26 snapshot mingguan → tambah "murah/mahal vs histori" (persentil < 20 / > 80).
- Graham = √(22.5 × EPS × BVPS) + margin of safety %. Undervalue + trend TURUN → flag `value_trap`.

## Penanganan error
- Retry 3× dengan backoff per ticker; gagal → `scan_errors`, scan lanjut. Header web menampilkan jumlah gagal/stale.
- Bar terakhir ≠ hari bursa terakhir → ticker `stale`, dikecualikan dari sinyal baru.
- Scan saat jam bursa (sebelum 16:15 WIB) → bar hari ini dibuang (candle belum close).
- Tidak ada bar baru untuk seluruh universe → scan berstatus "tidak ada sesi" (libur).
- Lock file mencegah dua scan berjalan bersamaan (scheduler + tombol manual).

## Halaman web
1. `/` Screener — tabel hasil scan terakhir, filter (skor min, setup, valuasi, sektor, trend) & sort via HTMX, header tanggal + status data, disclaimer "bukan saran investasi".
2. `/stock/{ticker}` — Lightweight Charts: candle + EMA20/50/200 + volume, **demand zone (area), garis entry/SL/TP1/TP2 digambar**; checklist ✓/✗ per kriteria; kartu valuasi; riwayat skor 30 hari; tombol tambah ke watchlist.
3. `/watchlist` — status otomatis: menunggu entry / entry tersentuh / TP1 kena / SL kena / zona invalid.
4. `/backtest` — hit-rate per setup & per rentang skor, avg R, jumlah sampel; tombol jalankan ulang.
5. `POST /scan` — jalankan scan di background thread; `/scan/status` di-poll HTMX untuk progress.

## Rencana implementasi (TDD, urut)

1. **Bootstrap**: buat folder, `git init`, `pyproject.toml`, venv, `config.yaml`, salin spec ke `docs/specs/`, commit.
2. **Universe**: susun `data/universe.csv` (emiten + sektor + is_financial) dari daftar emiten idx.co.id; `universe.py` + test.
3. **ticks.py + indicators.py** — test dulu (nilai referensi tangan / deret sintetis), lalu implementasi.
4. **store.py** — schema + upsert/query; test dengan SQLite temp.
5. **Adapter Yahoo** — `base.py` Protocol + `yahoo.py`; test pakai fixture rekaman (tanpa jaringan); satu test integrasi opsional ber-marker `network`.
6. **structure.py** — swing points, verdict trend, demand zone + invalidasi, resistance; test deret sintetis (uptrend, downtrend, zona tertembus).
7. **setup.py** — entry/SL/TP/flags; test kedalaman zona, buffer sempit, TP1<1R, rounding.
8. **scoring.py** — filter + skor + reasons; test per komponen.
9. **valuation.py** — median sektor, label, bank, rugi, data kosong, value trap, Graham; test.
10. **jobs.py** — `daily_scan`, `weekly_fundamentals`, aturan candle-belum-close, stale, libur, lock; smoke test 10 ticker ke DB temp.
11. **backtest.py** — replay tanpa look-ahead (hanya data ≤ bar t); test kecil deterministik.
12. **Web** — routes + template screener → detail (chart) → watchlist → backtest → tombol scan.
13. **scripts/register_task.ps1** + README singkat (cara jalan, cara ganti sumber data).

## Verifikasi end-to-end
- `pytest` hijau (unit + smoke; test `network` dijalankan manual sekali).
- `python -m app.jobs weekly_fundamentals` lalu `python -m app.jobs daily_scan` pada universe penuh: cek ringkasan (n_ok/n_failed/n_stale) wajar, durasi tercatat.
- `uvicorn app.web.main:app` → buka di browser pane: filter/sort berfungsi, halaman detail menggambar zona & garis, watchlist & backtest tampil, tidak ada error console.
- Cocokkan 3–5 saham (mis. BBCA, BBRI, TLKM) dengan chart TradingView via MCP repo ini: EMA, RSI, high 20 hari, dan level zona masuk akal.
- Jalankan `register_task.ps1` hanya setelah user setuju (mengubah konfigurasi Task Scheduler).

## Catatan risiko
- Yahoo unofficial: bisa rate-limit/berubah → adapter + retry + status data di UI; jalur upgrade ke Sectors.app.
- Fundamental Yahoo untuk `.JK` kadang kosong/terlambat → label "Data tidak tersedia", bukan tebakan.
- Skor adalah alat penyaring, bukan prediksi; UI selalu menampilkan disclaimer bukan saran investasi.
