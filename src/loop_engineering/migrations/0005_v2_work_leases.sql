CREATE TABLE IF NOT EXISTS loop_work_leases (
    work_identity TEXT PRIMARY KEY REFERENCES loop_work_records(identity),
    holder_identity TEXT NOT NULL,
    packet_generation BIGINT NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS loop_work_leases_expiry_index
    ON loop_work_leases (expires_at ASC);
