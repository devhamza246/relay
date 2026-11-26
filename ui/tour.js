/* Guided tour of the demo page: dims the page, highlights one real feature at a time and explains it. No dependencies. */
(function () {
  "use strict";
  var SEEN = "relayTourSeen";
  var byId = function (id) { return function () { return document.getElementById(id); }; };

  var STEPS = [
    { target: byId("what"), title: "A support agent that knows when to stop",
      text: "Relay answers routine questions for a fictional store, and passes the conversation to a person when it should not handle it alone." },
    { target: byId("chips"), title: "Suggested prompts",
      text: "Each button starts a new chat and sends a ready-made message. Try “Billing dispute” to see a handoff." },
    { target: function () { return RelayWidget.element("panel"); }, title: "The chat box",
      text: "Type your own question here. Replies cite their sources: click a number to read the article or order record behind it.",
      enter: function () { var was = RelayWidget.isOpen(); RelayWidget.open(); return function () { if (!was) RelayWidget.close(); }; } },
    { target: byId("state-badge"), title: "Escalation indicator",
      text: "Shows who has the conversation: the bot, the bot offering a human, or a human with a ticket number." },
    { target: byId("inside"), title: "Why the agent did that",
      text: "After each message you see how it was classified, which escalation signals fired, what was retrieved and how long it took." },
    { target: byId("agent"), title: "The human agent's view",
      text: "On a handoff the ticket appears here with the Slack message: summary, suspected intent and transcript, so nobody has to ask again." },
    { target: byId("limits"), title: "Engine and live allowance",
      text: "Pick who is signed in and which engine answers. This line shows how much of today's small live-model allowance is left." },
    { target: byId("key-box"), title: "Your own key (optional)",
      text: "Paste a separate, low-limit Anthropic key to ask without the demo's limits. It is used per message and never stored.",
      enter: function () { var d = document.getElementById("key-box"), was = d.open; d.open = true; return function () { d.open = was; }; } },
    { target: byId("tour-button"), title: "That's it", text: "You can replay this tour from here at any time." }
  ];

  var state = null;

  function seen() { try { return window.localStorage.getItem(SEEN) === "1"; } catch (e) { return false; } }
  function remember() { try { window.localStorage.setItem(SEEN, "1"); } catch (e) { /* storage blocked: the tour may show again */ } }

  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function button(label, cls, onClick) {
    var b = make("button", cls, label); b.type = "button"; b.onclick = onClick; return b;
  }
  function available(step) {
    var node = step.target();
    return !!node && !node.hidden;
  }

  function start() {
    if (state) return;
    var root = make("div", "tour-root");
    var shade = make("div", "tour-shade"), spot = make("div", "tour-spot"), card = make("div", "tour-card");
    card.setAttribute("role", "dialog"); card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-labelledby", "tour-title"); card.setAttribute("aria-describedby", "tour-text");
    var count = make("p", "tour-count"), title = make("h2"), text = make("p");
    count.setAttribute("aria-live", "polite"); title.id = "tour-title"; text.id = "tour-text";
    var actions = make("div", "tour-actions");
    var skip = button("Skip tour", "tour-skip", end), back = button("Back", "tour-back", function () { show(state.index - 1); });
    var next = button("Next", "tour-next", function () { show(state.index + 1); });
    actions.appendChild(skip); actions.appendChild(back); actions.appendChild(next);
    [count, title, text, actions].forEach(function (n) { card.appendChild(n); });
    root.appendChild(shade); root.appendChild(spot); root.appendChild(card);

    var previous = document.activeElement;
    var others = Array.prototype.filter.call(document.body.children, function (n) { return n.tagName !== "SCRIPT" && !n.inert; });
    others.forEach(function (n) { n.inert = true; });  // the page behind cannot be focused or read while the tour is open
    document.body.appendChild(root);
    document.body.classList.add("touring");
    state = { steps: STEPS.filter(available), index: 0, root: root, spot: spot, card: card, count: count, title: title, text: text,
              back: back, next: next, previous: previous, others: others, leave: null };
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    show(0);
  }

  function leaveStep() { if (state.leave) { state.leave(); state.leave = null; } }

  function show(index) {
    if (!state || index < 0) return;
    leaveStep();
    if (index >= state.steps.length) { end(); return; }
    state.index = index;
    var step = state.steps[index];
    if (step.enter) state.leave = step.enter();
    state.count.textContent = "Step " + (index + 1) + " of " + state.steps.length;
    state.title.textContent = step.title;
    state.text.textContent = step.text;
    state.back.disabled = index === 0;
    state.next.textContent = index === state.steps.length - 1 ? "Done" : "Next";
    scrollTo(step.target());
    place();
    state.next.focus({ preventScroll: true });
  }

  function scrollTo(node) {
    if (!node || node.getRootNode() !== document) return;  // the chat panel is fixed on screen
    var rect = node.getBoundingClientRect(), vh = window.innerHeight;
    var narrow = window.innerWidth < 640;
    var room = narrow ? vh - state.card.offsetHeight - 24 : vh;
    var top = rect.height >= room - 24 ? 12 : (room - rect.height) / 2;
    window.scrollTo(0, Math.max(0, window.scrollY + rect.top - top));
  }

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(v, hi)); }

  function place() {
    if (!state) return;
    var node = state.steps[state.index].target();
    if (!node) return;
    var r = node.getBoundingClientRect(), vw = window.innerWidth, vh = window.innerHeight, pad = 6, gap = 12;
    var left = clamp(r.left - pad, 0, vw), top = clamp(r.top - pad, 0, vh);
    var right = clamp(r.right + pad, 0, vw), bottom = clamp(r.bottom + pad, 0, vh);
    var s = state.spot.style;
    s.left = left + "px"; s.top = top + "px"; s.width = (right - left) + "px"; s.height = (bottom - top) + "px";
    var card = state.card, c = card.style;
    if (vw < 640) {  // phone: a sheet on whichever edge hides less of the highlighted feature
      card.classList.add("sheet");
      var atTop = (top + bottom) / 2 > vh / 2;
      c.left = "8px"; c.right = "8px"; c.top = atTop ? "8px" : "auto"; c.bottom = atTop ? "auto" : "8px";
      return;
    }
    card.classList.remove("sheet");
    c.right = "auto"; c.bottom = "auto";
    var w = card.offsetWidth, h = card.offsetHeight, x, y;
    if (vh - bottom >= h + gap) { x = left; y = bottom + gap; }
    else if (top >= h + gap) { x = left; y = top - h - gap; }
    else if (left >= w + gap) { x = left - w - gap; y = top; }
    else if (vw - right >= w + gap) { x = right + gap; y = top; }
    else { x = vw - w - gap; y = vh - h - gap; }
    c.left = clamp(x, 8, vw - w - 8) + "px"; c.top = clamp(y, 8, vh - h - 8) + "px";
  }

  function onKey(e) {
    if (!state) return;
    if (e.key === "Escape") { e.preventDefault(); end(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); show(state.index + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); show(state.index - 1); }
    else if (e.key === "Tab") {  // keep focus inside the dialog
      var items = Array.prototype.filter.call(state.card.querySelectorAll("button"), function (b) { return !b.disabled; });
      var at = items.indexOf(document.activeElement);
      e.preventDefault();
      items[(at + (e.shiftKey ? items.length - 1 : 1) + items.length) % items.length].focus();
    }
  }

  function end() {
    if (!state) return;
    leaveStep();
    remember();
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("resize", place);
    window.removeEventListener("scroll", place, true);
    state.others.forEach(function (n) { n.inert = false; });
    state.root.remove();
    document.body.classList.remove("touring");
    var back = state.previous;
    state = null;
    window.scrollTo(0, 0);
    var target = back && back !== document.body && back.isConnected ? back : document.getElementById("tour-button");
    if (target) target.focus({ preventScroll: true });
  }

  document.getElementById("tour-button").addEventListener("click", start);
  window.addEventListener("relay:ready", function () { if (!seen() && !RelayWidget.busy()) start(); });
  window.RelayTour = { start: start, end: end };
})();
