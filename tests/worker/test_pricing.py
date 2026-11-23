from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError

from worker.pricing import SpotPrices

T0 = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


class FakeEc2:
    """describe_spot_price_history, with the price changes given as (minute, price)."""

    def __init__(self, changes=(), error=None, pages=1):
        self.changes, self.error, self.pages = list(changes), error, pages
        self.calls = []

    def describe_spot_price_history(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        rows = [{"Timestamp": at(m), "SpotPrice": str(p), "InstanceType": kwargs["InstanceTypes"][0],
                 "AvailabilityZone": kwargs.get("AvailabilityZone", "us-west-2a")} for m, p in self.changes]
        rows.sort(key=lambda r: r["Timestamp"], reverse=True)    # AWS answers newest first
        if self.pages == 1:
            return {"SpotPriceHistory": rows}
        token = kwargs.get("NextToken")
        half = len(rows) // 2
        return ({"SpotPriceHistory": rows[:half], "NextToken": "more"} if not token
                else {"SpotPriceHistory": rows[half:]})


def prices(ec2, fallback=None):
    return SpotPrices(ec2, fallback or {})


def test_flat_price_for_an_hour():
    ec2 = FakeEc2([(-30, 0.80)])
    assert prices(ec2).cost("g5.2xlarge", "us-west-2a", at(0), at(60)) == pytest.approx(0.80)


def test_cost_scales_with_the_seconds_used():
    ec2 = FakeEc2([(-30, 0.90)])
    assert prices(ec2).cost("g5.2xlarge", "us-west-2a", at(0), at(10)) == pytest.approx(0.15)


def test_price_change_mid_attempt_is_time_weighted():
    # 0.60 for the first 30 minutes, 1.20 for the next 30: 0.30 + 0.60
    ec2 = FakeEc2([(-5, 0.60), (30, 1.20)])
    assert prices(ec2).cost("g5.2xlarge", "us-west-2a", at(0), at(60)) == pytest.approx(0.90)


def test_the_price_in_effect_before_the_start_counts():
    # the API also returns the last change before StartTime; an older one is ignored
    ec2 = FakeEc2([(-90, 0.10), (-5, 0.60)])
    assert prices(ec2).cost("g5.2xlarge", "us-west-2a", at(0), at(60)) == pytest.approx(0.60)


def test_asks_for_this_type_and_zone_over_the_attempt():
    ec2 = FakeEc2([(-5, 0.7)])
    prices(ec2).cost("g5.2xlarge", "us-west-2b", at(0), at(60))
    call = ec2.calls[0]
    assert call["InstanceTypes"] == ["g5.2xlarge"]
    assert call["AvailabilityZone"] == "us-west-2b"
    assert call["ProductDescriptions"] == ["Linux/UNIX"]
    assert call["StartTime"] == at(0) and call["EndTime"] == at(60)


def test_follows_pagination():
    ec2 = FakeEc2([(-5, 0.60), (20, 1.00), (40, 0.80), (50, 0.40)], pages=2)
    cost = prices(ec2).cost("g5.2xlarge", "us-west-2a", at(0), at(60))
    assert cost == pytest.approx((20 * 0.60 + 20 * 1.00 + 10 * 0.80 + 10 * 0.40) / 60)
    assert len(ec2.calls) == 2


def test_zero_length_attempt_costs_nothing():
    assert prices(FakeEc2([(-5, 0.7)])).cost("g5.2xlarge", "us-west-2a", at(0), at(0)) == 0.0


def test_api_error_uses_the_fallback_table():
    err = ClientError({"Error": {"Code": "UnauthorizedOperation", "Message": "no"}}, "DescribeSpotPriceHistory")
    got = prices(FakeEc2(error=err), {"g5.2xlarge": 0.90}).cost("g5.2xlarge", "us-west-2a", at(0), at(60))
    assert got == pytest.approx(0.90)


def test_no_history_uses_the_fallback_table():
    got = prices(FakeEc2([]), {"g5.2xlarge": 0.90}).cost("g5.2xlarge", "us-west-2a", at(0), at(30))
    assert got == pytest.approx(0.45)


def test_unknown_zone_uses_the_fallback_table():
    ec2 = FakeEc2([(-5, 0.7)])
    got = prices(ec2, {"g5.2xlarge": 0.90}).cost("g5.2xlarge", None, at(0), at(60))
    assert got == pytest.approx(0.90) and ec2.calls == []


def test_no_price_anywhere_is_none_not_a_failure():
    assert prices(FakeEc2(error=RuntimeError("down"))).cost("p4d.24xlarge", "us-west-2a", at(0), at(60)) is None


def test_unknown_instance_type_is_none():
    assert prices(FakeEc2([]), {"g5.2xlarge": 0.9}).cost("unknown", None, at(0), at(60)) is None
