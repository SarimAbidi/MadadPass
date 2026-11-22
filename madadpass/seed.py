"""Deterministic fictional accounts and cases for a portable live demonstration."""

from .assistance import assess, inspect_document
from .store import add_case, add_policy, as_json, audit, now, password_hash, uid


DEMO_PASSWORD = "MadadDemo26!"
DEMO_ROLES = {
    "citizen": "Amina Rahim",
    "helper": "Sana Yusuf",
    "officer": "Haris Noor",
    "merchant": "Cedar Grocery",
    "admin": "Nadia Karim",
    "auditor": "Rafi Iqbal",
}


def document_sample(members, income):
    return f"FICTIONAL INCOME STATEMENT\nHousehold members: {members}\nMonthly income: PKR {income:,}\n".encode()


def populate(db):
    db.execute("INSERT INTO organizations VALUES ('cedar','Cedar Neighborhood Aid')")
    add_policy(db, "cedar", 1, 2, 32000, 4500, True)
    accounts = {}

    def user(login, name, role, org="harbor"):
        user_id = uid()
        db.execute("INSERT INTO users (id,org,login,name,role,password_hash) VALUES (?,?,?,?,?,?)",
                   (user_id, org, login, name, role, password_hash(DEMO_PASSWORD)))
        result = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        accounts[login] = result
        return result

    for role, name in DEMO_ROLES.items():
        user(role, name, role)
    user("citizen-b", "Muhammad Ahmed", "citizen")
    user("citizen-c", "Mohammad Ahmad", "citizen")
    user("citizen-d", "Zoya Ali", "citizen")
    user("officer-cedar", "Ibrahim Farid", "officer", "cedar")
    customer = user("citizen-cedar", "Maya Saeed", "citizen", "cedar")
    db.execute("INSERT INTO consents VALUES (?,?,?,?,?,NULL)",
               (uid(), accounts["citizen"]["id"], accounts["helper"]["id"], "assist", now()))

    def sample(actor, reference, address, members, income, evidence=True):
        case = add_case(db, actor, actor["id"], uid(), {"applicant": actor["name"], "identity_ref": reference,
                        "address": address, "family_members": members, "monthly_income": income})
        audit(db, actor, "case.submitted", case["id"], seeded=True)
        if evidence:
            content = document_sample(members, income)
            document_id = uid()
            db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?)", (document_id, case["id"], "demo-income.txt",
                       "text/plain", content, as_json(inspect_document(content, "text/plain", False)), now()))
            audit(db, actor, "document.uploaded", case["id"], document=document_id, seeded=True)
        return case

    cases = [sample(accounts["citizen"], "HH-101", "17 Harbor Walk", 5, 30000),
             sample(accounts["citizen-b"], "HH-102", "12 Lantern Road, Harbor", 4, 28500),
             sample(accounts["citizen-c"], "HH-103", "12 Lantern Rd, Harbor", 4, 29200),
             sample(accounts["citizen-d"], "HH-104", "8 Maple Lane, Harbor", 3, None, evidence=False),
             sample(customer, "HH-101", "2 Orchard Court, Cedar", 3, 24000)]
    for case in cases:
        assess(db, db.execute("SELECT * FROM users WHERE id=?", (case["citizen"],)).fetchone(), case)
