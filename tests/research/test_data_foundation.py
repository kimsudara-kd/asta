import json
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP
from decimal import Decimal as D
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from asta.core.money import QUANTUM, quantize, round_down, round_up
from asta.research.data.checksum import bars_checksum, canon_decimal
from asta.research.data.fixtures import synthetic_dataset
from asta.research.data.importer import (
    ImportRejected,
    import_candles_file,
    parse_candles_csv,
    parse_quotes_csv,
    parse_trades_csv,
)
from asta.research.data.models import Bar, QuoteTick, TradeTick
from asta.research.data.quality import validate_bars, validate_quotes, validate_trades
from asta.research.data.resample import ResampleError, resample
from asta.research.data.store import DatasetFileStore, StoreError
from asta.research.types import Severity, SourceType, Timeframe

from .helpers import T0, bar, dataset, flat_bars


# ---------------- 1. Decimal precision and rounding ----------------
def test_rounding_modes_are_explicit_and_conservative():
    x = D("1.123456785")
    assert quantize(x) == D("1.12345678")  # half-even
    assert round_up(x) == D("1.12345679")  # costs round against us
    assert round_down(x) == D("1.12345678")  # receipts round against us
    assert quantize(D("0.000000005")) == D("0.00000000")  # banker's: .5 -> even (0)
    assert quantize(D("0.000000015")) == D("0.00000002")
    assert QUANTUM == D("1e-8")
    assert ROUND_UP != ROUND_DOWN != ROUND_HALF_EVEN


@given(st.decimals(min_value=0, max_value=10**6, places=12, allow_nan=False, allow_infinity=False))
def test_round_up_never_below_round_down_and_within_one_quantum(x):
    lo, hi = round_down(x), round_up(x)
    assert lo <= x <= hi and hi - lo <= QUANTUM


def test_decimal_context_has_no_float_leak():
    assert (D("0.1") + D("0.2")) == D("0.3")
    with pytest.raises(TypeError):
        from asta.core.money import to_decimal

        to_decimal(0.1)


# ---------------- 2/3. import, validation, duplicates, missing ----------------
def write_csv(tmp: Path, name: str, rows: list[str], meta: dict | None = None) -> Path:
    p = tmp / name
    p.write_text(
        "open_time,open,high,low,close,volume\n" + "\n".join(rows) + "\n", encoding="utf-8"
    )
    m = meta or {
        "venue": "BINANCE",
        "symbol": "BTC-USDT",
        "timeframe": "1h",
        "source": "unit-test file",
        "source_type": "EXCHANGE_HISTORICAL",
    }
    p.with_name(p.name + ".meta.json").write_text(json.dumps(m), encoding="utf-8")
    return p


def ms(i: int) -> str:
    return str(int((T0 + timedelta(hours=i)).timestamp() * 1000))


def test_import_valid_file_pass(tmp_path):
    rows = [f"{ms(i)},100,101,99,100.5,10" for i in range(5)]
    r = import_candles_file(write_csv(tmp_path, "a.csv", rows))
    assert r.report.status == Severity.PASS and r.dataset.meta.row_count == 5
    assert r.dataset.meta.accepted_for_research
    assert r.dataset.meta.provenance["file_name"] == "a.csv"
    assert r.dataset.meta.period_start == T0 and r.dataset.meta.period_end == T0 + timedelta(
        hours=5
    )


def test_import_requires_sidecar_and_rejects_bad_structure(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("open_time,open,high,low,close,volume\n", encoding="utf-8")
    with pytest.raises(ImportRejected, match="sidecar"):
        import_candles_file(p)
    q = write_csv(tmp_path, "y.csv", [])
    q.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ImportRejected, match="missing columns"):
        import_candles_file(q)
    with pytest.raises(ImportRejected, match="not found"):
        import_candles_file(tmp_path / "nope.csv")


def test_import_unparseable_rows_reject_dataset_not_silently_skip(tmp_path):
    rows = [
        f"{ms(0)},100,101,99,100,10",
        f"{ms(1)},abc,101,99,100,10",
        f"{ms(2)},100,101,99,100,10",
    ]
    r = import_candles_file(write_csv(tmp_path, "bad.csv", rows))
    assert r.report.status == Severity.REJECTED and r.report.has("PARSE_ERROR")
    assert not r.dataset.meta.accepted_for_research


def test_naive_timestamp_rejected(tmp_path):
    rows = ["2024-01-01T00:00:00,100,101,99,100,10", "2024-01-01T01:00:00+00:00,100,101,99,100,10"]
    r = import_candles_file(write_csv(tmp_path, "tz.csv", rows))
    assert r.report.has("PARSE_ERROR") and r.report.status == Severity.REJECTED
    assert any("timezone" in e for e in r.report.finding("PARSE_ERROR").examples)


def test_non_utc_offset_rejected_but_utc_z_and_offset_zero_accepted(tmp_path):
    rows = [
        "2024-01-01T00:00:00Z,100,101,99,100,10",
        "2024-01-01T01:00:00+00:00,100,101,99,100,10",
        "2024-01-01T03:00:00+02:00,100,101,99,100,10",  # = 01:00Z but a non-UTC offset: rejected
    ]
    r = import_candles_file(write_csv(tmp_path, "off.csv", rows))
    assert r.dataset.meta.row_count == 2 and r.report.has("PARSE_ERROR")


def test_identity_mismatch_in_columns_rejected(tmp_path):
    p = tmp_path / "id.csv"
    p.write_text(
        "open_time,open,high,low,close,volume,venue,symbol\n"
        f"{ms(0)},100,101,99,100,10,BINANCE,BTC-USDT\n"
        f"{ms(1)},100,101,99,100,10,OKX,BTC-USDT\n",
        encoding="utf-8",
    )
    p.with_name("id.csv.meta.json").write_text(
        json.dumps(
            {
                "venue": "BINANCE",
                "symbol": "BTC-USDT",
                "timeframe": "1h",
                "source": "s",
                "source_type": "EXCHANGE_HISTORICAL",
            }
        ),
        encoding="utf-8",
    )
    r = import_candles_file(p)
    assert r.report.status == Severity.REJECTED and r.report.has("PARSE_ERROR")


def test_symbol_and_venue_identity_validated():
    bars = flat_bars(3)
    assert validate_bars(bars, Timeframe.H1, "NOPE", "BTC-USDT").has("VENUE_UNKNOWN")
    assert validate_bars(bars, Timeframe.H1, "BINANCE", "btcusdt").has("SYMBOL_INVALID")
    assert validate_bars(bars, Timeframe.H1, "BINANCE", "BTC-USDT").status == Severity.PASS


def test_derived_source_cannot_be_imported(tmp_path):
    meta = {
        "venue": "BINANCE",
        "symbol": "BTC-USDT",
        "timeframe": "1h",
        "source": "x",
        "source_type": "DERIVED",
    }
    p = write_csv(tmp_path, "d.csv", [f"{ms(0)},1,1,1,1,1"], meta)
    with pytest.raises(ImportRejected, match="DERIVED"):
        import_candles_file(p)


def test_quality_empty_duplicates_order_alignment():
    assert validate_bars([], Timeframe.H1, "BINANCE", "BTC-USDT").status == Severity.REJECTED
    b = flat_bars(3)
    dup = validate_bars([b[0], b[1], b[1], b[2]], Timeframe.H1, "BINANCE", "BTC-USDT")
    assert dup.status == Severity.REJECTED and dup.finding("DUPLICATE").count == 1
    back = validate_bars([b[0], b[2], b[1]], Timeframe.H1, "BINANCE", "BTC-USDT")
    assert back.has("NON_MONOTONIC") and back.status == Severity.REJECTED
    shifted = [
        Bar(x.open_time + timedelta(minutes=7), x.open, x.high, x.low, x.close, x.volume) for x in b
    ]
    assert validate_bars(shifted, Timeframe.H1, "BINANCE", "BTC-USDT").has("NOT_ALIGNED")


def test_quality_price_volume_ohlc_rules():
    def one(**kw):
        base = dict(o="100", h="101", lo="99", c="100", v="5")
        base.update(kw)
        return [bar(0, base["o"], base["h"], base["lo"], base["c"], base["v"])]

    assert validate_bars(one(o="0"), Timeframe.H1, "BINANCE", "BTC-USDT").has("NON_POSITIVE_PRICE")
    assert validate_bars(one(lo="-1"), Timeframe.H1, "BINANCE", "BTC-USDT").has(
        "NON_POSITIVE_PRICE"
    )
    assert validate_bars(one(v="-1"), Timeframe.H1, "BINANCE", "BTC-USDT").has("NEGATIVE_VOLUME")
    assert validate_bars(one(h="99.5"), Timeframe.H1, "BINANCE", "BTC-USDT").has(
        "OHLC_INCONSISTENT"
    )
    assert validate_bars(one(lo="100.5"), Timeframe.H1, "BINANCE", "BTC-USDT").has(
        "OHLC_INCONSISTENT"
    )
    z = validate_bars(one(v="0"), Timeframe.H1, "BINANCE", "BTC-USDT")
    assert z.has("ZERO_VOLUME") and z.status == Severity.WARNING  # warning, not rejection


def gapped(n: int, drop: set[int]):
    return [bar(i, "100", "101", "99", "100", "10") for i in range(n) if i not in drop]


def test_missing_candle_policy_warning_vs_rejected():
    ok = validate_bars(gapped(1000, {10}), Timeframe.H1, "BINANCE", "BTC-USDT")
    assert ok.status == Severity.WARNING and ok.finding("GAPS").count == 1
    assert ok.stats["missing_rows"] == 1 and ok.stats["max_missing_run"] == 1
    many = validate_bars(gapped(1000, set(range(100, 110))), Timeframe.H1, "BINANCE", "BTC-USDT")
    # 10 of 1000 = 1.0 % > the 0.5 % limit -> REJECTED
    assert many.stats["missing_rows"] == 10 and many.finding("GAPS").severity == Severity.REJECTED
    assert many.status == Severity.REJECTED


def test_missing_candle_long_run_rejected_even_if_fraction_small():
    r = validate_bars(gapped(5000, set(range(100, 114))), Timeframe.H1, "BINANCE", "BTC-USDT")
    assert r.stats["max_missing_run"] == 14 and r.status == Severity.REJECTED  # run > 12


def test_gaps_are_never_filled_in_stored_data():
    bars = gapped(10, {4})
    r = dataset(bars)
    assert r.dataset.meta.row_count == 9 and len(r.dataset.bars) == 9  # nothing fabricated


def test_discontinuity_is_a_warning_with_examples():
    b = [bar(0, "100", "101", "99", "100"), bar(1, "150", "151", "149", "150")]
    r = validate_bars(b, Timeframe.H1, "BINANCE", "BTC-USDT")
    assert r.has("DISCONTINUITY") and r.status == Severity.WARNING


def test_precision_vs_tick_is_warning_only():
    b = [bar(0, "100.005", "101", "99", "100")]
    r = validate_bars(b, Timeframe.H1, "BINANCE", "BTC-USDT", tick_size=D("0.01"))
    assert r.has("PRECISION_TICK") and r.status == Severity.WARNING


def test_trades_and_quotes_import_and_validation():
    ts = lambda s: f"2024-01-01T00:00:{s:02d}+00:00"  # noqa: E731
    trades = parse_trades_csv(
        "ts,price,quantity,trade_id\n" + "\n".join(f"{ts(i)},100,0.5,{i}" for i in range(3))
    )
    assert validate_trades(trades).status == Severity.PASS
    assert validate_trades(trades + [trades[0]]).has("DUPLICATE")
    assert validate_trades(list(reversed(trades))).has("NON_MONOTONIC")
    assert validate_trades([TradeTick(trades[0].ts, D("0"), D("1"), "z")]).has("INVALID_VALUES")
    quotes = parse_quotes_csv(
        "ts,bid,ask,bid_qty,ask_qty\n" + "\n".join(f"{ts(i)},99,100,1,1" for i in range(3))
    )
    assert validate_quotes(quotes).status == Severity.PASS
    crossed = [QuoteTick(quotes[0].ts, D("101"), D("100"), D("1"), D("1"))]
    assert validate_quotes(crossed).has("CROSSED_BOOK")
    assert validate_trades([]).status == Severity.REJECTED


def test_parse_candles_csv_reports_line_numbers():
    bars, errs = parse_candles_csv(
        "open_time,open,high,low,close,volume\n1704067200000,1,2,1,2,3\nnotanumber,1,2,1,2,3\n",
        "BINANCE",
        "BTC-USDT",
    )
    assert len(bars) == 1 and errs and errs[0].startswith("line 3")


# ---------------- 4. versioning and checksums ----------------
def test_checksum_deterministic_and_content_sensitive():
    a = bars_checksum("BINANCE", "BTC-USDT", Timeframe.H1, flat_bars(4))
    assert a == bars_checksum("BINANCE", "BTC-USDT", Timeframe.H1, flat_bars(4))
    assert len(a) == 64
    changed = flat_bars(4)
    changed[2] = bar(2, "100", "100", "100", "100.00000001")
    assert a != bars_checksum("BINANCE", "BTC-USDT", Timeframe.H1, changed)
    assert a != bars_checksum("OKX", "BTC-USDT", Timeframe.H1, flat_bars(4))
    assert a != bars_checksum("BINANCE", "ETH-USDT", Timeframe.H1, flat_bars(4))
    assert a != bars_checksum("BINANCE", "BTC-USDT", Timeframe.M15, flat_bars(4))


def test_checksum_ignores_decimal_formatting_not_value():
    assert canon_decimal(D("1.50")) == canon_decimal(D("1.5")) == "1.5"
    assert canon_decimal(D("100")) == "100" and canon_decimal(D("-0")) == "0"
    x = [bar(0, "1.50", "2.0", "1.0", "1.5")]
    y = [bar(0, "1.5", "2", "1", "1.50")]
    assert bars_checksum("BINANCE", "BTC-USDT", Timeframe.H1, x) == bars_checksum(
        "BINANCE", "BTC-USDT", Timeframe.H1, y
    )


def test_store_roundtrip_verifies_checksum_and_detects_tampering(tmp_path):
    ds = dataset(gapped(20, set())).dataset
    store = DatasetFileStore(tmp_path)
    path = store.save(ds)
    assert path.name == f"{ds.meta.checksum}.csv" and store.exists(ds.meta.checksum)
    loaded = store.load(ds.meta.checksum)
    assert loaded.bars == ds.bars and loaded.meta.checksum == ds.meta.checksum
    assert loaded.meta.validation_status == Severity.PASS
    path.write_text(
        path.read_text().replace("100,101,99,100,10", "100,101,99,100,11", 1), encoding="utf-8"
    )
    with pytest.raises(StoreError, match="checksum mismatch"):
        store.load(ds.meta.checksum)
    with pytest.raises(StoreError):
        store.load("zz" * 32)
    with pytest.raises(StoreError, match="not in store"):
        store.load("a" * 64)


def test_store_save_is_idempotent(tmp_path):
    ds = dataset(flat_bars(5)).dataset
    s = DatasetFileStore(tmp_path)
    assert s.save(ds) == s.save(ds)


# ---------------- resampling: derive coarser only, never fabricate ----------------
def m5_bars(n: int, skip: set[int] = frozenset()):
    return [
        Bar(
            T0 + timedelta(minutes=5 * i),
            D(100 + i),
            D(101 + i),
            D(99 + i),
            D(100 + i) + D("0.5"),
            D("2"),
        )
        for i in range(n)
        if i not in skip
    ]


def test_resample_aggregates_ohlcv_exactly():
    parent = dataset(m5_bars(24), Timeframe.M5).dataset
    out = resample(parent, Timeframe.H1)
    assert out.report.status == Severity.PASS and out.dataset.meta.source_type == SourceType.DERIVED
    first = out.dataset.bars[0]  # bars 0..11
    assert first.open == D("100") and first.close == D("111.5")
    assert first.high == D("112") and first.low == D("99") and first.volume == D("24")
    assert out.dataset.meta.parent_checksums == (parent.meta.checksum,)
    assert out.dataset.meta.provenance["derived_from"] == parent.meta.checksum
    assert len(out.dataset.bars) == 2


def test_resample_drops_incomplete_buckets_instead_of_partial_bars():
    # 2004 five-minute bars = 167 whole hours; one missing bar is only a WARNING (0.05 %)
    parent = dataset(m5_bars(2004, skip={50}), Timeframe.M5).dataset  # gap inside hour #4
    assert parent.meta.validation_status == Severity.WARNING
    out = resample(parent, Timeframe.H1)
    assert len(out.dataset.bars) == 166  # the hour holding the gap is dropped, never half-built
    assert out.dataset.meta.provenance["dropped_incomplete_buckets"] == 1
    assert out.report.has("GAPS")  # the dropped hour stays visible as a gap in the derived data


def test_resample_never_derives_finer_or_from_rejected():
    h1 = dataset(flat_bars(5)).dataset
    with pytest.raises(ResampleError, match="never invented"):
        resample(h1, Timeframe.M5)
    with pytest.raises(ResampleError):
        resample(h1, Timeframe.H1)
    bad = dataset([flat_bars(2)[0], flat_bars(2)[0]], Timeframe.M5).dataset  # duplicate => REJECTED
    with pytest.raises(ResampleError, match="REJECTED"):
        resample(bad, Timeframe.H1)


def test_resample_chain_5m_to_all_supported_timeframes():
    parent = dataset(m5_bars(288 * 2), Timeframe.M5).dataset  # two days of 5m bars
    for tf, expect in (
        (Timeframe.M15, 192),
        (Timeframe.H1, 48),
        (Timeframe.H4, 12),
        (Timeframe.D1, 2),
    ):
        r = resample(parent, tf)
        assert len(r.dataset.bars) == expect and r.report.status == Severity.PASS


# ---------------- labelling: synthetic is never real ----------------
def test_synthetic_and_fixture_never_labelled_real_or_accepted():
    s = synthetic_dataset(segments=None)
    assert s.dataset.meta.source_type == SourceType.SYNTHETIC
    assert "not real exchange data" in s.dataset.meta.label
    assert not s.dataset.meta.is_real_exchange_data and not s.dataset.meta.accepted_for_research
    f = synthetic_dataset(source_type=SourceType.FIXTURE)
    assert not f.dataset.meta.accepted_for_research
    with pytest.raises(ValueError):
        synthetic_dataset(source_type=SourceType.EXCHANGE_HISTORICAL)


def test_derived_from_synthetic_stays_non_research():
    s = synthetic_dataset(timeframe=Timeframe.M5, segments=None)
    out = resample(s.dataset, Timeframe.H1)
    assert out.dataset.meta.provenance["root_source_type"] == "SYNTHETIC"
    assert not out.dataset.meta.accepted_for_research and not out.dataset.meta.is_real_exchange_data
    # the LABEL must also say so: a derived dataset never hides a synthetic root
    assert out.dataset.meta.label == "SYNTHETIC (not real exchange data) [derived]"
    assert (
        "SYNTHETIC" in out.dataset.meta.label
        and "DERIVED" not in out.dataset.meta.label.split("[")[0]
    )


def test_synthetic_generator_is_deterministic_and_valid():
    a = synthetic_dataset(seed=11).dataset
    b = synthetic_dataset(seed=11).dataset
    c = synthetic_dataset(seed=12).dataset
    assert a.meta.checksum == b.meta.checksum != c.meta.checksum
    assert a.meta.validation_status == Severity.PASS


def test_rejected_dataset_kept_for_diagnostics_but_not_accepted():
    r = dataset([flat_bars(2)[0], flat_bars(2)[0]])
    assert r.dataset.meta.validation_status == Severity.REJECTED
    assert r.dataset.meta.row_count == 2 and not r.dataset.meta.accepted_for_research


def test_dataset_slice_is_a_pure_view():
    ds = dataset(flat_bars(10)).dataset
    part = ds.slice(T0 + timedelta(hours=2), T0 + timedelta(hours=5))
    assert [b.open_time for b in part] == [T0 + timedelta(hours=h) for h in (2, 3, 4)]
    assert len(ds.bars) == 10
    assert datetime(2024, 1, 1, tzinfo=UTC) == T0
