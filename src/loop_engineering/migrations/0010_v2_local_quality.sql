CREATE TABLE IF NOT EXISTS loop_local_quality_state (
    work_identity TEXT PRIMARY KEY REFERENCES loop_work_records(identity),
    target_head_sha TEXT NOT NULL,
    change_identity TEXT NOT NULL,
    stage TEXT NOT NULL,
    review_cycle INTEGER NOT NULL DEFAULT 0 CHECK (review_cycle >= 0),
    no_progress_count INTEGER NOT NULL DEFAULT 0 CHECK (no_progress_count >= 0),
    last_progress_fingerprint TEXT,
    verification_identity TEXT,
    review_request_key TEXT,
    review_identity TEXT,
    local_pass_identity TEXT,
    approved_findings JSONB NOT NULL DEFAULT '[]'::jsonb,
    diagnostics JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS loop_local_quality_target_index
    ON loop_local_quality_state (target_head_sha, change_identity, updated_at DESC);
