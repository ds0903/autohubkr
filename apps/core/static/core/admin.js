/* Адмінка: українські підписи там, де Jazzmin не має перекладу. */
document.addEventListener("DOMContentLoaded", function () {
  var labels = {
    "Dashboard": "Головна",
    "See Profile": "Мій профіль",
    "Sign out": "Вийти",
    "Skip to main content": "Перейти до змісту",
    "Skip to navigation": "Перейти до меню",
  };
  document.querySelectorAll("a, #jazzy-navigation .nav-link p, #jazzy-usermenu .dropdown-footer").forEach(function (el) {
    var text = el.textContent.trim();
    if (labels[text] && el.children.length === 0) el.textContent = labels[text];
  });
  document.querySelectorAll(".app-footer").forEach(function (footer) {
    // «Jazzmin version …» праворуч нікому не потрібна: прибираємо з розмітки, а не лише ховаємо стилем
    footer.querySelectorAll(".float-end").forEach(function (el) { el.remove(); });
    footer.innerHTML = footer.innerHTML.replace(/\s*All rights reserved\.?/i, "");
  });
});
