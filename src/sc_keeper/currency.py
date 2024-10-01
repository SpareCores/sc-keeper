import logging
from collections import deque
from shutil import copyfileobj
from tempfile import NamedTemporaryFile
from threading import Event, Lock, Thread
from time import sleep

import safe_exit
from currency_converter import SINGLE_DAY_ECB_URL
from currency_converter import CurrencyConverter as CC
from requests import get
from sc_data.data import close_tmpfiles


# mostly follows the logic of sparecores-data.Data
class CurrencyConverter(Thread):
    """Currency converter with hourly auto-updates. Data source: ECB."""

    converter: CC
    daemon = True

    def __init__(self, *args, **kwargs):
        self.tmpfiles = deque()
        self.updated = Event()
        self.lock = Lock()
        self.file_path = None
        self.file_last_updated = None
        super().__init__(*args, **kwargs)

    def update(self):
        with get(SINGLE_DAY_ECB_URL, stream=True) as r:
            if (
                200 <= r.status_code < 300
                and (file_last_updated := r.headers.get("Last-Modified"))
                != self.file_last_updated
            ):
                # delete=False due to Windows support
                # https://stackoverflow.com/questions/15588314/cant-access-temporary-files-created-with-tempfile/15590253#15590253
                tmpfile = NamedTemporaryFile(delete=False, suffix=".zip")
                copyfileobj(r.raw, tmpfile)
                tmpfile.flush()
                with self.lock:
                    self.file_path = tmpfile.name
                    self.file_last_updated = file_last_updated
                    self.converter = CC(self.file_path)
                close_tmpfiles(self.tmpfiles)
                self.tmpfiles.append(tmpfile)
                logging.debug("Updated ECB file at %s", self.file_path)
            else:
                logging.debug("No need to update ECB file")

    def run(self):
        """Start the update thread."""
        while True:
            try:
                self.update()
            except Exception:
                logging.exception("Failed to update the ECB file")
            self.updated.set()
            sleep(60 * 60)

    def convert(
        self, amount: float, from_currency: str, to_currency: str = "USD"
    ) -> float:
        """Convert amount from a currency to another one.

        Args:
            amount: amount in `from_currency` to be converted to `to_currency`
            from_currency: 3-letter currency code
            to_currency: 3-letter currency code (defaults to "USD")

        Examples:
            >>> c = currency_converter
            >>> c.convert(42, "EUR", "HUF")  # doctest: +SKIP
            16371.6
        """
        with self.lock:
            return self.converter.convert(amount, from_currency, to_currency)


currency_converter = CurrencyConverter()
currency_converter.start()
safe_exit.register(close_tmpfiles, currency_converter.tmpfiles)
currency_converter.updated.wait(10)
