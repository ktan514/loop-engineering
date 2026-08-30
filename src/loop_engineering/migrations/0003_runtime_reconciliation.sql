ALTER TABLE loop_checkpoints
    ADD COLUMN IF NOT EXISTS run_identity TEXT;

ALTER TABLE loop_checkpoints
    ADD COLUMN IF NOT EXISTS pr_number BIGINT;

ALTER TABLE loop_checkpoints
    ADD COLUMN IF NOT EXISTS head_sha TEXT;

ALTER TABLE loop_blockers
    ADD COLUMN IF NOT EXISTS run_identity TEXT;

ALTER TABLE loop_external_waits
    ADD COLUMN IF NOT EXISTS run_identity TEXT;

CREATE INDEX IF NOT EXISTS loop_checkpoints_run_index
    ON loop_checkpoints (run_identity, recorded_at DESC);

CREATE INDEX IF NOT EXISTS loop_runs_project_status_index
    ON loop_runs (project_key, repository, status, started_at DESC);

CREATE INDEX IF NOT EXISTS loop_blockers_run_status_index
    ON loop_blockers (run_identity, status);

CREATE INDEX IF NOT EXISTS loop_external_waits_target_index
    ON loop_external_waits (kind, target_identity, status);

CREATE INDEX IF NOT EXISTS loop_external_waits_run_status_index
    ON loop_external_waits (run_identity, status);
