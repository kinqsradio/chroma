CREATE TABLE embeddings (
    id INTEGER PRIMARY KEY,
    segment_id TEXT NOT NULL,
    embedding_id TEXT NOT NULL,
    seq_id BLOB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (segment_id, embedding_id)
);

CREATE TABLE embedding_metadata {
    id INTEGER REFERENCES embeddings(id),
    key TEXT NOT NULL,
    string_value TEXT,
    int_value INTEGER,
    float_value REAL
};

CREATE VIRTUAL TABLE embedding_fulltext USING fts5(id UNINDEXED, string_value);
