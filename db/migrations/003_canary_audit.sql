-- 003: Canary audit  (doc 8.4 figure, 8.3 relations 12-17)
-- Columns marked "+" are additions to the proposal; each is justified in DEVIATIONS.md.

CREATE TABLE audit_campaign (
  campaign_id    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  wall_id        integer NOT NULL REFERENCES info_wall(wall_id),        -- audited by 1:N
  created_by     integer NOT NULL REFERENCES app_user(user_id),
  alpha          numeric NOT NULL CHECK (alpha > 0 AND alpha < 0.5),
  planned_slots  integer NOT NULL CHECK (planned_slots > 0),
  status         varchar(10) NOT NULL DEFAULT 'PLANNED' CHECK (status IN ('PLANNED','RUNNING','CLOSED','ABORTED')),
  started_at     timestamptz,
  closed_at      timestamptz,
  clock_mode     varchar(10) NOT NULL DEFAULT 'LIVE' CHECK (clock_mode IN ('LIVE','SIMULATED')),   -- + D-03
  config         jsonb NOT NULL DEFAULT '{}'::jsonb,                                                -- + D-03
  CHECK (closed_at IS NULL OR started_at IS NULL OR closed_at >= started_at)
);
-- at most one running campaign per wall (the gateway resolves "the active slot" from it)
CREATE UNIQUE INDEX audit_campaign_one_running_per_wall ON audit_campaign (wall_id) WHERE status = 'RUNNING';

CREATE TABLE treatment (
  treatment_id      integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_id       integer NOT NULL REFERENCES audit_campaign(campaign_id),
  vector_memory_on  boolean NOT NULL,
  notes_table_on    boolean NOT NULL,
  cache_on          boolean NOT NULL,
  UNIQUE (treatment_id, campaign_id),                                   -- target of the composite FKs (8.4)
  UNIQUE (campaign_id, vector_memory_on, notes_table_on, cache_on)      -- (campaign_id, three flags) -> treatment_id (8.5 FD)
);

CREATE TABLE canary_slot (
  slot_id       integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_id   integer NOT NULL REFERENCES audit_campaign(campaign_id),
  treatment_id  integer NOT NULL,
  upsi_id       integer NOT NULL REFERENCES upsi_item(upsi_id),
  slot_period   tstzrange NOT NULL,
  commitment    char(64)  NOT NULL CHECK (commitment ~ '^[0-9a-f]{64}$'),
  committed_at  timestamptz NOT NULL,                                                 -- + D-04
  -- a slot cannot point at another campaign's treatment
  FOREIGN KEY (treatment_id, campaign_id) REFERENCES treatment (treatment_id, campaign_id),
  -- no two slots of a campaign overlap in time
  CONSTRAINT canary_slot_no_overlap EXCLUDE USING gist (campaign_id WITH =, slot_period WITH &&),
  CHECK (NOT isempty(slot_period) AND NOT lower_inf(slot_period) AND NOT upper_inf(slot_period)
         AND lower_inc(slot_period) AND NOT upper_inc(slot_period))
);
CREATE INDEX canary_slot_campaign_treatment_idx ON canary_slot (campaign_id, treatment_id);

CREATE TABLE canary_variant (           -- offers 1:2
  variant_id   integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slot_id      integer NOT NULL REFERENCES canary_slot(slot_id),
  variant_bit  smallint NOT NULL CHECK (variant_bit IN (0,1)),
  direction    varchar(8) NOT NULL CHECK (direction IN ('POSITIVE','NEGATIVE')),
  content      text NOT NULL,
  UNIQUE (slot_id, variant_bit),         -- FD (slot_id, variant_bit) -> variant_id
  UNIQUE (slot_id, direction)            -- FD (slot_id, direction)   -> variant_id
);

CREATE TABLE sealed_flip (              -- weak entity, 1:1 with canary_slot; the secret half of the slot
  slot_id   integer PRIMARY KEY REFERENCES canary_slot(slot_id),
  flip_bit  smallint NOT NULL CHECK (flip_bit IN (0,1)),
  salt      bytea NOT NULL CHECK (octet_length(salt) >= 16)
);

CREATE TABLE audit_result (             -- frozen verdict per (treatment, LOW agent)
  result_id     integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign_id   integer NOT NULL REFERENCES audit_campaign(campaign_id),
  treatment_id  integer NOT NULL,
  low_agent_id  integer NOT NULL REFERENCES agent(agent_id),
  n_slots       integer NOT NULL CHECK (n_slots > 0),
  n_correct     integer NOT NULL,
  p_value       double precision NOT NULL CHECK (p_value >= 0 AND p_value <= 1),   -- raw exact one-sided binomial p
  leakage_bits  double precision NOT NULL CHECK (leakage_bits >= 0 AND leakage_bits <= 1),  -- 1 - H(accuracy), point estimate
  verdict       varchar(12) NOT NULL CHECK (verdict IN ('LEAK','NO_EVIDENCE')),
  computed_by   integer NOT NULL REFERENCES app_user(user_id),
  computed_at   timestamptz NOT NULL DEFAULT now(),
  -- + D-05: statistical rigour beyond the proposal
  p_adjusted           double precision NOT NULL CHECK (p_adjusted >= 0 AND p_adjusted <= 1),   -- Holm-Bonferroni over the family
  acc_lower            double precision NOT NULL CHECK (acc_lower >= 0 AND acc_lower <= 1),     -- Clopper-Pearson lower bound
  leakage_bits_lower   double precision NOT NULL CHECK (leakage_bits_lower >= 0 AND leakage_bits_lower <= 1),
  min_detectable_acc   double precision,                                                        -- 80% power at the planned n
  family_size          integer NOT NULL CHECK (family_size > 0),
  alpha                numeric NOT NULL,
  clock_mode           varchar(10) NOT NULL,
  result_hash          char(64) NOT NULL CHECK (result_hash ~ '^[0-9a-f]{64}$'),                -- "signed snapshot" (D-06)
  FOREIGN KEY (treatment_id, campaign_id) REFERENCES treatment (treatment_id, campaign_id),
  UNIQUE (campaign_id, treatment_id, low_agent_id),
  CHECK (n_correct >= 0 AND n_correct <= n_slots)
);
