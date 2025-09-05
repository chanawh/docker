CREATE TABLE IF NOT EXISTS kvstore (
    key TEXT PRIMARY KEY,
    value TEXT
);

INSERT INTO kvstore (key, value) VALUES ('test_key', 'Hello from Postgres!')
ON CONFLICT (key) DO NOTHING;
