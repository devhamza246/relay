/* Relay chat widget. Drop-in: <script src="https://your-host/widget.js" data-api="https://your-host" defer></script>
   No dependencies. All model and customer text is inserted with textContent, never as HTML. */
(function () {
  "use strict";
  var script = document.currentScript;
  var API = ((script && script.dataset.api) || "").replace(/\/$/, "");
  var session = null, busy = false, options = {};

  var host = document.createElement("div");
  var root = host.attachShadow({ mode: "open" });
  root.innerHTML =
    "<style>" +
    ":host{all:initial}*{box-sizing:border-box;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}" +
    ".launch{position:fixed;right:20px;bottom:20px;z-index:2147483000;background:#1f5c4a;color:#fff;border:0;border-radius:999px;padding:14px 20px;font-size:15px;font-weight:600;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.25)}" +
    ".panel{position:fixed;right:20px;bottom:20px;z-index:2147483001;width:390px;max-width:calc(100vw - 24px);height:600px;max-height:calc(100vh - 40px);display:none;flex-direction:column;background:#fff;color:#17211d;border-radius:14px;overflow:hidden;box-shadow:0 18px 50px rgba(0,0,0,.3);border:1px solid #d8e0dc}" +
    ".panel.open{display:flex}" +
    "header{background:#1f5c4a;color:#fff;padding:12px 14px;display:flex;align-items:center;gap:8px}" +
    "header b{flex:1;font-size:15px}header small{display:block;font-weight:400;opacity:.85;font-size:12px}" +
    "header button{background:transparent;border:1px solid rgba(255,255,255,.5);color:#fff;border-radius:6px;padding:4px 8px;cursor:pointer;font-size:12px}" +
    ".log{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:10px;background:#f5f7f6}" +
    ".msg{max-width:88%;padding:9px 12px;border-radius:12px;font-size:14px;line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere}" +
    ".me{align-self:flex-end;background:#1f5c4a;color:#fff;border-bottom-right-radius:4px}" +
    ".bot{align-self:flex-start;background:#fff;border:1px solid #dde5e1;border-bottom-left-radius:4px}" +
    ".status{font-size:12px;color:#5d6b65;font-style:italic}" +
    ".cite{display:inline-block;vertical-align:baseline;margin:0 1px;padding:0 5px;border-radius:5px;background:#e2efe9;color:#14503d;font-size:11px;font-weight:700;border:0;cursor:pointer}" +
    ".sources{margin-top:8px;border-top:1px solid #e6ece9;padding-top:6px;display:flex;flex-direction:column;gap:4px}" +
    ".sources button{text-align:left;background:none;border:0;padding:0;color:#14503d;font-size:12px;cursor:pointer;text-decoration:underline}" +
    ".excerpt{font-size:12px;background:#f1f5f3;border-radius:6px;padding:6px 8px;color:#33423c;white-space:pre-wrap}" +
    ".tag{display:inline-block;margin-top:6px;font-size:11px;color:#5d6b65;background:#eef2f0;border-radius:4px;padding:1px 6px}" +
    ".tag.warn{background:#fdf0d5;color:#7a5200}" +
    ".nohelp{display:block;margin-top:6px;background:none;border:0;padding:0;font-size:12px;color:#5d6b65;cursor:pointer;text-decoration:underline}" +
    "form{display:flex;gap:8px;padding:10px;border-top:1px solid #dde5e1;background:#fff}" +
    "input{flex:1;min-width:0;border:1px solid #c5d0cb;border-radius:8px;padding:10px;font-size:14px;color:#17211d;background:#fff}" +
    "form button{background:#1f5c4a;color:#fff;border:0;border-radius:8px;padding:0 16px;font-weight:600;cursor:pointer}" +
    "form button:disabled{opacity:.5;cursor:default}" +
    "</style>" +
    '<button class="launch" type="button">Chat with support</button>' +
    '<section class="panel" role="dialog" aria-label="Support chat">' +
    '<header><b>Relay support<small></small></b><button type="button" data-a="new">New chat</button><button type="button" data-a="close" aria-label="Close chat">Close</button></header>' +
    '<div class="log" aria-live="polite"></div>' +
    '<form><input type="text" maxlength="1000" placeholder="Ask about orders, returns, billing..." aria-label="Message"><button type="submit">Send</button></form>' +
    "</section>";

  var $ = function (s) { return root.querySelector(s); };
  var panel = $(".panel"), log = $(".log"), input = $("input"), send = $("form button");

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function scroll() { log.scrollTop = log.scrollHeight; }
  function open() { panel.classList.add("open"); $(".launch").style.display = "none"; if (!log.childNodes.length) greet(); input.focus(); }
  function close() { panel.classList.remove("open"); $(".launch").style.display = ""; }
  function greet() { log.appendChild(el("div", "msg bot", "Hi, I'm Relay, the Fernwick Goods support assistant. I answer from our help center, check your orders, and pass you to a person when I should.")); }
  function reset(next) {
    options = next || options; session = null; log.textContent = ""; greet();
    window.dispatchEvent(new CustomEvent("relay:reset"));
  }

  function request(path, body) {
    return fetch(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) {
        if (r.ok) return r;
        return r.json().catch(function () { return {}; }).then(function (d) {
          var error = new Error(typeof d.detail === "string" ? d.detail : "Request failed (" + r.status + ")");
          error.status = r.status; throw error;
        });
      });
  }
  function ensureSession() {
    if (session) return Promise.resolve(session);
    return request("/api/sessions", { customer: options.customer || "", engine: options.engine || "" })
      .then(function (r) { return r.json(); }).then(function (d) { session = d.session_id; return session; });
  }

  function showSource(box, citation) {
    var old = box.querySelector(".excerpt");
    if (old) { var same = old.dataset.n === String(citation.n); old.remove(); if (same) return; }
    var excerpt = el("div", "excerpt", "Loading..."); excerpt.dataset.n = citation.n; box.appendChild(excerpt); scroll();
    if (citation.kind === "article") {
      fetch(API + "/api/articles/" + encodeURIComponent(citation.slug)).then(function (r) { return r.json(); })
        .then(function (a) { excerpt.textContent = a.title + "\n" + a.body; scroll(); })
        .catch(function () { excerpt.textContent = "Could not load the article."; });
    } else if (citation.kind === "order" && citation.record) {
      var o = citation.record;
      excerpt.textContent = o.id + " | " + o.status + " | placed " + o.placed + "\n" + o.items.join(", ") + " | $" + o.total + "\n" + o.detail;
    } else { excerpt.textContent = citation.title; }
  }

  function render(bubble, done) {
    var citations = done.citations || [], byN = {};
    citations.forEach(function (c) { byN[c.n] = c; });
    var box = el("div", "sources"), body = el("div");
    done.text.split(/(\[\d+\])/).forEach(function (part) {
      var m = /^\[(\d+)\]$/.exec(part), c = m && byN[m[1]];
      if (!c) { body.appendChild(document.createTextNode(part)); return; }
      var chip = el("button", "cite", m[1]); chip.type = "button"; chip.title = c.title;
      chip.onclick = function () { showSource(box, c); }; body.appendChild(chip);
    });
    bubble.textContent = ""; bubble.appendChild(body);
    if (citations.length) {
      citations.forEach(function (c) {
        var b = el("button", "", "[" + c.n + "] " + c.title); b.type = "button"; b.onclick = function () { showSource(box, c); }; box.appendChild(b);
      });
      bubble.appendChild(box);
    }
    if (done.outcome === "escalated") {
      var sent = done.handoff && done.handoff.delivery === "sent";
      bubble.appendChild(el("span", "tag warn", "Handed to a human · " + done.ticket + (sent ? " · Slack alerted" : " · Slack alert simulated (demo)")));
    }
    var engineLabel = { rules: "rule-based offline engine", recorded: "recorded model answer", live: "live model" }[done.engine];
    if (done.outcome === "answered") {
      bubble.appendChild(el("span", "tag", engineLabel));
      var no = el("button", "nohelp", "That didn't help"); no.type = "button";
      no.onclick = function () { no.remove(); submit("That didn't help."); }; bubble.appendChild(no);
    }
  }

  function readStream(response, onEvent) {
    var reader = response.body.getReader(), decoder = new TextDecoder(), buffer = "";
    function pump() {
      return reader.read().then(function (chunk) {
        if (chunk.done) return;
        buffer += decoder.decode(chunk.value, { stream: true });
        var parts = buffer.split("\n\n"); buffer = parts.pop();
        parts.forEach(function (part) {
          var line = part.split("\n").filter(function (l) { return l.indexOf("data: ") === 0; })[0];
          if (line) onEvent(JSON.parse(line.slice(6)));
        });
        return pump();
      });
    }
    return pump();
  }

  function submit(text) {
    text = (text || "").trim();
    if (!text || busy) return Promise.resolve();
    open(); busy = true; send.disabled = true;
    log.appendChild(el("div", "msg me", text));
    var bubble = el("div", "msg bot"), status = el("span", "status", "Sending...");
    bubble.appendChild(status); log.appendChild(bubble); scroll();
    var started = performance.now(), first = null, reply = "", finished = false;
    return ensureSession()
      .then(function (sid) { return request("/api/sessions/" + sid + "/messages", { text: text }); })
      .then(function (response) {
        return readStream(response, function (event) {
          if (event.type === "status" && !reply) status.textContent = event.text + "...";
          if (event.type === "token") {
            if (first === null) first = performance.now() - started;
            reply += event.text; bubble.textContent = reply; scroll();
          }
          if (event.type === "done") {
            finished = true; render(bubble, event); scroll();
            event.browser = { first_token_ms: first, total_ms: performance.now() - started };
            window.dispatchEvent(new CustomEvent("relay:done", { detail: { session: session, event: event } }));
          }
          if (event.type === "error") { finished = true; bubble.textContent = event.text; }
        });
      })
      .then(function () { if (!finished) bubble.textContent = "The reply was interrupted. Please try again."; })
      .catch(function (error) {
        if (error.status === 404) session = null;
        bubble.textContent = error.status ? error.message : "I can't reach support right now. Please try again.";
      })
      .then(function () { busy = false; send.disabled = false; scroll(); });
  }

  $(".launch").onclick = open;
  root.querySelector('[data-a="close"]').onclick = close;
  root.querySelector('[data-a="new"]').onclick = function () { reset(); };
  $("form").onsubmit = function (e) { e.preventDefault(); if (busy) return; var t = input.value; input.value = ""; submit(t); };

  function mount() { document.body.appendChild(host); }
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount);

  window.RelayWidget = {
    open: open, close: close, reset: reset,
    send: submit,
    configure: function (next) { options = next; },
    busy: function () { return busy; },
    subtitle: function (text) { $("header small").textContent = text; }
  };
})();
