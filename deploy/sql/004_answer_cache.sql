CREATE TABLE answer_cache (
    cache_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    question text NOT NULL,
    embedding vector(768) NOT NULL,
    groups_key text NOT NULL,
    answer text NOT NULL,
    citations jsonb NOT NULL,
    version_ids uuid[] NOT NULL,
    hits integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX answer_cache_groups_idx ON answer_cache (groups_key, created_at);
CREATE INDEX answer_cache_versions_idx ON answer_cache USING gin (version_ids);
