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
    
    HOW IT WORKS
    ------------
    The query performs the following steps:

    1.  Find every schedule that stops at `origin_id`  (stop_origin CTE).
    2.  Find every schedule that stops at `destination_id` (stop_dest CTE).
    3.  Keep only schedules present in BOTH CTEs AND where the origin stop comes
        BEFORE the destination stop (stop_order comparison).  This ensures we
        never return a schedule that runs in the wrong direction.
    4.  Join to `national_rail_schedules` to obtain schedule metadata
        (line name, service type, direction, departure/arrival windows,
        frequency, etc.).
    5.  If `travel_date` is provided:
          • Restrict results to schedules that operate on the weekday matching
            `travel_date` (e.g. "2025-06-01" is a Sunday → filter for 'sun').
          • Count booked/confirmed seats on that date to compute occupancy,
            then subtract from the total seat count to get available_seats.
        If `travel_date` is omitted, available_seats equals total_seats
        (no bookings subtracted, since there is no date to check against).
    6.  Compute `stops_travelled` = dest.stop_order − origin.stop_order, which
        is needed later by query_national_rail_fare() to price the journey.
    7.  Also return the origin/destination stop names for display purposes.

    Args:
        origin_id:       e.g. "NR01"
        destination_id:  e.g. "NR05"
        travel_date:     e.g. "2025-06-01" — used to count bookings; omit for general info
    
    Returns:
        List of dicts, one per matching schedule, containing:
          schedule_id, line_name, service_type, direction,
          first_train_time, last_train_time, frequency_min,
          origin_stop_order, dest_stop_order, stops_travelled,
          origin_station_name, destination_station_name,
          total_seats, available_seats
    """
    # ------------------------------------------------------------------
    # Build the day-of-week filter clause dynamically.
    # PostgreSQL's TO_CHAR(..., 'Dy') returns 3-letter abbreviations with
    # mixed case (e.g. 'Mon', 'Sun').  We use LOWER() so it matches the
    # CHECK constraint values in national_rail_schedule_operating_days
    # ('mon', 'tue', ..., 'sun').
    # ------------------------------------------------------------------
    day_filter_clause = ""
    if travel_date:
        # Add a WHERE predicate that restricts to schedules operating on
        # the weekday of the supplied travel_date.
        day_filter_clause = """
            AND nrs.schedule_id IN (
                SELECT schedule_id
                  FROM national_rail_schedule_operating_days
                 WHERE day_of_week = LOWER(TO_CHAR(%s::date, 'Dy'))
            )
        """

    sql = f"""
        -- CTE 1: all active stops at the origin station for any schedule.
        -- effective_to IS NULL means the stop record is currently active.
        WITH stop_origin AS (
            SELECT schedule_id,
                   stop_order AS origin_stop_order
              FROM national_rail_schedule_stops
             WHERE station_id    = %s
               AND is_stop       = TRUE
               AND effective_to  IS NULL
        ),

        -- CTE 2: all active stops at the destination station for any schedule.
        stop_dest AS (
            SELECT schedule_id,
                   stop_order AS dest_stop_order
              FROM national_rail_schedule_stops
             WHERE station_id    = %s
               AND is_stop       = TRUE
               AND effective_to  IS NULL
        ),

        -- CTE 3: valid schedules = schedules that appear in BOTH CTEs
        -- AND where origin comes strictly before destination in stop order.
        valid_schedules AS (
            SELECT o.schedule_id,
                   o.origin_stop_order,
                   d.dest_stop_order,
                   -- Count actual stops (is_stop=TRUE) between origin and destination,
                   -- inclusive of destination but not origin.
                   -- Using stop_order difference would include pass-through positions
                   -- (is_stop=FALSE) and overcharge on express schedules.
                   (SELECT COUNT(*)
                      FROM national_rail_schedule_stops nrss2
                     WHERE nrss2.schedule_id  = o.schedule_id
                       AND nrss2.stop_order   > o.origin_stop_order
                       AND nrss2.stop_order  <= d.dest_stop_order
                       AND nrss2.is_stop      = TRUE
                       AND nrss2.effective_to IS NULL
                   ) AS stops_travelled
              FROM stop_origin  o
              JOIN stop_dest    d USING (schedule_id)
             WHERE d.dest_stop_order > o.origin_stop_order
        ),

        -- CTE 4: total physical seat capacity per schedule, computed once here
        -- and referenced via JOIN so the three-table traversal does not execute
        -- once per result row.
        seat_counts AS (
            SELECT nsl.schedule_id,
                   COUNT(*) AS total_seats
              FROM national_rail_seats        ns2
              JOIN national_rail_coaches      nc  ON nc.coach_id   = ns2.coach_id
              JOIN national_rail_seat_layouts nsl ON nsl.layout_id = nc.layout_id
             GROUP BY nsl.schedule_id
        ),

        -- CTE 5: booked seats grouped by (schedule_id, departure_time) for the
        -- requested date. Grouping by departure_time is critical: the same physical
        -- seat can be booked on multiple departure times of the same schedule on the
        -- same day (the unique index is on schedule+date+departure_time+seat_pk).
        -- Summing across all departures would exceed total_seats and produce a
        -- negative available_seats; grouping by departure keeps each seat counted
        -- at most once per train run.
        -- When travel_date is NULL, NULL::date comparisons never match, so this CTE
        -- returns no rows — available_seats falls back to total_seats below. ✓
        bookings_per_departure AS (
            SELECT schedule_id,
                   departure_time,
                   COUNT(*) AS booked_count
              FROM booking_tickets
             WHERE travel_date = %s::date
               AND status     != 'cancelled'
               AND seat_pk    IS NOT NULL
             GROUP BY schedule_id, departure_time
        ),

        -- CTE 6: peak booking load — the highest single-departure booking count
        -- for each schedule on the requested date.
        -- available_seats is based on this worst-case departure: if the busiest
        -- train still has free seats, at least one departure has availability.
        max_bookings AS (
            SELECT schedule_id,
                   MAX(booked_count) AS peak_booked
              FROM bookings_per_departure
             GROUP BY schedule_id
        )

        SELECT
            nrs.schedule_id,
            nrs.line_name,
            nrs.service_type,
            nrs.direction,
            nrs.first_train_time,
            nrs.last_train_time,
            nrs.frequency_min,
            vs.origin_stop_order,
            vs.dest_stop_order,
            vs.stops_travelled,

            -- Origin and destination station names (denormalised for display).
            origin_stn.name   AS origin_station_name,
            dest_stn.name     AS destination_station_name,

            COALESCE(sc.total_seats, 0) AS total_seats,

            -- Conservative available-seat estimate: seats remaining on the
            -- most-booked departure of the day.
            -- GREATEST(..., 0) guards against data anomalies where booked_count
            -- somehow exceeds physical capacity.
            GREATEST(
                COALESCE(sc.total_seats, 0) - COALESCE(mb.peak_booked, 0),
                0
            ) AS available_seats

        FROM valid_schedules vs
        JOIN national_rail_schedules nrs ON nrs.schedule_id = vs.schedule_id
        LEFT JOIN seat_counts  sc  ON sc.schedule_id = nrs.schedule_id
        LEFT JOIN max_bookings mb  ON mb.schedule_id = nrs.schedule_id

        -- Join to station master tables to resolve human-readable names.
        JOIN national_rail_stations origin_stn ON origin_stn.station_id = %s
        JOIN national_rail_stations dest_stn   ON dest_stn.station_id   = %s

        -- Apply the optional operating-day filter (empty string when no date given).
        WHERE TRUE
        {day_filter_clause}

        ORDER BY nrs.line_name, nrs.schedule_id;
    """

    # Build the parameter list in the same order as the %s placeholders above.
    # Placeholders in order:
    #   1. stop_origin WHERE station_id = %s
    #   2. stop_dest   WHERE station_id = %s
    #   3. bookings_per_departure WHERE travel_date = %s::date  (NULL-safe)
    #   4. origin_stn  JOIN station_id = %s
    #   5. dest_stn    JOIN station_id = %s
    #   6. (optional)  day_filter_clause %s::date
    params = [
        origin_id,       # 1. stop_origin WHERE station_id = %s
        destination_id,  # 2. stop_dest   WHERE station_id = %s
        travel_date,     # 3. bookings_per_departure WHERE travel_date = %s::date
        origin_id,       # 4. origin station name JOIN
        destination_id,  # 5. destination station name JOIN
    ]

    if travel_date:
        # 6. The day-of-week filter needs travel_date a second time.
        params.append(travel_date)

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    """
    Calculate the fare for a national rail journey.

    HOW IT WORKS
    ------------
    National rail pricing uses a two-part formula stored in
    `national_rail_schedule_fares`:

        total_fare = base_fare_usd + (per_stop_rate_usd × stops_travelled)

    Each schedule can have two fare rows — one for 'standard' and one for
    'first' class — so the query must filter on both `schedule_id` AND
    `fare_class` to retrieve the correct parameters.

    The total fare is computed entirely in SQL using NUMERIC arithmetic
    (no floating-point rounding errors) and returned alongside the raw
    components so the caller can display an itemised breakdown to the user.

    Args:
        schedule_id:     e.g. "NR_SCH01"
        fare_class:      "standard" or "first"
        stops_travelled: number of stops between origin and destination (inclusive)

    Returns:
        dict with keys:
          fare_class        – the requested class ("standard" / "first")
          base_fare_usd     – fixed component of the fare (NUMERIC)
          per_stop_rate_usd – variable rate per stop (NUMERIC)
          total_fare_usd    – base + per_stop_rate × stops_travelled (NUMERIC)
        or None if no fare record exists for the given schedule / class combo.
    """

    sql = """
        SELECT
            fare_class,
            base_fare_usd,
            per_stop_rate_usd,
            -- Compute total fare in SQL to keep all arithmetic in NUMERIC,
            -- preventing any Python float rounding when the caller displays it.
            base_fare_usd + (per_stop_rate_usd * %s) AS total_fare_usd
        FROM national_rail_schedule_fares
        WHERE schedule_id = %s
          AND fare_class  = %s;
    """

    # Parameters in placeholder order:
    #   1. %s  → stops_travelled (used in the arithmetic expression)
    #   2. %s  → schedule_id
    #   3. %s  → fare_class
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (stops_travelled, schedule_id, fare_class))
            row = cur.fetchone()
            # fetchone() returns None when no matching fare row exists.
            return dict(row) if row else None

# ── METRO SCHEDULES & FARE ────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """
    Return metro schedules that serve both origin and destination in the correct order.

    HOW IT WORKS
    ------------
    The query mirrors the national rail availability logic but targets the metro
    tables and omits the date/occupancy dimension (metro trips are pay-as-you-go;
    there is no reserved seating and no capacity check needed).

    1.  Find all stops for `origin_id`      (stop_origin CTE).
    2.  Find all stops for `destination_id` (stop_dest   CTE).
    3.  Keep schedules present in BOTH CTEs where origin stop_sequence is
        strictly less than destination stop_sequence.
    4.  Join to `metro_schedules` for full schedule metadata including the
        fare parameters (base_fare_usd, per_stop_rate_usd) stored directly
        on the schedule row.
    5.  Join to `metro_stations` twice for human-readable station names.
    6.  Compute stops_travelled = dest.stop_sequence − origin.stop_sequence
        for use in query_metro_fare().

    Metro schedules also carry operating-day information in
    `metro_schedule_operating_days`, but that table is intentionally NOT
    filtered here: the agent layer decides whether to surface day-of-week
    restrictions to the user.  All matching schedules are returned so the
    caller has the complete picture.

    Args:
        origin_id:       e.g. "MS01"
        destination_id:  e.g. "MS09"
    
    Returns:
        List of dicts, one per matching schedule, containing:
          schedule_id, line_name, direction,
          first_train_time, last_train_time, frequency_min,
          base_fare_usd, per_stop_rate_usd,
          origin_stop_sequence, dest_stop_sequence, stops_travelled,
          origin_station_name, destination_station_name,
          operating_days  (sorted list of 3-letter day abbreviations)
    """

    sql = """
        -- CTE 1: all stops at the origin station across every metro schedule.
        WITH stop_origin AS (
            SELECT schedule_id,
                   stop_sequence AS origin_stop_sequence
              FROM metro_schedule_stops
             WHERE station_id = %s
        ),

        -- CTE 2: all stops at the destination station across every metro schedule.
        stop_dest AS (
            SELECT schedule_id,
                   stop_sequence AS dest_stop_sequence
              FROM metro_schedule_stops
             WHERE station_id = %s
        ),

        -- CTE 3: schedules valid for this journey direction.
        -- origin_stop_sequence must be strictly less than dest_stop_sequence.
        valid_schedules AS (
            SELECT o.schedule_id,
                   o.origin_stop_sequence,
                   d.dest_stop_sequence,
                   (d.dest_stop_sequence - o.origin_stop_sequence) AS stops_travelled
              FROM stop_origin o
              JOIN stop_dest   d USING (schedule_id)
             WHERE d.dest_stop_sequence > o.origin_stop_sequence
        ),

        -- CTE 4: aggregate operating days per schedule into a sorted array so
        -- the caller receives a single row per schedule (avoids fan-out from
        -- joining metro_schedule_operating_days directly).
        operating_days_agg AS (
            SELECT schedule_id,
                   ARRAY_AGG(day_of_week ORDER BY
                       CASE day_of_week
                           WHEN 'mon' THEN 1 WHEN 'tue' THEN 2 WHEN 'wed' THEN 3
                           WHEN 'thu' THEN 4 WHEN 'fri' THEN 5 WHEN 'sat' THEN 6
                           WHEN 'sun' THEN 7
                       END
                   ) AS operating_days
              FROM metro_schedule_operating_days
             GROUP BY schedule_id
        )

        SELECT
            ms.schedule_id,
            ms.line_name,
            ms.direction,
            ms.first_train_time,
            ms.last_train_time,
            ms.frequency_min,
            -- Fare components are stored directly on metro_schedules.
            ms.base_fare_usd,
            ms.per_stop_rate_usd,
            vs.origin_stop_sequence,
            vs.dest_stop_sequence,
            vs.stops_travelled,
            -- Human-readable station names for display in the agent's response.
            origin_stn.name AS origin_station_name,
            dest_stn.name   AS destination_station_name,
            -- Coalesce in case a schedule somehow has no operating_days rows.
            COALESCE(oda.operating_days, ARRAY[]::VARCHAR[]) AS operating_days

        FROM valid_schedules         vs
        JOIN metro_schedules         ms        ON ms.schedule_id  = vs.schedule_id
        JOIN metro_stations          origin_stn ON origin_stn.station_id = %s
        JOIN metro_stations          dest_stn   ON dest_stn.station_id   = %s
        LEFT JOIN operating_days_agg oda        ON oda.schedule_id       = ms.schedule_id

        ORDER BY ms.line_name, ms.schedule_id;
    """

    # Parameters in placeholder order:
    #   1. %s → stop_origin WHERE station_id = %s
    #   2. %s → stop_dest   WHERE station_id = %s
    #   3. %s → origin_stn  JOIN station_id  = %s  (for name lookup)
    #   4. %s → dest_stn    JOIN station_id  = %s  (for name lookup)
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (origin_id, destination_id, origin_id, destination_id))
            return [dict(row) for row in cur.fetchall()]

def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    """
    Calculate the metro fare for a single-ticket journey.

    HOW IT WORKS
    ------------
    Metro pricing uses the same two-part formula as national rail, but the
    fare parameters (base_fare_usd, per_stop_rate_usd) are stored directly
    on the `metro_schedules` row rather than in a separate fares table.
    There is only one fare class for metro (no 'standard' / 'first' split).

        total_fare = base_fare_usd + (per_stop_rate_usd × stops_travelled)

    All arithmetic is done inside SQL using NUMERIC types to avoid any
    floating-point rounding that could occur if the multiplication were
    performed in Python.

    Args:
        schedule_id:     e.g. "MS_SCH01"
        stops_travelled: number of stops between origin and destination

    Returns:
        dict with keys:
          base_fare_usd     – fixed component of the fare (NUMERIC)
          per_stop_rate_usd – variable rate per stop (NUMERIC)
          total_fare_usd    – base + per_stop_rate × stops_travelled (NUMERIC)
        or None if no schedule with the given ID exists.
    """
    sql = """
        SELECT
            base_fare_usd,
            per_stop_rate_usd,
            -- Compute total fare in SQL so all intermediate values remain NUMERIC,
            -- consistent with how national rail fares are handled.
            base_fare_usd + (per_stop_rate_usd * %s) AS total_fare_usd
        FROM metro_schedules
        WHERE schedule_id = %s;
    """

    # Parameters in placeholder order:
    #   1. %s → stops_travelled (used in the arithmetic expression)
    #   2. %s → schedule_id
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (stops_travelled, schedule_id))
            row = cur.fetchone()
            # fetchone() returns None when the schedule_id does not exist.
            return dict(row) if row else None

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
    raise NotImplementedError("TODO: implement after designing your schema")


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """
    Cancel a national rail booking owned by the given user.

    Calculates the refund amount according to the booking's service type:
      - Normal service: RF001 windows (100% / 75% / 50% / 0%)
      - Express service: RF002 windows (100% / 50% / 0%)

    Args:
        booking_id: e.g. "BK001"
        user_id:    must match the booking's user_id

    Returns:
        (True, result_dict)  with refund_amount_usd and policy note
        (False, error_msg)
    """
    raise NotImplementedError("TODO: implement after designing your schema")


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

    Passwords are hashed with Argon2id before storage.
    The salt is embedded in the PHC-format hash string — no separate salt column needed.
    """
    import uuid

    # Generate a unique user ID using the first 8 hex chars of a UUID
    user_id = "RU-" + uuid.uuid4().hex[:8].upper()
    # Combine first and last name into a single full name string
    full_name = f"{first_name} {surname}"
    # Only year is provided, so default to Jan 1 of that year
    dob = f"{year_of_birth}-01-01"

    # Hash password with Argon2id — salt is embedded in the output PHC string
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
                """
                INSERT INTO users (user_id, full_name, email, date_of_birth)
                VALUES (%s, %s, %s, %s)
                """,
                (user_id, full_name, email, dob),
            )
            # Insert hashed credentials — salt columns omitted because
            # Argon2id embeds the salt inside the PHC hash string
            cur.execute(
                """
                INSERT INTO user_security
                    (user_id, password_hash, secret_question, secret_answer_hash)
                VALUES (%s, %s, %s, %s)
                """,
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
