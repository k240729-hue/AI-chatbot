import os
import uuid
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from dotenv import load_dotenv

import db
import bot
from auth import login_required, admin_required, current_user, is_admin, is_logged_in

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "hospital_secret_key_2024")


# ── Globals for templates ────────────────────────────────────────────────────

@app.context_processor
def inject_user():
    return {
        "current_user": current_user(),
        "is_admin":     is_admin(),
        "is_logged_in": is_logged_in()
    }


# ── Auth routes ──────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET"])
def login():
    if is_logged_in():
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login_post():
    data = request.get_json() or {}
    nic  = (data.get("nic")  or "").strip().upper()
    name = (data.get("name") or "").strip()
    role = (data.get("role") or "patient").strip().lower()

    if not nic or not name:
        return jsonify({"error": "NIC and name are required."}), 400

    user, err = db.login_user(nic, name, role)
    if err:
        return jsonify({"error": err}), 401

    session.clear()
    session["chat_id"] = str(uuid.uuid4())
    session["user"] = {
        "id":   str(user["_id"]),
        "nic":  user["nic"],
        "name": user["name"],
        "role": user.get("role", "patient")
    }
    session["chat_state"] = "awaiting_intent"
    session["history"]    = []
    return jsonify({"ok": True, "role": user.get("role", "patient")})


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Page routes ──────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html", active_page="dashboard")


@app.route("/appointments")
@login_required
def appointments_page():
    return render_template("appointments.html", active_page="appointments")


@app.route("/doctors")
@login_required
def doctors_page():
    return render_template("doctors.html", active_page="doctors")


@app.route("/patients")
@admin_required
def patients_page():
    return render_template("patients.html", active_page="patients")


@app.route("/chat-page")
@login_required
def chat_page():
    return render_template("chat.html", active_page="chat")


@app.route("/admin")
@admin_required
def admin_page():
    return render_template("admin.html", active_page="admin")


# ── API routes ───────────────────────────────────────────────────────────────

@app.route("/api/stats")
@login_required
def api_stats():
    return jsonify(db.get_stats())


@app.route("/api/appointments")
@login_required
def api_appointments():
    if is_admin():
        return jsonify(db.get_all_appointments_with_details())
    # Patients see only their own
    u = current_user()
    appts = db.get_patient_appointments(u["id"], include_past=True)
    return jsonify([{
        "id":           str(a["_id"]),
        "patient_name": u["name"],
        "patient_nic":  u["nic"],
        "doctor_name":  a.get("doctor_name", ""),
        "department":   a.get("department", ""),
        "date":         a.get("date", ""),
        "time":         a.get("time", ""),
        "status":       a.get("status", ""),
        "booked_at":    a["booked_at"].strftime("%Y-%m-%d %H:%M") if a.get("booked_at") else ""
    } for a in appts])


@app.route("/api/doctors")
@login_required
def api_doctors():
    return jsonify(db.get_all_doctors_serializable())


@app.route("/api/patients")
@admin_required
def api_patients():
    return jsonify(db.get_all_patients_with_stats())


@app.route("/api/chat-state")
@login_required
def api_chat_state():
    return jsonify({
        "state":   session.get("chat_state", "awaiting_intent"),
        "patient": current_user(),
        "history": session.get("history", [])[-30:]
    })


@app.route("/api/patient-appointments")
@login_required
def api_patient_appointments():
    u = current_user()
    appts = db.get_patient_appointments(u["id"])
    return jsonify([{
        "id":          str(a["_id"]),
        "doctor_name": a.get("doctor_name", ""),
        "department":  a.get("department", ""),
        "date":        a.get("date", ""),
        "time":        a.get("time", ""),
        "status":      a.get("status", "")
    } for a in appts])


# ── Admin actions ────────────────────────────────────────────────────────────

@app.route("/api/admin/cancel-appointment", methods=["POST"])
@admin_required
def admin_cancel_appointment():
    data = request.get_json() or {}
    appt_id = data.get("id")
    if not appt_id:
        return jsonify({"error": "Missing id"}), 400
    ok, err = db.cancel_appointment(appt_id)
    return jsonify({"ok": ok, "error": err})


@app.route("/api/admin/add-slot", methods=["POST"])
@admin_required
def admin_add_slot():
    data = request.get_json() or {}
    doctor_id = data.get("doctor_id")
    date      = data.get("date")
    time      = data.get("time")
    if not all([doctor_id, date, time]):
        return jsonify({"error": "Missing fields"}), 400
    ok = db.add_slot_to_doctor(doctor_id, date, time)
    return jsonify({"ok": ok})


# ── Chat endpoints ────────────────────────────────────────────────────────────

@app.route("/chat", methods=["POST"])
@login_required
def chat():
    u = current_user()
    if u["role"] != "patient":
        return jsonify({"reply": "The chatbot is for patient use. Switch to a patient account to book appointments.",
                        "state": "awaiting_intent"})

    data      = request.get_json()
    user_text = (data.get("message") or "").strip()
    if not user_text:
        return jsonify({"reply": "Please type a message.", "state": session.get("chat_state")})

    SESSION_KEYS = [
        "parsed", "selected_doctor", "chosen_slot",
        "cancel_appts", "cancel_target_idx",
        "dept_doctors", "pending_prefs", "pending_dept",
        "available_slots",
        "reschedule_appts", "reschedule_appt_id",
    ]

    sess_data = {
        "state":   session.get("chat_state", "awaiting_intent"),
        "patient": u,
        "history": session.get("history", []),
    }
    for k in SESSION_KEYS:
        sess_data[k] = session.get(k)

    db.log_message(session.get("chat_id"), "user", user_text, u["nic"])
    reply, updated = bot.process_message(user_text, sess_data)
    db.log_message(session.get("chat_id"), "assistant", reply, u["nic"])

    session["chat_state"] = updated["state"]
    session["history"]    = updated.get("history", [])
    for k in SESSION_KEYS:
        if k in updated:
            session[k] = updated[k]
        else:
            session.pop(k, None)

    is_emergency = bool((updated.get("parsed") or {}).get("emergency"))
    return jsonify({
        "reply":     reply,
        "state":     session["chat_state"],
        "patient":   u,
        "emergency": is_emergency
    })


@app.route("/reset", methods=["POST"])
@login_required
def reset():
    u = current_user()
    session["chat_id"]    = str(uuid.uuid4())
    session["chat_state"] = "awaiting_intent"
    session["history"]    = []
    for k in ["parsed", "selected_doctor", "chosen_slot",
              "cancel_appts", "cancel_target_idx",
              "dept_doctors", "pending_prefs", "pending_dept",
              "available_slots", "reschedule_appts", "reschedule_appt_id"]:
        session.pop(k, None)
    return jsonify({
        "reply":   f"Hi {u['name'].split()[0]}, fresh session started. How can I help?",
        "state":   "awaiting_intent",
        "patient": u
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
