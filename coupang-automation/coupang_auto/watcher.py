"""도매처 상품페이지 가격·품절 감시.

- 페이지 수집: 기본 requests. 로그인해야 가격이 보이는 몰은 Playwright + 저장된 로그인 세션
  (coupang-auto watch-login <공급처키> 로 1회 로그인해 두면 storage_state 로 재사용).
- 파싱: og/product 메타태그, JSON-LD, 흔한 가격 셀렉터 순으로 시도. 몰마다 마크업이 다르면
  config.yaml 의 공급처별 watch.price_selector / soldout_selector 로 지정.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

DEFAULT_SOLDOUT_MARKERS = ["품절", "일시품절", "SOLD OUT", "SOLDOUT", "재고없음", "판매중지"]


@dataclass
class CheckResult:
    price: int | None = None
    soldout: bool | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and (self.price is not None or self.soldout is not None)


def _to_price(text: str) -> int | None:
    digits = "".join(c for c in str(text) if c.isdigit())
    if not digits:
        return None
    value = int(digits)
    return value if 100 <= value <= 10_000_000 else None  # 원 단위 상식 범위 밖은 오탐으로 간주


def parse_product_page(html: str, watch_conf: dict | None = None) -> CheckResult:
    watch_conf = watch_conf or {}
    soup = BeautifulSoup(html, "html.parser")
    result = CheckResult()

    # --- 가격 ---
    if sel := watch_conf.get("price_selector"):
        if el := soup.select_one(sel):
            result.price = _to_price(el.get_text())
    if result.price is None:
        for prop in ("product:price:amount", "og:price:amount", "product:sale_price:amount"):
            if meta := soup.find("meta", attrs={"property": prop}):
                result.price = _to_price(meta.get("content", ""))
                if result.price:
                    break
    if result.price is None:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            for node in (data if isinstance(data, list) else [data]):
                offers = node.get("offers") if isinstance(node, dict) else None
                if isinstance(offers, list):
                    offers = offers[0] if offers else None
                if isinstance(offers, dict) and offers.get("price"):
                    result.price = _to_price(str(offers["price"]))
                    break
            if result.price:
                break
    if result.price is None:
        # cafe24/고도몰류의 흔한 가격 요소
        for sel in ("#span_product_price_text", ".price", "[class*=price]", "strong.c-price"):
            if el := soup.select_one(sel):
                result.price = _to_price(el.get_text())
                if result.price:
                    break

    # --- 품절 ---
    if sel := watch_conf.get("soldout_selector"):
        result.soldout = soup.select_one(sel) is not None
    else:
        markers = watch_conf.get("soldout_text") or DEFAULT_SOLDOUT_MARKERS
        soldout = False
        # class 에 soldout 이 들어간 요소, 품절 이미지 alt, 버튼/강조 텍스트만 검사해 오탐을 줄인다
        if soup.select_one("[class*=soldout], [class*=sold_out], [class*=sold-out]"):
            soldout = True
        if not soldout:
            for img in soup.find_all("img", alt=True):
                if any(m in img["alt"] for m in markers):
                    soldout = True
                    break
        if not soldout:
            for el in soup.find_all(["button", "a", "em", "strong", "span"]):
                text = el.get_text(strip=True)
                if text and len(text) <= 20 and any(m in text for m in markers):
                    soldout = True
                    break
        result.soldout = soldout

    if result.price is None and result.soldout is not True:
        result.error = "가격을 찾지 못했습니다 (로그인 필요 몰이면 watch-login, 아니면 price_selector 지정)"
    return result


def fetch_html_requests(url: str, timeout: int = 20) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding
    return resp.text


def fetch_html_playwright(url: str, storage_state: Path) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("playwright 미설치 — pip install playwright 후 playwright install chromium") from e
    if not storage_state.exists():
        raise RuntimeError(f"로그인 세션이 없습니다 — coupang-auto watch-login 으로 먼저 로그인하세요 ({storage_state})")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(storage_state), user_agent=USER_AGENT)
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        html = page.content()
        browser.close()
    return html


def check_product(url: str, watch_conf: dict | None, storage_dir: Path, supplier_key: str) -> CheckResult:
    watch_conf = watch_conf or {}
    try:
        if watch_conf.get("fetch") == "playwright":
            html = fetch_html_playwright(url, storage_dir / f"{supplier_key}.json")
        else:
            html = fetch_html_requests(url)
    except Exception as e:
        return CheckResult(error=f"페이지 수집 실패: {e}")
    return parse_product_page(html, watch_conf)


def diff_state(prev: dict | None, base_price: int | None, result: CheckResult, name: str) -> list[str]:
    """이전 상태·시트 기준 공급가와 비교해 알림 메시지 목록을 만든다."""
    alerts = []
    if not result.ok:
        # 이전엔 정상 확인되던 상품이 갑자기 실패하면 알린다 (최초 실패는 조용히)
        if prev and not prev.get("error"):
            alerts.append(f"⚠️ {name}: 확인 실패 — {result.error}")
        return alerts

    prev_soldout = bool(prev["soldout"]) if prev and prev.get("soldout") is not None else None
    if result.soldout and prev_soldout is not True:
        alerts.append(f"🚫 {name}: 품절 — 쿠팡 재고 0 처리 필요")
    elif result.soldout is False and prev_soldout is True:
        alerts.append(f"✅ {name}: 품절 해제 — 판매 재개 가능")

    if result.price is not None:
        prev_price = prev.get("price") if prev else None
        if prev_price and result.price != prev_price:
            arrow = "📈 인상" if result.price > prev_price else "📉 인하"
            alerts.append(f"{arrow} {name}: 공급가 {prev_price:,} → {result.price:,}원")
        if base_price and result.price > base_price:
            alerts.append(
                f"💸 {name}: 현재 공급가 {result.price:,}원이 시트 기준 {base_price:,}원보다 높음 — 마진 재계산 필요"
            )
    return alerts


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def soldout_label(result: CheckResult) -> str:
    if not result.ok:
        return "확인실패"
    if result.soldout:
        return "품절"
    return "정상"


def run_watch(config, db, bot=None) -> str:
    """시트의 감시 대상 전체를 확인하고 시트 기록 + 변경 알림. 요약 문자열 반환."""
    from .sheet import SheetClient

    gs = config.google_sheet
    sheet = SheetClient(gs["spreadsheet_id"], gs["worksheet"], str(config._resolve(gs["service_account_json"])))
    items = sheet.load_watch_items()
    sheet.ensure_result_columns()

    storage_dir = config._resolve("data/storage")
    all_alerts: list[str] = []
    writes = []
    checked = 0

    for item in items:
        supplier_key = _supplier_key_for(config, item.supplier)
        watch_conf = (config.suppliers.get(supplier_key).watch if supplier_key in config.suppliers else {}) or {}
        result = check_product(item.url, watch_conf, storage_dir, supplier_key or "unknown")
        prev = db.get_watch_state(item.url)
        all_alerts.extend(diff_state(prev, item.base_price, result, item.name))
        db.set_watch_state(item.url, result.price, result.soldout, result.error)
        row_data = {"품절여부": soldout_label(result), "확인시각": now_str()}
        if result.price is not None:
            row_data["현재공급가"] = f"{result.price:,}"
        writes.append((item.row, row_data))
        checked += 1

    sheet.write_results(writes)

    summary = f"🔎 도매처 확인 완료 — {checked}개 상품, 변경 {len(all_alerts)}건"
    if all_alerts:
        summary += "\n" + "\n".join(all_alerts)
    db.log("watch_run", summary)
    if bot and all_alerts:
        bot.send(summary)
    else:
        log.info(summary)
    return summary


def _supplier_key_for(config, supplier_name: str) -> str | None:
    """시트의 공급처 표기(한글)를 config 의 공급처 키로 변환."""
    for key, sconf in config.suppliers.items():
        if sconf.name and sconf.name in supplier_name:
            return key
    return None
