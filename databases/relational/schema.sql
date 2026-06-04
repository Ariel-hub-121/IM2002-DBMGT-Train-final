-- ============================================================
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
CREATE INDEX IF NOT EXISTS idx_policy_documents_embedding ON policy_documents USING hnsw (embedding vector_cosine_ops);