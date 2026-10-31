# Papan Swing — IDX swing screener

Web pribadi untuk menyaring saham IDX yang berpotensi naik (swing harian), lengkap dengan
setup entry / SL / TP bertahap dan label valuasi Undervalue / Wajar / Overvalue.
Jalan lokal di laptop; order tetap dieksekusi manual di Stockbit (Stockbit tidak punya API publik).

## Menjalankan

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m app.jobs daily_scan          # scan pertama: ±2 menit, ambil 2 tahun data
.venv/Scripts/python -m uvicorn app.web.main:app --port 8765
```

Buka http://localhost:8765.

| Perintah | Fungsi |
|---|---|
| `python -m app.jobs daily_scan [--tickers BBCA,TLKM] [--force]` | ambil harga baru, hitung skor/setup/valuasi |
| `python -m app.jobs weekly_fundamentals` | refresh EPS/BVPS + simpan snapshot valuasi mingguan |
| `python -m app.jobs backtest` | replay mesin skor 2 tahun, hasil di halaman Backtest |
| `python scripts/build_universe.py` | perbarui daftar emiten (IPO baru) |
| `powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1` | jadwalkan scan 16:30 WIB hari kerja + fundamental Sabtu 08:00 |

## Sumber data

- **Harga**: Yahoo Finance (`.JK`), endpoint chart publik — gratis, tidak resmi. Split-adjusted, tidak
  dividend-adjusted (sama dengan chart Stockbit/TradingView default).
- **Fundamental**: endpoint screener publik TradingView (satu request untuk semua emiten) — gratis, tidak resmi.
- **Daftar emiten + sektor**: juga dari screener TradingView (`data/universe.csv`).

Untuk ganti ke API berbayar (mis. Sectors.app), buat kelas baru yang memenuhi `PriceSource` /
`FundamentalSource` di `app/data/base.py`, lalu pakai di `app/jobs.py`.

## Aturan

Semua ambang ada di `config.yaml` (likuiditas, bobot skor, kedalaman entry, buffer SL, batas valuasi, biaya backtest).
Spesifikasi lengkap: `docs/specs/2026-10-31-idx-screener-design.md`.

Ringkasnya:
- **Skor 0–100**: trend 30 · momentum 20 · volume 20 · trigger (breakout / pullback ke demand zone) 20 · ruang naik 10.
- **Plan**: entry 65% ke dalam zona lebar (+ opsi bertahap 30/40/30), SL di bawah zona + buffer ATR
  (lebih lebar untuk zona sempit), TP1 = hambatan nyata terdekat, TP2 = hambatan berikutnya / 2R.
- **Valuasi**: PER & PBV dibanding median sektor (bank: PBV saja); < 0,75× Undervalue, > 1,25× Overvalue.
- Candle hari ini diabaikan kalau scan dijalankan sebelum 16:15 WIB (belum close).

## Test

```bash
.venv/Scripts/python -m pytest            # unit + smoke, tanpa internet
.venv/Scripts/python -m pytest -m network # cek sumber data live
```

Bukan saran investasi.
