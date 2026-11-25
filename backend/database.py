import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from contextlib import contextmanager

DB_PATH = Path(__file__).parent / "clinic.db"


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS doctors (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                specialty TEXT NOT NULL,
                room TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS slots (
                id TEXT PRIMARY KEY,
                doctor_id TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                is_booked INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (doctor_id) REFERENCES doctors(id)
            );

            CREATE TABLE IF NOT EXISTS appointments (
                id TEXT PRIMARY KEY,
                phone_number TEXT NOT NULL,
                patient_name TEXT,
                doctor_id TEXT NOT NULL,
                slot_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'confirmed',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (doctor_id) REFERENCES doctors(id),
                FOREIGN KEY (slot_id) REFERENCES slots(id)
            );
            """
        )

        existing = conn.execute("SELECT COUNT(*) FROM doctors").fetchone()[0]
        if existing == 0:
            seed_data(conn)


def seed_data(conn: sqlite3.Connection):
    doctors = [
        ("doc-rao", "Dr. Ananya Rao", "Cardiology", "Suite 204"),
        ("doc-mehta", "Dr. Vikram Mehta", "Dermatology", "Suite 112"),
        ("doc-nair", "Dr. Priya Nair", "Pediatrics", "Suite 305"),
        ("doc-kulkarni", "Dr. Rohan Kulkarni", "General Medicine", "Suite 101"),
    ]
    conn.executemany(
        "INSERT INTO doctors (id, name, specialty, room) VALUES (?, ?, ?, ?)",
        doctors,
    )

    today = datetime.now().date()
    d1 = (today + timedelta(days=1)).isoformat()
    d2 = (today + timedelta(days=2)).isoformat()

    slots = [
        ("slot-1", "doc-rao", d1, "09:30 AM", 0),
        ("slot-2", "doc-rao", d1, "11:00 AM", 1),
        ("slot-3", "doc-rao", d2, "02:30 PM", 0),
        ("slot-4", "doc-mehta", d1, "10:00 AM", 0),
        ("slot-5", "doc-mehta", d1, "04:00 PM", 0),
        ("slot-6", "doc-mehta", d2, "11:30 AM", 0),
        ("slot-7", "doc-nair", d1, "09:00 AM", 0),
        ("slot-8", "doc-nair", d2, "03:00 PM", 0),
        ("slot-9", "doc-kulkarni", d1, "10:30 AM", 0),
        ("slot-10", "doc-kulkarni", d1, "05:00 PM", 1),
        ("slot-11", "doc-kulkarni", d2, "09:30 AM", 0),
    ]
    conn.executemany(
        "INSERT INTO slots (id, doctor_id, date, time, is_booked) VALUES (?, ?, ?, ?, ?)",
        slots,
    )

    now = datetime.now().isoformat(timespec="seconds")
    sample_appointments = [
        ("APT-1001", "9876543210", "Aarav Sharma", "doc-rao", "slot-2", "confirmed", now, now),
        ("APT-1002", "9123456789", "Meera Iyer", "doc-kulkarni", "slot-10", "confirmed", now, now),
    ]
    conn.executemany(
        """
        INSERT INTO appointments
        (id, phone_number, patient_name, doctor_id, slot_id, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        sample_appointments,
    )


def normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return digits[-10:] if len(digits) > 10 else digits


def get_doctors_with_slots() -> list[dict]:
    with get_conn() as conn:
        docs = [dict(r) for r in conn.execute("SELECT * FROM doctors ORDER BY name").fetchall()]
        slots = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM slots ORDER BY date, time"
            ).fetchall()
        ]
    slot_map: dict[str, list[dict]] = {}
    for s in slots:
        slot_map.setdefault(s["doctor_id"], []).append(
            {
                "id": s["id"],
                "date": s["date"],
                "time": s["time"],
                "is_booked": bool(s["is_booked"]),
            }
        )
    for d in docs:
        d["slots"] = slot_map.get(d["id"], [])
    return docs


def get_appointments() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.phone_number, a.patient_name, a.status, a.created_at, a.updated_at,
                   d.id AS doctor_id, d.name AS doctor_name, d.specialty, d.room,
                   s.id AS slot_id, s.date, s.time
            FROM appointments a
            JOIN doctors d ON a.doctor_id = d.id
            JOIN slots s ON a.slot_id = s.id
            ORDER BY a.updated_at DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def check_availability(doctor_name: str = "", specialty: str = "", date: str = "") -> dict:
    with get_conn() as conn:
        query = """
            SELECT s.id AS slot_id, s.date, s.time,
                   d.id AS doctor_id, d.name AS doctor_name, d.specialty, d.room
            FROM slots s
            JOIN doctors d ON s.doctor_id = d.id
            WHERE s.is_booked = 0
        """
        params: list[str] = []
        if doctor_name.strip():
            query += " AND LOWER(d.name) LIKE ?"
            params.append(f"%{doctor_name.strip().lower()}%")
        if specialty.strip():
            query += " AND LOWER(d.specialty) LIKE ?"
            params.append(f"%{specialty.strip().lower()}%")
        if date.strip():
            query += " AND s.date = ?"
            params.append(date.strip())
        query += " ORDER BY d.name, s.date, s.time"

        rows = [dict(r) for r in conn.execute(query, params).fetchall()]

    if not rows:
        return {
            "available": False,
            "message": "No open slots matched that filter.",
            "slots": [],
        }
    return {
        "available": True,
        "count": len(rows),
        "slots": rows,
    }


def book_slot(
    phone_number: str,
    doctor_id: str,
    slot_id: str,
    patient_name: str = "",
) -> dict:
    clean_phone = normalize_phone(phone_number)
    if len(clean_phone) < 7:
        return {"success": False, "error": "Please provide a valid phone number."}

    with get_conn() as conn:
        slot = conn.execute(
            """
            SELECT s.*, d.name AS doctor_name, d.specialty, d.room
            FROM slots s
            JOIN doctors d ON s.doctor_id = d.id
            WHERE s.id = ? AND s.doctor_id = ?
            """,
            (slot_id, doctor_id),
        ).fetchone()

        if not slot:
            return {"success": False, "error": "Selected doctor or slot was not found."}
        if slot["is_booked"]:
            return {"success": False, "error": "That slot is already booked. Please choose another."}

        count = conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
        appt_id = f"APT-{1001 + count}"
        now = datetime.now().isoformat(timespec="seconds")

        conn.execute("UPDATE slots SET is_booked = 1 WHERE id = ?", (slot_id,))
        conn.execute(
            """
            INSERT INTO appointments
            (id, phone_number, patient_name, doctor_id, slot_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'confirmed', ?, ?)
            """,
            (
                appt_id,
                clean_phone,
                patient_name.strip() or None,
                doctor_id,
                slot_id,
                now,
                now,
            ),
        )

    return {
        "success": True,
        "appointment": {
            "id": appt_id,
            "phone_number": clean_phone,
            "patient_name": patient_name.strip() or None,
            "doctor_id": doctor_id,
            "doctor_name": slot["doctor_name"],
            "specialty": slot["specialty"],
            "room": slot["room"],
            "slot_id": slot_id,
            "date": slot["date"],
            "time": slot["time"],
            "status": "confirmed",
        },
    }


def cancel_by_phone(phone_number: str, appointment_id: str = "") -> dict:
    clean_phone = normalize_phone(phone_number)
    if len(clean_phone) < 7:
        return {"success": False, "error": "Please provide a valid phone number."}

    with get_conn() as conn:
        if appointment_id.strip():
            row = conn.execute(
                """
                SELECT a.*, d.name AS doctor_name, d.specialty, s.date, s.time
                FROM appointments a
                JOIN doctors d ON a.doctor_id = d.id
                JOIN slots s ON a.slot_id = s.id
                WHERE a.id = ? AND a.phone_number = ? AND a.status = 'confirmed'
                """,
                (appointment_id.strip().upper(), clean_phone),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT a.*, d.name AS doctor_name, d.specialty, s.date, s.time
                FROM appointments a
                JOIN doctors d ON a.doctor_id = d.id
                JOIN slots s ON a.slot_id = s.id
                WHERE a.phone_number = ? AND a.status = 'confirmed'
                ORDER BY a.created_at DESC
                LIMIT 1
                """,
                (clean_phone,),
            ).fetchone()

        if not row:
            return {
                "success": False,
                "error": f"No active appointment found for phone number {clean_phone}.",
            }

        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "UPDATE appointments SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        conn.execute("UPDATE slots SET is_booked = 0 WHERE id = ?", (row["slot_id"],))

    return {
        "success": True,
        "cancelled_appointment": {
            "id": row["id"],
            "phone_number": row["phone_number"],
            "doctor_name": row["doctor_name"],
            "specialty": row["specialty"],
            "date": row["date"],
            "time": row["time"],
            "status": "cancelled",
        },
    }
