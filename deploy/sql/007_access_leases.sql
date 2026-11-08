ALTER TABLE access_grants ADD COLUMN IF NOT EXISTS expires_at timestamptz;
ALTER TABLE access_grants ADD COLUMN IF NOT EXISTS revoked_at timestamptz;
CREATE INDEX IF NOT EXISTS access_grants_active_idx ON access_grants (expires_at) WHERE revoked_at IS NULL;
