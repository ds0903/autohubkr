// Спокійна поява блоків зі скролом. Без цього файлу сторінка працює так само, лише без анімації:
// base.html через 3 секунди знімає клас .js, якщо скрипт не запустився, і все стає видимим.
(function () {
  "use strict";
  window.motionReady = true;
  var root = document.documentElement;
  var items = Array.prototype.slice.call(document.querySelectorAll("[data-reveal]"));
  if (!items.length) return;

  function settle(el) {
    el.removeAttribute("data-reveal");
    el.classList.remove("in");
    el.style.removeProperty("--i");
  }

  var nav = performance.getEntriesByType && performance.getEntriesByType("navigation")[0];
  var calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var back = nav && nav.type === "back_forward";   // повернулись «назад»: нічого не програємо вдруге
  if (calm || back || !("IntersectionObserver" in window)) {
    if (back) root.classList.add("no-reveal");
    items.forEach(settle);
    return;
  }

  // Затримка залежить від місця серед сусідів, але не більше 6 кроків, щоб довгий список не «розкочувався» хвилину
  var seen = new Map();
  items.forEach(function (el) {
    var n = seen.get(el.parentNode) || 0;
    seen.set(el.parentNode, n + 1);
    el.style.setProperty("--i", Math.min(n, 6));
  });

  function show(el) {
    el.classList.add("in");
    var done = function () { settle(el); };
    el.addEventListener("transitionend", function (e) { if (e.propertyName === "opacity") done(); }, { once: true });
    setTimeout(done, 1500);   // запасний вихід, якщо transitionend не прийшов
  }

  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      io.unobserve(entry.target);
      show(entry.target);
    });
  }, { threshold: 0.08 });
  items.forEach(function (el) { io.observe(el); });
})();
