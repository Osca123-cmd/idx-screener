from app import universe


def test_yahoo_symbol_roundtrip():
    assert universe.yahoo_symbol("bbca") == "BBCA.JK"
    assert universe.from_yahoo("BBCA.JK") == "BBCA"


def test_load_real_universe_has_banks_flagged():
    stocks = universe.by_ticker(universe.load())
    assert len(stocks) > 700
    assert stocks["BBCA"].is_financial
    assert not stocks["TLKM"].is_financial
    assert stocks["BBCA"].sector == "Finance"


def test_load_from_custom_csv(tmp_path):
    p = tmp_path / "u.csv"
    p.write_text("ticker,name,sector,industry,is_financial\nAAAA,PT A, Tbk,X,Y,0\n".replace("PT A, Tbk", '"PT A, Tbk"'))
    [s] = universe.load(str(p))
    assert s.name == "PT A, Tbk" and s.is_financial is False
