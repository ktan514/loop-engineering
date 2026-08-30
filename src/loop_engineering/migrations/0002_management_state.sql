CREATE TABLE IF NOT EXISTS loop_runs (
    identity TEXT PRIMARY KEY,
    project_key TEXT NOT NULL,
    repository TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS loop_transitions (
    identity TEXT PRIMARY KEY,
    run_identity TEXT NOT NULL,
    sequence_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL,
    work_issue BIGINT,
    pr_number BIGINT,
    head_sha TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_identity, sequence_number)
);

CREATE TABLE IF NOT EXISTS loop_checkpoints (
    identity TEXT PRIMARY KEY,
    project_key TEXT NOT NULL,
    work_issue BIGINT,
    source_revision TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS loop_blockers (
    identity TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    subject_identity TEXT NOT NULL,
    kind TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS loop_leases (
    identity TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    subject_identity TEXT NOT NULL,
    holder_identity TEXT NOT NULL,
    status TEXT NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS loop_dispatches (
    identity TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    target_identity TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS loop_external_waits (
    identity TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    target_identity TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS loop_transitions_run_index
    ON loop_transitions (run_identity, sequence_number);
CREATE INDEX IF NOT EXISTS loop_blockers_subject_index
    ON loop_blockers (scope, subject_identity, status);
CREATE INDEX IF NOT EXISTS loop_leases_subject_index
    ON loop_leases (scope, subject_identity, status);
