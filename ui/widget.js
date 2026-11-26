/* Relay chat widget. Drop-in: <script src="https://your-host/widget.js" data-api="https://your-host" defer></script>
   No dependencies. All model and customer text is inserted with textContent, never as HTML. */
(function () {
  "use strict";
  var script = document.currentScript;
  var API = ((script && script.dataset.api) || "").replace(/\/$/, "");
  var session = null, busy = false, options = {};
  var SLOW_MS = 5000, GIVE_UP_MS = 45000;
  var current = null;  // the request in flight, so "New chat" can cancel it
  var ownKey = "";  // the visitor's own API key: this variable only, never written to browser storage

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
    "form button{min-width:70px;display:inline-flex;align-items:center;justify-content:center}" +
    ".spin{display:inline-block;width:14px;height:14px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:turn .8s linear infinite;vertical-align:-2px}" +
    ".typing{display:inline-flex;gap:4px;margin-right:8px;vertical-align:middle}.typing i{width:6px;height:6px;border-radius:50%;background:#6c807a;animation:bob 1.1s ease-in-out infinite}" +
    ".typing i:nth-child(2){animation-delay:.15s}.typing i:nth-child(3){animation-delay:.3s}" +
    ".pending{display:block;margin-top:2px}.slow{display:block;margin-top:6px;font-size:12px;color:#7a5200}" +
    ".bot.error{border-color:#e2b4ac;background:#fdf1ef}" +
    ".retry{display:block;margin-top:8px;background:#1f5c4a;color:#fff;border:0;border-radius:6px;padding:5px 12px;font-size:13px;cursor:pointer}" +
    "@keyframes turn{to{transform:rotate(360deg)}}@keyframes bob{0%,80%,100%{opacity:.35;transform:none}40%{opacity:1;transform:translateY(-3px)}}" +
    "@media (prefers-reduced-motion:reduce){.spin,.typing i{animation:none}}" +
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
    if (current) current.cancel();
    options = next || options; session = null; log.textContent = ""; greet();
    window.dispatchEvent(new CustomEvent("relay:reset"));
  }

  function setBusy(value) {
    busy = value; send.disabled = value; send.setAttribute("aria-busy", String(value));
    send.textContent = ""; send.appendChild(value ? el("span", "spin") : document.createTextNode("Send"));
    send.setAttribute("aria-label", value ? "Sending" : "Send");
    window.dispatchEvent(new CustomEvent("relay:busy", { detail: { busy: value } }));
  }
  function typing() {
    var dots = el("span", "typing"); dots.setAttribute("aria-hidden", "true");
    for (var i = 0; i < 3; i++) dots.appendChild(el("i"));
    return dots;
  }

  function request(path, body, extra, signal) {
    var headers = { "Content-Type": "application/json" };
    if (extra) Object.keys(extra).forEach(function (k) { headers[k] = extra[k]; });
    return fetch(API + path, { method: "POST", headers: headers, body: JSON.stringify(body), signal: signal })
      .then(function (r) {
        if (r.ok) return r;
        return r.json().catch(function () { return {}; }).then(function (d) {
          var error = new Error(typeof d.detail === "string" ? d.detail : "Request failed (" + r.status + ")");
          error.status = r.status; throw error;
        });
      });
  }
  function ensureSession(signal) {
    if (session) return Promise.resolve(session);
    return request("/api/sessions", { customer: options.customer || "", engine: options.engine || "" }, null, signal)
      .then(function (r) { return r.json(); }).then(function (d) { session = d.session_id; return session; });
  }

  function showSource(box, citation) {
    var old = box.querySelector(".excerpt");
    if (old) { var same = old.dataset.n === String(citation.n); old.remove(); if (same) return; }
    var excerpt = el("div", "excerpt"); excerpt.dataset.n = citation.n;
    excerpt.appendChild(el("span", "spin")); excerpt.appendChild(document.createTextNode(" Loading the source..."));
    box.appendChild(excerpt); scroll();
    if (citation.kind === "article") {
      var slow = setTimeout(function () { excerpt.appendChild(el("span", "slow", "This is taking longer than usual.")); }, SLOW_MS);
      fetch(API + "/api/articles/" + encodeURIComponent(citation.slug))
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }).finally(function () { clearTimeout(slow); })
        .then(function (a) { excerpt.textContent = a.title + "\n" + a.body; scroll(); })
        .catch(function () { excerpt.textContent = "Could not load the article. Click the source again to retry."; excerpt.dataset.n = ""; });
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
    open(); setBusy(true);
    var mine = el("div", "msg me", text); log.appendChild(mine);
    var bubble = el("div", "msg bot"), answer = el("span"), pending = el("span", "pending"), status = el("span", "status", "Sending...");
    pending.setAttribute("role", "status"); pending.appendChild(typing()); pending.appendChild(status);
    bubble.appendChild(answer); bubble.appendChild(pending); log.appendChild(bubble); scroll();
    var started = performance.now(), first = null, reply = "", finished = false, cancelled = false, timedOut = false;
    var abort = new AbortController();
    var slow = setTimeout(function () {
      pending.appendChild(el("span", "slow", "This is taking longer than usual. Still working...")); scroll();
    }, SLOW_MS);
    var giveUp = setTimeout(function () { timedOut = true; abort.abort(); }, GIVE_UP_MS);
    function settle() { clearTimeout(slow); clearTimeout(giveUp); current = null; setBusy(false); }
    current = { cancel: function () { cancelled = true; abort.abort(); settle(); } };

    function fail(message, retryable) {
      bubble.textContent = message; bubble.classList.add("error"); bubble.setAttribute("role", "alert");
      if (retryable) {
        var again = el("button", "retry", "Try again"); again.type = "button";
        again.onclick = function () { if (busy) return; mine.remove(); bubble.remove(); submit(text); };
        bubble.appendChild(again);
      }
      window.dispatchEvent(new CustomEvent("relay:failed"));
    }

    return ensureSession(abort.signal)
      .then(function (sid) {
        return request("/api/sessions/" + sid + "/messages", { text: text }, ownKey ? { "X-Anthropic-Key": ownKey } : null, abort.signal);
      })
      .then(function (response) {
        return readStream(response, function (event) {
          if (cancelled) return;
          if (event.type === "status" && !reply) status.textContent = event.text + "...";
          if (event.type === "token") {
            if (first === null) { first = performance.now() - started; status.textContent = ""; }
            reply += event.text; answer.textContent = reply; scroll();
          }
          if (event.type === "done") {
            finished = true; clearTimeout(slow); render(bubble, event); scroll();
            event.browser = { first_token_ms: first, total_ms: performance.now() - started };
            window.dispatchEvent(new CustomEvent("relay:done", { detail: { session: session, event: event } }));
          }
          if (event.type === "error") { finished = true; fail(event.text, true); }
        });
      })
      .then(function () { if (!finished && !cancelled) fail("The reply was interrupted.", true); })
      .catch(function (error) {
        if (cancelled || finished) return;
        if (error.status === 404) session = null;
        if (timedOut) fail("The reply took too long, so I stopped waiting.", true);
        else if (error.status) fail(error.message, error.status !== 400 && error.status !== 422);
        else fail("I can't reach support right now. Check your connection.", true);
      })
      .then(function () { if (!cancelled) { settle(); scroll(); } });
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
    setKey: function (key) { ownKey = (key || "").trim(); },
    busy: function () { return busy; },
    isOpen: function () { return panel.classList.contains("open"); },
    element: function (name) { return name === "panel" ? panel : $(".launch"); },
    subtitle: function (text) { $("header small").textContent = text; }
  };
})();
