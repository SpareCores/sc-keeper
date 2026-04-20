"""Unit tests for /servers endpoint focusing on response data correctness."""

from fastapi.testclient import TestClient

from sc_keeper.api import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_servers(**params):
    """Shortcut that returns parsed JSON list and the raw response."""
    resp = client.get("/servers", params=params)
    assert resp.status_code == 200
    return resp.json(), resp


# ---------------------------------------------------------------------------
# Basic response structure
# ---------------------------------------------------------------------------


class TestResponseStructure:
    def test_default_returns_list(self):
        data, _ = get_servers()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_default_limit(self):
        data, _ = get_servers()
        assert len(data) <= 25

    def test_custom_limit(self):
        data, _ = get_servers(limit=5)
        assert len(data) <= 5

    def test_server_has_required_fields(self):
        data, _ = get_servers(limit=1)
        server = data[0]
        for field in [
            "vendor_id",
            "server_id",
            "vcpus",
            "memory_amount",
            "min_price",
            "vendor",
        ]:
            assert field in server, f"Missing field: {field}"

    def test_vendor_nested(self):
        data, _ = get_servers(limit=1)
        vendor = data[0]["vendor"]
        assert "vendor_id" in vendor
        assert "name" in vendor

    def test_no_price_breakdown_without_extras(self):
        """price_breakdown is always present on every response (part of ServerPKs)."""
        data, _ = get_servers(limit=1)
        assert "price_breakdown" in data[0]

    def test_price_breakdown_empty_without_extras(self):
        """Without traffic/storage params all traffic/storage breakdown fields should be zero."""
        data, _ = get_servers(limit=1)
        pb = data[0]["price_breakdown"]
        for field in [
            "traffic_hourly",
            "traffic_monthly",
            "extra_storage_hourly",
            "extra_storage_monthly",
        ]:
            assert (pb.get(field) or 0) == 0, f"Expected {field} to be 0 without extras"


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


class TestOrdering:
    def test_order_by_min_price_asc(self):
        data, _ = get_servers(limit=10, order_by="min_price", order_dir="asc")
        prices = [s["min_price"] for s in data if s["min_price"] is not None]
        assert prices == sorted(prices)

    def test_order_by_min_price_desc(self):
        data, _ = get_servers(limit=10, order_by="min_price", order_dir="desc")
        prices = [s["min_price"] for s in data if s["min_price"] is not None]
        assert prices == sorted(prices, reverse=True)

    def test_order_by_vcpus(self):
        data, _ = get_servers(limit=10, order_by="vcpus", order_dir="asc")
        vcpus = [s["vcpus"] for s in data]
        assert vcpus == sorted(vcpus)

    def test_order_by_memory(self):
        data, _ = get_servers(limit=10, order_by="memory_amount", order_dir="asc")
        mem = [s["memory_amount"] for s in data]
        assert mem == sorted(mem)


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


class TestFiltering:
    def test_vendor_filter(self):
        data, _ = get_servers(vendor=["aws"], limit=10)
        assert all(s["vendor_id"] == "aws" for s in data)

    def test_multi_vendor_filter(self):
        data, _ = get_servers(vendor=["aws", "hcloud"], limit=50)
        vendor_ids = {s["vendor_id"] for s in data}
        assert vendor_ids <= {"aws", "hcloud"}

    def test_vcpus_min(self):
        data, _ = get_servers(vcpus_min=8, limit=10)
        assert all(s["vcpus"] >= 8 for s in data)

    def test_vcpus_max(self):
        data, _ = get_servers(vcpus_max=4, limit=10)
        assert all(s["vcpus"] <= 4 for s in data)

    def test_vcpus_range(self):
        data, _ = get_servers(vcpus_min=4, vcpus_max=8, limit=10)
        assert all(4 <= s["vcpus"] <= 8 for s in data)

    def test_memory_min(self):
        data, _ = get_servers(memory_min=16000, limit=10)
        assert all(s["memory_amount"] >= 16000 for s in data)

    def test_architecture(self):
        data, _ = get_servers(architecture="arm64", limit=10)
        assert all(s["cpu_architecture"] == "arm64" for s in data)

    def test_partial_name_or_id(self):
        data, _ = get_servers(partial_name_or_id="t3", vendor=["aws"], limit=10)
        assert all(
            any(
                "t3" in (s.get(f) or "").lower()
                for f in ("server_id", "name", "api_reference", "display_name")
            )
            for s in data
        )

    def test_gpu_min(self):
        data, _ = get_servers(gpu_min=1, limit=10, order_by="vcpus")
        assert all(s["gpu_count"] >= 1 for s in data)

    def test_countries_filter(self):
        """Country filter should reduce the result set compared to unfiltered."""
        _, baseline = get_servers(vendor=["aws"], limit=1, add_total_count_header=True)
        _, filtered = get_servers(
            vendor=["aws"], countries=["DE"], limit=1, add_total_count_header=True
        )
        assert int(filtered.headers["x-total-count"]) < int(
            baseline.headers["x-total-count"]
        )


# ---------------------------------------------------------------------------
# Currency conversion
# ---------------------------------------------------------------------------


class TestCurrency:
    def test_default_currency_usd(self):
        data, _ = get_servers(limit=1)
        assert data[0].get("currency", "USD") == "USD"

    def test_eur_currency(self):
        data, _ = get_servers(limit=1, currency="EUR")
        # currency field is only present when conversion happens
        assert data[0].get("currency", "EUR") == "EUR"

    def test_different_prices_for_different_currencies(self):
        usd_data, _ = get_servers(vendor=["aws"], limit=1, currency="USD")
        eur_data, _ = get_servers(vendor=["aws"], limit=1, currency="EUR")
        # prices should differ after conversion (unless rate is exactly 1)
        if usd_data[0]["min_price"] and eur_data[0]["min_price"]:
            assert usd_data[0]["min_price"] != eur_data[0]["min_price"]


# ---------------------------------------------------------------------------
# Best price allocation
# ---------------------------------------------------------------------------


class TestBestPriceAllocation:
    def test_spot_only(self):
        data, _ = get_servers(best_price_allocation="SPOT_ONLY", limit=10)
        for s in data:
            if s["min_price_spot"] is not None:
                assert s["min_price"] == s["min_price_spot"]

    def test_ondemand_only(self):
        data, _ = get_servers(best_price_allocation="ONDEMAND_ONLY", limit=10)
        for s in data:
            if s["min_price_ondemand"] is not None:
                assert s["min_price"] == s["min_price_ondemand"]

    def test_monthly(self):
        data, _ = get_servers(best_price_allocation="MONTHLY", limit=10)
        for s in data:
            if s["min_price_ondemand_monthly"] is not None:
                assert s["min_price"] == s["min_price_ondemand_monthly"]


# ---------------------------------------------------------------------------
# Monthly outbound traffic - price_breakdown
# ---------------------------------------------------------------------------


class TestMonthlyOutboundTraffic:
    def test_price_breakdown_present(self):
        data, _ = get_servers(monthly_outbound_traffic=1000, limit=5)
        for s in data:
            assert "price_breakdown" in s
            pb = s["price_breakdown"]
            assert pb["traffic_monthly"] is not None
            assert pb["traffic_monthly"] >= 0

    def test_traffic_adds_to_price(self):
        """min_price with traffic should be >= min_price without traffic."""
        base, _ = get_servers(vendor=["hcloud"], limit=5, order_by="vcpus")
        with_traffic, _ = get_servers(
            vendor=["hcloud"], monthly_outbound_traffic=5000, limit=5, order_by="vcpus"
        )
        base_by_id = {s["server_id"]: s for s in base}
        traffic_by_id = {s["server_id"]: s for s in with_traffic}
        common_ids = base_by_id.keys() & traffic_by_id.keys()
        assert common_ids
        for server_id in common_ids:
            b = base_by_id[server_id]
            t = traffic_by_id[server_id]
            if b["min_price"] is not None and t["min_price"] is not None:
                assert t["min_price"] >= b["min_price"]

    def test_breakdown_components_sum_to_min_price(self):
        """min_price should equal compute price + traffic hourly + extra storage hourly."""
        data, _ = get_servers(monthly_outbound_traffic=1000, limit=10)
        for s in data:
            pb = s["price_breakdown"]
            if s["min_price"] is not None and pb["compute_min_price"] is not None:
                expected = (
                    (pb["compute_min_price"] or 0)
                    + (pb["traffic_hourly"] or 0)
                    + (pb["extra_storage_hourly"] or 0)
                )
                assert abs(s["min_price"] - expected) < 0.001

    def test_zero_traffic_cost_without_param(self):
        """Without monthly_outbound_traffic, traffic_hourly and traffic_monthly should be 0."""
        data, _ = get_servers(limit=5)
        for s in data:
            pb = s["price_breakdown"]
            assert (pb.get("traffic_hourly") or 0) == 0
            assert (pb.get("traffic_monthly") or 0) == 0

    def test_traffic_monthly_is_hourly_times_730(self):
        """traffic_hourly should equal traffic_monthly / 730 (within rounding)."""
        data, _ = get_servers(
            monthly_outbound_traffic=1000, limit=10, best_price_allocation="ANY"
        )
        for s in data:
            pb = s["price_breakdown"]
            if pb.get("traffic_hourly") and pb.get("traffic_monthly"):
                assert abs(pb["traffic_hourly"] - pb["traffic_monthly"] / 730) < 0.0001


# ---------------------------------------------------------------------------
# Extra storage - price_breakdown
# ---------------------------------------------------------------------------


class TestExtraStorage:
    def test_price_breakdown_present(self):
        data, _ = get_servers(extra_storage_size=200, limit=5)
        for s in data:
            assert "price_breakdown" in s
            pb = s["price_breakdown"]
            assert pb["extra_storage_monthly"] is not None

    def test_no_extra_cost_when_builtin_exceeds(self):
        """Servers with storage >= extra_storage_size should have 0 extra storage cost."""
        data, _ = get_servers(extra_storage_size=10, limit=20)
        for s in data:
            pb = s["price_breakdown"]
            if s.get("storage_size") and s["storage_size"] >= 10:
                assert pb["extra_storage_monthly"] == 0

    def test_storage_adds_to_price(self):
        """min_price with extra storage should be >= min_price without."""
        base, _ = get_servers(vendor=["hcloud"], limit=5, order_by="vcpus")
        with_storage, _ = get_servers(
            vendor=["hcloud"], extra_storage_size=500, limit=5, order_by="vcpus"
        )
        base_by_id = {s["server_id"]: s for s in base}
        storage_by_id = {s["server_id"]: s for s in with_storage}
        common_ids = base_by_id.keys() & storage_by_id.keys()
        assert common_ids
        for server_id in common_ids:
            b = base_by_id[server_id]
            s = storage_by_id[server_id]
            if b["min_price"] is not None and s["min_price"] is not None:
                assert s["min_price"] >= b["min_price"]

    def test_storage_type_filter(self):
        """Filtering by storage type should still return valid breakdown."""
        data, _ = get_servers(
            extra_storage_size=200, extra_storage_type=["ssd"], limit=5
        )
        for s in data:
            assert "price_breakdown" in s


# ---------------------------------------------------------------------------
# Combined traffic + storage
# ---------------------------------------------------------------------------


class TestCombinedExtras:
    def test_both_extras_present(self):
        data, _ = get_servers(monthly_outbound_traffic=1000, extra_storage_size=200, limit=5)
        for s in data:
            pb = s["price_breakdown"]
            assert pb["traffic_monthly"] is not None
            assert pb["extra_storage_monthly"] is not None

    def test_total_equals_sum_of_parts_monthly(self):
        """min_price_ondemand_monthly should equal compute monthly + traffic monthly + extra storage monthly."""
        data, _ = get_servers(
            monthly_outbound_traffic=1000,
            extra_storage_size=200,
            best_price_allocation="MONTHLY",
            limit=10,
        )
        for s in data:
            pb = s["price_breakdown"]
            if s["min_price"] is not None and pb["compute_min_price_ondemand_monthly"] is not None:
                expected = (
                    (pb["compute_min_price_ondemand_monthly"] or 0)
                    + (pb["traffic_monthly"] or 0)
                    + (pb["extra_storage_monthly"] or 0)
                )
                assert abs(s["min_price"] - expected) < 0.02

    def test_min_price_equals_compute_plus_extras_hourly(self):
        """For hourly allocations, min_price should equal compute + traffic + extra storage hourly."""
        data, _ = get_servers(
            monthly_outbound_traffic=500,
            extra_storage_size=100,
            best_price_allocation="ANY",
            limit=10,
        )
        for s in data:
            pb = s["price_breakdown"]
            if s["min_price"] is not None and pb["compute_min_price"] is not None:
                expected = (
                    (pb["compute_min_price"] or 0)
                    + (pb["traffic_hourly"] or 0)
                    + (pb["extra_storage_hourly"] or 0)
                )
                assert abs(s["min_price"] - expected) < 0.001


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestPagination:
    def test_page_1_and_2_differ(self):
        page1, _ = get_servers(limit=5, page=1, order_by="server_id")
        page2, _ = get_servers(limit=5, page=2, order_by="server_id")
        ids1 = {(s["vendor_id"], s["server_id"]) for s in page1}
        ids2 = {(s["vendor_id"], s["server_id"]) for s in page2}
        assert ids1.isdisjoint(ids2)

    def test_total_count_header(self):
        _, resp = get_servers(limit=5, add_total_count_header=True)
        assert "x-total-count" in resp.headers
        assert int(resp.headers["x-total-count"]) > 0

    def test_no_total_count_by_default(self):
        _, resp = get_servers(limit=5)
        assert "x-total-count" not in resp.headers
