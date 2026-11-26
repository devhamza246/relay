(function () {
  "use strict";
  var SLOW_MS = 5000, GIVE_UP_MS = 20000;
  var $ = function (id) { return document.getElementById(id); };
  var NOTES = {
    recorded: "Recorded model answers: replies Claude produced earlier are replayed, so the demo never spends API credits. Free-typed knowledge questions that were not recorded say so. Escalation rules and tools work for any text.",
    rules: "Rule-based offline engine: no AI model. It quotes help center sentences and uses keyword rules, so it is weaker than the real model, but you can type anything.",
    live: "Recorded answers are replayed for free. Anything new is sent to the Claude API, within a small daily allowance; when it is used up, the recorded examples still work."
  };
  function el(tag, text, cls) {
    var n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  }
  function option(value, label) { var o = el("option", label); o.value = value; return o; }

  function getJson(url) {
    var abort = new AbortController(), timer = setTimeout(function () { abort.abort(); }, GIVE_UP_MS);
    return fetch(url, { signal: abort.signal }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).finally(function () { clearTimeout(timer); });
  }

  /* A spinner with a label inside `box`, plus a note if the wait passes SLOW_MS. Returns a function that stops it. */
  function waiting(box, label) {
    box.textContent = ""; box.classList.remove("muted");
    var line = el("p", undefined, "wait"); line.setAttribute("role", "status");
    line.appendChild(el("span", undefined, "spin")); var text = el("span", label); line.appendChild(text); box.appendChild(line);
    var timer = setTimeout(function () { text.textContent = label + " This is taking longer than usual."; }, SLOW_MS);
    return function () { clearTimeout(timer); };
  }
  function problem(box, message, retry) {
    box.textContent = ""; box.classList.remove("muted");
    var line = el("p", message, "problem"); line.setAttribute("role", "alert"); box.appendChild(line);
    if (retry) { var b = el("button", "Try again", "retry"); b.type = "button"; b.onclick = retry; box.appendChild(b); }
  }

  function configure() {
    var engine = $("engine").value;
    $("engine-note").textContent = NOTES[engine] || "";
    RelayWidget.reset({ customer: $("customer").value, engine: engine });
    RelayWidget.subtitle({ recorded: "Recorded model answers", rules: "Rule-based offline demo", live: "Recorded first, then live" }[engine] || "");
  }

  var usingKey = false, lastAllowance = null;
  function showAllowance(a) {
    var box = $("allowance");
    lastAllowance = a;
    box.classList.add("muted");
    if (usingKey) { box.textContent = "Using your key: new questions are billed to your Anthropic account, not this demo's allowance."; return; }
    if (!a) { box.textContent = ""; return; }
    var left = a.remaining_usd <= 0 || a.visitor_remaining === 0;
    box.textContent = (left ? "Today's live allowance is used up; the recorded examples still work. " : "Live allowance left today: ") +
      "$" + a.remaining_usd.toFixed(3) + " of $" + a.daily_usd.toFixed(2) + " for everyone" +
      (a.visitor_remaining === null ? "" : " · " + a.visitor_remaining + " of " + a.visitor_per_day + " live messages for you") +
      " · resets " + a.resets;
  }
  function refreshAllowance() {
    var stop = waiting($("allowance"), "Updating the allowance…");
    getJson("api/status").then(function (s) { stop(); showAllowance(s.live_allowance); })
      .catch(function () { stop(); $("allowance").textContent = "Could not refresh the allowance."; });
  }

  function keyChanged() {
    var key = $("own-key").value.trim();
    var valid = key.indexOf("sk-ant-") === 0 && key.length >= 20 && key.length <= 300;
    usingKey = valid;
    RelayWidget.setKey(valid ? key : "");
    $("key-state").textContent = valid ? "Your key is in use until you clear this box or reload the page." :
      (key ? "That does not look like an Anthropic key (it starts with sk-ant-). It is not being sent." : "");
    showAllowance(lastAllowance);
  }
  $("own-key").addEventListener("input", keyChanged);
  $("own-key").value = "";
  $("forget-key").onclick = function () { $("own-key").value = ""; keyChanged(); };

  function setBusy(busy) {
    Array.prototype.forEach.call(document.querySelectorAll("#chips button, #customer, #engine"), function (n) { n.disabled = busy; });
    if (!busy) Array.prototype.forEach.call(document.querySelectorAll("#chips button.loading"), function (b) {
      b.classList.remove("loading"); b.removeAttribute("aria-busy");
    });
  }

  function sequence(turns) {
    return turns.reduce(function (p, t) { return p.then(function () { return RelayWidget.send(t); }); }, Promise.resolve());
  }

  function ready(status) {
    $("customer").textContent = ""; $("engine").textContent = ""; $("chips").textContent = "";
    status.customers.forEach(function (c) { $("customer").appendChild(option(c.email, c.name + " (orders " + c.orders.join(", ") + ")")); });
    var names = { recorded: "Recorded model answers", rules: "Rule-based (type anything)", live: "Recorded first, then live" };
    status.engines.forEach(function (e) { $("engine").appendChild(option(e, names[e] || e)); });
    status.suggestions.forEach(function (s) {
      var b = el("button", s.label); b.type = "button"; b.title = s.turns.join(" / ");
      b.onclick = function () {
        if (RelayWidget.busy()) return;
        configure(); b.classList.add("loading"); b.setAttribute("aria-busy", "true"); RelayWidget.open(); sequence(s.turns);
      };
      $("chips").appendChild(b);
    });
    showAllowance(status.live_allowance);
    $("retention").textContent = status.retention_hours;
    $("key-box").hidden = status.engines.indexOf("live") < 0 && status.engines.indexOf("recorded") < 0;
    $("slack-mode").textContent = status.slack === "webhook" ? "It is also posted to the connected Slack channel." :
      "On this demo the Slack alert is simulated: you see the exact message, and nothing is sent.";
    $("meta").textContent = status.articles + " help articles · Slack: " + (status.slack === "webhook" ? "webhook connected" : "dry run (nothing is sent)");
    $("customer").onchange = configure; $("engine").onchange = configure; configure(); setBusy(false);
  }

  function boot() {
    var box = $("page-loading");
    box.hidden = false; document.body.dataset.state = "loading";
    var stop = waiting(box, "Loading the demo…");
    getJson("api/status").then(function (status) {
      stop(); ready(status); box.hidden = true; document.body.dataset.state = "ready";
      window.dispatchEvent(new CustomEvent("relay:ready"));
    }).catch(function () {
      stop(); document.body.dataset.state = "failed";
      problem(box, "The demo could not reach its server, so the buttons and chat are not available yet.", boot);
    });
  }

  function row(list, key, value) { if (value === undefined || value === null || value === "") return; list.appendChild(el("dt", key)); list.appendChild(el("dd", String(value))); }

  function badge(kind, text) { var b = $("state-badge"); b.className = "state " + kind; b.textContent = text; }
  function showState(done) {
    if (done.state === "escalated") badge("human", "With a human · ticket " + (done.ticket || ""));
    else if (done.state === "offered_human") badge("offered", "Bot offered a human · reply “yes” to hand off");
    else badge("bot", "The bot is handling this conversation");
  }

  function showDecision(done) {
    var box = $("decision"), list = el("dl"); box.textContent = ""; box.classList.remove("muted");
    var t = done.triage;
    if (t) row(list, "Triage", t.intent + " · " + t.sentiment + " · urgency " + t.urgency + " (" + t.source + ")");
    row(list, "Escalation signals", (done.signals || []).join(", ") || "none");
    row(list, "Outcome", done.outcome + (done.reason ? " (" + done.reason + ")" : ""));
    if (done.confidence !== undefined) row(list, "Retrieval confidence", done.confidence.toFixed(3));
    if (done.retrieved) row(list, "Retrieved", done.retrieved.map(function (h) { return h.slug + " " + h.similarity.toFixed(2); }).join(", "));
    if (done.tool) row(list, "Tool call", done.tool.name + " " + (done.tool.order || done.tool.ticket || ""));
    if ((done.dropped_sentences || []).length) row(list, "Blocked by citation gate", done.dropped_sentences.map(function (d) { return d.reason + ": " + d.sentence; }).join(" | "));
    row(list, "Failed answers", done.failed_answers);
    row(list, "Engine", done.engine);
    var b = done.browser || {};
    if (b.total_ms) row(list, "Timing in your browser", "first text " + Math.round(b.first_token_ms || 0) + " ms, complete " + Math.round(b.total_ms) + " ms" + (done.engine === "live" ? "" : " (no live model call)"));
    if ((done.notes || []).length) row(list, "Notes", done.notes.join(" "));
    box.appendChild(list);
  }

  function unescape(s) { return s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&"); }
  function mrkdwn(text) {
    var div = el("div");
    text.split(/(\*[^*\n]+\*)/).forEach(function (part) {
      if (/^\*[^*\n]+\*$/.test(part)) div.appendChild(el("strong", unescape(part.slice(1, -1))));
      else div.appendChild(document.createTextNode(unescape(part)));
    });
    return div;
  }
  function slackMessage(payload) {
    var box = el("div", undefined, "slack");
    payload.blocks.forEach(function (b) {
      if (b.type === "header") box.appendChild(el("h3", b.text.text));
      else if (b.type === "divider") box.appendChild(el("hr"));
      else if (b.fields) { var g = el("div", undefined, "fields"); b.fields.forEach(function (f) { g.appendChild(mrkdwn(f.text)); }); box.appendChild(g); }
      else if (b.text) box.appendChild(mrkdwn(b.text.text));
      else if (b.elements) box.appendChild(el("div", b.elements[0].text, "muted"));
    });
    return box;
  }

  var view = 0;  // bumped on every new chat or message, so a late response cannot overwrite a newer view
  function showHandoff(session) {
    var mine = ++view, box = $("handoff"), stop = waiting(box, "Loading the ticket…");
    getJson("api/sessions/" + encodeURIComponent(session)).then(function (data) {
      stop();
      if (mine !== view) return;
      box.textContent = ""; box.classList.remove("muted");
      if (!data.tickets.length) { box.className = "muted"; box.textContent = "No ticket in this conversation yet."; return; }
      data.tickets.forEach(function (t) { box.appendChild(el("p", t.code + " · " + t.kind + " · priority " + t.priority + " · " + t.status)); });
      data.handoffs.forEach(function (h) {
        var sent = h.delivery === "sent";
        box.appendChild(el("div", sent ? "Delivered to Slack" : "Slack " + h.delivery.replace("_", " ") + ": " + h.delivery_detail, "badge" + (sent ? " sent" : "")));
        box.appendChild(slackMessage(h.payload));
        var details = el("details"); details.appendChild(el("summary", "Block Kit JSON")); details.appendChild(el("pre", JSON.stringify(h.payload, null, 2))); box.appendChild(details);
      });
    }).catch(function () {
      stop();
      if (mine === view) problem(box, "Could not load the ticket for this conversation.", function () { showHandoff(session); });
    });
  }

  var stopDecision = null;
  function clearDecisionWait() { if (stopDecision) { stopDecision(); stopDecision = null; } }
  window.addEventListener("relay:busy", function (e) {
    setBusy(e.detail.busy);
    if (e.detail.busy) { clearDecisionWait(); stopDecision = waiting($("decision"), "Waiting for the agent…"); }
  });
  window.addEventListener("relay:done", function (e) {
    clearDecisionWait(); showDecision(e.detail.event); showState(e.detail.event); showHandoff(e.detail.session); refreshAllowance();
  });
  window.addEventListener("relay:failed", function () {
    clearDecisionWait(); problem($("decision"), "That message did not go through. Use “Try again” in the chat.");
  });
  window.addEventListener("relay:reset", function () {
    view++; clearDecisionWait(); badge("bot", "The bot is handling this conversation");
    $("handoff").className = "muted"; $("handoff").textContent = "No handoff in this conversation yet.";
    $("decision").className = "muted"; $("decision").textContent = "Send a message to see what the agent decides.";
  });

  // Wait for the other scripts (the tour listens for relay:ready) before loading.
  if (document.readyState === "complete") boot(); else window.addEventListener("load", boot);
})();
