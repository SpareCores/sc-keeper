from pytest import approx

from sc_keeper.currency import currency_converter as cc


def test_convert():
    assert isinstance(cc.convert(42, "USD", "USD"), float)
    assert cc.convert(42, "USD", "USD") == approx(42)
    assert cc.convert(42, "USD", "HUF") > 42
