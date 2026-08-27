"""결과 출력 — CSV, JSON, 그리고 읽히는 HTML 리포트."""
from __future__ import annotations

import csv
import html
import json
from datetime import datetime
from pathlib import Path

CSV_FIELDS = [
    "rank", "verdict", "score", "category", "item", "keyword",
    "products", "volume", "competition", "yoy", "surge",
    "entry_window", "price", "net_margin", "profit_per_unit",
    "peak_month", "amplitude", "notes",
]


def write_csv(rows: list[dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_json(rows: list[dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _fmt(value, kind: str = "") -> str:
    if value is None or value == "":
        return "—"
    if kind == "pct":
        return f"{value:+.0%}"
    if kind == "num2":
        return f"{value:.2f}"
    if kind == "won":
        return f"{int(value):,}"
    if kind == "int":
        return f"{int(value):,}"
    return html.escape(str(value))


_CSS = """
:root{--paper:#F4F3EF;--card:#fff;--alt:#FAFAF7;--ink:#14181A;--ink2:#333B3C;--muted:#5C6560;
--rule:#DEDDD6;--strong:#C5C6BE;--accent:#0E6E6B;--accent-ink:#0A4F4D;--accent-soft:#E1EDEB;
--good:#2E6B45;--good-soft:#E2EEE6;--warn:#8F6310;--warn-soft:#F3EBD8;--crit:#9B3340;--crit-soft:#F4E4E5;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#0D1113;--card:#161B1D;--alt:#12171A;
--ink:#E5E7E3;--ink2:#C3C9C5;--muted:#94A09B;--rule:#262E30;--strong:#38423F;--accent:#57B9B3;
--accent-ink:#8ED6D0;--accent-soft:#12302E;--good:#78C193;--good-soft:#14301F;--warn:#D8AC55;
--warn-soft:#302614;--crit:#E28A94;--crit-soft:#331A1D;}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:"IBM Plex Sans KR","Apple SD Gothic Neo",system-ui,sans-serif;line-height:1.7;word-break:keep-all}
.wrap{max-width:1180px;margin:0 auto;padding:44px 20px 80px}
h1{font-family:Georgia,"Nanum Myeongjo",serif;font-size:clamp(24px,4vw,36px);margin:0 0 10px;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:14px;margin-bottom:28px;font-variant-numeric:tabular-nums}
.tw{overflow-x:auto;border:1px solid var(--rule);border-radius:4px;background:var(--card)}
table{border-collapse:collapse;width:100%;min-width:1040px;font-size:13.5px}
th{background:var(--alt);text-align:left;padding:10px 12px;border-bottom:1px solid var(--strong);
font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);white-space:nowrap}
td{padding:10px 12px;border-bottom:1px solid var(--rule);vertical-align:top}
tr:last-child td{border-bottom:0}
tr:hover td{background:var(--alt)}
.num{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-variant-numeric:tabular-nums;white-space:nowrap}
.item{font-weight:600}
.kw{font-family:ui-monospace,monospace;font-size:12px;background:var(--accent-soft);color:var(--accent-ink);padding:2px 6px;border-radius:3px}
.chip{display:inline-block;font-family:ui-monospace,monospace;font-size:10.5px;font-weight:600;
padding:3px 7px;border-radius:3px;border:1px solid transparent;white-space:nowrap}
.ok{background:var(--good-soft);color:var(--good);border-color:var(--good)}
.hold{background:var(--warn-soft);color:var(--warn);border-color:var(--warn)}
.no{background:var(--crit-soft);color:var(--crit);border-color:var(--crit)}
.win{background:var(--accent-soft);color:var(--accent-ink);border-color:var(--accent)}
.notes{color:var(--ink2);font-size:12.5px;max-width:340px}
footer{margin-top:30px;color:var(--muted);font-size:12.5px}
"""


def _verdict_chip(v: str) -> str:
    cls = {"발주 검토": "ok", "보류": "hold", "탈락": "no"}.get(v, "hold")
    return f'<span class="chip {cls}">{html.escape(v)}</span>'


def write_html(rows: list[dict], path: str | Path, title: str = "소싱 레이더") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    passed = sum(1 for r in rows if r.get("verdict") == "발주 검토")
    windows = sum(1 for r in rows if r.get("entry_window"))

    body = []
    for r in rows:
        window = '<span class="chip win">진입 창</span>' if r.get("entry_window") else ""
        body.append(
            "<tr>"
            f'<td class="num">{_fmt(r.get("rank"))}</td>'
            f'<td>{_verdict_chip(r.get("verdict", ""))} {window}</td>'
            f'<td class="num"><b>{_fmt(r.get("score"))}</b></td>'
            f'<td class="num">{_fmt(r.get("category"))}</td>'
            f'<td class="item">{_fmt(r.get("item"))}</td>'
            f'<td><span class="kw">{_fmt(r.get("keyword"))}</span></td>'
            f'<td class="num">{_fmt(r.get("products"), "int")}</td>'
            f'<td class="num">{_fmt(r.get("volume"), "int")}</td>'
            f'<td class="num">{_fmt(r.get("competition"), "num2")}</td>'
            f'<td class="num">{_fmt(r.get("yoy"), "pct")}</td>'
            f'<td class="num">{_fmt(r.get("price"), "won")}</td>'
            f'<td class="num">{_fmt(r.get("net_margin"), "pct")}</td>'
            f'<td class="notes">{_fmt(r.get("notes"))}</td>'
            "</tr>"
        )

    head = (
        "<tr>"
        + "".join(
            f"<th>{h}</th>"
            for h in ["#", "판정", "점수", "분류", "아이템", "키워드", "상품수", "월검색수",
                      "경쟁강도", "전년비", "권장가", "순마진", "근거"]
        )
        + "</tr>"
    )

    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{_CSS}</style></head><body><div class="wrap">
<h1>{html.escape(title)}</h1>
<p class="sub">{stamp} 기준 · 후보 {len(rows)}개 · 발주 검토 {passed}개 · 진입 창 {windows}개</p>
<div class="tw"><table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table></div>
<footer>경쟁강도 = 상품 수 ÷ 월간 총검색 수. 0.3 이하 우수 / 0.7 초과 탈락.
전년비는 −15%까지 보합으로 읽습니다(네이버 전체 검색량 감소 보정).
권장가는 목표 마진을 만족하는 역산 판매가이며, 시장 상단을 넘으면 그 아이템은 탈락입니다.</footer>
</div></body></html>"""
    path.write_text(doc, encoding="utf-8")
