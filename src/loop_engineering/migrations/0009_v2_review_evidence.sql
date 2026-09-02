CREATE TABLE IF NOT EXISTS loop_review_evidence (
    request_key TEXT PRIMARY KEY,
    work_identity TEXT NOT NULL REFERENCES loop_work_records(identity),
    head_sha TEXT NOT NULL,
    status TEXT NOT NULL,
    reviewer_identity TEXT,
    findings JSONB NOT NULL DEFAULT '[]'::jsonb,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS loop_review_evidence_work_head_index
    ON loop_review_evidence (work_identity, head_sha, updated_at DESC);
