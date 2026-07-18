"""텔레그램 승인/알림 봇 — 외부 라이브러리 없이 Bot API 롱폴링만 사용."""
from __future__ import annotations

import logging
import threading
from typing import Callable

import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramBot:
    def __init__(self, bot_token: str, chat_id: str):
        self.token = bot_token
        self.chat_id = chat_id
        self._offset = 0
        self._stop = threading.Event()

    def _call(self, method: str, **params) -> dict:
        resp = requests.post(API.format(token=self.token, method=method), json=params, timeout=40)
        data = resp.json()
        if not data.get("ok"):
            log.warning("telegram %s failed: %s", method, data)
        return data

    def send(self, text: str):
        self._call("sendMessage", chat_id=self.chat_id, text=text)

    def send_po_approval(self, po_id: int, text: str):
        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ 발주 승인", "callback_data": f"po_approve:{po_id}"},
                {"text": "❌ 반려", "callback_data": f"po_reject:{po_id}"},
            ]]
        }
        self._call("sendMessage", chat_id=self.chat_id, text=text, reply_markup=keyboard)

    def send_document(self, file_path: str, caption: str = ""):
        with open(file_path, "rb") as f:
            resp = requests.post(
                API.format(token=self.token, method="sendDocument"),
                data={"chat_id": self.chat_id, "caption": caption},
                files={"document": f},
                timeout=60,
            )
        if not resp.json().get("ok"):
            log.warning("telegram sendDocument failed: %s", resp.text[:300])

    # ---- long polling ----
    def poll_forever(self, on_decision: Callable[[str, int], str]):
        """on_decision(action, po_id) -> 응답 텍스트. action: 'approve' | 'reject'"""
        while not self._stop.is_set():
            try:
                data = self._call("getUpdates", offset=self._offset + 1, timeout=30)
            except requests.RequestException as e:
                log.warning("telegram poll error: %s", e)
                self._stop.wait(5)
                continue
            for update in data.get("result", []):
                self._offset = max(self._offset, update["update_id"])
                cq = update.get("callback_query")
                if not cq:
                    continue
                payload = cq.get("data") or ""
                try:
                    action_key, po_id_s = payload.split(":", 1)
                    action = {"po_approve": "approve", "po_reject": "reject"}[action_key]
                    reply = on_decision(action, int(po_id_s))
                except Exception as e:  # 승인 처리 실패도 사용자에게 알린다
                    log.exception("callback handling failed")
                    reply = f"처리 실패: {e}"
                self._call("answerCallbackQuery", callback_query_id=cq["id"])
                self.send(reply)

    def stop(self):
        self._stop.set()

    def start_polling_thread(self, on_decision: Callable[[str, int], str]) -> threading.Thread:
        t = threading.Thread(target=self.poll_forever, args=(on_decision,), daemon=True)
        t.start()
        return t
