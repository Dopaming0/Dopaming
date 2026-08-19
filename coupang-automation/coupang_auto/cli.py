"""CLI: python -m coupang_auto.cli <command>"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .config import load_config
from .coupang_client import CoupangClient
from .db import Database
from . import pipeline
from .scheduler import run_daemon


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coupang-auto", description="쿠팡 과일 위탁판매 자동화")
    parser.add_argument("--config", default="config.yaml", help="설정 파일 경로 (기본: config.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="DB 초기화")

    p = sub.add_parser("import-sku", help="SKU 매핑 CSV 임포트")
    p.add_argument("csv_file")

    p = sub.add_parser("run-batch", help="배치 1회 수동 실행 (수집→취소반영→발주엑셀)")
    p.add_argument("--label", default=None, help="배치 라벨 (기본: 현재시각 HHMM)")

    sub.add_parser("list-pending", help="승인 대기 중인 발주서(PO) 목록")

    p = sub.add_parser("approve", help="발주서 승인 → 상품준비중 전환")
    p.add_argument("po_id", type=int)

    p = sub.add_parser("reject", help="발주서 반려 (주문은 다음 배치에서 재발주)")
    p.add_argument("po_id", type=int)

    sub.add_parser("run-daemon", help="상시 데몬 실행 (배치 스케줄 + 텔레그램 봇 + 가격·품절 감시)")

    sub.add_parser("watch-run", help="구글시트 상품의 도매처 가격·품절 즉시 확인")

    p = sub.add_parser("watch-login", help="로그인 필요한 도매처의 세션 저장 (브라우저가 열림)")
    p.add_argument("supplier_key", help="config.yaml 의 공급처 키 (예: woleokdojeon)")
    p.add_argument("login_url", help="도매처 로그인 페이지 URL")

    args = parser.parse_args(argv)
    config = load_config(args.config)
    db = Database(config.db_path)

    if args.command == "init-db":
        print(f"DB 준비 완료: {config.db_path}")
        return 0

    if args.command == "import-sku":
        n = db.import_sku_csv(args.csv_file)
        print(f"SKU {n}건 임포트 완료")
        return 0

    if args.command == "run-batch":
        client = CoupangClient(config.coupang.vendor_id, config.coupang.access_key, config.coupang.secret_key)
        label = args.label or datetime.now(config.tz).strftime("%H%M")
        result = pipeline.run_batch(client, db, config, label=label)
        if result is None:
            print("이미 실행된 배치입니다 (같은 날짜/라벨).")
            return 1
        print(result.summary_text())
        return 0

    if args.command == "list-pending":
        rows = db.pending_pos()
        if not rows:
            print("승인 대기 중인 발주서가 없습니다.")
        for r in rows:
            print(f"[{r['id']}] {r['supplier_key']} batch={r['batch_id']} file={r['file_path']}")
        return 0

    if args.command == "approve":
        client = CoupangClient(config.coupang.vendor_id, config.coupang.access_key, config.coupang.secret_key)
        print(pipeline.approve_po(client, db, args.po_id))
        return 0

    if args.command == "reject":
        print(pipeline.reject_po(db, args.po_id))
        return 0

    if args.command == "run-daemon":
        run_daemon(config)
        return 0

    if args.command == "watch-run":
        from .watcher import run_watch
        print(run_watch(config, db))
        return 0

    if args.command == "watch-login":
        from playwright.sync_api import sync_playwright
        storage_dir = config._resolve("data/storage")
        storage_dir.mkdir(parents=True, exist_ok=True)
        state_path = storage_dir / f"{args.supplier_key}.json"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            page.goto(args.login_url)
            input("브라우저에서 로그인을 마친 뒤, 이 터미널에서 Enter 를 누르세요... ")
            context.storage_state(path=str(state_path))
            browser.close()
        print(f"로그인 세션 저장 완료: {state_path}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
