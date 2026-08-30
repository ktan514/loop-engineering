ALTER TABLE loop_checkpoints
    ADD COLUMN IF NOT EXISTS run_identity TEXT;

ALTER TABLE loop_checkpoints
    ADD COLUMN IF NOT EXISTS pr_number BIGINT;

ALTER TABLE loop_checkpoints
    ADD COLUMN IF NOT EXISTS head_sha TEXT;

CREATE INDEX IF NOT EXISTS loop_checkpoints_run_index
    ON loop_checkpoints (run_identity, recorded_at DESC);

CREATE INDEX IF NOT EXISTS loop_runs_project_status_index
    ON loop_runs (project_key, repository, status, started_at DESC);

CREATE INDEX IF NOT EXISTS loop_external_waits_target_index
    ON loop_external_waits (kind, target_identity, status);
