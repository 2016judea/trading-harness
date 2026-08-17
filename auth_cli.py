"""Two-step OAuth helper so the handshake can span separate invocations.

  python auth_cli.py start            -> prints the authorize URL
  python auth_cli.py finish <code>    -> exchanges the verifier for tokens
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from requests_oauthlib import OAuth1Session

import etrade

_TMP = Path(__file__).parent / ".auth_tmp.json"


def start():
    oauth = OAuth1Session(etrade._KEY, client_secret=etrade._SECRET, callback_uri="oob")
    tok = oauth.fetch_request_token(etrade._REQUEST_TOKEN_URL)
    _TMP.write_text(json.dumps({"rt": tok["oauth_token"], "rts": tok["oauth_token_secret"]}))
    print(f"{etrade._AUTHORIZE_URL}?key={etrade._KEY}&token={tok['oauth_token']}")


def finish(verifier: str):
    tmp = json.loads(_TMP.read_text())
    oauth = OAuth1Session(
        etrade._KEY,
        client_secret=etrade._SECRET,
        resource_owner_key=tmp["rt"],
        resource_owner_secret=tmp["rts"],
        verifier=verifier,
    )
    tok = oauth.fetch_access_token(etrade._ACCESS_TOKEN_URL)
    etrade._TOKEN_CACHE.write_text(
        json.dumps({"key": tok["oauth_token"], "secret": tok["oauth_token_secret"]})
    )
    _TMP.unlink(missing_ok=True)
    print("Access token saved to .tokens")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "start":
        start()
    elif len(sys.argv) >= 3 and sys.argv[1] == "finish":
        finish(sys.argv[2])
    else:
        sys.exit("usage: auth_cli.py start | finish <code>")
