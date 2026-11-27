-- Uploaded documents and photos are kept (still encrypted) in the database, not on the server's disk: free hosting
-- wipes the disk on every deploy, which would silently lose them. data = the Fernet token (already text-safe).
CREATE TABLE document_blobs (
    stored_name TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
/*pg*/ ALTER TABLE document_blobs ENABLE ROW LEVEL SECURITY;
/*pg*/ REVOKE ALL ON document_blobs FROM anon, authenticated;
