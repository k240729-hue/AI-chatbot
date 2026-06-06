"""
Seed the hospital_chatbot MongoDB database with users (patients + admin),
doctors, and indexes. Idempotent — drops and recreates each run.
"""
import os
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client["hospital_chatbot"]

# Drop in old patient collection (rename to users)
db.patients.drop()
db.users.drop()
db.doctors.drop()
db.appointments.drop()
db.chat_logs.drop()
print("Cleared collections.")

# ── Seed admin + patients ────────────────────────────────────────────────────
users = [
    {"nic": "ADMIN001",     "name": "Admin User",    "role": "admin",   "phone": "555-9999", "email": "admin@hospital.lk", "age": None},
    {"nic": "200012345678", "name": "John Smith",    "role": "patient", "phone": "555-0101", "email": "john@example.com",  "age": 35},
    {"nic": "199523456789", "name": "Maria Garcia",  "role": "patient", "phone": "555-0102", "email": "maria@example.com", "age": 28},
    {"nic": "198934567890", "name": "Ahmed Hassan",  "role": "patient", "phone": "555-0103", "email": "ahmed@example.com", "age": 45},
    {"nic": "197145678901", "name": "Sarah Johnson", "role": "patient", "phone": "555-0104", "email": "sarah@example.com", "age": 52},
    {"nic": "199256789012", "name": "Li Wei",        "role": "patient", "phone": "555-0105", "email": "liwei@example.com", "age": 31},
    {"nic": "198367890123", "name": "Emma Wilson",   "role": "patient", "phone": "555-0106", "email": "emma@example.com",  "age": 40},
]
from datetime import datetime
for u in users:
    u["registered_at"] = datetime.utcnow()

db.users.insert_many(users)
print(f"Inserted {len(users)} users (1 admin + {len(users)-1} patients).")

# ── Seed doctors ─────────────────────────────────────────────────────────────
doctors = [
    {"name": "Dr. Emily Carter",  "department": "Cardiology",
     "available_slots": [
        {"date": "2026-06-10", "time": "09:00"}, {"date": "2026-06-10", "time": "10:00"},
        {"date": "2026-06-11", "time": "14:00"}, {"date": "2026-06-12", "time": "11:00"},
        {"date": "2026-06-13", "time": "09:30"}, {"date": "2026-06-15", "time": "16:00"},
     ]},
    {"name": "Dr. James Patel",   "department": "Neurology",
     "available_slots": [
        {"date": "2026-06-10", "time": "11:00"}, {"date": "2026-06-11", "time": "09:00"},
        {"date": "2026-06-12", "time": "15:00"}, {"date": "2026-06-14", "time": "10:00"},
        {"date": "2026-06-16", "time": "13:00"},
     ]},
    {"name": "Dr. Sofia Nguyen",  "department": "Orthopedics",
     "available_slots": [
        {"date": "2026-06-10", "time": "13:00"}, {"date": "2026-06-11", "time": "16:00"},
        {"date": "2026-06-13", "time": "10:00"}, {"date": "2026-06-15", "time": "09:00"},
     ]},
    {"name": "Dr. Marcus Lee",    "department": "General Medicine",
     "available_slots": [
        {"date": "2026-06-09", "time": "08:30"}, {"date": "2026-06-09", "time": "09:30"},
        {"date": "2026-06-10", "time": "08:30"}, {"date": "2026-06-11", "time": "13:00"},
        {"date": "2026-06-12", "time": "08:30"}, {"date": "2026-06-13", "time": "14:00"},
     ]},
    {"name": "Dr. Aisha Rahman",  "department": "Pediatrics",
     "available_slots": [
        {"date": "2026-06-09", "time": "10:00"}, {"date": "2026-06-10", "time": "14:00"},
        {"date": "2026-06-11", "time": "10:00"}, {"date": "2026-06-13", "time": "14:00"},
     ]},
    {"name": "Dr. Robert Kim",    "department": "Dermatology",
     "available_slots": [
        {"date": "2026-06-10", "time": "15:00"}, {"date": "2026-06-12", "time": "09:00"},
        {"date": "2026-06-14", "time": "11:00"}, {"date": "2026-06-15", "time": "14:00"},
     ]},
]
db.doctors.insert_many(doctors)
print(f"Inserted {len(doctors)} doctors.")

# ── Indexes ──────────────────────────────────────────────────────────────────
db.users.create_index([("nic", ASCENDING)], unique=True)
db.users.create_index("name")
db.users.create_index("role")
db.doctors.create_index("name")
db.doctors.create_index("department")
db.appointments.create_index("patient_id")
db.appointments.create_index("status")
db.appointments.create_index("date")
db.chat_logs.create_index("session_id")
db.chat_logs.create_index("user_nic")
print("Indexes created.")

print("\n" + "="*54)
print(" Database seeded successfully!")
print("="*54)
print("\nLogin credentials:")
print("  ADMIN:")
print("    NIC : ADMIN001")
print("    Name: Admin User")
print("    Role: admin")
print("\n  PATIENTS (sample):")
for u in users[1:]:
    print(f"    NIC : {u['nic']:<16} Name: {u['name']}")
print("\nOpen Compass -> mongodb://localhost:27017 -> hospital_chatbot")
