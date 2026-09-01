ALTER TABLE loop_effect_attempts
    ADD COLUMN IF NOT EXISTS packet_generation BIGINT;

ALTER TABLE loop_effect_attempts
    ADD COLUMN IF NOT EXISTS expected_preconditions JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE loop_effect_attempts
    ADD COLUMN IF NOT EXISTS expected_effect JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE UNIQUE INDEX IF NOT EXISTS loop_issue_report_outbox_logical_identity_key
    ON loop_issue_report_outbox (
        work_identity,
        COALESCE(checkpoint_identity, ''),
        report_kind
    );
