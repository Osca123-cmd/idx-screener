import pytest

from app import config
from app import valuation as val
from app.universe import Stock


@pytest.fixture(scope="module")
def cfg():
    return config.load()


def peers(sector="Energy", n=6, fin=False):
    # PER 10..15, PBV 1.0..1.5 at close 1000 -> medians 12.5 / 1.25
    stocks, funds, closes = [], {}, {}
    for i in range(n):
        t = f"P{i:03d}"
        stocks.append(Stock(t, t, sector, "x", fin))
        funds[t] = {"eps": 1000 / (10 + i), "bvps": 1000 / (1 + i / 10)}
        closes[t] = 1000.0
    return stocks, funds, closes


def test_sector_medians(cfg):
    stocks, funds, closes = peers()
    m = val.sector_medians(stocks, funds, closes, cfg.valuation.per_max)
    assert m["sectors"]["Energy"]["per"] == pytest.approx(12.5)
    assert m["sectors"]["Energy"]["pbv"] == pytest.approx(1.25)
    assert m["sectors"]["Energy"]["n"] == 6


def test_labels_under_fair_over(cfg):
    stocks, funds, closes = peers()
    m = val.sector_medians(stocks, funds, closes, cfg.valuation.per_max)
    s = Stock("AAAA", "A", "Energy", "x", False)
    cheap = val.label(s, {"eps": 1000 / 6, "bvps": 1000 / 0.6}, 1000, m, cfg)
    assert cheap["label"] == val.UNDER and "PER 6.0 vs 12.5" in cheap["reason"]
    fair = val.label(s, {"eps": 80, "bvps": 800}, 1000, m, cfg)
    assert fair["label"] == val.FAIR
    rich = val.label(s, {"eps": 40, "bvps": 400}, 1000, m, cfg)
    assert rich["label"] == val.OVER


def test_bank_uses_pbv_only(cfg):
    stocks, funds, closes = peers(sector="Finance", fin=True)
    m = val.sector_medians(stocks, funds, closes, cfg.valuation.per_max)
    bank = Stock("BANK", "B", "Finance", "Major Banks", True)
    # PER would look expensive (40) but PBV is cheap (0.8 vs 1.25)
    r = val.label(bank, {"eps": 25, "bvps": 1250}, 1000, m, cfg)
    assert r["label"] == val.UNDER and "PER" not in r["reason"] and "bank" in r["reason"]


def test_loss_and_missing(cfg):
    stocks, funds, closes = peers()
    m = val.sector_medians(stocks, funds, closes, cfg.valuation.per_max)
    s = Stock("GOTO", "G", "Energy", "x", False)
    assert val.label(s, {"eps": -0.02, "bvps": 30}, 24, m, cfg)["label"] == val.LOSS
    assert val.label(s, None, 24, m, cfg)["label"] == val.NO_DATA
    assert val.label(s, {"eps": None, "bvps": None}, 24, m, cfg)["label"] == val.NO_DATA


def test_small_sector_falls_back_to_market(cfg):
    stocks, funds, closes = peers()
    lone = Stock("LONE", "L", "Tiny", "x", False)
    stocks.append(lone)
    funds["LONE"] = {"eps": 100, "bvps": 1000}
    closes["LONE"] = 1000.0
    m = val.sector_medians(stocks, funds, closes, cfg.valuation.per_max)
    r = val.label(lone, funds["LONE"], 1000, m, cfg)
    assert "median pasar" in r["reason"]


def test_graham(cfg):
    g, mos = val.graham({"eps": 100, "bvps": 1000}, 1000)
    assert g == pytest.approx(1500.0)
    assert mos == pytest.approx(33.33, rel=1e-3)
    assert val.graham({"eps": -1, "bvps": 1000}, 1000) == (None, None)


def test_history_note_needs_enough_snapshots(cfg):
    stocks, funds, closes = peers()
    m = val.sector_medians(stocks, funds, closes, cfg.valuation.per_max)
    s = Stock("AAAA", "A", "Energy", "x", False)
    f = {"eps": 100, "bvps": 1000}  # PER 10 now
    few = [{"per": 20, "pbv": 2}] * 5
    assert val.label(s, f, 1000, m, cfg, few)["history_note"] is None
    many = [{"per": 15 + i % 5, "pbv": 2} for i in range(30)]
    assert "murah vs histori" in val.label(s, f, 1000, m, cfg, many)["history_note"]
