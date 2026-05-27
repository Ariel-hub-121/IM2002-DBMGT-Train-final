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
  ```python
  with _connect() as conn:
      with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
          cur.execute("SELECT ...", (param,))
          return [dict(row) for row in cur.fetchall()]
  ```
- **Graph pattern:** Use `_driver()` helper + session:
  ```python
  with _driver() as driver:
      with driver.session() as session:
          result = session.run("MATCH ...", station_id=station_id)
          return [dict(record) for record in result]
  ```

## Agreed Relational Schema

<!-- ============================================================
  FILL THIS IN after your team completes the schema design workshop.
  Paste your final CREATE TABLE statements here.
  ============================================================ -->

```sql
-- TODO: paste your final schema.sql contents here after team review
-- ============================================================
-- TransitFlow — final_v.4.sql
--
-- 基於 final_v.3.sql 的修正：
--   修正C（必改）→ booking_tickets 補 status 欄位，唯一索引排除已取消的票：
--            原索引 WHERE seat_pk IS NOT NULL 會讓取消的票永久鎖住座位，
--            上線後每一張取消票都會吃掉一個座位配額。
--            新增 status VARCHAR(20) CHECK IN ('confirmed','completed','cancelled')，
--            唯一索引改為 WHERE seat_pk IS NOT NULL AND status != 'cancelled'。
--            同時補上 cancelled_at TIMESTAMPTZ（status='cancelled'時必填），
--            供 RF001/RF002 退款時間窗口（hours_before_departure）計算使用。
--   修正D → metro_trip_purchases 補 cancelled_at TIMESTAMPTZ：
--            供退款稽核與統計分析使用。
--            加 chk_metro_cancelled_at：cancelled_at 與 travelled_at 互斥，
--            防止「已乘車又被取消」的矛盾資料。
--
-- 沿用 final_v.1.sql 的所有修正（未變動部分照舊）：
--   修正1 → metro_travel_history 拆分為：
--              metro_trip_purchases（購買記錄，連 travel_orders）
--              metro_day_pass_trips（日票子行程事件，不連 travel_orders）
--   修正2 → customer_feedback 移除 user_id（透過 travel_orders JOIN 取得）
--   修正3 → 補回來回票起終點對稱觸發器 trg_return_ticket_symmetry
--   修正4 → payment_sources 兩個重疊 CHECK 合併為 chk_source_type_and_fields
--   修正5 → metro_trip_purchases 補回 chk_stops_or_daypass CHECK
--   修正6 → national_rail_schedule_stops 補上 effective_from < effective_to CHECK
--   修正7 → station_interchanges 補上方向正規化 CHECK，防止雙向重複
--   優化1 → bookings.ticket_count 改由觸發器自動同步（DEFAULT 0）
--   優化2 → station_interchanges 補上站點查詢索引
--   優化3 → bookings 補回 return_travel_date 便利欄位（v.1 已有欄位，v.2 補強約束與觸發器）
--   優化4 → travel_orders 補上 created_at 索引
-- ============================================================


-- ============================================================
-- 使用者
-- ============================================================

CREATE TABLE "users" (
  "user_id"       VARCHAR(20)  PRIMARY KEY,
  "full_name"     VARCHAR(100) NOT NULL,
  "email"         VARCHAR(100) NOT NULL UNIQUE,
  "phone"         VARCHAR(50),
  "date_of_birth" DATE,
  "registered_at" TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  "is_active"     BOOLEAN      NOT NULL DEFAULT TRUE
);

COMMENT ON TABLE "users" IS '已註冊乘客帳號';

CREATE TABLE "user_security" (
  "user_id"            VARCHAR(20)  PRIMARY KEY,
  "password_hash"      VARCHAR(255) NOT NULL,
  "password_salt"      VARCHAR(255) NOT NULL,
  "secret_question"    VARCHAR(255),
  "secret_answer_hash" VARCHAR(255),
  "secret_answer_salt" VARCHAR(255)
);

COMMENT ON TABLE  "user_security"                      IS '使用者安全驗證表（敏感資訊與基本資料分離）';
COMMENT ON COLUMN "user_security"."password_hash"      IS '密碼雜湊值。建議演算法：bcrypt（cost≥12）或 Argon2id。';
COMMENT ON COLUMN "user_security"."password_salt"      IS '密碼專用隨機 salt，每位使用者唯一，建議 32 bytes 以 hex/base64 儲存。';
COMMENT ON COLUMN "user_security"."secret_answer_hash" IS '密保答案雜湊值，與密碼採相同演算法處理。';
COMMENT ON COLUMN "user_security"."secret_answer_salt" IS '密保答案專用 salt。';


-- ============================================================
-- 捷運站點
-- ============================================================

CREATE TABLE "metro_stations" (
  "station_id" VARCHAR(10)  PRIMARY KEY,
  "name"       VARCHAR(100) NOT NULL
);

COMMENT ON TABLE "metro_stations" IS '城市地鐵站點主資料';

CREATE TABLE "metro_station_lines" (
  "station_id" VARCHAR(10) NOT NULL,
  "line_name"  VARCHAR(20) NOT NULL,
  PRIMARY KEY ("station_id", "line_name")
);

COMMENT ON TABLE "metro_station_lines" IS '地鐵站所屬路線（一站可屬多線）';


-- ============================================================
-- 國鐵站點
-- ============================================================

CREATE TABLE "national_rail_stations" (
  "station_id" VARCHAR(10)  PRIMARY KEY,
  "name"       VARCHAR(100) NOT NULL
);

COMMENT ON TABLE "national_rail_stations" IS '國鐵站點主資料';

CREATE TABLE "national_rail_station_lines" (
  "station_id" VARCHAR(10) NOT NULL,
  "line_name"  VARCHAR(20) NOT NULL,
  PRIMARY KEY ("station_id", "line_name")
);

COMMENT ON TABLE "national_rail_station_lines" IS '國鐵站所屬路線（一站可屬多線）';


-- ============================================================
-- 換乘站
-- [修正7] 補上方向正規化 CHECK：
--   強制 network_a < network_b（字母序），
--   同網路時強制 station_id_a < station_id_b，
--   確保同一換乘關係只能用一個方向儲存，防止 (A,B) 與 (B,A) 重複插入。
--   'metro' < 'national_rail'，因此跨網路換乘 metro 永遠在 a 位置。
-- ============================================================

CREATE TABLE "station_interchanges" (
  "interchange_id"    BIGSERIAL   PRIMARY KEY,
  "network_a"         VARCHAR(20) NOT NULL CHECK ("network_a" IN ('metro', 'national_rail')),
  "station_id_a"      VARCHAR(10) NOT NULL,
  "network_b"         VARCHAR(20) NOT NULL CHECK ("network_b" IN ('metro', 'national_rail')),
  "station_id_b"      VARCHAR(10) NOT NULL,
  "transfer_time_min" INT         NOT NULL DEFAULT 5 CHECK ("transfer_time_min" >= 0),

  UNIQUE ("network_a", "station_id_a", "network_b", "station_id_b"),

  -- [修正7] 方向正規化：防止同一換乘關係以兩個方向重複儲存
  CONSTRAINT "chk_canonical_direction" CHECK (
    "network_a" < "network_b"
    OR ("network_a" = "network_b" AND "station_id_a" < "station_id_b")
  )
);

COMMENT ON TABLE  "station_interchanges" IS
  '跨網路（或同網路）換乘關係。'
  'chk_canonical_direction 確保方向唯一：network_a 字母序 ≤ network_b，同網路時 station_id_a < station_id_b。'
  'station_id_a/b 無 DB 層 FK，完整性請由應用層或定期 audit query 確保。';
COMMENT ON COLUMN "station_interchanges"."network_a"    IS '字母序較小的網路（metro < national_rail），正規化方向的起點。';
COMMENT ON COLUMN "station_interchanges"."network_b"    IS '字母序較大的網路，正規化方向的終點。';


-- ============================================================
-- 捷運時刻表
-- ============================================================

CREATE TABLE "metro_schedules" (
  "schedule_id"       VARCHAR(50)   PRIMARY KEY,
  "line_name"         VARCHAR(20)   NOT NULL,
  "direction"         VARCHAR(50)   NOT NULL,
  "origin_station_id" VARCHAR(10),
  "dest_station_id"   VARCHAR(10),
  "first_train_time"  TIME          NOT NULL,
  "last_train_time"   TIME          NOT NULL,
  "base_fare_usd"     NUMERIC(10,2) NOT NULL CHECK ("base_fare_usd"     >= 0),
  "per_stop_rate_usd" NUMERIC(10,2) NOT NULL CHECK ("per_stop_rate_usd" >= 0),
  "frequency_min"     INT           NOT NULL CHECK ("frequency_min"     >  0),
  CHECK ("first_train_time" < "last_train_time")
);

COMMENT ON TABLE "metro_schedules" IS '地鐵班次定義（含票價計算基準）';

CREATE TABLE "metro_schedule_stops" (
  "schedule_id"               VARCHAR(50) NOT NULL,
  "station_id"                VARCHAR(10) NOT NULL,
  "stop_sequence"             INT         NOT NULL CHECK ("stop_sequence" > 0),
  "travel_time_from_origin_min" INT       NOT NULL CHECK ("travel_time_from_origin_min" >= 0),
  PRIMARY KEY ("schedule_id", "station_id"),
  UNIQUE ("schedule_id", "stop_sequence")
);

COMMENT ON TABLE "metro_schedule_stops" IS '地鐵班次停靠站序';

CREATE TABLE "metro_schedule_operating_days" (
  "schedule_id" VARCHAR(50) NOT NULL,
  "day_of_week" VARCHAR(3)  NOT NULL,
  PRIMARY KEY ("schedule_id", "day_of_week"),
  CHECK ("day_of_week" IN ('mon','tue','wed','thu','fri','sat','sun'))
);

COMMENT ON TABLE "metro_schedule_operating_days" IS '地鐵班次營運日';


-- ============================================================
-- 國鐵時刻表
-- ============================================================

CREATE TABLE "national_rail_schedules" (
  "schedule_id"            VARCHAR(20) PRIMARY KEY,
  "line_name"              VARCHAR(20) NOT NULL,
  "service_type"           VARCHAR(20) NOT NULL CHECK ("service_type" IN ('normal','express')),
  "direction"              VARCHAR(50) NOT NULL,
  "origin_station_id"      VARCHAR(10),
  "destination_station_id" VARCHAR(10),
  "first_train_time"       TIME        NOT NULL,
  "last_train_time"        TIME        NOT NULL,
  "frequency_min"          INT         NOT NULL CHECK ("frequency_min" > 0),
  CHECK ("first_train_time" < "last_train_time")
);

COMMENT ON TABLE "national_rail_schedules" IS
  '國鐵班次定義。service_type 決定退款政策（normal→RF001, express→RF002）。';

-- [修正6] 補上 effective_from < effective_to 合法性約束：
--   防止結束日早於開始日的無效區間被插入，
--   確保「查詢目前生效停靠站」的 WHERE 條件永遠能得到正確結果。
CREATE TABLE "national_rail_schedule_stops" (
  "id"                          SERIAL      PRIMARY KEY,
  "schedule_id"                 VARCHAR(20),
  "station_id"                  VARCHAR(10),
  "stop_order"                  INT         NOT NULL,
  "travel_time_from_origin_min" INT         NOT NULL,
  "is_stop"                     BOOLEAN     NOT NULL DEFAULT TRUE,
  "effective_from"              DATE        NOT NULL DEFAULT '2000-01-01',
  "effective_to"                DATE,

  -- [修正6] 區間合法性：結束日必須晚於開始日（NULL 表示目前仍生效，允許）
  CONSTRAINT "chk_effective_range" CHECK (
    "effective_to" IS NULL OR "effective_from" < "effective_to"
  )
);

COMMENT ON COLUMN "national_rail_schedule_stops"."is_stop"        IS 'true = 停靠；false = 快車途經但不停靠。';
COMMENT ON COLUMN "national_rail_schedule_stops"."effective_from" IS '此停靠狀態的生效起始日（含）。';
COMMENT ON COLUMN "national_rail_schedule_stops"."effective_to"   IS '此停靠狀態的生效終止日（不含）；NULL 表示目前仍生效。';

CREATE TABLE "national_rail_schedule_operating_days" (
  "schedule_id" VARCHAR(20) NOT NULL,
  "day_of_week" VARCHAR(3)  NOT NULL,
  PRIMARY KEY ("schedule_id", "day_of_week"),
  CHECK ("day_of_week" IN ('mon','tue','wed','thu','fri','sat','sun'))
);

COMMENT ON TABLE "national_rail_schedule_operating_days" IS '國鐵班次營運日';

CREATE TABLE "national_rail_schedule_fares" (
  "schedule_id"       VARCHAR(20)   NOT NULL,
  "fare_class"        VARCHAR(20)   NOT NULL CHECK ("fare_class" IN ('standard','first')),
  "base_fare_usd"     NUMERIC(10,2) NOT NULL CHECK ("base_fare_usd"     >= 0),
  "per_stop_rate_usd" NUMERIC(10,2) NOT NULL CHECK ("per_stop_rate_usd" >= 0),
  PRIMARY KEY ("schedule_id", "fare_class")
);

COMMENT ON TABLE "national_rail_schedule_fares" IS '國鐵班次票價（依艙等；不同班次費率可不同）';


-- ============================================================
-- 國鐵座位配置
-- ============================================================

CREATE TABLE "national_rail_seat_layouts" (
  "layout_id"   VARCHAR(20) PRIMARY KEY,
  "schedule_id" VARCHAR(20) NOT NULL UNIQUE
);

COMMENT ON TABLE "national_rail_seat_layouts" IS '國鐵班次座位佈局（1:1 對應班次）';

CREATE TABLE "national_rail_coaches" (
  "coach_id"   BIGSERIAL   PRIMARY KEY,
  "layout_id"  VARCHAR(20) NOT NULL,
  "coach_name" VARCHAR(10) NOT NULL,
  "fare_class" VARCHAR(20) NOT NULL CHECK ("fare_class" IN ('standard','first')),
  UNIQUE ("layout_id", "coach_name")
);

COMMENT ON TABLE "national_rail_coaches" IS '車廂定義';

CREATE TABLE "national_rail_seats" (
  "seat_pk"     BIGSERIAL   PRIMARY KEY,
  "coach_id"    BIGINT      NOT NULL,
  "seat_code"   VARCHAR(10) NOT NULL,
  "seat_row"    INT         NOT NULL CHECK ("seat_row" > 0),
  "seat_column" VARCHAR(5)  NOT NULL,
  UNIQUE ("coach_id", "seat_code")
);

COMMENT ON TABLE "national_rail_seats" IS '座位定義';


-- ============================================================
-- 訂單父表
-- ============================================================

CREATE TABLE "travel_orders" (
  "order_id"   VARCHAR(20)   PRIMARY KEY,
  "user_id"    VARCHAR(20)   NOT NULL,
  "order_type" VARCHAR(20)   NOT NULL CHECK ("order_type" IN ('national_rail','metro')),
  "amount_usd" NUMERIC(10,2) NOT NULL CHECK ("amount_usd" >= 0),
  "status"     VARCHAR(20)   NOT NULL DEFAULT 'pending'
    CHECK ("status" IN ('pending','confirmed','completed','cancelled')),
  "created_at" TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE "travel_orders" IS
  '所有訂單共同父表。order_type 決定子層在 bookings（國鐵）或 metro_trip_purchases（地鐵）。';


-- ============================================================
-- 國鐵訂單
-- [優化3] bookings 補回 return_travel_date 便利欄位
-- [優化1] ticket_count 改由觸發器自動同步，DEFAULT 0
-- ============================================================

CREATE TABLE "bookings" (
  "booking_id"         VARCHAR(20) PRIMARY KEY,   -- = travel_orders.order_id
  "ticket_count"       INT         NOT NULL DEFAULT 0,
  "return_travel_date" DATE                        -- NULL = 單程；有值 = 來回票回程日期
  -- 注意：return_travel_date 的正確性完全由觸發器 trg_sync_return_travel_date 維護。
  -- 原 chk_return_date_with_return_ticket CHECK 已移除：
  --   該約束邏輯為「return_travel_date IS NOT NULL OR ticket_count = 0」，
  --   實際上只要 ticket_count = 0（訂單剛建立）即可繞過，無法防止
  --   「ticket_count > 0 且含 inbound 票但 return_travel_date 為 NULL」的情況，
  --   形同無效。資料正確性請依賴觸發器，不保留誤導性的假約束。
);

COMMENT ON TABLE  "bookings"                       IS '國鐵訂單頭（子層）。ticket_count 與 return_travel_date 由觸發器自動維護。';
COMMENT ON COLUMN "bookings"."booking_id"          IS '與 travel_orders.order_id 1:1，兼作 FK。';
COMMENT ON COLUMN "bookings"."ticket_count"        IS '此訂單所含票券數，由 trg_sync_ticket_count 觸發器自動同步。';
COMMENT ON COLUMN "bookings"."return_travel_date"  IS '來回票回程日期（快速查詢用）；單程票為 NULL。由 trg_sync_return_travel_date 觸發器自動同步；回程詳情見 booking_tickets WHERE leg=''inbound''。';

CREATE TABLE "booking_tickets" (
  "ticket_id"              SERIAL      PRIMARY KEY,
  "booking_id"             VARCHAR(20),
  "schedule_id"            VARCHAR(20),
  "origin_station_id"      VARCHAR(10),
  "destination_station_id" VARCHAR(10),
  "seat_pk"                BIGINT,
  "travel_date"            DATE        NOT NULL,
  "departure_time"         TIME        NOT NULL,
  "ticket_type"            VARCHAR(20) NOT NULL CHECK ("ticket_type" IN ('single','return')),
  "fare_class"             VARCHAR(20) NOT NULL CHECK ("fare_class"  IN ('standard','first')),
  "coach"                  VARCHAR(10) NOT NULL,
  "seat_code"              VARCHAR(10) NOT NULL,
  "stops_travelled"        INT,
  "travelled_at"           TIMESTAMPTZ,
  "leg"                    VARCHAR(10) NOT NULL DEFAULT 'single'
    CHECK ("leg" IN ('outbound','inbound','single')),

  -- [問題1修正] 票券狀態：取消的票不應佔用座位，唯一索引需排除 cancelled
  "status"       VARCHAR(20) NOT NULL DEFAULT 'confirmed'
    CHECK ("status" IN ('confirmed','completed','cancelled')),
  -- [問題2修正] 取消時間：支援退款時間窗口計算（RF001/RF002 的 hours_before_departure 判斷）
  "cancelled_at" TIMESTAMPTZ,

  -- cancelled_at 只在 status = 'cancelled' 時才有意義
  CONSTRAINT "chk_cancelled_at_consistency" CHECK (
    ("status" = 'cancelled' AND "cancelled_at" IS NOT NULL)
    OR
    ("status" != 'cancelled' AND "cancelled_at" IS NULL)
  ),

  CONSTRAINT "chk_leg_matches_ticket_type" CHECK (
    ("ticket_type" = 'single' AND "leg" = 'single')
    OR
    ("ticket_type" = 'return' AND "leg" IN ('outbound','inbound'))
  ),

  CHECK ("origin_station_id" <> "destination_station_id")
);

COMMENT ON COLUMN "booking_tickets"."seat_pk"       IS 'FK → national_rail_seats.seat_pk，ON DELETE SET NULL 保留票務歷史。';
COMMENT ON COLUMN "booking_tickets"."leg"           IS 'single = 單程票；outbound = 來回票去程；inbound = 來回票回程。去回程各自獨立一列，可有不同班次、日期、座位。';
COMMENT ON COLUMN "booking_tickets"."coach"         IS '反正規化自 national_rail_coaches.coach_name，方便顯示用。';
COMMENT ON COLUMN "booking_tickets"."seat_code"     IS '反正規化自 national_rail_seats.seat_code，方便顯示用。';
COMMENT ON COLUMN "booking_tickets"."status"        IS 'confirmed = 已訂位；completed = 已乘車；cancelled = 已取消。取消的票不佔用座位（見唯一索引條件）。';
COMMENT ON COLUMN "booking_tickets"."cancelled_at"  IS '取消時間。status = cancelled 時必填，其餘為 NULL。用於 RF001/RF002 退款時間窗口（hours_before_departure）計算。';

-- [問題1修正] 已取消的票不佔座位：唯一索引排除 status = 'cancelled'
-- 原索引：WHERE seat_pk IS NOT NULL
-- 新索引：WHERE seat_pk IS NOT NULL AND status != 'cancelled'
CREATE UNIQUE INDEX "uq_booking_tickets_seat"
  ON "booking_tickets" ("schedule_id", "travel_date", "departure_time", "seat_pk")
  WHERE "seat_pk" IS NOT NULL AND "status" != 'cancelled';

-- ============================================================
-- booking_tickets 觸發器
-- （必須在 CREATE TABLE booking_tickets 之後定義）
-- ============================================================

-- [修正3] 來回票起終點對稱觸發器
-- 插入 inbound 票時，驗證起終點與同訂單的 outbound 票互為對調
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
          '來回票起終點不對稱：去程 (%) → (%)，回程 (%) → (%)。回程起終點必須與去程互換。',
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
  '確保同一訂單的 inbound 票起終點與 outbound 票互為對調。';

-- [優化1] ticket_count 自動同步觸發器
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
  'booking_tickets 每次 INSERT/DELETE 時自動更新 bookings.ticket_count，確保反正規化快取一致。';

-- [修正1] return_travel_date 自動同步觸發器
-- inbound 票 INSERT / UPDATE travel_date → 將回程日期寫入 bookings.return_travel_date
-- inbound 票 DELETE               → 清空 bookings.return_travel_date（還原為單程狀態）
CREATE OR REPLACE FUNCTION sync_return_travel_date()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    -- 刪除 inbound 票：確認同訂單無其他 inbound 票後才清空
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

  -- INSERT 或 UPDATE：只處理 inbound 票
  IF NEW.leg = 'inbound' THEN
    UPDATE bookings
       SET return_travel_date = NEW.travel_date
     WHERE booking_id = NEW.booking_id;
  END IF;

  -- 若 UPDATE 將 leg 從 inbound 改為其他值，清空回程日期
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
  'booking_tickets 的 inbound 票 INSERT/UPDATE/DELETE 時，自動同步 bookings.return_travel_date。'
  'DELETE 時若同訂單已無其他 inbound 票則清空，確保單程票回程日期永遠為 NULL。';


-- ============================================================
-- 捷運乘車記錄
-- [修正1] 拆分為：
--   metro_trip_purchases  → 購買記錄（單程票 + 日票主記錄），連 travel_orders
--   metro_day_pass_trips  → 日票子行程事件，不是訂單，不連 travel_orders
-- ============================================================

-- [修正1-A] 購買記錄：每次付費行為在此建一列
-- [修正5] 補回 chk_stops_or_daypass：單程票 stops_travelled 不得為 NULL
CREATE TABLE "metro_trip_purchases" (
  "purchase_id"            VARCHAR(20) PRIMARY KEY,   -- = travel_orders.order_id
  "schedule_id"            VARCHAR(50) NOT NULL,
  "origin_station_id"      VARCHAR(10) NOT NULL,
  "destination_station_id" VARCHAR(10) NOT NULL,
  "travel_date"            DATE        NOT NULL,
  "ticket_type"            VARCHAR(20) NOT NULL CHECK ("ticket_type" IN ('single','day_pass')),
  "stops_travelled"        INT         CHECK ("stops_travelled" >= 0),
  "purchased_at"           TIMESTAMPTZ NOT NULL,
  "travelled_at"           TIMESTAMPTZ,
  -- [問題2修正] 取消時間：RF003/RF004 退款判斷（journey has not commenced）
  --   主要靠 status + travelled_at 判斷，cancelled_at 供統計分析與稽核使用
  "cancelled_at"           TIMESTAMPTZ,

  CHECK ("origin_station_id" <> "destination_station_id"),

  -- [修正5] 單程票必須有站數；日票主記錄可為 NULL（實際站數記錄在子行程）
  CONSTRAINT "chk_stops_or_daypass" CHECK (
    "ticket_type" = 'day_pass' OR "stops_travelled" IS NOT NULL
  ),

  -- cancelled_at 與 travel_orders.status = 'cancelled' 一致性由應用層維護
  -- （metro_trip_purchases 本身無 status 欄，status 在父表 travel_orders）
  CONSTRAINT "chk_metro_cancelled_at" CHECK (
    "cancelled_at" IS NULL OR "travelled_at" IS NULL
  )
);

COMMENT ON TABLE  "metro_trip_purchases"                  IS '捷運購買記錄（子層）。單程票與日票主記錄各一列，連結 travel_orders。';
COMMENT ON COLUMN "metro_trip_purchases"."purchase_id"    IS '與 travel_orders.order_id 1:1，兼作 FK。';
COMMENT ON COLUMN "metro_trip_purchases"."stops_travelled" IS '單程票必填；日票此欄為 NULL，站數記錄在 metro_day_pass_trips。';
COMMENT ON COLUMN "metro_trip_purchases"."cancelled_at"   IS '取消時間。status（在父表 travel_orders）= cancelled 時由應用層填入；供退款稽核與統計分析使用。cancelled_at 與 travelled_at 互斥（chk_metro_cancelled_at）。';

-- [修正1-B] 日票子行程：乘車事件，不是訂單，purchase_id 指向主購買記錄
CREATE TABLE "metro_day_pass_trips" (
  "trip_id"                VARCHAR(20) PRIMARY KEY,
  "purchase_id"            VARCHAR(20) NOT NULL,
  "schedule_id"            VARCHAR(50) NOT NULL,
  "origin_station_id"      VARCHAR(10) NOT NULL,
  "destination_station_id" VARCHAR(10) NOT NULL,
  "stops_travelled"        INT         NOT NULL CHECK ("stops_travelled" >= 0),
  "travelled_at"           TIMESTAMPTZ NOT NULL,

  CHECK ("origin_station_id" <> "destination_station_id")
);

COMMENT ON TABLE  "metro_day_pass_trips"               IS '日票子行程事件。不是訂單，不連 travel_orders；透過 purchase_id 指向主購買記錄。';
COMMENT ON COLUMN "metro_day_pass_trips"."purchase_id" IS 'FK → metro_trip_purchases.purchase_id，指向此子行程所屬的日票主記錄。';


-- ============================================================
-- 付款
-- [修正4] payment_sources 兩個重疊 CHECK 合併為 chk_source_type_and_fields
--   未來新增來源只需在此處加一個 OR 分支，不需同步修改兩個地方
-- ============================================================

CREATE TABLE "payments" (
  "payment_id" VARCHAR(20)   PRIMARY KEY,
  "amount_usd" NUMERIC(10,2) NOT NULL CHECK ("amount_usd" >= 0),
  "method"     VARCHAR(20)   NOT NULL CHECK ("method" IN ('credit_card','debit_card','ewallet')),
  "status"     VARCHAR(20)   NOT NULL CHECK ("status" IN ('pending','paid','refunded','failed')),
  "paid_at"    TIMESTAMPTZ
);

COMMENT ON TABLE "payments" IS '付款主表（來源無關）。來源 FK 集中在 payment_sources。';

CREATE TABLE "payment_sources" (
  "payment_id"               VARCHAR(20) PRIMARY KEY,
  "source_type"              VARCHAR(30) NOT NULL,
  "national_rail_booking_id" VARCHAR(20),
  "metro_trip_id"            VARCHAR(20),
  -- 未來擴充欄位加在此處，例如：
  -- "monthly_pass_id" VARCHAR(20),

  -- [修正4] 合併後的單一 CHECK：同時確保「恰好一個非 NULL」與「source_type 一致」
  --   新增來源時只需在此處加一個 OR 分支
  CONSTRAINT "chk_source_type_and_fields" CHECK (
    ("source_type" = 'national_rail_booking'
      AND "national_rail_booking_id" IS NOT NULL
      AND "metro_trip_id"            IS NULL)
    OR
    ("source_type" = 'metro_trip'
      AND "metro_trip_id"            IS NOT NULL
      AND "national_rail_booking_id" IS NULL)
    -- 新增來源範例：
    -- OR
    -- ("source_type" = 'monthly_pass'
    --   AND "monthly_pass_id"          IS NOT NULL
    --   AND "national_rail_booking_id" IS NULL
    --   AND "metro_trip_id"            IS NULL)
  ),

  UNIQUE ("national_rail_booking_id"),
  UNIQUE ("metro_trip_id")
);

COMMENT ON TABLE  "payment_sources"               IS '付款來源路由表。新增來源只需加欄位並在 chk_source_type_and_fields 增加一個 OR 分支。';
COMMENT ON COLUMN "payment_sources"."source_type" IS '來源類型識別碼，必須與非 NULL 欄位完全對應。';


-- ============================================================
-- 客服回饋
-- [修正2] 移除 user_id（透過 travel_orders JOIN 取得，避免與 travel_orders.user_id 不一致）
-- ============================================================

CREATE TABLE "customer_feedback" (
  "feedback_id"  VARCHAR(20) PRIMARY KEY,
  "order_id"     VARCHAR(20) NOT NULL,
  "rating"       INT         NOT NULL CHECK ("rating" BETWEEN 1 AND 5),
  "submitted_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE ("order_id")   -- 一筆訂單限一次回饋
);

COMMENT ON TABLE  "customer_feedback"          IS '乘客評分（每筆訂單限一次）。user_id 透過 travel_orders JOIN 取得，不重複儲存。';
COMMENT ON COLUMN "customer_feedback"."order_id" IS 'FK → travel_orders.order_id。查詢回饋者身份請 JOIN travel_orders.user_id。';

CREATE TABLE "feedback_comments" (
  "feedback_id"  VARCHAR(20) PRIMARY KEY,
  "comment_text" TEXT        NOT NULL
);

COMMENT ON TABLE "feedback_comments" IS '評分附帶文字留言（選填，與 customer_feedback 1:1）';


-- ============================================================
-- 索引
-- ============================================================

-- travel_orders
CREATE INDEX "idx_travel_orders_user_id" ON "travel_orders" ("user_id");
CREATE INDEX "idx_travel_orders_status"  ON "travel_orders" ("status");
-- [優化4] 補上 created_at 索引，支援月報、退款統計等時間範圍查詢
CREATE INDEX "idx_travel_orders_created_at" ON "travel_orders" ("created_at");

-- station_interchanges
-- [優化2] 補上雙向站點查詢索引，支援「查某站所有換乘夥伴」的高頻查詢
CREATE INDEX "idx_interchanges_a" ON "station_interchanges" ("network_a", "station_id_a");
CREATE INDEX "idx_interchanges_b" ON "station_interchanges" ("network_b", "station_id_b");

-- 國鐵停靠站
CREATE UNIQUE INDEX ON "national_rail_schedule_stops" ("schedule_id", "station_id", "effective_from");
CREATE UNIQUE INDEX ON "national_rail_schedule_stops" ("schedule_id", "stop_order",  "effective_from");
CREATE        INDEX "idx_schedule_stops_effective"
  ON "national_rail_schedule_stops" ("schedule_id", "is_stop", "effective_from", "effective_to");

-- booking_tickets
CREATE INDEX "idx_booking_tickets_booking" ON "booking_tickets" ("booking_id");
CREATE INDEX "idx_booking_tickets_leg"     ON "booking_tickets" ("booking_id", "leg");
CREATE INDEX "idx_booking_tickets_date"    ON "booking_tickets" ("schedule_id", "travel_date");

-- [優化3] 來回票回程日期索引
CREATE INDEX "idx_bookings_return_date"
  ON "bookings" ("return_travel_date")
  WHERE "return_travel_date" IS NOT NULL;

-- metro_trip_purchases
CREATE INDEX "idx_metro_purchases_travel_date"   ON "metro_trip_purchases" ("travel_date");
CREATE INDEX "idx_metro_purchases_user"
  ON "metro_trip_purchases" ("purchase_id");

-- metro_day_pass_trips
CREATE INDEX "idx_day_pass_trips_purchase" ON "metro_day_pass_trips" ("purchase_id");
CREATE INDEX "idx_day_pass_trips_date"     ON "metro_day_pass_trips" ("travelled_at");

-- payments / payment_sources
CREATE INDEX "idx_payment_sources_booking_id" ON "payment_sources" ("national_rail_booking_id");
CREATE INDEX "idx_payment_sources_metro_id"   ON "payment_sources" ("metro_trip_id");

-- customer_feedback
CREATE INDEX "idx_customer_feedback_order_id" ON "customer_feedback" ("order_id");


-- ============================================================
-- 外來鍵
-- ============================================================

-- 使用者
ALTER TABLE "user_security"
  ADD FOREIGN KEY ("user_id") REFERENCES "users" ("user_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- 捷運站點
ALTER TABLE "metro_station_lines"
  ADD FOREIGN KEY ("station_id") REFERENCES "metro_stations" ("station_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- 國鐵站點
ALTER TABLE "national_rail_station_lines"
  ADD FOREIGN KEY ("station_id") REFERENCES "national_rail_stations" ("station_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- 捷運時刻表
ALTER TABLE "metro_schedules"
  ADD FOREIGN KEY ("origin_station_id") REFERENCES "metro_stations" ("station_id")
  DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "metro_schedules"
  ADD FOREIGN KEY ("dest_station_id") REFERENCES "metro_stations" ("station_id")
  DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "metro_schedule_stops"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "metro_schedules" ("schedule_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "metro_schedule_stops"
  ADD FOREIGN KEY ("station_id") REFERENCES "metro_stations" ("station_id")
  DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "metro_schedule_operating_days"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "metro_schedules" ("schedule_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- 國鐵時刻表
ALTER TABLE "national_rail_schedules"
  ADD FOREIGN KEY ("origin_station_id") REFERENCES "national_rail_stations" ("station_id")
  DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "national_rail_schedules"
  ADD FOREIGN KEY ("destination_station_id") REFERENCES "national_rail_stations" ("station_id")
  DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "national_rail_schedule_stops"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "national_rail_schedules" ("schedule_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "national_rail_schedule_stops"
  ADD FOREIGN KEY ("station_id") REFERENCES "national_rail_stations" ("station_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "national_rail_schedule_operating_days"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "national_rail_schedules" ("schedule_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "national_rail_schedule_fares"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "national_rail_schedules" ("schedule_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- 國鐵座位
ALTER TABLE "national_rail_seat_layouts"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "national_rail_schedules" ("schedule_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "national_rail_coaches"
  ADD FOREIGN KEY ("layout_id") REFERENCES "national_rail_seat_layouts" ("layout_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "national_rail_seats"
  ADD FOREIGN KEY ("coach_id") REFERENCES "national_rail_coaches" ("coach_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- travel_orders
ALTER TABLE "travel_orders"
  ADD FOREIGN KEY ("user_id") REFERENCES "users" ("user_id")
  DEFERRABLE INITIALLY IMMEDIATE;

-- bookings（國鐵訂單頭）
ALTER TABLE "bookings"
  ADD FOREIGN KEY ("booking_id") REFERENCES "travel_orders" ("order_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

-- booking_tickets（國鐵票券明細）
ALTER TABLE "booking_tickets"
  ADD FOREIGN KEY ("booking_id") REFERENCES "bookings" ("booking_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "booking_tickets"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "national_rail_schedules" ("schedule_id")
  DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "booking_tickets"
  ADD FOREIGN KEY ("origin_station_id") REFERENCES "national_rail_stations" ("station_id")
  DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "booking_tickets"
  ADD FOREIGN KEY ("destination_station_id") REFERENCES "national_rail_stations" ("station_id")
  DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "booking_tickets"
  ADD FOREIGN KEY ("seat_pk") REFERENCES "national_rail_seats" ("seat_pk")
  ON DELETE SET NULL DEFERRABLE INITIALLY IMMEDIATE;

-- [修正1] metro_trip_purchases（捷運購買記錄）
ALTER TABLE "metro_trip_purchases"
  ADD FOREIGN KEY ("purchase_id") REFERENCES "travel_orders" ("order_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "metro_trip_purchases"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "metro_schedules" ("schedule_id")
  DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "metro_trip_purchases"
  ADD FOREIGN KEY ("origin_station_id") REFERENCES "metro_stations" ("station_id")
  DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "metro_trip_purchases"
  ADD FOREIGN KEY ("destination_station_id") REFERENCES "metro_stations" ("station_id")
  DEFERRABLE INITIALLY IMMEDIATE;

-- [修正1] metro_day_pass_trips（日票子行程）
ALTER TABLE "metro_day_pass_trips"
  ADD FOREIGN KEY ("purchase_id") REFERENCES "metro_trip_purchases" ("purchase_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "metro_day_pass_trips"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "metro_schedules" ("schedule_id")
  DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "metro_day_pass_trips"
  ADD FOREIGN KEY ("origin_station_id") REFERENCES "metro_stations" ("station_id")
  DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "metro_day_pass_trips"
  ADD FOREIGN KEY ("destination_station_id") REFERENCES "metro_stations" ("station_id")
  DEFERRABLE INITIALLY IMMEDIATE;

-- payments + payment_sources
ALTER TABLE "payment_sources"
  ADD FOREIGN KEY ("payment_id") REFERENCES "payments" ("payment_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "payment_sources"
  ADD FOREIGN KEY ("national_rail_booking_id") REFERENCES "bookings" ("booking_id")
  DEFERRABLE INITIALLY IMMEDIATE;

-- [修正1] metro_trip_id 現在指向 metro_trip_purchases（原 metro_travel_history）
ALTER TABLE "payment_sources"
  ADD FOREIGN KEY ("metro_trip_id") REFERENCES "metro_trip_purchases" ("purchase_id")
  DEFERRABLE INITIALLY IMMEDIATE;

-- customer_feedback
ALTER TABLE "customer_feedback"
  ADD FOREIGN KEY ("order_id") REFERENCES "travel_orders" ("order_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "feedback_comments"
  ADD FOREIGN KEY ("feedback_id") REFERENCES "customer_feedback" ("feedback_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

```

## Agreed Graph Schema

<!-- ============================================================
  FILL THIS IN after your team agrees on Neo4j node labels and
  relationship types.
  ============================================================ -->

```text
Node labels:
- Station (通用標籤，所有車站節點皆具備)
- Metro (捷運站專屬標籤，與 Station 搭配使用，例如 :Station:Metro)
- NationalRail (國鐵站專屬標籤，與 Station 搭配使用，例如 :Station:NationalRail)

Relationship types:
- METRO_LINK (捷運站之間的相鄰連線)
- RAIL_LINK (國鐵站之間的相鄰連線)
- INTERCHANGE_TO (捷運與國鐵之間的站內轉乘連線)

Key properties:
- Node properties: station_id (必須與 PostgreSQL 的 ID 完全一致), name, zone (適度冗餘，方便視覺化與除錯)
- Edge properties: travel_time_min (計算最短路徑權重用), line (所屬路線名稱，支援計算最少換線次數等進階查詢)

## Function Signatures We Are Implementing

These are fixed contracts. AI-generated code must match these signatures exactly.

### Relational (`databases/relational/queries.py`)

```python
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
```

### Graph (`databases/graph/queries.py`)

```python
def query_shortest_route(origin_id: str, destination_id: str, network: str = "auto") -> dict: ...
def query_cheapest_route(origin_id: str, destination_id: str, network: str = "auto", fare_class: str = "standard") -> dict: ...
def query_alternative_routes(origin_id, destination_id, avoid_station_id, network="auto", max_routes=3) -> list[list[dict]]: ...
def query_interchange_path(origin_id: str, destination_id: str) -> dict: ...
def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]: ...
def query_station_connections(station_id: str) -> list[dict]: ...
```

## Team Decisions Log

<!-- Add entries as you make decisions. Format: "Decision: X. Why: Y." -->

## Team Decisions Log

- [ ] Schema design: TODO — add your table/column decisions here
- [x] Graph schema:
  - Decision: Node labels 採用多重標籤 (`:Station:Metro` / `:Station:NationalRail`)。 Why: 兼具全網搜尋彈性與單一路網查詢的高效能。
  - Decision: Relationship types 明確區分連線 (`METRO_LINK`, `RAIL_LINK`, `INTERCHANGE_TO`)。 Why: 限制特定路網查詢時（例如避開火車網路）效能極佳。
  - Decision: Edge properties 儲存行車時間與路線 (`travel_time_min`, `line`)。 Why: 足以應付 Dijkstra 最短路徑演算法，且支援未來「最少換乘」等進階路徑計算。
  - Decision: Node properties 採用適度冗餘 (`station_id`, `name`, `zone`)。 Why: 確保與 PostgreSQL 對齊的同時，在 Neo4j Browser 視覺化檢視時可直接看到站名，大幅提升除錯效率。
- [ ] (example) Metro schedule stop ordering: using `jsonb_array_elements` approach — easier to debug than containment operators

## Prompts That Worked

<!-- Share prompts that produced good output so teammates can reuse them. -->

### Schema design prompt that worked:
```
TODO — add a prompt here after your schema design workshop
```

### Query implementation prompt that worked:
```
TODO — add after implementing your first function
```
