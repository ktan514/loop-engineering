CREATE TABLE IF NOT EXISTS loop_work_records (
    identity TEXT PRIMARY KEY,
    repository TEXT NOT NULL,
    issue_number BIGINT NOT NULL,
    issue_revision TEXT NOT NULL,
    lifecycle TEXT NOT NULL,
    selected_transition TEXT,
    active_lineage_identity TEXT,
    latest_task_packet_identity TEXT,
    latest_checkpoint_identity TEXT,
    revision BIGINT NOT NULL DEFAULT 1,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (repository, issue_number)
);

CREATE TABLE IF NOT EXISTS loop_task_packets (
    identity TEXT PRIMARY KEY,
    work_identity TEXT NOT NULL REFERENCES loop_work_records(identity),
    generation BIGINT NOT NULL,
    transition TEXT NOT NULL,
    status TEXT NOT NULL,
    canonical_design_identities JSONB NOT NULL DEFAULT '[]'::jsonb,
    external_target_identities JSONB NOT NULL DEFAULT '[]'::jsonb,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (work_identity, generation)
);

CREATE TABLE IF NOT EXISTS loop_work_checkpoints (
    identity TEXT PRIMARY KEY,
    work_identity TEXT NOT NULL REFERENCES loop_work_records(identity),
    run_identity TEXT NOT NULL,
    task_packet_identity TEXT REFERENCES loop_task_packets(identity),
    checkpoint_kind TEXT NOT NULL,
    resumable_state TEXT NOT NULL,
    next_action TEXT NOT NULL,
    external_target_identities JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_identities JSONB NOT NULL DEFAULT '[]'::jsonb,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS loop_effect_attempts (
    idempotency_key TEXT PRIMARY KEY,
    work_identity TEXT NOT NULL REFERENCES loop_work_records(identity),
    kind TEXT NOT NULL,
    target_identity TEXT NOT NULL,
    status TEXT NOT NULL,
    request_identity TEXT,
    confirmed_at TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS loop_issue_report_outbox (
    identity TEXT PRIMARY KEY,
    work_identity TEXT NOT NULL REFERENCES loop_work_records(identity),
    status TEXT NOT NULL,
    report_kind TEXT NOT NULL,
    checkpoint_identity TEXT REFERENCES loop_work_checkpoints(identity),
    body TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS loop_work_records_repository_issue_index
    ON loop_work_records (repository, issue_number, updated_at DESC);
CREATE INDEX IF NOT EXISTS loop_work_checkpoints_work_index
    ON loop_work_checkpoints (work_identity, recorded_at DESC);
CREATE INDEX IF NOT EXISTS loop_effect_attempts_work_status_index
    ON loop_effect_attempts (work_identity, status, recorded_at DESC);
CREATE INDEX IF NOT EXISTS loop_issue_report_outbox_pending_index
    ON loop_issue_report_outbox (status, recorded_at ASC);
