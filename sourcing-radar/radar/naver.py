"""네이버 공식 API 클라이언트 — 상품 수, 월간 검색 수, 검색어 트렌드."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from typing import Iterable

import requests

from .config import Config

_UNDER_TEN = re.compile(r"^\s*<\s*10\s*$")


def _as_int(value) -> int:
    """검색광고 API는 소량 키워드를 '< 10' 문자열로 준다."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if _UNDER_TEN.match(text):
        return 5  # '< 10'의 중앙값으로 둔다
    try:
        return int(text.replace(",", ""))
    except ValueError:
        return 0


class NaverClient:
    def __init__(self, cfg: Config, session: requests.Session | None = None, timeout: int = 15):
        self.cfg = cfg
        self.timeout = timeout
        self.session = session or requests.Session()

    # ── 상품 수 (공급) ────────────────────────────────────────────
    def product_count(self, keyword: str) -> int:
        """네이버쇼핑 검색 결과 총 건수. 경쟁강도의 분자."""
        r = self.session.get(
            f"{self.cfg.openapi_base}/v1/search/shop.json",
            params={"query": keyword, "display": 1},
            headers={
                "X-Naver-Client-Id": self.cfg.client_id,
                "X-Naver-Client-Secret": self.cfg.client_secret,
            },
            timeout=self.timeout,
        )
        r.raise_for_status()
        return int(r.json().get("total", 0))

    # ── 월간 검색 수 (수요) ───────────────────────────────────────
    def _searchad_headers(self, method: str, uri: str) -> dict:
        timestamp = str(round(time.time() * 1000))
        message = f"{timestamp}.{method.upper()}.{uri}"
        signature = base64.b64encode(
            hmac.new(
                self.cfg.ad_secret_key.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        return {
            "X-Timestamp": timestamp,
            "X-API-KEY": self.cfg.ad_api_key,
            "X-Customer": str(self.cfg.ad_customer_id),
            "X-Signature": signature,
        }

    def search_volume(self, keywords: Iterable[str]) -> dict[str, dict]:
        """검색광고 키워드도구. 한 번에 최대 5개, 공백 제거가 필수."""
        wanted = [k.replace(" ", "") for k in keywords]
        out: dict[str, dict] = {}
        for i in range(0, len(wanted), 5):
            chunk = wanted[i : i + 5]
            uri = "/keywordstool"
            r = self.session.get(
                f"{self.cfg.searchad_base}{uri}",
                params={"hintKeywords": ",".join(chunk), "showDetail": 1},
                headers=self._searchad_headers("GET", uri),
                timeout=self.timeout,
            )
            r.raise_for_status()
            for row in r.json().get("keywordList", []):
                pc = _as_int(row.get("monthlyPcQcCnt"))
                mo = _as_int(row.get("monthlyMobileQcCnt"))
                out[row.get("relKeyword", "")] = {
                    "pc": pc,
                    "mobile": mo,
                    "total": pc + mo,
                    "comp_idx": row.get("compIdx", ""),
                }
            time.sleep(0.35)  # 초당 호출 제한 회피
        return out

    # ── 검색어 트렌드 ─────────────────────────────────────────────
    def search_trend(
        self, groups: list[dict], start: str, end: str, time_unit: str = "month"
    ) -> dict:
        r = self.session.post(
            f"{self.cfg.openapi_base}/v1/datalab/search",
            json={
                "startDate": start,
                "endDate": end,
                "timeUnit": time_unit,
                "keywordGroups": groups,
            },
            headers={
                "X-Naver-Client-Id": self.cfg.client_id,
                "X-Naver-Client-Secret": self.cfg.client_secret,
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()
