"""SQLite storage: SKU map, collected orders, purchase orders, batch runs."""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sku_map (
  vendor_item_id     TEXT PRIMARY KEY,
  coupang_item_name  TEXT,
  supplier_key       TEXT NOT NULL,
  supplier_item_name TEXT NOT NULL,
  supply_price       INTEGER NOT NULL DEFAULT 0,
  ship_days          INTEGER NOT NULL DEFAULT 1,
  active             INTEGER NOT NULL DEFAULT 1
);

-- 주문 단위는 쿠팡 배송박스(shipment_box). 상품준비중 처리도 박스 단위로 이뤄진다.
CREATE TABLE IF NOT EXISTS orders (
  shipment_box_id  TEXT PRIMARY KEY,
  order_id         TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'collected',
      -- collected: 수집됨(발주 전, 취소 흡수 구간) / po_pending: 발주서 승인 대기
      -- ordered: 발주 완료(상품준비중 전환) / cancelled: 발주 전 취소 확인됨
  ordered_at       TEXT,
  receiver_name    TEXT,
  receiver_phone   TEXT,
  receiver_zip     TEXT,
  receiver_addr    TEXT,
  delivery_message TEXT,
  raw_json         TEXT,
  collected_at     TEXT NOT NULL,
  updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS order_items (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  shipment_box_id TEXT NOT NULL REFERENCES orders(shipment_box_id),
  vendor_item_id  TEXT NOT NULL,
  item_name       TEXT,
  qty             INTEGER NOT NULL DEFAULT 1,
  sale_price      INTEGER NOT NULL DEFAULT 0,
  po_id           INTEGER
);

CREATE TABLE IF NOT EXISTS purchase_orders (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id     TEXT NOT NULL,
  supplier_key TEXT NOT NULL,
  file_path    TEXT,
  status       TEXT NOT NULL DEFAULT 'pending',  -- pending / approved / rejected
  created_at   TEXT NOT NULL,
  decided_at   TEXT
);

CREATE TABLE IF NOT EXISTS batch_runs (
  batch_id    TEXT PRIMARY KEY,
  label       TEXT NOT NULL,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  summary     TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  ts     TEXT NOT NULL,
  kind   TEXT NOT NULL,
  detail TEXT
);

-- 도매처 상품페이지 감시 상태 (가격·품절 변경 감지용 직전 스냅샷)
CREATE TABLE IF NOT EXISTS watch_state (
  url        TEXT PRIMARY KEY,
  price      INTEGER,
  soldout    INTEGER,
  error      TEXT,
  checked_at TEXT
);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---- events ----
    def log(self, kind: str, detail: str = ""):
        self.conn.execute(
            "INSERT INTO events (ts, kind, detail) VALUES (?, ?, ?)", (now_iso(), kind, detail)
        )
        self.conn.commit()

    # ---- sku map ----
    def upsert_sku(self, vendor_item_id: str, coupang_item_name: str, supplier_key: str,
                   supplier_item_name: str, supply_price: int = 0, ship_days: int = 1):
        self.conn.execute(
            """INSERT INTO sku_map (vendor_item_id, coupang_item_name, supplier_key,
                                    supplier_item_name, supply_price, ship_days)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(vendor_item_id) DO UPDATE SET
                 coupang_item_name=excluded.coupang_item_name,
                 supplier_key=excluded.supplier_key,
                 supplier_item_name=excluded.supplier_item_name,
                 supply_price=excluded.supply_price,
                 ship_days=excluded.ship_days""",
            (str(vendor_item_id), coupang_item_name, supplier_key,
             supplier_item_name, int(supply_price), int(ship_days)),
        )
        self.conn.commit()

    def import_sku_csv(self, csv_path: str | Path) -> int:
        count = 0
        with open(csv_path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                self.upsert_sku(
                    row["vendor_item_id"].strip(),
                    row.get("coupang_item_name", "").strip(),
                    row["supplier_key"].strip(),
                    row["supplier_item_name"].strip(),
                    int(row.get("supply_price") or 0),
                    int(row.get("ship_days") or 1),
                )
                count += 1
        return count

    def get_sku(self, vendor_item_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM sku_map WHERE vendor_item_id = ? AND active = 1", (str(vendor_item_id),)
        ).fetchone()

    # ---- orders ----
    def upsert_order(self, box: dict) -> bool:
        """Insert a collected shipment box. Returns True if newly inserted."""
        existing = self.conn.execute(
            "SELECT status FROM orders WHERE shipment_box_id = ?", (box["shipment_box_id"],)
        ).fetchone()
        if existing:
            return False
        self.conn.execute(
            """INSERT INTO orders (shipment_box_id, order_id, status, ordered_at, receiver_name,
                                   receiver_phone, receiver_zip, receiver_addr, delivery_message,
                                   raw_json, collected_at)
               VALUES (?, ?, 'collected', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (box["shipment_box_id"], box["order_id"], box.get("ordered_at"),
             box.get("receiver_name"), box.get("receiver_phone"), box.get("receiver_zip"),
             box.get("receiver_addr"), box.get("delivery_message"),
             json.dumps(box.get("raw"), ensure_ascii=False), now_iso()),
        )
        for it in box.get("items", []):
            self.conn.execute(
                """INSERT INTO order_items (shipment_box_id, vendor_item_id, item_name, qty, sale_price)
                   VALUES (?, ?, ?, ?, ?)""",
                (box["shipment_box_id"], str(it["vendor_item_id"]), it.get("item_name"),
                 int(it.get("qty") or 1), int(it.get("sale_price") or 0)),
            )
        self.conn.commit()
        return True

    def set_order_status(self, shipment_box_ids: list[str], status: str):
        self.conn.executemany(
            "UPDATE orders SET status = ?, updated_at = ? WHERE shipment_box_id = ?",
            [(status, now_iso(), b) for b in shipment_box_ids],
        )
        self.conn.commit()

    def mark_cancelled_missing(self, seen_box_ids: set[str]) -> list[str]:
        """발주 전 주문 중 이번 수집(결제완료 목록)에 없는 건 → 취소로 간주.

        발주 전 = collected(수집됨) + po_pending(발주서 승인 대기).
        승인(ordered) 이후에는 취소를 확인하지 않는다."""
        rows = self.conn.execute(
            "SELECT shipment_box_id FROM orders WHERE status IN ('collected', 'po_pending')"
        ).fetchall()
        gone = [r["shipment_box_id"] for r in rows if r["shipment_box_id"] not in seen_box_ids]
        if gone:
            self.set_order_status(gone, "cancelled")
        return gone

    def pending_boxes(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM orders WHERE status = 'collected' ORDER BY ordered_at"
        ).fetchall()

    def items_for_box(self, shipment_box_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM order_items WHERE shipment_box_id = ?", (shipment_box_id,)
        ).fetchall()

    # ---- purchase orders ----
    def create_po(self, batch_id: str, supplier_key: str, file_path: str, item_ids: list[int]) -> int:
        cur = self.conn.execute(
            "INSERT INTO purchase_orders (batch_id, supplier_key, file_path, created_at) VALUES (?, ?, ?, ?)",
            (batch_id, supplier_key, file_path, now_iso()),
        )
        po_id = cur.lastrowid
        self.conn.executemany(
            "UPDATE order_items SET po_id = ? WHERE id = ?", [(po_id, i) for i in item_ids]
        )
        self.conn.commit()
        return po_id

    def get_po(self, po_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (po_id,)).fetchone()

    def pending_pos(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM purchase_orders WHERE status = 'pending' ORDER BY id"
        ).fetchall()

    def decide_po(self, po_id: int, status: str):
        self.conn.execute(
            "UPDATE purchase_orders SET status = ?, decided_at = ? WHERE id = ?",
            (status, now_iso(), po_id),
        )
        self.conn.commit()

    def po_boxes(self, po_id: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT shipment_box_id FROM order_items WHERE po_id = ?", (po_id,)
        ).fetchall()
        return [r["shipment_box_id"] for r in rows]

    def boxes_ready_to_acknowledge(self, po_id: int) -> list[str]:
        """PO 승인 후 상품준비중 전환 대상 박스: 박스의 모든 아이템이 '승인된 PO'에 속해야 한다.

        한 박스에 여러 공급처 상품이 섞인 경우, 마지막 PO가 승인될 때 함께 전환된다.
        승인 대기 중 취소된(cancelled) 박스는 제외한다."""
        ready = []
        for box_id in self.po_boxes(po_id):
            order = self.conn.execute(
                "SELECT status FROM orders WHERE shipment_box_id = ?", (box_id,)
            ).fetchone()
            if order is None or order["status"] != "po_pending":
                continue
            rows = self.conn.execute(
                """SELECT oi.id, po.status AS po_status
                   FROM order_items oi LEFT JOIN purchase_orders po ON oi.po_id = po.id
                   WHERE oi.shipment_box_id = ?""",
                (box_id,),
            ).fetchall()
            if all(r["po_status"] == "approved" for r in rows):
                ready.append(box_id)
        return ready

    # ---- watch state ----
    def get_watch_state(self, url: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM watch_state WHERE url = ?", (url,)).fetchone()
        return dict(row) if row else None

    def set_watch_state(self, url: str, price: int | None, soldout: bool | None, error: str | None):
        self.conn.execute(
            """INSERT INTO watch_state (url, price, soldout, error, checked_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET
                 price=excluded.price, soldout=excluded.soldout,
                 error=excluded.error, checked_at=excluded.checked_at""",
            (url, price, None if soldout is None else int(soldout), error, now_iso()),
        )
        self.conn.commit()

    # ---- batch runs ----
    def start_batch(self, batch_id: str, label: str) -> bool:
        try:
            self.conn.execute(
                "INSERT INTO batch_runs (batch_id, label, started_at) VALUES (?, ?, ?)",
                (batch_id, label, now_iso()),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False  # already ran

    def finish_batch(self, batch_id: str, summary: str):
        self.conn.execute(
            "UPDATE batch_runs SET finished_at = ?, summary = ? WHERE batch_id = ?",
            (now_iso(), summary, batch_id),
        )
        self.conn.commit()
