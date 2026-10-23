import morning_report as mr


def test_market_regime_follows_daepyo_lines():
    # 대표 10-23: +1%↑ 좋음(−3%도) · ±1% 보통(−5%) · −1~−2% 안 좋음(−1.5~−2% 빈칸 포함, 6-A) · −2%↓ 매우 안 좋음
    assert mr.market_regime(1.2)[:2] == ("좋음", -3.0)
    assert mr.market_regime(0.3)[:2] == ("보통", -5.0)
    assert mr.market_regime(-1.0)[:2] == ("안 좋음", -5.0)
    assert mr.market_regime(-1.7)[:2] == ("안 좋음", -5.0)
    assert mr.market_regime(-2.0)[:2] == ("매우 안 좋음", -5.0)
    assert mr.market_regime(None)[1] == -5.0


def test_attach_comments_swaps_only_comment_block(tmp_path, monkeypatch):
    # 대표 10-23 「총평이 달린 리포트를 한 번에」 — --comments는 총평 칸만 갈아끼우고 숫자·CSV는 그대로.
    import daily_link_report as link
    mo, lo = tmp_path / "morning_report", tmp_path / "link_sample"
    monkeypatch.setattr(mr, "OUT", mo)
    monkeypatch.setattr(link, "OUT", lo)
    d = "2026-10-26"
    page = '<td>-21.6%</td><div class="cm"><!--총평--><p class="note">아직 작성 전</p><!--/총평--></div><td>63,000</td>'
    files = {mo / "action" / f"{d}.html": mo / "latest_action.html",
             lo / "daily" / f"{d}.html": lo / "latest.html",
             mo / "cfg" / f"{d}.html": mo / "latest_cfg.html",
             mo / "basecamp" / f"{d}.html": mo / "latest_basecamp.html"}
    for dated, latest in files.items():
        dated.parent.mkdir(parents=True, exist_ok=True)
        dated.write_text(page, encoding="utf-8")
        latest.write_text(page, encoding="utf-8")
    csv = mo / "cfg_log.csv"
    csv.write_text("날짜,코드\n2026-10-26,000001\n", encoding="utf-8")
    (mo / "comments").mkdir()
    (mo / "comments" / f"{d}_action.html").write_text("<p>오늘 장 안 좋아</p>", encoding="utf-8")

    out = mr.attach_comments(d)

    act = (mo / "action" / f"{d}.html").read_text(encoding="utf-8")
    assert "<p>오늘 장 안 좋아</p>" in act and "아직 작성 전" not in act
    assert act.startswith("<td>-21.6%</td>") and act.endswith("<td>63,000</td>")      # 숫자 그대로
    assert (mo / "latest_action.html").read_text(encoding="utf-8") == act
    assert "아직 작성 전" in (mo / "cfg" / f"{d}.html").read_text(encoding="utf-8")     # 총평 없으면 그대로
    assert csv.read_text(encoding="utf-8") == "날짜,코드\n2026-10-26,000001\n"          # CSV 안 건드림
    assert len(out) == 4


def test_cfg_distance_per_sieve_and_max_ordering():
    # 대표 10-23 ⓐ: 체마다 문턱까지 거리(0~1), 줄 세우기는 가장 큰 거리 → 합. 0 이하로 늘었으면 거리 ≥ 1(안 보임).
    w_ok = {"시작값": 2.0, "d": 0.1, "d3": 0.4}            # 끝 값은 문턱 아래지만 끝 사흘 최고는 넘음 → 외인 거리 0
    assert mr.cfg_distance(w_ok, {"반납률": 0.1})["외인"] == 0
    w = {"시작값": 2.0, "d": 0.2, "d3": 0.2}
    d = mr.cfg_distance(w, {"반납률": 0.1})
    assert abs(d["외인"] - 1 / 3) < 1e-9 and d["max"] == d["외인"]
    a = mr.cfg_distance({"시작값": 2.0, "d": 0.27, "d3": 0.27}, {"반납률": 0.33, "착시": True})   # 0.1 · 0.1
    b = mr.cfg_distance({"시작값": 2.0, "d": 0.5, "d3": 0.5}, {"반납률": 0.36, "착시": True})     # 0 · 0.2
    assert a["max"] < b["max"]                                   # 체마다 아슬아슬한 A가 앞(87 안)
    assert mr.cfg_distance({"시작값": 2.0, "d": -0.1, "d3": 0.0}, {"반납률": 0})["max"] >= 1
    assert not mr.cfg_distance({"시작값": 2.0, "d": 0.0, "d3": 0.0}, {"반납률": 0})["max"] < 1   # 증가 0 → 거리 1 → 안 보임(build는 < 1)
    assert mr.cfg_distance({"시작값": 2.0, "d": 0.5, "d3": 0.5}, {"반납률": 0.30, "착시": True})["주원인"] == "착시"   # 딱 30%
    t = mr.cfg_distance({"시작값": 2.0, "d": 0.5, "d3": 0.5}, {"반납률": 0.27, "꺾임": True})
    assert abs(t["꺾임"] - 0.8) < 1e-9 and t["주원인"] == "꺾임"
    # 원래 비중 3% 이상이면 상대 기준(5%)과 짧은 쪽: 10% 비중에서 +0.45%p = +4.5% → 상대 거리 0.1 < 외인 거리 0 아님
    r = mr.cfg_distance({"시작값": 10.0, "d": 0.2, "d3": 0.45}, {"반납률": 0})
    assert r["외인"] == 0                                        # 0.45%p ≥ 0.3 → 0


def test_save_cfg_rank_keeps_other_days(tmp_path, monkeypatch):
    p = tmp_path / "cfg_rank.csv"
    monkeypatch.setattr(mr, "CFG_RANK_CSV", p)
    p.write_text("날짜,순위,종목코드\n2026-10-22,1,000001\n2026-10-26,1,999999\n", encoding="utf-8-sig")
    row = {"코드": "000002", "종목명": "가", "출처들": {"Fishing"}, "시작": "2026-08-19", "pct": -12.0,
           "fw": {"시작값": 2.0, "d": 0.5, "d3": 0.5}}
    near = {**row, "코드": "000003", "dist": mr.cfg_distance({"시작값": 2.0, "d": 0.2, "d3": 0.2}, {"반납률": 0})}
    mr.save_cfg_rank({"today": "2026-10-26", "cfg_rank": [row, near]}, {"000002": 100}, "2026-10-26 09:30")
    import pandas as pd
    df = pd.read_csv(p, dtype={"종목코드": str})
    assert list(df[df["날짜"] == "2026-10-22"]["종목코드"]) == ["000001"]          # 다른 날 그대로
    t = df[df["날짜"] == "2026-10-26"]
    assert list(t["종목코드"]) == ["000002", "000003"] and list(t["구분"]) == ["진짜", "가까운 것"]


def test_regen_note_marks_time_and_reason_in_subtitle(monkeypatch):
    # 대표 10-23 MD 보고 답 5-5 A — 사고로 숫자가 틀린 리포트는 고친 뒤 다시 만들고 "HH:MM 재생성(사유)"를 밝힌다.
    from datetime import datetime
    note = mr.regen_note("NXT 종가 섞임 <고침>", datetime(2026, 10, 26, 10, 12))
    assert "10:12 재생성(NXT 종가 섞임 &lt;고침&gt;)" in note and "숫자는 10:12 기준" in note
    monkeypatch.setattr(mr, "REGEN_NOTE", note)
    assert note in mr.page("T", "2026-10-26 · 주가 10:12 기준", "<p>x</p>")
    monkeypatch.setattr(mr, "REGEN_NOTE", "")
    assert "재생성" not in mr.page("T", "s", "<p>x</p>")                 # 평소엔 아무것도 안 붙음
