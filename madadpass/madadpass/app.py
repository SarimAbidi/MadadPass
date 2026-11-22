import base64
import binascii
import json
import logging
import os
import re
import secrets
import time
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path

from jsonschema import Draft202012Validator
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import assistance
from .security import claims_for, issue_jwt, load_secret, voucher_token
from .seed import DEMO_ROLES, document_sample
from .store import Store, add_case, add_policy, as_json, audit, current_policy, digest, now, password_hash, password_ok, uid
from .vendor.qrencoder import QRCode, QRErrorCorrectLevel


STATIC = Path(__file__).with_name("static")
ROOT = Path(__file__).resolve().parent.parent
MAX_BODY = 8_100_000
ROLES = [*DEMO_ROLES]
STATUSES = ("submitted", "in_review", "needs_information", "escalated", "approved", "rejected")
LOG = logging.getLogger("madadpass")


def string(maximum=180, minimum=0, **constraints):
    return {"type": "string", "minLength": minimum, "maxLength": maximum, **constraints}


def integer(minimum, maximum, nullable=False):
    return {"type": ["integer", "null"] if nullable else "integer", "minimum": minimum, "maximum": maximum}


def choice(*options):
    return {"type": "string", "enum": list(options)}


def shape(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


BOOL = {"type": "boolean"}
IDENTIFIER = string(64, 1, pattern=r"^[A-Za-z0-9_-]+$")
CASE_FIELDS = {
    "applicant": string(100, 1), "identity_ref": string(30, pattern=r"^(HH-[A-Z0-9]{1,20})?$"),
    "address": string(180), "family_members": integer(1, 40, True), "monthly_income": integer(0, 10000000, True),
}
POLICY_FIELDS = {"min_members": integer(1, 40), "max_income": integer(0, 10000000),
                 "voucher_value": integer(100, 100000), "evidence_required": BOOL}
UPLOAD = {"filename": string(120, 1), "mime": string(80, 1), "data": string(7_900_000, 1), "provider": BOOL}


class Api:
    def __init__(self):
        self.routes = []
        self.paths = {}

    def route(self, path, method, title, roles=None, schema=None, *, mock=False):
        def register(handler):
            async def endpoint(request):
                request.state.operation = handler.__name__
                try:
                    actor = await run_in_threadpool(authenticate, request, roles, mock) if roles is not None else None
                    if method not in ("GET", "HEAD") and request.headers.get("origin"):
                        from urllib.parse import urlsplit
                        origin = urlsplit(request.headers["origin"])
                        if origin.scheme not in {"http", "https"} or origin.netloc != request.headers.get("host"):
                            raise HTTPException(403, "This request came from a different origin.")
                    data = {}
                    if schema is not None:
                        if request.headers.get("content-type", "").split(";")[0].lower() != "application/json":
                            raise HTTPException(415, "Send the request as JSON.")
                        body = bytearray()
                        async for chunk in request.stream():
                            body.extend(chunk)
                            if len(body) > MAX_BODY:
                                raise HTTPException(413, "The request is too large.")
                        try:
                            data = json.loads(body)
                        except (ValueError, RecursionError):
                            raise HTTPException(400, "The JSON request could not be read.") from None
                        error = next(Draft202012Validator(schema).iter_errors(data), None)
                        if error:
                            field = str(error.path[0]) if error.path else "request body"
                            raise HTTPException(422, f"Check {field.replace('_', ' ')} for missing or invalid values.")
                    result = await run_in_threadpool(handler, request, actor, data)
                    response = result if isinstance(result, Response) else JSONResponse(result)
                except HTTPException as error:
                    if error.status_code in (401, 403, 429):
                        await run_in_threadpool(record_denial, request, error.status_code)
                    response = JSONResponse({"detail": error.detail}, status_code=error.status_code)
                response.headers["Cache-Control"] = "no-store"
                return response

            self.routes.append(Route(path, endpoint, methods=[method], name=handler.__name__))
            operation = {"summary": title, "operationId": handler.__name__, "responses": {
                "200": {"description": "Success"}, "400": {"description": "Invalid request"},
                "403": {"description": "Permission denied"}, "409": {"description": "Conflicting state"},
                "422": {"description": "Validation failure"},
            }}
            if roles is not None:
                operation["security"] = [{"BearerAuth": []}]
                if mock:
                    operation["security"].append({"MockApiKey": []})
            if schema is not None:
                operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": schema}}}
            parameters = [{"name": key, "in": "path", "required": True, "schema": {"type": "string"}}
                          for key in re.findall(r"\{(\w+)\}", path)]
            if parameters:
                operation["parameters"] = parameters
            self.paths.setdefault(path, {})[method.lower()] = operation
            return handler
        return register


api = Api()


def store(request):
    return request.app.state.store


def secret(request):
    return request.app.state.secret


def record_denial(request, status):
    actor = getattr(request.state, "actor", None)
    if actor is None:
        actor = {"id": None, "org": "harbor", "role": "anonymous"}
    with store(request).connect(write=True) as db:
        audit(db, actor, "access.denied" if status != 429 else "rate_limit.reached",
              getattr(request.state, "audit_case", None), operation=request.state.operation, status=status)


def authenticate(request, roles, mock):
    key = request.headers.get("x-api-key", "")
    if mock and key and len(key) < 160:
        with store(request).connect() as db:
            entry = db.execute("SELECT * FROM api_keys WHERE token_hash=? AND revoked_at IS NULL", (digest(key),)).fetchone()
        if not entry:
            raise HTTPException(401, "The mock API key is invalid.")
        actor = {"id": None, "role": "integration", "org": entry["org"], "key_id": entry["id"]}
        request.state.actor = actor
        return actor
    prefix, _, token = request.headers.get("authorization", "").partition(" ")
    if prefix.lower() != "bearer":
        raise HTTPException(401, "Sign in to continue.")
    claims = claims_for(secret(request), token)
    with store(request).connect() as db:
        actor = db.execute("""SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id
                             WHERE s.id=? AND s.user_id=? AND s.expires>? AND u.active=1""",
                           (claims["jti"], claims["sub"], int(time.time()))).fetchone()
    if actor is None:
        raise HTTPException(401, "This session has ended. Sign in again.")
    request.state.actor, request.state.session = actor, claims["jti"]
    if roles and actor["role"] not in roles:
        raise HTTPException(403, "Your role cannot perform this action.")
    return actor


def throttle(request, name, maximum, actor=None):
    if actor:
        subject = (actor["id"] or actor["key_id"])
    else:
        subject = request.client.host if request.client else "unknown"
    if not store(request).throttle(f"{name}:{subject}", maximum):
        raise HTTPException(429, "Too many requests. Try again in a minute.")


def public_user(user):
    return {field: user[field] for field in ("id", "login", "name", "role", "org", "active")}


def consent_for(db, citizen, helper):
    return db.execute("SELECT * FROM consents WHERE citizen=? AND helper=? AND revoked_at IS NULL", (citizen, helper)).fetchone()


def case_for(request, db, actor, case_id=None, access="case"):
    case_id = case_id or request.path_params["id"]
    case = db.execute("SELECT * FROM cases WHERE id=? AND org=?", (case_id, actor["org"])).fetchone()
    if not case:
        raise HTTPException(404, "Application not found in this workspace.")
    request.state.audit_case = case_id
    if actor["role"] == "officer" and access != "edit":
        return case
    if actor["role"] == "citizen" and case["citizen"] == actor["id"]:
        return case
    if actor["role"] == "helper":
        consent = consent_for(db, case["citizen"], actor["id"])
        scopes = {"case": ("case", "documents", "assist"), "documents": ("documents", "assist"), "edit": ("assist",)}
        if consent and consent["scope"] in scopes[access]:
            return case
    raise HTTPException(403, "You do not have consent or permission to access this application.")


def case_output(db, case, actor, detailed=False):
    result = {field: case[field] for field in ("id", "applicant", "family_members", "monthly_income", "status", "created_at", "updated_at")}
    if actor["role"] == "officer":
        result["assigned_to"] = case["assigned_to"]
    assessment = db.execute("""SELECT a.*,p.version FROM assessments a JOIN policies p ON p.id=a.policy_id
                               WHERE a.case_id=? ORDER BY a.rowid DESC LIMIT 1""", (case["id"],)).fetchone()
    if assessment:
        found = json.loads(assessment["findings"])
        keys = ("route", "explanation", "warnings", "missing", "failed", "references")
        if actor["role"] == "officer" and detailed:
            keys = tuple(found)
        result["assessment"] = {**{key: found[key] for key in keys}, "id": assessment["id"], "version": assessment["version"],
                                "created_at": assessment["created_at"]}
    if not detailed:
        return result
    result.update({"identity_ref": case["identity_ref"], "address": case["address"], "citizen": case["citizen"],
                   "current_policy": current_policy(db, case["org"])["version"]})
    decisions = db.execute("""SELECT d.action,d.reason,d.created_at,p.version FROM decisions d
                             JOIN assessments a ON a.id=d.assessment JOIN policies p ON p.id=a.policy_id
                             WHERE d.case_id=? ORDER BY d.created_at DESC""", (case["id"],)).fetchall()
    result["decisions"] = [dict(row) for row in decisions]
    can_read_documents = actor["role"] != "helper" or consent_for(db, case["citizen"], actor["id"])["scope"] in ("documents", "assist")
    result["documents"] = [dict(id=row["id"], filename=row["filename"], mime=row["mime"], created_at=row["created_at"],
                                extraction=json.loads(row["extraction"])) for row in db.execute(
                                    "SELECT id,filename,mime,created_at,extraction FROM documents WHERE case_id=? ORDER BY created_at", (case["id"],)
                                )] if can_read_documents else []
    if actor["role"] == "citizen":
        voucher = db.execute("SELECT id,value,status,expires FROM vouchers WHERE case_id=?", (case["id"],)).fetchone()
        result["voucher"] = voucher_summary(voucher) if voucher else None
    return result


def voucher_summary(voucher):
    result = dict(voucher)
    if result["status"] == "issued" and result["expires"] <= time.time():
        result["status"] = "expired"
    return result


@api.route("/health", "GET", "Check database and service health")
def health(request, actor, data):
    with store(request).connect() as db:
        db.execute("SELECT 1 FROM policies LIMIT 1").fetchone()
    return {"status": "ok", "database": "sqlite", "demo": request.app.state.demo,
            "gemini": bool(os.getenv("GEMINI_API_KEY")), "groq": bool(os.getenv("GROQ_API_KEY"))}


@api.route("/api/login", "POST", "Sign in with a synthetic account", schema=shape(
    {"login": string(40, 1), "password": string(160, 1)}, ("login", "password")))
def login(request, actor, data):
    throttle(request, "login", 30)
    with store(request).connect() as db:
        user = db.execute("SELECT * FROM users WHERE login=?", (data["login"].strip().lower(),)).fetchone()
    valid = password_ok(data["password"], user["password_hash"] if user else store(request).dummy_password)
    if not user or not user["active"] or not valid:
        raise HTTPException(401, "Username or password is incorrect.")
    session, expires = uid(), int(time.time()) + 8 * 60 * 60
    with store(request).connect(write=True) as db:
        db.execute("DELETE FROM sessions WHERE expires<=?", (int(time.time()),))
        db.execute("INSERT INTO sessions VALUES (?,?,?)", (session, user["id"], expires))
        audit(db, user, "session.started")
    return {"token": issue_jwt(secret(request), user["id"], session, expires), "user": public_user(user)}


@api.route("/api/register", "POST", "Register a synthetic citizen", schema=shape({
    "login": string(40, 3, pattern=r"^[a-z0-9][a-z0-9_-]+$"), "name": string(100, 2),
    "password": string(160, 10), "synthetic": BOOL,
}, ("login", "name", "password", "synthetic")))
def register(request, actor, data):
    throttle(request, "register", 8)
    if not data["synthetic"] or not data["name"].strip():
        raise HTTPException(422, "Confirm that the name and account are fictional.")
    hashed = password_hash(data["password"])
    with store(request).connect(write=True) as db:
        if db.execute("SELECT 1 FROM users WHERE login=?", (data["login"],)).fetchone():
            raise HTTPException(409, "That username is already used.")
        user = {"id": uid(), "role": "citizen", "org": "harbor"}
        db.execute("INSERT INTO users (id,org,login,name,role,password_hash) VALUES (?,?,?,?,?,?)",
                   (user["id"], user["org"], data["login"], data["name"].strip(), user["role"], hashed))
        audit(db, user, "user.registered")
    return {"registered": True}


@api.route("/api/logout", "POST", "Revoke the current login session", roles=())
def logout(request, actor, data):
    with store(request).connect(write=True) as db:
        db.execute("DELETE FROM sessions WHERE id=?", (request.state.session,))
        audit(db, actor, "session.ended")
    return {"signed_out": True}


@api.route("/api/me", "GET", "Read the signed-in account", roles=())
def me(request, actor, data):
    with store(request).connect() as db:
        result = public_user(actor)
        result["organization"] = db.execute("SELECT name FROM organizations WHERE id=?", (actor["org"],)).fetchone()["name"]
    return result


@api.route("/api/cases", "GET", "List authorized applications", roles=("citizen", "helper", "officer"))
def list_cases(request, actor, data):
    with store(request).connect() as db:
        role, org, actor_id = actor["role"], actor["org"], actor["id"]
        if role == "citizen":
            rows = db.execute("SELECT * FROM cases WHERE org=? AND citizen=? ORDER BY updated_at DESC", (org, actor_id))
        elif role == "helper":
            rows = db.execute("""SELECT c.* FROM cases c JOIN consents s ON c.citizen=s.citizen
                                 WHERE c.org=? AND s.helper=? AND s.revoked_at IS NULL ORDER BY c.updated_at DESC""", (org, actor_id))
        else:
            rows = db.execute("SELECT * FROM cases WHERE org=? ORDER BY updated_at DESC", (org,))
        results = [case_output(db, row, actor) for row in rows]
    return {"cases": results}


@api.route("/api/cases", "POST", "Create an application and run the assessment", roles=("citizen", "helper"), schema=shape(
    {**CASE_FIELDS, "citizen": IDENTIFIER, "request_id": IDENTIFIER, "confirmed": BOOL},
    (*CASE_FIELDS, "request_id", "confirmed")))
def submit_case(request, actor, data):
    throttle(request, "submit", 30, actor)
    citizen = data.get("citizen", actor["id"])
    if not data["confirmed"]:
        raise HTTPException(422, "Confirm the application details before you submit.")
    with store(request).connect(write=True) as db:
        if actor["role"] == "helper":
            owner = db.execute("SELECT * FROM users WHERE org=? AND id=? AND role='citizen' AND active=1", (actor["org"], citizen)).fetchone()
            permission = consent_for(db, citizen, actor["id"])
            if not owner or not permission or permission["scope"] != "assist":
                raise HTTPException(403, "This citizen has not granted permission to file an application.")
        elif citizen != actor["id"]:
            raise HTTPException(403, "Submit an application only for your own account.")
        fields = cleaned_fields(data)
        existing = db.execute("SELECT * FROM cases WHERE submitted_by=? AND request_id=?", (actor["id"], data["request_id"])).fetchone()
        if existing:
            if existing["citizen"] != citizen or existing["input_hash"] != digest(as_json(fields)):
                raise HTTPException(409, "This submission reference belongs to a different application.")
            return {"case": case_output(db, existing, actor, True), "replayed": True}
        case = add_case(db, actor, citizen, data["request_id"], fields)
        audit(db, actor, "case.submitted", case["id"], consent_used=actor["role"] == "helper")
        assistance.assess(db, actor, case)
        return JSONResponse({"case": case_output(db, case, actor, True), "replayed": False}, status_code=201)


def cleaned_fields(data):
    result = {key: data[key].strip() if isinstance(data[key], str) else data[key] for key in CASE_FIELDS}
    if not result["applicant"]:
        raise HTTPException(422, "Add a fictional applicant name.")
    return result


@api.route("/api/cases/{id}", "GET", "Read an authorized case, assessment and decisions", roles=("citizen", "helper", "officer"))
def get_case(request, actor, data):
    with store(request).connect(write=True) as db:
        case = case_for(request, db, actor)
        audit(db, actor, "case.viewed", case["id"])
        return {"case": case_output(db, case, actor, True)}


@api.route("/api/cases/{id}", "PATCH", "Correct application details and reassess", roles=("citizen", "helper"),
           schema=shape({**CASE_FIELDS, "confirmed": BOOL}, (*CASE_FIELDS, "confirmed")))
def correct_case(request, actor, data):
    if not data["confirmed"]:
        raise HTTPException(422, "Confirm the corrected details.")
    fields = cleaned_fields(data)
    with store(request).connect(write=True) as db:
        case = case_for(request, db, actor, access="edit")
        if case["status"] in ("approved", "rejected"):
            raise HTTPException(409, "This decision is closed. Submit a new application for corrections.")
        db.execute("""UPDATE cases SET applicant=?,identity_ref=?,address=?,family_members=?,monthly_income=?,
                      status='submitted',updated_at=? WHERE id=?""", (*fields.values(), now(), case["id"]))
        case = case_for(request, db, actor)
        audit(db, actor, "case.corrected", case["id"])
        assistance.assess(db, actor, case)
        return {"case": case_output(db, case, actor, True)}


@api.route("/api/ai/extract", "POST", "Suggest editable application fields", roles=("citizen", "helper"),
           schema=shape({"text": string(3500, 1), "provider": BOOL}, ("text",)))
def extract(request, actor, data):
    throttle(request, "extract", 24, actor)
    result = assistance.extract_application(data["text"], data.get("provider", False))
    with store(request).connect(write=True) as db:
        audit(db, actor, "ai.fields_suggested", model=result["model"], provided=[key for key, val in result["fields"].items() if val is not None],
              input_length=len(data["text"]), fallback=bool(result.get("notice")))
    return result


@api.route("/api/ai/transcribe", "POST", "Transcribe explicitly shared synthetic Urdu audio", roles=("citizen", "helper"),
           schema=shape(UPLOAD, ("filename", "mime", "data", "provider")))
def transcribe(request, actor, data):
    throttle(request, "transcribe", 8, actor)
    formats = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav", "audio/mpeg": "mp3", "audio/mp4": "mp4"}
    mime = data["mime"].split(";")[0]
    content = file_bytes(data["data"], 5_500_000)
    if not data["provider"] or mime not in formats:
        raise HTTPException(422, "Confirm cloud audio sharing and use a WebM, Ogg, WAV, MP3 or MP4 recording.")
    try:
        result = assistance.transcribe(content, formats[mime], mime)
    except assistance.ProviderUnavailable as error:
        with store(request).connect(write=True) as db:
            audit(db, actor, "ai.transcription_unavailable", mime=mime)
        raise HTTPException(503, str(error)) from None
    with store(request).connect(write=True) as db:
        audit(db, actor, "ai.transcribed", model=result["model"], output_length=len(result["text"]))
    return result


@api.route("/api/ai/message", "POST", "Route suspicious relief payment messages", roles=(),
           schema=shape({"text": string(3000, 1)}, ("text",)))
def check_message(request, actor, data):
    throttle(request, "message", 24, actor)
    result = assistance.classify_text(data["text"])
    with store(request).connect(write=True) as db:
        audit(db, actor, "ai.message_screened", route=result["route"], signals=result["signals"], model=result["model"])
    return result


@api.route("/api/ai/policy", "POST", "Retrieve current grocery policy passages with sources", roles=(),
           schema=shape({"query": string(250, 1)}, ("query",)))
def query_policy(request, actor, data):
    throttle(request, "policy", 24, actor)
    with store(request).connect(write=True) as db:
        policy = current_policy(db, actor["org"])
        excerpts = assistance.retrieve(policy, data["query"])
        audit(db, actor, "ai.policy_retrieved", version=policy["version"], rules=[entry["rule"] for entry in excerpts], model="policy-tfidf-v1")
    return {"references": excerpts, "version": policy["version"]}


@api.route("/api/ai/assess/{id}", "POST", "Reassess an open case under the current policy", roles=("officer",))
def reassess(request, actor, data):
    throttle(request, "assess", 35, actor)
    with store(request).connect(write=True) as db:
        case = case_for(request, db, actor)
        if case["status"] in ("approved", "rejected"):
            raise HTTPException(409, "Closed decisions keep their historical assessment.")
        assistance.assess(db, actor, case)
        return {"case": case_output(db, case, actor, True)}


def file_bytes(encoded, limit):
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "This file could not be read.") from None
    if not content or len(content) > limit:
        raise HTTPException(413, "This file is empty or exceeds the upload limit.")
    return content


@api.route("/api/cases/{id}/documents", "POST", "Store and inspect synthetic evidence", roles=("citizen", "helper"),
           schema=shape({**UPLOAD, "synthetic": BOOL}, ("filename", "mime", "data", "synthetic")))
def upload_document(request, actor, data):
    throttle(request, "document", 12, actor)
    if not data["synthetic"]:
        raise HTTPException(422, "Confirm that the document is fictional.")
    allowed = {"text/plain": None, "image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}
    mime = data["mime"]
    if mime not in allowed:
        raise HTTPException(415, "Use a UTF-8 text, JPEG or PNG document.")
    content = file_bytes(data["data"], 3_000_000)
    if mime == "text/plain":
        try:
            content.decode("utf-8")
        except UnicodeError:
            raise HTTPException(422, "Save the text document in UTF-8 format.") from None
    elif not content.startswith(allowed[mime]):
        raise HTTPException(422, "The file does not match its image format.")
    with store(request).connect() as db:
        case = case_for(request, db, actor, access="edit")
        if case["status"] in ("approved", "rejected"):
            raise HTTPException(409, "This case has been closed.")
    result = assistance.inspect_document(content, mime, data.get("provider", False))
    document_id = uid()
    filename = re.sub(r"[\\/\x00-\x1f\x7f]", "_", data["filename"]).strip() or "evidence"
    with store(request).connect(write=True) as db:
        case = case_for(request, db, actor, access="edit")
        if case["status"] in ("approved", "rejected"):
            raise HTTPException(409, "This case has been closed.")
        if db.execute("SELECT count(*) AS count FROM documents WHERE case_id=?", (case["id"],)).fetchone()["count"] >= 10:
            raise HTTPException(409, "This application has reached its evidence limit.")
        db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?)",
                   (document_id, case["id"], filename, mime, content, as_json(result), now()))
        db.execute("UPDATE cases SET status='submitted', updated_at=? WHERE id=?", (now(), case["id"]))
        case = case_for(request, db, actor)
        audit(db, actor, "document.uploaded", case["id"], document=document_id, mime=mime)
        audit(db, actor, "ai.document_inspected", case["id"], document=document_id, model=result["model"],
              document_type=result["document_type"], review_required=True)
        assistance.assess(db, actor, case)
        return JSONResponse({"case": case_output(db, case, actor, True)}, status_code=201)


@api.route("/api/cases/{id}/documents/{document}", "GET", "Read consent-protected evidence", roles=("citizen", "helper", "officer"))
def read_document(request, actor, data):
    with store(request).connect(write=True) as db:
        case = case_for(request, db, actor, access="documents")
        document = db.execute("SELECT * FROM documents WHERE id=? AND case_id=?", (request.path_params["document"], case["id"])).fetchone()
        if not document:
            raise HTTPException(404, "The document could not be found.")
        audit(db, actor, "document.viewed", case["id"], document=document["id"])
    return Response(document["content"], media_type=document["mime"], headers={
        "Content-Disposition": "attachment", "Content-Security-Policy": "default-src 'none'; sandbox", "X-Content-Type-Options": "nosniff",
    })


@api.route("/api/cases/{id}/assignment", "PATCH", "Assign an open case to a workspace officer", roles=("officer",),
           schema=shape({"officer": IDENTIFIER}))
def assign_case(request, actor, data):
    assignee = data.get("officer", actor["id"])
    with store(request).connect(write=True) as db:
        case = case_for(request, db, actor)
        officer = db.execute("SELECT id FROM users WHERE id=? AND org=? AND role='officer' AND active=1", (assignee, actor["org"])).fetchone()
        if not officer:
            raise HTTPException(404, "Active officer not found in the workspace.")
        if case["status"] in ("approved", "rejected"):
            raise HTTPException(409, "This case is already closed.")
        db.execute("UPDATE cases SET assigned_to=?, status='in_review', updated_at=? WHERE id=?", (assignee, now(), case["id"]))
        audit(db, actor, "case.assigned", case["id"], officer=assignee)
        return {"case": case_output(db, case_for(request, db, actor), actor, True)}


@api.route("/api/cases/{id}/decision", "PATCH", "Decide or escalate with a recorded reason", roles=("officer",), schema=shape({
    "assessment": IDENTIFIER, "action": choice("approve", "reject", "request_information", "escalate"),
    "reason": string(1200, 8), "verified": BOOL,
}, ("assessment", "action", "reason", "verified")))
def decide(request, actor, data):
    reason = data["reason"].strip()
    if len(reason) < 8:
        raise HTTPException(422, "Enter an explanation of at least eight characters.")
    with store(request).connect(write=True) as db:
        case = case_for(request, db, actor)
        if case["status"] in ("approved", "rejected"):
            raise HTTPException(409, "This case already has a final decision.")
        if case["assigned_to"] and case["assigned_to"] != actor["id"]:
            raise HTTPException(403, "Assign this application to yourself before deciding.")
        policy = current_policy(db, actor["org"])
        assessment = db.execute("SELECT * FROM assessments WHERE case_id=? ORDER BY rowid DESC LIMIT 1", (case["id"],)).fetchone()
        if assessment is None or assessment["id"] != data["assessment"] or assessment["policy_id"] != policy["id"]:
            raise HTTPException(409, "This assessment changed or the policy was updated. Reassess and review before deciding.")
        action = data["action"]
        findings = json.loads(assessment["findings"])
        if action == "approve":
            live = assistance.evaluate(db, case, policy)
            if live != findings:
                raise HTTPException(409, "New information affected this assessment. Reassess and review before approving.")
            if findings["missing"] or findings["failed"]:
                raise HTTPException(409, "The application has missing information or fails the current rules.")
            if not data["verified"] or (findings["warnings"] and len(reason) < 20):
                raise HTTPException(422, "Verify the application and explain how you resolved any flagged information.")
        state = {"approve": "approved", "reject": "rejected", "request_information": "needs_information", "escalate": "escalated"}[action]
        db.execute("UPDATE cases SET status=?,assigned_to=?,updated_at=? WHERE id=?", (state, actor["id"], now(), case["id"]))
        decision_id = uid()
        db.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?)", (decision_id, case["id"], actor["id"], assessment["id"], action, reason, now()))
        audit(db, actor, "case.decision", case["id"], decision=decision_id, action_taken=action, version=policy["version"], verified=data["verified"])
        case = case_for(request, db, actor)
        if action == "approve":
            issue_voucher(db, actor, case, secret(request))
        return {"case": case_output(db, case, actor, True)}


def issue_voucher(db, actor, case, key):
    voucher = db.execute("SELECT id,value,status,expires FROM vouchers WHERE case_id=?", (case["id"],)).fetchone()
    if voucher:
        return voucher_summary(voucher)
    if case["status"] != "approved":
        raise HTTPException(409, "An officer must approve this application before a voucher can be issued.")
    rule = db.execute("""SELECT p.voucher_value FROM policies p JOIN assessments a ON p.id=a.policy_id
                         JOIN decisions d ON d.assessment=a.id WHERE d.case_id=? AND d.action='approve'""", (case["id"],)).fetchone()
    if not rule:
        raise HTTPException(409, "An approval record is required.")
    voucher_id, expiry = uid(), int(time.time()) + 86400
    db.execute("INSERT INTO vouchers VALUES (?,?,?,?,?,?,?)", (voucher_id, case["id"], digest(voucher_token(key, voucher_id)),
               rule["voucher_value"], "issued", expiry, now()))
    audit(db, actor, "voucher.issued", case["id"], voucher=voucher_id, value=rule["voucher_value"], expires=expiry)
    return {"id": voucher_id, "value": rule["voucher_value"], "status": "issued", "expires": expiry}


@api.route("/api/vouchers", "POST", "Issue or retrieve an approved voucher", roles=("officer",),
           schema=shape({"case": IDENTIFIER}, ("case",)))
def issue(request, actor, data):
    with store(request).connect(write=True) as db:
        case = case_for(request, db, actor, case_id=data["case"])
        if case["assigned_to"] != actor["id"]:
            raise HTTPException(403, "Only the approving officer may issue this voucher.")
        return {"voucher": issue_voucher(db, actor, case, secret(request))}


def voucher_for(db, request, actor):
    voucher = db.execute("""SELECT v.* FROM vouchers v JOIN cases c ON c.id=v.case_id
                            WHERE v.id=? AND c.org=? AND c.citizen=? AND c.status='approved'""",
                         (request.path_params["id"], actor["org"], actor["id"])).fetchone()
    if not voucher:
        raise HTTPException(404, "Voucher not found for this account.")
    if voucher["status"] != "issued" or voucher["expires"] <= time.time():
        raise HTTPException(409, "This voucher is no longer active.")
    return voucher


@api.route("/api/vouchers/{id}", "GET", "Reveal an active voucher token to its owner", roles=("citizen",))
def reveal_voucher(request, actor, data):
    with store(request).connect(write=True) as db:
        voucher = voucher_for(db, request, actor)
        audit(db, actor, "voucher.viewed", voucher["case_id"], voucher=voucher["id"])
    return {"token": voucher_token(secret(request), voucher["id"]), "voucher": voucher_summary(voucher)}


@api.route("/api/vouchers/{id}/qr", "GET", "Render the owner's private voucher QR code", roles=("citizen",))
def render_qr(request, actor, data):
    with store(request).connect() as db:
        voucher = voucher_for(db, request, actor)
    code = QRCode(4, QRErrorCorrectLevel.M)
    code.addData(voucher_token(secret(request), voucher["id"]))
    code.make()
    size = code.moduleCount + 8
    path = "".join(f"M{x+4},{y+4}h1v1h-1z" for y, line in enumerate(code.modules) for x, dark in enumerate(line) if dark)
    return Response(f'<svg xmlns="http://www.w3.org/2000/svg" width="246" height="246" viewBox="0 0 {size} {size}" shape-rendering="crispEdges">'
                    f'<rect width="{size}" height="{size}" fill="white"/><path d="{path}" fill="black"/></svg>', media_type="image/svg+xml")


@api.route("/api/vouchers/redeem", "POST", "Redeem exactly once and record a mock settlement", roles=("merchant",),
           schema=shape({"token": string(80, 1), "request_id": IDENTIFIER}, ("token", "request_id")))
def redeem(request, actor, data):
    throttle(request, "redeem", 35, actor)
    token = data["token"].strip()
    if not re.fullmatch(r"mp_[A-Za-z0-9_-]{43}", token):
        raise HTTPException(422, "This is not a MadadPass voucher code.")
    with store(request).connect(write=True) as db:
        voucher = db.execute("""SELECT v.*,c.status AS case_status FROM vouchers v JOIN cases c ON c.id=v.case_id
                                WHERE v.token_hash=? AND c.org=?""", (digest(token), actor["org"])).fetchone()
        if not voucher or voucher["case_status"] != "approved":
            raise HTTPException(404, "Voucher not found in this workspace.")
        prior = db.execute("SELECT * FROM redemptions WHERE merchant=? AND request_id=?", (actor["id"], data["request_id"])).fetchone()
        if prior:
            if prior["voucher_id"] != voucher["id"]:
                raise HTTPException(409, "This request reference was used for a different voucher.")
            return redemption_output(db, prior, voucher, True)
        if voucher["status"] == "redeemed":
            error = "This voucher has already been redeemed."
            audit(db, actor, "voucher.redemption_blocked", voucher["case_id"], voucher=voucher["id"], cause="already_redeemed")
        elif not db.execute("UPDATE vouchers SET status='redeemed' WHERE id=? AND status='issued' AND expires>?",
                            (voucher["id"], int(time.time()))).rowcount:
            error = "This voucher has expired."
            audit(db, actor, "voucher.redemption_blocked", voucher["case_id"], voucher=voucher["id"], cause="expired")
        else:
            redemption_id = uid()
            db.execute("INSERT INTO redemptions VALUES (?,?,?,?,?)", (redemption_id, voucher["id"], actor["id"], data["request_id"], now()))
            prior = db.execute("SELECT * FROM redemptions WHERE id=?", (redemption_id,)).fetchone()
            settlement = settle(db, redemption_id)
            audit(db, actor, "voucher.redeemed", voucher["case_id"], voucher=voucher["id"], redemption=redemption_id, settlement=settlement)
            return JSONResponse(redemption_output(db, prior, voucher, False), status_code=201)
    raise HTTPException(409, error)


def settle(db, redemption_id):
    db.execute("INSERT INTO settlements VALUES (?,?,?) ON CONFLICT(redemption_id) DO NOTHING", (uid(), redemption_id, now()))
    return db.execute("SELECT id FROM settlements WHERE redemption_id=?", (redemption_id,)).fetchone()["id"]


def redemption_output(db, entry, voucher, replayed):
    settlement = db.execute("SELECT id FROM settlements WHERE redemption_id=?", (entry["id"],)).fetchone()
    return {"id": entry["id"], "value": voucher["value"], "created_at": entry["created_at"],
            "settlement": settlement["id"], "simulated": True, "replayed": replayed}


@api.route("/api/consents", "GET", "List helper sharing and withdrawal history", roles=("citizen", "helper"))
def consents(request, actor, data):
    with store(request).connect() as db:
        if actor["role"] == "citizen":
            rows = db.execute("""SELECT s.*,u.name AS helper_name FROM consents s JOIN users u ON u.id=s.helper
                                 WHERE s.citizen=? ORDER BY granted_at DESC""", (actor["id"],))
            available = [public_user(row) for row in db.execute("SELECT * FROM users WHERE org=? AND role='helper' AND active=1 ORDER BY name", (actor["org"],))]
        else:
            rows = db.execute("""SELECT s.*,u.name AS citizen_name FROM consents s JOIN users u ON u.id=s.citizen
                                 WHERE s.helper=? AND s.revoked_at IS NULL AND u.active=1 ORDER BY name""", (actor["id"],))
            available = []
        return {"consents": [dict(row) for row in rows], "helpers": available}


@api.route("/api/consents", "POST", "Grant or replace a helper's access scope", roles=("citizen",), schema=shape({
    "helper": IDENTIFIER, "scope": choice("case", "documents", "assist"),
}, ("helper", "scope")))
def grant(request, actor, data):
    with store(request).connect(write=True) as db:
        helper = db.execute("SELECT id FROM users WHERE id=? AND org=? AND role='helper' AND active=1", (data["helper"], actor["org"])).fetchone()
        if not helper:
            raise HTTPException(404, "Active helper not found in this workspace.")
        active = consent_for(db, actor["id"], helper["id"])
        if active and active["scope"] == data["scope"]:
            return {"id": active["id"], "scope": active["scope"]}
        if active:
            db.execute("UPDATE consents SET revoked_at=? WHERE id=?", (now(), active["id"]))
            audit(db, actor, "consent.revoked", consent=active["id"], helper=helper["id"], reason="scope_changed")
        entry = uid()
        db.execute("INSERT INTO consents VALUES (?,?,?,?,?,NULL)", (entry, actor["id"], helper["id"], data["scope"], now()))
        audit(db, actor, "consent.granted", consent=entry, helper=helper["id"], scope=data["scope"])
        return {"id": entry, "scope": data["scope"]}


@api.route("/api/consents/{id}/revoke", "POST", "Withdraw consent immediately", roles=("citizen",))
def revoke(request, actor, data):
    with store(request).connect(write=True) as db:
        entry = db.execute("SELECT * FROM consents WHERE id=? AND citizen=?", (request.path_params["id"], actor["id"])).fetchone()
        if not entry:
            raise HTTPException(404, "Consent record not found.")
        if entry["revoked_at"] is None:
            db.execute("UPDATE consents SET revoked_at=? WHERE id=?", (now(), entry["id"]))
            audit(db, actor, "consent.revoked", consent=entry["id"], helper=entry["helper"], scope=entry["scope"])
    return {"revoked": True}


@api.route("/api/admin/rules", "GET", "Read versioned grocery policies", roles=("admin", "officer", "auditor"))
def policies(request, actor, data):
    with store(request).connect() as db:
        versions = db.execute("SELECT * FROM policies WHERE org=? ORDER BY version DESC", (actor["org"],))
        return {"policies": [{**dict(row), "excerpts": assistance.policy_text(row)} for row in versions]}


@api.route("/api/admin/rules/preview", "POST", "Simulate proposed rules on workspace cases", roles=("admin",),
           schema=shape(POLICY_FIELDS, POLICY_FIELDS))
def preview_rules(request, actor, data):
    throttle(request, "preview", 20, actor)
    with store(request).connect(write=True) as db:
        policy = dict(current_policy(db, actor["org"]))
        proposed = {**policy, **data, "version": policy["version"] + 1, "id": "preview"}
        rows = db.execute("SELECT * FROM cases WHERE org=? AND status NOT IN ('approved','rejected')", (actor["org"],)).fetchall()
        counts = Counter()
        for row in rows:
            before, after = (assistance.evaluate(db, row, rule)["route"] for rule in (policy, proposed))
            if before != after:
                counts[(before, after)] += 1
        audit(db, actor, "policy.previewed", current_version=policy["version"], reviewed=len(rows), changed=sum(counts.values()))
        return {"reviewed": len(rows), "changed": sum(counts.values()),
                "transitions": [{"from": before, "to": after, "count": count} for (before, after), count in counts.items()]}


@api.route("/api/admin/rules", "PATCH", "Publish a new policy version", roles=("admin",),
           schema=shape({**POLICY_FIELDS, "base_version": integer(1, 1000000)}, (*POLICY_FIELDS, "base_version")))
def publish_rules(request, actor, data):
    with store(request).connect(write=True) as db:
        current = current_policy(db, actor["org"])
        if current["version"] != data["base_version"]:
            raise HTTPException(409, "Another administrator already published a rule version. Refresh and review it.")
        version = current["version"] + 1
        policy = add_policy(db, actor["org"], version, **{key: data[key] for key in POLICY_FIELDS})
        audit(db, actor, "policy.published", version=version, policy=policy, previous=current["version"])
    return {"version": version, "id": policy}


@api.route("/api/admin/users", "GET", "List workspace user roles", roles=("admin",))
def users(request, actor, data):
    with store(request).connect() as db:
        return {"users": [public_user(row) for row in db.execute("SELECT * FROM users WHERE org=? ORDER BY role,name", (actor["org"],))]}


@api.route("/api/admin/users/{id}", "PATCH", "Change a workspace staff role or access state", roles=("admin",),
           schema=shape({"role": choice(*ROLES), "active": BOOL}, ("role", "active")))
def update_user(request, actor, data):
    with store(request).connect(write=True) as db:
        target = db.execute("SELECT * FROM users WHERE id=? AND org=?", (request.path_params["id"], actor["org"])).fetchone()
        if not target:
            raise HTTPException(404, "User not found in this workspace.")
        if target["id"] == actor["id"]:
            raise HTTPException(409, "You cannot change your own access.")
        if (target["role"] == "citizen") != (data["role"] == "citizen"):
            raise HTTPException(409, "Citizen accounts retain their role to protect application ownership.")
        if target["role"] == "helper" and (not data["active"] or data["role"] != "helper"):
            db.execute("UPDATE consents SET revoked_at=? WHERE helper=? AND revoked_at IS NULL", (now(), target["id"]))
        if target["role"] == "officer" and (not data["active"] or data["role"] != "officer"):
            db.execute("UPDATE cases SET assigned_to=NULL WHERE assigned_to=? AND status NOT IN ('approved','rejected')", (target["id"],))
        db.execute("UPDATE users SET role=?,active=? WHERE id=?", (data["role"], data["active"], target["id"]))
        db.execute("DELETE FROM sessions WHERE user_id=?", (target["id"],))
        audit(db, actor, "user.access_updated", user=target["id"], previous_role=target["role"], role_now=data["role"], active=data["active"])
    return {"updated": True}


@api.route("/api/admin/keys", "GET", "List mock-service API keys", roles=("admin",))
def keys(request, actor, data):
    with store(request).connect() as db:
        return {"keys": [dict(row) for row in db.execute(
            "SELECT id,label,created_at,revoked_at FROM api_keys WHERE org=? ORDER BY created_at DESC", (actor["org"],))]}


@api.route("/api/admin/keys", "POST", "Create a scoped mock-service API key", roles=("admin",),
           schema=shape({"label": string(60, 1)}, ("label",)))
def create_key(request, actor, data):
    throttle(request, "key", 12, actor)
    token = "mpk_" + secrets.token_urlsafe(32)
    key_id = uid()
    with store(request).connect(write=True) as db:
        db.execute("INSERT INTO api_keys VALUES (?,?,?,?,?,NULL)", (key_id, actor["org"], digest(token), data["label"].strip(), now()))
        audit(db, actor, "key.created", key=key_id)
    return {"id": key_id, "key": token}


@api.route("/api/admin/keys/{id}", "DELETE", "Revoke a mock-service API key", roles=("admin",))
def revoke_key(request, actor, data):
    with store(request).connect(write=True) as db:
        if not db.execute("UPDATE api_keys SET revoked_at=? WHERE id=? AND org=? AND revoked_at IS NULL",
                          (now(), request.path_params["id"], actor["org"])).rowcount:
            raise HTTPException(404, "Active mock API key not found.")
        audit(db, actor, "key.revoked", key=request.path_params["id"])
    return {"revoked": True}


@api.route("/api/audit", "GET", "Read recent redacted workspace audit events", roles=("auditor", "admin"))
def audit_list(request, actor, data):
    with store(request).connect() as db:
        rows = db.execute("SELECT * FROM audit WHERE org=? ORDER BY id DESC LIMIT 150", (actor["org"],))
        result = [{**dict(row), "details": json.loads(row["details"])} for row in rows]
        return {"events": result}


@api.route("/api/audit/{id}", "GET", "Read a redacted case's policy, decision and model provenance", roles=("auditor",))
def audit_case(request, actor, data):
    with store(request).connect() as db:
        case_id = request.path_params["id"]
        if not db.execute("SELECT 1 FROM cases WHERE org=? AND id=?", (actor["org"], case_id)).fetchone():
            raise HTTPException(404, "Application not found in this workspace.")
        rows = db.execute("""SELECT a.id,a.created_at,a.findings,p.version FROM assessments a JOIN policies p ON p.id=a.policy_id
                             WHERE a.case_id=? ORDER BY a.rowid DESC""", (case_id,))
        entries = []
        for row in rows:
            findings = json.loads(row["findings"])
            entries.append({**dict(row), "findings": findings})
        events = db.execute("SELECT * FROM audit WHERE org=? AND case_id=? ORDER BY id DESC LIMIT 100", (actor["org"], case_id))
        decisions = db.execute("SELECT id,assessment,action,officer,created_at FROM decisions WHERE case_id=?", (case_id,))
        return {"case": case_id, "assessments": entries, "decisions": [dict(row) for row in decisions],
                "events": [{**dict(row), "details": json.loads(row["details"])} for row in events]}


@api.route("/mock/identity/{id}", "GET", "Check a fictional household reference", roles=("admin", "officer"), mock=True)
def identity_mock(request, actor, data):
    throttle(request, "mock", 40, actor)
    reference = request.path_params["id"]
    if not re.fullmatch(r"HH-[A-Z0-9]{1,20}", reference):
        raise HTTPException(422, "Enter a fictional reference in HH-101 format.")
    result = assistance.mock_identity(reference)
    with store(request).connect(write=True) as db:
        audit(db, actor, "mock.identity_checked", result=result["status"])
    return result


@api.route("/mock/data/{id}", "GET", "Read a fixed synthetic public-data fixture", roles=("admin", "officer"), mock=True)
def data_mock(request, actor, data):
    throttle(request, "mock", 40, actor)
    fixtures = {"harbor": {"zone": "Harbor", "program": "Grocery relief"}, "cedar": {"zone": "Cedar", "program": "Grocery relief"}}
    fixture = fixtures.get(request.path_params["id"])
    if not fixture:
        raise HTTPException(404, "Fictional data fixture not found.")
    with store(request).connect(write=True) as db:
        audit(db, actor, "mock.data_checked", fixture=request.path_params["id"])
    return {**fixture, "simulated": True}


@api.route("/mock/payment/settle", "POST", "Retrieve an idempotent simulated settlement", roles=("merchant", "admin"),
           schema=shape({"redemption": IDENTIFIER}, ("redemption",)), mock=True)
def payment_mock(request, actor, data):
    throttle(request, "mock", 40, actor)
    with store(request).connect(write=True) as db:
        row = db.execute("""SELECT r.id,r.merchant,c.id AS case_id,v.value FROM redemptions r JOIN vouchers v ON v.id=r.voucher_id
                            JOIN cases c ON c.id=v.case_id WHERE r.id=? AND c.org=?""", (data["redemption"], actor["org"])).fetchone()
        if not row or (actor["role"] == "merchant" and row["merchant"] != actor["id"]):
            raise HTTPException(404, "Mock redemption not found.")
        settlement = settle(db, row["id"])
        audit(db, actor, "mock.payment_checked", row["case_id"], settlement=settlement)
    return {"settlement": settlement, "value": row["value"], "simulated": True}


async def homepage(request):
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


async def docs(request):
    return FileResponse(STATIC / "docs.html")


async def service_worker(request):
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


async def schema(request):
    return JSONResponse({"openapi": "3.1.0", "info": {"title": "MadadPass API", "version": "1.0.0",
                        "description": "Synthetic grocery assistance platform. Obtain a session at /api/login or a mock key from an administrator."},
                         "paths": api.paths, "components": {"securitySchemes": {
                             "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
                             "MockApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
                         }}})


async def example_document(request):
    return Response(document_sample(5, 30000), media_type="text/plain", headers={
        "Content-Disposition": 'attachment; filename="fictional-income-statement.txt"',
    })


def create_app(db_path=None, demo=True, signing_key=None):
    db_path = Path(db_path or os.getenv("MADADPASS_DB", ROOT / "data" / "madadpass.sqlite3"))

    def initialize():
        database = Store(db_path)
        database.initialize(demo)
        key = load_secret(db_path.with_suffix(".secret"), signing_key or os.getenv("MADADPASS_SECRET"))
        return database, key

    @asynccontextmanager
    async def lifespan(app):
        app.state.store, app.state.secret = await run_in_threadpool(initialize)
        yield

    routes = sorted(api.routes, key=lambda route: route.path.count("{")) + [Route("/", homepage), Route("/docs", docs),
              Route("/openapi.json", schema), Route("/sw.js", service_worker), Route("/sample/income.txt", example_document),
              Mount("/static", app=StaticFiles(directory=STATIC), name="static")]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.demo = demo

    async def headers(request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' blob: data:; "
                                    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if request.url.path.startswith(("/api/", "/mock/")):
            LOG.info("%s %s %s %.0fms", request.method, getattr(request.state, "operation", "unmatched"),
                     response.status_code, (time.perf_counter() - started) * 1000)
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=headers)
    return app


@api.route("/api/vouchers/history", "GET", "List a merchant's own redemption receipts", roles=("merchant",))
def history(request, actor, data):
    with store(request).connect() as db:
        rows = db.execute("""SELECT r.id,r.created_at,v.value,s.id AS settlement FROM redemptions r
                             JOIN vouchers v ON v.id=r.voucher_id JOIN settlements s ON s.redemption_id=r.id
                             WHERE r.merchant=? ORDER BY r.created_at DESC LIMIT 50""", (actor["id"],))
        return {"redemptions": [dict(row) for row in rows]}
