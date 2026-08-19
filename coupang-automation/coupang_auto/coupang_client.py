"""쿠팡 마켓플레이스 오픈API 클라이언트 (HMAC-SHA256 서명).

엔드포인트 경로·파라미터는 쿠팡 개발자센터(https://developers.coupangcorp.com) 문서 기준이며,
정책 변경 시 이 파일의 상수만 고치면 되도록 한곳에 모아 두었다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from datetime import datetime, timezone

import requests

BASE_URL = "https://api-gateway.coupang.com"

# 발주서(주문) 상태값
STATUS_ACCEPT = "ACCEPT"        # 결제완료 (발주 전 — 고객 취소가 자유로운 구간)
STATUS_INSTRUCT = "INSTRUCT"    # 상품준비중


class CoupangApiError(RuntimeError):
    def __init__(self, status_code: int, body: str):
        super().__init__(f"Coupang API error {status_code}: {body[:500]}")
        self.status_code = status_code
        self.body = body


class CoupangClient:
    def __init__(self, vendor_id: str, access_key: str, secret_key: str,
                 session: requests.Session | None = None, timeout: int = 30):
        self.vendor_id = vendor_id
        self.access_key = access_key
        self.secret_key = secret_key
        self.session = session or requests.Session()
        self.timeout = timeout

    # ---- auth ----
    def _authorization(self, method: str, path: str, query: str, signed_date: str | None = None) -> str:
        signed_date = signed_date or datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
        message = signed_date + method + path + query
        signature = hmac.new(
            self.secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return (
            f"CEA algorithm=HmacSHA256, access-key={self.access_key}, "
            f"signed-date={signed_date}, signature={signature}"
        )

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        query = urllib.parse.urlencode(params or {}, doseq=True)
        headers = {
            "Authorization": self._authorization(method, path, query),
            "Content-Type": "application/json;charset=UTF-8",
        }
        url = BASE_URL + path + (f"?{query}" if query else "")
        for attempt in range(3):
            resp = self.session.request(
                method, url, headers=headers, timeout=self.timeout,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
            )
            if resp.status_code == 429:  # rate limit — back off and retry
                time.sleep(2 ** attempt)
                headers["Authorization"] = self._authorization(method, path, query)
                continue
            break
        if resp.status_code >= 400:
            raise CoupangApiError(resp.status_code, resp.text)
        return resp.json() if resp.text else {}

    # ---- orders ----
    def fetch_ordersheets(self, created_from: str, created_to: str,
                          status: str = STATUS_ACCEPT, max_per_page: int = 50) -> list[dict]:
        """발주서 목록 조회 (일단위 페이징 전체 수집). created_from/to: 'YYYY-MM-DD'."""
        path = f"/v2/providers/openapi/apis/api/v4/vendors/{self.vendor_id}/ordersheets"
        results: list[dict] = []
        next_token = ""
        while True:
            params = {
                "createdAtFrom": created_from,
                "createdAtTo": created_to,
                "status": status,
                "maxPerPage": max_per_page,
            }
            if next_token:
                params["nextToken"] = next_token
            data = self._request("GET", path, params=params)
            results.extend(data.get("data") or [])
            next_token = str(data.get("nextToken") or "")
            if not next_token:
                return results

    def acknowledge(self, shipment_box_ids: list[str | int]) -> dict:
        """결제완료(ACCEPT) → 상품준비중(INSTRUCT) 전환. 발주 확정 직후에만 호출한다.

        상품준비중 전환 이후에는 고객이 즉시 취소할 수 없으므로,
        반드시 공급처 발주가 확정된 박스만 전달할 것."""
        path = f"/v2/providers/openapi/apis/api/v4/vendors/{self.vendor_id}/ordersheets/acknowledgement"
        body = {"vendorId": self.vendor_id, "shipmentBoxIds": [int(b) for b in shipment_box_ids]}
        return self._request("PUT", path, body=body)

    def upload_invoice(self, order_sheet_invoice_applies: list[dict]) -> dict:
        """송장 업로드 (2주차 범위 — 공급처 송장 회수 파이프라인에서 사용 예정)."""
        path = f"/v2/providers/openapi/apis/api/v4/vendors/{self.vendor_id}/orders/invoices"
        body = {"vendorId": self.vendor_id, "orderSheetInvoiceApplyDtos": order_sheet_invoice_applies}
        return self._request("POST", path, body=body)


def parse_ordersheet(sheet: dict) -> dict:
    """쿠팡 발주서 JSON → 내부 표현. 필드명은 v4 발주서 응답 기준."""
    receiver = sheet.get("receiver") or {}
    addr = " ".join(x for x in (receiver.get("addr1"), receiver.get("addr2")) if x)
    return {
        "shipment_box_id": str(sheet.get("shipmentBoxId")),
        "order_id": str(sheet.get("orderId")),
        "ordered_at": sheet.get("orderedAt"),
        "receiver_name": receiver.get("name"),
        "receiver_phone": receiver.get("safeNumber") or receiver.get("receiverNumber"),
        "receiver_zip": receiver.get("postCode"),
        "receiver_addr": addr,
        "delivery_message": sheet.get("parcelPrintMessage") or "",
        "items": [
            {
                "vendor_item_id": str(it.get("vendorItemId")),
                "item_name": it.get("vendorItemName"),
                "qty": it.get("shippingCount") or 1,
                "sale_price": it.get("salesPrice") or 0,
            }
            for it in (sheet.get("orderItems") or [])
        ],
        "raw": sheet,
    }
