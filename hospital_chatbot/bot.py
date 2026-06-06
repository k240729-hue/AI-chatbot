"""
Hospital Appointment Chatbot — hybrid LLM + deterministic NLP.

Design:
  - Local LLM (Ollama / Llama 3.2) handles freeform language understanding.
  - Deterministic NLP layer (nlp.py) handles dates, symptoms, intents, yes/no.
  - State machine drives multi-turn flows with explicit confirmation on
    destructive actions (book / reschedule / cancel).
  - Past-date slots are filtered at the data layer.
  - Same-time double bookings are prevented at the data layer.
"""
import os
import json
import difflib
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

import db
import nlp

load_dotenv()

_client = OpenAI(
    api_key="ollama",
    base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
)
MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

SYSTEM_PROMPT = """You are MedAssist, an AI scheduling assistant at City General Hospital.
You handle ONLY appointment booking, rescheduling, cancellation, and account queries.
You NEVER answer medical questions, give diagnoses, or recommend treatments.
You speak briefly and warmly, never use markdown, never use emojis.

You must respond with valid JSON ONLY in this schema:
{
  "intent":         "book|reschedule|cancel|query|greet|small_talk|out_of_scope|unknown",
  "doctor_name":    null or "Dr. Last Name",
  "department":     null or "Cardiology|Neurology|Orthopedics|Pediatrics|Dermatology|General Medicine",
  "preferred_date": null or "YYYY-MM-DD",
  "preferred_time": null or "HH:MM",
  "time_period":    null or "morning|afternoon|evening",
  "symptom_hint":   null or short string (symptom in user text, never advice)
}

Examples:
User: "I need to see a heart doctor next monday morning"
Output: {"intent":"book","doctor_name":null,"department":"Cardiology","preferred_date":null,"preferred_time":null,"time_period":"morning","symptom_hint":"heart"}

User: "cancel my appointment with Dr Patel"
Output: {"intent":"cancel","doctor_name":"Dr. Patel","department":null,"preferred_date":null,"preferred_time":null,"time_period":null,"symptom_hint":null}

User: "move my booking to friday"
Output: {"intent":"reschedule","doctor_name":null,"department":null,"preferred_date":null,"preferred_time":null,"time_period":null,"symptom_hint":null}

User: "what time is it"
Output: {"intent":"out_of_scope","doctor_name":null,"department":null,"preferred_date":null,"preferred_time":null,"time_period":null,"symptom_hint":null}
"""

DEPARTMENTS = ["Cardiology", "Neurology", "Orthopedics", "Pediatrics", "Dermatology", "General Medicine"]


# -- LLM call with strict JSON output ----------------------------------------

def llm_parse(user_message, history=None):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history[-6:])
    messages.append({"role": "user", "content": user_message})
    try:
        resp = _client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.1,
            max_tokens=300,
            response_format={"type": "json_object"}
        )
        raw = resp.choices[0].message.content.strip().strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        return json.loads(raw)
    except Exception as e:
        return {"intent": "unknown", "_error": str(e)}


# -- Hybrid parser: deterministic first, LLM as fallback ---------------------

def parse_user_input(text, history=None):
    """Combines deterministic + LLM signals into one parse dict."""
    out = {
        "intent":         None,
        "doctor_name":    None,
        "department":     None,
        "preferred_date": None,
        "preferred_time": None,
        "time_period":    None,
        "symptom_hint":   None,
        "earliest":       False,
        "emergency":      False,
        "medical_q":      False,
    }

    if nlp.detect_emergency(text):
        out["intent"]    = "emergency"
        out["emergency"] = True
        return out

    if nlp.detect_medical_question(text):
        out["intent"]    = "medical_question"
        out["medical_q"] = True
        return out

    t = text.lower()

    # Help / list intents
    if nlp.detect_help_intent(text):
        out["intent"] = "help"
        return out
    list_kind = nlp.detect_list_intent(text)
    if list_kind == "doctors":
        out["intent"] = "list_doctors"
        return out
    if list_kind == "departments":
        out["intent"] = "list_departments"
        return out

    # Keyword intent detection
    # Order matters — cancel/reschedule/book are checked BEFORE query, because
    # phrases like "cancel my appointment" or "reschedule my appointment" all
    # contain "my appointment" which would otherwise wrongly trigger query.
    if any(k in t for k in ("cancel", "remove my appointment", "delete my appointment")):
        out["intent"] = "cancel"
    elif any(k in t for k in ("reschedule", "change my", "move my", "shift my appointment", "different time")):
        out["intent"] = "reschedule"
    elif any(k in t for k in ("book", "schedule", "make appointment", "see a doctor", "see dr",
                              "appointment with", "appointment for", "i need a doctor",
                              "i want to see", "consult", "visit")):
        out["intent"] = "book"
    elif any(k in t for k in ("show my", "view my", "my appointment", "list my", "see my appointment", "what appointment")):
        out["intent"] = "query"
    elif t.strip() in ("hi", "hello", "hey") or any(k in t for k in ("good morning", "good afternoon", "good evening")):
        out["intent"] = "greet"

    out["earliest"]                    = nlp.detect_earliest_intent(text)
    out["preferred_date"]              = nlp.parse_natural_date(text)
    out["time_period"], out["preferred_time"] = nlp.parse_time_period(text)

    dept_from_symptom = nlp.detect_department_from_symptom(text)
    symptom_word      = nlp.detect_symptom_keyword(text)
    if dept_from_symptom:
        out["department"]   = dept_from_symptom
        out["symptom_hint"] = symptom_word
        if out["intent"] is None:
            out["intent"] = "book"

    for d in DEPARTMENTS:
        if d.lower() in t:
            out["department"] = d
            if out["intent"] is None:
                out["intent"] = "book"

    doctor_match = fuzzy_doctor_match(text)
    if doctor_match:
        out["doctor_name"] = doctor_match
        if out["intent"] is None:
            out["intent"] = "book"

    if out["intent"] is None:
        parsed = llm_parse(text, history)
        out["intent"]         = parsed.get("intent", "unknown")
        out["doctor_name"]    = out["doctor_name"]    or fuzzy_doctor_match(parsed.get("doctor_name") or "")
        out["department"]     = out["department"]     or parsed.get("department")
        out["preferred_date"] = out["preferred_date"] or parsed.get("preferred_date")
        out["preferred_time"] = out["preferred_time"] or parsed.get("preferred_time")
        out["time_period"]    = out["time_period"]    or parsed.get("time_period")

    return out


# -- Fuzzy matching ----------------------------------------------------------

def fuzzy_doctor_match(name):
    if not name:
        return None
    doctors = db.get_all_doctors()
    names = [d["name"] for d in doctors]
    matches = difflib.get_close_matches(name, names, n=1, cutoff=0.5)
    if matches:
        return matches[0]
    name_lower = (name or "").lower()
    for n in names:
        if name_lower in n.lower() or n.lower() in name_lower:
            return n
    for n in names:
        last_name = n.split()[-1].lower()
        if last_name and last_name in name_lower:
            return n
    return None


# -- Formatters --------------------------------------------------------------

def format_slots(slots, max_show=8):
    if not slots:
        return "No available slots."
    shown = slots[:max_show]
    extra = len(slots) - len(shown)
    lines = [f"  {i+1}. {s['date']} at {s['time']}" for i, s in enumerate(shown)]
    if extra > 0:
        lines.append(f"  ...and {extra} more.")
    return "\n".join(lines)


def format_appointments(appts):
    if not appts:
        return "You have no upcoming appointments."
    return "\n".join(
        f"  {i+1}. {a['doctor_name']} ({a['department']}) on {a['date']} at {a['time']}"
        for i, a in enumerate(appts)
    )


# -- Special intent replies --------------------------------------------------

def emergency_reply():
    return (
        "EMERGENCY DETECTED.\n\n"
        "Please call 1122 (Rescue / Ambulance) or 115 IMMEDIATELY, "
        "or go to the nearest emergency room.\n\n"
        "This chatbot only handles appointment scheduling and cannot help with emergencies. "
        "Your safety is the priority."
    )


def medical_question_reply():
    return (
        "I'm an appointment scheduling assistant — I'm not able to give medical advice, "
        "diagnoses, or recommend treatments.\n\n"
        "If you describe your symptom, I can book you with the right specialist. "
        "For example: 'I have chest pain' would route you to Cardiology."
    )


def help_reply():
    return (
        "I can help you with:\n"
        "  - Book an appointment ('book a cardiologist next Monday morning')\n"
        "  - Reschedule an existing appointment ('reschedule my appointment')\n"
        "  - Cancel an appointment ('cancel my appointment')\n"
        "  - View your upcoming appointments ('show my appointments')\n"
        "  - Browse our doctors or departments ('list doctors', 'list departments')\n"
        "  - Symptom-based routing ('I have a headache' -> Neurology)\n"
        "  - Earliest available slot ('soonest cardiology appointment')\n\n"
        "Say 'back' or 'cancel' anytime to exit a flow. For emergencies, call 1122."
    )


def list_all_doctors_text():
    out = []
    for d in db.get_all_doctors():
        out.append(f"  - {d['name']} ({d.get('department','')})")
    return "Our doctors:\n" + "\n".join(out)


def list_all_departments_text():
    counts = {dept: 0 for dept in DEPARTMENTS}
    for d in db.get_all_doctors():
        dept = d.get("department")
        if dept in counts:
            counts[dept] += 1
    lines = [f"  - {dept} ({n} doctor{'s' if n != 1 else ''})" for dept, n in counts.items() if n > 0]
    return "Departments at City General Hospital:\n" + "\n".join(lines)


# ============================================================================
# STATE MACHINE
# ============================================================================
#
# States:
#   awaiting_intent              -> no active flow
#   awaiting_symptom_confirm     -> proposed department from symptom, awaiting OK
#   awaiting_doctor_choice       -> showed dept doctor list
#   awaiting_slot_choice         -> showed slots
#   awaiting_confirmation        -> showed booking summary (book)
#   awaiting_cancel_choice       -> showed cancel list
#   awaiting_cancel_confirm      -> picked an appointment, confirm cancel
#   awaiting_reschedule_pick     -> showed reschedule list
#   awaiting_reschedule_slot     -> showed new slots
#   awaiting_reschedule_confirm  -> showed reschedule summary, confirm
# ============================================================================


def process_message(user_text, session):
    state   = session.get("state", "awaiting_intent")
    patient = session.get("patient")
    history = session.get("history", [])

    history.append({"role": "user", "content": user_text})

    if not patient:
        return _send("Your session has expired. Please log in again.", session, history)

    # Quick navigation works in any state except the base
    quick_back = user_text.strip().lower() in ("cancel", "back", "stop", "nevermind", "never mind", "menu")
    if quick_back and state != "awaiting_intent":
        # If user types "cancel" while in the cancel flow, treat it as exiting
        session["state"] = "awaiting_intent"
        return _send("Okay, going back. What would you like to do?", session, history)

    # -- State-specific handlers -------------------------------------------

    if state == "awaiting_symptom_confirm":
        return _handle_symptom_confirm(user_text, session, history)

    if state == "awaiting_doctor_choice":
        return _handle_doctor_choice(user_text, session, history)

    if state == "awaiting_slot_choice":
        return _handle_slot_choice(user_text, session, history)

    if state == "awaiting_confirmation":
        return _handle_confirmation(user_text, session, history)

    if state == "awaiting_cancel_choice":
        return _handle_cancel_choice(user_text, session, history)

    if state == "awaiting_cancel_confirm":
        return _handle_cancel_confirm(user_text, session, history)

    if state == "awaiting_reschedule_pick":
        return _handle_reschedule_pick(user_text, session, history)

    if state == "awaiting_reschedule_slot":
        return _handle_reschedule_slot(user_text, session, history)

    if state == "awaiting_reschedule_confirm":
        return _handle_reschedule_confirm(user_text, session, history)

    # -- Default: parse fresh intent ---------------------------------------
    parsed = parse_user_input(user_text, history)
    session["parsed"] = parsed

    if parsed.get("emergency"):
        return _send(emergency_reply(), session, history)
    if parsed.get("medical_q"):
        return _send(medical_question_reply(), session, history)

    intent = parsed.get("intent", "unknown")

    if intent == "help":
        return _send(help_reply(), session, history)

    if intent == "list_doctors":
        return _send(list_all_doctors_text(), session, history)

    if intent == "list_departments":
        return _send(list_all_departments_text(), session, history)

    if intent == "query":
        appts = db.get_patient_appointments(patient["id"])
        return _send("Your upcoming appointments:\n" + format_appointments(appts), session, history)

    if intent == "greet":
        return _send(_smart_greet(patient), session, history)

    if intent == "small_talk":
        return _send("I'm focused on helping you with appointments. What would you like to do?", session, history)

    if intent == "out_of_scope":
        return _send("I can only help with appointment booking, rescheduling, or cancellation. Want to book one?", session, history)

    if intent == "book":
        # Earliest-available shortcut
        if parsed.get("earliest") and parsed.get("department"):
            return _try_earliest(parsed, session, history)
        # Symptom routing -> ask user to confirm dept before continuing
        if parsed.get("symptom_hint") and parsed.get("department") and not parsed.get("doctor_name"):
            session["state"]             = "awaiting_symptom_confirm"
            session["pending_dept"]      = parsed["department"]
            session["pending_prefs"]     = {"date": parsed.get("preferred_date"),
                                            "time": parsed.get("preferred_time"),
                                            "period": parsed.get("time_period")}
            return _send(
                f"Based on '{parsed['symptom_hint']}', I'd suggest our {parsed['department']} department. "
                "Should I look for slots there? (yes/no)",
                session, history
            )
        return _start_booking(parsed, session, history)

    if intent == "reschedule":
        return _start_reschedule(session, history)

    if intent == "cancel":
        return _start_cancel(session, history)

    return _send(
        "I didn't catch that. Try:\n"
        "  - 'Book me with a cardiologist next Monday morning'\n"
        "  - 'Show my appointments'\n"
        "  - 'Reschedule my appointment'\n"
        "  - 'Cancel my appointment'\n"
        "Or type 'help' for the full list of things I can do.",
        session, history
    )


def _smart_greet(patient):
    first = patient["name"].split()[0]
    appts = db.get_patient_appointments(patient["id"])
    base = f"Hi {first}, glad to see you!"
    if appts:
        nxt = appts[0]
        base += (f"\n\nYour next appointment: {nxt['doctor_name']} ({nxt['department']}) "
                 f"on {nxt['date']} at {nxt['time']}.")
    base += (
        "\n\nI can:\n"
        "  - Book an appointment\n"
        "  - Reschedule or cancel an appointment\n"
        "  - Show your upcoming appointments\n\n"
        "What would you like to do?"
    )
    return base


def _try_earliest(parsed, session, history):
    dept = parsed["department"]
    doctor_name, slot = db.get_next_available_in_department(dept)
    if not slot:
        session["state"] = "awaiting_intent"
        return _send(f"No upcoming slots in {dept} at the moment.", session, history)
    session["selected_doctor"] = doctor_name
    session["chosen_slot"]     = slot
    session["state"]           = "awaiting_confirmation"
    return _send(
        f"Earliest available in {dept}:\n\n"
        f"  Doctor : {doctor_name}\n"
        f"  Date   : {slot['date']}\n"
        f"  Time   : {slot['time']}\n\n"
        "Reply 'yes' to confirm or 'no' to go back.",
        session, history
    )


# -- Flow starters -----------------------------------------------------------

def _start_booking(parsed, session, history):
    doc_name  = parsed.get("doctor_name")
    dept      = parsed.get("department")
    pref_date = parsed.get("preferred_date")
    pref_time = parsed.get("preferred_time")
    period    = parsed.get("time_period")

    if doc_name:
        return _present_doctor_slots(doc_name, pref_date, pref_time, period, session, history)

    if dept:
        doctors = db.get_doctors_by_department(dept)
        if not doctors:
            session["state"] = "awaiting_intent"
            return _send(f"We don't have doctors in {dept} right now. Try another department.", session, history)
        if len(doctors) == 1:
            return _present_doctor_slots(doctors[0]["name"], pref_date, pref_time, period, session, history,
                                         intro=f"You'll see {doctors[0]['name']} ({dept}).")
        session["dept_doctors"]  = [d["name"] for d in doctors]
        session["pending_prefs"] = {"date": pref_date, "time": pref_time, "period": period}
        lines = "\n".join(f"  {i+1}. {d['name']}" for i, d in enumerate(doctors))
        session["state"] = "awaiting_doctor_choice"
        return _send(f"Doctors in {dept}:\n{lines}\n\nReply with the number or the doctor's name.", session, history)

    # No doctor, no department
    session["state"] = "awaiting_intent"
    return _send(
        list_all_doctors_text() + "\n\nWhich doctor or department would you like?",
        session, history
    )


def _present_doctor_slots(doc_name, pref_date, pref_time, period, session, history, intro=None):
    """Common slot presentation with smart fallback when filters return nothing."""
    slots = db.get_available_slots(doc_name)
    if not slots:
        session["state"] = "awaiting_intent"
        return _send(f"{doc_name} has no upcoming slots available. Try another doctor.", session, history)

    filtered = nlp.filter_slots_by_preference(slots, pref_date, pref_time, period)

    session["selected_doctor"] = doc_name
    session["available_slots"] = filtered if filtered else slots
    session["state"]           = "awaiting_slot_choice"

    title = intro + " " if intro else ""
    if pref_date or period or pref_time:
        note = []
        if pref_date: note.append(pref_date)
        if period:    note.append(period)
        if pref_time: note.append(pref_time)
        title += f"Slots matching ({', '.join(note)}) for {doc_name}"
    else:
        title += f"Available slots for {doc_name}"

    msg = f"{title}:\n{format_slots(session['available_slots'])}\n\nReply with the slot number."

    # Period fallback hint: requested period had nothing, but other periods do
    if period and not nlp.has_period_in_slots(slots, period):
        alts = nlp.alternative_periods(slots, period)
        if alts:
            msg = (f"No {period} slots for {doc_name} — showing all available instead.\n"
                   f"({', '.join(alts)} slots are open).\n\n"
                   f"{format_slots(session['available_slots'])}\n\nReply with the slot number.")
    return _send(msg, session, history)


def _start_cancel(session, history):
    appts = db.get_patient_appointments(session["patient"]["id"])
    if not appts:
        return _send("You don't have any upcoming appointments to cancel.", session, history)
    if len(appts) == 1:
        a = appts[0]
        session["cancel_appts"]      = [str(a["_id"])]
        session["cancel_target_idx"] = 0
        session["state"]             = "awaiting_cancel_confirm"
        return _send(
            f"Confirm cancellation:\n\n"
            f"  Doctor : {a['doctor_name']}\n"
            f"  When   : {a['date']} at {a['time']}\n\n"
            "Reply 'yes' to cancel or 'no' to keep it.",
            session, history
        )
    session["cancel_appts"] = [str(a["_id"]) for a in appts]
    session["state"]        = "awaiting_cancel_choice"
    return _send(
        "Your appointments:\n" + format_appointments(appts) + "\n\nReply with the number to cancel.",
        session, history
    )


def _start_reschedule(session, history):
    appts = db.get_patient_appointments(session["patient"]["id"])
    if not appts:
        return _send("You don't have any upcoming appointments to reschedule.", session, history)
    if len(appts) == 1:
        appt = appts[0]
        session["reschedule_appt_id"] = str(appt["_id"])
        session["selected_doctor"]    = appt["doctor_name"]
        slots = db.get_available_slots(appt["doctor_name"])
        if not slots:
            session["state"] = "awaiting_intent"
            return _send(f"{appt['doctor_name']} has no other available slots.", session, history)
        session["available_slots"] = slots
        session["state"] = "awaiting_reschedule_slot"
        return _send(
            f"Rescheduling your appointment with {appt['doctor_name']} (currently {appt['date']} at {appt['time']}).\n\n"
            f"Available slots:\n{format_slots(slots)}\n\nReply with the slot number.",
            session, history
        )
    session["reschedule_appts"] = [str(a["_id"]) for a in appts]
    session["state"] = "awaiting_reschedule_pick"
    return _send(
        "Which appointment would you like to reschedule?\n" + format_appointments(appts) + "\n\nReply with the number.",
        session, history
    )


# -- State handlers ----------------------------------------------------------

def _handle_symptom_confirm(user_text, session, history):
    if nlp.is_yes(user_text):
        dept  = session.pop("pending_dept", None)
        prefs = session.pop("pending_prefs", {}) or {}
        if not dept:
            session["state"] = "awaiting_intent"
            return _send("Something went wrong — let's start over. What would you like to do?", session, history)
        parsed = {
            "department": dept, "doctor_name": None,
            "preferred_date": prefs.get("date"),
            "preferred_time": prefs.get("time"),
            "time_period":    prefs.get("period")
        }
        return _start_booking(parsed, session, history)
    if nlp.is_no(user_text):
        session.pop("pending_dept", None)
        session.pop("pending_prefs", None)
        session["state"] = "awaiting_intent"
        return _send("No problem. Which department or doctor would you like instead?", session, history)
    return _send("Please reply 'yes' to use that department, or 'no' to pick another.", session, history)


def _handle_doctor_choice(user_text, session, history):
    dept_doctors = session.get("dept_doctors", [])
    chosen = None
    try:
        idx = int(user_text.strip()) - 1
        if 0 <= idx < len(dept_doctors):
            chosen = dept_doctors[idx]
    except ValueError:
        m = fuzzy_doctor_match(user_text.strip())
        if m and m in dept_doctors:
            chosen = m

    if not chosen:
        return _send(f"Please reply with a number between 1 and {len(dept_doctors)}, or the doctor's name.", session, history)

    prefs = session.get("pending_prefs", {}) or {}
    return _present_doctor_slots(chosen, prefs.get("date"), prefs.get("time"), prefs.get("period"),
                                 session, history)


def _handle_slot_choice(user_text, session, history):
    slots    = session.get("available_slots", [])
    doc_name = session.get("selected_doctor")
    try:
        idx = int(user_text.strip()) - 1
        if 0 <= idx < len(slots):
            chosen = slots[idx]
            # Pre-check for double-booking before showing confirmation
            if db.patient_has_conflict(session["patient"]["id"], chosen["date"], chosen["time"]):
                return _send(
                    f"You already have another appointment on {chosen['date']} at {chosen['time']}. "
                    "Please choose a different slot.",
                    session, history
                )
            session["chosen_slot"] = chosen
            session["state"]       = "awaiting_confirmation"
            return _send(
                f"Please confirm your booking:\n\n"
                f"  Doctor : {doc_name}\n"
                f"  Date   : {chosen['date']}\n"
                f"  Time   : {chosen['time']}\n\n"
                "Reply 'yes' to confirm or 'no' to go back.",
                session, history
            )
        return _send(f"Please enter a number between 1 and {len(slots)}.", session, history)
    except ValueError:
        return _send("Please enter the slot number (e.g. 1, 2, 3).", session, history)


def _handle_confirmation(user_text, session, history):
    if nlp.is_yes(user_text):
        doc_name = session.get("selected_doctor")
        slot     = session.get("chosen_slot")
        appt_id, err = db.book_appointment(
            session["patient"]["id"], doc_name, slot["date"], slot["time"]
        )
        if err:
            session["state"] = "awaiting_intent"
            return _send(f"Booking failed: {err}\nPlease try a different slot.", session, history)
        session["state"] = "awaiting_intent"
        return _send(
            f"Appointment confirmed!\n\n"
            f"  Doctor  : {doc_name}\n"
            f"  Date    : {slot['date']}\n"
            f"  Time    : {slot['time']}\n"
            f"  Ref ID  : ...{appt_id[-6:]}\n\n"
            "A reminder will be visible on your dashboard. Anything else I can help with?",
            session, history
        )
    if nlp.is_no(user_text):
        session["state"] = "awaiting_intent"
        return _send("No problem, booking cancelled. What else can I help you with?", session, history)
    return _send("Please reply 'yes' to confirm or 'no' to cancel.", session, history)


def _handle_cancel_choice(user_text, session, history):
    ids = session.get("cancel_appts", [])
    try:
        idx = int(user_text.strip()) - 1
        if 0 <= idx < len(ids):
            # Move to confirm step
            appt_id = ids[idx]
            appt = db.appointments_col.find_one({"_id": db.ObjectId(appt_id)})
            if not appt:
                session["state"] = "awaiting_intent"
                return _send("That appointment was not found.", session, history)
            session["cancel_target_idx"] = idx
            session["state"]             = "awaiting_cancel_confirm"
            return _send(
                f"Confirm cancellation:\n\n"
                f"  Doctor : {appt['doctor_name']}\n"
                f"  When   : {appt['date']} at {appt['time']}\n\n"
                "Reply 'yes' to cancel or 'no' to keep it.",
                session, history
            )
        return _send(f"Please enter a number between 1 and {len(ids)}.", session, history)
    except ValueError:
        return _send("Please enter the appointment number (e.g. 1, 2, 3).", session, history)


def _handle_cancel_confirm(user_text, session, history):
    if nlp.is_yes(user_text):
        ids = session.get("cancel_appts", [])
        idx = session.get("cancel_target_idx", 0)
        if not ids or idx >= len(ids):
            session["state"] = "awaiting_intent"
            return _send("Something went wrong — no appointment was selected.", session, history)
        ok, err = db.cancel_appointment(ids[idx], session["patient"]["id"])
        session["state"] = "awaiting_intent"
        session.pop("cancel_appts", None)
        session.pop("cancel_target_idx", None)
        return _send(
            "Appointment cancelled. The slot is now available again. Anything else?"
            if ok else f"Could not cancel: {err}",
            session, history
        )
    if nlp.is_no(user_text):
        session["state"] = "awaiting_intent"
        session.pop("cancel_appts", None)
        session.pop("cancel_target_idx", None)
        return _send("Kept the appointment. Anything else?", session, history)
    return _send("Please reply 'yes' to cancel the appointment or 'no' to keep it.", session, history)


def _handle_reschedule_pick(user_text, session, history):
    ids = session.get("reschedule_appts", [])
    try:
        idx = int(user_text.strip()) - 1
        if 0 <= idx < len(ids):
            appt_id = ids[idx]
            appt    = db.appointments_col.find_one({"_id": db.ObjectId(appt_id)})
            if not appt:
                session["state"] = "awaiting_intent"
                return _send("That appointment was not found.", session, history)
            session["reschedule_appt_id"] = appt_id
            session["selected_doctor"]    = appt["doctor_name"]
            slots = db.get_available_slots(appt["doctor_name"])
            if not slots:
                session["state"] = "awaiting_intent"
                return _send(f"{appt['doctor_name']} has no other available slots.", session, history)
            session["available_slots"] = slots
            session["state"] = "awaiting_reschedule_slot"
            return _send(
                f"Available slots for {appt['doctor_name']}:\n{format_slots(slots)}\n\n"
                "Reply with the new slot number.",
                session, history
            )
        return _send(f"Please enter a number between 1 and {len(ids)}.", session, history)
    except ValueError:
        return _send("Please enter the appointment number.", session, history)


def _handle_reschedule_slot(user_text, session, history):
    slots = session.get("available_slots", [])
    try:
        idx = int(user_text.strip()) - 1
        if 0 <= idx < len(slots):
            chosen  = slots[idx]
            appt_id = session.get("reschedule_appt_id")
            if db.patient_has_conflict(session["patient"]["id"], chosen["date"], chosen["time"],
                                       exclude_appt_id=appt_id):
                return _send(
                    f"You already have another appointment on {chosen['date']} at {chosen['time']}. "
                    "Please pick a different slot.",
                    session, history
                )
            session["chosen_slot"] = chosen
            session["state"]       = "awaiting_reschedule_confirm"
            return _send(
                f"Confirm reschedule:\n\n"
                f"  Doctor   : {session.get('selected_doctor')}\n"
                f"  New Date : {chosen['date']}\n"
                f"  New Time : {chosen['time']}\n\n"
                "Reply 'yes' to confirm or 'no' to cancel.",
                session, history
            )
        return _send(f"Please enter a number between 1 and {len(slots)}.", session, history)
    except ValueError:
        return _send("Please enter the slot number.", session, history)


def _handle_reschedule_confirm(user_text, session, history):
    if nlp.is_yes(user_text):
        chosen  = session.get("chosen_slot")
        appt_id = session.get("reschedule_appt_id")
        ok, err = db.reschedule_appointment(
            appt_id, session["patient"]["id"], chosen["date"], chosen["time"]
        )
        session["state"] = "awaiting_intent"
        if ok:
            return _send(
                f"Rescheduled successfully!\n\n"
                f"  Doctor  : {session.get('selected_doctor')}\n"
                f"  New Date: {chosen['date']}\n"
                f"  New Time: {chosen['time']}\n\n"
                "Anything else?",
                session, history
            )
        return _send(f"Reschedule failed: {err}", session, history)
    if nlp.is_no(user_text):
        session["state"] = "awaiting_intent"
        return _send("Reschedule cancelled — your original appointment is unchanged.", session, history)
    return _send("Please reply 'yes' to confirm the new slot or 'no' to keep the original.", session, history)


# -- Helper ------------------------------------------------------------------

def _send(reply, session, history):
    history.append({"role": "assistant", "content": reply})
    session["history"] = history
    return reply, session
