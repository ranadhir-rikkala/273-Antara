CREATE TABLE ops_events (
    event_id bigserial PRIMARY KEY,
    kind text NOT NULL,
    actor_id text NOT NULL,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ops_events_created_idx ON ops_events (created_at DESC);
