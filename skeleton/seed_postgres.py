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
import uuid

import psycopg2
from psycopg2.extras import execute_values

# ── resolve paths ────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR    = os.path.join(PROJECT_DIR, "train-mock-data")

sys.path.insert(0, PROJECT_DIR)
from skeleton import config as cfg

# Fixed namespace for deriving deterministic UUIDs from JSON business IDs.
# uuid5 + fixed namespace guarantees the same UUID for the same business_id
# on every re-run, enabling ON CONFLICT (pk) DO NOTHING for UUID-PK tables
# that have no other unique business-key column.
_SEED_NAMESPACE = uuid.UUID("f47ac10b-58cc-4372-a567-0e02b2c3d479")


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


def business_uuid(business_id: str) -> str:
    """Return a deterministic UUID derived from a JSON business ID string.

    uuid5 with a fixed namespace always produces the same UUID for the same
    input, so this can be passed as an explicit UUID PK value and
    ON CONFLICT (pk) DO NOTHING gives clean idempotency without a separate
    business_id column in the schema.
    """
    return str(uuid.uuid5(_SEED_NAMESPACE, business_id))


# ── seeders ──────────────────────────────────────────────────────────────────

def seed_metro_stations(cur):
    data = load("metro_stations.json")

    # 1. metro_stations — SERIAL PK: do not pass station_id; let DB generate it.
    # Idempotency: station name is unique in mock data; use it as the conflict proxy.
    names = [s["name"] for s in data]
    cur.execute("SELECT name FROM metro_stations WHERE name = ANY(%s)", (names,))
    existing_names = {row[0] for row in cur.fetchall()}
    new_stations = [s for s in data if s["name"] not in existing_names]
    if new_stations:
        execute_values(
            cur,
            "INSERT INTO metro_stations (name) VALUES %s",
            [(s["name"],) for s in new_stations],
        )
    print(f"  metro_stations:            {len(new_stations)} rows")

    # SELECT all to build name → DB station_id, then remap JSON business_id → DB int.
    cur.execute(
        "SELECT station_id, name FROM metro_stations WHERE name = ANY(%s)", (names,)
    )
    name_to_db = {row[1]: row[0] for row in cur.fetchall()}
    metro_station_map = {}  # "MS01" → 1
    for s in data:
        db_id = name_to_db.get(s["name"])
        if db_id is None:
            raise RuntimeError(f"metro_stations: '{s['name']}' not found after INSERT")
        metro_station_map[s["station_id"]] = db_id

    # 2. metro_station_lines — composite PK (station_id, line_name); ON CONFLICT DO NOTHING safe
    line_rows = [
        (metro_station_map[s["station_id"]], line)
        for s in data
        for line in s["lines"]
    ]
    n = insert_many(cur, "metro_station_lines", ["station_id", "line_name"], line_rows)
    print(f"  metro_station_lines:       {n} rows")

    # 3. metro_line_transfer_times — composite PK; ON CONFLICT DO NOTHING safe.
    # Schema enforces from_line < to_line (lexicographic), so sort before inserting.
    transfer_rows = []
    for s in data:
        if s["is_interchange_metro"]:
            lines = sorted(s["interchange_metro_lines"])
            for i in range(len(lines)):
                for j in range(i + 1, len(lines)):
                    transfer_rows.append(
                        (metro_station_map[s["station_id"]], lines[i], lines[j])
                    )
    n = insert_many(
        cur, "metro_line_transfer_times", ["station_id", "from_line", "to_line"],
        transfer_rows,
    )
    print(f"  metro_line_transfer_times: {n} rows")

    # NOTE: metro_rail_interchanges is seeded in seed_national_rail_stations()
    # because its FK references both metro_stations (inserted above) AND
    # national_rail_stations (not yet inserted at this point in the call order).

    return metro_station_map


def seed_national_rail_stations(cur, metro_station_map):
    data = load("national_rail_stations.json")

    # 1. national_rail_stations — SERIAL PK: same idempotency approach as metro_stations.
    names = [s["name"] for s in data]
    cur.execute("SELECT name FROM national_rail_stations WHERE name = ANY(%s)", (names,))
    existing_names = {row[0] for row in cur.fetchall()}
    new_stations = [s for s in data if s["name"] not in existing_names]
    if new_stations:
        execute_values(
            cur,
            "INSERT INTO national_rail_stations (name) VALUES %s",
            [(s["name"],) for s in new_stations],
        )
    print(f"  national_rail_stations:      {len(new_stations)} rows")

    # SELECT back to build "NR01" → DB int mapping via name.
    cur.execute(
        "SELECT station_id, name FROM national_rail_stations WHERE name = ANY(%s)", (names,)
    )
    name_to_db = {row[1]: row[0] for row in cur.fetchall()}
    rail_station_map = {}  # "NR01" → 1
    for s in data:
        db_id = name_to_db.get(s["name"])
        if db_id is None:
            raise RuntimeError(f"national_rail_stations: '{s['name']}' not found after INSERT")
        rail_station_map[s["station_id"]] = db_id

    # 2. national_rail_station_lines — composite PK; ON CONFLICT DO NOTHING safe
    line_rows = [
        (rail_station_map[s["station_id"]], line)
        for s in data
        for line in s["lines"]
    ]
    n = insert_many(
        cur, "national_rail_station_lines", ["station_id", "line_name"], line_rows
    )
    print(f"  national_rail_station_lines: {n} rows")

    # 3. metro_rail_interchanges — seeded here because metro_stations (from
    #    seed_metro_stations) and national_rail_stations (above) must both exist
    #    before these INT FKs can be satisfied.
    interchange_rows = [
        (
            metro_station_map[s["interchange_metro_station_id"]],
            rail_station_map[s["station_id"]],
        )
        for s in data
        if s["is_interchange_metro"]
    ]
    n = insert_many(
        cur, "metro_rail_interchanges",
        ["metro_station_id", "rail_station_id"], interchange_rows,
    )
    print(f"  metro_rail_interchanges:     {n} rows")

    return rail_station_map


def seed_metro_schedules(cur, metro_station_map):
    data = load("metro_schedules.json")

    # 1. metro_schedules — SERIAL PK: use (line_name, direction) as uniqueness proxy.
    # This combination is unique across all metro schedules in mock data.
    cur.execute("SELECT line_name, direction FROM metro_schedules")
    existing_keys = {(row[0], row[1]) for row in cur.fetchall()}
    new_schedules = [
        s for s in data if (s["line"], s["direction"]) not in existing_keys
    ]
    if new_schedules:
        execute_values(
            cur,
            "INSERT INTO metro_schedules"
            " (json_id, line_name, direction, origin_station_id, dest_station_id,"
            "  first_train_time, last_train_time, base_fare_usd, per_stop_rate_usd, frequency_min)"
            " VALUES %s",
            [
                (
                    s["schedule_id"],
                    s["line"],
                    s["direction"],
                    metro_station_map[s["origin_station_id"]],
                    metro_station_map[s["destination_station_id"]],
                    s["first_train_time"],
                    s["last_train_time"],
                    s["base_fare_usd"],
                    s["per_stop_rate_usd"],
                    s["frequency_min"],
                )
                for s in new_schedules
            ],
        )
    print(f"  metro_schedules:               {len(new_schedules)} rows")

    # SELECT all to build (line_name, direction) → DB schedule_id, then map JSON ID → DB int.
    cur.execute("SELECT schedule_id, line_name, direction FROM metro_schedules")
    key_to_db = {(row[1], row[2]): row[0] for row in cur.fetchall()}
    metro_schedule_map = {}  # "MS_SCH01" → 1
    for s in data:
        db_id = key_to_db.get((s["line"], s["direction"]))
        if db_id is None:
            raise RuntimeError(
                f"metro_schedules: ({s['line']}, {s['direction']}) not found after INSERT"
            )
        metro_schedule_map[s["schedule_id"]] = db_id

    # 2. metro_schedule_stops — composite PK (schedule_id, station_id); ON CONFLICT DO NOTHING safe.
    # Both FKs are now INT; convert using the maps built above.
    stop_rows = [
        (
            metro_schedule_map[s["schedule_id"]],
            metro_station_map[station_id],
            seq + 1,
            s["travel_time_from_origin_min"][station_id],
        )
        for s in data
        for seq, station_id in enumerate(s["stops_in_order"])
    ]
    n = insert_many(
        cur, "metro_schedule_stops",
        ["schedule_id", "station_id", "stop_sequence", "travel_time_from_origin_min"],
        stop_rows,
    )
    print(f"  metro_schedule_stops:          {n} rows")

    # 3. metro_schedule_operating_days — composite PK; ON CONFLICT DO NOTHING safe
    day_rows = [
        (metro_schedule_map[s["schedule_id"]], day)
        for s in data
        for day in s["operates_on"]
    ]
    n = insert_many(
        cur, "metro_schedule_operating_days", ["schedule_id", "day_of_week"], day_rows
    )
    print(f"  metro_schedule_operating_days: {n} rows")

    return metro_schedule_map


def seed_national_rail_schedules(cur, rail_station_map):
    data = load("national_rail_schedules.json")

    # 1. national_rail_schedules — SERIAL PK: use (line_name, direction, service_type) as proxy.
    cur.execute(
        "SELECT line_name, direction, service_type FROM national_rail_schedules"
    )
    existing_keys = {(row[0], row[1], row[2]) for row in cur.fetchall()}
    new_schedules = [
        s for s in data
        if (s["line"], s["direction"], s["service_type"]) not in existing_keys
    ]
    if new_schedules:
        execute_values(
            cur,
            "INSERT INTO national_rail_schedules"
            " (json_id, line_name, service_type, direction, origin_station_id, destination_station_id,"
            "  first_train_time, last_train_time, frequency_min)"
            " VALUES %s",
            [
                (
                    s["schedule_id"],
                    s["line"],
                    s["service_type"],
                    s["direction"],
                    rail_station_map[s["origin_station_id"]],
                    rail_station_map[s["destination_station_id"]],
                    s["first_train_time"],
                    s["last_train_time"],
                    s["frequency_min"],
                )
                for s in new_schedules
            ],
        )
    print(f"  national_rail_schedules:               {len(new_schedules)} rows")

    # SELECT back to build "NR_SCH01" → DB int mapping.
    cur.execute(
        "SELECT schedule_id, line_name, direction, service_type FROM national_rail_schedules"
    )
    key_to_db = {(row[1], row[2], row[3]): row[0] for row in cur.fetchall()}
    rail_schedule_map = {}  # "NR_SCH01" → 1
    for s in data:
        db_id = key_to_db.get((s["line"], s["direction"], s["service_type"]))
        if db_id is None:
            raise RuntimeError(
                f"national_rail_schedules: ({s['line']}, {s['direction']}, "
                f"{s['service_type']}) not found after INSERT"
            )
        rail_schedule_map[s["schedule_id"]] = db_id

    # 2. national_rail_schedule_stops — SERIAL PK `id`; conflict target is the unique
    # index on (schedule_id, station_id, effective_from). Both IDs are now INT FKs.
    # effective_from defaults to '2000-01-01' in schema; the conflict target matches
    # the unique index on (schedule_id, station_id, effective_from).
    stop_rows = [
        (
            rail_schedule_map[s["schedule_id"]],
            rail_station_map[station_id],
            seq + 1,
            s["travel_time_from_origin_min"][station_id],
        )
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

    # 3. national_rail_schedule_operating_days — composite PK; ON CONFLICT DO NOTHING safe.
    # schedule_id is now INT FK.
    day_rows = [
        (rail_schedule_map[s["schedule_id"]], day)
        for s in data
        for day in s["operates_on"]
    ]
    n = insert_many(
        cur, "national_rail_schedule_operating_days",
        ["schedule_id", "day_of_week"], day_rows,
    )
    print(f"  national_rail_schedule_operating_days: {n} rows")

    # 4. national_rail_schedule_fares — composite PK (schedule_id, fare_class); ON CONFLICT safe.
    # JSON uses "fare_classes" key; schedule_id is now INT FK.
    fare_rows = [
        (
            rail_schedule_map[s["schedule_id"]],
            fare_class,
            fares["base_fare_usd"],
            fares["per_stop_rate_usd"],
        )
        for s in data
        for fare_class, fares in s["fare_classes"].items()
    ]
    n = insert_many(
        cur, "national_rail_schedule_fares",
        ["schedule_id", "fare_class", "base_fare_usd", "per_stop_rate_usd"], fare_rows,
    )
    print(f"  national_rail_schedule_fares:          {n} rows")

    return rail_schedule_map


def seed_seat_layouts(cur, rail_schedule_map):
    data = load("national_rail_seat_layouts.json")

    # 1. national_rail_seat_layouts — SERIAL PK; schedule_id INT FK has UNIQUE constraint.
    # ON CONFLICT (schedule_id) DO NOTHING is safe for idempotency.
    layout_rows = [(rail_schedule_map[lay["schedule_id"]],) for lay in data]
    if layout_rows:
        execute_values(
            cur,
            "INSERT INTO national_rail_seat_layouts (schedule_id) VALUES %s"
            " ON CONFLICT (schedule_id) DO NOTHING",
            layout_rows,
        )
    n = cur.rowcount if layout_rows else 0
    print(f"  national_rail_seat_layouts: {n} rows")

    # SELECT back schedule_int_pk → layout_int_pk (needed as FK for coaches).
    schedule_int_pks = [rail_schedule_map[lay["schedule_id"]] for lay in data]
    cur.execute(
        "SELECT layout_id, schedule_id FROM national_rail_seat_layouts"
        " WHERE schedule_id = ANY(%s)",
        (schedule_int_pks,),
    )
    # seat_layout_map: DB schedule_id (int) → DB layout_id (int)
    seat_layout_map = {row[1]: row[0] for row in cur.fetchall()}

    # 2. national_rail_coaches — BIGSERIAL PK; (layout_id, coach_name) is UNIQUE.
    coach_rows = [
        (
            seat_layout_map[rail_schedule_map[lay["schedule_id"]]],
            c["coach"],
            c["fare_class"],
        )
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

    # Query back DB-generated coach_ids — needed as FK for seat rows.
    # This SELECT works whether rows were just inserted or already existed.
    layout_int_pks = list(seat_layout_map.values())
    cur.execute(
        "SELECT coach_id, layout_id, coach_name"
        " FROM national_rail_coaches WHERE layout_id = ANY(%s)",
        (layout_int_pks,),
    )
    # coach_id_map: (DB layout_id int, coach_name str) → coach_id bigint
    coach_id_map = {(row[1], row[2]): row[0] for row in cur.fetchall()}

    # Verify every expected (layout_id, coach_name) pair is in the map before
    # building seat_rows. A missing key means the coach INSERT silently failed
    # (e.g. a CHECK constraint violation); fail fast with a clear message
    # instead of letting a KeyError surface from inside the list comprehension.
    missing = [
        (seat_layout_map[rail_schedule_map[lay["schedule_id"]]], c["coach"])
        for lay in data
        for c in lay["coaches"]
        if (seat_layout_map[rail_schedule_map[lay["schedule_id"]]], c["coach"])
        not in coach_id_map
    ]
    if missing:
        raise RuntimeError(
            f"seed_seat_layouts: {len(missing)} coach(es) not found in DB after INSERT. "
            f"Missing (layout_id, coach_name): {missing}. "
            f"The INSERT may have been rejected by a CHECK constraint (fare_class), "
            f"a UNIQUE constraint, or a FK violation on layout_id. "
            f"Check the psycopg2 warning log or query national_rail_coaches directly."
        )

    # 3. national_rail_seats — BIGSERIAL PK; (coach_id, seat_code) is UNIQUE.
    seat_rows = [
        (
            coach_id_map[(seat_layout_map[rail_schedule_map[lay["schedule_id"]]], c["coach"])],
            seat["seat_id"],
            seat["row"],
            seat["column"],
        )
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

    # Build seat_pk_map for use in seed_national_rail_bookings.
    # Key: (DB schedule_id int, coach_name str, seat_code str) → seat_pk bigint.
    cur.execute(
        "SELECT s.seat_pk, sl.schedule_id, c.coach_name, s.seat_code"
        " FROM national_rail_seats s"
        " JOIN national_rail_coaches c ON s.coach_id = c.coach_id"
        " JOIN national_rail_seat_layouts sl ON c.layout_id = sl.layout_id"
        " WHERE sl.schedule_id = ANY(%s)",
        (schedule_int_pks,),
    )
    seat_pk_map = {(row[1], row[2], row[3]): row[0] for row in cur.fetchall()}

    return seat_pk_map


def seed_users(cur):
    from argon2 import PasswordHasher
    data = load("registered_users.json")
    ph = PasswordHasher()

    # 1. users — UUID PK. Pass a deterministic UUID derived from the JSON user_id
    # so ON CONFLICT (user_id) DO NOTHING gives clean idempotency without a
    # separate business_id column. The DEFAULT gen_random_uuid() is bypassed when
    # a value is supplied explicitly, which is intentional here.
    user_rows = [
        (
            business_uuid(u["user_id"]),  # "RU01" → fixed UUID, same on every run
            u["full_name"], u["email"], u["phone"],
            u["date_of_birth"], u["registered_at"], u["is_active"],
        )
        for u in data
    ]
    if user_rows:
        execute_values(
            cur,
            "INSERT INTO users"
            " (user_id, full_name, email, phone, date_of_birth, registered_at, is_active)"
            " VALUES %s"
            " ON CONFLICT (user_id) DO NOTHING",
            user_rows,
        )
    n = cur.rowcount
    print(f"  users:         {n} rows")

    # user_map: "RU01" → UUID string. Deterministic — no SELECT needed.
    user_map = {u["user_id"]: business_uuid(u["user_id"]) for u in data}

    # 2. user_security — UUID PK (= user_id FK). Argon2id is deliberately slow (anti-brute-force).
    # Only hash passwords for users not yet in user_security to avoid
    # expensive re-hashing on every re-run.
    all_uuids = list(user_map.values())
    cur.execute(
        "SELECT user_id::text FROM user_security WHERE user_id = ANY(%s::uuid[])",
        (all_uuids,),
    )
    existing_uuids = {row[0] for row in cur.fetchall()}
    new_users = [u for u in data if user_map[u["user_id"]] not in existing_uuids]

    if new_users:
        security_rows = [
            (
                user_map[u["user_id"]],
                ph.hash(u["password"]),
                u["secret_question"],
                ph.hash(u["secret_answer"]),
            )
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

    return user_map


def seed_national_rail_bookings(cur, user_map, rail_schedule_map, rail_station_map, seat_pk_map):
    data = load("bookings.json")

    # 1. travel_orders — UUID PK. Derive deterministic UUID from JSON booking_id ("BK001")
    # so ON CONFLICT (order_id) DO NOTHING gives idempotency without a business_id column.
    # JSON "booked_at" → schema "created_at"
    order_rows = [
        (
            business_uuid(b["booking_id"]),  # "BK001" → fixed UUID
            user_map[b["user_id"]],
            "national_rail",
            b["amount_usd"], b["status"], b["booked_at"],
        )
        for b in data
    ]
    if order_rows:
        execute_values(
            cur,
            "INSERT INTO travel_orders"
            " (order_id, user_id, order_type, amount_usd, status, created_at)"
            " VALUES %s"
            " ON CONFLICT (order_id) DO NOTHING",
            order_rows,
        )
    n = cur.rowcount
    print(f"  travel_orders:   {n} rows")

    # booking_map: "BK001" → UUID string. Deterministic — no SELECT needed.
    booking_map = {b["booking_id"]: business_uuid(b["booking_id"]) for b in data}

    # 2. bookings — UUID PK (= booking_id FK to travel_orders)
    booking_rows = [(booking_map[b["booking_id"]],) for b in data]
    if booking_rows:
        execute_values(
            cur,
            "INSERT INTO bookings (booking_id) VALUES %s"
            " ON CONFLICT (booking_id) DO NOTHING",
            booking_rows,
        )
    n = cur.rowcount
    print(f"  bookings:        {n} rows")

    # 3. booking_tickets — SERIAL PK with no unique business key.
    # ON CONFLICT cannot be used without a unique index target.
    # Idempotency is achieved by pre-filtering: only insert tickets for
    # booking_ids not yet present in booking_tickets.
    all_booking_uuids = list(booking_map.values())
    cur.execute(
        "SELECT DISTINCT booking_id::text FROM booking_tickets WHERE booking_id = ANY(%s::uuid[])",
        (all_booking_uuids,),
    )
    existing_booking_uuids = {row[0] for row in cur.fetchall()}
    new_bookings = [
        b for b in data if booking_map[b["booking_id"]] not in existing_booking_uuids
    ]

    if new_bookings:
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
            schedule_int = rail_schedule_map[b["schedule_id"]]
            seat_pk = seat_pk_map.get((schedule_int, b["coach"], b["seat_id"]))
            ticket_rows.append((
                booking_map[b["booking_id"]],          # UUID
                schedule_int,                           # INT
                rail_station_map[b["origin_station_id"]],       # INT
                rail_station_map[b["destination_station_id"]],  # INT
                seat_pk,                                # BIGINT or None
                b["travel_date"],
                # departure_time is provided directly in bookings.json (e.g. "07:00").
                # It records the scheduled departure time of the specific service the
                # passenger booked — not derived from national_rail_schedules here,
                # because the booking already captures the exact service selected.
                b["departure_time"],
                b["ticket_type"], b["fare_class"],
                b["coach"], b["seat_id"],  # seat_id in JSON → seat_code (denormalised)
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

    return booking_map


def seed_metro_travels(cur, user_map, metro_schedule_map, metro_station_map):
    data = load("metro_travel_history.json")

    # Build (json_schedule_id, json_station_id) → stop_sequence map from JSON source.
    # Used to calculate stops_travelled for day-pass trip records, which lack this value.
    schedule_data = load("metro_schedules.json")
    stop_seq_map = {
        (s["schedule_id"], station_id): seq + 1
        for s in schedule_data
        for seq, station_id in enumerate(s["stops_in_order"])
    }

    # Validate that the JSON stop data (converted to DB int keys) matches what was
    # actually seeded into metro_schedule_stops, catching drift between JSON and DB.
    # NOTE: this check requires seed_metro_schedules() to have already run.
    json_stops_as_ints = {
        (metro_schedule_map[sch_id], metro_station_map[sta_id])
        for (sch_id, sta_id) in stop_seq_map.keys()
    }
    cur.execute("SELECT schedule_id, station_id FROM metro_schedule_stops")
    db_stops = {(row[0], row[1]) for row in cur.fetchall()}
    if json_stops_as_ints != db_stops:
        missing_in_db   = json_stops_as_ints - db_stops
        missing_in_json = db_stops - json_stops_as_ints
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

    # 1. travel_orders — one row per purchase event (single or day_pass).
    # Derive deterministic UUID from JSON trip_id ("MT001") for idempotency.
    # JSON "purchased_at" → schema "created_at"
    order_rows = [
        (
            business_uuid(t["trip_id"]),  # "MT001" → fixed UUID
            user_map[t["user_id"]],
            "metro",
            t["amount_usd"], t["status"], t["purchased_at"],
        )
        for t in purchases
    ]
    if order_rows:
        execute_values(
            cur,
            "INSERT INTO travel_orders"
            " (order_id, user_id, order_type, amount_usd, status, created_at)"
            " VALUES %s"
            " ON CONFLICT (order_id) DO NOTHING",
            order_rows,
        )
    n = cur.rowcount
    print(f"  travel_orders (metro):  {n} rows")

    # purchase_map: "MT001" → UUID string. Deterministic — no SELECT needed.
    purchase_map = {t["trip_id"]: business_uuid(t["trip_id"]) for t in purchases}

    # 2. metro_trip_purchases — one row per purchase event.
    # purchase_id UUID = same deterministic UUID as travel_orders.order_id.
    purchase_rows = [
        (
            purchase_map[t["trip_id"]],                        # UUID (= travel_orders.order_id)
            metro_schedule_map[t["schedule_id"]],              # INT FK
            metro_station_map[t["origin_station_id"]],         # INT FK
            metro_station_map[t["destination_station_id"]],    # INT FK
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
            t["purchased_at"] if t["status"] == "cancelled" else None,
        )
        for t in purchases
    ]
    if purchase_rows:
        execute_values(
            cur,
            "INSERT INTO metro_trip_purchases"
            " (purchase_id, schedule_id, origin_station_id, destination_station_id,"
            "  travel_date, ticket_type, stops_travelled,"
            "  purchased_at, travelled_at, cancelled_at)"
            " VALUES %s"
            " ON CONFLICT (purchase_id) DO NOTHING",
            purchase_rows,
        )
    n = cur.rowcount
    print(f"  metro_trip_purchases:   {n} rows")

    # 3. metro_day_pass_trips — individual journeys made under a day pass.
    # trip_id UUID: derive deterministically from JSON trip_id ("MT021") for idempotency.
    # stops_travelled is computed from stop_seq_map (JSON source of truth).

    # calc_stops derives stops_travelled for day-pass journeys from the schedule
    # stop-sequence map. For cross-line trips where origin or destination does not
    # appear in the referenced schedule, both lookups cannot succeed simultaneously.
    # In those cases the function returns 0 as a sentinel value meaning
    # "cross-line trip, distance unknown".
    # NOTE: stops_travelled=0 satisfies the schema CHECK (stops_travelled >= 0)
    # and is valid for day-pass records, but must NOT be interpreted as a genuine
    # zero-stop journey — it signals missing route data in the seed source.
    def calc_stops(t):
        origin_seq = stop_seq_map.get((t["schedule_id"], t["origin_station_id"]))
        dest_seq   = stop_seq_map.get((t["schedule_id"], t["destination_station_id"]))
        return abs(dest_seq - origin_seq) if origin_seq and dest_seq else 0

    day_trip_rows = [
        (
            business_uuid(t["trip_id"]),                       # UUID PK (deterministic)
            purchase_map[t["day_pass_ref"]],                   # UUID FK to metro_trip_purchases
            metro_schedule_map[t["schedule_id"]],              # INT FK
            metro_station_map[t["origin_station_id"]],         # INT FK
            metro_station_map[t["destination_station_id"]],    # INT FK
            calc_stops(t),
            t["travelled_at"],
        )
        for t in day_pass_trips
    ]
    if day_trip_rows:
        execute_values(
            cur,
            "INSERT INTO metro_day_pass_trips"
            " (trip_id, purchase_id, schedule_id,"
            "  origin_station_id, destination_station_id,"
            "  stops_travelled, travelled_at)"
            " VALUES %s"
            " ON CONFLICT (trip_id) DO NOTHING",
            day_trip_rows,
        )
    n = cur.rowcount if day_trip_rows else 0
    print(f"  metro_day_pass_trips:   {n} rows")

    return purchase_map


def seed_payments(cur, booking_map, purchase_map):
    data = load("payments.json")

    # 1. payments — UUID PK. Derive deterministic UUID from JSON payment_id ("PM001").
    payment_rows = [
        (
            business_uuid(p["payment_id"]),  # "PM001" → fixed UUID
            p["amount_usd"], p["method"], p["status"], p["paid_at"],
        )
        for p in data
    ]
    if payment_rows:
        execute_values(
            cur,
            "INSERT INTO payments (payment_id, amount_usd, method, status, paid_at)"
            " VALUES %s"
            " ON CONFLICT (payment_id) DO NOTHING",
            payment_rows,
        )
    n = cur.rowcount
    print(f"  payments:        {n} rows")

    # payment_map: "PM001" → UUID string. Deterministic — no SELECT needed.
    payment_map = {p["payment_id"]: business_uuid(p["payment_id"]) for p in data}

    # 2. payment_sources — route each payment to its order source.
    # JSON "booking_id" serves as both national rail and metro order IDs;
    # the prefix determines source_type and which FK column is populated:
    #   "BK…" → source_type='national_rail_booking', national_rail_booking_id set (UUID)
    #   "MT…" → source_type='metro_trip',            metro_trip_id set (UUID)
    # The schema CHECK constraint enforces that exactly one FK column is non-NULL.
    source_rows = []
    for p in data:
        bid = p["booking_id"]
        if bid.startswith("BK"):
            source_rows.append((
                payment_map[p["payment_id"]],
                "national_rail_booking",
                booking_map[bid],  # UUID
                None,
            ))
        else:
            source_rows.append((
                payment_map[p["payment_id"]],
                "metro_trip",
                None,
                purchase_map[bid],  # UUID
            ))
    n = insert_many(
        cur, "payment_sources",
        ["payment_id", "source_type", "national_rail_booking_id", "metro_trip_id"],
        source_rows,
    )
    print(f"  payment_sources: {n} rows")

    return payment_map


def seed_feedback(cur, booking_map, purchase_map):
    data = load("feedback.json")

    def order_uuid(json_order_id):
        """Resolve a JSON booking/trip ID to its DB UUID."""
        if json_order_id.startswith("BK"):
            return booking_map[json_order_id]
        return purchase_map[json_order_id]

    # 1. customer_feedback — SERIAL PK; UNIQUE(order_id) allows idempotency.
    # JSON "booking_id" → schema "order_id" (FK to travel_orders).
    # JSON "user_id" is intentionally omitted: schema stores it only in travel_orders,
    # so it is retrieved via JOIN when needed rather than being stored twice.
    feedback_rows = [
        (order_uuid(f["booking_id"]), f["rating"], f["submitted_at"])
        for f in data
    ]
    if feedback_rows:
        execute_values(
            cur,
            "INSERT INTO customer_feedback (order_id, rating, submitted_at)"
            " VALUES %s"
            " ON CONFLICT (order_id) DO NOTHING",
            feedback_rows,
        )
    n = cur.rowcount
    print(f"  customer_feedback: {n} rows")

    # 2. feedback_comments — INT PK (= feedback_id FK). Only insert rows where comment is set.
    # Stored in a separate 1:1 table to keep TEXT out of customer_feedback
    # and avoid penalizing rating-only queries with large column scans.
    # SELECT back feedback_id by order_id to resolve the FK.
    comment_data = [f for f in data if f.get("comment") is not None]
    if comment_data:
        order_uuids = [order_uuid(f["booking_id"]) for f in comment_data]
        cur.execute(
            "SELECT feedback_id, order_id::text FROM customer_feedback"
            " WHERE order_id = ANY(%s::uuid[])",
            (order_uuids,),
        )
        order_to_feedback_id = {row[1]: row[0] for row in cur.fetchall()}

        comment_rows = [
            (order_to_feedback_id[order_uuid(f["booking_id"])], f["comment"])
            for f in comment_data
            if order_uuid(f["booking_id"]) in order_to_feedback_id
        ]
        n = insert_many(cur, "feedback_comments", ["feedback_id", "comment_text"], comment_rows)
    else:
        n = 0
    print(f"  feedback_comments: {n} rows")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("Connecting to PostgreSQL...")
    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    try:
        print("Seeding tables (dependency order):")
        metro_station_map  = seed_metro_stations(cur)
        rail_station_map   = seed_national_rail_stations(cur, metro_station_map)
        metro_schedule_map = seed_metro_schedules(cur, metro_station_map)
        rail_schedule_map  = seed_national_rail_schedules(cur, rail_station_map)
        seat_pk_map        = seed_seat_layouts(cur, rail_schedule_map)
        user_map           = seed_users(cur)
        booking_map        = seed_national_rail_bookings(
                                 cur, user_map, rail_schedule_map,
                                 rail_station_map, seat_pk_map)
        purchase_map       = seed_metro_travels(
                                 cur, user_map, metro_schedule_map, metro_station_map)
        seed_payments(cur, booking_map, purchase_map)
        seed_feedback(cur, booking_map, purchase_map)
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
