"""네이버 3개 API를 흉내내는 로컬 목 서버. 표준 라이브러리만 쓴다."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# keyword -> (상품수, 월간검색수, 트렌드 시나리오)
FIXTURES = {
    "피클볼라켓":   (380, 8200, "climb"),      # 지속 성장 + 공급 비어있음 → 진입 창
    "무타공선반":   (1600, 12000, "growth"),   # 경쟁강도 0.13
    "경추베개":     (4200, 21000, "growth"),   # 경쟁강도 0.20
    "욕실물막이":   (900, 3400, "flat"),       # 경쟁강도 0.26
    "강아지유모차": (31000, 9500, "decline"),  # 경쟁강도 3.26 → 하드 게이트 탈락
    "테니스라켓":   (52000, 15000, "decline"), # 경쟁강도 3.47 → 탈락
}
DEFAULT = (5000, 10000, "flat")


def _trend(scenario: str) -> list[dict]:
    """25개월 시계열. 마지막 달은 부분 집계라 낮게 준다(실제 API와 동일)."""
    base = {
        "growth":  [30 + i * 0.9 for i in range(24)],
        "surge":   [20] * 21 + [45, 62, 80],                      # 날카로운 유행성 급등
        "climb":   [20] * 6 + [20 + i * 2.6 for i in range(18)],  # 완만하고 긴 상승 (피클볼형)
        "flat":    [40 + (i % 3) for i in range(24)],
        "decline": [70 - i * 1.6 for i in range(24)],
    }[scenario]
    out = []
    for i, v in enumerate(base):
        year, month = 2024 + (7 + i) // 12, (7 + i) % 12 + 1
        out.append({"period": f"{year}-{month:02d}-01", "ratio": round(v, 3)})
    out.append({"period": "2026-08-01", "ratio": round(base[-1] * 0.4, 3)})  # 부분 집계 월
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):  # 테스트 출력을 더럽히지 않는다
        pass

    def _send(self, payload: dict, code: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        qs = parse_qs(url.query)

        if url.path == "/v1/search/shop.json":
            if not self.headers.get("X-Naver-Client-Id"):
                return self._send({"errorMessage": "missing client id"}, 401)
            kw = (qs.get("query") or [""])[0]
            return self._send({"total": FIXTURES.get(kw, DEFAULT)[0], "items": []})

        if url.path == "/keywordstool":
            for h in ("X-Timestamp", "X-API-KEY", "X-Customer", "X-Signature"):
                if not self.headers.get(h):
                    return self._send({"title": "Unauthorized", "detail": f"missing {h}"}, 401)
            hints = (qs.get("hintKeywords") or [""])[0].split(",")
            rows = []
            for kw in [h for h in hints if h]:
                vol = FIXTURES.get(kw, DEFAULT)[1]
                pc = round(vol * 0.25)
                rows.append({
                    "relKeyword": kw,
                    "monthlyPcQcCnt": pc,
                    "monthlyMobileQcCnt": vol - pc,
                    "compIdx": "중간",
                })
            rows.append({  # '< 10' 파싱 경로를 반드시 태운다
                "relKeyword": "__저볼륨__",
                "monthlyPcQcCnt": "< 10",
                "monthlyMobileQcCnt": "< 10",
                "compIdx": "낮음",
            })
            return self._send({"keywordList": rows})

        self._send({"error": "not found"}, 404)

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/v1/datalab/search":
            return self._send({"error": "not found"}, 404)
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        results = []
        for g in payload.get("keywordGroups", []):
            name = g.get("groupName", "")
            results.append({"title": name, "data": _trend(FIXTURES.get(name, DEFAULT)[2])})
        self._send({"results": results})


def serve() -> tuple[HTTPServer, str]:
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


if __name__ == "__main__":
    s, url = serve()
    print(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        s.shutdown()
