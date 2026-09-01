CREATE TABLE IF NOT EXISTS loop_v2_cutovers (
    repository TEXT PRIMARY KEY,
    cutover_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE loop_task_packets
    ADD COLUMN IF NOT EXISTS effect_kind TEXT;

ALTER TABLE loop_task_packets
    ADD COLUMN IF NOT EXISTS effect_target_identity TEXT;

ALTER TABLE loop_task_packets
    ADD COLUMN IF NOT EXISTS effect_idempotency_key TEXT;

ALTER TABLE loop_task_packets
    ADD COLUMN IF NOT EXISTS expected_preconditions JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE loop_task_packets
    ADD COLUMN IF NOT EXISTS expected_effect JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE UNIQUE INDEX IF NOT EXISTS loop_task_packets_effect_idempotency_key
    ON loop_task_packets (effect_idempotency_key)
    WHERE effect_idempotency_key IS NOT NULL;
