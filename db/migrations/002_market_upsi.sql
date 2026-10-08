-- 002: Market and confidential information  (doc 8.3 relations 8-11)

CREATE TABLE security (
  isin          char(12)     PRIMARY KEY CHECK (isin ~ '^IN[A-Z0-9]{10}$'),
  symbol        varchar(20)  NOT NULL UNIQUE,
  company_name  varchar(200) NOT NULL,
  sector        varchar(100) NOT NULL
);

CREATE TABLE daily_price (              -- weak entity: identified by (isin, trade_date)
  isin        char(12) NOT NULL REFERENCES security(isin),
  trade_date  date     NOT NULL,
  open_px     numeric(14,2) NOT NULL CHECK (open_px  > 0),
  high_px     numeric(14,2) NOT NULL CHECK (high_px  > 0),
  low_px      numeric(14,2) NOT NULL CHECK (low_px   > 0),
  close_px    numeric(14,2) NOT NULL CHECK (close_px > 0),
  volume      bigint        NOT NULL CHECK (volume >= 0),
  PRIMARY KEY (isin, trade_date),
  CHECK (low_px <= open_px AND low_px <= close_px AND open_px <= high_px AND close_px <= high_px)
);

CREATE TABLE upsi_item (
  upsi_id            integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  isin               char(12) NOT NULL REFERENCES security(isin),
  category           varchar(40) NOT NULL,
  summary            text NOT NULL,
  created_by         integer NOT NULL REFERENCES app_user(user_id),
  created_at         timestamptz NOT NULL DEFAULT now(),
  planned_release_at timestamptz
);
CREATE INDEX upsi_item_isin_idx ON upsi_item (isin);

CREATE TABLE sdd_entry (                -- Structured Digital Database: one sharing event
  sdd_id            integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  upsi_id           integer NOT NULL REFERENCES upsi_item(upsi_id),
  shared_by_user    integer REFERENCES app_user(user_id),
  shared_by_agent   integer REFERENCES agent(agent_id),
  recipient_user    integer REFERENCES app_user(user_id),
  recipient_agent   integer REFERENCES agent(agent_id),
  purpose           text NOT NULL,
  shared_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT sdd_exactly_one_sharer    CHECK (num_nonnulls(shared_by_user, shared_by_agent) = 1),
  CONSTRAINT sdd_exactly_one_recipient CHECK (num_nonnulls(recipient_user, recipient_agent) = 1)
);
CREATE INDEX sdd_entry_upsi_idx ON sdd_entry (upsi_id, shared_at);
