"""주간 인사이트 요약: 수집된 data/*.json에서 이번 주 신호를 뽑고, Claude로 기획 브리핑을 붙인다.

    python trends/digest.py            # 마지막 요약이 6일 넘었을 때만 새로 만든다
    python trends/digest.py --force    # 바로 만든다

- 통계는 표준 라이브러리로 계산한다(대시보드 인사이트 탭과 같은 정의).
- ANTHROPIC_API_KEY가 있으면 Claude 요약(헤드라인·발견·기획 아이디어)을 붙인다. 없으면 통계만 남긴다.
- GITHUB_TOKEN·GITHUB_REPOSITORY가 있으면 '주간 인사이트' 이슈에 댓글로 올려 GitHub 알림으로 받는다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
HOT_X = 3
MIN_N = 8
SHORT_MAX = 180
REFRESH_DAYS = 6
# CLAUDE.md 정책: 요약·추출은 Opus 4.8이 기본값. 저장소 변수 DIGEST_MODEL로 바꿀 수 있다.
DEFAULT_MODEL = "claude-opus-4-8"
ISSUE_TITLE = "📈 주간 인사이트"

# index.html의 HOOKS와 같은 정의(테스트가 두 쪽 결과를 비교한다). JS와 맞추려고 re.ASCII로 \b·\d를 ASCII 기준으로 둔다.
# JS \p{Extended_Pictographic}에 맞춘 이모지 범위(국기용 지역 문자는 빠진다)
EMOJI = "[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in [(0x1F000, 0x1F1E5), (0x1F200, 0x1FAFF), (0x203C, 0x203C), (0x2049, 0x2049), (0x2122, 0x2122), (0x2139, 0x2139), (0x2194, 0x2199), (0x21A9, 0x21AA), (0x231A, 0x231B), (0x2328, 0x2328), (0x23CF, 0x23CF), (0x23E9, 0x23FA), (0x24C2, 0x24C2), (0x25AA, 0x25FE), (0x2600, 0x27BF), (0x2934, 0x2935), (0x2B05, 0x2B55), (0x3030, 0x3030), (0x303D, 0x303D), (0x3297, 0x3297), (0x3299, 0x3299), (0xA9, 0xA9), (0xAE, 0xAE)]) + "]"
HOOKS = [
    ("money", "돈·금액", r"(\d[\d,.]*\s*(원|만\s?원|억|천만|만|조)(?![가-힣])|[$₩€£]\s?\d|\d\s?(k|K|M)\b|수익|매출|연봉|월급|시급|순수익|얼마|가격|\bMRR\b|\bARR\b|/month|per month|a year|/year|salary|make\?|earn)"),
    ("age", "나이·세대", r"(\d{1,2}\s?(살|세)(?![가-힣])|\d{2}년생|\d{2}-year-old|\b\d{2}\s?yo\b|[1-9]0대|MZ|Gen ?Z|청년|대학생|고등학생|중학생|teen|student)"),
    ("number", "숫자", r"\d"),
    ("question", "질문형", r"(\?|？|일까|할까|인가요|나요|까요|는가|왜\s|어떻게|무엇|얼마|\bhow\b|\bwhy\b|\bwhat\b|\bwho\b)"),
    ("versus", "대결·비교", r"(\bvs\.?\b|대결|비교|차이|보다\s|이기|승자|\bbetter than\b)"),
    ("quote", "말 인용", r"[\"“”]|'[^']{2,}'|‘[^’]{2,}’"),
    ("first", "1인칭 경험", r"((^|\s)(나|내가|저는|제가|저의|우리)\s|해봤|해 봤|가봤|가 봤|직접|\bI\b|\bI'm\b|\bI've\b|\bmy\b|\bwe\b)"),
    ("warn", "경고·부정", r"(하지\s?마|절대|금지|최악|망한|망했|망하|실패|후회|위험|사기|조심|\bdon'?t\b|\bnever\b|\bstop\b|\bworst\b|\bmistake)"),
    ("extreme", "최초·극단", r"(최초|최고|최대|역대|처음|1위|미친|레전드|충격|역대급|\bfirst\b|\bbiggest\b|\bbest\b|\binsane\b|\bcrazy\b)"),
    ("challenge", "기간·도전", r"(\d+\s?(일|시간|분|주|개월|년)\s?(동안|만에|째|살기)|하루\s?(동안|만에)|24시간|도전|챌린지|\bchallenge\b|\d+\s?(days|hours)\b)"),
    ("reveal", "공개·진짜", r"(공개|폭로|진짜|실제|솔직|정체|비밀|현실|\breveal|\breal\b|\btruth\b|\bsecret\b)"),
    ("emoji", "이모지", EMOJI),
]
HOOK_RE = [(k, label, re.compile(p, re.IGNORECASE | re.ASCII)) for k, label, p in HOOKS]
SCOPES = [("all", "전체"), ("이팔청춘", "이팔청춘"), ("farm", "농산물"), ("challenge", "챌린지·참여형")]


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def rows_from(refs: dict | None, farm: dict | None) -> list[dict]:
    out = []
    for doc, src in ((refs, "ref"), (farm, "farm")):
        for ch in (doc or {}).get("channels", []):
            for r in ch.get("recent", []):
                if r.get("x") is None:
                    continue
                out.append({**r, "ch": ch.get("title", ""), "cid": ch["id"], "src": src,
                            "grp": ch.get("group", "") if src == "ref" else "farm", "hot": r["x"] >= HOT_X})
    return out


def in_scope(v: dict, key: str) -> bool:
    if key == "all":
        return True
    if key == "challenge":
        return v["grp"] in ("챌린지", "참여형")
    return v["grp"] == key


def conf(n1: int, h1: int, n2: int, h2: int) -> str:
    p = (h1 + h2) / (n1 + n2) if n1 + n2 else 0
    if not n1 or not n2 or p <= 0 or p >= 1:
        return "낮음"
    z = abs(h1 / n1 - h2 / n2) / (p * (1 - p) * (1 / n1 + 1 / n2)) ** 0.5
    return "높음" if z >= 2 else "보통" if z >= 1.3 else "낮음"


def hook_stats(rows: list[dict]) -> list[dict]:
    """같은 채널 안에서 패턴이 있는 영상과 없는 영상의 터질 확률을 비교한다(대시보드와 같은 방식)."""
    by_ch: dict[str, list[dict]] = {}
    for v in rows:
        by_ch.setdefault(v["cid"], []).append(v)
    out = []
    for key, label, rx in HOOK_RE:
        wn = wh = on = oh = chs = 0
        ex = []
        for lst in by_ch.values():
            w = [v for v in lst if rx.search(v["t"])]
            if not w or len(w) == len(lst):
                continue
            chs += 1
            wn += len(w)
            wh += sum(v["hot"] for v in w)
            on += len(lst) - len(w)
            oh += sum(v["hot"] for v in lst if not rx.search(v["t"]))
            ex += [v for v in w if v["hot"]]
        lift = ((wh + 0.5) / (wn + 1)) / ((oh + 0.5) / (on + 1))
        ex.sort(key=lambda v: -v["x"])
        out.append({"key": key, "label": label, "n": wn, "hot": wh, "rate": round(wh / wn, 3) if wn else 0,
                    "nOff": on, "rateOff": round(oh / on, 3) if on else 0, "chs": chs, "lift": round(lift, 2),
                    "ok": wn >= MIN_N and on >= MIN_N, "conf": conf(wn, wh, on, oh),
                    "ex": [{"id": v["id"], "t": v["t"], "ch": v["ch"], "x": v["x"]} for v in ex[:2]]})
    return sorted(out, key=lambda h: -h["lift"])


def video(v: dict) -> dict:
    out = {k: v.get(k) for k in ("id", "t", "ch", "x", "v", "p", "d")}
    if v.get("vph") is not None:
        out["vph"] = v["vph"]
    out["grp"] = v["grp"]
    return out


def compute(refs: dict | None, farm: dict | None, mine: dict | None, now: datetime) -> dict:
    rows = rows_from(refs, farm)
    since = now - timedelta(days=7)
    week = [v for v in rows if v.get("p") and parse_ts(v["p"]) >= since]
    hot = sorted((v for v in week if v["hot"]), key=lambda v: -v["x"])[:12]
    rising = sorted((v for v in rows if v.get("vph")), key=lambda v: -v["vph"])[:8]
    scopes = []
    for key, label in SCOPES:
        rs = [v for v in rows if in_scope(v, key)]
        if not rs:
            continue
        hs = hook_stats(rs)
        n, h = len(rs), sum(v["hot"] for v in rs)
        scopes.append({"key": key, "label": label, "n": n, "hot": h, "rate": round(h / n, 3),
                       "up": [x for x in hs if x["ok"] and x["lift"] >= 1.3][:3],
                       "down": [x for x in hs if x["ok"] and x["lift"] <= 0.7][-2:]})
    out = {"week": {"from": since.date().isoformat(), "to": now.date().isoformat(), "uploads": len(week),
                    "hot": [video(v) for v in hot], "rising": [video(v) for v in rising]},
           "scopes": scopes}
    mine_rows = [r for ch in (mine or {}).get("channels", []) for r in ch.get("recent", [])]
    if mine_rows:
        out["mine"] = {"videos": len(mine_rows), "hot": sum(1 for r in mine_rows if (r.get("x") or 0) >= HOT_X),
                       "recent": [{k: r.get(k) for k in ("t", "x", "v", "p", "vph")} for r in sorted(mine_rows, key=lambda r: r.get("p", ""), reverse=True)[:8]]}
    return out


SYSTEM = """너는 한국 유튜브 기획팀의 데이터 분석 PD다. 매주 벤치마크 대시보드 데이터를 읽고 기획자에게 짧은 주간 브리핑을 쓴다.

기획 중인 채널:
- 이팔청춘: 16~32세만 출연하는 인터뷰 채널. 출연자에게 실제 거래(돈·과일·시간)를 걸어 대화를 끌어낸다.
- 농산물: 과일 위탁·사입 판매를 하며 농장을 준비하는 사업가의 채널.
- 챌린지: 제한을 걸고 끝까지 가는 사회실험·도전.

원칙:
- '배수'는 영상 조회수 ÷ 같은 채널 최근 영상 조회수 중앙값이다. 3배 이상이 '터진 영상'이다.
- 제목 패턴 통계는 같은 채널 안에서 패턴이 있는 영상과 없는 영상을 비교한 값이다. 상관관계일 뿐이니 원인처럼 단정하지 않는다. 표본이 적으면 적다고 쓴다.
- 제목은 데이터에 있는 그대로 인용한다. 없는 영상이나 숫자를 만들지 않는다.
- 문장은 짧은 존댓말로 쓴다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "이번 주를 한 문장으로"},
        "findings": {"type": "array", "description": "3~5개", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "detail": {"type": "string"},
                           "evidence": {"type": "array", "items": {"type": "string"}}},
            "required": ["title", "detail", "evidence"], "additionalProperties": False}},
        "ideas": {"type": "array", "description": "채널마다 2개씩 6개", "items": {
            "type": "object",
            "properties": {"for": {"type": "string", "enum": ["이팔청춘", "농산물", "챌린지"]},
                           "line": {"type": "string"}, "thumb": {"type": "string"}, "why": {"type": "string"}},
            "required": ["for", "line", "thumb", "why"], "additionalProperties": False}},
        "watch": {"type": "array", "description": "다음 주에 지켜볼 것 2~3개", "items": {"type": "string"}},
        "mine": {"type": "string", "description": "내 채널 데이터가 있으면 한두 문장 코멘트, 없으면 빈 문자열"},
    },
    "required": ["headline", "findings", "ideas", "watch", "mine"],
    "additionalProperties": False,
}


def prompt(stats: dict) -> str:
    def vline(v: dict) -> str:
        fmt = "?" if not v.get("d") else "쇼츠" if v["d"] <= SHORT_MAX else f"{round(v['d'] / 60)}분"
        extra = f" | +{v['vph']:.0f}회/시간" if v.get("vph") else ""
        return f"{v['x']}배 | {fmt} | {v['ch']} | {v['t'][:110]}{extra}"

    parts = [f"[기간] {stats['week']['from']} ~ {stats['week']['to']} · 이번 주 업로드 {stats['week']['uploads']}개"]
    parts.append("[이번 주 터진 영상] 배수 | 길이 | 채널 | 제목\n" + ("\n".join(vline(v) for v in stats["week"]["hot"]) or "없음"))
    if stats["week"]["rising"]:
        parts.append("[지금 오르는 중 · 어제 대비 시간당 조회 증가]\n" + "\n".join(vline(v) for v in stats["week"]["rising"]))
    for s in stats["scopes"]:
        lines = [f"- {h['label']}: 있으면 {h['rate']:.0%}, 없으면 {h['rateOff']:.0%} ({h['lift']}배, 영상 {h['n']}개, 신뢰도 {h['conf']})"
                 for h in s["up"] + s["down"]]
        parts.append(f"[{s['label']}] 영상 {s['n']}개 중 {s['hot']}개({s['rate']:.0%})가 터짐. 제목 패턴:\n" + ("\n".join(lines) or "- 뚜렷한 패턴 없음"))
    if stats.get("mine"):
        m = stats["mine"]
        parts.append(f"[내 채널] 영상 {m['videos']}개 중 {m['hot']}개가 평소의 3배 이상. 최근 영상:\n" +
                     "\n".join(f"- {r.get('x')}배 | 조회 {r.get('v')} | {r.get('t')}" for r in m["recent"]))
    parts.append("""할 일
1. headline: 이번 주 데이터를 한 문장으로.
2. findings: 기획에 쓸 만한 발견 3~5개. 각 발견에 근거 제목을 위 목록에서 그대로 1~3개.
3. ideas: 이팔청춘·농산물·챌린지에 하나씩 이상, 모두 6개. line은 "A가 B한다" 한 줄, thumb는 12자 이내 썸네일 문구.
4. watch: 다음 주에 지켜볼 것 2~3개.
5. mine: 내 채널 데이터가 있으면 한두 문장, 없으면 빈 문자열.""")
    return "\n\n".join(parts)


def call_claude(user: str, model: str) -> dict | None:
    """Claude로 브리핑을 받는다. 실패하면 None을 돌려주고 통계만 남긴다."""
    import anthropic  # 키가 있을 때만 필요하다

    client = anthropic.Anthropic()
    try:
        res = client.messages.create(
            model=model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.RateLimitError:
        log("Claude: 요청 한도 초과, 이번 주는 통계만 남깁니다")
        return None
    except anthropic.APIStatusError as e:
        log(f"Claude: API 오류 {e.status_code} {e.message}")
        return None
    except anthropic.APIConnectionError:
        log("Claude: 연결 실패")
        return None
    if res.stop_reason == "refusal":
        log(f"Claude: 거절됨 ({getattr(res.stop_details, 'category', None)})")
        return None
    if res.stop_reason == "max_tokens":
        log("Claude: 출력 한도에 걸려 잘림")
        return None
    text = next((b.text for b in res.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        log("Claude: JSON을 읽지 못함")
        return None


def markdown(doc: dict) -> str:
    w = doc["week"]
    lines = [f"## {ISSUE_TITLE} · {w['from']} ~ {w['to']}", ""]
    ai = doc.get("ai")
    if ai:
        lines += [f"**{ai['headline']}**", ""]
        lines += [f"- **{f['title']}** — {f['detail']}" + (f" (예: {', '.join(f['evidence'][:2])})" if f["evidence"] else "") for f in ai["findings"]]
        lines += ["", "### 기획 아이디어"] + [f"- [{i['for']}] {i['line']} · 썸네일 ‘{i['thumb']}’ — {i['why']}" for i in ai["ideas"]]
        if ai["watch"]:
            lines += ["", "### 다음 주에 지켜볼 것"] + [f"- {x}" for x in ai["watch"]]
        if ai.get("mine"):
            lines += ["", f"**내 채널** {ai['mine']}"]
    else:
        lines.append("_Claude 요약 없이 통계만 정리했습니다 (ANTHROPIC_API_KEY 미설정 또는 호출 실패)._")
    lines += ["", "### 이번 주 터진 영상"]
    lines += [f"- {v['x']}배 · [{v['t']}](https://youtu.be/{v['id']}) — {v['ch']}" for v in w["hot"][:8]] or ["- 없음"]
    lines += ["", "### 제목 공식 (같은 채널 안에서 비교)"]
    for s in doc["scopes"]:
        if s["up"]:
            lines.append(f"- {s['label']}: " + ", ".join(f"{h['label']} {h['lift']}배" for h in s["up"]))
    return "\n".join(lines)


def gh(method: str, path: str, body: dict | None = None) -> dict | list:
    req = urllib.request.Request(
        "https://api.github.com" + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"], "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read() or b"null")


def post_issue(md: str) -> str | None:
    """'주간 인사이트' 이슈(없으면 만든다)에 댓글을 단다. 이슈를 구독하면 GitHub 알림으로 받는다."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not (repo and os.environ.get("GITHUB_TOKEN")) or os.environ.get("DIGEST_ISSUE") == "0":
        return None
    try:
        issues = gh("GET", f"/repos/{repo}/issues?state=open&per_page=100")
        issue = next((i for i in issues if i.get("title") == ISSUE_TITLE and "pull_request" not in i), None)
        if not issue:
            issue = gh("POST", f"/repos/{repo}/issues", {
                "title": ISSUE_TITLE,
                "body": "트렌드 관측소가 매주 이 이슈에 인사이트 요약을 댓글로 남깁니다. 이 이슈를 구독하면 GitHub 알림으로 받습니다. 그만 받으려면 저장소 변수 `DIGEST_ISSUE`를 `0`으로 두세요."})
        c = gh("POST", f"/repos/{repo}/issues/{issue['number']}/comments", {"body": md})
        return c.get("html_url")
    except Exception as e:  # 알림 실패가 데이터 게시를 막지 않게 한다
        log(f"GitHub 이슈 댓글 실패: {e}")
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="주간 인사이트 요약")
    ap.add_argument("--force", action="store_true", help="지난 요약이 최근이어도 새로 만든다")
    ap.add_argument("--now", help="기준 시각 (ISO, 테스트용)")
    args = ap.parse_args(argv)
    now = parse_ts(args.now) if args.now else datetime.now(timezone.utc).replace(microsecond=0)
    prev = load(DATA / "digest.json")
    if prev and not args.force:
        try:
            age = now - parse_ts(prev["generatedAt"])
        except (KeyError, ValueError):
            age = timedelta(days=REFRESH_DAYS)
        if age < timedelta(days=REFRESH_DAYS):
            log(f"지난 요약이 {age.days}일 전이라 건너뜁니다 (--force로 강제)")
            return 0
    refs, farm, mine = load(DATA / "refs.json"), load(DATA / "farm.json"), load(DATA / "mine.json")
    if not refs and not farm:
        log("data/refs.json, data/farm.json이 없어 요약을 만들 수 없습니다")
        return 1
    doc = {"generatedAt": now.isoformat().replace("+00:00", "Z"), **compute(refs, farm, mine, now), "ai": None, "model": None}
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        model = os.environ.get("DIGEST_MODEL") or DEFAULT_MODEL
        doc["ai"] = call_claude(prompt(doc), model)
        doc["model"] = model if doc["ai"] else None
    else:
        log("ANTHROPIC_API_KEY가 없어 통계만 정리합니다")
    url = post_issue(markdown(doc))
    if url:
        doc["issue"] = url
    (DATA / "digest.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    log(f"→ digest.json (이번 주 터진 영상 {len(doc['week']['hot'])}개, Claude 요약 {'있음' if doc['ai'] else '없음'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
