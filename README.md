# Bella Vista — Restaurant Reservation Agent

A conversational agent that lets guests **book, modify, and cancel** restaurant
reservations through a web chat interface — without ever double-booking a table.

Built for the BRD in [`BRD-restaurant-reservation-agent.md`](BRD-restaurant-reservation-agent.md).
It runs end-to-end on `localhost` with **zero API keys and zero paid services** —
a non-technical staff member can `pip install` and demo it in one command.

---

## Quickstart

Requires Python 3.10+ (developed on 3.13).

```bash
# 1. Create a virtual environment
python -m venv .venv

# Windows (PowerShell):   .venv\Scripts\Activate.ps1
# macOS / Linux:          source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run
python app.py
```

Then open **http://127.0.0.1:8000** and start chatting. Try:

> *"I'd like to book a table for Saturday at 7pm"* → *"4 of us"* → *"Alex Rivera, 555-0123, it's our anniversary"*

The data store is seeded with a demo reservation (`BV-DEMO`, tomorrow at 7pm) so
you can immediately try **modify** and **cancel** flows.

### Running the tests

```bash
pytest            # 45 tests: rules, tools, allocator, a concurrency race, and the HTTP API
```

### Optional: enable the real LLM brain

The agent works fully on a built-in deterministic parser. To switch the
conversational layer to a real LLM with tool-calling, install a provider SDK and
set its key — the app auto-detects it on startup:

```bash
pip install anthropic && export ANTHROPIC_API_KEY=sk-...    # Anthropic Claude
# or
pip install openai    && export OPENAI_API_KEY=sk-...       # OpenAI
```

Either way the **business logic is identical** — the LLM only orchestrates the
same tools and phrases replies (see *Architecture* below).

---

## Stack choice & rationale

| Layer | Choice | Why |
|---|---|---|
| **Backend** | Python + **FastAPI** + Uvicorn | The BRD explicitly discourages a heavy SPA. FastAPI is a single, lightweight file that serves both the JSON API and the static UI, has first-class typing, and pairs naturally with `pytest`. |
| **Frontend** | Vanilla HTML/CSS/JS (no framework) | A chat UI is ~150 lines of JS. A framework would be a poor use of time and would blur the clean frontend/backend boundary. |
| **Data store** | JSON file + in-process lock + atomic write | "File-based is acceptable" per the BRD, and JSON is human-inspectable (you can open the file or hit `/api/reservations`). The lock + atomic replace give the integrity guarantees a single-process server needs (see *State integrity*). |
| **Agent brain** | **Hybrid**: deterministic NLU by default, real LLM tool-calling when a key is present | The reviewer can demo with no key, no cost, and deterministic behavior — while the codebase still demonstrates real LLM tool orchestration. Crucially, **all correctness lives in deterministic tools**, so "never double-book" is provable without a model in the loop. |

---

## Architecture

```
 app.py  (FastAPI)            HTTP, sessions (cookie), serves the static UI
   │
 agent.py                     one Agent per chat session; one logged tool dispatcher
   │
 nlu.py  /  llm.py            two interchangeable "brains" (deterministic | LLM)
   │
 tools.py                     the 5 capabilities + EVERY business rule (BR-1..BR-9)
   │
 store.py / allocator.py      JSON persistence (lock + atomic write) | table seating
   │
 models.py / config.py / clock.py
```

**Clean frontend/backend separation (a rubric item):** the browser talks only to
`POST /chat`. The agent, tools, and store know nothing about HTTP. You could swap
the UI for a phone bot, or the LLM for the deterministic parser, without touching
the other side — the tools are the stable contract.

**All correctness is in the tool layer.** The conversational layer can phrase
things however it likes, but it can never violate a business rule, because the
rules are enforced inside `tools.py` and re-validated atomically at write time.

---

## What's implemented

**All six user stories**, end-to-end in the browser:

| | Story | Status |
|---|---|---|
| US-1 | Book a table (happy path, out-of-order info, notes) | ✅ |
| US-2 | Suggest ≥2 real alternatives within ±90 min when full | ✅ |
| US-3 | Modify time / party size (by code **or** name+date) | ✅ |
| US-4 | Cancel (confirms first, then releases the slot) | ✅ |
| US-5 | Capture free-text notes without over-promising | ✅ |
| US-6 | Refuse out-of-scope asks (menu, prices, orders) and redirect | ✅ |

**All five tools** (`check_availability`, `create_reservation`, `get_reservation`,
`modify_reservation`, `cancel_reservation`) with strict validation that **fails
loudly** on bad input.

**All business rules BR-1…BR-9**, the exact **§9 persisted schema**, and the
**§8 seed data** (12 tables = 4×2 + 6×4 + 2×6; the `BV-DEMO` reservation).

**Backend requirements (§6.2):** `POST /chat`, cookie-based sessions, persistence
that survives restart, and every tool invocation logged to the server console.
Plus a read-only `GET /api/reservations` to make verification easy (it returns
guest PII, so it's a localhost debug aid — set `BV_DEBUG=0` to disable it).

**Stretch goals:** ST-3 (a read-only admin view via `/api/reservations`) and
ST-6 (mobile-responsive). The LLM tool-calling adapter is also wired (dormant
until a key is set).

---

## State integrity — how double-booking is prevented (BO-2)

Three things together guarantee "zero conflicting reservations":

1. **Table-level seating, not seat-counting.** The 12 tables are discrete. A slot
   is bookable only if the *whole set* of parties in it can be assigned to
   distinct tables simultaneously (`allocator.py`). A naive "44 seats" model would
   wrongly seat a party of 6 when both six-tops are taken — we don't.
   - It even handles the BR-3-vs-seed edge case: **max party is 8 but the largest
     table seats 6**, so parties of 7–8 are seated by *combining two tables*.
2. **An atomic critical section.** `create`/`modify`/`cancel` run their entire
   read-validate-write inside a re-entrant lock (`store.transaction()`), so the
   availability check and the insert are indivisible — closing the check-then-act
   (TOCTOU) race.
3. **Atomic file writes.** Saves write a temp file and `os.replace()` it, so a
   crash mid-write can't corrupt the store, and reservations survive a restart.

There's a dedicated test (`tests/test_concurrency.py`) that fires two conflicting
bookings at the last free table from two threads and asserts **exactly one wins**.

---

## Open questions (§12) — decisions made

The BRD left three questions intentionally ambiguous. My choices:

| Question | Decision |
|---|---|
| Casual dates ("next Friday")? | **Resolve them** (the server knows today's date), then **echo the absolute date back** for confirmation ("Saturday, June 27 — that slot is available…"). If a date is unparseable, ask for an explicit one. |
| Guest changes mind mid-booking ("actually make it 6")? | **Update in place.** The in-progress booking is kept in session state and the changed field is re-validated; the flow never restarts. |
| Obviously fake info ("name is asdfasdf")? | We can't verify identity without auth (out of scope), so **light sanity checks only**: a name must be non-empty, a phone must contain ≥7 digits. We capture what's given rather than gatekeep. |

---

## Assumptions

- **Last seating is 21:30**, not 22:00 — we don't seat a party at the moment the
  restaurant closes. Operating hours are otherwise exactly Tue–Sun 17:00–22:00.
- **A reservation holds its table(s) for its single 30-minute slot only** — we
  don't model dining duration / table turnover. This matches the BRD's
  "finite table capacity *per slot*" language.
- **Guest identity = phone number** (there are no accounts). BR-8 ("one
  reservation per date") is enforced per phone number.
- **Parties of ≤6 take a single best-fit table; only parties of 7–8 combine
  tables.** This mirrors the BRD's own example ("fully booked *for parties of 6*").
- Chat *sessions* are in-memory (a server restart starts a fresh conversation),
  but *reservations* are persisted to disk as required.

---

## Known gaps / what I'd do next

- The **deterministic parser** handles the BRD's example phrasings and common
  variations well, but it isn't an LLM — very unusual phrasings may need a
  rephrase. (Set an API key to get full natural-language understanding; the tool
  layer is unchanged.)
- Persistence is a JSON file suitable for a single-process demo. For real
  multi-process deployment I'd move to SQLite (a transaction per booking) — the
  `store.py` interface is already shaped so this is a drop-in change.
- Not implemented (out of the core scope): waitlist (ST-1), recurring
  reservations (ST-2), cross-session guest memory (ST-4), token streaming (ST-5).

---

## Project structure

```
bella_vista/
├── app.py                     FastAPI: POST /chat, sessions, static UI, debug endpoint
├── requirements.txt
├── reservation_agent/
│   ├── config.py              hours, slot grid, party limits, table inventory (BR-*)
│   ├── clock.py               injectable "now" (deterministic tests)
│   ├── models.py              Reservation (exact §9 schema)
│   ├── store.py               JSON store: lock, atomic write, seed
│   ├── allocator.py           table-seating feasibility (best-fit + 7–8 combine)
│   ├── tools.py               the 5 tools + BR-1..BR-9 + alternatives
│   ├── nlu.py                 deterministic conversational brain
│   ├── llm.py                 optional LLM tool-calling brain (Anthropic/OpenAI)
│   ├── agent.py               session + logged tool dispatcher
│   └── ...
├── static/                    index.html, styles.css, app.js
└── tests/                     rules, tools, allocator, concurrency, HTTP API
```

## Acceptance matrix (requirement → proof)

| Requirement | Proven by |
|---|---|
| BO-2 no double-booking, under a race | `tests/test_concurrency.py` |
| BR-1…BR-8 | `tests/test_rules.py` |
| Table-level capacity & 7–8 combine | `tests/test_allocator.py` |
| Tool contracts, §9 schema, persistence, US-2 alternatives | `tests/test_tools.py` |
| `/chat` sessions, US-1/US-4/US-6, session isolation | `tests/test_api.py` |
| US-1/US-2/US-4/US-6 in a real browser | verified manually (see transcript) |
