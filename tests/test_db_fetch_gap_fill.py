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


def test_krx_close_window_and_prev_fix(monkeypatch):
    # 2026-10-23 대표 A: 종가는 KRX 15:30 종가 — 15:30~15:40 창, 다음 날 장중 "전일"로 다른 줄만 고침.
    from datetime import datetime
    m = _load(monkeypatch)
    assert m.in_krx_close_window(datetime(2026, 10, 26, 15, 35))
    assert not m.in_krx_close_window(datetime(2026, 10, 26, 15, 40))    # NXT 애프터 시작
    assert not m.in_krx_close_window(datetime(2026, 10, 26, 15, 29))
    rows = [{"stock_code": "031430", "close_price": 10580.0},          # NXT 끝값 → 고침
            {"stock_code": "005930", "close_price": 214500.0}]         # KRX와 같음 → 그대로
    recs = m.build_prev_fix_records(rows, {"031430": 11200.0, "005930": 214500.0}, {"031430": 11280.0}, "2026-10-22")
    assert len(recs) == 1 and recs[0]["close_price"] == 11200.0
    assert recs[0]["change_pct"] == round((11200 / 11280 - 1) * 100, 2) and "volume" not in recs[0]


def test_cron_price_history_keeps_existing_rows(monkeypatch):
    # 저녁 cron(실제 21~22시)이 15:35 KRX 종가 줄을 NXT 끝값으로 덮지 않게 — ignore-duplicates 헤더로 보낸다.
    m = _load(monkeypatch)
    sent = {}

    class R:
        ok = True

        def json(self):
            return [{}]

    def fake_post(url, headers=None, json=None, timeout=None):
        sent["prefer"] = headers["Prefer"]
        return R()
    monkeypatch.setattr(m.requests, "post", fake_post)
    m.upsert("price_history", [{"stock_code": "1"}], "stock_code,trade_date", keep_existing=True)
    assert "ignore-duplicates" in sent["prefer"]
    m.upsert("price_history", [{"stock_code": "1"}], "stock_code,trade_date")
    assert "merge-duplicates" in sent["prefer"]


def test_fix_prev_close_touches_only_previous_trading_day(monkeypatch):
    # 대표 10-23 「지난 기록은 픽스」 — 안전망은 오늘 바로 앞 거래일 줄만, 그중 KRX와 다른 것만 고친다.
    from datetime import datetime
    m = _load(monkeypatch)
    monkeypatch.setattr(m.core, "now_kst", lambda: datetime(2026, 10, 26, 9, 25))
    monkeypatch.setattr(m.core, "today_kst_str", lambda: "2026-10-26")
    monkeypatch.setattr(m.core, "fetch_quotes",
                        lambda codes: ({"031430": {"price": 1, "prev_close": 11000.0}}, []))
    asked = []

    def fake_get(path):
        asked.append(path)
        if "trade_date=eq.2026-10-23" in path:
            return [{"stock_code": "031430", "close_price": 10900.0}]
        if "trade_date=eq.2026-10-22" in path:
            return [{"stock_code": "031430", "close_price": 11200.0}]
        return [{"stock_code": "031430"}]
    sent = []
    monkeypatch.setattr(m, "rest_get", fake_get)
    monkeypatch.setattr(m, "upsert", lambda t, recs, oc, **kw: sent.extend(recs) or len(recs))
    m.fix_prev_close()
    assert [r["trade_date"] for r in sent] == ["2026-10-23"]        # 10-26 월요일 → 금요일 10-23 줄만
    assert sent[0]["close_price"] == 11000.0
    # 장 밖 시각엔 아무것도 안 씀
    sent.clear()
    monkeypatch.setattr(m.core, "now_kst", lambda: datetime(2026, 10, 26, 16, 0))
    m.fix_prev_close()
    assert sent == []
