(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var NOTES = {
    recorded: "Recorded model answers: replies Claude produced earlier are replayed, so the demo never spends API credits. Free-typed knowledge questions that were not recorded say so. Escalation rules and tools work for any text.",
    rules: "Rule-based offline engine: no AI model. It quotes help center sentences and uses keyword rules, so it is weaker than the real model, but you can type anything.",
    live: "Live model: every message calls the Claude API."
  };
  function el(tag, text, cls) {
    var n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  }
  function option(value, label) { var o = el("option", label); o.value = value; return o; }
  function configure() {
    var engine = $("engine").value;
    $("engine-note").textContent = NOTES[engine] || "";
    RelayWidget.reset({ customer: $("customer").value, engine: engine });
    RelayWidget.subtitle({ recorded: "Recorded model answers", rules: "Rule-based offline demo", live: "Live model" }[engine] || "");
  }

  function sequence(turns) {
    return turns.reduce(function (p, t) { return p.then(function () { return RelayWidget.send(t); }); }, Promise.resolve());
  }

  fetch("api/status").then(function (r) { return r.json(); }).then(function (status) {
    status.customers.forEach(function (c) { $("customer").appendChild(option(c.email, c.name + " (orders " + c.orders.join(", ") + ")")); });
    var names = { recorded: "Recorded model answers", rules: "Rule-based (type anything)", live: "Live model" };
    status.engines.forEach(function (e) { $("engine").appendChild(option(e, names[e] || e)); });
    status.suggestions.forEach(function (s) {
      var b = el("button", s.label); b.type = "button"; b.title = s.turns.join(" / ");
      b.onclick = function () { if (RelayWidget.busy()) return; configure(); RelayWidget.open(); sequence(s.turns); };
      $("chips").appendChild(b);
    });
    $("retention").textContent = status.retention_hours;
    $("meta").textContent = status.articles + " help articles · Slack: " + (status.slack === "webhook" ? "webhook connected" : "dry run (nothing is sent)");
    $("customer").onchange = configure; $("engine").onchange = configure; configure();
  }).catch(function () { $("meta").textContent = "The API is unavailable."; });

  function row(list, key, value) { if (value === undefined || value === null || value === "") return; list.appendChild(el("dt", key)); list.appendChild(el("dd", String(value))); }

  function showDecision(done) {
    var box = $("decision"), list = el("dl"); box.textContent = ""; box.className = "";
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
  function showHandoff(session) {
    fetch("api/sessions/" + encodeURIComponent(session)).then(function (r) { return r.json(); }).then(function (data) {
      var box = $("handoff"); box.textContent = ""; box.className = "";
      if (!data.tickets.length) { box.className = "muted"; box.textContent = "No ticket in this conversation yet."; return; }
      data.tickets.forEach(function (t) { box.appendChild(el("p", t.code + " · " + t.kind + " · priority " + t.priority + " · " + t.status)); });
      data.handoffs.forEach(function (h) {
        var sent = h.delivery === "sent";
        box.appendChild(el("div", sent ? "Delivered to Slack" : "Slack " + h.delivery.replace("_", " ") + ": " + h.delivery_detail, "badge" + (sent ? " sent" : "")));
        box.appendChild(slackMessage(h.payload));
        var details = el("details"); details.appendChild(el("summary", "Block Kit JSON")); details.appendChild(el("pre", JSON.stringify(h.payload, null, 2))); box.appendChild(details);
      });
    });
  }

  window.addEventListener("relay:done", function (e) { showDecision(e.detail.event); showHandoff(e.detail.session); });
  window.addEventListener("relay:reset", function () {
    $("handoff").className = "muted"; $("handoff").textContent = "No handoff in this conversation yet.";
    $("decision").className = "muted"; $("decision").textContent = "Send a message to see what the agent decides.";
  });
})();
