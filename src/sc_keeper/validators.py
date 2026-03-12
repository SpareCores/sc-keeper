from typing import List, Optional

from fastapi import HTTPException, Request

from .currency import currency_converter


def check_currency(currency: Optional[str]) -> None:
    if currency and currency not in currency_converter.converter.currencies:
        raise HTTPException(status_code=400, detail="Invalid currency code")


def check_filter_limits(
    request: Request,
    countries: Optional[List[str]] = None,
    regions: Optional[List[str]] = None,
    vendor_regions: Optional[List[str]] = None,
    benchmark_id: Optional[str] = None,
    max_countries: int = 1,
    max_regions: int = 3,
) -> None:
    """Enforce filter count limits for unauthenticated requests.

    Raises HTTP 400 if an unauthenticated request exceeds the allowed number
    of filter values. Authenticated users (i.e. requests with a valid token
    resolved by AuthMiddleware) bypass all limits.

    The combined count of `regions` and `vendor_regions` is checked against
    `max_regions`, since they are alternative ways to filter by region and
    their total should stay within a reasonable bound.

    Args:
        request: The incoming FastAPI request (used to check auth state).
        countries: List of country filter values provided by the caller.
        regions: List of region filter values provided by the caller.
        vendor_regions: List of vendor-region filter values provided by the caller.
        benchmark_id: Optional benchmark_id filter (enforces auth if provided).
        max_countries: Maximum number of country values allowed without auth.
        max_regions: Maximum combined number of region and vendor-region values allowed without auth.

    Raises:
        HTTPException: 400 if any filter exceeds the allowed limit for unauthenticated requests.
    """
    user = getattr(request.state, "user", None)
    if user:
        return
    if benchmark_id:
        raise HTTPException(
            status_code=401,
            detail="Filtering by benchmark_id requires authentication.",
        )
    regions_set = set(regions or [])
    if vendor_regions:
        for vr in vendor_regions:
            try:
                _, r = vr.split("~", 1)
                regions_set.add(r)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid vendor_region format: {vr}. Expected 'vendor_id~region_id'.",
                )
    if len(regions_set) > max_regions:
        raise HTTPException(
            status_code=400,
            detail=f"Max {max_regions} {'regions' if max_regions > 1 else 'region'} can be queried at a time without authentication.",
        )
    if len(countries or []) > max_countries:
        raise HTTPException(
            status_code=400,
            detail=f"Max {max_countries} {'countries' if max_countries > 1 else 'country'} can be queried at a time without authentication.",
        )
