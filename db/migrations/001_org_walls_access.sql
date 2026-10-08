-- 001: Organisation, walls and access  (doc 8.2 figure, 8.3 relations 1-7)

CREATE TABLE department (
  dept_id    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  dept_name  varchar(100) NOT NULL UNIQUE,
  area_type  varchar(10)  NOT NULL CHECK (area_type IN ('INSIDE','PUBLIC'))
);

CREATE TABLE app_user (
  user_id    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  full_name  varchar(120) NOT NULL,
  email      varchar(200) NOT NULL UNIQUE CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
  user_role  varchar(20)  NOT NULL CHECK (user_role IN ('COMPLIANCE','DEVELOPER','AUDITOR','DEAL_TEAM'))
);

CREATE TABLE agent (
  agent_id       integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  agent_name     varchar(80) NOT NULL UNIQUE,
  dept_id        integer NOT NULL REFERENCES department(dept_id),   -- employs 1:N
  owner_user_id  integer NOT NULL REFERENCES app_user(user_id),     -- owns   1:N
  model_name     varchar(80) NOT NULL,
  model_version  varchar(40) NOT NULL,
  is_active      boolean     NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE info_wall (
  wall_id     integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  wall_name   varchar(100) NOT NULL UNIQUE,
  description text,
  created_by  integer NOT NULL REFERENCES app_user(user_id),
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE wall_membership (          -- resolves AGENT M:N INFO_WALL
  wall_id   integer NOT NULL REFERENCES info_wall(wall_id),
  agent_id  integer NOT NULL REFERENCES agent(agent_id),
  side      varchar(4) NOT NULL CHECK (side IN ('HIGH','LOW')),
  PRIMARY KEY (wall_id, agent_id)       -- one side per agent per wall
);

CREATE TABLE data_asset (
  asset_id        integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  asset_name      varchar(100) NOT NULL UNIQUE,
  asset_kind      varchar(10)  NOT NULL CHECK (asset_kind IN ('TABLE','VECTOR','CACHE')),
  classification  varchar(10)  NOT NULL CHECK (classification IN ('UPSI','INTERNAL','PUBLIC')),
  owner_dept_id   integer NOT NULL REFERENCES department(dept_id)   -- owns 1:N
);

CREATE TABLE access_grant (             -- resolves AGENT M:N DATA_ASSET
  grant_id    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  agent_id    integer NOT NULL REFERENCES agent(agent_id),
  asset_id    integer NOT NULL REFERENCES data_asset(asset_id),
  privilege   varchar(5) NOT NULL CHECK (privilege IN ('READ','WRITE')),
  granted_by  integer NOT NULL REFERENCES app_user(user_id),
  valid_from  timestamptz NOT NULL,
  valid_to    timestamptz NOT NULL DEFAULT 'infinity',               -- time-bounded; 'infinity' = open-ended
  CHECK (valid_to > valid_from)
);
CREATE INDEX access_grant_lookup_idx ON access_grant (agent_id, asset_id, privilege);
