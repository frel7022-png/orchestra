"""db_fetch_daily_prices.fill_recent_gaps — cron이 하루 빠진 날을 다음 실행 때 저절로 메우는지(2026-10-13)."""
import importlib
import os

import portfolio_core as core


def _load(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test")
    return importlib.import_module("db_fetch_daily_prices")


def test_fill_recent_gaps_backfills_only_missing_day(monkeypatch):
    m = _load(monkeypatch)
    watch = [{"stock_code": c, "stock_name": c} for c in ("000001", "000002", "000003")]
    days = ["2026-10-07", "2026-10-08", "2026-10-09"]

    def fake_hist(code, start, end):
        if code == "KOSPI":
            return [{"날짜": d, "종가": 1.0, "거래량": 1} for d in days + ["2026-10-10"]]
        return [{"날짜": "2026-10-07", "종가": 100.0, "거래량": 10},
                {"날짜": "2026-10-08", "종가": 110.0, "거래량": 20}]

    def fake_rest_get(path):
        if path.startswith("price_history") and "2026-10-08" in path:
            return []                                   # 10/8은 통째로 비었음
        if path.startswith("price_history"):
            return [{"stock_code": w["stock_code"]} for w in watch]
        return [{"market": "KOSPI"}, {"market": "KOSDAQ"}]

    written = {}
    monkeypatch.setattr(core, "fetch_daily_price_history", fake_hist)
    monkeypatch.setattr(m, "rest_get", fake_rest_get)
    monkeypatch.setattr(m, "upsert", lambda t, recs, oc, chunk_size=None: written.setdefault(t, recs) and len(recs))
    monkeypatch.setattr(m.time, "sleep", lambda s: None)

    m.fill_recent_gaps(watch, "2026-10-10")

    rows = written["price_history"]
    assert {r["trade_date"] for r in rows} == {"2026-10-08"}
    assert {r["stock_code"] for r in rows} == {"000001", "000002", "000003"}
    assert rows[0]["close_price"] == 110.0 and rows[0]["change_pct"] == 10.0 and rows[0]["volume"] == 20
    assert "market_flow" not in written


def test_fill_recent_gaps_ignores_single_halted_stock(monkeypatch):
    m = _load(monkeypatch)
    watch = [{"stock_code": f"{i:06d}", "stock_name": str(i)} for i in range(20)]
    monkeypatch.setattr(core, "fetch_daily_price_history",
                        lambda code, s, e: [{"날짜": "2026-10-08", "종가": 1.0, "거래량": 1}])
    monkeypatch.setattr(m, "rest_get", lambda path: (
        [{"stock_code": w["stock_code"]} for w in watch[1:]] if path.startswith("price_history")
        else [{"market": "KOSPI"}, {"market": "KOSDAQ"}]))
    called = []
    monkeypatch.setattr(m, "upsert", lambda *a, **k: called.append(a) or 0)

    m.fill_recent_gaps(watch, "2026-10-10")

    assert called == []                                  # 19/20 = 95% → 거래정지 1종목은 안 건드림
