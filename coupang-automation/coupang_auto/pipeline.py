"""배치 파이프라인: 취소 반영 → 주문 수집 → SKU 매핑 → 발주 엑셀 생성 → 승인 → 상품준비중.

핵심 원칙 (신선식품 위탁판매):
- 주문은 결제완료(ACCEPT) 상태로 두는 동안 고객 취소를 자연 흡수한다.
- 취소 확인은 각 배치의 발주 직전 1회만 수행한다. 발주(승인) 이후 취소는 처리하지 않는다.
- 상품준비중(acknowledge) 전환은 발주 승인 직후, 박스의 모든 아이템 발주가 확정된 박스만.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import Config
from .coupang_client import CoupangClient, parse_ordersheet
from .db import Database
from .order_sheet import generate_order_sheet

COLLECT_WINDOW_DAYS = 3  # 수집 조회 범위: 최근 N일 (미발주 잔여 주문 재확인 겸용)


@dataclass
class BatchResult:
    batch_id: str
    new_orders: int = 0
    cancelled: int = 0
    pos: list[dict] = field(default_factory=list)          # [{po_id, supplier, name, file, lines}]
    unmapped: list[dict] = field(default_factory=list)     # [{vendor_item_id, item_name}]
    held_boxes: int = 0                                    # 매핑 누락으로 보류된 박스 수

    def summary_text(self) -> str:
        lines = [
            f"📦 배치 {self.batch_id}",
            f"신규 주문 {self.new_orders}건 · 발주 전 취소 {self.cancelled}건",
        ]
        if self.pos:
            for po in self.pos:
                lines.append(f"— [{po['po_id']}] {po['name']}: {po['lines']}개 라인 → {po['file']}")
        else:
            lines.append("— 발주할 주문이 없습니다.")
        if self.unmapped:
            lines.append(f"⚠️ SKU 매핑 누락 {len(self.unmapped)}종 (박스 {self.held_boxes}건 보류):")
            for u in self.unmapped[:10]:
                lines.append(f"   · {u['vendor_item_id']} {u['item_name'] or ''}")
        return "\n".join(lines)


def collect_orders(client: CoupangClient, db: Database, now: datetime) -> tuple[int, int]:
    """결제완료(ACCEPT) 주문 수집 + 발주 전 취소 반영. returns (신규 수, 취소 수)."""
    created_from = (now - timedelta(days=COLLECT_WINDOW_DAYS)).strftime("%Y-%m-%d")
    created_to = now.strftime("%Y-%m-%d")
    sheets = client.fetch_ordersheets(created_from, created_to)
    boxes = [parse_ordersheet(s) for s in sheets]

    new_count = sum(1 for b in boxes if db.upsert_order(b))

    # 이전에 수집됐지만(collected) 이번 결제완료 목록에 없는 주문 = 발주 전 고객 취소.
    seen = {b["shipment_box_id"] for b in boxes}
    cancelled = db.mark_cancelled_missing(seen)
    if cancelled:
        db.log("cancelled_before_po", f"{len(cancelled)} boxes: {', '.join(cancelled[:20])}")
    return new_count, len(cancelled)


def build_purchase_orders(db: Database, config: Config, batch_id: str, result: BatchResult):
    """collected 주문을 공급처별 발주 엑셀로 변환하고 PO(pending)를 만든다."""
    by_supplier: dict[str, list[tuple[int, dict]]] = {}  # supplier_key -> [(item_row_id, line)]
    unmapped_skus: dict[str, dict] = {}

    for box in db.pending_boxes():
        items = db.items_for_box(box["shipment_box_id"])
        mapped_lines: list[tuple[str, int, dict]] = []
        box_ok = True
        for it in items:
            sku = db.get_sku(it["vendor_item_id"])
            if sku is None:
                box_ok = False
                unmapped_skus[it["vendor_item_id"]] = {
                    "vendor_item_id": it["vendor_item_id"], "item_name": it["item_name"],
                }
                continue
            if sku["supplier_key"] not in config.suppliers:
                box_ok = False
                unmapped_skus[it["vendor_item_id"]] = {
                    "vendor_item_id": it["vendor_item_id"],
                    "item_name": f"{it['item_name']} (미정의 공급처: {sku['supplier_key']})",
                }
                continue
            mapped_lines.append((sku["supplier_key"], it["id"], {
                "receiver_name": box["receiver_name"],
                "receiver_phone": box["receiver_phone"],
                "receiver_zip": box["receiver_zip"],
                "receiver_addr": box["receiver_addr"],
                "supplier_item_name": sku["supplier_item_name"],
                "coupang_item_name": it["item_name"],
                "qty": it["qty"],
                "delivery_message": box["delivery_message"],
                "order_id": box["order_id"],
                "shipment_box_id": box["shipment_box_id"],
            }))
        # 매핑이 하나라도 빠진 박스는 통째로 보류 — 상품준비중 전환이 박스 단위라서
        # 일부만 발주하면 나머지 아이템이 출고 불능 상태로 지연 페널티를 만든다.
        if not box_ok:
            result.held_boxes += 1
            continue
        for supplier_key, item_row_id, line in mapped_lines:
            by_supplier.setdefault(supplier_key, []).append((item_row_id, line))

    result.unmapped = list(unmapped_skus.values())

    for supplier_key, entries in sorted(by_supplier.items()):
        sconf = config.suppliers[supplier_key]
        file_path = config.orders_dir / batch_id / f"{batch_id}_{supplier_key}.xlsx"
        generate_order_sheet(file_path, [line for _, line in entries], sconf.columns or None)
        po_id = db.create_po(batch_id, supplier_key, str(file_path), [i for i, _ in entries])
        boxes = db.po_boxes(po_id)
        db.set_order_status(boxes, "po_pending")
        result.pos.append({
            "po_id": po_id, "supplier": supplier_key, "name": sconf.name,
            "file": str(file_path), "lines": len(entries),
        })
        db.log("po_created", f"po={po_id} supplier={supplier_key} lines={len(entries)}")


def run_batch(client: CoupangClient, db: Database, config: Config, label: str = "manual",
              now: datetime | None = None) -> BatchResult | None:
    now = now or datetime.now(config.tz)
    batch_id = now.strftime("%Y%m%d") + "-" + label
    if not db.start_batch(batch_id, label):
        return None  # 같은 배치가 이미 실행됨
    result = BatchResult(batch_id=batch_id)
    result.new_orders, result.cancelled = collect_orders(client, db, now)
    build_purchase_orders(db, config, batch_id, result)
    db.finish_batch(batch_id, result.summary_text())
    return result


def approve_po(client: CoupangClient, db: Database, po_id: int) -> str:
    """PO 승인 → (모든 아이템 발주가 확정된 박스만) 상품준비중 전환.

    이 시점 이후 해당 주문의 고객 취소는 자동 처리하지 않는다."""
    po = db.get_po(po_id)
    if po is None:
        return f"PO {po_id} 없음"
    if po["status"] != "pending":
        return f"PO {po_id} 는 이미 {po['status']} 상태"
    db.decide_po(po_id, "approved")
    ready = db.boxes_ready_to_acknowledge(po_id)
    if ready:
        client.acknowledge(ready)
        db.set_order_status(ready, "ordered")
    db.log("po_approved", f"po={po_id} acknowledged_boxes={len(ready)}")
    return f"✅ PO {po_id} 승인 — 상품준비중 전환 {len(ready)}건. 발주 엑셀: {po['file_path']}"


def reject_po(db: Database, po_id: int) -> str:
    po = db.get_po(po_id)
    if po is None:
        return f"PO {po_id} 없음"
    if po["status"] != "pending":
        return f"PO {po_id} 는 이미 {po['status']} 상태"
    db.decide_po(po_id, "rejected")
    # 주문을 collected 로 되돌려 다음 배치에서 다시 발주 대상이 되게 한다.
    # (승인 대기 중 취소된 박스는 되살리지 않는다)
    boxes = [
        b for b in db.po_boxes(po_id)
        if (row := db.conn.execute(
            "SELECT status FROM orders WHERE shipment_box_id = ?", (b,)
        ).fetchone()) and row["status"] == "po_pending"
    ]
    db.set_order_status(boxes, "collected")
    db.conn.execute("UPDATE order_items SET po_id = NULL WHERE po_id = ?", (po_id,))
    db.conn.commit()
    db.log("po_rejected", f"po={po_id}")
    return f"❌ PO {po_id} 반려 — 주문 {len(boxes)}건은 다음 배치에서 재발주 대상이 됩니다."
