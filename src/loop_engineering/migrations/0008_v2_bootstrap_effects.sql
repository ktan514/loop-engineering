CREATE TABLE IF NOT EXISTS loop_bootstrap_effects (
    idempotency_key TEXT PRIMARY KEY,
    product_key TEXT NOT NULL,
    repository TEXT NOT NULL,
    goal_revision TEXT NOT NULL,
    kind TEXT NOT NULL,
    target_identity TEXT NOT NULL,
    status TEXT NOT NULL,
    request_identity TEXT,
    expected_preconditions JSONB NOT NULL DEFAULT '{}'::jsonb,
    expected_effect JSONB NOT NULL DEFAULT '{}'::jsonb,
    confirmed_at TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS loop_bootstrap_effects_goal_status_index
    ON loop_bootstrap_effects (repository, product_key, goal_revision, status, recorded_at ASC);
