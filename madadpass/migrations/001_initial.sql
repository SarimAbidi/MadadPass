CREATE TABLE organizations (id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE users (
    id TEXT PRIMARY KEY, org TEXT NOT NULL REFERENCES organizations(id),
    login TEXT NOT NULL UNIQUE, name TEXT NOT NULL, role TEXT NOT NULL,
    password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires INTEGER NOT NULL
);
CREATE TABLE policies (
    id TEXT PRIMARY KEY, org TEXT NOT NULL REFERENCES organizations(id), version INTEGER NOT NULL,
    min_members INTEGER NOT NULL, max_income INTEGER NOT NULL, voucher_value INTEGER NOT NULL,
    evidence_required INTEGER NOT NULL, published_at TEXT NOT NULL,
    UNIQUE (org, version)
);
CREATE TABLE cases (
    id TEXT PRIMARY KEY, org TEXT NOT NULL REFERENCES organizations(id),
    citizen TEXT NOT NULL REFERENCES users(id), submitted_by TEXT NOT NULL REFERENCES users(id),
    request_id TEXT NOT NULL, input_hash TEXT NOT NULL, applicant TEXT NOT NULL,
    identity_ref TEXT NOT NULL, address TEXT NOT NULL, family_members INTEGER, monthly_income INTEGER,
    status TEXT NOT NULL, assigned_to TEXT REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE (submitted_by, request_id)
);
CREATE INDEX cases_listing ON cases (org, status, updated_at);
CREATE TABLE consents (
    id TEXT PRIMARY KEY, citizen TEXT NOT NULL REFERENCES users(id), helper TEXT NOT NULL REFERENCES users(id),
    scope TEXT NOT NULL, granted_at TEXT NOT NULL, revoked_at TEXT
);
CREATE UNIQUE INDEX active_consent ON consents (citizen, helper) WHERE revoked_at IS NULL;
CREATE TABLE documents (
    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), filename TEXT NOT NULL,
    mime TEXT NOT NULL, content BLOB NOT NULL, extraction TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE assessments (
    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), policy_id TEXT NOT NULL REFERENCES policies(id),
    findings TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX assessment_case ON assessments (case_id, created_at);
CREATE TABLE decisions (
    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), officer TEXT NOT NULL REFERENCES users(id),
    assessment TEXT NOT NULL REFERENCES assessments(id), action TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE vouchers (
    id TEXT PRIMARY KEY, case_id TEXT NOT NULL UNIQUE REFERENCES cases(id), token_hash TEXT NOT NULL UNIQUE,
    value INTEGER NOT NULL, status TEXT NOT NULL, expires INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE redemptions (
    id TEXT PRIMARY KEY, voucher_id TEXT NOT NULL UNIQUE REFERENCES vouchers(id),
    merchant TEXT NOT NULL REFERENCES users(id), request_id TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE (merchant, request_id)
);
CREATE TABLE settlements (
    id TEXT PRIMARY KEY, redemption_id TEXT NOT NULL UNIQUE REFERENCES redemptions(id), created_at TEXT NOT NULL
);
CREATE TABLE audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, org TEXT NOT NULL REFERENCES organizations(id),
    actor TEXT REFERENCES users(id), role TEXT NOT NULL, action TEXT NOT NULL, case_id TEXT,
    details TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX audit_org ON audit (org, id DESC);
CREATE TABLE api_keys (
    id TEXT PRIMARY KEY, org TEXT NOT NULL REFERENCES organizations(id), token_hash TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT
);
CREATE TABLE limits (bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, expires INTEGER NOT NULL);
