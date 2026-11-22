-- 0004: Forms & data requests (PRD §5.6, §10.2, §10.4, §13; Milestone 4).
--
-- Personal links are stored as a SHA-256 of the token only: the link is shown once to staff and cannot
-- be recovered from the database. Answers are stored raw (canonical keys + answer state) on the
-- submission and are only written to the CRM record when a staff reviewer accepts them.

CREATE TABLE form_invitations (
    id {{ID}},
    token_hash TEXT NOT NULL UNIQUE,
    template_key TEXT NOT NULL,          -- seller_deep | buyer_deep | targeted
    template_version TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('seller','buyer')),
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    field_keys TEXT,                     -- JSON list for targeted requests; NULL = whole template
    doc_types TEXT,                      -- JSON list of requested document types
    purpose TEXT,                        -- shown to the respondent ("why we are asking")
    channel TEXT NOT NULL CHECK (channel IN ('copy_link','whatsapp_mock')),
    status TEXT NOT NULL DEFAULT 'sent' CHECK (status IN
        ('sent','opened','started','submitted','accepted','more_info_required','revoked','expired')),
    expires_at TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    opened_at TEXT,                      -- recorded by our own server when the page is first loaded
    started_at TEXT,
    submitted_at TEXT,
    closed_at TEXT,
    chased_at TEXT
);
CREATE INDEX ix_invitations_opp ON form_invitations(opportunity_id, status);
CREATE INDEX ix_invitations_status ON form_invitations(status, created_at);

CREATE TABLE form_submissions (
    id {{ID}},
    invitation_id INTEGER REFERENCES form_invitations(id),   -- NULL for public quick forms
    template_key TEXT NOT NULL,
    template_version TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('seller','buyer')),
    opportunity_id INTEGER REFERENCES opportunities(id),
    contact_id INTEGER REFERENCES contacts(id),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','submitted','reviewed')),
    answers TEXT NOT NULL,               -- JSON {field_key: {"value": raw, "state": answer_state}}
    ignored_fields TEXT,                 -- JSON list of privileged / unknown keys that were dropped
    consent TEXT,                        -- JSON {purpose: {"granted": bool, "text_version": ...}}
    ip_hash TEXT,
    user_agent TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    submitted_at TEXT,
    review_outcome TEXT CHECK (review_outcome IN ('applied','partially_applied','rejected','more_info_required','recorded')),
    applied_fields TEXT,                 -- JSON list
    review_note TEXT,
    review_task_id INTEGER REFERENCES tasks(id),
    reviewed_by INTEGER REFERENCES users(id),
    reviewed_at TEXT
);
CREATE INDEX ix_submissions_status ON form_submissions(status, submitted_at);
CREATE INDEX ix_submissions_opp ON form_submissions(opportunity_id);

-- Uploaded files live outside the web root, encrypted, under a random name (no guessable URL).
CREATE TABLE documents (
    id {{ID}},
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    submission_id INTEGER REFERENCES form_submissions(id),
    invitation_id INTEGER REFERENCES form_invitations(id),
    doc_type TEXT NOT NULL,
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL UNIQUE,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    encrypted INTEGER NOT NULL DEFAULT 1,
    uploaded_via TEXT NOT NULL CHECK (uploaded_via IN ('form','staff')),
    uploaded_by INTEGER REFERENCES users(id),
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','reviewed','rejected')),
    review_note TEXT,
    reviewed_by INTEGER REFERENCES users(id),
    reviewed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_documents_opp ON documents(opportunity_id);

-- Outbound communications. In M4 the only provider is a labelled MOCK: nothing leaves the server.
CREATE TABLE communications (
    id {{ID}},
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    opportunity_id INTEGER REFERENCES opportunities(id),
    invitation_id INTEGER REFERENCES form_invitations(id),
    channel TEXT NOT NULL CHECK (channel IN ('whatsapp')),
    provider TEXT NOT NULL,
    template_key TEXT NOT NULL,
    body TEXT NOT NULL,                  -- rendered text WITHOUT the secret link token
    status TEXT NOT NULL CHECK (status IN ('provider_accepted','failed','suppressed')),
    provider_message_id TEXT,
    detail TEXT,
    sent_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE INDEX ix_comms_contact ON communications(contact_id, created_at);

-- Abuse protection for the public quick forms (rate limiting by hashed IP).
CREATE TABLE public_form_hits (
    id {{ID}},
    ip_hash TEXT NOT NULL,
    form TEXT NOT NULL,
    outcome TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX ix_public_hits ON public_form_hits(ip_hash, at);
