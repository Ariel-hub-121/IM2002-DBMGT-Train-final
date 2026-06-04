# AI Session Context — TransitFlow

**How to use this file:**
At the start of every AI coding session, paste the full contents of this file as your first message to your AI assistant. This gives the AI the context it needs to produce code that fits your codebase and is consistent with your teammates' work.

**Who maintains this file:**
Whoever makes a schema change or architectural decision updates this file in the same commit. Treat it like a team contract.

---

## Project Overview

TransitFlow is a Python-based AI chat assistant for a fictional transit operator. It queries three databases — PostgreSQL (relational + vector), Neo4j (graph) — and uses an LLM to answer user questions. Our task as students is to design the database schema and implement the query functions in `databases/relational/queries.py` and `databases/graph/queries.py`.

## Tech Stack

- Language: Python 3.11+
- Relational DB: PostgreSQL via `psycopg2` with `RealDictCursor`
- Graph DB: Neo4j via the `neo4j` Python driver
- Vector search: `pgvector` extension (already implemented — do not modify)
- Web UI: Gradio
- LLM: Google Gemini or local Ollama (configured via `.env`)

## Coding Conventions

- **Naming:** `snake_case` for all Python names and SQL identifiers
- **Docstrings:** All functions must have a docstring with `Args:` and `Returns:` sections
- **Return types:** Use type hints. Read-only functions return `list[dict]` or `Optional[dict]`
- **Empty results:** Return `[]` or `None` (as documented), never raise an exception for "not found"
- **SQL:** Use `%s` placeholders for all user inputs — never string-format into SQL
- **Relational pattern:** Use `_connect()` helper + `psycopg2.extras.RealDictCursor`:
  ``python
  with _connect() as conn:
      with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
          cur.execute("SELECT ...", (param,))
          return [dict(row) for row in cur.fetchall()]
  ``
- **Graph pattern:** Use `_driver()` helper + session:
  ``python
  with _driver() as driver:
      with driver.session() as session:
          result = session.run("MATCH ...", station_id=station_id)
          return [dict(record) for record in result]
  ``

## Agreed Relational Schema

<!-- ============================================================
  FILL THIS IN after your team completes the schema design workshop.
  Paste your final CREATE TABLE statements here.
  ============================================================ -->

``sql
-- TODO: paste your final schema.sql contents here after team review
----============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
--
--  TWO ROLES:
--    1. Relational  → dual-network transit data designed below
--    2. Vector      → policy documents for RAG (provided — do not modify)
-- ============================================================

-- ============================================================
-- PRIMARY KEY DESIGN
--
-- Two PK types are used throughout this schema:
--
-- VARCHAR(10–50): for IDs that carry business meaning.
--   These IDs (e.g. user_id='U001', schedule_id='NR1_SCH_01') originate from
--   mock data or external systems and are human-readable by design.
--   UUID is not used because this system does not require distributed uniqueness
--   and UUID adds storage overhead with no benefit at this scale.
--   SERIAL is not used for these columns because a purely numeric surrogate key
--   would lose the semantic meaning the business ID already provides.
--
-- BIGSERIAL / SERIAL: for internal records that have no natural business key
--   and require a DB-generated surrogate (e.g. seat_pk, coach_id, stop id).
--   These are referenced heavily as FKs; integer joins outperform VARCHAR joins.
--   BIGSERIAL is preferred over SERIAL where cumulative row counts could exceed
--   the INT upper bound (~2.1 billion) over the system's operational lifetime.
-- ============================================================

-- ============================================================
-- DELETE STRATEGY
--
-- A mixed strategy is applied based on data sensitivity.
-- Every FK explicitly declares its ON DELETE behaviour;
-- none rely on the database default (which is RESTRICT).
--
-- [Soft Delete]  users.is_active = FALSE
--   Orders, tickets, and payments all hold a user_id FK. Hard-deleting a user
--   would orphan all historical records. Deactivating the account preserves the
--   audit trail and satisfies financial record-keeping requirements.
--   The application layer implements "delete account" as:
--     UPDATE users SET is_active = FALSE
--
-- [ON DELETE CASCADE]  order sub-layer data
--   Applies to: travel_orders → bookings / metro_trip_purchases
--               → booking_tickets / metro_day_pass_trips
--   Child rows have no independent meaning; they should be removed when their
--   parent is hard-deleted to avoid orphan accumulation.
--   Note: the normal end-state of an order is cancellation, not hard deletion;
--   CASCADE only triggers during administrative hard-delete operations.
--
-- [ON DELETE SET NULL]  booking_tickets.seat_pk → national_rail_seats
--   Rolling stock changes may cause seats to be retired, but the ticket history
--   (travel records, refund records) must be retained. SET NULL detaches the
--   seat reference while keeping the ticket row intact.
--
-- [ON DELETE RESTRICT]  travel_orders.user_id → users
--   A user with existing orders must not be hard-deleted directly.
--   This forces the application layer to soft-delete the account first,
--   ensuring every order can always be traced back to a user.
-- ============================================================


-- ============================================================
-- USERS
-- ============================================================

CREATE TABLE "users" (
  -- VARCHAR(20) business ID (e.g. 'U001') aligned with mock data; human-readable.
  "user_id"       VARCHAR(20)  PRIMARY KEY,
  "full_name"     VARCHAR(100) NOT NULL,
  "email"         VARCHAR(100) NOT NULL UNIQUE,
  "phone"         VARCHAR(50),
  "date_of_birth" DATE,
  "registered_at" TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  -- Soft-delete flag: accounts are deactivated rather than hard-deleted.
  -- Preserves referential integrity for all historical orders and payments.
  "is_active"     BOOLEAN      NOT NULL DEFAULT TRUE
);

COMMENT ON TABLE "users" IS 'Registered passenger accounts. Delete strategy: soft delete via is_active=FALSE to preserve historical order and payment references.';

CREATE TABLE "user_security" (
  -- VARCHAR(20) shares the same type as users.user_id; serves as both PK and FK.
  -- Security-sensitive columns are isolated from the users table so that
  -- general queries (e.g. profile lookups) never expose password hashes.
  "user_id"            VARCHAR(20)  PRIMARY KEY,
  -- Password stored as an Argon2id hash.
  -- Argon2id encodes the salt, parameters, and digest in a single self-contained
  -- string (PHC format), so no separate salt column is needed.
  -- Plain text, MD5, and SHA-family hashes are explicitly forbidden.
  "password_hash"      VARCHAR(255) NOT NULL,
  "secret_question"    VARCHAR(255),
  -- Secret answer is hashed with the same Argon2id algorithm before storage.
  "secret_answer_hash" VARCHAR(255)
);

COMMENT ON TABLE "user_security" IS 'User authentication data, separated from the users table to avoid exposing hashes in general queries. Passwords and secret answers are stored as Argon2id hashes (PHC format; salt is embedded in the hash string).';


-- ============================================================
-- METRO STATIONS
-- ============================================================

CREATE TABLE "metro_stations" (
  -- VARCHAR(10) business ID (e.g. 'MS01') aligned with mock data.
  "station_id" VARCHAR(10)  PRIMARY KEY,
  "name"       VARCHAR(100) NOT NULL
);

COMMENT ON TABLE "metro_stations" IS 'Metro station master data.';

CREATE TABLE "metro_station_lines" (
  "station_id" VARCHAR(10) NOT NULL,
  "line_name"  VARCHAR(20) NOT NULL,
  -- Composite PK: (station_id, line_name) is a natural unique key; no surrogate needed.
  -- A station belonging to multiple lines implicitly means it is an intra-metro interchange;
  -- no separate same-network interchange table is required for existence checks.
  PRIMARY KEY ("station_id", "line_name")
);

COMMENT ON TABLE "metro_station_lines" IS 'Metro lines serving each station (one station may belong to multiple lines). A station on multiple lines is implicitly an intra-metro interchange.';

-- ============================================================
-- METRO INTRA-NETWORK TRANSFER TIMES
--
-- metro_station_lines records which lines a station belongs to,
-- but does not capture the walking time required to transfer between platforms.
-- This table stores that transfer time for each directed line pair at a station.
--
-- The CHECK (from_line < to_line) normalises direction so that the pair
-- (M1→M2) and (M2→M1) are stored as a single row, preventing duplicates.
-- Transfer time is assumed symmetric; the application reads this one row for
-- both directions.
--
-- Interchange stations in mock data:
--   MS01: M1 ↔ M2    MS04: M1 ↔ M3    MS08: M2 ↔ M4
--   MS12: M3 ↔ M4    MS17: M1 ↔ M4
-- ============================================================

CREATE TABLE "metro_line_transfer_times" (
  "station_id"        VARCHAR(10) NOT NULL,
  "from_line"         VARCHAR(20) NOT NULL,
  "to_line"           VARCHAR(20) NOT NULL,
  -- Walking time between platforms at this station, in minutes.
  -- Default 3 minutes; adjust per actual station layout.
  "transfer_time_min" INT         NOT NULL DEFAULT 3
    CHECK ("transfer_time_min" >= 0),
  -- Composite PK: one row per station + ordered line pair.
  PRIMARY KEY ("station_id", "from_line", "to_line"),
  -- Direction normalisation: always store the lexicographically smaller line first.
  -- Prevents storing both (M1,M2) and (M2,M1) as separate rows.
  CHECK ("from_line" < "to_line")
);

COMMENT ON TABLE  "metro_line_transfer_times"                     IS 'Walking transfer time between metro lines at interchange stations. One row per station + normalised line pair (from_line < to_line); transfer is assumed symmetric.';
COMMENT ON COLUMN "metro_line_transfer_times"."transfer_time_min" IS 'Platform-to-platform walking time in minutes. Used by the routing engine when summing total journey time across a line change.';


-- ============================================================
-- NATIONAL RAIL STATIONS
-- ============================================================

CREATE TABLE "national_rail_stations" (
  -- VARCHAR(10) business ID (e.g. 'NR01'), symmetric with metro_stations design.
  "station_id" VARCHAR(10)  PRIMARY KEY,
  "name"       VARCHAR(100) NOT NULL
);

COMMENT ON TABLE "national_rail_stations" IS 'National rail station master data.';

CREATE TABLE "national_rail_station_lines" (
  "station_id" VARCHAR(10) NOT NULL,
  "line_name"  VARCHAR(20) NOT NULL,
  -- Composite PK: natural unique key, same rationale as metro_station_lines.
  PRIMARY KEY ("station_id", "line_name")
);

COMMENT ON TABLE "national_rail_station_lines" IS 'National rail lines serving each station (one station may belong to multiple lines).';


-- ============================================================
-- CROSS-NETWORK INTERCHANGES  (metro ↔ national rail)
--
-- The only interchange type in this system is between a metro station and a
-- national rail station. Mock data contains three pairs:
--   MS01 ↔ NR01,  MS07 ↔ NR03,  MS15 ↔ NR07
--
-- Intra-metro line transfers are handled by metro_line_transfer_times above.
-- Intra-rail transfers (NR01 serves NR1+NR2) are implicit in national_rail_station_lines.
--
-- Both columns carry full DB-layer FK constraints pointing to their respective
-- station master tables, eliminating the conditional-FK problem that would arise
-- from a single generic interchange table with a network-type discriminator column.
--
-- The composite PK (metro_station_id, rail_station_id) prevents duplicate rows
-- and fixes direction (metro always first), making a separate canonical-direction
-- CHECK unnecessary.
-- ============================================================

CREATE TABLE "metro_rail_interchanges" (
  "metro_station_id"  VARCHAR(10) NOT NULL,
  "rail_station_id"   VARCHAR(10) NOT NULL,
  -- Walking time between the metro and rail platforms, in minutes.
  "transfer_time_min" INT         NOT NULL DEFAULT 5
    CHECK ("transfer_time_min" >= 0),
  PRIMARY KEY ("metro_station_id", "rail_station_id")
);

COMMENT ON TABLE  "metro_rail_interchanges"                     IS 'Metro ↔ national rail cross-network interchange pairs. Both FKs point to their respective station tables, giving full DB-layer referential integrity. Corresponds to the INTERCHANGE_TO relationship in Neo4j.';
COMMENT ON COLUMN "metro_rail_interchanges"."transfer_time_min" IS 'Walking time between metro and rail platforms in minutes. Added to total journey time by the routing engine.';


-- ============================================================
-- METRO SCHEDULES
-- ============================================================

CREATE TABLE "metro_schedules" (
  -- VARCHAR(50) business ID aligned with mock data schedule identifiers.
  "schedule_id"       VARCHAR(50)   PRIMARY KEY,
  "line_name"         VARCHAR(20)   NOT NULL,
  "direction"         VARCHAR(50)   NOT NULL,
  "origin_station_id" VARCHAR(10),
  "dest_station_id"   VARCHAR(10),
  "first_train_time"  TIME          NOT NULL,
  "last_train_time"   TIME          NOT NULL,
  -- NUMERIC(10,2) stores fares to the cent, avoiding floating-point rounding errors.
  "base_fare_usd"     NUMERIC(10,2) NOT NULL CHECK ("base_fare_usd"     >= 0),
  "per_stop_rate_usd" NUMERIC(10,2) NOT NULL CHECK ("per_stop_rate_usd" >= 0),
  "frequency_min"     INT           NOT NULL CHECK ("frequency_min"     >  0),
  CHECK ("first_train_time" < "last_train_time")
);

COMMENT ON TABLE "metro_schedules" IS 'Metro schedule definitions including fare parameters. Fare formula: total_fare = base_fare + per_stop_rate × stops_travelled.';

CREATE TABLE "metro_schedule_stops" (
  "schedule_id"                 VARCHAR(50) NOT NULL,
  "station_id"                  VARCHAR(10) NOT NULL,
  "stop_sequence"               INT         NOT NULL CHECK ("stop_sequence" > 0),
  "travel_time_from_origin_min" INT         NOT NULL CHECK ("travel_time_from_origin_min" >= 0),
  -- Composite PK: a schedule visits each station at most once.
  PRIMARY KEY ("schedule_id", "station_id"),
  -- stop_sequence must be unique within a schedule to give an unambiguous stop order.
  UNIQUE ("schedule_id", "stop_sequence")
);

COMMENT ON TABLE "metro_schedule_stops" IS 'Ordered stop list for each metro schedule. Stops are stored as individual rows (not an array column) with an explicit stop_sequence, as required by the schema normalisation rules.';

CREATE TABLE "metro_schedule_operating_days" (
  "schedule_id" VARCHAR(50) NOT NULL,
  "day_of_week" VARCHAR(3)  NOT NULL,
  PRIMARY KEY ("schedule_id", "day_of_week"),
  CHECK ("day_of_week" IN ('mon','tue','wed','thu','fri','sat','sun'))
);

COMMENT ON TABLE "metro_schedule_operating_days" IS 'Operating days for each metro schedule. Stored as individual rows to allow simple WHERE day_of_week = ? filtering.';


-- ============================================================
-- NATIONAL RAIL SCHEDULES
-- ============================================================

CREATE TABLE "national_rail_schedules" (
  -- VARCHAR(20) business ID (e.g. 'NR1_SCH_01') aligned with mock data.
  "schedule_id"            VARCHAR(20) PRIMARY KEY,
  "line_name"              VARCHAR(20) NOT NULL,
  -- service_type drives the refund policy applied at cancellation time:
  --   normal  → RF001 (standard refund window)
  --   express → RF002 (stricter refund window)
  "service_type"           VARCHAR(20) NOT NULL CHECK ("service_type" IN ('normal','express')),
  "direction"              VARCHAR(50) NOT NULL,
  "origin_station_id"      VARCHAR(10),
  "destination_station_id" VARCHAR(10),
  "first_train_time"       TIME        NOT NULL,
  "last_train_time"        TIME        NOT NULL,
  "frequency_min"          INT         NOT NULL CHECK ("frequency_min" > 0),
  CHECK ("first_train_time" < "last_train_time")
);

COMMENT ON TABLE "national_rail_schedules" IS 'National rail schedule definitions. service_type determines the refund policy (normal→RF001, express→RF002).';

CREATE TABLE "national_rail_schedule_stops" (
  -- SERIAL surrogate PK: no natural business key exists for a stop record.
  -- The same station may appear in multiple historical versions of a schedule
  -- (see effective_from / effective_to); business uniqueness is enforced by
  -- the UNIQUE indexes below, not by the PK itself.
  "id"                          SERIAL      PRIMARY KEY,
  "schedule_id"                 VARCHAR(20),
  "station_id"                  VARCHAR(10),
  "stop_order"                  INT         NOT NULL,
  "travel_time_from_origin_min" INT         NOT NULL,
  -- is_stop distinguishes a scheduled stop from a pass-through (express trains
  -- may pass a station without stopping but still need it in the sequence for
  -- travel-time calculations).
  "is_stop"                     BOOLEAN     NOT NULL DEFAULT TRUE,
  -- effective_from / effective_to support temporal changes to stop status,
  -- e.g. a station temporarily removed from service for platform works.
  -- effective_to = NULL means this record is currently active.
  "effective_from"              DATE        NOT NULL DEFAULT '2000-01-01',
  "effective_to"                DATE,

  -- Reject intervals where the end date precedes the start date.
  CONSTRAINT "chk_effective_range" CHECK (
    "effective_to" IS NULL OR "effective_from" < "effective_to"
  )
);

COMMENT ON COLUMN "national_rail_schedule_stops"."is_stop"        IS 'true = train stops; false = train passes through without stopping (travel time still counted).';
COMMENT ON COLUMN "national_rail_schedule_stops"."effective_from" IS 'Start date (inclusive) for this stop status version.';
COMMENT ON COLUMN "national_rail_schedule_stops"."effective_to"   IS 'End date (exclusive) for this stop status version. NULL means currently active.';

CREATE TABLE "national_rail_schedule_operating_days" (
  "schedule_id" VARCHAR(20) NOT NULL,
  "day_of_week" VARCHAR(3)  NOT NULL,
  PRIMARY KEY ("schedule_id", "day_of_week"),
  CHECK ("day_of_week" IN ('mon','tue','wed','thu','fri','sat','sun'))
);

COMMENT ON TABLE "national_rail_schedule_operating_days" IS 'Operating days for each national rail schedule.';

CREATE TABLE "national_rail_schedule_fares" (
  "schedule_id"       VARCHAR(20)   NOT NULL,
  "fare_class"        VARCHAR(20)   NOT NULL CHECK ("fare_class" IN ('standard','first')),
  -- NUMERIC(10,2) stores fares to the cent, avoiding floating-point rounding errors.
  "base_fare_usd"     NUMERIC(10,2) NOT NULL CHECK ("base_fare_usd"     >= 0),
  "per_stop_rate_usd" NUMERIC(10,2) NOT NULL CHECK ("per_stop_rate_usd" >= 0),
  -- Composite PK: (schedule_id, fare_class) is the natural unique key.
  PRIMARY KEY ("schedule_id", "fare_class")
);

COMMENT ON TABLE "national_rail_schedule_fares" IS 'Per-class fare parameters for each national rail schedule. Fare formula: total_fare = base_fare + per_stop_rate × stops_travelled.';


-- ============================================================
-- NATIONAL RAIL SEAT LAYOUTS
-- ============================================================

CREATE TABLE "national_rail_seat_layouts" (
  -- VARCHAR(20) business ID aligned with mock data layout identifiers.
  "layout_id"   VARCHAR(20) PRIMARY KEY,
  -- Each schedule has exactly one seat layout (enforced by UNIQUE).
  "schedule_id" VARCHAR(20) NOT NULL UNIQUE
);

COMMENT ON TABLE "national_rail_seat_layouts" IS 'Seat layout definition for each national rail schedule (1:1 relationship).';

CREATE TABLE "national_rail_coaches" (
  -- BIGSERIAL surrogate PK: coach_name is only unique within a layout,
  -- not globally. A surrogate integer is needed for the FK from national_rail_seats.
  "coach_id"   BIGSERIAL   PRIMARY KEY,
  "layout_id"  VARCHAR(20) NOT NULL,
  "coach_name" VARCHAR(10) NOT NULL,
  -- fare_class determines the ticket class required to occupy seats in this coach.
  "fare_class" VARCHAR(20) NOT NULL CHECK ("fare_class" IN ('standard','first')),
  UNIQUE ("layout_id", "coach_name")
);

COMMENT ON TABLE "national_rail_coaches" IS 'Coach definitions within a seat layout. fare_class links to national_rail_schedule_fares to determine the applicable ticket price.';

CREATE TABLE "national_rail_seats" (
  -- BIGSERIAL surrogate PK: seat_code is only unique within a coach, not globally.
  -- This column is heavily referenced as a FK in booking_tickets; integer joins
  -- are more efficient than VARCHAR at scale.
  -- BIGSERIAL (not SERIAL) is used because accumulated seat records across all
  -- schedules and coaches could exceed the INT upper bound over the system lifetime.
  "seat_pk"     BIGSERIAL   PRIMARY KEY,
  "coach_id"    BIGINT      NOT NULL,
  "seat_code"   VARCHAR(10) NOT NULL,
  "seat_row"    INT         NOT NULL CHECK ("seat_row" > 0),
  "seat_column" VARCHAR(5)  NOT NULL,
  UNIQUE ("coach_id", "seat_code")
);

COMMENT ON TABLE "national_rail_seats" IS 'Individual seat definitions. seat_pk is the lock key used during the booking process to guarantee seat uniqueness.';


-- ============================================================
-- ORDER PARENT TABLE
-- ============================================================

CREATE TABLE "travel_orders" (
  -- VARCHAR(20) business ID aligned with mock data order identifiers.
  "order_id"   VARCHAR(20)   PRIMARY KEY,
  "user_id"    VARCHAR(20)   NOT NULL,
  -- order_type is a discriminator that determines which child table holds the
  -- order details: national_rail → bookings, metro → metro_trip_purchases.
  "order_type" VARCHAR(20)   NOT NULL CHECK ("order_type" IN ('national_rail','metro')),
  "amount_usd" NUMERIC(10,2) NOT NULL CHECK ("amount_usd" >= 0),
  "status"     VARCHAR(20)   NOT NULL DEFAULT 'pending'
    CHECK ("status" IN ('pending','confirmed','completed','cancelled')),
  "created_at" TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE "travel_orders" IS 'Shared parent table for all orders. order_type determines whether child detail rows live in bookings (national rail) or metro_trip_purchases (metro).';


-- ============================================================
-- NATIONAL RAIL ORDERS
-- ============================================================

CREATE TABLE "bookings" (
  -- VARCHAR(20) shares the same type as travel_orders.order_id; acts as both PK and FK.
  "booking_id"         VARCHAR(20) PRIMARY KEY,
  -- ticket_count is a denormalised cache maintained by the trg_sync_ticket_count trigger.
  -- It avoids a COUNT(*) query on booking_tickets every time the booking is displayed.
  "ticket_count"       INT         NOT NULL DEFAULT 0,
  -- return_travel_date is a denormalised cache maintained by trg_sync_return_travel_date.
  -- It allows return-trip bookings to be filtered by return date without joining
  -- booking_tickets every time.
  "return_travel_date" DATE
);

COMMENT ON TABLE  "bookings"                      IS 'National rail order header (child of travel_orders). ticket_count and return_travel_date are denormalised caches kept consistent by triggers.';
COMMENT ON COLUMN "bookings"."ticket_count"       IS 'Number of tickets in this booking. Automatically maintained by trg_sync_ticket_count; do not update manually.';
COMMENT ON COLUMN "bookings"."return_travel_date" IS 'Return leg travel date for round-trip bookings; NULL for single-trip. Automatically maintained by trg_sync_return_travel_date.';

CREATE TABLE "booking_tickets" (
  -- SERIAL surrogate PK: no natural business key exists for an individual ticket.
  -- SERIAL (not BIGSERIAL) is sufficient; total ticket volume in a single system
  -- is not expected to exceed the INT upper bound (~2.1 billion).
  "ticket_id"              SERIAL      PRIMARY KEY,
  "booking_id"             VARCHAR(20),
  "schedule_id"            VARCHAR(20),
  "origin_station_id"      VARCHAR(10),
  "destination_station_id" VARCHAR(10),
  -- seat_pk is nullable: if the seat is retired (rolling stock change), the FK is
  -- set to NULL (ON DELETE SET NULL) to preserve the ticket history.
  "seat_pk"                BIGINT,
  "travel_date"            DATE        NOT NULL,
  "departure_time"         TIME        NOT NULL,
  "ticket_type"            VARCHAR(20) NOT NULL CHECK ("ticket_type" IN ('single','return')),
  "fare_class"             VARCHAR(20) NOT NULL CHECK ("fare_class"  IN ('standard','first')),
  -- coach and seat_code are denormalised from the coach/seat tables for display purposes,
  -- avoiding a multi-table JOIN every time a ticket is shown to the passenger.
  "coach"                  VARCHAR(10) NOT NULL,
  "seat_code"              VARCHAR(10) NOT NULL,
  "stops_travelled"        INT,
  "travelled_at"           TIMESTAMPTZ,
  -- leg encodes the journey direction for round trips.
  -- Single-trip tickets always use 'single'.
  -- Round-trip tickets are stored as two separate rows (outbound + inbound),
  -- allowing each leg to have a different schedule, date, and seat.
  "leg"                    VARCHAR(10) NOT NULL DEFAULT 'single'
    CHECK ("leg" IN ('outbound','inbound','single')),
  -- status tracks the ticket lifecycle. Cancelled tickets must not hold a seat
  -- reservation; see the partial unique index uq_booking_tickets_seat below.
  "status"       VARCHAR(20) NOT NULL DEFAULT 'confirmed'
    CHECK ("status" IN ('confirmed','completed','cancelled')),
  -- cancelled_at is required when status='cancelled'; used to determine which
  -- refund window (RF001 / RF002) applies based on hours_before_departure.
  "cancelled_at" TIMESTAMPTZ,

  -- Enforce that cancelled_at is set if and only if status='cancelled'.
  CONSTRAINT "chk_cancelled_at_consistency" CHECK (
    ("status" = 'cancelled' AND "cancelled_at" IS NOT NULL)
    OR
    ("status" != 'cancelled' AND "cancelled_at" IS NULL)
  ),

  -- Enforce logical consistency between ticket_type and leg:
  -- a single ticket must have leg='single'; a return ticket must have outbound or inbound.
  CONSTRAINT "chk_leg_matches_ticket_type" CHECK (
    ("ticket_type" = 'single' AND "leg" = 'single')
    OR
    ("ticket_type" = 'return' AND "leg" IN ('outbound','inbound'))
  ),

  CHECK ("origin_station_id" <> "destination_station_id")
);

COMMENT ON COLUMN "booking_tickets"."seat_pk"      IS 'FK to national_rail_seats. ON DELETE SET NULL: seat retirement preserves ticket history with seat_pk set to NULL.';
COMMENT ON COLUMN "booking_tickets"."leg"          IS 'single = one-way ticket; outbound = round-trip departure leg; inbound = round-trip return leg. Each leg is a separate row and may have a different schedule, date, and seat.';
COMMENT ON COLUMN "booking_tickets"."coach"        IS 'Denormalised from national_rail_coaches.coach_name for display; avoids a JOIN on every ticket view.';
COMMENT ON COLUMN "booking_tickets"."seat_code"    IS 'Denormalised from national_rail_seats.seat_code for display; avoids a JOIN on every ticket view.';
COMMENT ON COLUMN "booking_tickets"."status"       IS 'confirmed = reserved; completed = travelled; cancelled = cancelled. Cancelled tickets do not hold a seat reservation (see uq_booking_tickets_seat).';
COMMENT ON COLUMN "booking_tickets"."cancelled_at" IS 'Cancellation timestamp; required when status=cancelled. Used to evaluate the RF001/RF002 refund window (hours_before_departure).';

-- Seat uniqueness constraint: within a given schedule, travel date, and departure time,
-- each physical seat may be held by at most one active ticket.
-- Cancelled tickets are excluded from this index so the seat becomes available again
-- for rebooking immediately after cancellation.
CREATE UNIQUE INDEX "uq_booking_tickets_seat"
  ON "booking_tickets" ("schedule_id", "travel_date", "departure_time", "seat_pk")
  WHERE "seat_pk" IS NOT NULL AND "status" != 'cancelled';

-- ============================================================
-- TRIGGERS ON booking_tickets
-- ============================================================

-- Round-trip origin/destination symmetry check:
-- When an inbound ticket is inserted, verify that its origin and destination are
-- the exact reverse of the outbound ticket in the same booking.
-- Prevents data anomalies such as outbound A→B with inbound A→B (instead of B→A).
CREATE OR REPLACE FUNCTION check_return_ticket_symmetry()
RETURNS TRIGGER AS $$
DECLARE
  v_outbound RECORD;
BEGIN
  IF NEW.leg = 'inbound' AND NEW.ticket_type = 'return' THEN
    SELECT origin_station_id, destination_station_id
      INTO v_outbound
      FROM booking_tickets
     WHERE booking_id = NEW.booking_id
       AND leg        = 'outbound'
     LIMIT 1;

    IF FOUND THEN
      IF v_outbound.origin_station_id      != NEW.destination_station_id OR
         v_outbound.destination_station_id != NEW.origin_station_id THEN
        RAISE EXCEPTION
          'Round-trip station mismatch: outbound (%) → (%), inbound (%) → (%). The inbound leg must reverse the outbound origin and destination.',
          v_outbound.origin_station_id,
          v_outbound.destination_station_id,
          NEW.origin_station_id,
          NEW.destination_station_id;
      END IF;
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_return_ticket_symmetry
  BEFORE INSERT OR UPDATE ON "booking_tickets"
  FOR EACH ROW EXECUTE FUNCTION check_return_ticket_symmetry();

COMMENT ON FUNCTION check_return_ticket_symmetry() IS
  'Validates that the inbound leg of a round-trip booking has its origin and destination swapped relative to the outbound leg.';

-- ticket_count cache sync:
-- Keeps bookings.ticket_count consistent with the actual row count in booking_tickets,
-- avoiding a COUNT(*) aggregation on every booking display.
CREATE OR REPLACE FUNCTION sync_booking_ticket_count()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    UPDATE bookings SET ticket_count = ticket_count + 1
     WHERE booking_id = NEW.booking_id;
  ELSIF TG_OP = 'DELETE' THEN
    UPDATE bookings SET ticket_count = ticket_count - 1
     WHERE booking_id = OLD.booking_id;
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sync_ticket_count
  AFTER INSERT OR DELETE ON "booking_tickets"
  FOR EACH ROW EXECUTE FUNCTION sync_booking_ticket_count();

COMMENT ON FUNCTION sync_booking_ticket_count() IS
  'Maintains the bookings.ticket_count denormalised cache on every INSERT or DELETE in booking_tickets.';

-- return_travel_date cache sync:
-- Writes the inbound ticket travel date to bookings.return_travel_date when an
-- inbound ticket is inserted or updated.
-- Clears the field if the last inbound ticket is removed, restoring the booking
-- to its single-trip state.
CREATE OR REPLACE FUNCTION sync_return_travel_date()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.leg = 'inbound' THEN
      IF NOT EXISTS (
        SELECT 1 FROM booking_tickets
         WHERE booking_id = OLD.booking_id
           AND leg        = 'inbound'
           AND ticket_id  != OLD.ticket_id
      ) THEN
        UPDATE bookings
           SET return_travel_date = NULL
         WHERE booking_id = OLD.booking_id;
      END IF;
    END IF;
    RETURN OLD;
  END IF;

  IF NEW.leg = 'inbound' THEN
    UPDATE bookings
       SET return_travel_date = NEW.travel_date
     WHERE booking_id = NEW.booking_id;
  END IF;

  -- If an UPDATE changes leg away from 'inbound' and no other inbound tickets remain,
  -- clear the return date so the booking is no longer treated as a round trip.
  IF TG_OP = 'UPDATE' AND OLD.leg = 'inbound' AND NEW.leg != 'inbound' THEN
    IF NOT EXISTS (
      SELECT 1 FROM booking_tickets
       WHERE booking_id = NEW.booking_id
         AND leg        = 'inbound'
         AND ticket_id  != NEW.ticket_id
    ) THEN
      UPDATE bookings
         SET return_travel_date = NULL
       WHERE booking_id = NEW.booking_id;
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sync_return_travel_date
  AFTER INSERT OR UPDATE OF "leg", "travel_date" OR DELETE ON "booking_tickets"
  FOR EACH ROW EXECUTE FUNCTION sync_return_travel_date();

COMMENT ON FUNCTION sync_return_travel_date() IS
  'Maintains bookings.return_travel_date when inbound tickets are inserted, updated, or deleted. Clears the field when the last inbound ticket is removed.';


-- ============================================================
-- METRO TRIP RECORDS
--
-- Split into two tables:
--   metro_trip_purchases  — one row per payment event (single ticket or day pass purchase),
--                           linked to travel_orders.
--   metro_day_pass_trips  — one row per individual journey made under a day pass,
--                           not an independent order; linked to the parent purchase only.
--
-- The split is necessary because a day pass covers multiple journeys under a single
-- payment. Storing each journey as a separate travel_orders row would inflate the
-- orders table and break the invariant that one order = one payment amount.
-- ============================================================

CREATE TABLE "metro_trip_purchases" (
  -- VARCHAR(20) shares the same type as travel_orders.order_id; acts as both PK and FK.
  "purchase_id"            VARCHAR(20) PRIMARY KEY,
  "schedule_id"            VARCHAR(50) NOT NULL,
  "origin_station_id"      VARCHAR(10) NOT NULL,
  "destination_station_id" VARCHAR(10) NOT NULL,
  "travel_date"            DATE        NOT NULL,
  "ticket_type"            VARCHAR(20) NOT NULL CHECK ("ticket_type" IN ('single','day_pass')),
  -- stops_travelled is required for single tickets (used in fare calculation).
  -- For day passes the field is NULL; individual journey distances are recorded
  -- in metro_day_pass_trips.
  "stops_travelled"        INT         CHECK ("stops_travelled" >= 0),
  "purchased_at"           TIMESTAMPTZ NOT NULL,
  "travelled_at"           TIMESTAMPTZ,
  -- cancelled_at records when the purchase was cancelled, used for refund auditing.
  "cancelled_at"           TIMESTAMPTZ,

  CHECK ("origin_station_id" <> "destination_station_id"),

  -- Enforce that single tickets always carry a stop count.
  CONSTRAINT "chk_stops_or_daypass" CHECK (
    "ticket_type" = 'day_pass' OR "stops_travelled" IS NOT NULL
  ),

  -- A journey that has already been made (travelled_at IS NOT NULL) cannot also
  -- be cancelled. Mutually exclusive fields prevent contradictory records.
  CONSTRAINT "chk_metro_cancelled_at" CHECK (
    "cancelled_at" IS NULL OR "travelled_at" IS NULL
  )
);

COMMENT ON TABLE  "metro_trip_purchases"                   IS 'Metro purchase records (child of travel_orders). One row per payment event: a single-trip ticket or a day pass purchase.';
COMMENT ON COLUMN "metro_trip_purchases"."stops_travelled" IS 'Required for single tickets (fare = base + rate × stops); NULL for day passes (per-journey stops recorded in metro_day_pass_trips).';
COMMENT ON COLUMN "metro_trip_purchases"."cancelled_at"    IS 'Cancellation timestamp. Mutually exclusive with travelled_at (chk_metro_cancelled_at): a completed journey cannot be cancelled.';

CREATE TABLE "metro_day_pass_trips" (
  -- VARCHAR(20) business ID for each individual day-pass journey event.
  "trip_id"                VARCHAR(20) PRIMARY KEY,
  "purchase_id"            VARCHAR(20) NOT NULL,
  "schedule_id"            VARCHAR(50) NOT NULL,
  "origin_station_id"      VARCHAR(10) NOT NULL,
  "destination_station_id" VARCHAR(10) NOT NULL,
  "stops_travelled"        INT         NOT NULL CHECK ("stops_travelled" >= 0),
  "travelled_at"           TIMESTAMPTZ NOT NULL,

  CHECK ("origin_station_id" <> "destination_station_id")
);

COMMENT ON TABLE  "metro_day_pass_trips"               IS 'Individual journey events made under a day pass. Not an independent order; linked to the parent purchase via purchase_id.';
COMMENT ON COLUMN "metro_day_pass_trips"."purchase_id" IS 'FK to metro_trip_purchases.purchase_id — the day pass purchase that covers this journey.';


-- ============================================================
-- PAYMENTS
--
-- Payment data is split into two tables:
--   payments        — the payment itself (amount, method, status), source-agnostic.
--   payment_sources — routing table that maps each payment to its order source.
--
-- The separation allows a payment to exist independently (e.g. while awaiting
-- a payment gateway callback) before its source is confirmed.
-- Adding a new source type requires only a new column and an extra OR branch in
-- chk_source_type_and_fields; the payments table itself is never modified.
-- ============================================================

CREATE TABLE "payments" (
  -- VARCHAR(20) business ID aligned with mock data payment identifiers.
  "payment_id" VARCHAR(20)   PRIMARY KEY,
  "amount_usd" NUMERIC(10,2) NOT NULL CHECK ("amount_usd" >= 0),
  "method"     VARCHAR(20)   NOT NULL CHECK ("method" IN ('credit_card','debit_card','ewallet')),
  "status"     VARCHAR(20)   NOT NULL CHECK ("status" IN ('pending','paid','refunded','failed')),
  "paid_at"    TIMESTAMPTZ
);

COMMENT ON TABLE "payments" IS 'Payment records, independent of their order source. Source routing is delegated to payment_sources, making it easy to add new payment source types.';

CREATE TABLE "payment_sources" (
  -- VARCHAR(20) shares the same type as payments.payment_id; no separate surrogate needed.
  "payment_id"               VARCHAR(20) PRIMARY KEY,
  "source_type"              VARCHAR(30) NOT NULL,
  "national_rail_booking_id" VARCHAR(20),
  "metro_trip_id"            VARCHAR(20),

  -- Enforce two invariants simultaneously:
  --   1. Exactly one source column is non-NULL (no payment covers two orders).
  --   2. source_type matches the non-NULL column (no mislabelled rows).
  -- To add a new source type: add a nullable column and a new OR branch here.
  CONSTRAINT "chk_source_type_and_fields" CHECK (
    ("source_type" = 'national_rail_booking'
      AND "national_rail_booking_id" IS NOT NULL
      AND "metro_trip_id"            IS NULL)
    OR
    ("source_type" = 'metro_trip'
      AND "metro_trip_id"            IS NOT NULL
      AND "national_rail_booking_id" IS NULL)
  ),

  -- Each order can be associated with at most one payment, preventing double charging.
  UNIQUE ("national_rail_booking_id"),
  UNIQUE ("metro_trip_id")
);

COMMENT ON TABLE  "payment_sources"               IS 'Payment-to-order routing table. chk_source_type_and_fields guarantees exactly one non-NULL source column and consistency with source_type.';
COMMENT ON COLUMN "payment_sources"."source_type" IS 'Discriminator identifying the source type; must match the non-NULL source column.';


-- ============================================================
-- CUSTOMER FEEDBACK
-- ============================================================

CREATE TABLE "customer_feedback" (
  -- VARCHAR(20) business ID aligned with mock data.
  "feedback_id"  VARCHAR(20) PRIMARY KEY,
  "order_id"     VARCHAR(20) NOT NULL,
  "rating"       INT         NOT NULL CHECK ("rating" BETWEEN 1 AND 5),
  "submitted_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- One feedback entry per order prevents duplicate submissions.
  UNIQUE ("order_id")
);

COMMENT ON TABLE  "customer_feedback"            IS 'Passenger ratings, one per order. user_id is intentionally omitted; it is retrieved via JOIN travel_orders to avoid storing it twice and risking inconsistency.';
COMMENT ON COLUMN "customer_feedback"."order_id" IS 'FK to travel_orders.order_id. To identify the reviewer, JOIN travel_orders on this column and read user_id from there.';

CREATE TABLE "feedback_comments" (
  "feedback_id"  VARCHAR(20) PRIMARY KEY,
  "comment_text" TEXT        NOT NULL
);

COMMENT ON TABLE "feedback_comments" IS 'Optional free-text comments attached to a rating. Stored in a separate 1:1 table to keep the TEXT column out of customer_feedback and avoid penalising rating-only queries.';


-- ============================================================
-- INDEXES
-- ============================================================

-- travel_orders: user_id supports per-user order history queries;
-- status supports filtering by order lifecycle state;
-- created_at supports time-range reports and refund statistics.
CREATE INDEX "idx_travel_orders_user_id"    ON "travel_orders" ("user_id");
CREATE INDEX "idx_travel_orders_status"     ON "travel_orders" ("status");
CREATE INDEX "idx_travel_orders_created_at" ON "travel_orders" ("created_at");

-- metro_rail_interchanges: both lookup directions are frequent
-- (find the rail station paired with a given metro station, and vice versa).
CREATE INDEX "idx_interchanges_metro" ON "metro_rail_interchanges" ("metro_station_id");
CREATE INDEX "idx_interchanges_rail"  ON "metro_rail_interchanges" ("rail_station_id");

-- national_rail_schedule_stops:
-- Historical version uniqueness: one record per (schedule, station, effective_from).
CREATE UNIQUE INDEX ON "national_rail_schedule_stops" ("schedule_id", "station_id", "effective_from");
-- Stop order uniqueness: stop_order must be unique within a schedule version.
CREATE UNIQUE INDEX ON "national_rail_schedule_stops" ("schedule_id", "stop_order", "effective_from");
-- Effective range lookup: supports queries such as "all stops active on a given date".
CREATE INDEX "idx_schedule_stops_effective"
  ON "national_rail_schedule_stops" ("schedule_id", "is_stop", "effective_from", "effective_to");
-- Current-record uniqueness: at most one active record per (schedule, station).
-- Multiple historical rows (effective_to IS NOT NULL) are permitted, but only one
-- currently-active row (effective_to IS NULL) is allowed per station per schedule.
-- Without this index, concurrent updates could produce two active records, making
-- it impossible to determine the current stop status.
CREATE UNIQUE INDEX "uq_schedule_stops_current"
  ON "national_rail_schedule_stops" ("schedule_id", "station_id")
  WHERE "effective_to" IS NULL;

-- booking_tickets: three common access patterns each get a dedicated index.
CREATE INDEX "idx_booking_tickets_booking" ON "booking_tickets" ("booking_id");
CREATE INDEX "idx_booking_tickets_leg"     ON "booking_tickets" ("booking_id", "leg");
CREATE INDEX "idx_booking_tickets_date"    ON "booking_tickets" ("schedule_id", "travel_date");

-- bookings: partial index on return_travel_date covers only round-trip bookings,
-- keeping the index small while still supporting fast return-date range queries.
CREATE INDEX "idx_bookings_return_date"
  ON "bookings" ("return_travel_date")
  WHERE "return_travel_date" IS NOT NULL;

-- metro_trip_purchases: travel_date supports daily availability and reporting queries.
CREATE INDEX "idx_metro_purchases_travel_date" ON "metro_trip_purchases" ("travel_date");
CREATE INDEX "idx_metro_purchases_purchase_id" ON "metro_trip_purchases" ("purchase_id");

-- metro_day_pass_trips: purchase_id lookup for all trips under a day pass;
-- travelled_at for time-range journey queries.
CREATE INDEX "idx_day_pass_trips_purchase" ON "metro_day_pass_trips" ("purchase_id");
CREATE INDEX "idx_day_pass_trips_date"     ON "metro_day_pass_trips" ("travelled_at");

-- payment_sources: both source ID columns are lookup keys for payment queries.
CREATE INDEX "idx_payment_sources_booking_id" ON "payment_sources" ("national_rail_booking_id");
CREATE INDEX "idx_payment_sources_metro_id"   ON "payment_sources" ("metro_trip_id");

-- customer_feedback: order_id lookup for feedback retrieval.
CREATE INDEX "idx_customer_feedback_order_id" ON "customer_feedback" ("order_id");


-- ============================================================
-- FOREIGN KEYS
-- ============================================================

-- user_security → users
-- CASCADE: user_security is 1:1 with users; removing an account removes its credentials.
ALTER TABLE "user_security"
  ADD FOREIGN KEY ("user_id") REFERENCES "users" ("user_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- metro_station_lines → metro_stations
-- CASCADE: a station's line memberships have no meaning without the station.
ALTER TABLE "metro_station_lines"
  ADD FOREIGN KEY ("station_id") REFERENCES "metro_stations" ("station_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- metro_line_transfer_times → metro_stations
-- CASCADE: transfer-time records for a station are meaningless without the station.
ALTER TABLE "metro_line_transfer_times"
  ADD FOREIGN KEY ("station_id") REFERENCES "metro_stations" ("station_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- national_rail_station_lines → national_rail_stations
-- CASCADE: same rationale as metro_station_lines.
ALTER TABLE "national_rail_station_lines"
  ADD FOREIGN KEY ("station_id") REFERENCES "national_rail_stations" ("station_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- metro_rail_interchanges → metro_stations / national_rail_stations
-- CASCADE: an interchange record is meaningless if either of its stations is removed.
ALTER TABLE "metro_rail_interchanges"
  ADD FOREIGN KEY ("metro_station_id") REFERENCES "metro_stations" ("station_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "metro_rail_interchanges"
  ADD FOREIGN KEY ("rail_station_id") REFERENCES "national_rail_stations" ("station_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- metro_schedules → metro_stations
-- RESTRICT: a station referenced as a schedule endpoint cannot be deleted until
-- the schedule is removed or updated.
ALTER TABLE "metro_schedules"
  ADD FOREIGN KEY ("origin_station_id") REFERENCES "metro_stations" ("station_id")
  ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "metro_schedules"
  ADD FOREIGN KEY ("dest_station_id") REFERENCES "metro_stations" ("station_id")
  ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

-- metro_schedule_stops → metro_schedules
-- CASCADE: stop sequence rows belong entirely to their schedule.
ALTER TABLE "metro_schedule_stops"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "metro_schedules" ("schedule_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- metro_schedule_stops → metro_stations
-- RESTRICT: a station still active in a stop list cannot be deleted.
ALTER TABLE "metro_schedule_stops"
  ADD FOREIGN KEY ("station_id") REFERENCES "metro_stations" ("station_id")
  ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

-- metro_schedule_operating_days → metro_schedules
-- CASCADE: operating day rows belong entirely to their schedule.
ALTER TABLE "metro_schedule_operating_days"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "metro_schedules" ("schedule_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- national_rail_schedules → national_rail_stations
-- RESTRICT: endpoint stations cannot be deleted while referenced by a schedule.
ALTER TABLE "national_rail_schedules"
  ADD FOREIGN KEY ("origin_station_id") REFERENCES "national_rail_stations" ("station_id")
  ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "national_rail_schedules"
  ADD FOREIGN KEY ("destination_station_id") REFERENCES "national_rail_stations" ("station_id")
  ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

-- national_rail_schedule_stops → national_rail_schedules
-- CASCADE: all stop records (including historical versions) are removed with the schedule.
ALTER TABLE "national_rail_schedule_stops"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "national_rail_schedules" ("schedule_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- national_rail_schedule_stops → national_rail_stations
-- CASCADE: stop records referencing a deleted station are removed with it.
ALTER TABLE "national_rail_schedule_stops"
  ADD FOREIGN KEY ("station_id") REFERENCES "national_rail_stations" ("station_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- national_rail_schedule_operating_days → national_rail_schedules
-- CASCADE: operating day rows belong entirely to their schedule.
ALTER TABLE "national_rail_schedule_operating_days"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "national_rail_schedules" ("schedule_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- national_rail_schedule_fares → national_rail_schedules
-- CASCADE: fare records belong entirely to their schedule.
ALTER TABLE "national_rail_schedule_fares"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "national_rail_schedules" ("schedule_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- national_rail_seat_layouts → national_rail_schedules
-- CASCADE: the seat layout for a schedule is removed with it.
ALTER TABLE "national_rail_seat_layouts"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "national_rail_schedules" ("schedule_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- national_rail_coaches → national_rail_seat_layouts
-- CASCADE: coaches within a layout are removed with the layout.
ALTER TABLE "national_rail_coaches"
  ADD FOREIGN KEY ("layout_id") REFERENCES "national_rail_seat_layouts" ("layout_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- national_rail_seats → national_rail_coaches
-- CASCADE: seats within a coach are removed with the coach.
ALTER TABLE "national_rail_seats"
  ADD FOREIGN KEY ("coach_id") REFERENCES "national_rail_coaches" ("coach_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- travel_orders → users
-- RESTRICT: a user with existing orders must not be hard-deleted directly.
-- The application layer must soft-delete (is_active=FALSE) first, ensuring every
-- order always traces back to a user record.
ALTER TABLE "travel_orders"
  ADD FOREIGN KEY ("user_id") REFERENCES "users" ("user_id")
  ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

-- bookings → travel_orders
-- CASCADE: the booking header is removed with its parent order.
ALTER TABLE "bookings"
  ADD FOREIGN KEY ("booking_id") REFERENCES "travel_orders" ("order_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- booking_tickets → bookings
-- CASCADE: ticket rows are removed with their booking.
ALTER TABLE "booking_tickets"
  ADD FOREIGN KEY ("booking_id") REFERENCES "bookings" ("booking_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- booking_tickets → national_rail_schedules
-- RESTRICT: a schedule referenced by an active ticket cannot be deleted.
ALTER TABLE "booking_tickets"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "national_rail_schedules" ("schedule_id")
  ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

-- booking_tickets → national_rail_stations (origin and destination)
-- RESTRICT: stations referenced by active tickets cannot be deleted.
ALTER TABLE "booking_tickets"
  ADD FOREIGN KEY ("origin_station_id") REFERENCES "national_rail_stations" ("station_id")
  ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "booking_tickets"
  ADD FOREIGN KEY ("destination_station_id") REFERENCES "national_rail_stations" ("station_id")
  ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

-- booking_tickets → national_rail_seats
-- SET NULL: if a seat is retired due to rolling stock changes, the ticket history
-- (travel records, refunds) is preserved with seat_pk set to NULL.
ALTER TABLE "booking_tickets"
  ADD FOREIGN KEY ("seat_pk") REFERENCES "national_rail_seats" ("seat_pk")
  ON DELETE SET NULL DEFERRABLE INITIALLY IMMEDIATE;

-- metro_trip_purchases → travel_orders
-- CASCADE: the purchase record is removed with its parent order.
ALTER TABLE "metro_trip_purchases"
  ADD FOREIGN KEY ("purchase_id") REFERENCES "travel_orders" ("order_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- metro_trip_purchases → metro_schedules
-- RESTRICT: a schedule referenced by purchase records cannot be deleted.
ALTER TABLE "metro_trip_purchases"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "metro_schedules" ("schedule_id")
  ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

-- metro_trip_purchases → metro_stations (origin and destination)
-- RESTRICT: stations referenced by purchase records cannot be deleted.
ALTER TABLE "metro_trip_purchases"
  ADD FOREIGN KEY ("origin_station_id") REFERENCES "metro_stations" ("station_id")
  ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "metro_trip_purchases"
  ADD FOREIGN KEY ("destination_station_id") REFERENCES "metro_stations" ("station_id")
  ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

-- metro_day_pass_trips → metro_trip_purchases
-- CASCADE: journey events are removed with their parent day-pass purchase.
ALTER TABLE "metro_day_pass_trips"
  ADD FOREIGN KEY ("purchase_id") REFERENCES "metro_trip_purchases" ("purchase_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- metro_day_pass_trips → metro_schedules
-- RESTRICT: a schedule referenced by journey events cannot be deleted.
ALTER TABLE "metro_day_pass_trips"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "metro_schedules" ("schedule_id")
  ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

-- metro_day_pass_trips → metro_stations (origin and destination)
-- RESTRICT: stations referenced by journey events cannot be deleted.
ALTER TABLE "metro_day_pass_trips"
  ADD FOREIGN KEY ("origin_station_id") REFERENCES "metro_stations" ("station_id")
  ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "metro_day_pass_trips"
  ADD FOREIGN KEY ("destination_station_id") REFERENCES "metro_stations" ("station_id")
  ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

-- payment_sources → payments
-- CASCADE: the routing record is removed with its payment.
ALTER TABLE "payment_sources"
  ADD FOREIGN KEY ("payment_id") REFERENCES "payments" ("payment_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- payment_sources → bookings / metro_trip_purchases
-- RESTRICT: an order with an associated payment cannot be deleted until the
-- refund process is completed and the payment record is resolved first.
ALTER TABLE "payment_sources"
  ADD FOREIGN KEY ("national_rail_booking_id") REFERENCES "bookings" ("booking_id")
  ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "payment_sources"
  ADD FOREIGN KEY ("metro_trip_id") REFERENCES "metro_trip_purchases" ("purchase_id")
  ON DELETE RESTRICT DEFERRABLE INITIALLY IMMEDIATE;

-- customer_feedback → travel_orders
-- CASCADE: feedback is removed with its parent order.
ALTER TABLE "customer_feedback"
  ADD FOREIGN KEY ("order_id") REFERENCES "travel_orders" ("order_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- feedback_comments → customer_feedback
-- CASCADE: the comment is removed with its parent feedback record.
ALTER TABLE "feedback_comments"
  ADD FOREIGN KEY ("feedback_id") REFERENCES "customer_feedback" ("feedback_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;


-- ============================================================
--  VECTOR SCHEMA  (RAG / Help Desk) — do not modify
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_documents (
    id          SERIAL       PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    category    VARCHAR(50)  NOT NULL,  -- 'refund', 'booking', 'conduct'
    content     TEXT         NOT NULL,
    -- 768-dim  → Ollama nomic-embed-text (default)
    -- 3072-dim → Gemini gemini-embedding-001
    -- If you switch LLM_PROVIDER to gemini, change to vector(3072) and reset the database.
    embedding   vector(768),
    source_file VARCHAR(200),
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

-- Index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS ON policy_documents USING hnsw (embedding vector_cosine_ops);

``

## Agreed Graph Schema

<!-- ============================================================
  FILL THIS IN after your team agrees on Neo4j node labels and
  relationship types.
  ============================================================ -->

``text
Node labels:
- Metro stations use multiple labels: :Station:Metro:MetroStation
- National rail stations use multiple labels: :Station:NationalRail:NationalRailStation

Reason:
- :Station supports whole-network queries.
- :Metro and :NationalRail follow our team graph schema.
- :MetroStation and :NationalRailStation match the teacher guide terminology and static evaluation expectations.

Relationship types:
- METRO_LINK     (捷運站之間的相鄰連線，雙向儲存)
- RAIL_LINK      (國鐵站之間的相鄰連線，雙向儲存)
- INTERCHANGE_TO (捷運↔國鐵站內轉乘，雙向儲存；A→B 與 B→A 各為獨立一條邊)

Node properties:
- station_id : 與 PostgreSQL PK 完全一致（例如 "MS01"），跨資料庫查詢的關鍵
- name       : 人可讀站名，方便 Neo4j Browser 視覺化與除錯
- lines      : 路線 ID 陣列（例如 ["M1","M2"]），原生 Neo4j list，可用 "M1" IN s.lines 過濾

Edge properties:
- travel_time_min : 行車時間（分鐘），Dijkstra 最短路徑的權重；INTERCHANGE_TO 固定為 5（步行換乘假設值）
- line            : 路線 ID（例如 "M1"），儲存於 METRO_LINK / RAIL_LINK；INTERCHANGE_TO 不儲存（轉乘非特定路線）

METRO_LINK 額外 fare 屬性（由 metro_schedules.json 帶入，依路線不同）：
- base_fare_usd     : 上車基本票價
- per_stop_rate_usd : 每站增量票價

RAIL_LINK 額外 fare 屬性（由 national_rail_schedules.json 帶入，依路線不同）：
國鐵有 normal / express 兩種 service_type，各自有 standard / first 兩種艙等，因此每條邊儲存 8 個 fare 欄位：
- normal_standard_fare_usd          : 普通車、標準艙基本票價
- normal_standard_per_stop_rate_usd : 普通車、標準艙每站增量票價
- normal_first_fare_usd             : 普通車、頭等艙基本票價
- normal_first_per_stop_rate_usd    : 普通車、頭等艙每站增量票價
- express_standard_fare_usd          : 快車、標準艙基本票價
- express_standard_per_stop_rate_usd : 快車、標準艙每站增量票價
- express_first_fare_usd             : 快車、頭等艙基本票價
- express_first_per_stop_rate_usd    : 快車、頭等艙每站增量票價

Idempotency:
- 所有節點與關係建立均使用 MERGE（不用 CREATE），重複執行不產生重複資料
- 關係的 MERGE key 包含 line，確保同一對站點被不同路線共用時各自有獨立邊

Directionality:
- 所有關係雙向儲存（A→B 與 B→A）
- METRO_LINK / RAIL_LINK：JSON 鄰接表本身對稱，迴圈自然產生雙向
- INTERCHANGE_TO：需各自執行兩次 session.run（metro→rail 與 rail→metro）
``

## Function Signatures We Are Implementing

These are fixed contracts. AI-generated code must match these signatures exactly.

### Relational (`databases/relational/queries.py`)

``python
# Read-only
def query_national_rail_availability(origin_id: str, destination_id: str, travel_date: Optional[str] = None) -> list[dict]: ...
def query_national_rail_fare(schedule_id: str, fare_class: str, stops_travelled: int) -> Optional[dict]: ...
def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]: ...
def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]: ...
def query_available_seats(schedule_id: str, travel_date: str, fare_class: str) -> list[dict]: ...
def query_user_profile(user_email: str) -> Optional[dict]: ...
def query_user_bookings(user_email: str) -> dict: ...  # returns {"national_rail": [...], "metro": [...]}
def query_payment_info(booking_id: str) -> Optional[dict]: ...

# Write operations
def execute_booking(user_id, schedule_id, origin_station_id, destination_station_id, travel_date, fare_class, seat_id, ticket_type="single") -> tuple[bool, dict | str]: ...
def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]: ...

# Auth
def register_user(email, first_name, surname, year_of_birth, password, secret_question, secret_answer) -> tuple[bool, str]: ...
def login_user(email: str, password: str) -> Optional[dict]: ...
def get_user_secret_question(email: str) -> Optional[str]: ...
def verify_secret_answer(email: str, answer: str) -> bool: ...
def update_password(email: str, new_password: str) -> bool: ...
``

### Graph (`databases/graph/queries.py`)

``python
def query_shortest_route(origin_id: str, destination_id: str, network: str = "auto") -> dict: ...
def query_cheapest_route(origin_id: str, destination_id: str, network: str = "auto", fare_class: str = "standard") -> dict: ...
def query_alternative_routes(origin_id, destination_id, avoid_station_id, network="auto", max_routes=3) -> list[dict]: ...
def query_interchange_path(origin_id: str, destination_id: str) -> dict: ...
def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]: ...
def query_station_connections(station_id: str) -> list[dict]: ...
``

## Team Decisions Log

<!-- Add entries as you make decisions. Format: "Decision: X. Why: Y." -->

- [x] Schema design (已完成，詳見 databases/relational/schema.sql):
  - Decision: PK 採用 VARCHAR business ID（如 "U001", "NR01"）給有業務意義的欄位，BIGSERIAL/SERIAL 給無自然 key 的內部記錄（如 seat_pk, coach_id, ticket_id）。 Why: VARCHAR ID 保留可讀性；BIGSERIAL 整數 FK join 效能優於 VARCHAR；不用 UUID 因為不需要分散式唯一性。
  - Decision: 刪除策略採混合模式。users 用 soft delete（is_active=FALSE），保留歷史訂單審計軌跡；訂單子層資料（booking_tickets 等）用 ON DELETE CASCADE；booking_tickets.seat_pk 用 ON DELETE SET NULL（座位退役時保留票務記錄）；travel_orders.user_id 用 ON DELETE RESTRICT（強制先 soft delete）。 Why: 財務記錄不能因帳號刪除而消失；每個 FK 都明確宣告行為，不依賴資料庫預設值。
  - Decision: national_rail_schedule_stops 有 is_stop 欄位區分「實際停靠」與「通過不停」（快車）。 Why: 票價計算以 is_stop=TRUE 的站數為準，快車通過中間站不計費。
  - Decision: 密碼使用 Argon2id（PHC format），存在獨立的 user_security 表。 Why: PHC format 將 salt 嵌入 hash 字串，不需要獨立 salt 欄位；user_security 獨立避免一般查詢意外曝露 hash。
  - Decision: booking_tickets 有部分唯一索引 uq_booking_tickets_seat on (schedule_id, travel_date, departure_time, seat_pk) WHERE seat_pk IS NOT NULL AND status != 'cancelled'。 Why: 同一物理座位在同一班次同一時刻只能被一個有效訂票佔用；cancelled 排除讓座位可重新被訂。
- [x] Graph schema (已在 seed_neo4j.py 實作，2026-05-30):
  - Decision: Node labels 採用三重標籤（`:Station:Metro:MetroStation` / `:Station:NationalRail:NationalRailStation`）。 Why: 第三個標籤（:MetroStation / :NationalRailStation）對應教師評分規範的名稱；前兩個標籤保留全網與單網查詢彈性。
  - Decision: Relationship types 明確區分（`METRO_LINK`, `RAIL_LINK`, `INTERCHANGE_TO`），全部雙向儲存。 Why: 雙向儲存讓 Dijkstra 不需要 undirected pattern（Neo4j 中較慢）；區分類型支援過濾單一路網。
  - Decision: Edge properties — `travel_time_min`（Dijkstra 權重）+ `line`（路線 ID，僅 METRO_LINK/RAIL_LINK）。INTERCHANGE_TO 不儲存 line，固定 travel_time_min=5。 Why: line 加入 MERGE key 以防兩線共用同一對站點時邊互相覆蓋；INTERCHANGE_TO 為步行換乘，不屬於任何路線。
  - Decision: RAIL_LINK fare 屬性採用 8 欄位（normal/express × standard/first），而非 4 欄位。 Why: 國鐵同一條路線同時有普通車與快車兩種 service_type，票價不同；將兩者展開在同一條邊，query_cheapest_route 可直接用 `r.normal_standard_fare_usd` 或 `r.express_standard_fare_usd` 取值，不需要額外 JOIN 或條件分支查另一張表。
  - Decision: Node properties 為 `station_id`（與 PostgreSQL PK 完全一致）、`name`、`lines`（原生 Neo4j 陣列）。 Why: station_id 對齊保證跨資料庫查詢正確；lines 存為陣列可用 "M1" IN s.lines 高效過濾。
  - Decision: 全部使用 MERGE 不用 CREATE（idempotent）。 Why: 開發期間會多次重新 seed，MERGE 確保安全重跑。
- [x] PostgreSQL seeding (已在 seed_postgres.py 全部實作，2026-05-31):
  **執行順序與 FK 依賴**
  - Decision: 執行順序固定為 `seed_metro_stations → seed_national_rail_stations → seed_metro_schedules → seed_national_rail_schedules → seed_seat_layouts → seed_users → seed_national_rail_bookings → seed_metro_travels → seed_payments → seed_feedback`。`metro_rail_interchanges` 放在 `seed_national_rail_stations` 結尾插入，而非 `seed_metro_stations` 中。Why: FK 約束強制此順序；`metro_rail_interchanges` 同時有指向捷運站和國鐵站的 FK，必須等兩個父表都存在才能插入。
  **Idempotency 策略**
  - Decision: 一般表使用通用 `insert_many`（內含 `ON CONFLICT DO NOTHING`）。`booking_tickets` 無業務唯一鍵（`SERIAL` PK），改用預先查詢過濾已存在的 `booking_id`。`national_rail_schedule_stops`、`national_rail_coaches`、`national_rail_seats` 各自指定明確的衝突目標（如 `ON CONFLICT (layout_id, coach_name) DO NOTHING`）。Why: 評分規範要求重複執行不得失敗或產生重複資料；無唯一目標的 `ON CONFLICT DO NOTHING` 在有多個 unique index 時行為不明確，必須明確指定。
  **`travel_orders` 共用父表設計**
  - Decision: 國鐵訂單（`BK…`）和捷運購票（`MT…`）都在 `travel_orders` 各有一列，以 `order_type = 'national_rail'` 或 `'metro'` 區分。JSON 的 `booked_at` / `purchased_at` 對應到 schema 的 `created_at`。Why: `query_user_bookings` 必須回傳 `{"national_rail": [...], "metro": [...]}`；共用父表讓單一 JOIN 可取得使用者的全部訂單，不需分兩次查詢。
  **JSON 欄位名稱 → Schema 欄位名稱對應**
  - Decision: 以下欄位在 seeding 時更名，**AI 生成查詢時一律使用 schema 欄位名稱**：
    | JSON 欄位 | Schema 欄位 | 所在資料表 |
    |---|---|---|
    | `line` | `line_name` | `metro_schedules`、`national_rail_schedules` |
    | `destination_station_id` | `dest_station_id` | `metro_schedules` |
    | `booked_at` / `purchased_at` | `created_at` | `travel_orders` |
    | `booking_id` | `order_id` | `customer_feedback` |
    | `seat_id` | `seat_code` | `booking_tickets` |
    Why: Schema 欄位已正規化命名；直接用 JSON key 生成查詢會用到不存在的欄位名稱。
  **`booking_tickets.leg` 推導規則**
  - Decision: `bookings.json` 無 `leg` 欄位，seeder 自行推導：`ticket_type='single'` → `leg='single'`；`ticket_type='return'` → `leg='outbound'`（mock data 只有去程記錄）；其他值立即拋出 `ValueError`。Why: Schema `CHECK` 約束要求 `leg` 必須是 `('single', 'outbound', 'inbound')` 之一且與 `ticket_type` 一致；靜默填錯會導致退票政策計算出錯。
  **已取消訂單的 `cancelled_at` 替代值**
  - Decision: `status='cancelled'` 的訂單，`cancelled_at` 以 `booked_at` 代替（`bookings.json` 無此欄位）；其他狀態設 `NULL`。Why: Schema `CHECK` 約束（`chk_cancelled_at_consistency`）要求取消訂單的 `cancelled_at IS NOT NULL`；`booked_at` 是最保守的替代值。注意：`execute_cancellation()` 執行期會記錄真實取消時間戳，此處僅為 seed 資料的近似值。
  **`national_rail_coaches` / `national_rail_seats` BIGSERIAL PK 查回策略**
  - Decision: 插入 coaches 後，立即透過三表 JOIN（`seats → coaches → layouts`）查回 DB 生成的 `coach_id`，再用於建立 seats 的 FK。若有任何 `(layout_id, coach_name)` 查不回來，立即 `RuntimeError`。Why: coach 名稱只在同一 layout 內唯一、seat code 只在同一 coach 內唯一，因此使用 BIGSERIAL 代理鍵；`execute_values` 不回傳自動生成的 ID，必須主動 SELECT；提早失敗比讓 `KeyError` 在 list comprehension 內部爆掉更好 debug。
  **捷運日票記錄的分流邏輯**
  - Decision: `metro_travel_history.json` 中 `day_pass_ref=None` 的記錄為購票事件，插入 `travel_orders` + `metro_trip_purchases`；`day_pass_ref` 有值的記錄為日票下的單次搭乘，只插入 `metro_day_pass_trips`，不新增 `travel_orders`。Why: 一張日票對應一筆 `travel_orders`（一次付款），旗下每次搭乘是獨立的 trip 記錄；若搭乘記錄也建 `travel_orders` 會造成重複計費資料。
  **日票搭乘 `stops_travelled` 計算與資料一致性驗證**
  - Decision: 日票搭乘記錄無 `stops_travelled`，seeder 從 `metro_schedules.json` 建立 `(schedule_id, station_id) → stop_sequence` 對應表，計算 `abs(dest_seq - origin_seq)`；跨線搭乘找不到序號時填 `0` 作為哨兵值。插入前會驗證此對應表與 DB 的 `metro_schedule_stops` 完全一致，不一致即 `RuntimeError`。Why: 票價公式 `base_fare + per_stop_rate × stops_travelled` 依賴此欄位；`0` 滿足 `CHECK (stops_travelled >= 0)` 且可辨識為「路線資料不足」而非真正零站搭乘；驗證步驟防止 JSON 與 DB 無聲地不同步。
  **`payment_sources` 來源類型判斷**
  - Decision: `booking_id` 前綴 `BK…` → `source_type='national_rail_booking'`；其他（如 `MT…`）→ `source_type='metro_trip'`；對應 FK 欄設值，另一欄設 `NULL`。Why: Schema `CHECK` 約束要求兩個 FK 欄位恰好一個非 `NULL`；ID 前綴是 mock data 中唯一的來源區分依據。
  **`customer_feedback` 不存 `user_id`；`feedback_comments` 獨立子表**
  - Decision: `customer_feedback` 不含 `user_id` 欄位，需要時透過 JOIN `travel_orders` 取得。`feedback_comments` 獨立為 1:1 子表，只在來源 JSON 的 `comment` 非 `NULL` 時插入。Why: 避免 `user_id` 在兩表重複儲存造成不一致；comment 獨立存放讓純評分聚合查詢不需掃描 TEXT 欄位。
  **密碼 Argon2id hash；重跑 seeder 只處理新使用者**
  - Decision: `user_security` 以 Argon2id hash 儲存密碼與密保答案。重跑 seeder 時先查詢已存在的 `user_id`，只對新使用者執行 hash，已有記錄的跳過。Why: Argon2id 刻意設計為耗時（防暴力破解）；若每次全部重新 hash 會讓 seeder 重跑過慢；mock data 密碼在執行間不變，跳過安全。
- [x] query_national_rail_availability — available_seats 計算方式 (2026-06-02):
  - Decision: `available_seats = total_seats - MAX(同一 departure_time 的訂座數)`，而非 `total_seats - 當天所有班次訂座加總`。 Why: 同一物理座位在不同 departure_time 可各自被訂（uq_booking_tickets_seat 的 unique key 包含 departure_time），若直接加總全天訂座量會超過 total_seats 產生負數。以最繁忙班次的訂座量為基準，得到的是保守但永遠非負的可用座位估計。
  - Decision: stops_travelled 計算：national rail 用 COUNT(is_stop=TRUE) 實際站數（不是 stop_order 差值）；metro 用 dest_stop_sequence - origin_stop_sequence。 Why: 快車在某些站是 pass-through（is_stop=FALSE），若用 stop_order 差值會把通過站也計費；metro 無 pass-through 概念故可直接用序列差。
- [x] `query_available_seats` (實作完成，2026-06-02):
  - Decision: 回傳欄位 `seat_id` 對應資料庫的 `seat_pk`（BIGINT）。Why: stub 合約要求 `seat_id` 這個 key 名稱；`execute_booking` 接收的 `seat_id` 參數實際上就是 `seat_pk`，兩端保持一致。
  - Decision: 子查詢排除條件為 `status != 'cancelled'`，未加 `departure_time` 篩選。Why: 函數簽名沒有 `departure_time` 參數，無法加入此條件；目前 mock data 每個 schedule 每天只有一個發車時間，不影響正確性。若未來簽名擴充才補上。
  - Decision: 使用 `NOT IN` 子查詢而非 LEFT JOIN 過濾已佔用座位。Why: 資料量小，可讀性高；子查詢已有 `seat_pk IS NOT NULL` 保護，不會因 NULL 導致 `NOT IN` 邏輯錯誤。
- [x] `query_user_profile` (實作完成，2026-06-02):
  - Decision: `first_name` / `surname` 在應用層從 `full_name` 拆分（`.split(" ", 1)`），不存於 schema。Why: `users` 表只有 `full_name` 欄位，Python 層拆分避免 schema 異動；未來若新增獨立欄位可直接移除此邏輯。
  - Decision: `date_of_birth` 與 `registered_at` 以 `str()` 轉換後回傳，`None` 時保持 `None`。Why: 避免呼叫端收到 `datetime.date` / `datetime.datetime` 物件導致 JSON 序列化失敗。
  - Decision: 對 `full_name = None` 加 `or ""` 防禦性處理。Why: 雖然 schema 定義為 `NOT NULL`，防禦性寫法在資料異常時不會崩潰。
- [x] `query_user_bookings` (實作完成，2026-06-02):
  - Decision: Metro 查詢拆為三段：① 主購買記錄、② Day Pass 子旅程、③ Python 層合併，不使用 LEFT JOIN 一次展平。Why: LEFT JOIN 展平會讓父層欄位（`amount_usd`、`created_at` 等）在每筆子旅程上重複出現，Python 合併後結構為巢狀，呼叫端不需自行 group。
  - Decision: Day Pass 子旅程使用 `WHERE dpt.purchase_id = ANY(%s)` 批次查詢。Why: 避免 N+1 查詢；`ANY(%s)` 傳入 Python list，psycopg2 自動轉為 PostgreSQL array，空 list 也能正常處理。
  - Decision: 組裝子旅程時使用 `r.pop("purchase_id")` 取出 key 並同時從 dict 移除。Why: `purchase_id` 只作為 Python groupby 的 key，不應出現在子旅程 dict 裡，避免前端收到重複欄位。
  - Decision: National Rail 查詢的 SELECT 清單包含 `bt.travelled_at`。Why: 呼叫端需判斷票是否已搭乘；僅靠 `status = 'completed'` 無法得知實際搭乘時間。
- [x] `query_payment_info` (實作完成，2026-06-02):
  - Decision: 使用兩段式查詢（先查 `national_rail_booking_id`，查無再查 `metro_trip_id`），不使用 `OR` 單一查詢。Why: `OR` 在 ID 字串恰好跨欄碰撞時會命中兩筆記錄，`fetchone()` 只取第一筆導致回傳錯誤付款資料；兩段式查詢從根本消除此風險，且不依賴 BK/MT 前綴命名假設。
  - Decision: 兩次 `fetchone()` 均做 `if row else None` 的 None 檢查後才呼叫 `dict()`。Why: 查無記錄時 `fetchone()` 回傳 `None`，直接呼叫 `dict(None)` 會崩潰。
- [x] Graph query implementation (databases/graph/queries.py, 2026-06-03):
  - Decision: `query_cheapest_route` 用 variable-length path + Cypher `reduce()` 而非 APOC dijkstra。Why: APOC dijkstra 只接受單一 property name 作為權重，但 fare 需依 relationship type 和 fare_class 動態計算，無法用單一 property 表示。
  - Decision: `query_cheapest_route` 與 `query_alternative_routes` 的路徑深度上限設為 `*1..30`（非 `*1..15`）。Why: 網路含 MS01–MS20（20站）+ NR01–NR10（10站），最長 simple path 達 29 hops；15 會遺漏遠端跨站路徑。
  - Decision: `query_delay_ripple` hops=0 分開處理，hops>0 使用 `*0..N` 包含 source 本身（hops_away=0）。Why: Cypher `*1..N` 無法表達深度 0（只回傳 source）；`*0..N` 讓 source 自然以 hops_away=0 出現在結果中，與 hops=0 case 行為一致。
  - Decision: `query_alternative_routes` 回傳 `list[dict]`，每個 dict 含 `total_time_min`、`path`、`legs`，而非 `list[list[dict]]`。Why: 實作提供完整路由資訊；agent.py 的 `enumerate(routes)` 直接迭代此結構，LLM 能解讀巢狀 dict。
- [x] `get_payment_info` 工具加入 agent.py (2026-06-04):
  - Decision: 在 TOOLS 列表新增 `get_payment_info` 工具定義，描述觸發時機為使用者詢問付款狀態、付款方式、是否已扣款、或指定 booking ID / trip ID 的付款明細。同步更新 TOOLS_SCHEMA、`_execute_tool` dispatch，並補上 `query_payment_info` import。Why: 函式雖已在 `queries.py` 實作但未在 agent 中宣告，LLM 沒有任何觸發信號，導致所有付款相關問題無法回答。
  - Decision: 工具定義前加兩行英文 comment，說明 `queries.py` 採兩段式查詢（先查 `national_rail_booking_id`，查無再查 `metro_trip_id`），一個工具即可處理 BK… 與 MT… 兩種 ID，不依賴前綴假設。Why: 這是非直觀的設計，未來讀者容易誤以為應各網拆成獨立工具。
  - Decision: `_execute_tool` 中 `query_payment_info` 回傳 `None` 時，替換為 `{"error": "No payment record found for <id>"}` 再序列化，並在該行加上原因 comment。Why: `json.dumps(None)` 產生字串 `"null"`，LLM 無法區分「查無資料」與「欄位缺失」，無法向使用者給出有意義的錯誤訊息。
  - Decision: `get_payment_info` 分支改為先從 `params` 嘗試四個候選 key（`booking_id` → `query` → `id` → `booking_reference`），取到第一個非空值再呼叫 `query_payment_info(booking_id)`。Why: 實測發現 LLM 有時傳入 `query` 而非 `booking_id`，用 `**params` 展開會導致 `TypeError`；容錯 key 對應覆蓋已知的偏差命名，不依賴 LLM 每次傳對欄位名稱。
- [ ] (example) Metro schedule stop ordering: using `jsonb_array_elements` approach — easier to debug than containment operators

## Prompts That Worked

<!-- Share prompts that produced good output so teammates can reuse them. -->

### Schema design prompt that worked:
``
TODO — add a prompt here after your schema design workshop
``

### Query implementation prompt that worked:
``
I'm implementing a Python function for a PostgreSQL database project called TransitFlow.
Follow these rules strictly:

CODING CONVENTIONS:
- Use only the table and column names in the schema below — do not invent names
- Use the _connect() helper already defined in the module (returns a psycopg2 connection with autocommit=True)
- Use psycopg2.extras.RealDictCursor so rows come back as dicts
- Match the stub signature exactly — do not change parameter names, return type, or type hints
- Return [] (not None) when no rows found, unless the return type is Optional[dict] — then return None
- Use %s placeholders for all inputs — never f-strings or .format() inside SQL
- Wrap the cursor in: with _connect() as conn: with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
- Return [dict(row) for row in cur.fetchall()] for list results, dict(cur.fetchone()) for single-row results
- Do not add try/except unless the docstring explicitly asks for error handling
- Do not import anything — all needed imports are already at the top of the module

PROJECT CONTEXT:
- Two networks: city metro (stations MS01–MS20, schedules MS_SCH01–MS_SCH08) and national rail (NR01–NR10, NR_SCH01–NR_SCH08)
- All orders share a parent travel_orders table; national rail details live in bookings + booking_tickets; metro details live in metro_trip_purchases (+ metro_day_pass_trips for day passes)
- Fare formula (both networks): total = base_fare_usd + (per_stop_rate_usd × stops_travelled)
- booking_tickets.leg values: 'single', 'outbound', 'inbound'
- booking_tickets.status values: 'confirmed', 'completed', 'cancelled'
- travel_orders.status values: 'pending', 'confirmed', 'completed', 'cancelled'
- metro_trip_purchases.ticket_type values: 'single', 'day_pass'

STUB TO IMPLEMENT:
[paste the full stub function including its docstring]

SCHEMA (relevant tables only):
[paste only the CREATE TABLE statements your function will query — trim the rest]
``

### Code Review Prompt:
``
Review this Python database function from the TransitFlow project against 
the stub contract and schema below.

Check ALL of the following — report only real bugs, not style suggestions:

CORRECTNESS CHECKS:
1. Table & column names — does it use ONLY names that exist in the schema below?
   Flag any invented column or table name.

2. Return type & shape — does it match the stub's return type exactly?
   - list-returning functions must return [] (not None) when no rows found
   - Optional[dict]-returning functions must return None (not []) when not found
   - execute_ functions must return (True, dict) on success and (False, str) on failure
   - query_user_bookings must always return {"national_rail": [...], "metro": [...]}
     — both keys must be present even when empty

3. Connection pattern — does it follow this exact pattern for read-only functions?
     with _connect() as conn:
         with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
             cur.execute(...)
             return [dict(row) for row in cur.fetchall()]  # or fetchone()
   Flag if _connect() is missing, RealDictCursor is missing, or rows are not 
   converted to dict.

4. Write operation pattern — for execute_ functions only:
   - Must NOT use _connect() — must use psycopg2.connect(PG_DSN) directly
   - Must set conn.autocommit = False
   - ALL inserts (e.g. travel_orders + bookings + booking_tickets + payments) 
     must be committed in a single conn.commit() — not separate commits
   - Must have conn.rollback() in the except branch
   - Must return (False, error_string) on failure, never raise

5. SQL injection — are ALL user-supplied values passed as %s parameters?
   Flag any value concatenated directly into the SQL string.

6. Empty-result handling — does fetchone() result get checked for None before 
   calling dict() on it? A bare dict(cur.fetchone()) will crash if no row found.

7. Two-network logic (national rail vs metro):
   - National rail bookings use: travel_orders → bookings → booking_tickets
   - Metro purchases use: travel_orders → metro_trip_purchases
   - Day pass journeys use: metro_day_pass_trips (child of metro_trip_purchases)
   Flag if the wrong tables are used for the wrong network.

8. Fare arithmetic — if fare is calculated in Python, verify:
   total_fare_usd = base_fare_usd + (per_stop_rate_usd × stops_travelled)
   Flag if the formula is wrong or if fare is calculated in the wrong currency type.

9. Auth functions only — password handling:
   - Plain-text password storage = critical bug
   - Must use argon2 ph.hash() to store, ph.verify() to check
   - login_user must return None (not raise) on wrong password

10. Refund logic (execute_cancellation only):
    - Normal service → RF001: 100% if ≥48h, 75% if 24–48h, 50% if 2–24h, 0% if <2h
    - Express service → RF002: 100% if ≥48h, 50% if 24–48h, 0% if <24h
    - Must use the booking's scheduled departure_time + travel_date to calculate 
      hours_before_departure, not the current date alone
    Flag if the wrong policy is applied or if the time calculation is incorrect.

STUB (the contract):
[paste the original stub]

IMPLEMENTATION TO REVIEW:
[paste your code]

SCHEMA (relevant tables only):
[paste relevant CREATE TABLE statements]
``
### Debugging Prompt
``
I have a bug in a Python database function from the TransitFlow project.
Help me fix it without changing the function's signature, return type, or logic 
that is already correct.

ERROR INFORMATION:
Full traceback:
[paste the full traceback]

ERROR TYPE (check one):
[ ] Runtime crash (exception raised during execution)
[ ] Wrong data returned (no crash but result is incorrect)
[ ] Transaction not committed (data not saved to database)
[ ] Silent failure (returns [] or None when data should exist)

FUNCTION WITH BUG:
[paste your code]

ORIGINAL STUB (the contract this function must satisfy):
[paste the original stub]

SCHEMA (relevant tables only):
[paste relevant CREATE TABLE statements]

WHAT I EXPECTED:
[one sentence for example：
"Should return a list of available seats for schedule NR_SCH01 on 2026-05-01
in standard class, but it returns an empty list instead."]

WHAT ACTUALLY HAPPENED:
[one sentence for example：
"Returns [] even though the database has confirmed seats for that date."]

CONSTRAINTS — the fix must:
1. Keep the same function signature (parameter names, return type)
2. Use only table/column names that exist in the schema above
3. Use _connect() + RealDictCursor for read-only functions
   OR psycopg2.connect(PG_DSN) with manual commit/rollback for execute_ functions
4. Return [] not None for list-returning functions when no rows found
5. Return None not [] for Optional[dict]-returning functions when not found
6. Keep ALL user inputs as %s parameters — no f-strings in SQL

KNOWN PROJECT-SPECIFIC PITFALLS (check if any apply to this bug):
[ ] fetchone() called without checking for None first
[ ] Two tables joined in wrong order (national rail vs metro tables mixed up)
[ ] Fare formula wrong: should be base_fare + (per_stop_rate × stops_travelled)
[ ] execute_ function used _connect() instead of psycopg2.connect(PG_DSN)
[ ] Multiple conn.commit() calls instead of one atomic commit
[ ] Password stored as plain text instead of argon2 hash
[ ] Refund hours calculated from today's date instead of scheduled departure datetime
[ ] stop_order / stop_sequence used incorrectly to determine direction of travel
[ ] Day pass trips queried from metro_trip_purchases instead of metro_day_pass_trips

OUTPUT FORMAT:
1. Identify the root cause in one sentence
2. Show only the fixed code (complete function)
3. Add a one-line comment on the line that was changed explaining what was wrong
Do NOT rewrite parts that were already correct.
``
