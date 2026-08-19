import hashlib
import hmac

from coupang_auto.coupang_client import CoupangClient, parse_ordersheet


def test_authorization_header_format_and_signature():
    client = CoupangClient("A001", "ak", "sk")
    signed_date = "260718T000000Z"
    path = "/v2/providers/openapi/apis/api/v4/vendors/A001/ordersheets"
    query = "createdAtFrom=2026-07-15&createdAtTo=2026-07-18&status=ACCEPT"
    auth = client._authorization("GET", path, query, signed_date=signed_date)

    expected_sig = hmac.new(
        b"sk", (signed_date + "GET" + path + query).encode(), hashlib.sha256
    ).hexdigest()
    assert auth == (
        f"CEA algorithm=HmacSHA256, access-key=ak, signed-date={signed_date}, signature={expected_sig}"
    )


def test_parse_ordersheet():
    sheet = {
        "shipmentBoxId": 111222333,
        "orderId": 555666777,
        "orderedAt": "2026-07-18T08:30:00",
        "receiver": {
            "name": "홍길동",
            "safeNumber": "0502-000-0000",
            "postCode": "12345",
            "addr1": "서울시 성동구",
            "addr2": "왕십리로 1, 101동 202호",
        },
        "parcelPrintMessage": "부재시 문앞",
        "orderItems": [
            {"vendorItemId": 80012345678, "vendorItemName": "성주 꿀참외 2kg", "shippingCount": 2, "salesPrice": 19900},
        ],
    }
    box = parse_ordersheet(sheet)
    assert box["shipment_box_id"] == "111222333"
    assert box["order_id"] == "555666777"
    assert box["receiver_addr"] == "서울시 성동구 왕십리로 1, 101동 202호"
    assert box["items"][0]["vendor_item_id"] == "80012345678"
    assert box["items"][0]["qty"] == 2
