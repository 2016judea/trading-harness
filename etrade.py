"""E*TRADE API client: OAuth 1.0a handshake + thin REST helpers.

E*TRADE uses OAuth 1.0a with an out-of-band (oob) verifier: you open an
authorize URL in a browser, log in, and paste back the short code it shows.
Access tokens are cached to .tokens (gitignored) and reused until they expire
(tokens go inactive after ~2 hours idle and expire at US market midnight ET).
"""
from __future__ import annotations

import json
import os
import webbrowser
from pathlib import Path

from dotenv import load_dotenv
from requests_oauthlib import OAuth1Session

load_dotenv()

_ENV = os.getenv("ETRADE_ENV", "sandbox").lower()
BASE = "https://api.etrade.com" if _ENV == "prod" else "https://apisb.etrade.com"

_REQUEST_TOKEN_URL = "https://api.etrade.com/oauth/request_token"
_ACCESS_TOKEN_URL = "https://api.etrade.com/oauth/access_token"
_AUTHORIZE_URL = "https://us.etrade.com/e/t/etws/authorize"

_KEY = os.getenv("ETRADE_CONSUMER_KEY")
_SECRET = os.getenv("ETRADE_CONSUMER_SECRET")
_TOKEN_CACHE = Path(__file__).parent / ".tokens"


def _interactive_authorize():
    """Run the full OAuth dance and return a (resource_owner_key, secret) pair."""
    if not _KEY or not _SECRET:
        raise SystemExit("Set ETRADE_CONSUMER_KEY and ETRADE_CONSUMER_SECRET in .env")

    # 1. Request token (callback must be the literal string "oob").
    oauth = OAuth1Session(_KEY, client_secret=_SECRET, callback_uri="oob")
    fetch = oauth.fetch_request_token(_REQUEST_TOKEN_URL)
    rt, rts = fetch["oauth_token"], fetch["oauth_token_secret"]

    # 2. Send the user to authorize; they paste back a verifier code.
    url = f"{_AUTHORIZE_URL}?key={_KEY}&token={rt}"
    print("\nAuthorize this app in your browser:\n  " + url + "\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    verifier = input("Paste the verification code from E*TRADE: ").strip()

    # 3. Exchange for an access token.
    oauth = OAuth1Session(
        _KEY,
        client_secret=_SECRET,
        resource_owner_key=rt,
        resource_owner_secret=rts,
        verifier=verifier,
    )
    tok = oauth.fetch_access_token(_ACCESS_TOKEN_URL)
    pair = (tok["oauth_token"], tok["oauth_token_secret"])
    _TOKEN_CACHE.write_text(json.dumps({"key": pair[0], "secret": pair[1]}))
    return pair


def session(force_reauth: bool = False) -> OAuth1Session:
    """Return an authenticated OAuth1Session, reusing cached tokens if present."""
    if not force_reauth and _TOKEN_CACHE.exists():
        cached = json.loads(_TOKEN_CACHE.read_text())
        key, secret = cached["key"], cached["secret"]
    else:
        key, secret = _interactive_authorize()
    return OAuth1Session(
        _KEY,
        client_secret=_SECRET,
        resource_owner_key=key,
        resource_owner_secret=secret,
    )


def get(path: str, params: dict | None = None) -> dict:
    """GET a v1 JSON endpoint, e.g. get('/v1/accounts/list')."""
    s = session()
    r = s.get(f"{BASE}{path}.json", params=params)
    if r.status_code == 401:  # tokens expired/inactive — re-auth once.
        s = session(force_reauth=True)
        r = s.get(f"{BASE}{path}.json", params=params)
    r.raise_for_status()
    # A 204 with an empty body is a legitimate "nothing matched" from some
    # endpoints (notably /orders when the account has no orders in the window).
    # raise_for_status() has already cleared real errors, so an empty body here
    # is data, not a fault — and r.json() would raise on it.
    if r.status_code == 204 or not r.content.strip():
        return {}
    return r.json()


def _send(method: str, path: str, payload: dict) -> dict:
    """POST/PUT a v1 JSON endpoint with a JSON body.

    Order endpoints are the only writes this repo makes, so the retry policy is
    deliberately different from get(): **a write is never retried automatically.**
    A 401 here means re-auth, and re-authing mid-write then replaying the body
    risks transmitting the same order twice. Raise instead and let the caller
    re-auth and re-decide.
    """
    s = session()
    url = f"{BASE}{path}.json"
    hdr = {"Content-Type": "application/json", "Accept": "application/json",
           "consumerKey": _KEY or ""}
    r = s.request(method, url, json=payload, headers=hdr)
    if r.status_code == 401:
        raise RuntimeError(
            "401 on a WRITE. Token is dead. Re-auth, then re-run deliberately — "
            "this is never retried automatically because replaying an order body "
            "can place it twice."
        )
    try:
        body = r.json() if r.content.strip() else {}
    except ValueError:
        body = {"_raw": r.text}
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {body}")
    return body


def post(path: str, payload: dict) -> dict:
    return _send("POST", path, payload)


def put(path: str, payload: dict) -> dict:
    return _send("PUT", path, payload)


def list_accounts() -> list[dict]:
    data = get("/v1/accounts/list")
    return data["AccountListResponse"]["Accounts"]["Account"]
