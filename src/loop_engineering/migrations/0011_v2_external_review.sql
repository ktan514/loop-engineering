CREATE TABLE IF NOT EXISTS loop_external_review_state (
    work_identity TEXT PRIMARY KEY REFERENCES loop_work_records(identity),
    target_head_sha TEXT NOT NULL,
    change_identity TEXT NOT NULL,
    local_pass_identity TEXT NOT NULL,
    level_index INTEGER NOT NULL DEFAULT 0 CHECK (level_index >= 0),
    pass_index INTEGER NOT NULL DEFAULT 1 CHECK (pass_index >= 1),
    completed_evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    current_request_key TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    approved_findings JSONB NOT NULL DEFAULT '[]'::jsonb,
    diagnostics JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS loop_external_review_target_index
    ON loop_external_review_state (
        target_head_sha,
        change_identity,
        local_pass_identity,
        updated_at DESC
    );
