import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def as_json(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    result = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return f"{salt}:{result.hex()}"


def password_ok(password, encoded):
    return hmac.compare_digest(password_hash(password, encoded.split(":", 1)[0]), encoded)


def audit(db, actor, action, case_id=None, **details):
    db.execute(
        "INSERT INTO audit (org,actor,role,action,case_id,details,created_at) VALUES (?,?,?,?,?,?,?)",
        (actor["org"], actor["id"], actor["role"], action, case_id, as_json(details), now()),
    )


def add_policy(db, org, version, min_members, max_income, voucher_value, evidence_required):
    policy_id = uid()
    db.execute("INSERT INTO policies VALUES (?,?,?,?,?,?,?,?)",
               (policy_id, org, version, min_members, max_income, voucher_value, evidence_required, now()))
    return policy_id


def current_policy(db, org):
    return db.execute("SELECT * FROM policies WHERE org=? ORDER BY version DESC LIMIT 1", (org,)).fetchone()


def add_case(db, actor, citizen, request_id, fields):
    case_id, stamp = uid(), now()
    db.execute(
        """INSERT INTO cases (id,org,citizen,submitted_by,request_id,input_hash,applicant,identity_ref,address,
           family_members,monthly_income,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (case_id, actor["org"], citizen, actor["id"], request_id, digest(as_json(fields)), fields["applicant"],
         fields["identity_ref"], fields["address"], fields["family_members"], fields["monthly_income"],
         "submitted", stamp, stamp),
    )
    return db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.dummy_password = password_hash("unused")

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self, demo=True):
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY)")
            for path in sorted(Path(__file__).with_name("migrations").glob("*.sql")):
                if not db.execute("SELECT 1 FROM migrations WHERE name=?", (path.name,)).fetchone():
                    script = path.read_text(encoding="utf-8")
                    db.executescript("BEGIN IMMEDIATE;\n" + script + f"\nINSERT INTO migrations VALUES ('{path.name}');\nCOMMIT;")
        with self.connect(write=True) as db:
            if db.execute("SELECT 1 FROM organizations LIMIT 1").fetchone():
                return
            db.execute("INSERT INTO organizations VALUES ('harbor','Harbor Community Relief')")
            add_policy(db, "harbor", 1, 3, 35000, 5000, True)
            if demo:
                from .seed import populate
                populate(db)

    def throttle(self, key, maximum, window=60):
        clock = int(time.time())
        with self.connect(write=True) as db:
            db.execute("DELETE FROM limits WHERE expires<=?", (clock,))
            row = db.execute(
                """INSERT INTO limits VALUES (?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1
                   RETURNING count""", (key, clock + window),
            ).fetchone()
        return row["count"] <= maximum
