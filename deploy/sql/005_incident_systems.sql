ALTER TABLE incidents ADD COLUMN system text;

CREATE INDEX incidents_open_system_idx ON incidents (system, status, updated_at);
