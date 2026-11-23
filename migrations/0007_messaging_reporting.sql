-- 0007: WhatsApp team inbox, templates, consent-guarded sending, batches, webhooks, e-mail digests (PRD §10, §11.4;
-- Milestone 7). Provider-agnostic: the live Meta Cloud API adapter is OFF until verified credentials are configured.

CREATE TABLE message_templates (
    id {{ID}},
    key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('service','marketing')),   -- service = requested follow-up
    language TEXT NOT NULL DEFAULT 'en',
    body TEXT NOT NULL,                  -- {name}-style placeholders
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved_local','provider_approved','paused','rejected')),
    provider_name TEXT,                  -- name of the approved template at the provider (live only)
    notes TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- One WhatsApp thread per phone number. contact_id NULL = unmatched sender (UAT-43).
CREATE TABLE conversations (
    id {{ID}},
    phone TEXT NOT NULL UNIQUE,
    contact_id INTEGER REFERENCES contacts(id),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
    assigned_to INTEGER REFERENCES users(id),
    last_message_at TEXT,
    last_inbound_at TEXT,                -- opens the provider's 24-hour customer-service window
    last_outbound_at TEXT,
    last_outbound_by INTEGER REFERENCES users(id),
    unread INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_conversations_contact ON conversations(contact_id);
CREATE INDEX ix_conversations_assigned ON conversations(assigned_to, status);

CREATE TABLE conversation_links (
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id),
    linked_by INTEGER REFERENCES users(id),
    linked_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, opportunity_id)
);

-- Every inbound and outbound message. Status never claims more than the provider reported (§10.3).
CREATE TABLE messages (
    id {{ID}},
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    direction TEXT NOT NULL CHECK (direction IN ('in','out')),
    kind TEXT NOT NULL CHECK (kind IN ('text','template','system')),
    template_key TEXT,
    body TEXT NOT NULL,                  -- secret links are stored as placeholders, never the token
    status TEXT NOT NULL CHECK (status IN ('queued','provider_accepted','sent','delivered','read','failed',
                                           'uncertain','suppressed','received')),
    provider TEXT NOT NULL,
    provider_message_id TEXT UNIQUE,
    error TEXT,
    sent_by INTEGER REFERENCES users(id),
    opportunity_id INTEGER REFERENCES opportunities(id),
    invitation_id INTEGER REFERENCES form_invitations(id),
    batch_id INTEGER,
    created_at TEXT NOT NULL,
    status_at TEXT,
    resolved_by INTEGER REFERENCES users(id),
    resolved_at TEXT
);
CREATE INDEX ix_messages_conv ON messages(conversation_id, created_at);
CREATE INDEX ix_messages_status ON messages(status);

-- Idempotent webhook ingestion (UAT-24): the provider's event id is unique; processing status is visible.
CREATE TABLE webhook_events (
    id {{ID}},
    provider TEXT NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'received' CHECK (status IN ('received','processed','failed','ignored')),
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    received_at TEXT NOT NULL,
    processed_at TEXT
);

-- Reviewed outreach batches (FR-027): recipients re-checked at send time (UAT-45).
CREATE TABLE batches (
    id {{ID}},
    name TEXT NOT NULL,
    template_key TEXT NOT NULL,
    audience TEXT NOT NULL,              -- JSON filter
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','submitted','approved','sent','cancelled')),
    note TEXT,
    created_by INTEGER REFERENCES users(id),
    approved_by INTEGER REFERENCES users(id),
    approved_at TEXT,
    sent_at TEXT,
    counts TEXT,                         -- JSON {eligible, suppressed, sent, failed}
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE batch_recipients (
    id {{ID}},
    batch_id INTEGER NOT NULL REFERENCES batches(id),
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    opportunity_id INTEGER REFERENCES opportunities(id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','suppressed','failed')),
    reason TEXT,
    message_id INTEGER REFERENCES messages(id),
    UNIQUE (batch_id, contact_id)
);

-- E-mail is also provider-agnostic: in development the outbox IS the provider (nothing leaves the server).
CREATE TABLE email_outbox (
    id {{ID}},
    user_id INTEGER REFERENCES users(id),
    to_addr TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','failed','held')),
    provider TEXT NOT NULL,
    error TEXT,
    dedupe_key TEXT UNIQUE,
    created_at TEXT NOT NULL,
    sent_at TEXT
);

ALTER TABLE users ADD COLUMN digest_opt_in INTEGER NOT NULL DEFAULT 1;
