-- 004: Behaviour log  (doc 8.5 figure, 8.3 relations 18-20)

CREATE TABLE agent_note (               -- shared memory: notes table, vector memory and cache are separate ASSETS
  note_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,   -- stored in this one table, told apart by asset_id
  author_agent_id  integer NOT NULL REFERENCES agent(agent_id),
  asset_id         integer NOT NULL REFERENCES data_asset(asset_id),
  isin             char(12) REFERENCES security(isin),                -- mentions 0..1 : N
  body             text NOT NULL,
  embedding        vector(384),
  created_at       timestamptz NOT NULL DEFAULT now()
);
-- Deliberately NO ANN (hnsw/ivfflat) index: the gateway filters by asset and by slot window, and a
-- post-filtered approximate index would silently return fewer rows than exist (see DEVIATIONS.md D-10).
CREATE INDEX agent_note_asset_time_idx ON agent_note (asset_id, created_at);

CREATE TABLE access_event (
  event_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  agent_id    integer NOT NULL REFERENCES agent(agent_id),
  asset_id    integer NOT NULL REFERENCES data_asset(asset_id),
  op          varchar(6) NOT NULL CHECK (op IN ('READ','INSERT','UPDATE','DELETE')),
  row_ref     text,
  event_time  timestamptz NOT NULL,
  txn_id      bigint NOT NULL,
  outcome     varchar(7) NOT NULL DEFAULT 'ALLOWED' CHECK (outcome IN ('ALLOWED','DENIED')),   -- + D-02
  detail      text                                                                              -- + D-02 (deny reason)
);
CREATE INDEX access_event_agent_time_idx ON access_event (agent_id, event_time);
CREATE INDEX access_event_asset_time_idx ON access_event (asset_id, event_time);

CREATE TABLE trade_order (
  order_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  agent_id     integer NOT NULL REFERENCES agent(agent_id),
  isin         char(12) NOT NULL REFERENCES security(isin),
  side         varchar(4) NOT NULL CHECK (side IN ('BUY','SELL')),
  quantity     integer NOT NULL CHECK (quantity > 0),
  limit_price  numeric(14,2) CHECK (limit_price > 0),
  placed_at    timestamptz NOT NULL
);
CREATE INDEX trade_order_agent_isin_time_idx ON trade_order (agent_id, isin, placed_at);
