"""Best-effort Spot cost of one job attempt (plan 08).

cost = seconds x the Spot price in the instance's zone, weighted by how long each price was in effect.
It is an estimate of the GPU instance alone: no boot or idle share, no egress. If the price history
cannot be read, a per-type fallback table is used; with neither, the cost is None. Pricing must never
fail a job, so every error here becomes the fallback or None.
"""
import logging

log = logging.getLogger("worker.pricing")

PRODUCT = "Linux/UNIX"


class SpotPrices:
    def __init__(self, ec2, fallback):
        self.ec2 = ec2
        self.fallback = fallback        # {instance_type: USD per hour}

    def cost(self, instance_type, zone, start, end):
        """USD for running `instance_type` in `zone` from `start` to `end` (aware datetimes), or None."""
        hours = max((end - start).total_seconds(), 0) / 3600
        rate = self._market_rate(instance_type, zone, start, end)
        if rate is None:
            rate = self.fallback.get(instance_type)
        return None if rate is None else hours * rate

    def _market_rate(self, instance_type, zone, start, end):
        """Average USD per hour over the attempt, or None if the history is missing or unreadable."""
        if not zone:
            return None
        try:
            changes = self._history(instance_type, zone, start, end)
        except Exception as e:
            log.warning("spot price history failed for %s: %s", instance_type, e)
            return None
        return _time_weighted(changes, start, end)

    def _history(self, instance_type, zone, start, end):
        """[(timestamp, USD per hour)] oldest first. AWS also returns the last change before `start`."""
        kwargs = dict(InstanceTypes=[instance_type], AvailabilityZone=zone, ProductDescriptions=[PRODUCT],
                      StartTime=start, EndTime=end)
        rows = []
        while True:
            page = self.ec2.describe_spot_price_history(**kwargs)
            rows += [(r["Timestamp"], float(r["SpotPrice"])) for r in page["SpotPriceHistory"]]
            if not page.get("NextToken"):
                return sorted(rows)
            kwargs["NextToken"] = page["NextToken"]


def _time_weighted(changes, start, end):
    """Average price over [start, end]; a price holds from its timestamp until the next change."""
    if not changes:
        return None
    price = None                    # price in effect at `start`
    inside = []
    for ts, p in changes:
        if ts <= start:
            price = p
        else:
            inside.append((ts, p))
    if price is None:
        price = inside[0][1]        # nothing before start: the first change is the best guess
    span = (end - start).total_seconds()
    if span <= 0:
        return price
    total, cursor = 0.0, start
    for ts, p in inside:
        if ts >= end:
            break
        total += price * (ts - cursor).total_seconds()
        cursor, price = ts, p
    total += price * (end - cursor).total_seconds()
    return total / span
