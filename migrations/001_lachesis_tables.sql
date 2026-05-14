-- Run against Alex's existing Supabase instance.
-- All tables namespaced with lachesis_ prefix per req 011.

CREATE TABLE IF NOT EXISTS lachesis_users (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    google_id   TEXT        NOT NULL UNIQUE,
    email       TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lachesis_repos (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES lachesis_users(id) ON DELETE CASCADE,
    repo_name   TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, repo_name)
);
