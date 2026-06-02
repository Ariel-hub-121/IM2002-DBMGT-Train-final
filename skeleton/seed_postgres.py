"""
Seed PostgreSQL with all TransitFlow mock data from train-mock-data/.

Usage:
    python skeleton/seed_postgres.py

Run AFTER docker-compose up -d.
You must first design and create your tables in databases/relational/schema.sql.
Safe to re-run: implement your inserts with ON CONFLICT DO NOTHING.
"""

import json
import os
import sys

import psycopg2
from psycopg2.extras import execute_values

# ── resolve paths ────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR    = os.path.join(PROJECT_DIR, "train-mock-data")

sys.path.insert(0, PROJECT_DIR)
from skeleton import config as cfg


def load(filename):
    with open(os.path.join(DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def connect():
    return psycopg2.connect(
        host=cfg.PG_HOST,
        port=cfg.PG_PORT,
        dbname=cfg.PG_DB,
        user=cfg.PG_USER,
        password=cfg.PG_PASSWORD,
    )


def insert_many(cur, table, columns, rows):
    """Bulk insert with ON CONFLICT DO NOTHING. Returns row count inserted."""
    if not rows:
        return 0
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s "
        f"ON CONFLICT DO NOTHING"
    )
    execute_values(cur, sql, rows)
    return cur.rowcount


# ── seeders ──────────────────────────────────────────────────────────────────

def seed_metro_stations(cur):
    data = load("metro_stations.json")

    # 1. metro_stations
    station_rows = [(s["station_id"], s["name"]) for s in data]
    n = insert_many(cur, "metro_stations", ["station_id", "name"], station_rows)
    print(f"  metro_stations:            {n} rows")

    # 2. metro_station_lines — one row per (station, line) pair
    line_rows = [
        (s["station_id"], line)
        for s in data
        for line in s["lines"]
    ]
    n = insert_many(cur, "metro_station_lines", ["station_id", "line_name"], line_rows)
    print(f"  metro_station_lines:       {n} rows")

    # 3. metro_line_transfer_times — one row per ordered line pair at intra-metro interchanges
    # Schema enforces from_line < to_line (lexicographic), so sort before inserting.
    transfer_rows = []
    for s in data:
        if s["is_interchange_metro"]:
            lines = sorted(s["interchange_metro_lines"])
            for i in range(len(lines)):
                for j in range(i + 1, len(lines)):
                    transfer_rows.append((s["station_id"], lines[i], lines[j]))
    n = insert_many(cur, "metro_line_transfer_times", ["station_id", "from_line", "to_line"], transfer_rows)
    print(f"  metro_line_transfer_times: {n} rows")

    # NOTE: metro_rail_interchanges is seeded in seed_national_rail_stations()
    # because its FK references both metro_stations (inserted above) AND
    # national_rail_stations (not yet inserted at this point in the call order).


def seed_national_rail_stations(cur):
    data = load("national_rail_stations.json")

    # 1. national_rail_stations
    station_rows = [(s["station_id"], s["name"]) for s in data]
    n = insert_many(cur, "national_rail_stations", ["station_id", "name"], station_rows)
    print(f"  national_rail_stations:      {n} rows")

    # 2. national_rail_station_lines — one row per (station, line) pair
    line_rows = [
        (s["station_id"], line)
        for s in data
        for line in s["lines"]
    ]
    n = insert_many(cur, "national_rail_station_lines", ["station_id", "line_name"], line_rows)
    print(f"  national_rail_station_lines: {n} rows")

    # 3. metro_rail_interchanges — seeded here because metro_stations (from
    #    seed_metro_stations) and national_rail_stations (above) must both
    #    exist before these FKs can be satisfied.
    interchange_rows = [
        (s["interchange_metro_station_id"], s["station_id"])
        for s in data
        if s["is_interchange_metro"]
    ]
    n = insert_many(cur, "metro_rail_interchanges", ["metro_station_id", "rail_station_id"], interchange_rows)
    print(f"  metro_rail_interchanges:     {n} rows")


def seed_metro_schedules(cur):
    data = load("metro_schedules.json")

    # 1. metro_schedules
    # JSON "line" → schema "line_name"  (field renamed in schema)
    # JSON "destination_station_id" → schema "dest_station_id"  (abbreviated in schema)
    schedule_rows = [
        (
            s["schedule_id"],
            s["line"],                      # → line_name
            s["direction"],
            s["origin_station_id"],
            s["destination_station_id"],    # → dest_station_id
            s["first_train_time"],
            s["last_train_time"],
            s["base_fare_usd"],
            s["per_stop_rate_usd"],
            s["frequency_min"],
        )
        for s in data
    ]
    n = insert_many(
        cur, "metro_schedules",
        ["schedule_id", "line_name", "direction", "origin_station_id", "dest_station_id",
         "first_train_time", "last_train_time", "base_fare_usd", "per_stop_rate_usd", "frequency_min"],
        schedule_rows,
    )
    print(f"  metro_schedules:               {n} rows")

    # 2. metro_schedule_stops — stop_sequence is 1-indexed position in stops_in_order
    stop_rows = [
        (s["schedule_id"], station_id, seq + 1, s["travel_time_from_origin_min"][station_id])
        for s in data
        for seq, station_id in enumerate(s["stops_in_order"])
    ]
    n = insert_many(
        cur, "metro_schedule_stops",
        ["schedule_id", "station_id", "stop_sequence", "travel_time_from_origin_min"],
        stop_rows,
    )
    print(f"  metro_schedule_stops:          {n} rows")

    # 3. metro_schedule_operating_days
    day_rows = [
        (s["schedule_id"], day)
        for s in data
        for day in s["operates_on"]
    ]
    n = insert_many(
        cur, "metro_schedule_operating_days",
        ["schedule_id", "day_of_week"],
        day_rows,
    )
    print(f"  metro_schedule_operating_days: {n} rows")


def seed_national_rail_schedules(cur):
    data = load("national_rail_schedules.json")

    # 1. national_rail_schedules
    # JSON "line" → schema "line_name"  (field renamed in schema)
    schedule_rows = [
        (
            s["schedule_id"],
            s["line"],                  # → line_name
            s["service_type"],
            s["direction"],
            s["origin_station_id"],
            s["destination_station_id"],
            s["first_train_time"],
            s["last_train_time"],
            s["frequency_min"],
        )
        for s in data
    ]
    n = insert_many(
        cur, "national_rail_schedules",
        ["schedule_id", "line_name", "service_type", "direction",
         "origin_station_id", "destination_station_id",
         "first_train_time", "last_train_time", "frequency_min"],
        schedule_rows,
    )
    print(f"  national_rail_schedules:               {n} rows")

    # 2. national_rail_schedule_stops
    # Cannot use insert_many here: the table has a SERIAL PK (id), so ON CONFLICT
    # DO NOTHING without a target cannot identify which unique index to use for
    # deduplication. We specify the conflict target explicitly instead.
    # effective_from defaults to '2000-01-01' in schema; the conflict target matches
    # the unique index on (schedule_id, station_id, effective_from).
    stop_rows = [
        (s["schedule_id"], station_id, seq + 1, s["travel_time_from_origin_min"][station_id])
        for s in data
        for seq, station_id in enumerate(s["stops_in_order"])
    ]
    if stop_rows:
        execute_values(
            cur,
            "INSERT INTO national_rail_schedule_stops"
            " (schedule_id, station_id, stop_order, travel_time_from_origin_min)"
            " VALUES %s"
            " ON CONFLICT (schedule_id, station_id, effective_from) DO NOTHING",
            stop_rows,
        )
    n = cur.rowcount if stop_rows else 0
    print(f"  national_rail_schedule_stops:          {n} rows")

    # 3. national_rail_schedule_operating_days
    day_rows = [
        (s["schedule_id"], day)
        for s in data
        for day in s["operates_on"]
    ]
    n = insert_many(
        cur, "national_rail_schedule_operating_days",
        ["schedule_id", "day_of_week"],
        day_rows,
    )
    print(f"  national_rail_schedule_operating_days: {n} rows")

    # 4. national_rail_schedule_fares — one row per (schedule, fare_class)
    # JSON "fare_classes" is a dict: { "standard": {...}, "first": {...} }
    fare_rows = [
        (s["schedule_id"], fare_class, fares["base_fare_usd"], fares["per_stop_rate_usd"])
        for s in data
        for fare_class, fares in s["fare_classes"].items()
    ]
    n = insert_many(
        cur, "national_rail_schedule_fares",
        ["schedule_id", "fare_class", "base_fare_usd", "per_stop_rate_usd"],
        fare_rows,
    )
    print(f"  national_rail_schedule_fares:          {n} rows")


def seed_seat_layouts(cur):
    data = load("national_rail_seat_layouts.json")

    # 1. national_rail_seat_layouts — VARCHAR PK, insert_many works fine
    layout_rows = [(lay["layout_id"], lay["schedule_id"]) for lay in data]
    n = insert_many(cur, "national_rail_seat_layouts", ["layout_id", "schedule_id"], layout_rows)
    print(f"  national_rail_seat_layouts: {n} rows")

    # 2. national_rail_coaches — BIGSERIAL PK; use explicit conflict target
    # (layout_id, coach_name) is the unique business key defined in the schema.
    coach_rows = [
        (lay["layout_id"], c["coach"], c["fare_class"])
        for lay in data
        for c in lay["coaches"]
    ]
    if coach_rows:
        execute_values(
            cur,
            "INSERT INTO national_rail_coaches (layout_id, coach_name, fare_class)"
            " VALUES %s"
            " ON CONFLICT (layout_id, coach_name) DO NOTHING",
            coach_rows,
        )
    n = cur.rowcount if coach_rows else 0
    print(f"  national_rail_coaches:      {n} rows")

    # Query back the DB-generated coach_ids — needed as FK for seat rows.
    # This SELECT works whether rows were just inserted or already existed.
    layout_ids = [lay["layout_id"] for lay in data]
    cur.execute(
        "SELECT coach_id, layout_id, coach_name"
        " FROM national_rail_coaches WHERE layout_id = ANY(%s)",
        (layout_ids,),
    )
    coach_id_map = {(row[1], row[2]): row[0] for row in cur.fetchall()}

    # Verify every expected (layout_id, coach_name) pair is in the map before
    # building seat_rows. A missing key means the coach INSERT silently failed
    # (e.g. a CHECK constraint violation); fail fast with a clear message
    # instead of letting a KeyError surface from inside the list comprehension.
    missing = [
        (lay["layout_id"], c["coach"])
        for lay in data
        for c in lay["coaches"]
        if (lay["layout_id"], c["coach"]) not in coach_id_map
    ]
    if missing:
        raise RuntimeError(
            f"seed_seat_layouts: {len(missing)} coach(es) not found in DB after INSERT. "
            f"Missing (layout_id, coach_name): {missing}. "
            f"The INSERT may have been rejected by a CHECK constraint (fare_class), "
            f"a UNIQUE constraint, or a FK violation on layout_id. "
            f"Check the psycopg2 warning log or query national_rail_coaches directly."
        )

    # 3. national_rail_seats — auto-generated integer PK (seat_pk); explicit conflict target
    # (coach_id, seat_code) is the unique business key defined in the schema.
    seat_rows = [
        (coach_id_map[(lay["layout_id"], c["coach"])], seat["seat_id"], seat["row"], seat["column"])
        for lay in data
        for c in lay["coaches"]
        for seat in c["seats"]
    ]
    if seat_rows:
        execute_values(
            cur,
            "INSERT INTO national_rail_seats (coach_id, seat_code, seat_row, seat_column)"
            " VALUES %s"
            " ON CONFLICT (coach_id, seat_code) DO NOTHING",
            seat_rows,
        )
    n = cur.rowcount if seat_rows else 0
    print(f"  national_rail_seats:        {n} rows")


def seed_users(cur):
    from argon2 import PasswordHasher
    data = load("registered_users.json")
    ph = PasswordHasher()

    # 1. users — VARCHAR PK, insert_many works fine
    user_rows = [
        (u["user_id"], u["full_name"], u["email"], u["phone"],
         u["date_of_birth"], u["registered_at"], u["is_active"])
        for u in data
    ]
    n = insert_many(cur, "users",
        ["user_id", "full_name", "email", "phone",
         "date_of_birth", "registered_at", "is_active"],
        user_rows)
    print(f"  users:         {n} rows")

    # 2. user_security — Argon2id is deliberately slow (anti-brute-force).
    # Only hash passwords for users not yet in user_security to avoid
    # expensive re-hashing on every re-run.
    all_ids = [u["user_id"] for u in data]
    cur.execute(
        "SELECT user_id FROM user_security WHERE user_id = ANY(%s)",
        (all_ids,),
    )
    existing_ids = {row[0] for row in cur.fetchall()}
    new_users = [u for u in data if u["user_id"] not in existing_ids]

    if new_users:
        security_rows = [
            (u["user_id"], ph.hash(u["password"]),
             u["secret_question"], ph.hash(u["secret_answer"]))
            for u in new_users
        ]
        execute_values(
            cur,
            "INSERT INTO user_security"
            " (user_id, password_hash, secret_question, secret_answer_hash)"
            " VALUES %s"
            # new_users filter above already excludes existing rows;
            # ON CONFLICT added as a safety net against concurrent seeder runs.
            " ON CONFLICT (user_id) DO NOTHING",
            security_rows,
        )
    n = cur.rowcount if new_users else 0
    print(f"  user_security: {n} rows")


def seed_national_rail_bookings(cur):
    data = load("bookings.json")

    # 1. travel_orders — VARCHAR PK, one row per booking
    # JSON "booked_at" → schema "created_at"
    order_rows = [
        (b["booking_id"], b["user_id"], "national_rail",
         b["amount_usd"], b["status"], b["booked_at"])
        for b in data
    ]
    n = insert_many(cur, "travel_orders",
        ["order_id", "user_id", "order_type", "amount_usd", "status", "created_at"],
        order_rows)
    print(f"  travel_orders:   {n} rows")

    # 2. bookings — VARCHAR PK (same ID as travel_orders)
    # ticket_count and return_travel_date are maintained automatically by triggers.
    booking_rows = [(b["booking_id"],) for b in data]
    n = insert_many(cur, "bookings", ["booking_id"], booking_rows)
    print(f"  bookings:        {n} rows")

    # 3. booking_tickets — SERIAL PK with no unique business key.
    # ON CONFLICT cannot be used without a unique index target.
    # Idempotency is achieved by pre-filtering: only insert tickets for
    # booking_ids not yet present in booking_tickets.
    booking_ids = [b["booking_id"] for b in data]
    cur.execute(
        "SELECT DISTINCT booking_id FROM booking_tickets WHERE booking_id = ANY(%s)",
        (booking_ids,),
    )
    existing_booking_ids = {row[0] for row in cur.fetchall()}
    new_bookings = [b for b in data if b["booking_id"] not in existing_booking_ids]

    if new_bookings:
        # Build (schedule_id, coach_name, seat_code) → seat_pk lookup via 3-table JOIN.
        schedule_ids = list({b["schedule_id"] for b in new_bookings})
        cur.execute(
            "SELECT s.seat_pk, sl.schedule_id, c.coach_name, s.seat_code"
            " FROM national_rail_seats s"
            " JOIN national_rail_coaches c ON s.coach_id = c.coach_id"
            " JOIN national_rail_seat_layouts sl ON c.layout_id = sl.layout_id"
            " WHERE sl.schedule_id = ANY(%s)",
            (schedule_ids,),
        )
        seat_pk_map = {(row[1], row[2], row[3]): row[0] for row in cur.fetchall()}

        ticket_rows = []
        for b in new_bookings:
            # leg is not a field in bookings.json; derive it from ticket_type.
            # Mock data only contains outbound legs for return tickets — there are
            # no inbound records in the seed data. If an unexpected ticket_type
            # appears, raise immediately rather than silently mislabelling it.
            if b["ticket_type"] == "single":
                leg = "single"
            elif b["ticket_type"] == "return":
                leg = "outbound"  # mock data has outbound legs only; no inbound records exist
            else:
                raise ValueError(
                    f"seed_national_rail_bookings: unexpected ticket_type "
                    f"'{b['ticket_type']}' in booking {b['booking_id']}. "
                    f"Expected 'single' or 'return'."
                )
            # JSON does not include a cancelled_at field.
            # Fall back to booked_at as a substitute so the schema CHECK constraint
            # (status='cancelled' AND cancelled_at IS NOT NULL) is satisfied.
            # NOTE: this means refund calculations for seeded cancelled bookings will
            # use booked_at as the cancellation time, which may not reflect reality.
            cancelled_at = b["booked_at"] if b["status"] == "cancelled" else None
            seat_pk = seat_pk_map.get((b["schedule_id"], b["coach"], b["seat_id"]))
            ticket_rows.append((
                b["booking_id"], b["schedule_id"],
                b["origin_station_id"], b["destination_station_id"],
                seat_pk,
                b["travel_date"],
                # departure_time is provided directly in bookings.json (e.g. "07:00").
                # It records the scheduled departure time of the specific service the
                # passenger booked — not derived from national_rail_schedules here,
                # because the booking already captures the exact service selected.
                b["departure_time"],
                b["ticket_type"], b["fare_class"],
                b["coach"], b["seat_id"],   # seat_id in JSON → seat_code (denormalised)
                b["stops_travelled"], b.get("travelled_at"),
                leg, b["status"], cancelled_at,
            ))
        execute_values(
            cur,
            "INSERT INTO booking_tickets"
            " (booking_id, schedule_id, origin_station_id, destination_station_id,"
            "  seat_pk, travel_date, departure_time, ticket_type, fare_class,"
            "  coach, seat_code, stops_travelled, travelled_at, leg, status, cancelled_at)"
            " VALUES %s"
            " ON CONFLICT DO NOTHING",
            ticket_rows,
        )
    n = cur.rowcount if new_bookings else 0
    print(f"  booking_tickets: {n} rows")


def seed_metro_travels(cur):
    data = load("metro_travel_history.json")

    # Build (schedule_id, station_id) → stop_sequence map from metro_schedules.json
    # to calculate stops_travelled for day-pass trip records, which lack this value.
    schedule_data = load("metro_schedules.json")
    stop_seq_map = {
        (s["schedule_id"], station_id): seq + 1
        for s in schedule_data
        for seq, station_id in enumerate(s["stops_in_order"])
    }

    # Validate that the JSON stop data matches the database, catching any drift
    # between the JSON source and what was actually seeded into metro_schedule_stops.
    # NOTE: this check requires seed_metro_schedules() to have already run.
    cur.execute("SELECT schedule_id, station_id FROM metro_schedule_stops")
    db_stops   = {(row[0], row[1]) for row in cur.fetchall()}
    json_stops = set(stop_seq_map.keys())
    if json_stops != db_stops:
        missing_in_db   = json_stops - db_stops
        missing_in_json = db_stops - json_stops
        raise RuntimeError(
            f"metro_schedules.json and metro_schedule_stops table are out of sync!\n"
            f"  In JSON but not in DB:  {missing_in_db}\n"
            f"  In DB but not in JSON:  {missing_in_json}"
        )

    # Split records by day_pass_ref:
    #   None  → purchase record (single ticket or day pass purchase) → travel_orders + metro_trip_purchases
    #   set   → individual journey under a day pass                  → metro_day_pass_trips only
    purchases      = [t for t in data if t.get("day_pass_ref") is None]
    day_pass_trips = [t for t in data if t.get("day_pass_ref") is not None]

    # 1. travel_orders — one row per purchase event (single or day_pass)
    # JSON "purchased_at" → schema "created_at"
    order_rows = [
        (t["trip_id"], t["user_id"], "metro", t["amount_usd"], t["status"], t["purchased_at"])
        for t in purchases
    ]
    n = insert_many(cur, "travel_orders",
        ["order_id", "user_id", "order_type", "amount_usd", "status", "created_at"],
        order_rows)
    print(f"  travel_orders (metro):  {n} rows")

    # 2. metro_trip_purchases — one row per purchase event
    purchase_rows = [
        (t["trip_id"], t["schedule_id"],
         t["origin_station_id"], t["destination_station_id"],
         t["travel_date"], t["ticket_type"], t.get("stops_travelled"),
         t["purchased_at"], t.get("travelled_at"),
         # cancelled_at: JSON source omits this field entirely.
         # For cancelled records, purchased_at is used as a conservative proxy
         # (earliest possible cancellation time) so the column is not left NULL.
         # This is a seed-data approximation; execute_cancellation() records the
         # real timestamp at runtime.
         #
         # Schema constraint (chk_metro_cancelled_at):
         #   cancelled_at IS NULL OR travelled_at IS NULL
         # Cancelled records always have travelled_at=NULL in mock data, so setting
         # cancelled_at here is a data quality choice, NOT a constraint workaround.
         t["purchased_at"] if t["status"] == "cancelled" else None)
        for t in purchases
    ]
    n = insert_many(cur, "metro_trip_purchases",
        ["purchase_id", "schedule_id", "origin_station_id", "destination_station_id",
         "travel_date", "ticket_type", "stops_travelled",
         "purchased_at", "travelled_at", "cancelled_at"],
        purchase_rows)
    print(f"  metro_trip_purchases:   {n} rows")

    # 3. metro_day_pass_trips — individual journeys made under a day pass
    # stops_travelled is computed from stop_seq_map (JSON source of truth).
    
    # calc_stops derives stops_travelled for day-pass journeys from the schedule
    # stop-sequence map. For cross-line trips where origin or destination does not
    # appear in the referenced schedule (e.g. MT021: schedule MS_SCH04 covers M4,
    # but destination MS14 is on M4 via a different segment not in stop_seq_map),
    # both lookups cannot succeed simultaneously. In those cases the function
    # returns 0 as a sentinel value meaning "cross-line trip, distance unknown".
    # NOTE: stops_travelled=0 satisfies the schema CHECK (stops_travelled >= 0)
    # and is valid for day-pass records, but must NOT be interpreted as a genuine
    # zero-stop journey — it signals missing route data in the seed source.
    def calc_stops(t):
        origin_seq = stop_seq_map.get((t["schedule_id"], t["origin_station_id"]))
        dest_seq   = stop_seq_map.get((t["schedule_id"], t["destination_station_id"]))
        return abs(dest_seq - origin_seq) if origin_seq and dest_seq else 0

    day_trip_rows = [
        (t["trip_id"], t["day_pass_ref"], t["schedule_id"],
         t["origin_station_id"], t["destination_station_id"],
         calc_stops(t), t["travelled_at"])
        for t in day_pass_trips
    ]
    n = insert_many(cur, "metro_day_pass_trips",
        ["trip_id", "purchase_id", "schedule_id",
         "origin_station_id", "destination_station_id",
         "stops_travelled", "travelled_at"],
        day_trip_rows)
    print(f"  metro_day_pass_trips:   {n} rows")


def seed_payments(cur):
    data = load("payments.json")

    # 1. payments — VARCHAR PK, straightforward
    payment_rows = [
        (p["payment_id"], p["amount_usd"], p["method"], p["status"], p["paid_at"])
        for p in data
    ]
    n = insert_many(cur, "payments",
        ["payment_id", "amount_usd", "method", "status", "paid_at"],
        payment_rows)
    print(f"  payments:        {n} rows")

    # 2. payment_sources — route each payment to its order source.
    # JSON "booking_id" serves as both national rail and metro order IDs;
    # the prefix determines source_type and which FK column is populated:
    #   "BK…" → source_type='national_rail_booking', national_rail_booking_id set
    #   "MT…" → source_type='metro_trip',            metro_trip_id set
    # The schema CHECK constraint enforces that exactly one FK column is non-NULL.
    source_rows = []
    for p in data:
        bid = p["booking_id"]
        if bid.startswith("BK"):
            source_rows.append((p["payment_id"], "national_rail_booking", bid, None))
        else:
            source_rows.append((p["payment_id"], "metro_trip", None, bid))
    n = insert_many(cur, "payment_sources",
        ["payment_id", "source_type", "national_rail_booking_id", "metro_trip_id"],
        source_rows)
    print(f"  payment_sources: {n} rows")


def seed_feedback(cur):
    data = load("feedback.json")

    # 1. customer_feedback — VARCHAR PK
    # JSON "booking_id" → schema "order_id"  (FK to travel_orders)
    # JSON "user_id" is intentionally omitted: schema stores it only in travel_orders,
    # so it is retrieved via JOIN when needed rather than being stored twice.
    feedback_rows = [
        (f["feedback_id"], f["booking_id"], f["rating"], f["submitted_at"])
        for f in data
    ]
    n = insert_many(cur, "customer_feedback",
        ["feedback_id", "order_id", "rating", "submitted_at"],
        feedback_rows)
    print(f"  customer_feedback: {n} rows")

    # 2. feedback_comments — only insert rows where comment is not null.
    # Stored in a separate 1:1 table to keep TEXT out of customer_feedback
    # and avoid penalizing rating-only queries with large column scans.
    comment_rows = [
        (f["feedback_id"], f["comment"])
        for f in data if f["comment"] is not None
    ]
    n = insert_many(cur, "feedback_comments",
        ["feedback_id", "comment_text"],
        comment_rows)
    print(f"  feedback_comments: {n} rows")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("Connecting to PostgreSQL...")
    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    try:
        print("Seeding tables (dependency order):")
        seed_metro_stations(cur)
        seed_national_rail_stations(cur)
        seed_metro_schedules(cur)
        seed_national_rail_schedules(cur)
        seed_seat_layouts(cur)
        seed_users(cur)
        seed_national_rail_bookings(cur)
        seed_metro_travels(cur)
        seed_payments(cur)
        seed_feedback(cur)
        conn.commit()
        print("\nAll done. Database seeded successfully.")
    except Exception as e:
        conn.rollback()
        print(f"\nError: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
