"""구글시트 연동 — 상품 목록(감시 대상) 읽기 + 확인 결과 쓰기.

시트 사용 규칙:
- 헤더 행에 '상품명', '공급가' 열이 있는 탭을 대상으로 한다 (헤더 행 위치는 자동 탐색).
- 감시 대상 행 = '상품명'과 '공급처URL'이 모두 채워진 행.
- 결과 열('현재공급가', '품절여부', '확인시각')은 없으면 헤더 행 오른쪽 끝에 자동 생성한다.
"""
from __future__ import annotations

from dataclasses import dataclass

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

COL_NAME = "상품명"
COL_SUPPLIER = "공급처"
COL_SUPPLY_PRICE = "공급가"
COL_URL = "공급처URL"
RESULT_COLUMNS = ["현재공급가", "품절여부", "확인시각"]


@dataclass
class WatchItem:
    row: int              # 1-indexed sheet row
    name: str
    supplier: str
    base_price: int | None  # 시트에 기록된 기준 공급가
    url: str


def parse_price(text: str) -> int | None:
    digits = "".join(c for c in str(text) if c.isdigit())
    return int(digits) if digits else None


class SheetClient:
    def __init__(self, spreadsheet_id: str, worksheet: str, service_account_json: str):
        creds = Credentials.from_service_account_file(service_account_json, scopes=SCOPES)
        self.ws = gspread.authorize(creds).open_by_key(spreadsheet_id).worksheet(worksheet)
        self._header_row: int | None = None
        self._cols: dict[str, int] = {}  # header name -> 1-indexed column

    def _load_grid(self) -> list[list[str]]:
        values = self.ws.get_all_values()
        for i, row in enumerate(values):
            cells = [c.strip() for c in row]
            if COL_NAME in cells and COL_SUPPLY_PRICE in cells:
                self._header_row = i + 1
                self._cols = {c: j + 1 for j, c in enumerate(cells) if c}
                return values
        raise ValueError(
            f"헤더 행을 찾지 못했습니다 — '{COL_NAME}'과 '{COL_SUPPLY_PRICE}' 열이 있는 탭인지 확인하세요."
        )

    def load_watch_items(self) -> list[WatchItem]:
        values = self._load_grid()
        if COL_URL not in self._cols:
            raise ValueError(
                f"'{COL_URL}' 열이 없습니다 — 헤더 행에 '{COL_URL}' 열을 추가하고 "
                "상품별 도매처 상품페이지 주소를 채워 주세요."
            )
        items = []
        for r in range(self._header_row, len(values)):
            row = values[r]

            def cell(col_name: str) -> str:
                idx = self._cols.get(col_name)
                return row[idx - 1].strip() if idx and idx <= len(row) else ""

            name, url = cell(COL_NAME), cell(COL_URL)
            if not name or not url.startswith("http"):
                continue
            items.append(WatchItem(
                row=r + 1,
                name=name,
                supplier=cell(COL_SUPPLIER).replace("[merged]", "").strip(),
                base_price=parse_price(cell(COL_SUPPLY_PRICE)),
                url=url,
            ))
        return items

    def ensure_result_columns(self):
        """결과 열이 없으면 헤더 행 오른쪽 끝에 추가한다."""
        if self._header_row is None:
            self._load_grid()
        missing = [c for c in RESULT_COLUMNS if c not in self._cols]
        if not missing:
            return
        next_col = max(self._cols.values()) + 1
        updates = []
        for i, name in enumerate(missing):
            self._cols[name] = next_col + i
            updates.append({
                "range": rowcol_to_a1(self._header_row, next_col + i),
                "values": [[name]],
            })
        self.ws.batch_update(updates)

    def write_results(self, results: list[tuple[int, dict[str, str]]]):
        """results: [(row, {열이름: 값})]. 열 이름은 RESULT_COLUMNS 중 하나."""
        updates = []
        for row, data in results:
            for col_name, value in data.items():
                col = self._cols.get(col_name)
                if col:
                    updates.append({"range": rowcol_to_a1(row, col), "values": [[value]]})
        if updates:
            self.ws.batch_update(updates)
