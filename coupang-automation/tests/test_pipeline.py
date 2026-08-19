"""가짜 쿠팡 클라이언트로 배치 전체 흐름 검증: 수집 → 취소 → 매핑 → PO → 승인/반려."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from coupang_auto import pipeline
from coupang_auto.config import Config, CoupangConf, SupplierConf, TelegramConf
from coupang_auto.db import Database


class FakeClient:
    def __init__(self, sheets):
        self.sheets = sheets
        self.acknowledged: list[list] = []

    def fetch_ordersheets(self, created_from, created_to, status="ACCEPT", max_per_page=50):
        return self.sheets

    def acknowledge(self, shipment_box_ids):
        self.acknowledged.append(list(shipment_box_ids))
        return {"code": 200}


def make_sheet(box_id, order_id, vendor_item_id, name, qty=1):
    return {
        "shipmentBoxId": box_id,
        "orderId": order_id,
        "orderedAt": "2026-07-18T08:00:00",
        "receiver": {"name": "홍길동", "safeNumber": "0502-1234", "postCode": "04000",
                     "addr1": "서울", "addr2": "어딘가 1"},
        "parcelPrintMessage": "문앞",
        "orderItems": [{"vendorItemId": vendor_item_id, "vendorItemName": name,
                        "shippingCount": qty, "salesPrice": 10000}],
    }


@pytest.fixture
def env(tmp_path):
    config = Config(
        coupang=CoupangConf("A001", "ak", "sk"),
        telegram=TelegramConf(),
        suppliers={
            "woleokdojeon": SupplierConf("woleokdojeon", "월억도전"),
            "sanjimate": SupplierConf("sanjimate", "산지메이트"),
        },
        batches=["09:00", "21:00"],
        base_dir=tmp_path,
    )
    db = Database(config.db_path)
    db.upsert_sku("101", "참외 2kg", "woleokdojeon", "성주참외 2kg", 12500, 1)
    db.upsert_sku("202", "복분자 500g", "sanjimate", "복분자 생과 500g", 17500, 2)
    return config, db


def test_full_batch_flow(env):
    config, db = env
    client = FakeClient([
        make_sheet(1001, 9001, 101, "참외 2kg", qty=2),
        make_sheet(1002, 9002, 202, "복분자 500g"),
        make_sheet(1003, 9003, 999, "매핑안된 상품"),  # SKU 미등록 → 보류
    ])
    now = datetime(2026, 7, 18, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    result = pipeline.run_batch(client, db, config, label="0900", now=now)
    assert result.new_orders == 3
    assert result.cancelled == 0
    assert len(result.pos) == 2
    assert result.held_boxes == 1
    assert result.unmapped[0]["vendor_item_id"] == "999"
    for po in result.pos:
        assert Path(po["file"]).exists()

    # 같은 배치 재실행 방지
    assert pipeline.run_batch(client, db, config, label="0900", now=now) is None

    # 승인 → 상품준비중 전환 (해당 박스만)
    po_chamoe = next(p for p in result.pos if p["supplier"] == "woleokdojeon")
    msg = pipeline.approve_po(client, db, po_chamoe["po_id"])
    assert "승인" in msg
    assert client.acknowledged == [["1001"]]
    assert db.conn.execute(
        "SELECT status FROM orders WHERE shipment_box_id='1001'"
    ).fetchone()["status"] == "ordered"

    # 반려 → 주문은 collected 로 복귀해 다음 배치 대상
    po_bokbunja = next(p for p in result.pos if p["supplier"] == "sanjimate")
    msg = pipeline.reject_po(db, po_bokbunja["po_id"])
    assert "반려" in msg
    assert db.conn.execute(
        "SELECT status FROM orders WHERE shipment_box_id='1002'"
    ).fetchone()["status"] == "collected"


def test_cancel_before_po_only(env):
    """발주 전 취소 흡수: 이전 수집분이 결제완료 목록에서 사라지면 취소 처리.
    이미 발주(ordered)된 주문은 목록에서 사라져도 건드리지 않는다."""
    config, db = env
    now = datetime(2026, 7, 18, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    client = FakeClient([make_sheet(2001, 9101, 101, "참외"), make_sheet(2002, 9102, 202, "복분자")])
    result = pipeline.run_batch(client, db, config, label="0900", now=now)
    po = next(p for p in result.pos if p["supplier"] == "woleokdojeon")
    pipeline.approve_po(client, db, po["po_id"])  # 2001 은 ordered

    # 다음 배치: 2001(발주완료)과 2002(발주 전) 둘 다 결제완료 목록에서 사라짐
    client2 = FakeClient([])
    result2 = pipeline.run_batch(client2, db, config, label="2100", now=now)
    assert result2.cancelled >= 1
    assert db.conn.execute(
        "SELECT status FROM orders WHERE shipment_box_id='2002'"
    ).fetchone()["status"] == "cancelled"
    # 발주 나간 건은 취소 확인 대상이 아니다
    assert db.conn.execute(
        "SELECT status FROM orders WHERE shipment_box_id='2001'"
    ).fetchone()["status"] == "ordered"
