"""가짜 워크시트로 시트 파싱·결과 기록 로직 검증 (네트워크 없음)."""
from coupang_auto import sheet as sheet_mod
from coupang_auto.sheet import SheetClient, parse_price


class FakeWorksheet:
    def __init__(self, grid):
        self.grid = grid
        self.updates = []

    def get_all_values(self):
        return self.grid

    def batch_update(self, updates):
        self.updates.extend(updates)


def make_client(grid) -> SheetClient:
    client = SheetClient.__new__(SheetClient)  # 인증 생략
    client.ws = FakeWorksheet(grid)
    client._header_row = None
    client._cols = {}
    return client


GRID = [
    ["", "", "", "", "", ""],
    ["일시", "상품명", "공급처", "공급가", "판매가", "공급처URL"],
    ["7/20", "샤인머스켓 1kg", "[merged] 월억도전", "12,000", "17,900", "https://mall.example/p/1"],
    ["7/20", "복숭아 2kg", "식품백억", "8,500", "12,800", "https://mall.example/p/2"],
    ["7/20", "URL 없는 상품", "월억도전", "5,000", "9,000", ""],
    ["", "", "", "", "", ""],
]


def test_parse_price():
    assert parse_price("12,500") == 12500
    assert parse_price(" 8,500 원") == 8500
    assert parse_price("") is None


def test_load_watch_items_skips_rows_without_url():
    client = make_client(GRID)
    items = client.load_watch_items()
    assert len(items) == 2
    assert items[0].name == "샤인머스켓 1kg"
    assert items[0].supplier == "월억도전"      # [merged] 제거됨
    assert items[0].base_price == 12000
    assert items[0].row == 3                    # 1-indexed 시트 행
    assert items[1].url.endswith("/p/2")


def test_missing_url_column_raises():
    grid = [["상품명", "공급가"], ["참외", "5,000"]]
    client = make_client(grid)
    try:
        client.load_watch_items()
        assert False, "should raise"
    except ValueError as e:
        assert "공급처URL" in str(e)


def test_ensure_result_columns_and_write():
    client = make_client(GRID)
    client.load_watch_items()
    client.ensure_result_columns()
    # 결과 열 3개가 헤더 행(2행) 오른쪽 끝(G/H/I)에 생성됨
    headers = [u for u in client.ws.updates if u["range"].endswith("2")]
    assert [u["values"][0][0] for u in headers] == ["현재공급가", "품절여부", "확인시각"]
    assert headers[0]["range"] == "G2"

    client.ws.updates.clear()
    client.write_results([(3, {"현재공급가": "12,500", "품절여부": "정상", "확인시각": "2026-07-20 09:00"})])
    ranges = {u["range"]: u["values"][0][0] for u in client.ws.updates}
    assert ranges == {"G3": "12,500", "H3": "정상", "I3": "2026-07-20 09:00"}
