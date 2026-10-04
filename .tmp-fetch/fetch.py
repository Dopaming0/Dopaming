"""Temporary: find channel IDs from YouTube search pages and download video thumbnails (no API key needed)."""
import json, re, pathlib, urllib.parse, urllib.request, concurrent.futures as cf

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept-Language": "ko-KR,ko;q=0.9", "Cookie": "CONSENT=YES+1; SOCS=CAI"}
out = pathlib.Path(".preview-thumbs"); out.mkdir(exist_ok=True)
inp = json.loads(pathlib.Path(".tmp-fetch/input.json").read_text())

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()

CH = re.compile(r'"channelRenderer":\{"channelId":"(UC[\w-]{22})","title":\{"simpleText":"((?:[^"\\]|\\.)*)"')
def search(q):
    try:
        html = get("https://www.youtube.com/results?sp=EgIQAg%253D%253D&search_query=" + urllib.parse.quote(q)).decode("utf-8", "replace")
    except Exception as e:
        return q, {"error": str(e)}
    return q, {"hits": [{"id": m.group(1), "title": json.loads('"%s"' % m.group(2))} for m in CH.finditer(html)][:4]}

found = dict(cf.ThreadPoolExecutor(4).map(search, inp["queries"]))

def thumb(name_url):
    name, url = name_url
    try:
        (out / name).write_bytes(get(url))
    except Exception as e:
        return f"{name} {e}"
jobs = {f"{v}.jpg": f"https://i.ytimg.com/vi/{v}/mqdefault.jpg" for v in inp["videos"]}
jobs.update({f"{c}.jpg": u for c, u in inp.get("avatars", {}).items()})
errs = [e for e in cf.ThreadPoolExecutor(16).map(thumb, jobs.items()) if e]
(out / "search.json").write_text(json.dumps({"found": found, "thumb_errors": errs}, ensure_ascii=False, indent=1))
print(json.dumps({q: [h["title"] for h in r.get("hits", [])] for q, r in found.items()}, ensure_ascii=False), len(jobs) - len(errs), "thumbs ok")
