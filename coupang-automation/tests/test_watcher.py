from coupang_auto.watcher import CheckResult, diff_state, parse_product_page, soldout_label

OG_PAGE = """
<html><head>
<meta property="product:price:amount" content="12500" />
</head><body><div class="detail">성주참외 2kg</div></body></html>
"""

CAFE24_SOLDOUT_PAGE = """
<html><body>
<span id="span_product_price_text">15,800원</span>
<img src="/icon.gif" alt="품절" />
</body></html>
"""

JSONLD_PAGE = """
<html><head><script type="application/ld+json">
{"@type": "Product", "name": "복분자", "offers": {"price": "17500", "availability": "InStock"}}
</script></head><body><button>구매하기</button></body></html>
"""

SELECTOR_PAGE = """
<html><body>
<div class="custom-price">9,600원</div>
<div class="stock-badge">일시품절</div>
</body></html>
"""


def test_parse_og_meta_price():
    r = parse_product_page(OG_PAGE)
    assert r.price == 12500
    assert r.soldout is False
    assert r.ok


def test_parse_cafe24_price_and_soldout_image():
    r = parse_product_page(CAFE24_SOLDOUT_PAGE)
    assert r.price == 15800
    assert r.soldout is True
    assert soldout_label(r) == "품절"


def test_parse_jsonld_price():
    r = parse_product_page(JSONLD_PAGE)
    assert r.price == 17500
    assert r.soldout is False


def test_parse_with_custom_selectors():
    r = parse_product_page(SELECTOR_PAGE, {
        "price_selector": ".custom-price",
        "soldout_selector": ".stock-badge",
    })
    assert r.price == 9600
    assert r.soldout is True


def test_parse_failure_sets_error():
    r = parse_product_page("<html><body>로그인이 필요합니다</body></html>")
    assert not r.ok
    assert soldout_label(r) == "확인실패"


def test_diff_state_alerts():
    # 첫 확인: 기준 공급가보다 비싸면 마진 경고만
    alerts = diff_state(None, 12000, CheckResult(price=12500, soldout=False), "샤인머스켓")
    assert len(alerts) == 1 and "마진" in alerts[0]

    # 가격 인상 + 품절 동시 감지
    prev = {"price": 12500, "soldout": 0, "error": None}
    alerts = diff_state(prev, 12000, CheckResult(price=13000, soldout=True), "샤인머스켓")
    assert any("품절" in a for a in alerts)
    assert any("12,500 → 13,000" in a for a in alerts)

    # 품절 해제
    prev = {"price": 13000, "soldout": 1, "error": None}
    alerts = diff_state(prev, 14000, CheckResult(price=13000, soldout=False), "샤인머스켓")
    assert alerts == ["✅ 샤인머스켓: 품절 해제 — 판매 재개 가능"]

    # 변화 없음 → 알림 없음
    prev = {"price": 13000, "soldout": 0, "error": None}
    assert diff_state(prev, 14000, CheckResult(price=13000, soldout=False), "샤인머스켓") == []

    # 정상 확인되다가 실패 → 경고, 최초부터 실패 → 조용
    prev_ok = {"price": 13000, "soldout": 0, "error": None}
    assert len(diff_state(prev_ok, None, CheckResult(error="x"), "샤인머스켓")) == 1
    assert diff_state(None, None, CheckResult(error="x"), "샤인머스켓") == []
