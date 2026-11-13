"""M2-R1: availability arithmetic never counts unmonitored or failed time as uptime."""

from datetime import UTC, datetime, timedelta

from asta.paper.supervision import Sample, expected_candles, summarize

T0 = datetime(2026, 11, 12, 0, 0, tzinfo=UTC)


def run(pattern, step=10, feeds_ok=True):
    """pattern: string of A (available), P (process dead), D (db down), W (worker stale)."""
    out = []
    for i, c in enumerate(pattern):
        out.append(
            Sample(
                T0 + timedelta(seconds=i * step),
                child_alive=c != "P",
                db_ok=c != "D",
                worker_ok=c not in "WD",
                feeds={"BINANCE": feeds_ok and c == "A"},
            )
        )
    return out


def test_fully_available_window():
    s = summarize(run("A" * 60), T0, T0 + timedelta(seconds=590))
    assert s["service_availability"] > 0.98 and not s["downtime_episodes"]
    assert s["unmonitored_seconds"] == 0


def test_downtime_episode_has_cause_and_recovery_time():
    s = summarize(run("AAAA" + "PPPPPP" + "AAAA"), T0, T0 + timedelta(seconds=130))
    ep = s["downtime_episodes"]
    assert len(ep) == 1 and ep[0]["cause"] == "process_exit" and ep[0]["recovery_seconds"] == 60
    assert 0.5 < s["service_availability"] < 0.9
    assert s["unavailable_seconds"] == 60


def test_causes_are_distinguished_and_an_open_episode_is_reported():
    assert (
        summarize(run("AADDA"), T0, T0 + timedelta(seconds=50))["downtime_episodes"][0]["cause"]
        == "database"
    )
    assert (
        summarize(run("AAWWA"), T0, T0 + timedelta(seconds=50))["downtime_episodes"][0]["cause"]
        == "worker_heartbeat"
    )
    openep = summarize(run("AAPP"), T0, T0 + timedelta(seconds=40))["downtime_episodes"]
    assert openep[0]["end"] is None and openep[0]["recovery_seconds"] is None


def test_time_without_samples_is_unmonitored_and_never_counts_as_uptime():
    first = run("A" * 6)
    later = [Sample(T0 + timedelta(seconds=600 + i * 10), True, True, True, {}) for i in range(6)]
    s = summarize(first + later, T0, T0 + timedelta(seconds=660))
    assert s["unmonitored_seconds"] >= 500
    assert s["service_availability"] < 0.2 and s["monitored_fraction"] < 0.2
    assert summarize([], T0, T0 + timedelta(hours=1))["unmonitored_seconds"] == 3600


def test_feed_freshness_is_real_time_and_independent_of_process_uptime():
    s = summarize(run("A" * 30, feeds_ok=False), T0, T0 + timedelta(seconds=290))
    assert s["service_availability"] > 0.95 and s["feed_fresh_fraction"]["BINANCE"] == 0.0


def test_expected_candles_and_json_roundtrip():
    assert expected_candles(T0, T0 + timedelta(hours=2)) == 120
    smp = run("A")[0]
    assert Sample.from_json(smp.to_json()) == smp


def test_per_venue_outage_intervals_are_reported_separately_from_service_availability():
    samples = []
    for i in range(30):
        samples.append(
            Sample(
                T0 + timedelta(seconds=i * 10),
                True,
                True,
                True,
                {"BINANCE": True, "OKX": not (10 <= i < 20)},
            )
        )
    s = summarize(samples, T0, T0 + timedelta(seconds=290))
    assert s["service_availability"] > 0.95  # the service itself was fine
    ivs = s["feed_outage_intervals"]
    assert ivs["BINANCE"] == [] and len(ivs["OKX"]) == 1 and ivs["OKX"][0]["seconds"] == 100
    assert s["feed_fresh_fraction"]["OKX"] < s["feed_fresh_fraction"]["BINANCE"]
