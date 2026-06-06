"""
NLP utilities for the chatbot:
  - Natural date parsing ("tomorrow", "next monday", "june 15", "in 3 days")
  - Symptom -> department routing
  - Emergency keyword detection
  - Conversational intent helpers (deterministic, complements LLM)
  - Yes/No normalization
"""
import re
from datetime import datetime, timedelta

# -- Symptom routing map -----------------------------------------------------
SYMPTOM_DEPT_MAP = {
    "Cardiology": [
        "chest pain", "heart", "palpit", "cardio", "blood pressure",
        "hypertension", "shortness of breath", "cholesterol", "arrhythm"
    ],
    "Neurology": [
        "headache", "migraine", "seizure", "dizzy", "vertigo", "memory",
        "neurolog", "nerve", "numbness", "tingling", "stroke"
    ],
    "Orthopedics": [
        "bone", "joint", "fracture", "broken", "back pain", "knee", "shoulder",
        "ankle", "wrist", "arthritis", "orthop", "sprain", "torn", "muscle pain"
    ],
    "Pediatrics": [
        "child", "kid", "baby", "infant", "toddler", "pediatric", "paediatric",
        "my son", "my daughter", "newborn"
    ],
    "Dermatology": [
        "skin", "rash", "acne", "eczema", "psoriasis", "mole", "hair loss",
        "dermat", "itch", "wart", "scar"
    ],
    "General Medicine": [
        "fever", "cold", "cough", "flu", "checkup", "check-up", "check up",
        "general", "tired", "fatigue", "weakness", "stomach", "nausea",
        "vomit", "diarr", "sore throat", "body pain"
    ]
}

# -- Emergency keywords (escalate, do not book) ------------------------------
EMERGENCY_KEYWORDS = [
    "severe chest pain", "can't breathe", "cannot breathe", "not breathing",
    "heart attack", "stroke", "unconscious", "passed out", "bleeding heavily",
    "heavy bleeding", "suicide", "overdose", "poisoning", "choking",
    "severe head injury", "broken neck", "paralyzed", "convulsion",
    "anaphylactic", "anaphylaxis", "911", "1122", "115", "emergency room",
    "dying", "life threatening"
]

# Word-boundary patterns for short tokens that need to be exact words
import re as _re
EMERGENCY_REGEX = [
    _re.compile(r"\b(emergency|ambulance)\b", _re.I),
    _re.compile(r"\bER\b"),   # uppercase ER abbreviation only — avoids "Carter", "doctor", etc.
]

# -- Medical question keywords (we politely decline) -------------------------
MEDICAL_QUESTION_PATTERNS = [
    r"\bwhat.*(should|do).*(take|treatment)",
    r"\bhow.*(treat|cure|heal)",
    r"\bwhich medicine",
    r"\bcan i take",
    r"\bdiagnos",
    r"\bprescrib",
    r"\bdosage",
    r"\bside effect"
]

# -- Yes / no normalization --------------------------------------------------
YES_WORDS = {"yes","y","yep","yeah","yup","yea","sure","ok","okay","confirm",
             "do it","go ahead","please","correct","right","absolutely","fine",
             "alright","sounds good","that works","proceed"}
NO_WORDS  = {"no","n","nope","nah","never","cancel","stop","wait","not now",
             "negative","don't","dont","no thanks","no thank you"}


def detect_emergency(text):
    t = text.lower()
    for kw in EMERGENCY_KEYWORDS:
        if kw in t:
            return True
    for pat in EMERGENCY_REGEX:
        if pat.search(text):
            return True
    return False


def detect_medical_question(text):
    t = text.lower()
    return any(re.search(p, t) for p in MEDICAL_QUESTION_PATTERNS)


def detect_department_from_symptom(text):
    """Return the most likely department for a user's symptom description, or None."""
    t = text.lower()
    scores = {}
    for dept, keywords in SYMPTOM_DEPT_MAP.items():
        score = sum(1 for k in keywords if k in t)
        if score > 0:
            scores[dept] = score
    if not scores:
        return None
    return max(scores.items(), key=lambda x: x[1])[0]


def detect_symptom_keyword(text):
    """Return the first matched symptom phrase (for explanation), or None."""
    t = text.lower()
    for kws in SYMPTOM_DEPT_MAP.values():
        for k in kws:
            if k in t:
                return k
    return None


def is_yes(text):
    t = (text or "").strip().lower()
    if not t:
        return False
    return t in YES_WORDS or any(t.startswith(w + " ") for w in YES_WORDS)


def is_no(text):
    t = (text or "").strip().lower()
    if not t:
        return False
    return t in NO_WORDS or any(t.startswith(w + " ") for w in NO_WORDS)


def detect_earliest_intent(text):
    """User wants the earliest available slot."""
    t = text.lower()
    return any(k in t for k in (
        "earliest", "soonest", "asap", "first available", "next available",
        "as soon as possible", "anytime", "any slot", "any time", "whatever"
    ))


def detect_list_intent(text):
    """User wants to browse doctors/departments."""
    t = text.lower()
    if any(k in t for k in ("list doctor", "all doctor", "show doctor",
                            "which doctor", "what doctor", "available doctor",
                            "see the doctor list")):
        return "doctors"
    if any(k in t for k in ("list department", "what department", "which department",
                            "all department", "departments do you", "specialti",
                            "what specialt")):
        return "departments"
    return None


def detect_help_intent(text):
    t = text.lower().strip()
    return t in ("help", "?", "menu") or any(k in t for k in (
        "what can you do", "what do you do", "how do you work", "how does this work",
        "what are you", "who are you", "your features", "your capabilities"
    ))


# -- Date parsing ------------------------------------------------------------
WEEKDAY_MAP = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6
}
MONTH_MAP = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12
}


def parse_natural_date(text, ref=None):
    """
    Returns YYYY-MM-DD string or None. Handles:
      today, tomorrow, day after tomorrow,
      next monday, this friday,
      in 3 days, in a week, in two weeks,
      june 15, 15 june, 2026-06-15, 15/6, 15/06/2026
    """
    if ref is None:
        ref = datetime.utcnow()
    t = text.lower().strip()

    # ISO format
    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if iso:
        return iso.group(0)

    # DD/MM or DD/MM/YYYY
    dmy = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t)
    if dmy:
        d, m = int(dmy.group(1)), int(dmy.group(2))
        y = int(dmy.group(3)) if dmy.group(3) else ref.year
        if y < 100:
            y += 2000
        try:
            return datetime(y, m, d).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Relative: "in 3 days", "in a week", "in two weeks"
    NUM_WORDS = {"a":1,"an":1,"one":1,"two":2,"three":3,"four":4,"five":5,
                 "six":6,"seven":7,"eight":8,"nine":9,"ten":10}
    rel = re.search(r"\bin\s+(\d+|" + "|".join(NUM_WORDS) + r")\s+(day|week)s?\b", t)
    if rel:
        n_raw = rel.group(1)
        n = int(n_raw) if n_raw.isdigit() else NUM_WORDS[n_raw]
        unit = rel.group(2)
        days = n if unit == "day" else n * 7
        return (ref + timedelta(days=days)).strftime("%Y-%m-%d")

    # Today / tomorrow / day after
    if "today" in t:
        return ref.strftime("%Y-%m-%d")
    if "day after tomorrow" in t:
        return (ref + timedelta(days=2)).strftime("%Y-%m-%d")
    if "tomorrow" in t:
        return (ref + timedelta(days=1)).strftime("%Y-%m-%d")

    # Next / this <weekday>
    m = re.search(r"\b(next|this|coming)?\s*(monday|mon|tuesday|tue|tues|wednesday|wed|thursday|thu|thurs|friday|fri|saturday|sat|sunday|sun)\b", t)
    if m:
        target = WEEKDAY_MAP[m.group(2)]
        delta  = (target - ref.weekday()) % 7
        if m.group(1) == "next" or delta == 0:
            delta = delta or 7
        return (ref + timedelta(days=delta)).strftime("%Y-%m-%d")

    # Month-day patterns
    m = re.search(r"\b(" + "|".join(MONTH_MAP) + r")\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?\b", t)
    if m:
        mo = MONTH_MAP[m.group(1)]
        d  = int(m.group(2))
        y  = int(m.group(3)) if m.group(3) else ref.year
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            pass

    m = re.search(r"\b(\d{1,2})\s+(" + "|".join(MONTH_MAP) + r")(?:\s*,?\s*(\d{4}))?\b", t)
    if m:
        d  = int(m.group(1))
        mo = MONTH_MAP[m.group(2)]
        y  = int(m.group(3)) if m.group(3) else ref.year
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def parse_time_period(text):
    """
    Returns ('morning'|'afternoon'|'evening'|None, 'HH:MM'|None).
    Handles: 14:00, 2pm, 2 pm, 2 p.m., noon, midnight, morning, afternoon, evening.
    """
    t = text.lower()

    # HH:MM with optional am/pm
    iso_t = re.search(r"\b(\d{1,2}):(\d{2})\s*([ap]\.?m\.?)?\b", t)
    if iso_t:
        h, mn = int(iso_t.group(1)), int(iso_t.group(2))
        suffix = (iso_t.group(3) or "").replace(".", "")
        if suffix == "pm" and h < 12: h += 12
        if suffix == "am" and h == 12: h = 0
        return None, f"{h:02d}:{mn:02d}"

    # Bare "2pm", "10 am", "2 p.m."
    bare = re.search(r"\b(\d{1,2})\s*([ap]\.?m\.?)\b", t)
    if bare:
        h = int(bare.group(1))
        suffix = bare.group(2).replace(".", "")
        if suffix == "pm" and h < 12: h += 12
        if suffix == "am" and h == 12: h = 0
        return None, f"{h:02d}:00"

    # "X o'clock"
    oc = re.search(r"\b(\d{1,2})\s*o['']?clock\b", t)
    if oc:
        h = int(oc.group(1))
        # default afternoon if 1-7
        if 1 <= h <= 7 and "morning" not in t:
            h += 12
        return None, f"{h:02d}:00"

    if "noon" in t or "midday" in t:
        return None, "12:00"
    if "midnight" in t:
        return None, "00:00"

    if "morning" in t:   return "morning", None
    if "afternoon" in t: return "afternoon", None
    if "evening" in t:   return "evening", None
    return None, None


def in_period(time_str, period):
    try:
        h = int(time_str.split(":")[0])
    except (ValueError, AttributeError):
        return False
    if period == "morning":   return 5  <= h < 12
    if period == "afternoon": return 12 <= h < 17
    if period == "evening":   return 17 <= h < 22
    return False


def filter_slots_by_preference(slots, preferred_date=None, preferred_time=None, period=None):
    """Filter slots based on natural preferences. Returns sorted list (graceful fallback if filters empty)."""
    result = list(slots)
    if preferred_date:
        date_match = [s for s in result if s["date"] == preferred_date]
        if date_match:
            result = date_match
    if preferred_time:
        time_match = [s for s in result if s["time"] == preferred_time]
        if time_match:
            result = time_match
    if period:
        period_match = [s for s in result if in_period(s["time"], period)]
        if period_match:
            result = period_match
    return sorted(result, key=lambda s: (s["date"], s["time"]))


def has_period_in_slots(slots, period):
    return any(in_period(s["time"], period) for s in slots)


def alternative_periods(slots, requested):
    """Return list of period names that DO have slots, excluding `requested`."""
    alts = []
    for p in ("morning", "afternoon", "evening"):
        if p != requested and has_period_in_slots(slots, p):
            alts.append(p)
    return alts
