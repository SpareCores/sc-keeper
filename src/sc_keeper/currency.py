"""Thread-safe currency converter backed by ECB exchange rates.

On module load, the latest single-day ECB data is downloaded (with a 60s
timeout) and used to initialize the ``currency_converter`` library. If the
download fails, the library's built-in (potentially outdated) dataset is
used as a fallback.

A background daemon thread keeps the data fresh:

- After a successful download, the next check is scheduled at the
  Last-Modified timestamp of the data plus 1 day and 15 minutes (the ECB
  publishes once per business day around the same time).
- If a HEAD request shows the data hasn't changed, or if any
  HEAD/download fails, an exponential backoff (1 min doubling up to
  60 min) is used until a new version is found or the server recovers.

Callers use ``currency_converter.convert(amount, from_currency, to_currency)``
and ``currency_converter.converter.currencies``. The converter instance is
swapped by simple attribute assignment, which is atomic under CPython's GIL.
CC.convert() only reads immutable-after-init data, so no lock is needed.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from tempfile import NamedTemporaryFile
from threading import Event, Thread
from time import sleep

import httpx
from currency_converter import SINGLE_DAY_ECB_URL
from currency_converter import CurrencyConverter as CC

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 60
BACKOFF_MIN_INIT = 1
BACKOFF_MAX_MINUTES = 60
SCHEDULED_DELTA = timedelta(days=1, minutes=15)


def _load_cc_from_url(timeout_seconds: float):
    """Download ECB zip to a temp file (with timeout), init CC(path), then delete file.

    We do the download ourselves instead of calling CC(SINGLE_DAY_ECB_URL) because
    the library uses urllib.request.urlopen() with no timeout; a slow or stuck
    connection would block indefinitely. Here we use httpx.get(..., timeout=...) so
    the load either completes or fails within timeout_seconds.

    CC reads the file fully in __init__ into memory and never touches the path again,
    so deleting the temp file after CC() returns is safe on all platforms.

    On any failure (download, temp file creation, write, or CC init) returns
    (None, None, error) so callers can fall back to the built-in database.

    Returns (cc_instance, last_modified_header, None) on success.
    """
    path = None
    try:
        r = httpx.get(SINGLE_DAY_ECB_URL, timeout=timeout_seconds)
        r.raise_for_status()
        last_modified = r.headers.get("Last-Modified")
        with NamedTemporaryFile(delete=False, suffix=".zip") as f:
            path = f.name
            f.write(r.content)
        return (CC(path), last_modified, None)
    except Exception as e:
        return (None, None, e)
    finally:
        if path:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


def _next_scheduled(last_modified_header: str):
    """Return next check datetime: last_modified + 1 day + 15 minutes (UTC)."""
    dt = parsedate_to_datetime(last_modified_header)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt + SCHEDULED_DELTA


class CurrencyConverter(Thread):
    """Currency converter with auto-updates. Data source: ECB (remote or built-in fallback)."""

    converter: CC
    daemon = True

    def __init__(self, *args, **kwargs):
        self.converter = None
        self._last_modified = None
        self._next_check_at = None
        self._backoff_minutes = BACKOFF_MIN_INIT
        self.updated = Event()
        super().__init__(*args, **kwargs)

    def _schedule(self, last_modified: str):
        """Schedule next check at last_modified + 1 day + 15 minutes; reset backoff.
        If Last-Modified cannot be parsed, degrades to exponential backoff instead of raising.
        """
        self._last_modified = last_modified
        try:
            self._next_check_at = _next_scheduled(last_modified)
        except (ValueError, TypeError, AttributeError):
            self._enter_backoff("Last-Modified header could not be parsed")
            return
        self._backoff_minutes = BACKOFF_MIN_INIT
        logger.debug("Next ECB check scheduled at %s", self._next_check_at.isoformat())

    def _enter_backoff(self, reason: str):
        """Schedule next check using exponential backoff (1 min up to 60 min)."""
        interval = min(self._backoff_minutes, BACKOFF_MAX_MINUTES)
        self._next_check_at = datetime.now(timezone.utc) + timedelta(minutes=interval)
        logger.warning("%s. Next ECB check in %s minute(s).", reason, interval)
        self._backoff_minutes = min(self._backoff_minutes * 2, BACKOFF_MAX_MINUTES)

    def _initial_load(self):
        """Load CC at module start: try remote URL with timeout, fall back to built-in."""
        cc_instance, last_modified, err = _load_cc_from_url(DOWNLOAD_TIMEOUT)
        if cc_instance is not None:
            self.converter = cc_instance
            self.updated.set()
            logger.info("Loaded ECB data from %s", SINGLE_DAY_ECB_URL)
            if last_modified:
                self._schedule(last_modified)
            else:
                self._enter_backoff("No Last-Modified in initial download response")
            return
        logger.error(
            "Initial load from %s failed: %s. Using built-in database (may be outdated).",
            SINGLE_DAY_ECB_URL,
            err,
        )
        self.converter = CC()
        self.updated.set()
        self._enter_backoff(f"Initial download failed: {err}")

    def _fetch_and_update(self, head_last_modified: str) -> bool:
        """Fetch CC from URL (with timeout). On success update converter and return True."""
        cc_instance, dl_last_modified, err = _load_cc_from_url(DOWNLOAD_TIMEOUT)
        if cc_instance is None:
            self._enter_backoff(f"Download failed: {err}")
            return False
        last_modified = dl_last_modified or head_last_modified
        self.converter = cc_instance
        if last_modified:
            self._schedule(last_modified)
        else:
            self._enter_backoff("Updated ECB data but no Last-Modified available")
        logger.info("Updated ECB data (Last-Modified: %s)", last_modified)
        return True

    def update(self):
        """Perform one HEAD; update CC if new Last-Modified, else backoff."""
        try:
            r = httpx.head(SINGLE_DAY_ECB_URL, timeout=10.0)
        except Exception as e:
            self._enter_backoff(f"HEAD request failed: {e}")
            return
        if r.status_code != 200:
            self._enter_backoff(f"HEAD returned {r.status_code}")
            return
        last_modified = r.headers.get("Last-Modified")
        if not last_modified:
            self._enter_backoff("HEAD response had no Last-Modified")
            return
        if last_modified != self._last_modified:
            self._fetch_and_update(last_modified)
            return
        self._enter_backoff("HEAD returned unchanged Last-Modified")

    def run(self):
        """Update loop: waits until next scheduled check, then runs update."""
        while True:
            now = datetime.now(timezone.utc)
            if self._next_check_at is not None and now < self._next_check_at:
                delay = (self._next_check_at - now).total_seconds()
                sleep(max(0, min(delay, 3600)))
                continue
            try:
                self.update()
            except Exception:
                logger.exception("Failed to update ECB data")
                self._enter_backoff("Update raised an exception")

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
        return self.converter.convert(amount, from_currency, to_currency)


currency_converter = CurrencyConverter()
currency_converter._initial_load()
currency_converter.start()
currency_converter.updated.wait(10)
