"""
TransitFlow — PostgreSQL / Relational Database Layer
=====================================================
This module handles all queries to PostgreSQL.

TWO ROLES ARE SERVED HERE:
  1. Relational  → dual-network transit (metro + national rail),
                   availability, fares, bookings, seat selection
  2. Vector      → policy document similarity search (pgvector)

STUDENT TASK
------------
Design your schema in databases/relational/schema.sql, seed it with
skeleton/seed_postgres.py, then implement the query functions below.

Functions prefixed with `query_`  are read-only lookups called by the agent.
Functions prefixed with `execute_` are write operations (booking/cancellation).

The vector functions (query_policy_vector_search, store_policy_document)
are already implemented — do not modify them.
"""

from __future__ import annotations

import json
import random
import string
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

from decimal import Decimal
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
_ph = PasswordHasher()
from skeleton.config import PG_DSN, VECTOR_TOP_K, VECTOR_SIMILARITY_THRESHOLD


def _connect():
    """Return a new psycopg2 connection with autocommit enabled."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    return conn


def _gen_booking_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"BK-{suffix}"


def _gen_payment_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"PM-{suffix}"


# ── Example ───────────────────────────────────────────────────────────────────
# The block below shows the query pattern: open a cursor, run SQL, return rows.
# Use _connect() for read-only queries; for write operations use a manual
# connection with conn.commit() / conn.rollback() (see execute_booking below).

def example_query() -> dict:
    """Example: returns the name of the connected database."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT current_database() AS db;")
            return dict(cur.fetchone())

# TODO: Implement the query_ and execute_ functions below.
# ─────────────────────────────────────────────────────────────────────────────


# ── NATIONAL RAIL AVAILABILITY ────────────────────────────────────────────────

def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,
) -> list[dict]:
    """
    Return national rail schedules that serve both origin and destination stations
    in the correct order, along with seat occupancy for the requested travel date.

    Args:
        origin_id:       e.g. "NR01"
        destination_id:  e.g. "NR05"
        travel_date:     e.g. "2025-06-01" — used to count bookings; omit for general info
    """
    raise NotImplementedError("TODO: implement after designing your schema")


def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    """
    Calculate the fare for a national rail journey.

    Args:
        schedule_id:     e.g. "NR_SCH01"
        fare_class:      "standard" or "first"
        stops_travelled: number of stops between origin and destination (inclusive)

    Returns:
        dict with fare_class, base_fare_usd, per_stop_rate_usd, total_fare_usd
    """
    raise NotImplementedError("TODO: implement after designing your schema")


# ── METRO SCHEDULES & FARE ────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """
    Return metro schedules that serve both origin and destination in the correct order.

    Args:
        origin_id:       e.g. "MS01"
        destination_id:  e.g. "MS09"
    """
    raise NotImplementedError("TODO: implement after designing your schema")


def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    """
    Calculate the metro fare for a single-ticket journey.

    Args:
        schedule_id:     e.g. "MS_SCH01"
        stops_travelled: number of stops between origin and destination

    Returns:
        dict with base_fare_usd, per_stop_rate_usd, total_fare_usd
    """
    raise NotImplementedError("TODO: implement after designing your schema")


# ── SEAT SELECTION ────────────────────────────────────────────────────────────

def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
) -> list[dict]:
    """
    Return available seats for a national rail journey on a given date.

    Args:
        schedule_id:  e.g. "NR_SCH01"
        travel_date:  e.g. "2025-06-01"
        fare_class:   "standard" or "first"

    Returns:
        List of dicts: {seat_id, coach, row, column}
    """
    raise NotImplementedError("TODO: implement after designing your schema")


def auto_select_adjacent_seats(available_seats: list[dict], count: int) -> list[str]:
    """
    Select `count` seats that are as close together as possible (same row preferred,
    then adjacent rows). Returns a list of seat_ids.

    Args:
        available_seats: output of query_available_seats()
        count:           number of seats needed
    """
    if not available_seats or count <= 0:
        return []
    if count >= len(available_seats):
        return [s["seat_id"] for s in available_seats[:count]]

    from collections import defaultdict
    rows: dict[int, list[dict]] = defaultdict(list)
    for seat in available_seats:
        rows[seat["row"]].append(seat)

    for row_seats in sorted(rows.values(), key=lambda s: s[0]["row"]):
        if len(row_seats) >= count:
            return [s["seat_id"] for s in row_seats[:count]]

    sorted_seats = sorted(available_seats, key=lambda s: (s["row"], s["column"]))
    return [s["seat_id"] for s in sorted_seats[:count]]


# ── USER & BOOKING QUERIES ────────────────────────────────────────────────────

def query_user_profile(user_email: str) -> Optional[dict]:
    """Return a user's profile by email."""
    raise NotImplementedError("TODO: implement after designing your schema")


def query_user_bookings(user_email: str) -> dict:
    """
    Return a user's combined booking history (national rail + metro).

    Returns:
        dict with keys 'national_rail' (list) and 'metro' (list)
    """
    raise NotImplementedError("TODO: implement after designing your schema")


def query_payment_info(booking_id: str) -> Optional[dict]:
    """Return payment record for a booking or metro trip."""
    raise NotImplementedError("TODO: implement after designing your schema")


# ── TRANSACTIONAL OPERATIONS ──────────────────────────────────────────────────
def execute_booking(
    user_id: str,
    schedule_id: str,
    origin_station_id: str,
    destination_station_id: str,
    travel_date: str,
    fare_class: str,
    seat_id: str,
    ticket_type: str = "single",
) -> tuple[bool, dict | str]:
    """
    Create a national rail booking for a logged-in user.

    Args:
        user_id:                e.g. "RU01" — must match the logged-in user
        schedule_id:            e.g. "NR_SCH01"
        origin_station_id:      e.g. "NR01"
        destination_station_id: e.g. "NR05"
        travel_date:            e.g. "2025-06-01"
        fare_class:             "standard" or "first"
        seat_id:                e.g. "B05" (or "any" to auto-assign)
        ticket_type:            "single" (default) or "return"

    Returns:
        (True, booking_dict)   on success
        (False, error_message) on failure
    """
    # Open a manual-commit connection so booking + payment are atomic
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # ── 1. Look up the schedule to get departure time and service type ──
            cur.execute(
                """
                SELECT s.first_train_time, s.service_type,
                       f.base_fare_usd, f.per_stop_rate_usd
                FROM national_rail_schedules s
                JOIN national_rail_schedule_fares f
                  ON f.schedule_id = s.schedule_id AND f.fare_class = %s
                WHERE s.schedule_id = %s
                """,
                (fare_class, schedule_id),
            )
            schedule = cur.fetchone()
            if not schedule:
                return (False, f"Schedule {schedule_id} not found or fare class {fare_class} unavailable")

            departure_time = schedule["first_train_time"]

            # ── 2. Count stops between origin and destination ──
            cur.execute(
                """
                SELECT station_id, stop_order
                FROM national_rail_schedule_stops
                WHERE schedule_id = %s
                  AND station_id IN (%s, %s)
                  AND is_stop = TRUE
                  AND (effective_to IS NULL OR effective_to > CURRENT_DATE)
                ORDER BY stop_order
                """,
                (schedule_id, origin_station_id, destination_station_id),
            )
            stop_rows = cur.fetchall()
            if len(stop_rows) != 2:
                return (False, "Origin or destination station not found on this schedule")

            stops_travelled = abs(stop_rows[1]["stop_order"] - stop_rows[0]["stop_order"])

            # ── 3. Calculate fare ──
            # Keep psycopg2's Decimal return type throughout to avoid floating-point errors.
            # Decimal(stops_travelled) ensures multiplication stays in the Decimal domain.
            base_fare     = schedule["base_fare_usd"]        # Decimal
            per_stop_rate = schedule["per_stop_rate_usd"]    # Decimal
            total_fare    = base_fare + per_stop_rate * Decimal(stops_travelled)

            # ── 4. Resolve seat: look up seat_pk from seat_code ──
            if seat_id.lower() == "any":
                # Auto-assign: pick first available seat in the correct fare class coach
                cur.execute(
                    """
                    SELECT ns.seat_pk, ns.seat_code, nc.coach_name
                    FROM national_rail_seats ns
                    JOIN national_rail_coaches nc ON nc.coach_id = ns.coach_id
                    JOIN national_rail_seat_layouts nl ON nl.layout_id = nc.layout_id
                    WHERE nl.schedule_id = %s
                      AND nc.fare_class = %s
                      AND ns.seat_pk NOT IN (
                          SELECT bt.seat_pk FROM booking_tickets bt
                          WHERE bt.schedule_id = %s
                            AND bt.travel_date = %s
                            AND bt.status != 'cancelled'
                            AND bt.seat_pk IS NOT NULL
                      )
                    LIMIT 1
                    """,
                    (schedule_id, fare_class, schedule_id, travel_date),
                )
                seat_row = cur.fetchone()
                if not seat_row:
                    return (False, "No available seats for the requested class")
                seat_pk   = seat_row["seat_pk"]
                seat_code = seat_row["seat_code"]
                coach     = seat_row["coach_name"]
            else:
                # Use the requested seat_id (seat_code)
                cur.execute(
                    """
                    SELECT ns.seat_pk, ns.seat_code, nc.coach_name
                    FROM national_rail_seats ns
                    JOIN national_rail_coaches nc ON nc.coach_id = ns.coach_id
                    JOIN national_rail_seat_layouts nl ON nl.layout_id = nc.layout_id
                    WHERE nl.schedule_id = %s
                      AND nc.fare_class = %s
                      AND ns.seat_code = %s
                    """,
                    (schedule_id, fare_class, seat_id),
                )
                seat_row = cur.fetchone()
                if not seat_row:
                    return (False, f"Seat {seat_id} not found in {fare_class} class for schedule {schedule_id}")
                seat_pk   = seat_row["seat_pk"]
                seat_code = seat_row["seat_code"]
                coach     = seat_row["coach_name"]

                # Confirm the seat is not already taken on this date
                cur.execute(
                    """
                    SELECT 1 FROM booking_tickets
                    WHERE schedule_id = %s
                      AND travel_date = %s
                      AND seat_pk = %s
                      AND status != 'cancelled'
                    """,
                    (schedule_id, travel_date, seat_pk),
                )
                if cur.fetchone():
                    return (False, f"Seat {seat_id} is already booked for {travel_date}")

            # ── 5. Generate IDs ──
            booking_id = _gen_booking_id()
            payment_id = _gen_payment_id()

            # ── 6. Insert travel_orders ──
            # FIX BUG-1: all values bound via %s — no inline literals in SQL string
            cur.execute(
                """
                INSERT INTO travel_orders (order_id, user_id, order_type, amount_usd, status)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (booking_id, user_id, "national_rail", round(total_fare, 2), "confirmed"),
            )

            # ── 7. Insert bookings header ──
            cur.execute(
                "INSERT INTO bookings (booking_id) VALUES (%s)",
                (booking_id,),
            )

            # ── 8. Insert booking_tickets ──
            # status bound via %s so it is consistent with step 6 above
            leg = "single" if ticket_type == "single" else "outbound"
            cur.execute(
                """
                INSERT INTO booking_tickets
                    (booking_id, schedule_id, origin_station_id, destination_station_id,
                     seat_pk, travel_date, departure_time, ticket_type, fare_class,
                     coach, seat_code, stops_travelled, leg, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    booking_id, schedule_id, origin_station_id, destination_station_id,
                    seat_pk, travel_date, departure_time, ticket_type, fare_class,
                    coach, seat_code, stops_travelled, leg, "confirmed",
                ),
            )

            # ── 9. Insert payment — must be in the same commit as the booking ──
            cur.execute(
                """
                INSERT INTO payments (payment_id, amount_usd, method, status, paid_at)
                VALUES (%s, %s, %s, %s, NOW())
                """,
                (payment_id, round(total_fare, 2), "credit_card", "paid"),
            )
            cur.execute(
                """
                INSERT INTO payment_sources (payment_id, source_type, national_rail_booking_id)
                VALUES (%s, %s, %s)
                """,
                (payment_id, "national_rail_booking", booking_id),
            )

        # Single commit covers all inserts — atomicity requirement met
        conn.commit()

        booking_dict = {
            "booking_id":              booking_id,
            "user_id":                 user_id,
            "schedule_id":             schedule_id,
            "origin_station_id":       origin_station_id,
            "destination_station_id":  destination_station_id,
            "travel_date":             travel_date,
            "fare_class":              fare_class,
            "ticket_type":             ticket_type,
            "seat_code":               seat_code,
            "coach":                   coach,
            "stops_travelled":         stops_travelled,
            # Convert to float only at the final output boundary for JSON serialisation
            "amount_usd":              float(round(total_fare, 2)),
            "payment_id":              payment_id,
        }
        return (True, booking_dict)

    except Exception as e:
        conn.rollback()
        return (False, str(e))
    finally:
        conn.close()


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """
    Cancel a national rail booking owned by the given user.

    Refund policy:
      - Normal service  RF001: ≥48h → 100%, 24–48h → 75% ($0.50 fee),
                               2–24h → 50% ($0.50 fee), <2h → 0%
      - Express service RF002: ≥48h → 100%, 24–48h → 50% ($1.00 fee), <24h → 0%

    Args:
        booking_id: e.g. "BK001"
        user_id:    must match the booking's user_id

    Returns:
        (True, result_dict)  with refund_amount_usd and policy note
        (False, error_msg)
    """
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # ── 1. Verify the booking exists, belongs to this user, and is cancellable ──
            cur.execute(
                """
                SELECT to_.order_id, to_.amount_usd, to_.status,
                       s.service_type
                FROM travel_orders to_
                JOIN bookings b ON b.booking_id = to_.order_id
                JOIN booking_tickets bt ON bt.booking_id = b.booking_id
                JOIN national_rail_schedules s ON s.schedule_id = bt.schedule_id
                WHERE to_.order_id = %s
                  AND to_.user_id = %s
                  AND to_.status = 'confirmed'
                LIMIT 1
                """,
                (booking_id, user_id),
            )
            order = cur.fetchone()
            if not order:
                return (False, "Booking not found, already cancelled, or does not belong to this user")

            service_type = order["service_type"]
            # Keep psycopg2's Decimal return type to preserve NUMERIC(10,2) precision
            amount_usd = order["amount_usd"]   # Decimal

            # ── 2. Get the earliest uncancelled ticket's departure datetime ──
            cur.execute(
                """
                SELECT bt.travel_date, bt.departure_time
                FROM booking_tickets bt
                WHERE bt.booking_id = %s
                  AND bt.status = 'confirmed'
                ORDER BY bt.travel_date ASC, bt.departure_time ASC
                LIMIT 1
                """,
                (booking_id,),
            )
            ticket = cur.fetchone()
            if not ticket:
                return (False, "No active tickets found for this booking")

            # Combine date + time into a timezone-aware datetime for accurate hour calculation
            departure_dt = datetime.combine(
                ticket["travel_date"],
                ticket["departure_time"],
            ).replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            hours_before = (departure_dt - now).total_seconds() / 3600

            # ── 3. Apply refund policy ──
            # All percentages and fees built from strings to avoid inheriting
            # floating-point error (e.g. Decimal(0.75) → Decimal('0.7499...'))
            #
            # RF001 (normal):  ≥48h→100%, 24–48h→75% ($0.50 fee), 2–24h→50% ($0.50 fee), <2h→0%
            # RF002 (express): ≥48h→100%, 24–48h→50% ($1.00 fee), <24h→0%
            if service_type == "normal":
                if hours_before >= 48:
                    refund_pct  = Decimal("1.00")
                    admin_fee   = Decimal("0.00")
                    policy_note = "RF001 W1: full refund (≥48 h before departure)"
                elif hours_before >= 24:
                    refund_pct  = Decimal("0.75")
                    admin_fee   = Decimal("0.50")
                    policy_note = "RF001 W2: 75% refund (24–48 h before departure)"
                elif hours_before >= 2:
                    refund_pct  = Decimal("0.50")
                    admin_fee   = Decimal("0.50")
                    policy_note = "RF001 W3: 50% refund (2–24 h before departure)"
                else:
                    refund_pct  = Decimal("0.00")
                    admin_fee   = Decimal("0.00")
                    policy_note = "RF001 W4: no refund (<2 h before departure)"
            else:
                # Express service — RF002
                # FIX BUG-2: W1 (≥48h) is a full refund with no admin fee per RF002 spec
                if hours_before >= 48:
                    refund_pct  = Decimal("1.00")
                    admin_fee   = Decimal("0.00")
                    policy_note = "RF002 W1: full refund (≥48 h before departure)"
                elif hours_before >= 24:
                    refund_pct  = Decimal("0.50")
                    admin_fee   = Decimal("1.00")
                    policy_note = "RF002 W2: 50% refund minus $1.00 fee (24–48 h before departure)"
                else:
                    refund_pct  = Decimal("0.00")
                    admin_fee   = Decimal("0.00")
                    policy_note = "RF002 W3: no refund (<24 h before departure)"

            # Clamp to zero in case fee exceeds the refund amount
            refund_amount = max(Decimal("0.00"), round(amount_usd * refund_pct - admin_fee, 2))

            # ── 4. Cancel all confirmed tickets in this booking ──
            cur.execute(
                """
                UPDATE booking_tickets
                SET status       = 'cancelled',
                    cancelled_at = NOW()
                WHERE booking_id = %s
                  AND status     = 'confirmed'
                """,
                (booking_id,),
            )

            # ── 5. Update the parent order status ──
            cur.execute(
                "UPDATE travel_orders SET status = 'cancelled' WHERE order_id = %s",
                (booking_id,),
            )

            # ── 6. Mark the payment as refunded ──
            cur.execute(
                """
                UPDATE payments p
                SET status = 'refunded'
                FROM payment_sources ps
                WHERE ps.payment_id               = p.payment_id
                  AND ps.national_rail_booking_id = %s
                """,
                (booking_id,),
            )

        conn.commit()

        return (True, {
            "booking_id":        booking_id,
            "status":            "cancelled",
            # Convert to float only at the final output boundary for JSON serialisation
            "refund_amount_usd": float(refund_amount),
            "policy_note":       policy_note,
        })

    except Exception as e:
        conn.rollback()
        return (False, str(e))
    finally:
        conn.close()
        
# ── AUTHENTICATION QUERIES ────────────────────────────────────────────────────

def register_user(
    email: str,
    first_name: str,
    surname: str,
    year_of_birth: int,
    password: str,
    secret_question: str,
    secret_answer: str,
) -> tuple[bool, str]:
    """
    Register a new user.
    Returns (True, user_id) on success or (False, error_message) on failure.

    NOTE: passwords are stored as plain text here intentionally for teaching
    purposes. In production, replace with a salted hash (e.g. bcrypt).
    """
    import uuid

    # Generate a unique user ID using the first 8 hex chars of a UUID
    user_id = "RU-" + uuid.uuid4().hex[:8].upper()
    # Combine first and last name into a single full name string
    full_name = f"{first_name} {surname}"
    # Only year is provided, so default to Jan 1 of that year
    dob = f"{year_of_birth}-01-01"

    # Hash password with Argon2id — salt is embedded in the output string (PHC format)
    pw_hash = _ph.hash(password)
    # Hash secret answer lowercase so verify_secret_answer can be case-insensitive
    ans_hash = _ph.hash(secret_answer.lower())

    # Open a direct connection for manual transaction control
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            # Insert basic user profile into the users table
            cur.execute(
                "INSERT INTO users (user_id, full_name, email, date_of_birth) VALUES (%s, %s, %s, %s)",
                (user_id, full_name, email, dob),
            )
            # Insert hashed credentials into the security table
            cur.execute(
                """INSERT INTO user_security
                       (user_id, password_hash, secret_question, secret_answer_hash)
                   VALUES (%s, %s, %s, %s)""",
                (user_id, pw_hash, secret_question, ans_hash),
            )
        # Commit both inserts together as a single atomic transaction
        conn.commit()
        return (True, user_id)
    except Exception as e:
        # Roll back all changes if anything goes wrong
        conn.rollback()
        return (False, str(e))
    finally:
        # Always close the connection regardless of success or failure
        conn.close()


def login_user(email: str, password: str) -> Optional[dict]:
    """
    Verify credentials. Returns a user dict on success or None on failure.
    Dict keys: user_id, email, full_name, first_name, surname, phone, date_of_birth, is_active.
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Fetch user profile and stored password hash in one query
            cur.execute(
                """
                SELECT u.user_id, u.email, u.full_name, u.phone,
                       u.date_of_birth, u.is_active,
                       us.password_hash
                FROM users u
                JOIN user_security us ON us.user_id = u.user_id
                WHERE u.email = %s
                """,
                (email,),
            )
            row = cur.fetchone()
            # Convert to plain dict immediately while cursor is still open
            row = dict(row) if row else None

    # Return None if user not found
    if not row:
        return None

    # Verify password against Argon2id hash — raises VerifyMismatchError on wrong password
    try:
        _ph.verify(row["password_hash"], password)
    except VerifyMismatchError:
        return None

    # Split full_name back into first/surname for the required return shape
    parts = (row["full_name"] or "").split(" ", 1)
    return {
        "user_id":       row["user_id"],
        "email":         row["email"],
        "full_name":     row["full_name"],
        "first_name":    parts[0] if parts else "",
        "surname":       parts[1] if len(parts) > 1 else "",
        "phone":         row["phone"],
        "date_of_birth": str(row["date_of_birth"]),
        "is_active":     row["is_active"],
    }


def get_user_secret_question(email: str) -> Optional[str]:
    """Return the secret question for a registered email, or None if not found."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Look up the secret question by matching email in users table
            cur.execute(
                """
                SELECT us.secret_question
                FROM user_security us
                JOIN users u ON u.user_id = us.user_id
                WHERE u.email = %s
                """,
                (email,),
            )
            row = cur.fetchone()
            # Convert to plain dict while cursor is still open
            row = dict(row) if row else None

    return row["secret_question"] if row else None


def verify_secret_answer(email: str, answer: str) -> bool:
    """Return True if the provided answer matches the stored secret answer (case-insensitive)."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Fetch stored secret answer hash by matching email
            cur.execute(
                """
                SELECT us.secret_answer_hash
                FROM user_security us
                JOIN users u ON u.user_id = us.user_id
                WHERE u.email = %s
                """,
                (email,),
            )
            row = cur.fetchone()
            # Convert to plain dict while cursor is still open
            row = dict(row) if row else None

    if not row or not row["secret_answer_hash"]:
        return False

    # Lowercase input matches how the answer was stored during registration
    try:
        _ph.verify(row["secret_answer_hash"], answer.lower())
        return True
    except VerifyMismatchError:
        return False


def update_password(email: str, new_password: str) -> bool:
    """Update the password for a user. Returns True if the row was updated."""
    # Hash the new password before storing
    new_hash = _ph.hash(new_password)

    # Open a direct connection for manual transaction control
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE user_security
                SET password_hash = %s
                FROM users u
                WHERE user_security.user_id = u.user_id
                  AND u.email = %s
                """,
                (new_hash, email),
            )
            updated = cur.rowcount
        conn.commit()
        return updated > 0
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()
        
# ── VECTOR / RAG QUERIES — do not modify ─────────────────────────────────────

def query_policy_vector_search(embedding: list[float], top_k: int = VECTOR_TOP_K) -> list[dict]:
    """
    Find the most relevant policy documents for a given query embedding.

    Args:
        embedding: Query vector from llm.embed(user_question)
        top_k:     Number of results to return

    Returns:
        List of dicts with title, category, content, and similarity score
    """
    sql = """
        SELECT
            title,
            category,
            content,
            1 - (embedding <=> %s::vector) AS similarity
        FROM policy_documents
        WHERE 1 - (embedding <=> %s::vector) > %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (vec_str, vec_str, VECTOR_SIMILARITY_THRESHOLD, vec_str, top_k))
            return [dict(row) for row in cur.fetchall()]


def store_policy_document(
    title: str,
    category: str,
    content: str,
    embedding: list[float],
    source_file: str = "",
) -> int:
    """
    Insert a policy document with its embedding into the database.
    Used by skeleton/seed_vectors.py — students don't need to call this directly.

    Returns:
        The new document's id
    """
    sql = """
        INSERT INTO policy_documents (title, category, content, embedding, source_file)
        VALUES (%s, %s, %s, %s::vector, %s)
        RETURNING id
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (title, category, content, vec_str, source_file))
            return cur.fetchone()[0]
