// Service Worker — نسخة GitHub Pages (leroiceramique.github.io)
// نفس منطق نسخة الخادم: الصفحات شبكة أولاً + API مرور مباشر بلا تخزين.
const CACHE = "ceram-pwa-gp-v20";   /* v148: سجل نظيف بلا ✕ + إخفاء تصحيح المخزون — تجديد الغلاف */
const SHELL = ["/", "/icon.jpg", "/manifest.webmanifest"];
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
  ).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  /* تنزيل فاتورة PDF يُسلَّم للمتصفح الأصلي (أنبوب التنزيل المضمون — v100) */
  if (url.pathname === "/api/invoice/pdf") return;
  if (url.pathname.startsWith("/api/")) {
    e.respondWith(fetch(e.request).catch(() =>
      new Response(JSON.stringify({ok: false, error: "لا يوجد اتصال — أعد المحاولة عند رجوع الشبكة."}),
        {status: 503, headers: {"Content-Type": "application/json"}})));
    return;
  }
  // صفحات التطبيق: شبكة أولاً دائماً — التحديثات تظهر من أول تحديث، وعند الانقطاع آخر نسخة.
  if (e.request.mode === "navigate" || url.pathname === "/") {
    e.respondWith(
      fetch(e.request).then((res) => {
        if (res && res.status === 200 && url.origin === location.origin) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      }).catch(() => caches.match(e.request).then((h) => h || caches.match("/")))
    );
    return;
  }
  e.respondWith(
    caches.match(e.request).then((hit) => {
      const net = fetch(e.request).then((res) => {
        if (res && res.status === 200 && url.origin === location.origin) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      }).catch(() => hit);
      return hit || net;
    })
  );
});
/* v143: إشعارات الهاتف الخارجية — استقبال التنبيه الجماعي من الإدارة
   حتى والتطبيق مغلق، والضغط على الإشعار يفتح التطبيق. */
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) {
    try { d = { title: "🔔 تنبيه من الإدارة", body: e.data ? e.data.text() : "" }; } catch (err2) { d = {}; }
  }
  const title = d.title || "🔔 تنبيه من الإدارة";
  e.waitUntil(self.registration.showNotification(title, {
    body: d.body || "",
    tag: d.tag || "ceram-broadcast",
    dir: "rtl",
    lang: "ar",
    icon: "/icon.jpg",
    badge: "/icon.jpg",
    renotify: true,
    vibrate: [140, 70, 140],
    data: { url: d.url || "/" }
  }));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil((async () => {
    let target = null;
    try {
      const cs = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const c of cs) {
        try { if ("focus" in c) { target = c; break; } } catch (err) {}
      }
    } catch (err) {}
    if (target) { try { return target.focus(); } catch (err) {} }
    try { return await self.clients.openWindow(url); } catch (err) {}
  })());
});

