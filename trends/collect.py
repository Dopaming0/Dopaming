#!/usr/bin/env python3
"""해외 트렌드 관측소 데이터 수집기.

YouTube Data API v3로 국가별 인기 급상승·쇼츠·카테고리 차트, 국내 농산물 채널,
기획 레퍼런스 채널 현황을 모아 trends/data/ 의 latest.json, farm.json, refs.json 으로
저장한다. IG_USER_ID·IG_ACCESS_TOKEN이 있으면 Instagram Graph API의 비즈니스
디스커버리로 농산물 인스타그램 계정도 함께 채운다.
표준 라이브러리만 사용한다.

    YOUTUBE_API_KEY=... python trends/collect.py

하루 쿼터(기본 10,000 units) 대비 1회 실행 사용량은 대략 이렇다.
    인기 차트   12개국 × (전체 1 + 카테고리 15)   ≈   190
    쇼츠 검색   12개국 × search 100              = 1,200
    농업 채널   70채널 × (업로드 목록 1 + 영상 1)  ≈   140
    채널 발굴   키워드 8개 × search 100           =   800
    레퍼런스    21채널 × (업로드 목록 1 + 영상 1)  ≈    45
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

API = "https://www.googleapis.com/youtube/v3/"
IG_API = "https://graph.facebook.com/v21.0/"
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

# (코드, 이름, 그룹, 검색 언어). lead = 숏폼 포맷·밈이 한국보다 먼저 도는 시장
REGIONS = [
    ("US", "미국", "lead", "en"),
    ("GB", "영국", "lead", "en"),
    ("JP", "일본", "lead", "ja"),
    ("CA", "캐나다", "lead", "en"),
    ("AU", "호주", "lead", "en"),
    ("DE", "독일", "global", "de"),
    ("FR", "프랑스", "global", "fr"),
    ("BR", "브라질", "global", "pt"),
    ("MX", "멕시코", "global", "es"),
    ("IN", "인도", "global", "hi"),
    ("ID", "인도네시아", "global", "id"),
    ("KR", "한국", "base", "ko"),
]

# videoCategories.list(regionCode=US)의 assignable 카테고리
CATEGORIES = {
    "1": "영화·애니",
    "2": "자동차",
    "10": "음악",
    "15": "동물",
    "17": "스포츠",
    "19": "여행",
    "20": "게임",
    "22": "인물·블로그",
    "23": "코미디",
    "24": "엔터테인먼트",
    "25": "뉴스·정치",
    "26": "노하우·스타일",
    "27": "교육",
    "28": "과학기술",
    "29": "비영리",
}

# 국내 농업 채널 자동 발굴용 검색어 (search.list type=channel)
FARM_KEYWORDS = [
    "귀농 브이로그",
    "농사 수익 공개",
    "과일 산지직송",
    "농산물 직거래",
    "과수원 농부",
    "청년농부",
    "감귤 농장",
    "딸기 농장",
]

SHORT_MAX_SEC = 180  # 2024-10 이후 쇼츠 최대 길이
SHORTS_WINDOW_H = 48
FARM_RECENT = 15
REF_RECENT = 30
IG_RECENT = 15
DISCOVER_MIN_SUBS = 1000


class ApiError(Exception):
    def __init__(self, status: int, body: str):
        try:
            err = json.loads(body)["error"]
            # YouTube: errors[0].reason / Graph API: code·error_subcode
            self.reason = (err.get("errors") or [{}])[0].get("reason", "") or str(err.get("code", ""))
            message = err.get("message", "")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            self.reason, message = "", body[:200]
        super().__init__(f"HTTP {status} {self.reason}: {message}")


_quota_out = False


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def api(endpoint: str, **params) -> dict:
    params["key"] = os.environ["YOUTUBE_API_KEY"]
    return fetch_json(API + endpoint + "?" + urllib.parse.urlencode(params))


def fetch_json(url: str) -> dict:
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as res:
                return json.load(res)
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < 2:
                time.sleep(2**attempt)
                continue
            raise ApiError(e.code, e.read().decode("utf-8", "replace")) from None
        except urllib.error.URLError:
            if attempt < 2:
                time.sleep(2**attempt)
                continue
            raise
    raise RuntimeError("unreachable")


def optional(fn, *args, **kwargs):
    """보조 호출. 실패해도 수집을 계속하고, 쿼터가 바닥나면 이후 보조 호출은 건너뛴다."""
    global _quota_out
    if _quota_out:
        return None
    try:
        return fn(*args, **kwargs)
    except ApiError as e:
        log(f"  skip {fn.__name__}{args}: {e}")
        if e.reason in ("quotaExceeded", "dailyLimitExceeded"):
            _quota_out = True
        return None


def chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


_DUR = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")


def parse_duration(iso: str) -> int:
    m = _DUR.fullmatch(iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


_YT3 = re.compile(r"^https://yt3\.(?:ggpht|googleusercontent)\.com/(.+?)=s\d+.*$")


def thumb_key(url: str) -> str:
    """채널 아바타 URL을 페이지가 다시 조립할 수 있는 짧은 키로 줄인다."""
    m = _YT3.match(url or "")
    return m.group(1) if m else (url or "")


def norm_video(item: dict) -> dict:
    sn, st = item["snippet"], item.get("statistics", {})
    return {
        "t": sn["title"],
        "c": sn["channelTitle"].strip(),
        "ci": sn["channelId"],
        "p": sn["publishedAt"],
        "d": parse_duration(item.get("contentDetails", {}).get("duration", "")),
        "v": int(st.get("viewCount", 0)),
        "l": int(st.get("likeCount", 0)),
        "m": int(st.get("commentCount", 0)),
        "cat": sn.get("categoryId", ""),
    }


def videos_by_ids(ids: list[str]) -> dict[str, dict]:
    out = {}
    for chunk in chunks(list(dict.fromkeys(ids)), 50):
        res = api("videos", part="snippet,statistics,contentDetails", id=",".join(chunk), maxResults=50)
        for it in res.get("items", []):
            out[it["id"]] = norm_video(it)
    return out


def chart(region: str, category: str | None = None, limit: int = 50) -> list[tuple[str, dict]]:
    params = dict(part="snippet,statistics,contentDetails", chart="mostPopular", regionCode=region, maxResults=limit)
    if category:
        params["videoCategoryId"] = category
    return [(it["id"], norm_video(it)) for it in api("videos", **params).get("items", [])]


def shorts(region: str, lang: str, limit: int = 25) -> list[tuple[str, dict]]:
    after = (datetime.now(timezone.utc) - timedelta(hours=SHORTS_WINDOW_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    res = api(
        "search",
        part="id",
        type="video",
        q="#shorts",
        videoDuration="short",
        order="viewCount",
        regionCode=region,
        relevanceLanguage=lang,
        publishedAfter=after,
        maxResults=limit,
    )
    ids = [it["id"]["videoId"] for it in res.get("items", [])]
    vids = videos_by_ids(ids)
    return [(i, vids[i]) for i in ids if i in vids and 0 < vids[i]["d"] <= SHORT_MAX_SEC]


def channels(ids) -> dict[str, dict]:
    out = {}
    for chunk in chunks(sorted(set(ids)), 50):
        res = api("channels", part="snippet,statistics", id=",".join(chunk), maxResults=50)
        for it in res.get("items", []):
            sn, st = it["snippet"], it.get("statistics", {})
            out[it["id"]] = {
                "title": sn["title"],
                "handle": sn.get("customUrl", ""),
                "since": sn["publishedAt"][:10],
                "thumb": thumb_key(sn.get("thumbnails", {}).get("default", {}).get("url", "")),
                "subs": None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0)),
                "views": int(st.get("viewCount", 0)),
                "videos": int(st.get("videoCount", 0)),
            }
    return out


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_trends(charts: dict, videos: dict, subs: dict, prev: dict, source: str, generated_at: str | None = None) -> dict:
    """charts: {국가: {"all": [id], "shorts": [id], "cat": {카테고리: [id]}}}"""
    used = set()
    for c in charts.values():
        used.update(c.get("all", []), c.get("shorts", []))
        for ids in c.get("cat", {}).values():
            used.update(ids)
    out_videos = {}
    for vid in sorted(used):
        v = dict(videos[vid])
        s = subs.get(v["ci"])
        if s is not None:
            v["s"] = s
        out_videos[vid] = v
    return {
        "generatedAt": generated_at or now_iso(),
        "source": source,
        "regions": [{"code": c, "name": n, "group": g} for c, n, g, _ in REGIONS],
        "categories": CATEGORIES,
        "videos": out_videos,
        "charts": charts,
        "prev": {r: prev[r] for r in charts if prev.get(r)},
    }


def collect_trends(prev_doc: dict | None) -> dict:
    videos: dict[str, dict] = {}
    charts: dict[str, dict] = {}
    for code, name, _, lang in REGIONS:
        rc = {"all": [], "shorts": [], "cat": {}}
        for vid, v in chart(code):
            videos[vid] = v
            rc["all"].append(vid)
        for cid in CATEGORIES:
            rows = optional(chart, code, cid, 20)
            if rows:
                videos.update(rows)
                rc["cat"][cid] = [vid for vid, _ in rows]
        rows = optional(shorts, code, lang)
        if rows:
            videos.update(rows)
            rc["shorts"] = [vid for vid, _ in rows]
        charts[code] = rc
        log(f"{code} {name}: 인기 {len(rc['all'])} · 쇼츠 {len(rc['shorts'])} · 카테고리 {len(rc['cat'])}")
    subs = {cid: c["subs"] for cid, c in (optional(channels, {v["ci"] for v in videos.values()}) or {}).items()}
    prev = {r: c.get("all", []) for r, c in (prev_doc or {}).get("charts", {}).items()}
    return build_trends(charts, videos, subs, prev, "YouTube Data API v3")


def recent_uploads(channel_id: str, limit: int = FARM_RECENT) -> list[tuple[str, dict]]:
    res = api("playlistItems", part="contentDetails", playlistId="UU" + channel_id[2:], maxResults=limit)
    ids = [it["contentDetails"]["videoId"] for it in res.get("items", [])]
    vids = videos_by_ids(ids)
    return [(i, vids[i]) for i in ids if i in vids]


def is_short(v: dict) -> bool:
    return 0 < v["d"] <= SHORT_MAX_SEC


def with_outliers(rows: list[tuple[str, dict]], by_format: bool = False) -> tuple[int, list[dict]]:
    """채널 최근 업로드의 중앙값 대비 배수(x)를 붙인다. x가 클수록 채널 평소보다 터진 영상.

    by_format이면 쇼츠는 쇼츠끼리, 롱폼은 롱폼끼리 중앙값을 따로 잡는다. 한 형식이
    3개 미만이면 비교할 기준이 없으므로 x를 None으로 둔다.
    """
    base = int(median(v["v"] for _, v in rows)) if rows else 0
    bases = {}
    if by_format:
        for fmt in (True, False):
            views = [v["v"] for _, v in rows if is_short(v) == fmt]
            if len(views) >= 3:
                bases[fmt] = int(median(views))
    out = []
    for vid, v in rows:
        ref = bases.get(is_short(v)) if by_format else base
        x = round(v["v"] / max(ref, 1), 1) if ref is not None else None
        row = {"id": vid, "t": v["t"], "p": v["p"], "d": v["d"], "v": v["v"], "x": x}
        if "m" in v:
            row["m"] = v["m"]
        out.append(row)
    return base, out


def build_farm(channels_out: list[dict], discovered: list[dict], source: str, generated_at: str | None = None,
               instagram: dict | None = None) -> dict:
    doc = {
        "generatedAt": generated_at or now_iso(),
        "source": source,
        "channels": channels_out,
        "discovered": discovered,
    }
    if instagram is not None:
        doc["instagram"] = instagram
    return doc


def ig_account(user_id: str, token: str, handle: str) -> dict:
    """비즈니스 디스커버리로 다른 비즈니스·크리에이터 계정의 공개 지표를 읽는다.

    릴스 조회수는 이 API로 받을 수 없어서, 게시물 반응(좋아요 + 댓글)을 계정 최근
    게시물의 중앙값과 비교한다. 좋아요 수를 숨긴 게시물은 댓글만 센다.
    """
    fields = (
        f"business_discovery.username({handle})"
        "{username,name,followers_count,media_count,profile_picture_url,"
        f"media.limit({IG_RECENT}){{caption,media_type,media_product_type,like_count,comments_count,timestamp,permalink}}}}"
    )
    url = IG_API + user_id + "?" + urllib.parse.urlencode({"fields": fields, "access_token": token})
    bd = fetch_json(url)["business_discovery"]
    posts = []
    for m in bd.get("media", {}).get("data", []):
        caption = (m.get("caption") or "").strip().splitlines()
        posts.append({
            "id": m["id"],
            "t": caption[0][:100] if caption else "",
            "p": (m.get("timestamp") or "").replace("+0000", "Z"),
            "type": m.get("media_product_type") or m.get("media_type") or "",
            "l": m.get("like_count"),
            "m": m.get("comments_count", 0),
            "url": m.get("permalink", ""),
        })
    eng = [(p["l"] or 0) + (p["m"] or 0) for p in posts]
    base = int(median(eng)) if eng else 0
    for p, e in zip(posts, eng):
        p["x"] = round(e / max(base, 1), 1)
    return {
        "name": bd.get("name") or handle,
        "followers": bd.get("followers_count"),
        "posts": bd.get("media_count"),
        "pic": bd.get("profile_picture_url", ""),
        "median": base,
        "recent": posts,
    }


def collect_instagram(curated: list[dict]) -> dict:
    user_id, token = os.environ.get("IG_USER_ID"), os.environ.get("IG_ACCESS_TOKEN")
    out = []
    for a in curated:
        acc = dict(a)
        if user_id and token:
            try:
                acc.update(ig_account(user_id, token, a["handle"]))
            except (ApiError, urllib.error.URLError, KeyError) as e:
                acc["error"] = str(e)[:160]
                log(f"  instagram @{a['handle']}: {acc['error']}")
        out.append(acc)
    if user_id and token:
        log(f"인스타그램 계정 {sum(1 for a in out if 'error' not in a)}/{len(out)}개 갱신")
    return {"api": bool(user_id and token), "accounts": out}


def discover_farm(known: set[str]) -> list[dict]:
    found: dict[str, str] = {}
    for kw in FARM_KEYWORDS:
        res = optional(api, "search", part="snippet", type="channel", q=kw, regionCode="KR", relevanceLanguage="ko", maxResults=25)
        for it in (res or {}).get("items", []):
            cid = it["snippet"]["channelId"]
            if cid not in known:
                found.setdefault(cid, kw)
    info = optional(channels, found) or {}
    picked = [{"id": cid, "kw": kw, **info[cid]} for cid, kw in found.items() if (info.get(cid, {}).get("subs") or 0) >= DISCOVER_MIN_SUBS]
    return sorted(picked, key=lambda c: -c["subs"])


def watch_channels(curated: list[dict], limit: int, by_format: bool = False) -> list[dict]:
    """목록의 채널 통계와 최근 업로드(배수 포함)를 채운다."""
    info = channels(c["id"] for c in curated)
    out = []
    for c in curated:
        ch = {**c, **info.get(c["id"], {})}
        rows = optional(recent_uploads, c["id"], limit)
        if rows:
            ch["median"], ch["recent"] = with_outliers(rows, by_format)
        out.append(ch)
    return out


def collect_farm(curated: list[dict], discover: bool) -> dict:
    out = watch_channels(curated, FARM_RECENT)
    log(f"농업 채널 {len(out)}개 갱신")
    found = discover_farm({c["id"] for c in curated}) if discover else []
    if found:
        log(f"자동 발굴 후보 {len(found)}개")
    accounts = (load_json(HERE / "farm_instagram.json") or {}).get("accounts", [])
    return build_farm(out, found, "YouTube Data API v3", instagram=collect_instagram(accounts))


def build_refs(channels_out: list[dict], groups: dict, source: str, generated_at: str | None = None) -> dict:
    return {
        "generatedAt": generated_at or now_iso(),
        "source": source,
        "groups": groups,
        "channels": channels_out,
    }


def collect_refs(doc: dict) -> dict:
    out = watch_channels(doc.get("channels", []), REF_RECENT, by_format=True)
    log(f"레퍼런스 채널 {len(out)}개 갱신")
    return build_refs(out, doc.get("groups", {}), "YouTube Data API v3")


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def write_json(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    log(f"→ {path.name} ({path.stat().st_size // 1024} KB)")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="해외 트렌드 관측소 데이터 수집기")
    ap.add_argument("--prev", type=Path, default=DATA / "latest.json", help="NEW·순위 변동 비교용 이전 스냅샷")
    ap.add_argument("--only", choices=["trends", "farm", "refs"], help="하나만 수집")
    ap.add_argument("--no-discover", action="store_true", help="농업 채널 자동 발굴 생략 (search 쿼터 절약)")
    args = ap.parse_args(argv)
    if not os.environ.get("YOUTUBE_API_KEY"):
        sys.exit("YOUTUBE_API_KEY 환경변수가 필요합니다.")
    DATA.mkdir(exist_ok=True)
    if args.only in (None, "trends"):
        write_json(DATA / "latest.json", collect_trends(load_json(args.prev)))
    if args.only in (None, "farm"):
        curated = (load_json(HERE / "farm_channels.json") or {}).get("channels", [])
        write_json(DATA / "farm.json", collect_farm(curated, discover=not args.no_discover))
    if args.only in (None, "refs"):
        write_json(DATA / "refs.json", collect_refs(load_json(HERE / "ref_channels.json") or {}))


if __name__ == "__main__":
    main()
