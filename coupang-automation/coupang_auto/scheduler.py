"""로컬 상시 구동 데몬: 설정된 배치 시각(기본 09:00/21:00)에 파이프라인 실행."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from .config import Config
from .coupang_client import CoupangClient
from .db import Database
from . import pipeline
from .telegram_bot import TelegramBot
from .watcher import run_watch

log = logging.getLogger(__name__)

CHECK_INTERVAL_SEC = 20
BATCH_WINDOW_MIN = 30  # 배치 시각으로부터 이 시간 안이면 (미실행 시) 실행 — 재부팅 지연 등을 흡수


def _due_label(now: datetime, batches: list[str]) -> str | None:
    for hhmm in batches:
        h, m = map(int, hhmm.split(":"))
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        delta_min = (now - start).total_seconds() / 60
        if 0 <= delta_min < BATCH_WINDOW_MIN:
            return hhmm.replace(":", "")
    return None


def notify_result(bot: TelegramBot | None, result: pipeline.BatchResult):
    if bot is None:
        log.info("batch result:\n%s", result.summary_text())
        return
    bot.send(result.summary_text())
    for po in result.pos:
        bot.send_document(po["file"], caption=f"[{po['po_id']}] {po['name']} 발주서")
        bot.send_po_approval(
            po["po_id"],
            f"PO {po['po_id']} — {po['name']} {po['lines']}개 라인\n"
            f"승인하면 해당 주문이 즉시 '상품준비중'으로 전환됩니다.\n"
            f"(전환 후에는 고객 취소를 자동 처리하지 않습니다)",
        )


def run_daemon(config: Config):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db = Database(config.db_path)
    client = CoupangClient(config.coupang.vendor_id, config.coupang.access_key, config.coupang.secret_key)

    bot: TelegramBot | None = None
    if config.telegram.enabled:
        bot = TelegramBot(config.telegram.bot_token, config.telegram.chat_id)

        def on_decision(action: str, po_id: int) -> str:
            if action == "approve":
                return pipeline.approve_po(client, db, po_id)
            return pipeline.reject_po(db, po_id)

        bot.start_polling_thread(on_decision)
        bot.send("🍑 쿠팡 자동화 데몬 시작 — 배치: " + ", ".join(config.batches))
    else:
        log.info("telegram 미설정 — 승인은 CLI(approve/reject 명령)로 처리하세요.")

    watch_enabled = bool(config.google_sheet.get("spreadsheet_id"))
    watch_interval = timedelta(hours=float(config.watch.get("interval_hours", 6)))
    next_watch = datetime.now(config.tz)  # 시작 직후 1회 실행

    log.info("daemon started (batches: %s, tz: %s, watch: %s)",
             config.batches, config.timezone, watch_enabled and f"every {watch_interval}")
    while True:
        now = datetime.now(config.tz)
        label = _due_label(now, config.batches)
        if label:
            try:
                result = pipeline.run_batch(client, db, config, label=label, now=now)
                if result:  # None 이면 오늘 이 배치는 이미 실행됨
                    notify_result(bot, result)
            except Exception:
                log.exception("batch failed")
                if bot:
                    bot.send(f"🚨 배치 실행 실패 ({label}) — 로그를 확인하세요.")
        if watch_enabled and now >= next_watch:
            next_watch = now + watch_interval
            try:
                run_watch(config, db, bot)
            except Exception:
                log.exception("watch failed")
                if bot:
                    bot.send("🚨 도매처 가격·품절 확인 실패 — 로그를 확인하세요.")
        time.sleep(CHECK_INTERVAL_SEC)
