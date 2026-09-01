ALTER TABLE loop_effect_attempts
    ADD COLUMN IF NOT EXISTS expected_preconditions JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE loop_effect_attempts
    ADD COLUMN IF NOT EXISTS expected_effect JSONB NOT NULL DEFAULT '{}'::jsonb;
