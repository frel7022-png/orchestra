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
