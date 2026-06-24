"""Deterministic conversational brain — runs the demo with NO API key.

It is a slot-filling dialogue manager over the same tools the LLM path uses:
extract entities from each message, fill in the missing booking fields, check
availability, and confirm. It also drives cancel/modify confirmations, offers
real alternatives when a slot is full (US-2), handles corrections in place
(§12), and refuses out-of-scope asks (US-6).

It won't match an LLM's fluency, but it makes the required user stories work
end-to-end, deterministically and for free.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

import dateparser
from dateparser.search import search_dates

from . import clock, config
from .errors import ReservationError, UnavailableError
from .formatting import fmt_date, fmt_reservation, fmt_time, fmt_when
from .models import CONFIRMED
from .session import ConversationState, Reply

# --------------------------------------------------------------------- vocab
WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_NUMWORD = "|".join(WORD_NUM)

CODE_RE = re.compile(r"\bBV[-\s]?([A-Za-z0-9]{4})\b", re.I)
ISO_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
PHONE_RE = re.compile(r"(\+?\d[\d\-\.\s()]{5,}\d)")

TIME_AMPM = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\.?\b", re.I)
TIME_24 = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
TIME_OCLOCK = re.compile(r"\b(\d{1,2})\s*o['’]?clock\b", re.I)
NOON_RE = re.compile(r"\bnoon\b", re.I)

PARTY_FOR = re.compile(rf"\b(?:party of|table for|reservation for|group of|seats? for|for)\s+(\d{{1,2}}|{_NUMWORD})\b", re.I)
PARTY_PEOPLE = re.compile(rf"\b(\d{{1,2}}|{_NUMWORD})\s*(?:people|persons?|guests?|of us|pax|adults?|diners?|heads?)\b", re.I)
SOLO_RE = re.compile(r"\b(just me|myself|for one|table for me|solo)\b", re.I)
COUPLE_RE = re.compile(r"\b(a couple|couple of us|two of us|for two)\b", re.I)

DATE_HINT_RE = re.compile(
    r"\b(today|tonight|tomorrow|tmrw|next|this|coming|"
    r"mon|tue|tues|wed|weds|thu|thur|thurs|fri|sat|sun|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|"
    r"january|february|march|april|june|july|august|september|october|november|december)\b"
    r"|\b\d{1,2}/\d{1,2}\b",
    re.I,
)

NAME_LEADIN = re.compile(
    r"\b(?:my name is|name is|name['’]?s|i['’]?m|i am|under the name|booking under|"
    r"reservation under|under|put it under)\s+"
    r"([A-Za-z][A-Za-z'’.\-]*(?:\s+[A-Za-z][A-Za-z'’.\-]*){0,2})",
    re.I,
)
NAME_STRIP_LEAD = re.compile(
    r"^\s*(?:my name is|name is|name['’]?s|i['’]?m|i am|it['’]?s|this is|under|for)\s+", re.I
)
NAME_STOPWORDS = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "today", "tomorrow", "tonight", "hungry", "starving", "looking", "trying",
    "here", "ready", "good", "fine", "okay", "ok", "yes", "no", "sure", "thanks",
}
# If any of these appear in a candidate name, it's really a correction/booking
# phrase ("actually make it 6"), not a name — reject it.
NAME_REJECT = NAME_STOPWORDS | {
    "actually", "make", "change", "move", "update", "instead", "book", "reserve",
    "reschedule", "switch", "want", "like", "need", "can", "could", "please",
    "table", "party", "reservation", "it", "set", "them", "also", "the", "a",
    "people", "guests", "of", "us", "pm", "am", "at", "for", "and",
}

NOTE_KW = re.compile(
    r"\b(anniversar\w*|birthday|celebrat\w*|engagement|proposal|honeymoon|"
    r"window|booth|patio|outdoor|outside|quiet|corner|view|"
    r"wheelchair|accessible|high ?chair|stroller|"
    r"allerg\w*|gluten|vegan|vegetarian|nut|nuts|dairy|shellfish|kosher|halal|dietary|"
    r"special occasion|date night)\b",
    re.I,
)
NOTE_STRIP = re.compile(
    r"^\s*(?:and|also|oh|btw|by the way|it['’]?s|its|we['’]?re|we are|i['’]?m|i am|"
    r"we have|i have|we['’]?d like|i['’]?d like|can we get|could we get|"
    r"we would like|please|just)\s+",
    re.I,
)

YES_RE = re.compile(
    r"\b(yes|yep|yeah|yup|sure|please do|go ahead|confirm|confirmed|correct|"
    r"that['’]?s right|sounds good|ok|okay|do it|book it|cancel it|that works|"
    r"perfect|great)\b",
    re.I,
)
NO_RE = re.compile(
    r"\b(?:no(?!\s+(?:problem|worries|prob\b))|nope|nah|don['’]?t|do not|"
    r"cancel that|never ?mind|stop|not really|neither|none|leave it|keep it)\b",
    re.I,
)
NEG_EXACT = {"no", "nope", "nah", "n"}
ABORT_RE = re.compile(r"^\s*(cancel|never ?mind|stop|forget it|start over|nvm)\s*\.?\s*$", re.I)

CANCEL_KW = re.compile(r"\b(cancel|delete)\b", re.I)
MODIFY_KW = re.compile(r"\b(change|modify|reschedul\w*|move|update|switch|instead|make it)\b", re.I)
BOOK_KW = re.compile(r"\b(book|reserve|reservation|table|set up a)\b", re.I)
LOOKUP_KW = re.compile(r"\b(look ?up|what['’]?s my|what is my|details|find my|check my|pull up)\b", re.I)
GREET_KW = re.compile(r"\b(hi|hello|hey|good (?:morning|evening|afternoon))\b", re.I)
HELP_KW = re.compile(r"\b(help|what can you (?:do|help)|how does this work|what do you do)\b", re.I)

# Out-of-scope (US-6)
MENU_PRICE_RE = re.compile(
    r"\b(menu|price|prices|pricing|cost|how much|wine list|drink list|cocktail|"
    r"dessert menu|appetizers?|entr[ée]es?|what do you (?:serve|have to eat)|recommend)\b",
    re.I,
)
ORDER_RE = re.compile(r"\b(order (?:food|delivery|takeout|a |some|the )|place an order|i want to order|can i order)\b", re.I)
DELIVERY_RE = re.compile(r"\b(deliver|delivery|take ?out|to ?go)\b", re.I)
ALLERGEN_Q = re.compile(r"\b(is|are|does|do|what|which|any)\b.{0,40}\b(gluten|nut|nuts|dairy|vegan|vegetarian|allerg\w*|ingredient|contain)\b", re.I)


# --------------------------------------------------------------------- extraction
class Extraction:
    __slots__ = ("party_size", "date", "time", "name", "phone", "code", "notes", "yes", "intents")

    def __init__(self):
        self.party_size: Optional[int] = None
        self.date: Optional[str] = None
        self.time: Optional[str] = None
        self.name: Optional[str] = None
        self.phone: Optional[str] = None
        self.code: Optional[str] = None
        self.notes: Optional[str] = None
        self.yes: Optional[bool] = None
        self.intents: set[str] = set()


def _to_int(token: str) -> Optional[int]:
    token = token.lower()
    if token.isdigit():
        return int(token)
    return WORD_NUM.get(token)


def _snap_time(h: int, m: int) -> str:
    total = h * 60 + m
    snapped = int(total / 30 + 0.5) * 30
    snapped %= 24 * 60
    return f"{snapped // 60:02d}:{snapped % 60:02d}"


def _extract_time(text: str, state: ConversationState) -> tuple[Optional[str], Optional[str]]:
    m = TIME_AMPM.search(text)
    if m:
        h, mm, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
        if ap == "p" and h != 12:
            h += 12
        if ap == "a" and h == 12:
            h = 0
        return _snap_time(h, mm), m.group(0)
    m = TIME_24.search(text)
    if m:
        return _snap_time(int(m.group(1)), int(m.group(2))), m.group(0)
    m = TIME_OCLOCK.search(text)
    if m:
        h = int(m.group(1))
        if 1 <= h <= 11:
            h += 12  # dinner service: assume evening
        return _snap_time(h, 0), m.group(0)
    if NOON_RE.search(text):
        return "12:00", "noon"
    if state.awaiting == "time":  # bare hour only when we explicitly asked for a time
        m = re.search(rf"\b({_NUMWORD})\b", text, re.I)
        if m:
            h = WORD_NUM[m.group(1).lower()]
            if 1 <= h <= 11:
                h += 12
            return _snap_time(h, 0), m.group(0)
        m = re.search(r"\b(\d{1,2})\b", text)
        if m:
            h = int(m.group(1))
            if 1 <= h <= 11:
                h += 12
            if 0 <= h <= 23:
                return _snap_time(h, 0), m.group(0)
    return None, None


_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_WEEKDAY = (
    r"mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday|s)?|thu(?:r(?:s(?:day)?)?)?|"
    r"fri(?:day)?|sat(?:urday)?|sun(?:day)?"
)
_REL_DAY = re.compile(r"\b(today|tonight|tomorrow|tmrw|yesterday)\b", re.I)
_MONTH_DAY = re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTHS})\b", re.I)
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_WEEKDAY_RE = re.compile(rf"\b(?:(next|this|coming)\s+)?({_WEEKDAY})\b", re.I)


def _extract_date(text: str) -> tuple[Optional[str], Optional[str]]:
    """Isolate a date phrase, then hand only that phrase to dateparser.

    Running dateparser on the whole noisy message mis-parses ('Book a table on
    Monday June 29' once resolved to 2029-06-01); targeting the phrase fixes it.
    """
    m = ISO_RE.search(text)
    if m:
        try:
            return dt.date.fromisoformat(m.group(1)).isoformat(), m.group(1)
        except ValueError:
            pass

    settings = {
        "RELATIVE_BASE": clock.now(),
        "PREFER_DATES_FROM": "future",
        "RETURN_AS_TIMEZONE_AWARE": False,
    }
    # Most specific first: relative day, month+day, day+month, numeric, weekday.
    for pat, extra in (
        (_REL_DAY, {}),
        (_MONTH_DAY, {}),
        (_DAY_MONTH, {}),
        (_NUMERIC_DATE, {"DATE_ORDER": "MDY"}),
        (_WEEKDAY_RE, {}),
    ):
        m = pat.search(text)
        if not m:
            continue
        parsed = dateparser.parse(m.group(0), settings={**settings, **extra}, languages=["en"])
        if parsed:
            return parsed.date().isoformat(), m.group(0)
    return None, None


_TARGET_NAME_RE = re.compile(
    r"\b(?:for|under|named|name(?:d)?(?: is)?|by the name of)\s+"
    r"([A-Za-z][A-Za-z'’.\-]*(?:\s+[A-Za-z][A-Za-z'’.\-]*){0,2})",
    re.I,
)


_NAME_CONNECTORS = {"on", "at", "for", "in", "this", "next", "coming",
                    "today", "tonight", "tomorrow", "yesterday"}


def _name_from_phrase(message: str) -> Optional[str]:
    """Pull a guest name out of 'cancel the booking for Alex Rivera on <date>'."""
    m = _TARGET_NAME_RE.search(message)
    if not m:
        return None
    toks: list[str] = []
    for t in m.group(1).split():  # stop before connectors so we don't grab 'on <date>'
        if t.lower() in _NAME_CONNECTORS or t.lower() in WORD_NUM:
            break
        toks.append(t)
    if not toks or any(t.lower() in NAME_REJECT or t.lower() in WORD_NUM for t in toks):
        return None
    cand = " ".join(toks)
    if NOTE_KW.search(cand):
        return None
    return _clean_name(cand)


def _extract_phone(text: str) -> Optional[str]:
    for m in PHONE_RE.finditer(text):
        cand = m.group(1)
        if len(re.sub(r"\D", "", cand)) >= 7:
            return cand.strip()
    return None


def _extract_party(text: str, state: ConversationState) -> tuple[Optional[int], Optional[str]]:
    """Return (party_size, matched_span). The span is removed from the residual
    before date parsing so dateparser can't eat the party number as a day."""
    m = SOLO_RE.search(text)
    if m:
        return 1, m.group(0)
    m = COUPLE_RE.search(text)
    if m:
        return 2, m.group(0)
    m = PARTY_FOR.search(text) or PARTY_PEOPLE.search(text)
    if m:
        return _to_int(m.group(1)), m.group(0)
    if state.awaiting == "party":
        m = re.search(r"\b(\d{1,2})\b", text)
        if m:
            return int(m.group(1)), m.group(0)
        m = re.search(rf"\b({_NUMWORD})\b", text, re.I)
        if m:
            return WORD_NUM[m.group(1).lower()], m.group(0)
    return None, None


def _extract_notes(message: str) -> Optional[str]:
    if not NOTE_KW.search(message):
        return None
    for clause in re.split(r"[,.;]|\bby the way\b|\bbtw\b", message, flags=re.I):
        if clause and NOTE_KW.search(clause):
            note = NOTE_STRIP.sub("", clause).strip(" .,-!")
            return note or NOTE_KW.search(message).group(0)
    return None


def _clean_name(s: str) -> str:
    out = []
    for tok in s.split():
        out.append(tok if any(c.isupper() for c in tok[1:]) else tok[:1].upper() + tok[1:])
    return " ".join(out)


def _extract_name(message: str, residual: str, state: ConversationState) -> Optional[str]:
    m = NAME_LEADIN.search(message)
    if m:
        cand = m.group(1).strip()
        first = cand.split()[0].lower() if cand.split() else ""
        if not NOTE_KW.search(cand) and first not in NAME_STOPWORDS:
            return _clean_name(cand)
    if state.awaiting in ("details", "name"):
        chunk = re.split(r"[,;.]", residual)[0]
        chunk = NAME_STRIP_LEAD.sub("", chunk).strip()
        if NOTE_KW.search(chunk):
            chunk = NOTE_KW.sub("", chunk).strip()
        tokens = [t for t in chunk.split() if t]
        if any(t.lower() in NAME_REJECT for t in tokens):
            return None  # a correction/booking phrase, not a name
        if 1 <= len(tokens) <= 4 and all(re.fullmatch(r"[A-Za-z][A-Za-z'’.\-]*", t) for t in tokens):
            return _clean_name(" ".join(tokens))
    return None


def _extract_yesno(message: str) -> Optional[bool]:
    s = message.strip().lower()
    if s in NEG_EXACT:
        return False
    # Negation dominates affirmation, so a stray "ok" can't flip "ok no, don't" to
    # a yes and fire a destructive cancel/modify against the guest's intent.
    if NO_RE.search(message):
        return False
    if YES_RE.search(message):
        return True
    return None


def _detect_intents(message: str) -> set[str]:
    ints: set[str] = set()
    if CANCEL_KW.search(message):
        ints.add("cancel")
    if MODIFY_KW.search(message):
        ints.add("modify")
    if LOOKUP_KW.search(message):
        ints.add("lookup")
    if BOOK_KW.search(message):
        ints.add("book")
    if HELP_KW.search(message):
        ints.add("help")
    if GREET_KW.search(message):
        ints.add("greet")
    return ints


def extract(message: str, state: ConversationState) -> Extraction:
    ext = Extraction()
    residual = message

    m = CODE_RE.search(residual)
    if m:
        ext.code = ("BV-" + m.group(1)).upper()
        residual = residual.replace(m.group(0), " ")

    t_val, t_span = _extract_time(residual, state)
    if t_val:
        ext.time = t_val
        if t_span:
            residual = residual.replace(t_span, " ", 1)

    # Party BEFORE date, removing its span, so dateparser can't read "for 6" as the 6th.
    party_val, party_span = _extract_party(residual, state)
    if party_val is not None:
        ext.party_size = party_val
        if party_span:
            residual = residual.replace(party_span, " ", 1)

    d_val, d_span = _extract_date(residual)
    if d_val:
        ext.date = d_val
        if d_span:
            residual = residual.replace(d_span, " ", 1)

    ph = _extract_phone(residual)
    if ph:
        ext.phone = ph
        residual = residual.replace(ph, " ")

    ext.notes = _extract_notes(message)
    ext.name = _extract_name(message, residual, state)
    ext.yes = _extract_yesno(message)
    ext.intents = _detect_intents(message)
    return ext


def _has_booking_entity(ext: Extraction) -> bool:
    return any(v is not None for v in (ext.party_size, ext.date, ext.time))


# --------------------------------------------------------------------- helpers
def _humanize_times(times: list[str]) -> str:
    pretty = [fmt_time(t) for t in times]
    if len(pretty) == 1:
        return pretty[0]
    if len(pretty) == 2:
        return f"{pretty[0]} or {pretty[1]}"
    return ", ".join(pretty[:-1]) + f", or {pretty[-1]}"


# --------------------------------------------------------------------- brain
class DeterministicBrain:
    name = "deterministic"

    def respond(self, state: ConversationState, message: str, agent) -> Reply:
        ext = extract(message, state)
        aw = state.awaiting

        # Universal abort from any in-progress flow (book / cancel / modify), so a
        # guest is never trapped in a code prompt.
        if ABORT_RE.match(message) and state.flow in ("book", "cancel", "modify"):
            state.reset_flow()
            return Reply("No problem — I've cancelled that. Anything else I can help with?")

        # Let a "for <name> on <date>" reply identify the target at the code prompt too.
        if aw in ("cancel_target", "modify_target") and not ext.code and not ext.name:
            ext.name = _name_from_phrase(message)

        # Pending confirmations / sub-prompts take priority.
        if aw == "confirm_cancel":
            return self._confirm_cancel(state, ext, agent)
        if aw == "confirm_modify":
            return self._confirm_modify(state, ext, agent)
        if aw == "choose_alt":
            return self._choose_alt(state, message, ext, agent)
        if aw == "cancel_target":
            return self._cancel_target(state, ext, agent)
        if aw == "modify_target":
            return self._modify_target(state, ext, agent)
        if aw == "modify_changes":
            return self._modify_changes(state, ext, agent)
        if aw == "confirm_name":
            return self._confirm_name(state, ext, agent)

        # Out-of-scope (US-6) — refuse, but keep any booking context alive.
        topic = self._out_of_scope(message, ext)
        if topic:
            return self._refuse(state, topic)

        # If we're actively collecting booking fields, treat this as booking input
        # (a correction like "actually make it 6" must not jump to the modify flow).
        if aw in ("party", "date", "time", "details", "name", "phone"):
            return self._continue_booking(state, agent, ext)

        # Intent routing (cancel/modify beat 'book' since they share keywords).
        if "cancel" in ext.intents or "modify" in ext.intents:
            if not ext.code and not ext.name:
                ext.name = _name_from_phrase(message)  # "...for Alex Rivera on <date>"
        if "cancel" in ext.intents:
            return self._start_cancel(state, ext, agent)
        if "modify" in ext.intents:
            return self._start_modify(state, ext, agent)
        if "lookup" in ext.intents and ext.code:
            return self._lookup(state, ext, agent)

        if (
            "book" in ext.intents
            or state.flow == "book"
            or aw in ("party", "date", "time", "details", "name", "phone")
            or _has_booking_entity(ext)
        ):
            return self._continue_booking(state, agent, ext)

        if ext.code:
            return self._lookup(state, ext, agent)
        if "greet" in ext.intents or "help" in ext.intents:
            return Reply(self._greeting())
        return Reply(self._fallback())

    # -------------------------------------------------- booking
    def _continue_booking(self, state: ConversationState, agent, ext: Extraction) -> Reply:
        state.flow = "book"
        p = state.pending
        if ext.party_size is not None:
            p["party_size"] = ext.party_size
        if ext.date is not None:
            p["date"] = ext.date
        if ext.time is not None:
            p["time"] = ext.time
        if ext.name:
            p["guest_name"] = ext.name
        if ext.phone:
            p["phone"] = ext.phone
        if ext.notes:
            p["notes"] = (p.get("notes", "") + "; " + ext.notes).strip("; ") if p.get("notes") else ext.notes

        if p.get("party_size", 0) and p["party_size"] > config.MAX_PARTY_SIZE:
            size = p.pop("party_size")
            state.awaiting = "party"
            return Reply(
                f"For a party of {size}, please call us at {config.RESTAURANT_PHONE} — "
                f"we seat up to {config.MAX_PARTY_SIZE} online. How many should I book for instead?"
            )

        if "party_size" not in p:
            state.awaiting = "party"
            return Reply("Happy to help! How many people will be dining?")
        if "date" not in p:
            state.awaiting = "date"
            return Reply("Great. What date would you like? (We're open Tuesday through Sunday.)")
        if "time" not in p:
            state.awaiting = "time"
            return Reply("And what time? We seat from 5:00pm to 9:30pm, in 30-minute slots.")

        avail = agent.call_tool("check_availability", date=p["date"], time=p["time"], party_size=p["party_size"])
        if not avail["available"]:
            return self._handle_unavailable(state, avail)

        if "guest_name" not in p and "phone" not in p:
            state.awaiting = "details"
            return Reply(
                f"{fmt_when(p['date'], p['time'])} for {p['party_size']} — that slot is available. "
                "Can I get a name and phone number for the reservation?"
            )
        if "guest_name" not in p:
            state.awaiting = "name"
            return Reply("Great — what name should I put the reservation under?")
        if "phone" not in p:
            state.awaiting = "phone"
            return Reply("And a phone number in case we need to reach you?")

        return self._finalize_booking(state, agent)

    def _handle_unavailable(self, state: ConversationState, avail: dict) -> Reply:
        reason = avail["reason"] or "That slot isn't available."
        alts = avail["alternatives"]
        if alts:
            state.awaiting = "choose_alt"
            state.context["alts"] = alts
            return Reply(
                f"{fmt_when(avail['date'], avail['time'])} is fully booked for a party of "
                f"{avail['party_size']}. I can offer {_humanize_times(alts)} — would any of those work?"
            )
        rl = reason.lower()
        if "monday" in rl or "60 days" in rl:
            state.pending.pop("date", None)
            state.awaiting = "date"
            return Reply(f"{reason} What other date works for you?")
        if "past" in rl:
            d = state.pending.get("date")
            if d and dt.date.fromisoformat(d) < clock.today():
                state.pending.pop("date", None)
                state.pending.pop("time", None)
                state.awaiting = "date"
                return Reply(f"{reason} What date would you like instead?")
            state.pending.pop("time", None)
            state.awaiting = "time"
            return Reply(f"{reason} What time would you like instead?")
        if "30-minute" in rl or "slot" in rl:
            state.pending.pop("time", None)
            state.awaiting = "time"
            return Reply(f"{reason} What time works?")
        state.pending.pop("time", None)
        state.awaiting = "time"
        return Reply(f"{reason} Want to try a different time?")

    def _finalize_booking(self, state: ConversationState, agent) -> Reply:
        p = state.pending
        try:
            res = agent.call_tool(
                "create_reservation",
                date=p["date"], time=p["time"], party_size=p["party_size"],
                guest_name=p["guest_name"], phone=p["phone"], notes=p.get("notes", ""),
            )
        except ReservationError as exc:
            msg = exc.message
            state.reset_flow()
            if "already have a reservation" in msg:
                msg += " Just let me know if you'd like to change it."
            return Reply(msg)
        state.reset_flow()
        text = (
            f"You're all set! {res.guest_name}, party of {res.party_size}, "
            f"{fmt_when(res.date, res.time)}. Your confirmation code is {res.confirmation_code}."
        )
        if res.notes:
            text += (
                f" I've noted: \"{res.notes}\" — I'll pass that to the team, "
                "though I can't guarantee specific seating."
            )
        return Reply(text, confirmation_code=res.confirmation_code)

    def _choose_alt(self, state: ConversationState, message: str, ext: Extraction, agent) -> Reply:
        p = state.pending
        # A new date or party size in the same reply is a correction, not an alt
        # pick — apply it and re-check rather than booking the original slot.
        if (ext.date and ext.date != p.get("date")) or (
            ext.party_size and ext.party_size != p.get("party_size")
        ):
            state.awaiting = None
            state.context.pop("alts", None)
            return self._continue_booking(state, agent, ext)
        alts = state.context.get("alts", [])
        low = message.lower()
        pick = None
        if ext.time and ext.time in config.SLOT_STRINGS:
            pick = ext.time
        elif "first" in low or "earliest" in low or "earlier" in low:
            pick = alts[0] if alts else None
        elif "second" in low and len(alts) > 1:
            pick = alts[1]
        elif "third" in low and len(alts) > 2:
            pick = alts[2]
        elif "last" in low or "later" in low or "latest" in low:
            pick = alts[-1] if alts else None
        elif "either" in low or (ext.yes and len(alts) == 1):
            pick = alts[0] if alts else None

        if pick:
            state.pending["time"] = pick
            state.awaiting = None
            state.context.pop("alts", None)
            return self._continue_booking(state, agent, Extraction())
        if ext.yes is False or "neither" in low or "none" in low:
            state.pending.pop("time", None)
            state.awaiting = None
            state.context.pop("alts", None)
            return Reply("No problem — what other day or time would you like?")
        return Reply(f"Which would you prefer — {_humanize_times(alts)}?")

    def _confirm_name(self, state: ConversationState, ext: Extraction, agent) -> Reply:
        if ext.yes is False:
            state.awaiting = "name"
            state.pending.pop("guest_name", None)
            return Reply("No worries — what name should I use?")
        state.awaiting = None
        return self._continue_booking(state, agent, Extraction())

    # -------------------------------------------------- cancel
    def _start_cancel(self, state: ConversationState, ext: Extraction, agent) -> Reply:
        target = self._resolve_target(state, ext, agent)
        if target == "ambiguous":
            state.flow, state.awaiting = "cancel", "cancel_target"
            return Reply("I found more than one — what's the confirmation code?")
        if target == "notfound":
            state.flow, state.awaiting = "cancel", "cancel_target"
            return Reply("I couldn't find that reservation. What's the confirmation code?")
        if target is None:
            state.flow, state.awaiting = "cancel", "cancel_target"
            return Reply("Sure — what's the confirmation code? (Or the name and date on the reservation.)")
        if target.status != CONFIRMED:
            state.reset_flow()
            return Reply(f"Reservation {target.confirmation_code} is already cancelled.")
        state.flow, state.awaiting = "cancel", "confirm_cancel"
        state.context["code"] = target.confirmation_code
        return Reply(f"That's {fmt_reservation(target)}. Shall I cancel it?")

    def _cancel_target(self, state: ConversationState, ext: Extraction, agent) -> Reply:
        if ext.yes is False and not ext.code and not ext.name:
            state.reset_flow()
            return Reply("Okay, never mind. Anything else I can help with?")
        return self._start_cancel(state, ext, agent)

    def _confirm_cancel(self, state: ConversationState, ext: Extraction, agent) -> Reply:
        if ext.yes is True:
            code = state.context.get("code")
            try:
                agent.call_tool("cancel_reservation", confirmation_code=code)
            except ReservationError as exc:
                state.reset_flow()
                return Reply(exc.message)
            state.reset_flow()
            return Reply("Cancelled. We hope to see you another time.")
        if ext.yes is False:
            state.reset_flow()
            return Reply("Okay, I've left your reservation as is. Anything else?")
        return Reply("Just to confirm — should I cancel it? (yes / no)")

    # -------------------------------------------------- modify
    def _start_modify(self, state: ConversationState, ext: Extraction, agent) -> Reply:
        target = self._resolve_target(state, ext, agent)
        if target in ("ambiguous", "notfound") or target is None:
            state.flow, state.awaiting = "modify", "modify_target"
            return Reply("Sure — which reservation? Share the confirmation code (or the name and date).")
        if target.status != CONFIRMED:
            state.reset_flow()
            return Reply(f"Reservation {target.confirmation_code} is cancelled, so there's nothing to change.")
        state.flow = "modify"
        state.context["code"] = target.confirmation_code
        changes = self._changes_from_ext(ext, target)
        if not changes:
            state.awaiting = "modify_changes"
            return Reply(
                f"Got it — {fmt_reservation(target)}. "
                "What would you like to change (date, time, or party size)?"
            )
        return self._propose_modify(state, target, changes)

    def _modify_target(self, state: ConversationState, ext: Extraction, agent) -> Reply:
        if ext.yes is False and not ext.code and not ext.name:
            state.reset_flow()
            return Reply("Okay, never mind. Anything else I can help with?")
        return self._start_modify(state, ext, agent)

    def _modify_changes(self, state: ConversationState, ext: Extraction, agent) -> Reply:
        code = state.context.get("code")
        target = agent.call_tool("get_reservation", confirmation_code=code)
        if target is None:
            state.reset_flow()
            return Reply("Hmm, I lost track of that reservation. Could you share the code again?")
        changes = self._changes_from_ext(ext, target)
        if not changes:
            return Reply("What would you like to change — the date, the time, or the party size?")
        return self._propose_modify(state, target, changes)

    def _propose_modify(self, state: ConversationState, target, changes: dict) -> Reply:
        state.context["code"] = target.confirmation_code
        state.context["changes"] = changes
        state.awaiting = "confirm_modify"
        return Reply(f"I'll {self._describe_changes(changes)}. Shall I go ahead?")

    def _confirm_modify(self, state: ConversationState, ext: Extraction, agent) -> Reply:
        if ext.yes is True:
            code = state.context.get("code")
            changes = state.context.get("changes", {})
            try:
                res = agent.call_tool("modify_reservation", confirmation_code=code, changes=changes)
            except UnavailableError as exc:
                state.awaiting = "modify_changes"
                return Reply(f"{exc.message} Want to try a different time?")
            except ReservationError as exc:
                state.reset_flow()
                return Reply(exc.message)
            state.reset_flow()
            return Reply(
                f"Done — your reservation is now {fmt_reservation(res)}. "
                f"Your code is still {res.confirmation_code}.",
                confirmation_code=res.confirmation_code,
            )
        if ext.yes is False:
            state.awaiting = "modify_changes"
            return Reply("Okay — what would you like to change instead?")
        # Maybe they specified a different change instead of yes/no.
        code = state.context.get("code")
        target = agent.call_tool("get_reservation", confirmation_code=code)
        if target:
            changes = self._changes_from_ext(ext, target)
            if changes:
                return self._propose_modify(state, target, changes)
        return Reply("Should I make that change? (yes / no)")

    def _changes_from_ext(self, ext: Extraction, target) -> dict:
        changes: dict = {}
        if ext.date and ext.date != target.date:
            changes["date"] = ext.date
        if ext.time and ext.time != target.time:
            changes["time"] = ext.time
        if ext.party_size and ext.party_size != target.party_size:
            changes["party_size"] = ext.party_size
        if ext.notes:
            changes["notes"] = ext.notes
        return changes

    def _describe_changes(self, changes: dict) -> str:
        bits = []
        if "date" in changes and "time" in changes:
            bits.append(f"move it to {fmt_when(changes['date'], changes['time'])}")
        elif "date" in changes:
            bits.append(f"move it to {fmt_date(changes['date'])}")
        elif "time" in changes:
            bits.append(f"move it to {fmt_time(changes['time'])}")
        if "party_size" in changes:
            bits.append(f"change the party size to {changes['party_size']}")
        if "notes" in changes:
            bits.append(f'add the note "{changes["notes"]}"')
        return " and ".join(bits) or "update it"

    # -------------------------------------------------- lookup / scope / misc
    def _lookup(self, state: ConversationState, ext: Extraction, agent) -> Reply:
        res = agent.call_tool("get_reservation", confirmation_code=ext.code)
        if res is None:
            return Reply(f"I couldn't find a reservation with code {ext.code}.")
        return Reply(
            f"Here's what I have: {fmt_reservation(res)}. Confirmation code {res.confirmation_code}.",
            confirmation_code=res.confirmation_code,
        )

    def _resolve_target(self, state: ConversationState, ext: Extraction, agent):
        """Return a Reservation, None (no info), or 'ambiguous'/'notfound'."""
        if ext.code:
            return agent.call_tool("get_reservation", confirmation_code=ext.code)
        if ext.name and ext.date:
            matches = agent.store.find_active(ext.name, ext.date)
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                return "ambiguous"
            return "notfound"
        return None

    def _out_of_scope(self, message: str, ext: Extraction) -> Optional[str]:
        if MENU_PRICE_RE.search(message):
            return "menu items or prices"
        if ORDER_RE.search(message):
            return "placing food orders"
        if DELIVERY_RE.search(message):
            return "delivery or takeout"
        if ALLERGEN_Q.search(message) and not _has_booking_entity(ext):
            return "specific ingredients or allergens"
        return None

    def _refuse(self, state: ConversationState, topic: str) -> Reply:
        text = (
            f"I'm just the reservations assistant, so I can't help with {topic}. "
            f"I can book, change, or cancel a table — for anything else, "
            f"please call {config.RESTAURANT_NAME} at {config.RESTAURANT_PHONE}."
        )
        if state.flow == "book" and state.awaiting:
            text += " In the meantime, shall we finish your booking?"
        return Reply(text)

    def _greeting(self) -> str:
        return (
            f"Hi! I'm the {config.RESTAURANT_NAME} reservations assistant. "
            "I can book a table, change or cancel a reservation, or check availability. "
            "What can I do for you?"
        )

    def _fallback(self) -> str:
        return (
            "I can help you book, change, or cancel a table at "
            f"{config.RESTAURANT_NAME}. Would you like to make a reservation?"
        )
