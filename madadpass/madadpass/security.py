import base64
import binascii
import hmac
import json
import re
import secrets
import time
from pathlib import Path

from starlette.exceptions import HTTPException

from .store import as_json


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64(data):
    return base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)


def signature(key, message):
    return hmac.digest(key, message.encode(), "sha256")


def load_secret(path, configured=None):
    if configured:
        if len(configured) < 32:
            raise ValueError("MADADPASS_SECRET must be at least 32 characters.")
        return configured.encode()
    path = Path(path)
    if not path.exists():
        try:
            with path.open("xb") as stream:
                stream.write(secrets.token_bytes(32))
        except FileExistsError:
            pass
    for _ in range(20):
        key = path.read_bytes()
        if len(key) == 32:
            return key
        time.sleep(.05)
    raise ValueError("The local secret file is invalid.")


def issue_jwt(key, subject, session_id, expires):
    header = b64(b'{"alg":"HS256","typ":"JWT"}')
    claims = b64(as_json({"sub": subject, "jti": session_id, "exp": expires, "iss": "madadpass", "aud": "madadpass-api"}).encode())
    content = f"{header}.{claims}"
    return f"{content}.{b64(signature(key, content))}"


def claims_for(key, token):
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token) or len(token) > 2048:
            raise ValueError
        header, claims, signed = token.split(".")
        if not hmac.compare_digest(signature(key, f"{header}.{claims}"), unb64(signed)):
            raise ValueError
        decoded = json.loads(unb64(claims))
        if (json.loads(unb64(header)) != {"alg": "HS256", "typ": "JWT"} or decoded["iss"] != "madadpass"
                or decoded["aud"] != "madadpass-api" or type(decoded["exp"]) is not int or decoded["exp"] <= time.time()
                or not isinstance(decoded["sub"], str) or not isinstance(decoded["jti"], str)):
            raise ValueError
        return decoded
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise HTTPException(401, "Sign in to continue.") from None


def voucher_token(key, voucher_id):
    return "mp_" + b64(signature(key, f"voucher:v1:{voucher_id}"))
