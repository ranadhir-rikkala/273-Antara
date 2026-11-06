CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
    document_id uuid PRIMARY KEY,
    owner_id text NOT NULL,
    title text NOT NULL,
    active_version_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);

CREATE TABLE document_versions (
    version_id uuid PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES documents (document_id),
    version_number integer NOT NULL,
    status text NOT NULL,
    object_key text NOT NULL,
    content_type text NOT NULL,
    text_key text,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, version_number)
);

ALTER TABLE documents
    ADD CONSTRAINT documents_active_version_fk FOREIGN KEY (active_version_id) REFERENCES document_versions (version_id);

CREATE INDEX document_versions_status_idx ON document_versions (status, updated_at);

CREATE TABLE document_acl (
    document_id uuid NOT NULL REFERENCES documents (document_id) ON DELETE CASCADE,
    group_name text NOT NULL,
    PRIMARY KEY (document_id, group_name)
);

CREATE TABLE document_chunks (
    chunk_id uuid PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES documents (document_id),
    version_id uuid NOT NULL REFERENCES document_versions (version_id),
    chunk_index integer NOT NULL,
    page_number integer NOT NULL,
    text text NOT NULL,
    UNIQUE (version_id, chunk_index)
);

CREATE INDEX document_chunks_document_idx ON document_chunks (document_id);

CREATE TABLE document_embeddings (
    chunk_id uuid PRIMARY KEY REFERENCES document_chunks (chunk_id) ON DELETE CASCADE,
    embedding vector(768) NOT NULL
);

CREATE INDEX document_embeddings_hnsw_idx ON document_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE TABLE tool_actions (
    idempotency_key text PRIMARY KEY,
    tool text NOT NULL,
    arguments jsonb NOT NULL,
    user_id text NOT NULL,
    status text NOT NULL,
    result jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE TABLE tickets (
    ticket_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title text NOT NULL,
    description text NOT NULL,
    priority text NOT NULL DEFAULT 'medium',
    status text NOT NULL DEFAULT 'OPEN',
    requester_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE approvals (
    approval_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    requester_id text NOT NULL,
    system text NOT NULL,
    role text NOT NULL,
    reason text NOT NULL DEFAULT '',
    status text NOT NULL,
    decided_by text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
    audit_id bigserial PRIMARY KEY,
    actor_id text NOT NULL,
    action text NOT NULL,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE deletion_jobs (
    document_id uuid PRIMARY KEY REFERENCES documents (document_id),
    status text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
