"""
MongoDB data access layer.
Collections: users (patients+admins), doctors, appointments, chat_logs
"""
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING
from bson import ObjectId

load_dotenv()

_client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
_db = _client["hospital_chatbot"]

users_col        = _db["users"]
doctors_col      = _db["doctors"]
appointments_col = _db["appointments"]
chat_logs_col    = _db["chat_logs"]


# ── User helpers ─────────────────────────────────────────────────────────────

def find_user_by_nic(nic):
    return users_col.find_one({"nic": nic.strip().upper()})


def find_user_by_id(user_id):
    try:
        return users_col.find_one({"_id": ObjectId(user_id)})
    except Exception:
        return None


def login_user(nic, name, role):
    """Look up by NIC. If exists, validate name+role. If patient and not found, register."""
    nic   = nic.strip().upper()
    name  = name.strip().title()
    role  = role.strip().lower()

    if not nic or not name or role not in ("patient", "admin"):
        return None, "Please provide NIC, name, and role."

    user = users_col.find_one({"nic": nic})

    if user:
        # Existing user — verify name (case-insensitive) and role
        if user["name"].lower() != name.lower():
            return None, f"Name does not match the record for NIC {nic}."
        if user.get("role", "patient") != role:
            return None, f"This NIC is registered as a {user.get('role','patient')}, not {role}."
        return user, None

    # New user — only patients can self-register
    if role != "patient":
        return None, "Admin accounts must be created by the system administrator."

    new_user = {
        "nic":           nic,
        "name":          name,
        "role":          "patient",
        "phone":         "N/A",
        "email":         "N/A",
        "age":           None,
        "registered_at": datetime.utcnow()
    }
    result = users_col.insert_one(new_user)
    return users_col.find_one({"_id": result.inserted_id}), None


def get_all_patients_with_stats():
    patients = list(users_col.find({"role": "patient"}).sort("name", 1))
    out = []
    for p in patients:
        c = appointments_col.count_documents({"patient_id": p["_id"], "status": "confirmed"})
        out.append({
            "id":                str(p["_id"]),
            "nic":               p.get("nic", ""),
            "name":              p.get("name", ""),
            "age":               p.get("age") or "N/A",
            "phone":             p.get("phone", "N/A"),
            "email":             p.get("email", "N/A"),
            "appointment_count": c,
            "registered_at":     p["registered_at"].strftime("%Y-%m-%d") if p.get("registered_at") else "N/A"
        })
    return out


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_stats():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return {
        "total_patients":     users_col.count_documents({"role": "patient"}),
        "total_doctors":      doctors_col.count_documents({}),
        "total_appointments": appointments_col.count_documents({"status": "confirmed"}),
        "today_appointments": appointments_col.count_documents({"status": "confirmed", "date": today}),
        "cancelled":          appointments_col.count_documents({"status": "cancelled"}),
        "total_slots":        sum(len(d.get("available_slots", [])) for d in doctors_col.find({}))
    }


# ── Doctor helpers ───────────────────────────────────────────────────────────

def get_all_doctors():
    return list(doctors_col.find({}).sort("name", 1))


def get_all_doctors_serializable():
    out = []
    for d in get_all_doctors():
        slots = d.get("available_slots", [])
        out.append({
            "id":          str(d["_id"]),
            "name":        d.get("name", ""),
            "department":  d.get("department", ""),
            "slots_count": len(slots),
            "next_slot":   slots[0] if slots else None
        })
    return out


def find_doctor_by_name(name):
    return doctors_col.find_one({"name": {"$regex": name, "$options": "i"}})


def get_doctors_by_department(department):
    return list(doctors_col.find({"department": {"$regex": department, "$options": "i"}}))


def get_available_slots(doctor_name, future_only=True):
    """Returns the doctor's available slots, sorted, optionally filtered to today+future."""
    doc = doctors_col.find_one({"name": {"$regex": doctor_name, "$options": "i"}})
    if not doc:
        return []
    slots = doc.get("available_slots", [])
    if future_only:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        slots = [s for s in slots if s.get("date", "") >= today]
    return sorted(slots, key=lambda s: (s.get("date", ""), s.get("time", "")))


def patient_has_conflict(patient_id, date, time, exclude_appt_id=None):
    """True if patient already has a confirmed appointment at the same date+time."""
    try:
        q = {
            "patient_id": ObjectId(patient_id),
            "status":     "confirmed",
            "date":       date,
            "time":       time
        }
        if exclude_appt_id:
            q["_id"] = {"$ne": ObjectId(exclude_appt_id)}
        return appointments_col.count_documents(q) > 0
    except Exception:
        return False


def get_next_available_in_department(department):
    """Returns (doctor_name, slot_dict) for the soonest available slot in a department."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    best = None  # (date, time, doctor, slot)
    for d in doctors_col.find({"department": {"$regex": department, "$options": "i"}}):
        for s in d.get("available_slots", []):
            if s.get("date", "") < today:
                continue
            key = (s["date"], s["time"])
            if best is None or key < (best[0], best[1]):
                best = (s["date"], s["time"], d["name"], s)
    if not best:
        return None, None
    return best[2], best[3]


def add_slot_to_doctor(doctor_id, date, time):
    try:
        result = doctors_col.update_one(
            {"_id": ObjectId(doctor_id)},
            {"$push": {"available_slots": {"date": date, "time": time}}}
        )
        return result.modified_count == 1
    except Exception:
        return False


# ── Appointment helpers ──────────────────────────────────────────────────────

def book_appointment(patient_id, doctor_name, slot_date, slot_time):
    doctor = doctors_col.find_one({"name": {"$regex": doctor_name, "$options": "i"}})
    if not doctor:
        return None, "Doctor not found."

    # Reject past dates
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if slot_date < today:
        return None, "That date is in the past."

    # Prevent the same patient double-booking the same date+time
    if patient_has_conflict(patient_id, slot_date, slot_time):
        return None, "You already have an appointment at that date and time."

    slot = {"date": slot_date, "time": slot_time}
    result = doctors_col.update_one(
        {"_id": doctor["_id"], "available_slots": slot},
        {"$pull": {"available_slots": slot}}
    )

    if result.modified_count == 0:
        return None, "That slot is no longer available."

    appt = {
        "patient_id":  ObjectId(patient_id),
        "doctor_name": doctor["name"],
        "department":  doctor.get("department", ""),
        "date":        slot_date,
        "time":        slot_time,
        "status":      "confirmed",
        "booked_at":   datetime.utcnow()
    }
    appt_id = appointments_col.insert_one(appt).inserted_id
    return str(appt_id), None


def get_patient_appointments(patient_id, include_past=False):
    try:
        q = {"patient_id": ObjectId(patient_id), "status": "confirmed"}
        if not include_past:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            q["date"] = {"$gte": today}
        return list(appointments_col.find(q).sort([("date", 1), ("time", 1)]))
    except Exception:
        return []


def cancel_appointment(appt_id, patient_id=None):
    try:
        q = {"_id": ObjectId(appt_id)}
        if patient_id:
            q["patient_id"] = ObjectId(patient_id)
        appt = appointments_col.find_one(q)
        if not appt:
            return False, "Appointment not found."

        appointments_col.update_one({"_id": appt["_id"]}, {"$set": {"status": "cancelled"}})
        doctors_col.update_one(
            {"name": appt["doctor_name"]},
            {"$push": {"available_slots": {"date": appt["date"], "time": appt["time"]}}}
        )
        return True, None
    except Exception as e:
        return False, str(e)


def reschedule_appointment(appt_id, patient_id, new_date, new_time):
    """Atomically cancel old + book new with same doctor."""
    try:
        appt = appointments_col.find_one({
            "_id": ObjectId(appt_id),
            "patient_id": ObjectId(patient_id),
            "status": "confirmed"
        })
        if not appt:
            return False, "Original appointment not found."

        today = datetime.utcnow().strftime("%Y-%m-%d")
        if new_date < today:
            return False, "That date is in the past."

        if patient_has_conflict(patient_id, new_date, new_time, exclude_appt_id=appt_id):
            return False, "You already have another appointment at that date and time."

        new_slot = {"date": new_date, "time": new_time}
        result = doctors_col.update_one(
            {"name": appt["doctor_name"], "available_slots": new_slot},
            {"$pull": {"available_slots": new_slot}}
        )
        if result.modified_count == 0:
            return False, "New slot is no longer available."

        old_slot = {"date": appt["date"], "time": appt["time"]}
        doctors_col.update_one(
            {"name": appt["doctor_name"]},
            {"$push": {"available_slots": old_slot}}
        )
        appointments_col.update_one(
            {"_id": appt["_id"]},
            {"$set": {"date": new_date, "time": new_time, "rescheduled_at": datetime.utcnow()}}
        )
        return True, None
    except Exception as e:
        return False, str(e)


def get_all_appointments_with_details(limit=100):
    pipeline = [
        {"$sort": {"booked_at": -1}},
        {"$limit": limit},
        {"$lookup": {
            "from": "users",
            "localField": "patient_id",
            "foreignField": "_id",
            "as": "patient_info"
        }},
        {"$unwind": {"path": "$patient_info", "preserveNullAndEmptyArrays": True}}
    ]
    out = []
    for a in appointments_col.aggregate(pipeline):
        out.append({
            "id":           str(a["_id"]),
            "patient_name": a.get("patient_info", {}).get("name", "Unknown"),
            "patient_nic":  a.get("patient_info", {}).get("nic", ""),
            "doctor_name":  a.get("doctor_name", ""),
            "department":   a.get("department", ""),
            "date":         a.get("date", ""),
            "time":         a.get("time", ""),
            "status":       a.get("status", ""),
            "booked_at":    a["booked_at"].strftime("%Y-%m-%d %H:%M") if a.get("booked_at") else ""
        })
    return out


# ── Chat log helpers ─────────────────────────────────────────────────────────

def log_message(session_id, role, content, user_nic=None):
    chat_logs_col.insert_one({
        "session_id": session_id,
        "user_nic":   user_nic,
        "role":       role,
        "content":    content,
        "timestamp":  datetime.utcnow()
    })


def get_chat_history(session_id):
    return list(chat_logs_col.find(
        {"session_id": session_id},
        {"_id": 0, "role": 1, "content": 1}
    ).sort("timestamp", 1))
