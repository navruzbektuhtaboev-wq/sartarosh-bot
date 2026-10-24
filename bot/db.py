import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Booking:
    id: int
    user_id: int
    client_name: str
    phone: str
    barber_id: str
    service_id: str
    start: datetime
    end: datetime
    status: str
    reminded: bool
    calendar_event_id: str | None


SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    client_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    barber_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    reminded INTEGER NOT NULL DEFAULT 0,
    calendar_event_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bookings_barber ON bookings(barber_id, start);
CREATE INDEX IF NOT EXISTS idx_bookings_user ON bookings(user_id);
"""


def _row(r) -> Booking:
    return Booking(
        id=r["id"],
        user_id=r["user_id"],
        client_name=r["client_name"],
        phone=r["phone"],
        barber_id=r["barber_id"],
        service_id=r["service_id"],
        start=datetime.fromisoformat(r["start"]),
        end=datetime.fromisoformat(r["end"]),
        status=r["status"],
        reminded=bool(r["reminded"]),
        calendar_event_id=r["calendar_event_id"],
    )


class DB:
    """Барча вақтлар timezone билан ISO форматда сақланади."""

    def __init__(self, path: str = "bookings.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()

    def busy_intervals(self, barber_id: str, day_start: datetime, day_end: datetime):
        rows = self.conn.execute(
            "SELECT start, end FROM bookings WHERE barber_id=? AND status='active' "
            "AND start < ? AND end > ?",
            (barber_id, day_end.isoformat(), day_start.isoformat()),
        ).fetchall()
        return [(datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"])) for r in rows]

    def create(self, user_id, client_name, phone, barber_id, service_id, start, end) -> Booking | None:
        """Вақт банд бўлса None қайтаради (икки мижоз бир вақтда босса ҳам)."""
        with self.lock:
            clash = self.conn.execute(
                "SELECT 1 FROM bookings WHERE barber_id=? AND status='active' AND start < ? AND end > ?",
                (barber_id, end.isoformat(), start.isoformat()),
            ).fetchone()
            if clash:
                return None
            cur = self.conn.execute(
                "INSERT INTO bookings (user_id, client_name, phone, barber_id, service_id, start, end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, client_name, phone, barber_id, service_id, start.isoformat(), end.isoformat()),
            )
            self.conn.commit()
            return self.get(cur.lastrowid)

    def get(self, booking_id: int) -> Booking | None:
        r = self.conn.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        return _row(r) if r else None

    def user_upcoming(self, user_id: int, now: datetime) -> list[Booking]:
        rows = self.conn.execute(
            "SELECT * FROM bookings WHERE user_id=? AND status='active' AND start >= ? ORDER BY start",
            (user_id, now.isoformat()),
        ).fetchall()
        return [_row(r) for r in rows]

    def between(self, start: datetime, end: datetime) -> list[Booking]:
        rows = self.conn.execute(
            "SELECT * FROM bookings WHERE status='active' AND start >= ? AND start < ? ORDER BY barber_id, start",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [_row(r) for r in rows]

    def due_reminders(self, now: datetime, until: datetime) -> list[Booking]:
        rows = self.conn.execute(
            "SELECT * FROM bookings WHERE status='active' AND reminded=0 AND start > ? AND start <= ?",
            (now.isoformat(), until.isoformat()),
        ).fetchall()
        return [_row(r) for r in rows]

    def mark_reminded(self, booking_id: int):
        self.conn.execute("UPDATE bookings SET reminded=1 WHERE id=?", (booking_id,))
        self.conn.commit()

    def cancel(self, booking_id: int):
        self.conn.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (booking_id,))
        self.conn.commit()

    def set_event_id(self, booking_id: int, event_id: str):
        self.conn.execute("UPDATE bookings SET calendar_event_id=? WHERE id=?", (event_id, booking_id))
        self.conn.commit()

    def last_phone(self, user_id: int) -> str | None:
        r = self.conn.execute(
            "SELECT phone FROM bookings WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)
        ).fetchone()
        return r["phone"] if r else None
