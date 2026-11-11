/* Панель АвтоХаб: власні випадні списки, картковий список, форми.
   Усі <select> замінюються на власний випадний список; рідний <select> лишається прихованим і тримає значення. */
(function () {
  "use strict";

  var CHEVRON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>';
  var CHECK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';
  var PLACEHOLDER = "Не вибрано";
  var instances = [];

  function qsa(selector, root) { return Array.prototype.slice.call((root || document).querySelectorAll(selector)); }
  function reduced() { return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches; }

  /* ---------- власний <select> ---------- */

  function isEmpty(option) { return option.value === ""; }
  function optionText(option) {
    if (isEmpty(option) && /^[-–—\s]*$/.test(option.textContent)) return PLACEHOLDER;
    return option.textContent;
  }
  function closeAll(except) {
    instances.forEach(function (instance) { if (instance !== except) instance.close(); });
  }

  function eligible(select) {
    if (select.multiple || select.size > 1) return false;
    if (select.dataset.cmSelect === "1" || select.dataset.cmSelect === "skip") return false;
    if (select.classList.contains("select2-hidden-accessible") || select.classList.contains("admin-autocomplete")) return false;
    if (select.closest(".empty-form") || /__prefix__/.test(select.name || "")) return false;
    if (select.closest(".selector") || select.closest("#jazzy-theme-chooser")) return false;
    return true;
  }

  function enhance(select) {
    select.dataset.cmSelect = "1";
    select.classList.add("cm-select-native");
    select.tabIndex = -1;

    var root = document.createElement("div");
    root.className = "cm-select";
    var trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "cm-select__trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    var label = select.id ? document.querySelector('label[for="' + select.id + '"]') : null;
    if (label) trigger.setAttribute("aria-label", label.textContent.replace("*", "").trim());
    var value = document.createElement("span");
    value.className = "cm-select__value";
    var chevron = document.createElement("span");
    chevron.className = "cm-select__chevron";
    chevron.innerHTML = CHEVRON;
    trigger.appendChild(value);
    trigger.appendChild(chevron);
    var menu = document.createElement("div");
    menu.className = "cm-select__menu";
    menu.setAttribute("role", "listbox");
    root.appendChild(trigger);
    root.appendChild(menu);
    select.insertAdjacentElement("afterend", root);

    var items = [];
    var active = -1;
    var search = null;
    var none = null;

    function syncTrigger() {
      var selected = select.options[select.selectedIndex];
      value.textContent = selected ? optionText(selected) : PLACEHOLDER;
      root.classList.toggle("is-placeholder", !selected || isEmpty(selected));
      root.classList.toggle("is-disabled", select.disabled);
      trigger.disabled = select.disabled;
    }

    function setActive(index) {
      if (active >= 0 && items[active]) items[active].classList.remove("is-active");
      active = index;
      if (active >= 0 && items[active]) {
        items[active].classList.add("is-active");
        var top = items[active].offsetTop;
        var bottom = top + items[active].offsetHeight;
        if (top < menu.scrollTop) menu.scrollTop = top - 6;
        else if (bottom > menu.scrollTop + menu.clientHeight) menu.scrollTop = bottom - menu.clientHeight + 6;
      }
    }

    function move(step) {
      if (!items.length) return;
      var next = active;
      for (var count = 0; count < items.length; count += 1) {
        next = (next + step + items.length) % items.length;
        if (!items[next].classList.contains("is-disabled") && !items[next].hidden) break;
      }
      setActive(next);
    }

    function applyFilter() {
      var query = search ? search.value.trim().toLowerCase() : "";
      var visible = 0;
      items.forEach(function (item) {
        var hit = !query || item.textContent.toLowerCase().indexOf(query) > -1;
        item.hidden = !hit;
        if (hit) visible += 1;
      });
      if (none) none.hidden = visible > 0;
      setActive(items.findIndex(function (item) { return !item.hidden && !item.classList.contains("is-disabled"); }));
      menu.scrollTop = 0;
    }

    function ensureSearch() {
      if (search) return;
      search = document.createElement("input");
      search.type = "text";
      search.className = "cm-select__search";
      search.placeholder = "Пошук";
      search.autocomplete = "off";
      search.setAttribute("aria-label", "Пошук у списку");
      search.addEventListener("input", applyFilter);
      search.addEventListener("keydown", function (event) {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          move(event.key === "ArrowDown" ? 1 : -1);
        } else if (event.key === "Enter") {
          event.preventDefault();
          choose(active);
        } else if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          close();
          trigger.focus();
        } else if (event.key === "Tab") {
          close();
        }
      });
      search.addEventListener("click", function (event) { event.stopPropagation(); });
      none = document.createElement("div");
      none.className = "cm-select__none";
      none.textContent = "Нічого не знайдено";
      none.hidden = true;
      root.appendChild(search);
    }

    function add(option) {
      var item = document.createElement("div");
      item.className = "cm-select__item";
      item.setAttribute("role", "option");
      item.dataset.index = String(option.index);
      var text = document.createElement("span");
      text.textContent = optionText(option);
      item.appendChild(text);
      if (isEmpty(option)) item.classList.add("is-empty");
      if (option.disabled) item.classList.add("is-disabled");
      if (option.selected) {
        item.classList.add("is-selected");
        item.setAttribute("aria-selected", "true");
        var mark = document.createElement("span");
        mark.className = "cm-select__check";
        mark.innerHTML = CHECK;
        item.appendChild(mark);
      }
      menu.appendChild(item);
      items.push(item);
    }

    function build() {
      menu.textContent = "";
      items = [];
      Array.prototype.forEach.call(select.children, function (child) {
        if (child.tagName === "OPTGROUP") {
          var group = document.createElement("div");
          group.className = "cm-select__group";
          group.textContent = child.label;
          menu.appendChild(group);
          Array.prototype.forEach.call(child.children, add);
        } else if (child.tagName === "OPTION") {
          add(child);
        }
      });
      if (items.length > 7 || select.dataset.cmSearch === "1") {
        ensureSearch();
        menu.appendChild(none);
      }
      syncTrigger();
    }

    function place() {
      var rect = trigger.getBoundingClientRect();
      var width = Math.max(rect.width, 220);
      var left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
      var anchored = getComputedStyle(document.body).position !== "static";
      var origin = anchored ? document.body.getBoundingClientRect() : { top: -window.pageYOffset, left: -window.pageXOffset };
      menu.style.width = width + "px";
      menu.style.left = left - origin.left + "px";
      menu.style.maxHeight = "280px";
      menu.style.bottom = "auto";
      menu.style.top = rect.bottom - origin.top + 6 + "px";
      // Знизу немає місця, а зверху є: відкриваємо список вгору.
      var height = menu.offsetHeight;
      if (rect.bottom + height + 12 > window.innerHeight && rect.top > height + 12) {
        menu.style.top = rect.top - origin.top - height - 6 + "px";
      }
    }

    function open() {
      if (select.disabled) return;
      closeAll(instance);
      build();
      root.classList.add("is-open");
      trigger.setAttribute("aria-expanded", "true");
      document.body.appendChild(menu);
      menu.classList.add("is-floating");
      place();
      void menu.offsetWidth;
      menu.classList.add("is-open");
      var selectedIndex = items.findIndex(function (item) { return item.classList.contains("is-selected"); });
      setActive(selectedIndex >= 0 ? selectedIndex : 0);
      if (active >= 0 && items[active]) menu.scrollTop = Math.max(0, items[active].offsetTop - 60);
      if (search) {
        var base = root.getBoundingClientRect();
        var box = value.getBoundingClientRect();
        search.style.left = box.left - base.left + "px";
        search.style.width = box.width + "px";
        search.value = "";
        none.hidden = true;
        items.forEach(function (item) { item.hidden = false; });
        root.classList.add("is-searching");
        search.focus();
      }
    }

    function close() {
      if (!root.classList.contains("is-open")) return;
      root.classList.remove("is-open", "is-searching");
      trigger.setAttribute("aria-expanded", "false");
      menu.classList.remove("is-open");
      function finish() {
        if (root.classList.contains("is-open")) return;
        menu.classList.remove("is-floating");
        menu.removeAttribute("style");
        root.appendChild(menu);
      }
      if (reduced()) finish();
      else window.setTimeout(finish, 200);
    }

    function choose(index) {
      var item = items[index];
      if (!item || item.classList.contains("is-disabled")) return;
      var optionIndex = Number(item.dataset.index);
      var changed = select.selectedIndex !== optionIndex;
      select.selectedIndex = optionIndex;
      close();
      build();
      trigger.focus();
      if (changed) {
        select.dispatchEvent(new Event("input", { bubbles: true }));
        select.dispatchEvent(new Event("change", { bubbles: true }));
        if (window.django && window.django.jQuery) window.django.jQuery(select).trigger("change");
      }
    }

    trigger.addEventListener("click", function (event) {
      event.stopPropagation();
      if (root.classList.contains("is-open")) close();
      else open();
    });

    trigger.addEventListener("keydown", function (event) {
      var opened = root.classList.contains("is-open");
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (!opened) open();
        else move(event.key === "ArrowDown" ? 1 : -1);
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        if (!opened) open();
        else choose(active);
      } else if (event.key === "Escape" && opened) {
        event.preventDefault();
        event.stopPropagation();
        close();
      } else if (event.key === "Tab" && opened) {
        close();
      } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        if (!opened) open();
        var letter = event.key.toLowerCase();
        for (var step = 1; step <= items.length; step += 1) {
          var candidate = (active + step) % items.length;
          if (items[candidate].textContent.trim().toLowerCase().indexOf(letter) === 0) {
            setActive(candidate);
            break;
          }
        }
      }
    });

    menu.addEventListener("mousedown", function (event) {
      if (event.target.closest(".cm-select__search")) return;
      event.preventDefault();
    });
    menu.addEventListener("click", function (event) {
      event.stopPropagation();
      var item = event.target.closest(".cm-select__item");
      if (item) choose(items.indexOf(item));
    });
    menu.addEventListener("mousemove", function (event) {
      var item = event.target.closest(".cm-select__item");
      if (!item) return;
      var index = items.indexOf(item);
      if (index !== active && !item.classList.contains("is-disabled")) setActive(index);
    });

    select.addEventListener("focus", function () { trigger.focus(); });
    select.addEventListener("change", syncTrigger);
    new MutationObserver(function () {
      if (root.classList.contains("is-open")) build();
      else syncTrigger();
    }).observe(select, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["disabled"] });

    var instance = { close: close, select: select, root: root };
    instances.push(instance);
    build();
  }

  function scanSelects(scope) {
    qsa("select", scope).forEach(function (select) { if (eligible(select)) enhance(select); });
  }

  document.addEventListener("click", function () { closeAll(null); });
  document.addEventListener("keydown", function (event) { if (event.key === "Escape") closeAll(null); });
  window.addEventListener("resize", function () { closeAll(null); });
  window.addEventListener("scroll", function (event) {
    var target = event.target;
    if (target === document || target === document.documentElement) { closeAll(null); return; }
    if (target && target.closest && target.closest(".cm-select__menu")) return;
    closeAll(null);
  }, true);
  window.cmCloseSelects = function () { closeAll(null); };

  /* ---------- випадні посилання (вкладки/фільтри в рядку списку) ---------- */

  function closeDropdowns(except) {
    qsa(".cm-dd.is-open").forEach(function (dropdown) {
      if (dropdown === except) return;
      dropdown.classList.remove("is-open");
      var button = dropdown.querySelector(".cm-dd__trigger");
      if (button) button.setAttribute("aria-expanded", "false");
    });
  }

  function enhanceDropdownSearch(dropdown) {
    if (dropdown.dataset.ready) return;
    dropdown.dataset.ready = "1";
    var menu = dropdown.querySelector(".cm-dd__menu");
    var items = qsa(".cm-dd__item", menu);
    var wrap = document.createElement("div");
    wrap.className = "cm-dd__searchwrap";
    var input = document.createElement("input");
    input.type = "text";
    input.className = "cm-dd__search";
    input.placeholder = "Пошук";
    input.autocomplete = "off";
    input.setAttribute("aria-label", "Пошук");
    wrap.appendChild(input);
    var none = document.createElement("div");
    none.className = "cm-dd__none";
    none.textContent = "Нічого не знайдено";
    none.hidden = true;
    menu.insertBefore(wrap, menu.firstChild);
    menu.appendChild(none);
    function visible() { return items.filter(function (item) { return !item.hidden; }); }
    input.addEventListener("input", function () {
      var query = input.value.trim().toLowerCase();
      items.forEach(function (item) { item.hidden = query !== "" && item.textContent.toLowerCase().indexOf(query) === -1; });
      none.hidden = visible().length > 0;
      menu.scrollTop = 0;
    });
    input.addEventListener("keydown", function (event) {
      if (event.key !== "Enter") return;
      event.preventDefault();
      var first = visible()[0];
      if (first) window.location.href = first.getAttribute("href");
    });
  }

  function initDropdowns() {
    qsa(".cm-dd--search").forEach(enhanceDropdownSearch);
    document.addEventListener("click", function (event) {
      var button = event.target.closest(".cm-dd__trigger");
      var inside = event.target.closest(".cm-dd");
      closeDropdowns(inside);
      if (button) {
        var dropdown = button.closest(".cm-dd");
        event.stopPropagation();
        var open = dropdown.classList.toggle("is-open");
        button.setAttribute("aria-expanded", open ? "true" : "false");
        if (open) {
          var input = dropdown.querySelector(".cm-dd__search");
          if (input) {
            input.value = "";
            input.dispatchEvent(new Event("input"));
            window.setTimeout(function () { input.focus(); }, 40);
          }
        }
      }
    });
    document.addEventListener("keydown", function (event) { if (event.key === "Escape") closeDropdowns(null); });
  }

  /* ---------- список карток: панель фільтрів, масові дії, перемикачі ---------- */

  function initList() {
    var page = document.querySelector(".cm-page");
    if (!page || !document.getElementById("cmResults")) return;
    var csrf = page.dataset.csrf || "";

    var filters = document.getElementById("cmFilters");
    var form = document.getElementById("cmFilterForm");
    if (filters && form) {
      var trigger = filters.querySelector(".cm-filters__trigger");
      var setPanel = function (open) {
        filters.classList.toggle("is-open", open);
        trigger.setAttribute("aria-expanded", open ? "true" : "false");
        if (!open && window.cmCloseSelects) window.cmCloseSelects();
      };
      trigger.addEventListener("click", function (event) {
        event.stopPropagation();
        closeDropdowns(null);
        setPanel(!filters.classList.contains("is-open"));
      });
      document.addEventListener("click", function (event) {
        if (!event.target.closest(".cm-filters")) setPanel(false);
      });
      document.addEventListener("keydown", function (event) { if (event.key === "Escape") setPanel(false); });
      form.addEventListener("pointerdown", function (event) {
        if (window.cmCloseSelects && !event.target.closest(".cm-select")) window.cmCloseSelects();
      });
      var go = function (reset) {
        var params = new URLSearchParams(window.location.search);
        params.delete("p");
        qsa("select", form).forEach(function (select) {
          params.delete(select.name);
          if (!reset && select.value !== "") params.set(select.name, select.value);
        });
        var query = params.toString();
        window.location.href = window.location.pathname + (query ? "?" + query : "");
      };
      form.addEventListener("submit", function (event) { event.preventDefault(); go(false); });
      form.querySelector("[data-filters-reset]").addEventListener("click", function () { go(true); });
    }

    var bulk = document.getElementById("cmBulk");
    if (bulk) {
      var count = bulk.querySelector("[data-bulk-count]");
      var picks = function () { return qsa(".cm-pick", page); };
      var refresh = function () {
        var chosen = picks().filter(function (box) { return box.checked; });
        picks().forEach(function (box) {
          var card = box.closest(".cm-card");
          if (card) card.classList.toggle("is-picked", box.checked);
        });
        count.textContent = String(chosen.length);
        bulk.hidden = chosen.length === 0;
      };
      page.addEventListener("change", function (event) { if (event.target.classList.contains("cm-pick")) refresh(); });
      bulk.querySelector("[data-bulk-all]").addEventListener("click", function () {
        picks().forEach(function (box) { box.checked = true; });
        refresh();
      });
      bulk.querySelector("[data-bulk-clear]").addEventListener("click", function () {
        picks().forEach(function (box) { box.checked = false; });
        refresh();
      });
      refresh();
    }

    page.addEventListener("click", function (event) {
      var button = event.target.closest(".cm-state[data-url]");
      if (!button) return;
      button.disabled = true;
      fetch(button.dataset.url, {
        method: "POST",
        headers: { "X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin"
      }).then(function (response) { return response.json(); }).then(function (data) {
        if (!data.ok) return;
        button.classList.toggle("cm-state--on", data.value);
        button.classList.toggle("cm-state--off", !data.value);
        button.setAttribute("aria-pressed", data.value ? "true" : "false");
        button.textContent = data.value ? button.dataset.onLabel : button.dataset.offLabel;
      }).catch(function () {
        button.classList.add("is-failed");
      }).then(function () { button.disabled = false; });
    });
  }

  /* ---------- форми: видалення рядків інлайнів, помилки, довгі списки галочок ---------- */

  function initForms() {
    document.addEventListener("click", function (event) {
      var button = event.target.closest(".cm-row-delete");
      if (!button) return;
      event.preventDefault();
      var box = button.closest(".cm-row-delete-box");
      var checkbox = box ? box.querySelector("input[type=checkbox]") : null;
      if (!checkbox) return;
      checkbox.checked = !checkbox.checked;
      button.classList.toggle("is-active", checkbox.checked);
      button.setAttribute("aria-pressed", checkbox.checked ? "true" : "false");
      var row = button.closest("tr");
      if (row) row.classList.toggle("is-deleted", checkbox.checked);
    });

    qsa(".cm-checks").forEach(function (list) {
      var rows = qsa("label", list);
      if (rows.length < 12 || list.dataset.searchReady) return;
      list.dataset.searchReady = "1";
      var input = document.createElement("input");
      input.type = "search";
      input.className = "cm-checks__search";
      input.placeholder = "Пошук серед варіантів";
      input.setAttribute("aria-label", "Пошук серед варіантів");
      input.addEventListener("input", function () {
        var query = input.value.trim().toLowerCase();
        rows.forEach(function (label) {
          var holder = label.closest("div") && label.closest("div") !== list ? label.closest("div") : label;
          holder.hidden = query !== "" && label.textContent.toLowerCase().indexOf(query) === -1;
        });
      });
      input.addEventListener("keydown", function (event) { if (event.key === "Enter") event.preventDefault(); });
      list.parentNode.insertBefore(input, list);
    });

    var failing = document.querySelector(".cm-field.has-error");
    if (failing) {
      window.addEventListener("load", function () {
        window.requestAnimationFrame(function () {
          var details = failing.closest("details");
          if (details) details.open = true;
          failing.scrollIntoView({ block: "center" });
          var control = failing.querySelector("input:not([type=hidden]), textarea, button.cm-select__trigger");
          if (control) control.focus({ preventScroll: true });
        });
      });
    }
    document.addEventListener("input", function (event) {
      var field = event.target.closest ? event.target.closest(".cm-field.has-error") : null;
      if (!field) return;
      field.classList.remove("has-error");
      qsa(".cm-field-error", field).forEach(function (node) { node.remove(); });
    });
  }

  function init() {
    scanSelects(document);
    initDropdowns();
    initList();
    initForms();
    new MutationObserver(function (records) {
      records.forEach(function (record) {
        record.addedNodes.forEach(function (node) {
          if (node.nodeType !== 1) return;
          if (node.tagName === "SELECT") { if (eligible(node)) enhance(node); }
          else scanSelects(node);
        });
      });
      instances = instances.filter(function (instance) { return document.contains(instance.root); });
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
