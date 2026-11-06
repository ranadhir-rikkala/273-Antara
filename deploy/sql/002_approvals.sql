ALTER TABLE approvals ADD COLUMN approval_key text UNIQUE;
ALTER TABLE approvals ADD COLUMN run_id text;
ALTER TABLE approvals ADD COLUMN escalated boolean NOT NULL DEFAULT false;
ALTER TABLE approvals ADD COLUMN decided_at timestamptz;

CREATE INDEX approvals_status_idx ON approvals (status, escalated);
CREATE INDEX approvals_run_idx ON approvals (run_id);

CREATE TABLE access_grants (
    grant_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id text NOT NULL,
    requester_id text NOT NULL,
    system text NOT NULL,
    role text NOT NULL,
    granted_at timestamptz NOT NULL DEFAULT now()
);
