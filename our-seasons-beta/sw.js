/* 우리의 계절 — 서비스 워커
   전략: 같은 출처는 네트워크 우선(항상 최신) + 실패 시 캐시(오프라인),
   Supabase 등 외부 요청은 건드리지 않음 */
importScripts("quotes.js");
/* 베타(-beta 경로)는 캐시도 분리해 정식 앱과 간섭하지 않게 한다 */
const IS_BETA = self.registration.scope.indexOf("-beta") > -1;
const CACHE = (IS_BETA ? "ourseasons-beta-" : "ourseasons-") + "v5";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(["./", "./index.html", "./manifest.json", "./quotes.js", "./icon-192.png", "./icon-512.png"])));
  self.skipWaiting();
});

/* 하루 한 번, 오늘의 사랑 한 줄 알림 (안드로이드 설치형 앱에서 동작) */
self.addEventListener("periodicsync", (e) => {
  if (e.tag === "daily-love-quote") e.waitUntil(showDailyQuote());
});
function showDailyQuote() {
  const q = quoteOfDay(new Date());
  return self.registration.showNotification("💌 오늘의 사랑 한 줄", {
    body: q.t + (q.by ? " — " + q.by : ""),
    icon: "icon-192.png",
    badge: "icon-192.png",
    tag: "daily-quote",
  });
}
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((ws) => {
      for (const w of ws) { if ("focus" in w) return w.focus(); }
      return clients.openWindow("./");
    })
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return; // 동기화(Supabase)는 항상 실시간
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
        return res;
      })
      .catch(() => caches.match(e.request).then((m) => m || caches.match("./index.html")))
  );
});
