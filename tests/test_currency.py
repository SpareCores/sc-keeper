import pytest
from sc_keeper.currency import CurrencyConverter


def test_covert():
    cc = CurrencyConverter()
    assert type(cc.convert(42, "USD", "USD")) is float
    assert cc.convert(42, "USD", "USD") == 42
    assert cc.convert(42, "USD", "HUF") > 42
