CREATE TABLE incidents (
    incident_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title text NOT NULL,
    embedding vector(768) NOT NULL,
    status text NOT NULL DEFAULT 'OPEN',
    reporter_count integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX incidents_open_idx ON incidents (status, updated_at);

CREATE TABLE incident_reports (
    report_id text PRIMARY KEY,
    incident_id uuid NOT NULL REFERENCES incidents (incident_id),
    reporter_id text NOT NULL,
    text text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX incident_reports_incident_idx ON incident_reports (incident_id);
