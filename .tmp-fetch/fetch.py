"""Temporary: resolve YouTube handles, read channel RSS feeds and download avatars (no API key needed)."""
import json, re, pathlib, urllib.parse, urllib.request, xml.etree.ElementTree as ET, concurrent.futures as cf

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept-Language": "ko-KR,ko;q=0.9", "Cookie": "CONSENT=YES+1; SOCS=CAI"}
out = pathlib.Path(".preview-thumbs"); out.mkdir(exist_ok=True)
inp = json.loads(pathlib.Path(".tmp-fetch/input.json").read_text())

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()

def resolve(handle):
    try:
        html = get("https://www.youtube.com/@" + urllib.parse.quote(handle)).decode("utf-8", "replace")
    except Exception as e:
        return handle, {"error": str(e)}
    m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', html) or re.search(r'"externalId":"(UC[\w-]{22})"', html)
    img = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    title = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    return handle, {"id": m.group(1) if m else None, "thumb": img.group(1) if img else "", "title": title.group(1) if title else ""}

resolved = dict(cf.ThreadPoolExecutor(4).map(resolve, inp["handles"]))
chans = {c["id"]: c["thumb"] for c in inp["channels"]}
for h, r in resolved.items():
    if r.get("id"):
        chans.setdefault(r["id"], r.get("thumb", ""))

NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015", "media": "http://search.yahoo.com/mrss/"}
def feed(cid):
    try:
        root = ET.fromstring(get("https://www.youtube.com/feeds/videos.xml?channel_id=" + cid))
    except Exception as e:
        return cid, {"error": str(e)}
    rows = []
    for e in root.findall("a:entry", NS):
        st = e.find("media:group/media:community/media:statistics", NS)
        rows.append({"id": e.findtext("yt:videoId", namespaces=NS), "t": e.findtext("a:title", namespaces=NS),
                     "p": e.findtext("a:published", namespaces=NS), "v": int(st.get("views")) if st is not None else None,
                     "short": "/shorts/" in (e.find("a:link", NS).get("href") if e.find("a:link", NS) is not None else "")})
    return cid, {"recent": rows}

feeds = dict(cf.ThreadPoolExecutor(8).map(feed, list(chans)))

def avatar(kv):
    cid, url = kv
    if not url:
        return
    try:
        (out / f"{cid}.jpg").write_bytes(get(url))
    except Exception as e:
        return f"{cid} {e}"
errs = [e for e in cf.ThreadPoolExecutor(8).map(avatar, chans.items()) if e]
(out / "agri.json").write_text(json.dumps({"resolved": resolved, "feeds": feeds, "avatar_errors": errs}, ensure_ascii=False, indent=1))
print(json.dumps({h: r.get("id") for h, r in resolved.items()}, ensure_ascii=False), sum(1 for f in feeds.values() if f.get("recent")), "feeds ok", len(errs), "avatar errors")
