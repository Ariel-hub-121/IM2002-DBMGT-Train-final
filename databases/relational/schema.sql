-- ============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
--
--  TWO ROLES:
--    1. Relational  → dual-network transit data you design below
--    2. Vector      → policy documents for RAG (provided — do not modify)
-- ============================================================

-- ============================================================
--  STUDENT TASK — Design and create your relational tables here
--
--  Start from the mock data in train-mock-data/:
--    metro_stations.json, national_rail_stations.json
--    metro_schedules.json, national_rail_schedules.json
--    national_rail_seat_layouts.json
--    registered_users.json
--    bookings.json, metro_travel_history.json
--    payments.json, feedback.json
--
--  Think about:
--    - What tables do you need?
--    - What columns and data types?
--    - Which fields are primary keys? Which are foreign keys?
--    - What constraints make sense?
--
--  Apply your schema with:
--    docker-compose down -v && docker-compose up -d
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

-- 決策 7（Ruby）：密保答案與密碼同樣採 hash + salt 分開儲存
CREATE TABLE "user_security" (
  "user_id"            VARCHAR(20)  PRIMARY KEY,
  "password_hash"      VARCHAR(255) NOT NULL,
  "password_salt"      VARCHAR(255) NOT NULL,   -- 決策 9：VARCHAR(255)
  "secret_question"    VARCHAR(255),
  "secret_answer_hash" VARCHAR(255),
  "secret_answer_salt" VARCHAR(255)             -- 決策 7：密保答案也 hash+salt
);

COMMENT ON TABLE  "user_security"                    IS '使用者安全驗證表（敏感資訊與基本資料分離）';
COMMENT ON COLUMN "user_security"."password_hash"    IS '密碼雜湊值。建議演算法：bcrypt（cost >= 12）或 Argon2id。';
COMMENT ON COLUMN "user_security"."password_salt"    IS '密碼專用隨機 salt，每位使用者唯一，建議 32 bytes 以 hex/base64 儲存。';
COMMENT ON COLUMN "user_security"."secret_answer_hash" IS '密保答案雜湊值，與密碼採相同演算法處理。';
COMMENT ON COLUMN "user_security"."secret_answer_salt" IS '密保答案專用 salt。';


-- ============================================================
-- 捷運站點
-- 決策 1（Ariel）：移除 interchange 欄位，改由 station_interchanges 管理
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
-- 決策 1（Ariel）：同上，移除 interchange 欄位
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
-- 決策 1（Ariel）：獨立表，支援 metro ↔ rail、metro ↔ metro、rail ↔ rail
--   station_id_a / station_id_b 刻意不加 FK（跨兩個主表無法直接 FK），
--   完整性由應用層或定期 audit query 驗證。
-- ============================================================

CREATE TABLE "station_interchanges" (
  "interchange_id"    BIGSERIAL   PRIMARY KEY,
  "network_a"         VARCHAR(20) NOT NULL CHECK ("network_a" IN ('metro', 'national_rail')),
  "station_id_a"      VARCHAR(10) NOT NULL,
  "network_b"         VARCHAR(20) NOT NULL CHECK ("network_b" IN ('metro', 'national_rail')),
  "station_id_b"      VARCHAR(10) NOT NULL,
  "transfer_time_min" INT         NOT NULL DEFAULT 5 CHECK ("transfer_time_min" >= 0),
  UNIQUE ("network_a", "station_id_a", "network_b", "station_id_b"),
  CHECK  ("network_a" <> "network_b" OR "station_id_a" <> "station_id_b")
);

COMMENT ON TABLE "station_interchanges" IS
  '跨網路（或同網路）換乘關係。'
  '取代原本兩個站點表互相指向的循環 FK，避免 DEFERRABLE 帶來的插入順序問題。'
  'station_id_a/b 無 DB 層 FK，完整性請由應用層或定期 audit query 確保。';

-- 決策 2（Ruby/Sharon）：不保留 adjacency 表，圖結構關係留給圖資料庫（如 Neo4j）


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
  "base_fare_usd"     NUMERIC(10,2) NOT NULL CHECK ("base_fare_usd"    >= 0),
  "per_stop_rate_usd" NUMERIC(10,2) NOT NULL CHECK ("per_stop_rate_usd" >= 0),
  "frequency_min"     INT           NOT NULL CHECK ("frequency_min"    >  0),
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
  CHECK ("day_of_week" IN ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'))
);

COMMENT ON TABLE "metro_schedule_operating_days" IS '地鐵班次營運日';


-- ============================================================
-- 國鐵時刻表
-- 決策 8（Sharon）：schedule_stops 加入 is_stop 旗標與生效區間
-- ============================================================

CREATE TABLE "national_rail_schedules" (
  "schedule_id"            VARCHAR(20) PRIMARY KEY,
  "line_name"              VARCHAR(20) NOT NULL,
  "service_type"           VARCHAR(20) NOT NULL CHECK ("service_type" IN ('normal', 'express')),
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

-- 決策 8（Sharon）：
--   is_stop       = false 表示快車途經但不停靠
--   effective_from / effective_to 支援時刻表改版（NULL 表示目前仍生效）
CREATE TABLE "national_rail_schedule_stops" (
  "id"                          SERIAL      PRIMARY KEY,
  "schedule_id"                 VARCHAR(20),
  "station_id"                  VARCHAR(10),
  "stop_order"                  INT         NOT NULL,
  "travel_time_from_origin_min" INT         NOT NULL,
  "is_stop"                     BOOLEAN     NOT NULL DEFAULT TRUE,
  "effective_from"              DATE        NOT NULL DEFAULT '2000-01-01',
  "effective_to"                DATE                                -- NULL = 目前仍生效
);

COMMENT ON COLUMN "national_rail_schedule_stops"."is_stop"        IS 'true = 停靠；false = 快車途經但不停靠。';
COMMENT ON COLUMN "national_rail_schedule_stops"."effective_from" IS '此停靠狀態的生效起始日（含）。';
COMMENT ON COLUMN "national_rail_schedule_stops"."effective_to"   IS '此停靠狀態的生效終止日（不含）；NULL 表示目前仍生效。';

CREATE TABLE "national_rail_schedule_operating_days" (
  "schedule_id" VARCHAR(20) NOT NULL,
  "day_of_week" VARCHAR(3)  NOT NULL,
  PRIMARY KEY ("schedule_id", "day_of_week"),
  CHECK ("day_of_week" IN ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'))
);

COMMENT ON TABLE "national_rail_schedule_operating_days" IS '國鐵班次營運日';

-- 決策 6（Ariel）：票價依艙等獨立儲存，不同班次可有不同費率
CREATE TABLE "national_rail_schedule_fares" (
  "schedule_id"       VARCHAR(20)   NOT NULL,
  "fare_class"        VARCHAR(20)   NOT NULL CHECK ("fare_class" IN ('standard', 'first')),
  "base_fare_usd"     NUMERIC(10,2) NOT NULL CHECK ("base_fare_usd"    >= 0),
  "per_stop_rate_usd" NUMERIC(10,2) NOT NULL CHECK ("per_stop_rate_usd" >= 0),
  PRIMARY KEY ("schedule_id", "fare_class")
);

COMMENT ON TABLE "national_rail_schedule_fares" IS '國鐵班次票價（依艙等；不同班次費率可不同）';


-- ============================================================
-- 國鐵座位配置
-- （採用 Ariel 的 BIGSERIAL、inline UNIQUE、seat_code 命名與 fare_class CHECK）
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
  "fare_class" VARCHAR(20) NOT NULL CHECK ("fare_class" IN ('standard', 'first')),
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
-- 訂單
-- 決策 3（Ariel）：travel_orders 作為 national_rail 與 metro 的共同父表
-- 決策 4（Ruby/Sharon）：bookings（頭）+ booking_tickets（明細），支援一單多票
-- ============================================================

CREATE TABLE "travel_orders" (
  "order_id"   VARCHAR(20)   PRIMARY KEY,
  "user_id"    VARCHAR(20)   NOT NULL,
  "order_type" VARCHAR(20)   NOT NULL CHECK ("order_type" IN ('national_rail', 'metro')),
  "amount_usd" NUMERIC(10,2) NOT NULL CHECK ("amount_usd" >= 0),
  "status"     VARCHAR(20)   NOT NULL DEFAULT 'pending'
    CHECK ("status" IN ('pending', 'confirmed', 'completed', 'cancelled')),
  "created_at" TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE "travel_orders" IS
  '所有訂單共同父表。order_type 決定子層在 bookings（國鐵）或 metro_travel_history（地鐵）。';

-- 決策 4（Ruby/Sharon）：bookings 為國鐵訂單頭，不重複儲存 user_id/amount/status（已在 travel_orders）
CREATE TABLE "bookings" (
  "booking_id"   VARCHAR(20) PRIMARY KEY,   -- = travel_orders.order_id
  "ticket_count" INT         NOT NULL DEFAULT 1
);

COMMENT ON TABLE  "bookings"              IS '國鐵訂單頭（子層）。ticket_count 為 booking_tickets 列數的反正規化快取。';
COMMENT ON COLUMN "bookings"."booking_id" IS '與 travel_orders.order_id 1:1，兼作 FK。';

-- 決策 4（Ruby/Sharon）：每列為一張票，含座位與行程細節
-- 決策 5（Ariel）：unique index 涵蓋 schedule + date + departure_time + seat_pk，允許同座位不同班次/時段重複訂
-- Sharon：leg 欄位區分去程/回程/單程；seat_pk FK 加 ON DELETE SET NULL 保留訂單歷史
CREATE TABLE "booking_tickets" (
  "ticket_id"              SERIAL      PRIMARY KEY,
  "booking_id"             VARCHAR(20),
  "schedule_id"            VARCHAR(20),
  "origin_station_id"      VARCHAR(10),
  "destination_station_id" VARCHAR(10),
  "seat_pk"                BIGINT,                          -- FK → national_rail_seats，允許 NULL（座位設定被刪後保留票務記錄）
  "travel_date"            DATE        NOT NULL,
  "departure_time"         TIME        NOT NULL,
  "ticket_type"            VARCHAR(20) NOT NULL CHECK ("ticket_type" IN ('single', 'return')),
  "fare_class"             VARCHAR(20) NOT NULL CHECK ("fare_class"  IN ('standard', 'first')),
  "coach"                  VARCHAR(10) NOT NULL,            -- 反正規化，方便顯示
  "seat_code"              VARCHAR(10) NOT NULL,            -- 反正規化，方便顯示
  "stops_travelled"        INT,
  "travelled_at"           TIMESTAMPTZ,
  "leg"                    VARCHAR(10) NOT NULL DEFAULT 'single'
    CHECK ("leg" IN ('outbound', 'inbound', 'single')),

  -- leg 必須與 ticket_type 一致
  CONSTRAINT "chk_leg_matches_ticket_type" CHECK (
    ("ticket_type" = 'single' AND "leg" = 'single')
    OR
    ("ticket_type" = 'return' AND "leg" IN ('outbound', 'inbound'))
  ),

  CHECK ("origin_station_id" <> "destination_station_id")
);

COMMENT ON COLUMN "booking_tickets"."seat_pk"    IS 'FK → national_rail_seats.seat_pk，ON DELETE SET NULL 保留票務歷史。';
COMMENT ON COLUMN "booking_tickets"."leg"        IS 'single = 單程票；outbound = 來回票去程；inbound = 來回票回程。';
COMMENT ON COLUMN "booking_tickets"."coach"      IS '反正規化自 national_rail_coaches.coach_name，方便顯示用。';
COMMENT ON COLUMN "booking_tickets"."seat_code"  IS '反正規化自 national_rail_seats.seat_code，方便顯示用。';

-- 決策 5（Ariel）：同班次、同日、同出發時間的同一座位不能重複劃位（不含已取消）
-- 注意：僅在 seat_pk IS NOT NULL 時生效，避免座位設定被刪後影響舊資料
CREATE UNIQUE INDEX "uq_booking_tickets_seat"
  ON "booking_tickets" ("schedule_id", "travel_date", "departure_time", "seat_pk")
  WHERE "seat_pk" IS NOT NULL;


-- ============================================================
-- 捷運乘車紀錄
-- 決策 3（Ariel）：trip_id = travel_orders.order_id，user_id/amount/status 不重複儲存
-- ============================================================

CREATE TABLE "metro_travel_history" (
  "trip_id"                VARCHAR(20) PRIMARY KEY,   -- = travel_orders.order_id
  "schedule_id"            VARCHAR(50) NOT NULL,
  "origin_station_id"      VARCHAR(10) NOT NULL,
  "destination_station_id" VARCHAR(10) NOT NULL,
  "travel_date"            DATE        NOT NULL,
  "ticket_type"            VARCHAR(20) NOT NULL CHECK ("ticket_type" IN ('single', 'day_pass')),

  -- 日票子行程指向同日購買的主 trip_id；NULL = 本身是日票購買記錄或單程票
  "day_pass_ref"           VARCHAR(20),

  -- 單程票必填；日票主記錄與子行程可為 NULL
  "stops_travelled"        INT         CHECK ("stops_travelled" >= 0),
  "purchased_at"           TIMESTAMPTZ,
  "travelled_at"           TIMESTAMPTZ,

  CHECK ("origin_station_id" <> "destination_station_id"),

  CONSTRAINT "chk_day_pass_ref" CHECK (
    "day_pass_ref" IS NULL OR "ticket_type" = 'day_pass'
  )
);

COMMENT ON TABLE  "metro_travel_history"                IS '地鐵乘車記錄（子層）。day_pass 購買記錄的 day_pass_ref = NULL；當日後續行程的 day_pass_ref 指向同日購買的 trip_id。';
COMMENT ON COLUMN "metro_travel_history"."day_pass_ref" IS '日票子行程指向主購買記錄的 trip_id；NULL = 主記錄或單程票。';
COMMENT ON COLUMN "metro_travel_history"."purchased_at" IS '購票時間；日票子行程（day_pass_ref IS NOT NULL）無獨立購買，此欄為 NULL。';


-- ============================================================
-- 付款
-- Sharon：payments 主表本身來源無關（無 order_id），
--          來源路由集中在 payment_sources 中介表。
--          優點：未來新增付款來源（如月票）只需加 payment_sources 欄位，
--                不動 payments 主表。
--          先前「一訂單一付款」的決定透過 payment_sources 的
--          UNIQUE(national_rail_booking_id) / UNIQUE(metro_trip_id) 維持。
-- ============================================================

CREATE TABLE "payments" (
  "payment_id" VARCHAR(20)   PRIMARY KEY,
  "amount_usd" NUMERIC(10,2) NOT NULL CHECK ("amount_usd" >= 0),
  "method"     VARCHAR(20)   NOT NULL CHECK ("method" IN ('credit_card', 'debit_card', 'ewallet')),
  "status"     VARCHAR(20)   NOT NULL CHECK ("status" IN ('pending', 'paid', 'refunded', 'failed')),
  "paid_at"    TIMESTAMPTZ
);

COMMENT ON TABLE "payments" IS '付款主表（來源無關）。來源 FK 集中在 payment_sources；新增來源類型只需擴充 payment_sources。';

-- payment_sources：將付款與具體訂單來源（國鐵/捷運）連結的中介表
-- UNIQUE on source FK 欄位：維持「一筆訂單對應一筆付款」的業務規則
CREATE TABLE "payment_sources" (
  "payment_id"               VARCHAR(20) PRIMARY KEY,
  "source_type"              VARCHAR(30) NOT NULL,
  "national_rail_booking_id" VARCHAR(20),
  "metro_trip_id"            VARCHAR(20),
  -- 未來擴充：例如 "monthly_pass_id" VARCHAR(20),

  -- 恰好一個來源欄位非 NULL
  CONSTRAINT "chk_payment_exactly_one_source" CHECK (
    (CASE WHEN "national_rail_booking_id" IS NOT NULL THEN 1 ELSE 0 END +
     CASE WHEN "metro_trip_id"            IS NOT NULL THEN 1 ELSE 0 END)
    = 1
  ),

  -- source_type 必須與非 NULL 欄位一致，防止標籤與資料錯位
  CONSTRAINT "chk_source_type_match" CHECK (
    ("source_type" = 'national_rail_booking' AND "national_rail_booking_id" IS NOT NULL AND "metro_trip_id" IS NULL)
    OR
    ("source_type" = 'metro_trip'            AND "metro_trip_id"            IS NOT NULL AND "national_rail_booking_id" IS NULL)
  ),

  -- 一筆訂單只能對應一筆付款
  UNIQUE ("national_rail_booking_id"),
  UNIQUE ("metro_trip_id")
);

COMMENT ON TABLE  "payment_sources"               IS '付款來源路由表。payments 主表保持來源無關；所有來源 FK 集中於此。UNIQUE 約束確保一訂單一付款。';
COMMENT ON COLUMN "payment_sources"."source_type" IS '來源類型識別碼，必須與非 NULL 欄位一致：national_rail_booking 或 metro_trip。';


-- ============================================================
-- 客戶回饋
-- 決策 3（Ariel）：透過 travel_orders 統一，一個訂單限一次評分
-- ============================================================

CREATE TABLE "customer_feedback" (
  "feedback_id"  VARCHAR(20) PRIMARY KEY,
  "order_id"     VARCHAR(20) NOT NULL,
  "user_id"      VARCHAR(20) NOT NULL,
  "rating"       INT         NOT NULL CHECK ("rating" BETWEEN 1 AND 5),
  "submitted_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE ("order_id")   -- 一筆訂單限一次回饋
);

COMMENT ON TABLE "customer_feedback" IS '乘客評分（每筆訂單限一次）';

CREATE TABLE "feedback_comments" (
  "feedback_id"  VARCHAR(20) PRIMARY KEY,
  "comment_text" TEXT        NOT NULL
);

COMMENT ON TABLE "feedback_comments" IS '評分附帶文字留言（選填，與 customer_feedback 1:1）';


-- ============================================================
-- 索引
-- ============================================================

-- travel_orders 常用查詢
CREATE INDEX "idx_travel_orders_user_id" ON "travel_orders" ("user_id");
CREATE INDEX "idx_travel_orders_status"  ON "travel_orders" ("status");

-- 國鐵停靠站（決策 8：含 effective_from 的複合唯一索引）
CREATE UNIQUE INDEX ON "national_rail_schedule_stops" ("schedule_id", "station_id", "effective_from");
CREATE UNIQUE INDEX ON "national_rail_schedule_stops" ("schedule_id", "stop_order",  "effective_from");
CREATE        INDEX "idx_schedule_stops_effective"
  ON "national_rail_schedule_stops" ("schedule_id", "is_stop", "effective_from", "effective_to");

-- booking_tickets
CREATE INDEX "idx_booking_tickets_booking"  ON "booking_tickets" ("booking_id");
CREATE INDEX "idx_booking_tickets_leg"      ON "booking_tickets" ("booking_id", "leg");
CREATE INDEX "idx_booking_tickets_date"     ON "booking_tickets" ("schedule_id", "travel_date");

-- metro_travel_history
CREATE INDEX "idx_metro_travel_date"        ON "metro_travel_history" ("travel_date");
CREATE INDEX "idx_metro_travel_day_pass"    ON "metro_travel_history" ("day_pass_ref");

-- payments / payment_sources / feedback
CREATE INDEX "idx_payment_sources_booking_id"  ON "payment_sources"    ("national_rail_booking_id");
CREATE INDEX "idx_payment_sources_metro_id"    ON "payment_sources"    ("metro_trip_id");
CREATE INDEX "idx_customer_feedback_user_id"   ON "customer_feedback"  ("user_id");


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

-- 決策 5：ON DELETE SET NULL 確保座位設定被刪後票務記錄仍可保留
ALTER TABLE "booking_tickets"
  ADD FOREIGN KEY ("seat_pk") REFERENCES "national_rail_seats" ("seat_pk")
  ON DELETE SET NULL DEFERRABLE INITIALLY IMMEDIATE;

-- metro_travel_history（地鐵乘車記錄）
ALTER TABLE "metro_travel_history"
  ADD FOREIGN KEY ("trip_id") REFERENCES "travel_orders" ("order_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "metro_travel_history"
  ADD FOREIGN KEY ("schedule_id") REFERENCES "metro_schedules" ("schedule_id")
  DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "metro_travel_history"
  ADD FOREIGN KEY ("origin_station_id") REFERENCES "metro_stations" ("station_id")
  DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "metro_travel_history"
  ADD FOREIGN KEY ("destination_station_id") REFERENCES "metro_stations" ("station_id")
  DEFERRABLE INITIALLY IMMEDIATE;

-- day_pass 自參照：DEFERRABLE INITIALLY DEFERRED 允許同一 transaction 內先插子行程再插主記錄
ALTER TABLE "metro_travel_history"
  ADD FOREIGN KEY ("day_pass_ref") REFERENCES "metro_travel_history" ("trip_id")
  DEFERRABLE INITIALLY DEFERRED;

-- payments + payment_sources
ALTER TABLE "payment_sources"
  ADD FOREIGN KEY ("payment_id") REFERENCES "payments" ("payment_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "payment_sources"
  ADD FOREIGN KEY ("national_rail_booking_id") REFERENCES "bookings" ("booking_id")
  DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "payment_sources"
  ADD FOREIGN KEY ("metro_trip_id") REFERENCES "metro_travel_history" ("trip_id")
  DEFERRABLE INITIALLY IMMEDIATE;

-- customer_feedback
ALTER TABLE "customer_feedback"
  ADD FOREIGN KEY ("order_id") REFERENCES "travel_orders" ("order_id")
  ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE "customer_feedback"
  ADD FOREIGN KEY ("user_id") REFERENCES "users" ("user_id")
  DEFERRABLE INITIALLY IMMEDIATE;

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
