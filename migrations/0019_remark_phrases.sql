-- Phrases the team teaches TeamOS for reading call remarks (English, Telugu, or Telugu in English letters).
-- Built-in phrases live in code (teamos/domain/remarks.py); these add to them and are used on every import.
CREATE TABLE remark_phrases (
    id {{ID}},
    phrase TEXT NOT NULL UNIQUE,                 -- lower-case words exactly as the team writes them
    meaning TEXT NOT NULL,                       -- a key from remarks.MEANINGS
    active INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);
