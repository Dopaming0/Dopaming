# 쿠팡 과일 위탁판매 자동화 (coupang-auto)

쿠팡 마켓플레이스 과일 위탁판매의 **주문수집 → 발주 → 상품준비중 전환**을 자동화하는 로컬 실행 도구입니다.
공급처(월억도전·신선마켓·산지메이트 등)는 API가 없으므로, 공급처별 양식에 맞춘 **발주 엑셀 자동 생성 + 텔레그램 원클릭 승인** 방식으로 동작합니다.

## 핵심 설계 (신선식품 특화)

- **일 2회 배치 (기본 09:00 / 21:00)** — 과일은 발주 후 취소가 불가능하므로 수시 수집 대신 배치로 처리합니다.
- **취소는 발주 전에만 확인** — 주문을 결제완료(ACCEPT) 상태로 유지하는 동안 고객 취소를 자연 흡수하고,
  각 배치의 발주 직전에 취소를 최종 반영합니다. 발주(승인) 이후 취소는 자동 처리하지 않습니다.
- **상품준비중 전환은 발주 승인 직후에만** — 승인 전에는 고객이 자유롭게 취소할 수 있는 완충 구간이 유지됩니다.
- **박스 단위 무결성** — 상품준비중 전환이 배송박스 단위이므로, SKU 매핑이 하나라도 빠진 박스는 통째로 보류하고 알립니다.

### 배치 1회의 흐름

```
1. 결제완료(ACCEPT) 주문 수집 (최근 3일 범위)
2. 이전 수집분 중 결제완료 목록에서 사라진 주문 → 발주 전 취소 처리
3. SKU 매핑(쿠팡 옵션 ↔ 공급처 상품)으로 공급처별 발주 엑셀 생성
4. 텔레그램으로 발주서 전송 + [승인/반려] 버튼
5. 승인 → 해당 주문 상품준비중(acknowledge) 전환 / 반려 → 다음 배치에서 재발주
```

## 설치

```bash
cd coupang-automation
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
cp config.example.yaml config.yaml   # 열어서 실제 값 입력
```

필요한 것:

1. **쿠팡 OPEN API 키** — WING 로그인 > 우측 상단 아이디 > 추가판매정보 > OPEN API 키 발급.
   **유효기간 180일**이므로 만료 전 재발급이 필요합니다 (`config.yaml`의 `api_key_issued_date`에 발급일 기록).
2. **텔레그램 봇** (선택이지만 권장) — [@BotFather](https://t.me/BotFather)로 봇 생성 → 토큰 발급,
   봇에게 아무 메시지나 보낸 뒤 `https://api.telegram.org/bot<토큰>/getUpdates`에서 `chat.id` 확인.
   미설정 시 승인은 CLI(`approve`/`reject`)로 처리합니다.

## SKU 매핑 등록

쿠팡 옵션(vendorItemId)과 공급처 상품을 연결하는 CSV를 만들어 임포트합니다. `sku_map.example.csv` 참고:

| 컬럼 | 설명 |
|---|---|
| `vendor_item_id` | 쿠팡 옵션 ID (WING 상품조회 또는 발주서에서 확인) |
| `coupang_item_name` | 쿠팡 노출 상품명 (참고용) |
| `supplier_key` | 공급처 키 — `config.yaml`의 suppliers 키와 일치 (`woleokdojeon`/`sinseonmarket`/`sanjimate`) |
| `supplier_item_name` | 공급처 발주서에 적을 상품명 (공급처 상품명과 정확히 일치시킬 것) |
| `supply_price` | 공급가 (원) — 추후 마진 감시에 사용 |
| `ship_days` | 출고소요일 — 출고가 안정적인 공급처는 1, 들쑥날쑥하면 2 |

```bash
coupang-auto import-sku my_sku_map.csv
```

매핑이 없는 상품이 주문되면 해당 박스는 발주에서 **보류**되고 텔레그램/로그로 알려줍니다.
매핑을 추가하면 다음 배치에서 자동으로 발주됩니다.

## 실행

```bash
coupang-auto run-daemon          # 상시 데몬: 배치 스케줄 + 텔레그램 승인 봇
coupang-auto run-batch           # 배치 1회 수동 실행 (테스트/보충용)
coupang-auto list-pending        # 승인 대기 발주서 목록
coupang-auto approve 3           # PO 3번 승인 (텔레그램 미사용 시)
coupang-auto reject 3            # PO 3번 반려 → 다음 배치에서 재발주
```

부팅 시 자동 시작(리눅스 예시, systemd):

```ini
# /etc/systemd/system/coupang-auto.service
[Unit]
Description=Coupang fruit consignment automation
After=network-online.target

[Service]
WorkingDirectory=/path/to/coupang-automation
ExecStart=/path/to/coupang-automation/.venv/bin/coupang-auto run-daemon
Restart=always

[Install]
WantedBy=multi-user.target
```

Windows라면 작업 스케줄러에 "로그온 시" 트리거로 `coupang-auto run-daemon`을 등록하세요.

## 운영 시 주의

- **오전 배치 시각은 공급처 당일출고 마감보다 1시간 이상 앞으로** 조정하세요 (마감 10시면 배치 08:30).
  쿠팡 출고예정일은 주문일 + 출고소요일(영업일, 주말·공휴일 자동 제외)로 계산되므로,
  오전 발주분이 산지 마감을 넘기는 것이 유일한 지연 리스크 구간입니다.
- 쿠팡 오픈API의 엔드포인트 경로·필드는 `coupang_auto/coupang_client.py` 한 곳에 모여 있습니다.
  쿠팡 정책 변경으로 호출이 실패하면 [개발자센터 문서](https://developers.coupangcorp.com)와 대조해 이 파일만 수정하면 됩니다.
- 첫 1~2주는 발주 엑셀을 눈으로 검증한 뒤 승인하는 반자동으로 운영하고,
  옵션 매핑 오류가 없다고 확인된 후 완전 자동 전환을 검토하세요.

## 로드맵 (다음 단계)

- [ ] **2주차 — 송장 자동화**: 공급처 송장 회수(Playwright/메일) → 쿠팡 송장 업로드(`upload_invoice` 이미 준비됨) + 발주 후 24시간 송장 미등록 알림
- [ ] **3주차 — 오픈채팅 품절·가격 감시**: 공기계 알림 포워딩 → LLM 파싱 → 재고 0 처리/가격 조정 제안
- [ ] **4주차 — CS 자동화**: 쿠팡 CS API 수집 → 자동응답/승인 초안 (품질 클레임은 공급처 클레임 메시지 동시 생성)
- [ ] **5주차~ — 상품등록 파이프라인**: 공급처 상품 → AI 상세페이지 → 쿠팡 상품등록 API (2026.5 브랜드·식별번호 의무화 반영), 정산·마진 대시보드

## 개발

```bash
pip install -e ".[dev]"
pytest tests/
```
