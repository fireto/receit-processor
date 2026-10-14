// Бележки — small page glue. HTMX does the rest.
(function () {
  "use strict";

  // Installable PWA. The SW caches versioned /static/ files only (see /sw.js).
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
  }

  // <form data-confirm="…">: ask before submitting (deleting a receipt).
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var message = form.getAttribute && form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  // Camera capture island: <form data-capture> with <input data-camera>. Each picture
  // taken is kept (a phone's camera input holds one file at a time) and shown as a
  // thumbnail; "＋ още снимка" takes the next part of a long receipt, up to MAX; "Изпрати"
  // posts them together. data-target: swap the returned fragment into that element
  // (home: the "Последни" list) — else follow the redirect (re-take). Without JS the form
  // posts the one picture as it is.
  var MAX = 3;

  function setupCapture(form) {
    var input = form.querySelector("[data-camera]");
    var pending = form.querySelector("[data-pending]");
    var thumbs = form.querySelector("[data-thumbs]");
    var more = form.querySelector("[data-more]");
    var send = form.querySelector("[data-send]");
    var error = form.querySelector("[data-error]");
    var target = form.getAttribute("data-target");
    var files = [];

    function render() {
      thumbs.innerHTML = "";
      files.forEach(function (file, i) {
        var img = document.createElement("img");
        img.src = URL.createObjectURL(file);
        img.alt = "Снимка " + (i + 1);
        img.className = "h-24 w-16 rounded-lg object-cover ring-1 ring-zinc-900/10 dark:ring-white/10";
        img.onload = function () { URL.revokeObjectURL(img.src); };
        thumbs.appendChild(img);
      });
      pending.hidden = files.length === 0;
      more.hidden = files.length >= MAX;
      send.textContent = files.length > 1 ? "Изпрати (" + files.length + " снимки)" : "Изпрати";
    }

    function reset() {
      files = [];
      input.value = "";
      render();
    }

    function fail(message) {
      if (!error) { window.alert(message); return; }
      error.textContent = message;
      error.hidden = false;
    }

    input.addEventListener("change", function () {
      if (error) error.hidden = true;
      Array.prototype.forEach.call(input.files, function (f) {
        if (files.length < MAX) files.push(f);
      });
      input.value = "";  // the same input takes the next picture
      render();
    });
    more.addEventListener("click", function () { input.click(); });
    form.querySelector("[data-cancel]").addEventListener("click", reset);

    form.addEventListener("submit", function (event) {
      if (!files.length) return;  // nothing collected: let the browser post the form
      event.preventDefault();
      var data = new FormData();
      Array.prototype.forEach.call(form.querySelectorAll("input[type=hidden]"), function (h) {
        data.append(h.name, h.value);
      });
      files.forEach(function (f) { data.append("photos", f, f.name || "photo.jpg"); });
      send.disabled = true;
      var headers = target ? { "HX-Request": "true" } : {};
      fetch(form.action, { method: "POST", body: data, headers: headers, credentials: "same-origin" })
        .then(function (resp) {
          return resp.text().then(function (text) {
            if (!resp.ok) {
              var doc = new DOMParser().parseFromString(text, "text/html");
              var msg = doc.querySelector("[data-message]");
              throw new Error(msg ? msg.textContent : "Неуспешно изпращане (" + resp.status + ")");
            }
            if (!target) { window.location.href = resp.url; return; }
            var old = document.querySelector(target);
            if (old) {
              old.outerHTML = text;
              if (window.htmx) window.htmx.process(document.querySelector(target));
            }
            reset();
          });
        })
        .catch(function (err) { fail(err.message || "Няма връзка — опитай пак."); })
        .then(function () { send.disabled = false; });
    });
  }

  // Gallery: picking the photos sends them (HTMX posts the form); then clear it.
  function setupGallery(form) {
    var input = form.querySelector("[data-gallery-input]");
    input.addEventListener("change", function () {
      if (input.files.length) form.requestSubmit();
    });
    form.addEventListener("htmx:afterRequest", function () { input.value = ""; });
  }

  function init() {
    Array.prototype.forEach.call(document.querySelectorAll("form[data-capture]"), setupCapture);
    Array.prototype.forEach.call(document.querySelectorAll("form[data-gallery]"), setupGallery);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // An HTMX request that failed (a refused photo): show the server's message.
  document.addEventListener("htmx:responseError", function (event) {
    var doc = new DOMParser().parseFromString(event.detail.xhr.responseText, "text/html");
    var msg = doc.querySelector("[data-message]");
    window.alert(msg ? msg.textContent : "Грешка " + event.detail.xhr.status);
  });
})();
