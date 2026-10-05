#!/usr/bin/env python3
"""영상 요약: data/*.json에 나온 영상마다 2~3줄 한국어 요약을 붙여 data/summaries.json에 쌓는다.

    YOUTUBE_API_KEY=... ANTHROPIC_API_KEY=... python trends/summarize.py
    python trends/summarize.py --sync      # 배치 대신 바로 호출(로컬 확인용)
    python trends/summarize.py --dry-run   # 무엇을 요약할지만 보여 준다

- 새 영상만 요약한다. 이미 요약한 영상은 다시 부르지 않고, 데이터에서 빠진 영상의 요약은 지운다.
- 입력은 제목·설명·태그·인기 댓글(YouTube Data API, 영상 50개당 1 unit + 영상마다 댓글 1 unit).
  자막은 클라우드 IP에서 막혀 쓰지 않는다.
- 외국어 제목에는 한국어 제목도 붙인다.
- CLAUDE.md 정책: 대량 저난이도 배치 → Haiku 4.5 + Message Batches API(50% 할인). SUMMARY_MODEL로 바꿀 수 있다.
- 배치는 최대 --wait초 기다리고, 안 끝나면 배치 ID를 저장해 다음 실행에서 결과를 가져온다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect import ApiError, api, chunks, load_json, log, write_json  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = DATA / "summaries.json"
DEFAULT_MODEL = "claude-haiku-4-5"
HOT_X = 3
PER_REQUEST = 15     # 요청 하나에 묶는 영상 수
MAX_NEW = 600        # 한 번에 새로 요약할 최대 영상 수(쿼터·비용 상한)
DESC_MAX = 1200
COMMENTS = 8
COMMENT_MAX = 200
PENDING_TTL_H = 48   # 배치는 최대 24시간. 이보다 오래된 대기 배치는 버리고 다시 보낸다
SOURCE = "제목·설명·태그·인기 댓글 요약"

SYSTEM = """너는 유튜브 콘텐츠 기획자를 돕는 리서처다. 기획자가 영상을 열지 않고도 무슨 내용인지 알 수 있게 영상마다 한국어로 요약한다.

- summary: 2~3문장, 합쳐 130자 안팎, "~한다" 체. 첫 문장은 영상에서 실제로 무슨 일이 벌어지는지(누가, 무엇을, 어떻게). 제목을 되풀이하지 말고 제목에 없는 정보(구성, 등장인물, 결과, 숫자)를 앞세운다. 인기 댓글에서 시청자가 반응한 지점이 보이면 마지막 문장을 "반응:"으로 시작해 붙인다.
- 설명·태그·댓글에 근거가 없는 내용은 지어내지 않는다. 내용을 확인할 수 없으면 제목과 채널에서 알 수 있는 만큼만 쓰고 guess를 true로 한다.
- 광고, 링크, 구독 요청, SNS 안내, 협찬 문구는 무시한다.
- title_ko: 제목이 한국어가 아니면 뜻이 통하는 자연스러운 한국어 제목(40자 이내). 한국어 제목이면 빈 문자열.
- 받은 id를 그대로 돌려준다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "summary": {"type": "string"},
                    "title_ko": {"type": "string"},
                    "guess": {"type": "boolean"},
                },
                "required": ["id", "summary", "title_ko", "guess"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

HANGUL = re.compile(r"[가-힣]")
URL = re.compile(r"https?://\S+|www\.\S+")


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


# ── 대상 영상: 페이지에 카드로 나오는 모든 영상. 터진 영상·한국 미상륙·내 채널을 먼저 한다
def candidates(trends: dict | None, refs: dict | None, farm: dict | None, mine: dict | None) -> list[dict]:
    out: dict[str, dict] = {}

    def add(vid: str, title: str, ch: str, dur: int, published: str, pri: int) -> None:
        cur = out.get(vid)
        if cur is None or pri > cur["pri"]:
            out[vid] = {"id": vid, "t": title, "ch": ch, "d": dur or 0, "p": published or "", "pri": pri}

    for doc, own in ((refs, False), (farm, False), (mine, True)):
        for ch in (doc or {}).get("channels", []):
            for r in ch.get("recent", []) or []:
                hot = r.get("x") is not None and r["x"] >= HOT_X
                add(r["id"], r.get("t", ""), ch.get("title", ""), r.get("d", 0), r.get("p", ""), 4 if own else 3 if hot else 1)
    if trends:
        lead = {r["code"] for r in trends.get("regions", []) if r.get("group") == "lead"}
        where: dict[str, set] = {}
        for code, c in (trends.get("charts") or {}).items():
            ids = set(c.get("all", [])) | set(c.get("shorts", []))
            for lst in (c.get("cat") or {}).values():
                ids |= set(lst)
            for vid in ids:
                where.setdefault(vid, set()).add(code)
        for vid, v in (trends.get("videos") or {}).items():
            w = where.get(vid, set())
            pri = 2 if "KR" not in w and w & lead else 1
            add(vid, v.get("t", ""), v.get("c", ""), v.get("d", 0), v.get("p", ""), pri)
    return sorted(out.values(), key=lambda c: (c["pri"], c["p"]), reverse=True)


# ── YouTube 메타데이터
def clean_desc(s: str) -> str:
    s = URL.sub("", s or "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s).strip()
    return s[:DESC_MAX]


def fetch_meta(ids: list[str]) -> dict[str, dict]:
    out = {}
    for chunk in chunks(ids, 50):
        try:
            res = api("videos", part="snippet", id=",".join(chunk), maxResults=50)
        except ApiError as e:
            log(f"  skip videos meta: {e}")
            if e.reason in ("quotaExceeded", "dailyLimitExceeded"):
                break
            continue
        for it in res.get("items", []):
            sn = it.get("snippet", {})
            out[it["id"]] = {"desc": clean_desc(sn.get("description", "")), "tags": (sn.get("tags") or [])[:15]}
    return out


def fetch_comments(ids: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for vid in ids:
        try:
            res = api("commentThreads", part="snippet", videoId=vid, maxResults=COMMENTS,
                      order="relevance", textFormat="plainText")
        except ApiError as e:
            if e.reason in ("quotaExceeded", "dailyLimitExceeded"):
                log(f"  quota out while reading comments ({len(out)} done)")
                break
            continue  # 댓글 막힘·삭제 영상
        texts = []
        for it in res.get("items", []):
            t = it.get("snippet", {}).get("topLevelComment", {}).get("snippet", {}).get("textDisplay", "")
            t = re.sub(r"\s+", " ", URL.sub("", t)).strip()
            if t:
                texts.append(t[:COMMENT_MAX])
        out[vid] = texts
    return out


# ── 프롬프트
def fmt_dur(sec: int) -> str:
    if not sec:
        return "라이브/알 수 없음"
    m, s = divmod(sec, 60)
    return f"{m}분 {s}초" if m else f"{s}초"


def video_block(i: int, c: dict, meta: dict, comments: list[str]) -> str:
    lines = [f"[{i}] id: {c['id']}",
             f"채널: {c['ch']} | 길이: {fmt_dur(c['d'])} | 업로드: {c['p'][:10]}",
             f"제목: {c['t']}"]
    if meta.get("desc"):
        lines.append("설명: " + meta["desc"].replace("\n", " / "))
    if meta.get("tags"):
        lines.append("태그: " + ", ".join(meta["tags"]))
    if comments:
        lines.append("인기 댓글:")
        lines += [f"- {t}" for t in comments]
    return "\n".join(lines)


def build_params(group: list[dict], metas: dict, comments: dict, model: str) -> dict:
    body = "\n\n".join(video_block(i + 1, c, metas.get(c["id"], {}), comments.get(c["id"], []))
                       for i, c in enumerate(group))
    return {
        "model": model,
        "max_tokens": 8000,
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
        "messages": [{"role": "user", "content": f"영상 {len(group)}개를 요약해라.\n\n{body}"}],
    }


def parse_message(msg, wanted: dict[str, dict]) -> dict[str, dict]:
    """응답 하나를 {id: 요약 항목}으로. 거절·잘림·형식 오류는 버리고 다음 실행에서 다시 한다."""
    if getattr(msg, "stop_reason", None) in ("refusal", "max_tokens"):
        log(f"  dropped response: stop_reason={msg.stop_reason}")
        return {}
    text = next((b.text for b in msg.content if getattr(b, "type", "") == "text"), "")
    try:
        items = json.loads(text)["items"]
    except (ValueError, KeyError, TypeError):
        log("  dropped response: invalid JSON")
        return {}
    today = now().strftime("%Y-%m-%d")
    out = {}
    for it in items:
        vid = str(it.get("id", ""))
        c = wanted.get(vid)
        s = re.sub(r"\s+", " ", str(it.get("summary", ""))).strip()
        if not c or not s:
            continue
        row = {"s": s[:260], "at": today}
        ko = re.sub(r"\s+", " ", str(it.get("title_ko", ""))).strip()
        if ko and not HANGUL.search(c["t"]):
            row["k"] = ko[:80]
        if it.get("guess"):
            row["g"] = 1
        out[vid] = row
    return out


# ── Claude 호출
def run_sync(client, groups: list[list[dict]], metas, comments, model) -> dict[str, dict]:
    import anthropic

    out = {}
    for g in groups:
        try:
            msg = client.messages.create(**build_params(g, metas, comments, model))
        except anthropic.RateLimitError:
            log("  rate limited, stopping early")
            break
        except anthropic.APIStatusError as e:
            log(f"  API error {e.status_code}: {e.message}")
            if e.status_code < 500:
                break
            continue
        except anthropic.APIConnectionError:
            log("  connection error, stopping early")
            break
        out.update(parse_message(msg, {c["id"]: c for c in g}))
    return out


def submit_batch(client, groups: list[list[dict]], metas, comments, model) -> dict | None:
    import anthropic

    reqs, mapping = [], {}
    for i, g in enumerate(groups):
        cid = f"g{i:04d}"
        reqs.append({"custom_id": cid, "params": build_params(g, metas, comments, model)})
        mapping[cid] = [{"id": c["id"], "t": c["t"]} for c in g]
    try:
        batch = client.messages.batches.create(requests=reqs)
    except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        log(f"  batch create failed: {e}")
        return None
    log(f"  batch {batch.id}: {len(reqs)} requests, {sum(len(g) for g in groups)} videos")
    return {"id": batch.id, "at": iso(now()), "model": model, "groups": mapping}


def collect_batch(client, pending: dict, wait: int, poll: int = 30) -> tuple[str, dict[str, dict]]:
    """('ended'|'running'|'gone', 결과). wait초까지 기다린다."""
    import anthropic

    deadline = time.monotonic() + wait
    while True:
        try:
            batch = client.messages.batches.retrieve(pending["id"])
        except anthropic.NotFoundError:
            return "gone", {}
        except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            log(f"  batch retrieve failed: {e}")
            return "running", {}
        if batch.processing_status == "ended":
            break
        if time.monotonic() >= deadline:
            return "running", {}
        time.sleep(poll)
    out = {}
    try:
        for res in client.messages.batches.results(pending["id"]):
            group = pending["groups"].get(res.custom_id, [])
            if res.result.type == "succeeded":
                out.update(parse_message(res.result.message, {c["id"]: c for c in group}))
            else:
                log(f"  {res.custom_id}: {res.result.type}")
    except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        log(f"  batch results failed: {e}")
        return "running", {}
    return "ended", out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="영상 2~3줄 요약")
    ap.add_argument("--sync", action="store_true", help="배치 대신 바로 호출")
    ap.add_argument("--limit", type=int, default=MAX_NEW, help=f"한 번에 새로 요약할 최대 영상 수 (기본 {MAX_NEW})")
    ap.add_argument("--wait", type=int, default=900, help="배치 결과를 기다릴 최대 초 (기본 900)")
    ap.add_argument("--dry-run", action="store_true", help="요약할 영상만 보여 주고 끝낸다")
    args = ap.parse_args(argv)

    docs = {n: load_json(DATA / f"{n}.json") for n in ("latest", "refs", "farm", "mine")}
    cands = candidates(docs["latest"], docs["refs"], docs["farm"], docs["mine"])
    if not cands:
        log("no videos in data/*.json")
        return 1
    store = load_json(OUT) or {}
    keep = {c["id"] for c in cands}
    items = {k: v for k, v in (store.get("items") or {}).items() if k in keep}
    pending = store.get("pending")
    model = os.environ.get("SUMMARY_MODEL") or DEFAULT_MODEL
    added = 0

    def merge(got: dict) -> None:
        nonlocal added
        items.update(got)
        added += len(got)

    def save() -> None:
        doc = {"generatedAt": iso(now()), "model": model if added else store.get("model"),
               "source": SOURCE if added and not str(store.get("source", "")).startswith("시드") else store.get("source", SOURCE),
               "items": items}
        if added and str(store.get("source", "")).startswith("시드"):
            doc["source"] = SOURCE + " (일부 시드)"
        if pending:
            doc["pending"] = pending
        write_json(OUT, doc)

    def to_do() -> list[dict]:
        busy = {x["id"] for g in (pending or {}).get("groups", {}).values() for x in g}
        return [c for c in cands if c["id"] not in items and c["id"] not in busy][: max(args.limit, 0)]

    todo = to_do()
    log(f"summaries: {len(items)} kept, {len(todo)} to do, pending batch: {pending['id'] if pending else '-'}")
    if args.dry_run:
        for c in todo[:40]:
            log(f"  [{c['pri']}] {c['id']} {c['ch']} · {c['t'][:60]}")
        return 0
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("ANTHROPIC_API_KEY 없음: 요약을 건너뜁니다 (빠진 영상 정리만)")
        save()
        return 0
    import anthropic

    client = anthropic.Anthropic()
    if pending:
        state, got = collect_batch(client, pending, args.wait)
        age_h = (now() - (parse_iso(pending.get("at")) or now())).total_seconds() / 3600
        if state == "running" and age_h <= PENDING_TTL_H:
            log(f"  batch {pending['id']} still running ({age_h:.1f}h)")
            save()
            return 0
        log(f"  batch {pending['id']}: {state}, +{len(got)}")
        merge(got)
        pending = None
        todo = to_do()
    if not todo:
        save()
        return 0
    if not os.environ.get("YOUTUBE_API_KEY"):
        log("YOUTUBE_API_KEY 없음: 설명·댓글 없이 제목만으로는 요약하지 않습니다")
        save()
        return 0
    ids = [c["id"] for c in todo]
    metas = fetch_meta(ids)
    comments = fetch_comments(ids)
    groups = list(chunks(todo, PER_REQUEST))
    if args.sync:
        merge(run_sync(client, groups, metas, comments, model))
    else:
        pending = submit_batch(client, groups, metas, comments, model)
        if pending:
            state, got = collect_batch(client, pending, args.wait)
            if state != "running":
                log(f"  batch {pending['id']}: {state}, +{len(got)}")
                merge(got)
                pending = None
    save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
