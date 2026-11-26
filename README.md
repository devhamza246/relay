# Relay: customer support agent with human handoff

[![CI](https://github.com/devhamza246/relay/actions/workflows/ci.yml/badge.svg)](https://github.com/devhamza246/relay/actions/workflows/ci.yml)

**Resolves the easy tickets with cited answers and escalates the rest with full context.**

**Live demo:** https://relay.hamzacodes.dev · **Results on Gauge:** https://gauge.hamzacodes.dev (suites `relay-support-live`, `relay-support-holdout` and `relay-support-rules`)

A chat widget answers from a help center and cites its sources. A billing dispute, legal threat, angry customer, security or
safety report, request for a person, or two failed answers hands the conversation to a human: a ticket is opened and a Slack
message with a summary, suspected intent and transcript is built.

Everything about the business is **fictional**: the store (Fernwick Goods), its 73 help articles, customers and orders. The
order API is a **mock**. **No Slack message has been delivered**: no webhook was available, so the live demo runs the handoff
as a dry run and shows the exact Block Kit payload. The webhook client is tested against a mocked HTTP transport only.

## How it works

```mermaid
flowchart TD
    W[Chat widget] -- "POST message, SSE reply" --> A[FastAPI]
    A --> R0[Redact card numbers and secrets]
    R0 --> T["Triage: intent, sentiment, urgency<br/>claude-haiku-5-5 + keyword rules"]
    R0 -. "drafts in parallel for likely questions" .-> K
    T --> E{"Escalation check<br/>billing dispute, legal, angry,<br/>security, safety, wants a person,<br/>2 failed answers"}
    E -- escalate --> H["Summary (claude-haiku-5-5)<br/>ticket + Slack Block Kit"]
    H --> S[(Slack webhook<br/>or dry run)]
    E -- order status --> O[Mock order API<br/>own orders only]
    E -- ticket request --> TK[Create ticket]
    E -- question --> K["Hybrid retrieval<br/>pgvector + full text"]
    K --> C{Confidence}
    C -- low --> U["I don't know, offer a human<br/>(counts as a failed answer)"]
    C -- ok --> G["Answer (claude-sonnet-5-5)"]
    G --> CG["Citation gate<br/>sentence by sentence"]
    CG --> W
    A --- PG[(PostgreSQL + pgvector<br/>help center, orders, logs, tickets)]
    A --- RD[(Redis<br/>sessions, locks, rate limits)]
```

- **State machine:** explicit, in [`app/machine.py`](app/machine.py) (states `open`, `awaiting_order_id`, `offered_human`,
  `escalated`). The flow has a handful of branches, so plain code is easier to read and test than a graph framework.
- **Escalation comes first.** Keyword rules run on every message and the model's triage can only add a reason, never remove
  one. A model outage cannot disable the rules (but see how weak the rules are on their own, below).
- **Citation gate** ([`app/citations.py`](app/citations.py)): a model sentence reaches the customer only if it cites a
  retrieved source, every number in it appears in that source (or in the question), and it shares vocabulary with it.
- **Handoff is saved before Slack is called**, so a Slack failure loses nothing. A turn finishes even if the browser
  disconnects. After handoff the bot stops answering and appends new messages to the ticket.
- **Every turn is logged** (triage, retrieval, citations, tokens, cost, latency) and exportable for evals through a
  token-protected endpoint, `GET /api/admin/export`. Conversations are deleted after 24 hours.
- **Retrieval** is adapted (copied and trimmed) from my Stacklight project; embeddings are `bge-small-en-v1.5` on the CPU.

## Results

Measured on 2026-11-26. "Real model" is Claude Haiku 5.5 and Sonnet 5.5 called through the API. "Rules" is the offline
stand-in with no model (keyword triage, quoted help center sentences).

### The 50-ticket set

[`eval/tickets.yaml`](eval/tickets.yaml) was committed before any pipeline code (first commit). Composition: 33 help center
questions, 5 order lookups, 2 ticket requests, 8 escalation triggers, 2 unanswerable. At most 38 of 50 (76%) can be
auto-resolved. A ticket counts only if the outcome is right, the expected article or order is cited, the expected facts are in
the reply and nothing is uncited.

| | Target | Real model | Rules |
|---|---|---|---|
| Auto-resolved (correct and cited), of all 50 | ≥ 70% | **36/50 = 72%** (see caveat) | 33/50 = 66% ✗ |
| Of the 38 resolvable | | 36/38 | 33/38 |
| Escalation triggers among the 50 | 100% | 8/8 | 8/8 |
| Extra trigger cases ([`safety.yaml`](eval/safety.yaml)) | 100% | 24/24 | 24/24 (see note) |
| False escalations: 20 near-miss questions | | 0/20 | 0/20 |
| False escalations: the other 42 tickets | | 1 | 0 |
| Unanswerable questions not answered (10) | | 9/10 | 8/10 |
| Replies containing an uncited claim | 0 | 0 | 0 |

**Caveat on 72%: it is in-sample.** After the first run of the rules stand-in on these tickets, I raised the number of
retrieved sources from 4 to 6 and gave the model path a lower confidence floor (0.60). Tickets t01 and t04 pass only because
of those two changes, so **without them the real model would have scored at most 34/50 (68%), below the target**. That upper
bound follows from retrieval scores; I did not spend money re-running it. The real-model run itself was run once, and nothing
was tuned after it.

Note on the rules column: the first run is saved as
[`rules-first-run.json`](eval/results/rules-first-run.json). It scored 23/24 on the extra triggers because the scorer only
looked at the last turn and one case escalated one turn earlier (correctly, on the second failed answer). I fixed the scorer.
The rules were written by me after the cases, so their 24/24 shows consistency, not detection ability.

I also read all of the real model's answers against the help center by hand and found no wrong statement. That is the
author's review, not an independent one.

### Holdout set

Because of the caveat above, I wrote [`holdout.yaml`](eval/holdout.yaml) after the pipeline was frozen, committed it before
its first run, ran it once and changed nothing afterwards. Same author, so it guards against overfitting but is not
independent, and it is small.

| | Real model | Rules |
|---|---|---|
| Help center questions resolved | **19/20** | 10/20 |
| Escalation triggers, reworded | **12/12** | **1/12** |
| False escalations (8 near misses) | 0/8 | 0/8 |
| Unanswerable not answered (4) | 4/4 | 4/4 |
| Replies containing an uncited claim | 0 | 0 |

**The keyword rules do not generalise.** On new phrasing ("I'll be taking this to a tribunal") they caught 1 of 12 triggers.
Escalation quality comes from the model triage. This matters in two places: the public demo's offline paths, and a live
deployment during a model outage, where only the rules and the two-failed-answers rule remain.

### Latency and cost (real model)

Measured on the server over loopback HTTP against a temporary API instance, from sending the message to the first reply text
and to the end. It excludes the internet and Cloudflare. Status messages are not counted as reply text.

| | Target | 50 + safety run (112 turns) | Holdout (44 turns) |
|---|---|---|---|
| First text, p50 / p95 / max | < 2 s | 1.32 / **2.05** / 2.30 s | 1.31 / **2.26** / 2.87 s |
| Turns over 2 s to first text | 0 | 8 of 112 ✗ | 3 of 44 ✗ |
| Full reply, p50 / p95 / max | < 6 s | 1.86 / 3.62 / 4.21 s | 1.66 / 3.92 / 4.70 s |
| Turns over 6 s | 0 | 0 | 0 |
| API cost | | $0.152 | $0.077 |

The first-text target is **met at the median and missed at p95**. The slow turns are mostly triage calls of about 2 s.
Answered turns cost about $0.0022 each. These are two short runs from one place at one time of day, with one conversation at
a time; there is no load test.

Two latency changes were made before the measured runs, after a two-message smoke test on other questions: the API client is
loaded at startup, and the answer is drafted while triage runs (nothing is shown before the escalation check passes; a draft
that is not used is thrown away and its cost is counted).

### Failure cases

From the real-model runs:

| Case | What happened |
|---|---|
| t21 "The price dropped a few days after I bought it. Can I get the difference?" | Triage called it a billing dispute, so an ordinary policy question went to a human. |
| t07 "My tracking says delivered but there's nothing at my door." | Triage called it an order lookup and asked for the order number instead of explaining what to do. |
| h04 "Part of my order showed up but not the rest, am I paying shipping twice?" | Same confusion between a policy question and an order lookup. |
| u03 "Can you help me with my taxes?" | Answered with true, cited sales tax information that is not what was asked. |
| t01 "How many days do I have to send something back?" | Correct, but added an unasked sentence about wrong items. Retrieval ranked the right article sixth. |

From the rules stand-in: wrong article chosen for t01, t06, t10 and t12; unrelated questions answered with a confident quote
(t49, u04, u07); 11 of 12 holdout triggers missed.

Known gaps in the guarantees:

- "No uncited claims" is enforced for form and figures, **not meaning**. A number that appears anywhere in the cited article
  passes even if the sentence uses it wrongly (a test written for the gate showed this with a price of "$2" against a source
  mentioning "2 business days"). There is no semantic entailment check.
- The scorer checks expected facts with patterns; it does not judge tone or completeness.
- The gate releases whole sentences, so "streaming" is sentence by sentence.

## The public demo

The public demo **answers live up to a small daily cap and otherwise replays recordings** (`RELAY_LLM_MODE=auto`).

| Engine | What it does |
|---|---|
| Recorded first, then live (default) | A request recorded in [`eval/cassette.jsonl`](eval/cassette.jsonl) (the suggested buttons and the test sets) is replayed for free. Anything else is sent to the Claude API, within the limits below. Public traffic is never added to the recordings. |
| Rule-based | No model. Type anything; expect the weaknesses listed above. |

| Limit | Value |
|---|---|
| Spend, all visitors | $0.05 per UTC day (about 20 live answers) |
| Live messages per visitor (by IP address, stored hashed) | 8 per UTC day |

The status line shows what is left. When either limit is reached, a new knowledge question gets a plain message saying the
live allowance is used up and that the recorded examples still work. It does not count as a failed answer. Recordings,
order lookups, tickets and keyword escalation keep working. **Escalation of new phrasing is much weaker then**, because
triage falls back to the keyword rules (1 of 12 on the holdout). The spend cap is held in memory and checked before each
call, so an API restart resets it and simultaneous requests can overshoot it slightly; visitor counts are in Redis. The
score and latency tables above come from the benchmark runs, not from public traffic.

**Bring your own key.** A visitor can paste their own Anthropic key into the page. It is sent with each message in the
`X-Anthropic-Key` header (it must start with `sk-ant-` and be 20 to 300 characters, otherwise the API answers 400), used
for that message's model calls only, and bypasses both limits. The browser holds it in page memory, not in storage, and the
server does not store, log, cache or record it; replies made with it are never written to a cassette. A key typed into the
chat itself is masked. A key that fails gets a plain message and does not count as a failed answer. It does pass through
this server and its Cloudflare tunnel, so use a separate key with a low spend limit and delete it afterwards. Tested with a
fake client against the database, session state, logs and cassette, and once on the deployment with a real key and the cap
at zero: the key was not found in Postgres, Redis, container logs or the cassette.

The page opens with a short "What is this?" guide covering how to trigger and inspect a handoff.

The compose default is `replay`, which needs no key and cannot spend. Replaying the cassette reproduces the benchmark
run's scores exactly, and CI gates on this. The demo shows the retention period, uses fictional identities and asks
visitors not to enter personal data.

## Run it

```bash
git clone https://github.com/devhamza246/relay && cd relay
cp .env.example .env        # set RELAY_ADMIN_DB_PASSWORD and RELAY_APP_DB_PASSWORD
docker network create ingress_default   # the compose file expects a shared proxy network
docker compose --env-file .env up -d --build --wait
# open http://localhost:8817
```

| Setting | Effect |
|---|---|
| `RELAY_LLM_MODE=auto` + `ANTHROPIC_API_KEY` | Recordings first, live on a miss. `live` always calls the model. Both are capped by `RELAY_DAILY_SPEND_CAP_USD` (default $0.05; 0 allows no call) and `RELAY_VISITOR_LIVE_PER_DAY` (default 8; 0 means no per-visitor limit). |
| `SLACK_WEBHOOK_URL` | Handoffs are posted to Slack (at most `RELAY_SLACK_MAX_PER_HOUR`). On a public demo, any visitor can trigger one. |
| `RELAY_ADMIN_TOKEN` | Enables the conversation export endpoint. |

Embed the widget elsewhere with `<script src="https://your-host/widget.js" data-api="https://your-host" defer></script>`
(same origin only as shipped: no CORS headers are sent).

## Tests and evals

```bash
docker run -d --rm --name relay-test-pg -e POSTGRES_PASSWORD=testadmin -p 127.0.0.1:55434:5432 \
  --tmpfs /var/lib/postgresql/data pgvector/pgvector:pg16
pip install -e ".[dev]" "git+https://github.com/devhamza246/gauge.git"
python -m playwright install chromium   # for the browser tests in tests/test_ui.py
pytest -q
RELAY_ADMIN_DATABASE_URL=postgresql://postgres:testadmin@127.0.0.1:55434/postgres RELAY_APP_DB_PASSWORD=dev python -m app.bootstrap
export RELAY_DATABASE_URL=postgresql://relay_app:dev@127.0.0.1:55434/postgres
python -m eval.runner --engine rules                       # add --suite holdout for the holdout set
RELAY_LLM_MODE=replay python -m eval.runner --engine recorded
gauge ci -c eval/gauge.yml --baseline eval/gauge-baseline.json
```

CI runs the tests against a real pgvector database and two Gauge gates (rules and recorded model answers) that fail on any
newly failing case. To measure the real model again (spends money), see [`scripts/bench_live.sh`](scripts/bench_live.sh).
The confidence thresholds were picked on a separate development set, [`calibration.yaml`](eval/calibration.yaml)
(`python -m scripts.calibrate`): at 0.74 it kept 19 of 20 answerable and rejected 13 of 15 unanswerable questions.

## Security, privacy and limits

- The API connects as a role that cannot change the help center or orders, or alter the schema; only the one-shot bootstrap
  holds the admin password.
- Order lookups are limited to the signed-in customer. In the demo the "sign-in" is a dropdown of fictional customers:
  **there is no authentication**, and a real deployment needs one.
- A conversation ID is a random secret; only its holder can read the transcript, tickets and Slack payload.
- Card numbers (Luhn-valid) and "password: …" style secrets are masked before storage, model calls or Slack. Other personal
  data is **not** detected. Customer text is escaped for Slack.
- Using this with real customers needs privacy notices, a data processing agreement with the model provider, a retention
  policy and a way to answer deletion requests. None of that is provided here.
- Out of scope: voice, Zendesk or Intercom sync, other languages, refunds, an agent inbox, analytics.
- Not done: load testing, accessibility audit, backups.

MIT licensed.
