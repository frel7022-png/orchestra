"""눈 한 장 — 종목 하나를 눈 셋(분모·바다·기간)으로 다시 볼 사실만 한 장에 펼친다 (2026-10-20 대표 "응 좋아", 위크 안).

    python eye_card.py 종목명

판정·점수는 찍지 않는다(MD 규율 §18-28) — 사실만 길이별로 나란히. 해석은 뇌_초안/바라보는_눈/의 질문으로 사람이(세션이) 한다.
- 기간(판정은 기간에 의해 변화한다): 최근 10·30·60거래일(`WINDOWS`) + DB 시작부터 전체, 같은 칸을 나란히(자료가 짧으면 그 길이는 빠진다).
- 바다(바다를 보고 상대적으로 평가한다): 같은 기간 코스피·혼합지수(ex)·시장 외인 순매수·관심종목 외인 가운데 값·관심종목 주가 가운데 값.
- 분모(분모를 바꿔 본다): 그 종목 외인 보유율 시작→지금, 변화 %p와 변화율(%) 둘 다.
재사용: `sea.load_data`·`sea.index_moves`·`sea.market_foreign`·`sea.relative_foreign`, `live_check.live_snapshot`. 새 계산을 만들지 않는다.
"""
import sys

import pandas as pd

import live_check
import sea

WINDOWS = (10, 30, 60)


def card(name: str, data: dict | None = None, snap: dict | None = None) -> dict:
    data = data or sea.load_data()
    snap = snap or live_check.live_snapshot()
    ph = data["ph"]
    g = ph[ph["종목명"] == name].sort_values("날짜")
    if g.empty:
        return {"종목명": name, "없음": True}
    code = str(g["종목코드"].iloc[0])
    dates = list(g["날짜"])
    now_px = data["live"].get(code) or float(g["종가"].iloc[-1])
    end = max(data["fh"]["날짜"])
    rows = []
    spans = [(f"{n}일", dates[-1 - n]) for n in WINDOWS if len(dates) > n]
    spans.append((f"전체({len(dates) - 1}일)", dates[0]))   # 넓게 — DB 시작부터(60일이 안 차면 이게 가장 긴 길이)
    for label, start in spans:
        p0 = float(g[g["날짜"] == start]["종가"].iloc[0])
        rel, info = sea.relative_foreign(data["fh"], start, ph, data["live"])
        me = rel[rel["종목코드"] == code]
        idx = sea.index_moves(snap["idx"], snap["bigdf"], snap["wk"], start)
        mkt = sea.market_foreign(data["mf"], start, end)
        r = {"기간": label, "시작": start, "주가%": (now_px / p0 - 1) * 100,
             "코스피%": idx.get("코스피"), "혼합ex%": idx.get("혼합(ex)"),
             "시장외인억": {k: v for k, v in mkt.items()},
             "관심외인중앙pp": info.get("외인중앙"), "관심주가중앙%": info.get("주가중앙")}
        if len(me):
            m = me.iloc[0]
            r.update({"외인시작": m["외인시작"], "외인지금": m["외인지금"], "외인pp": m["dF"],
                      "외인변화율%": m.get("외인변화율%"), "상대외인pp": m["상대외인"]})
        rows.append(r)
    return {"종목명": name, "종목코드": code, "지금가": now_px, "외인기준일": end, "rows": rows,
            "asof": snap.get("asof"), "basis": snap.get("basis")}


def _f(v, d=1, unit="%"):
    return "-" if v is None or (isinstance(v, float) and v != v) else f"{v:+.{d}f}{unit}"


def report(c: dict) -> None:
    if c.get("없음"):
        print(f"{c['종목명']}: 관심종목 DB에 없음")
        return
    print(f"[눈 한 장] {c['종목명']}({c['종목코드']}) · 지금 {c['지금가']:,.0f} · {c.get('basis') or ''} {c.get('asof') or ''} · 외인 {c['외인기준일']}까지")
    print("판정 아님 — 사실만. 질문은 바라보는 눈: 이때 시장은? 외인은? 분모·기간을 바꾸면?")
    for r in c["rows"]:
        if r.get("없음"):
            print(f"\n[{r['기간']}] {r['없음']}")
            continue
        mk = " · ".join(f"{k} {v:+,.0f}억" for k, v in sorted(r["시장외인억"].items()))
        print(f"\n[{r['기간']}] {r['시작']}~")
        print(f"  기간  주가 {_f(r['주가%'])}")
        print(f"  바다  코스피 {_f(r['코스피%'])} · 혼합(ex) {_f(r['혼합ex%'])} · 시장 외인 {mk} · 관심종목 외인 가운데 {_f(r['관심외인중앙pp'], 2, '%p')} · 주가 가운데 {_f(r['관심주가중앙%'])}")
        if "외인pp" in r:
            print(f"  분모  외인 보유율 {r['외인시작']:.2f}% → {r['외인지금']:.2f}% · {_f(r['외인pp'], 2, '%p')} (변화율 {_f(r['외인변화율%'], 0)}) · 관심종목 가운데 대비 {_f(r['상대외인pp'], 2, '%p')}")
        else:
            print("  분모  외인 자료 없음")


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    report(card(sys.argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
