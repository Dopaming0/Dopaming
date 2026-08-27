"""소싱 레이더 CLI.

  python -m radar.cli scan  --keywords keywords.csv --out out
  python -m radar.cli diff  --out out            # 지난 실행과 비교해 변화만 보고
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from .config import Config
from .margin import UnreachableMargin, margin_at_price, reverse_price
from .metrics import competition_index, is_entry_window, trend_stats
from .naver import NaverClient
from .report import write_csv, write_html, write_json
from .score import score_item


def _int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def read_keywords(path: str | Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            kw = (row.get("keyword") or "").strip()
            if not kw or kw.startswith("#"):
                continue
            rows.append(
                {
                    "item": (row.get("item") or kw).strip(),
                    "keyword": kw,
                    "unit_cost": _int(row.get("unit_cost")),
                    "market_price_cap": _int(row.get("market_price_cap")),
                    "shoppable": _int(row.get("shoppable"), 10),
                    "logistics": _int(row.get("logistics"), 7),
                    "cert": _int(row.get("cert"), 7),
                    "expansion": _int(row.get("expansion"), 3),
                    "note": (row.get("notes") or "").strip(),
                }
            )
    return rows


def _trend_window() -> tuple[str, str]:
    end = date.today().replace(day=1) - timedelta(days=1)
    start = (end.replace(day=1) - timedelta(days=365 * 2)).replace(day=1)
    return start.isoformat(), end.isoformat()


def collect_trends(client: NaverClient, keywords: list[str]) -> dict[str, list[dict]]:
    """데이터랩은 한 번에 최대 5개 그룹."""
    start, end = _trend_window()
    out: dict[str, list[dict]] = {}
    for i in range(0, len(keywords), 5):
        chunk = keywords[i : i + 5]
        groups = [{"groupName": k, "keywords": [k]} for k in chunk]
        try:
            payload = client.search_trend(groups, start, end)
        except Exception as exc:  # 한 배치가 실패해도 전체를 멈추지 않는다
            print(f"  ! 트렌드 조회 실패 {chunk}: {exc}", file=sys.stderr)
            continue
        for result in payload.get("results", []):
            out[result.get("title", "")] = result.get("data", [])
    return out


def scan(args) -> int:
    cfg = Config.load(args.config)
    missing = []
    if not cfg.has_openapi:
        missing.append("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (상품 수 · 트렌드)")
    if not cfg.has_searchad:
        missing.append("NAVER_AD_API_KEY / NAVER_AD_SECRET_KEY / NAVER_AD_CUSTOMER_ID (월간 검색 수)")
    if missing:
        print("설정이 비어 있습니다:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        print("\nconfig.toml을 채우거나 환경변수로 넣으세요. README 참조.", file=sys.stderr)
        return 2

    items = read_keywords(args.keywords)
    if not items:
        print("키워드 파일이 비어 있습니다.", file=sys.stderr)
        return 2

    client = NaverClient(cfg)
    keywords = [i["keyword"] for i in items]

    print(f"[1/4] 상품 수 조회 ({len(keywords)}개)")
    products: dict[str, int] = {}
    for kw in keywords:
        try:
            products[kw] = client.product_count(kw)
        except Exception as exc:
            print(f"  ! {kw}: {exc}", file=sys.stderr)
            products[kw] = 0

    print("[2/4] 월간 검색 수 조회")
    try:
        volumes = client.search_volume(keywords)
    except Exception as exc:
        print(f"  ! 검색광고 API 실패: {exc}", file=sys.stderr)
        volumes = {}

    print("[3/4] 검색어 트렌드 조회")
    trends = collect_trends(client, keywords)

    print("[4/4] 채점")
    rows = []
    for it in items:
        kw = it["keyword"]
        norm = kw.replace(" ", "")
        vol = volumes.get(norm, volumes.get(kw, {})).get("total", 0)
        prod = products.get(kw, 0)
        comp = competition_index(prod, vol)
        stats = trend_stats(trends.get(kw, []))

        price = net_margin = profit = None
        if it["unit_cost"] > 0:
            try:
                mr = reverse_price(it["unit_cost"], cfg.margin)
                price, profit = mr.price, mr.profit_per_unit
                cap = it["market_price_cap"]
                if cap and mr.price > cap:
                    # 시장 상단을 넘으면, 상단 가격에서의 실제 마진으로 평가한다
                    net_margin = margin_at_price(cap, it["unit_cost"], cfg.margin)
                    it["note"] = (it["note"] + " / 역산가가 시장 상단 초과").strip(" /")
                else:
                    net_margin = mr.net_margin
            except UnreachableMargin as exc:
                it["note"] = (it["note"] + f" / {exc}").strip(" /")

        sc = score_item(
            comp, stats, net_margin, cfg.thresholds,
            cfg.margin.floor_margin, cfg.margin.target_margin,
            shoppable=it["shoppable"], logistics=it["logistics"],
            cert=it["cert"], expansion=it["expansion"],
        )
        window = is_entry_window(
            stats, prod, cfg.thresholds.surge_multiple,
            cfg.thresholds.surge_products_max, cfg.thresholds.sustained_yoy,
        )

        notes = " · ".join(sc.reasons)
        if it["note"]:
            notes = f"{it['note']} · {notes}"

        rows.append({
            "verdict": sc.verdict, "score": sc.total, "item": it["item"], "keyword": kw,
            "products": prod, "volume": vol,
            "competition": round(comp, 3) if comp is not None else None,
            "yoy": round(stats.yoy, 3) if stats.yoy is not None else None,
            "surge": round(stats.surge, 2) if stats.surge is not None else None,
            "entry_window": window, "price": price,
            "net_margin": round(net_margin, 3) if net_margin is not None else None,
            "profit_per_unit": profit, "peak_month": stats.peak_month,
            "amplitude": round(stats.amplitude, 1) if stats.amplitude is not None else None,
            "notes": notes, "parts": sc.parts,
        })

    rows.sort(key=lambda r: (r["verdict"] != "발주 검토", -r["score"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    out = Path(args.out)
    write_csv(rows, out / "radar.csv")
    write_json(rows, out / "radar.json")
    write_html(rows, out / "radar.html")

    ok = sum(1 for r in rows if r["verdict"] == "발주 검토")
    win = sum(1 for r in rows if r["entry_window"])
    print(f"\n완료 — 후보 {len(rows)}개 중 발주 검토 {ok}개, 진입 창 {win}개")
    print(f"  {out/'radar.html'}")
    for r in rows[:5]:
        comp = f"{r['competition']:.2f}" if r["competition"] is not None else "—"
        print(f"  {r['rank']:>2}. [{r['score']:>3}] {r['item']} (경쟁강도 {comp})")
    return 0


def diff(args) -> int:
    """지난 실행과 비교해 판정이 바뀐 것만 보고한다. 주간 자동 실행용."""
    out = Path(args.out)
    cur_path, prev_path = out / "radar.json", out / "radar.prev.json"
    if not cur_path.exists():
        print("radar.json이 없습니다. 먼저 scan을 실행하세요.", file=sys.stderr)
        return 2
    cur = {r["keyword"]: r for r in json.loads(cur_path.read_text(encoding="utf-8"))}
    if not prev_path.exists():
        print("이전 실행 기록이 없습니다. 이번 결과를 기준선으로 저장합니다.")
        prev_path.write_text(cur_path.read_text(encoding="utf-8"), encoding="utf-8")
        return 0
    prev = {r["keyword"]: r for r in json.loads(prev_path.read_text(encoding="utf-8"))}

    changes = []
    for kw, row in cur.items():
        old = prev.get(kw)
        if not old:
            changes.append(f"[신규] {row['item']} — {row['verdict']} {row['score']}점")
            continue
        if row["entry_window"] and not old.get("entry_window"):
            changes.append(f"[진입 창 열림] {row['item']} — 급등 {row.get('surge')}배, 상품 {row['products']:,}개")
        if row["verdict"] != old["verdict"]:
            changes.append(f"[판정 변경] {row['item']}: {old['verdict']} → {row['verdict']}")
        if old.get("competition") and row.get("competition"):
            drop = old["competition"] - row["competition"]
            if drop > 0.2:
                changes.append(f"[경쟁 완화] {row['item']}: {old['competition']:.2f} → {row['competition']:.2f}")

    if changes:
        print(f"변화 {len(changes)}건")
        for c in changes:
            print("  " + c)
    else:
        print("변화 없음")
    prev_path.write_text(cur_path.read_text(encoding="utf-8"), encoding="utf-8")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="radar", description="네이버 공식 API로 소싱 후보를 자동 채점한다")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="후보를 조회하고 채점한다")
    s.add_argument("--keywords", default="keywords.csv")
    s.add_argument("--out", default="out")
    s.add_argument("--config", default="config.toml")
    s.set_defaults(func=scan)

    d = sub.add_parser("diff", help="지난 실행과 비교해 변화만 보고한다")
    d.add_argument("--out", default="out")
    d.set_defaults(func=diff)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
