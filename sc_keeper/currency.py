from time import time

from currency_converter import CurrencyConverter as CC, SINGLE_DAY_ECB_URL


class CurrencyConverter:
    """Currency converter with hourly auto-updates. Data source: ECB."""

    last_updated: float = 0
    converter: CC

    def __init__(self):
        self.update()

    def update(self):
        self.converter = CC(SINGLE_DAY_ECB_URL)

    def convert(
        self, amount: float, from_currency: str, to_currency: str = "USD"
    ) -> float:
        """Convert amount from a currency to another one.

        Args:
            amount: amount in `from_currency` to be converted to `to_currency`
            from_currency: 3-letter currency code
            to_currency: 3-letter currency code (defaults to "USD")

        Examples:
            >>> c = CurrencyConverter()
            >>> c.convert(42, "EUR", "HUF")  # doctest: +SKIP
            16371.6
        """
        if self.last_updated < time() - 60**2:
            self.update()
        return self.converter.convert(amount, from_currency, to_currency)
