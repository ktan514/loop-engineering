CREATE TABLE IF NOT EXISTS loop_autonomous_runtimes (
    runtime_identity TEXT PRIMARY KEY,
    product_key TEXT NOT NULL,
    repository TEXT NOT NULL,
    goal_revision TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'WAITING', 'INTERVENTION_REQUIRED', 'COMPLETED')),
    current_work_identity TEXT NULL,
    last_schedule_key TEXT NULL,
    last_progress_fingerprint TEXT NULL,
    no_progress_count INTEGER NOT NULL DEFAULT 0 CHECK (no_progress_count >= 0),
    last_detail TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ NULL,
    UNIQUE (product_key, repository, goal_revision)
);

CREATE TABLE IF NOT EXISTS loop_autonomous_dispatches (
    schedule_key TEXT PRIMARY KEY,
    runtime_identity TEXT NOT NULL REFERENCES loop_autonomous_runtimes(runtime_identity),
    work_identity TEXT NOT NULL,
    transition TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('DISPATCHED', 'COMPLETED', 'WAITING', 'FAILED', 'SUPERSEDED')),
    detail TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS loop_autonomous_dispatches_runtime_idx
    ON loop_autonomous_dispatches (runtime_identity, created_at);
