"""단위 + 통합 테스트. 목 서버를 띄워 CLI를 실제로 한 바퀴 돌린다."""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar.cli import main  # noqa: E402
from radar.config import MarginPolicy, Thresholds  # noqa: E402
from radar.margin import UnreachableMargin, margin_at_price, reverse_price  # noqa: E402
from radar.metrics import competition_index, is_entry_window, trend_stats  # noqa: E402
from radar.naver import _as_int  # noqa: E402
from radar.score import score_item  # noqa: E402
from mock_server import serve  # noqa: E402

FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = ""):
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label} {detail}")
        FAILURES.append(label)


def test_margin():
    print("\n[마진 역산]")
    p = MarginPolicy(commission_rate=0.108, payment_rate=0.03, ad_rate=0.05,
                     return_rate=0.05, vat_rate=0.10, shipping_cost=3000,
                     fulfillment_cost=0, target_margin=0.35)
    r = reverse_price(12000, p)
    # 고정비 15,000 / (1 - 0.338 - 0.35) = 15000 / 0.312
    check("판매가 역산", r.price == round(15000 / 0.312), f"got {r.price}")
    check("역산가에서 목표 마진 복원", abs(r.net_margin - 0.35) < 1e-6, f"got {r.net_margin}")
    check("손익분기가 < 판매가", r.breakeven_price < r.price)
    check("건당 이익 양수", r.profit_per_unit > 0)

    low = margin_at_price(r.breakeven_price, 12000, p)
    check("손익분기에서 마진 0", abs(low) < 0.005, f"got {low}")

    cheap = margin_at_price(20000, 12000, p)
    check("낮은 가격이면 마진 하락", cheap < 0.35, f"got {cheap}")

    try:
        reverse_price(10000, MarginPolicy(target_margin=0.80))
        check("도달 불가 마진에서 예외", False)
    except UnreachableMargin:
        check("도달 불가 마진에서 예외", True)


def test_metrics():
    print("\n[지표]")
    check("경쟁강도 계산", competition_index(1500, 5000) == 0.3)
    check("검색량 0이면 None", competition_index(100, 0) is None)

    flat = [{"period": f"2024-{m:02d}-01", "ratio": 50} for m in range(1, 13)]
    flat += [{"period": f"2025-{m:02d}-01", "ratio": 50} for m in range(1, 13)]
    flat += [{"period": "2026-01-01", "ratio": 20}]  # 부분 집계 월
    s = trend_stats(flat)
    check("부분 집계 월 제외", s.points == 24, f"points={s.points}")
    check("평탄하면 YoY 0", abs(s.yoy) < 1e-9, f"yoy={s.yoy}")

    # 유행성 급등: 21개월 평탄 후 3개월 수직 상승
    spike = [{"period": f"2024-{m:02d}-01", "ratio": 10} for m in range(1, 13)]
    spike += [{"period": f"2025-{m:02d}-01", "ratio": 10} for m in range(1, 10)]
    spike += [{"period": f"2025-{m:02d}-01", "ratio": r} for m, r in ((10, 40), (11, 55), (12, 70))]
    spike += [{"period": "2026-01-01", "ratio": 30}]
    s2 = trend_stats(spike)
    check("급등 배수 산출", s2.surge is not None and s2.surge > 3.0, f"surge={s2.surge}")
    check("공급 비면 진입 창", is_entry_window(s2, 400, 3.0, 500))
    check("공급 많으면 진입 창 아님", not is_entry_window(s2, 5000, 3.0, 500))

    # 지속 성장: 배수는 눌리지만 전년비가 크다 (피클볼형)
    climb = [{"period": f"2024-{m:02d}-01", "ratio": 20 + m * 1.5} for m in range(1, 13)]
    climb += [{"period": f"2025-{m:02d}-01", "ratio": 38 + m * 3.0} for m in range(1, 13)]
    climb += [{"period": "2026-01-01", "ratio": 30}]
    s3 = trend_stats(climb)
    check("지속 성장은 배수가 눌린다", s3.surge is not None and s3.surge < 3.0, f"surge={s3.surge}")
    check("지속 성장도 진입 창으로 잡힘", is_entry_window(s3, 400, 3.0, 500, 0.50),
          f"yoy={s3.yoy}")
    check("지속 성장도 공급 많으면 제외", not is_entry_window(s3, 5000, 3.0, 500, 0.50))

    check("'< 10' 파싱", _as_int("< 10") == 5)
    check("콤마 숫자 파싱", _as_int("12,345") == 12345)
    check("None 파싱", _as_int(None) == 0)


def test_score():
    print("\n[채점]")
    th, floor, target = Thresholds(), 0.25, 0.35
    good = trend_stats([{"period": f"2024-{m:02d}-01", "ratio": 30} for m in range(1, 13)]
                       + [{"period": f"2025-{m:02d}-01", "ratio": 40} for m in range(1, 13)]
                       + [{"period": "2026-01-01", "ratio": 15}])

    hi = score_item(0.15, good, 0.40, th, floor, target, 10, 10, 10, 5)
    check("좋은 후보는 발주 검토", hi.verdict == "발주 검토", f"{hi.verdict} {hi.total}")
    check("좋은 후보 75점 이상", hi.total >= 75, f"total={hi.total}")

    over = score_item(2.5, good, 0.40, th, floor, target, 10, 10, 10, 5)
    check("경쟁강도 초과는 하드 게이트 탈락", over.verdict == "탈락", f"{over.verdict}")

    thin = score_item(0.15, good, 0.10, th, floor, target, 10, 10, 10, 5)
    check("마진 하한 미달은 탈락", thin.verdict == "탈락", f"{thin.verdict}")

    check("배점 합 100", sum([25, 15, 15, 20, 10, 10, 5]) == 100)
    check("만점 구성은 100점", score_item(0.1, good, 0.5, th, floor, target, 10, 10, 10, 5).total == 100,
          f"total={score_item(0.1, good, 0.5, th, floor, target, 10, 10, 10, 5).total}")


def test_end_to_end():
    print("\n[통합 — 목 서버로 CLI 실행]")
    srv, base = serve()
    try:
        tmp = Path(tempfile.mkdtemp())
        kw = tmp / "keywords.csv"
        kw.write_text(
            "item,keyword,unit_cost,market_price_cap,shoppable,logistics,cert,expansion,notes\n"
            "피클볼 라켓세트,피클볼라켓,18000,80000,10,9,10,5,급등 후보\n"
            "무타공 욕실선반,무타공선반,6000,30000,10,10,10,5,성장\n"
            "경추베개,경추베개,12000,50000,10,9,10,5,성장\n"
            "욕실 물막이,욕실물막이,4000,22000,10,10,10,3,보합\n"
            "반려견 유모차,강아지유모차,55000,190000,10,3,8,3,공급과잉\n"
            "테니스 라켓,테니스라켓,3000,15000,10,10,10,4,공급과잉\n",
            encoding="utf-8",
        )
        os.environ.update({
            "NAVER_CLIENT_ID": "mock-id", "NAVER_CLIENT_SECRET": "mock-secret",
            "NAVER_AD_API_KEY": "mock-key", "NAVER_AD_SECRET_KEY": "mock-ad-secret",
            "NAVER_AD_CUSTOMER_ID": "1234567",
            "NAVER_OPENAPI_BASE": base, "NAVER_SEARCHAD_BASE": base,
        })
        out = tmp / "out"
        rc = main(["scan", "--keywords", str(kw), "--out", str(out), "--config", "nonexistent.toml"])
        check("scan 종료코드 0", rc == 0, f"rc={rc}")

        for name in ("radar.csv", "radar.json", "radar.html"):
            check(f"{name} 생성", (out / name).exists())

        rows = json.loads((out / "radar.json").read_text(encoding="utf-8"))
        by_kw = {r["keyword"]: r for r in rows}
        check("6개 전부 채점", len(rows) == 6, f"got {len(rows)}")

        pb = by_kw["피클볼라켓"]
        check("피클볼 경쟁강도 정확", abs(pb["competition"] - 380 / 8200) < 1e-3, f"{pb['competition']}")
        check("피클볼 진입 창 감지", pb["entry_window"] is True, f"surge={pb['surge']}")
        check("피클볼 발주 검토", pb["verdict"] == "발주 검토", f"{pb['verdict']} {pb['score']}")

        mt = by_kw["무타공선반"]
        check("무타공선반 경쟁강도 정확", abs(mt["competition"] - 1600 / 12000) < 1e-3)
        check("무타공선반 통과", mt["verdict"] == "발주 검토", f"{mt['verdict']}")

        yu = by_kw["강아지유모차"]
        check("유모차 공급과잉 탈락", yu["verdict"] == "탈락", f"{yu['verdict']} comp={yu['competition']}")
        check("유모차 경쟁강도 > 1", yu["competition"] > 1.0)

        check("순위는 발주 검토 우선", rows[0]["verdict"] == "발주 검토")
        ranks = [r["rank"] for r in rows]
        check("순위 1..N 연속", ranks == list(range(1, len(rows) + 1)))

        with (out / "radar.csv").open(encoding="utf-8-sig") as f:
            csv_rows = list(csv.DictReader(f))
        check("CSV 행 수 일치", len(csv_rows) == len(rows))

        html = (out / "radar.html").read_text(encoding="utf-8")
        check("HTML에 아이템명 포함", "피클볼 라켓세트" in html)
        check("HTML 다크모드 토큰", "prefers-color-scheme" in html)
        check("HTML 진입 창 배지", "진입 창" in html)

        rc2 = main(["diff", "--out", str(out)])
        check("최초 diff는 기준선 저장", rc2 == 0 and (out / "radar.prev.json").exists())
        rc3 = main(["diff", "--out", str(out)])
        check("두 번째 diff 정상 종료", rc3 == 0)
    finally:
        srv.shutdown()


def test_missing_keys():
    print("\n[설정 누락 처리]")
    for k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "NAVER_AD_API_KEY",
              "NAVER_AD_SECRET_KEY", "NAVER_AD_CUSTOMER_ID"):
        os.environ.pop(k, None)
    tmp = Path(tempfile.mkdtemp())
    (tmp / "k.csv").write_text("item,keyword\na,b\n", encoding="utf-8")
    rc = main(["scan", "--keywords", str(tmp / "k.csv"), "--out", str(tmp / "o"),
               "--config", "nonexistent.toml"])
    check("키 없으면 종료코드 2", rc == 2, f"rc={rc}")


if __name__ == "__main__":
    test_margin()
    test_metrics()
    test_score()
    test_end_to_end()
    test_missing_keys()
    print("\n" + "=" * 52)
    if FAILURES:
        print(f"실패 {len(FAILURES)}건: " + ", ".join(FAILURES))
        raise SystemExit(1)
    print("전체 통과")
