"""Read-only tools over card-banking-platform's account-service, with a confirm step.

Endpoints (GET only, after a JWT login):
    GET /api/accounts                      -> [{id, maskedNumber, type, currency, balance, status, openedAt}]
    GET /api/accounts/{id}/transactions    -> {content: [{id, postedAt, direction, amount, signedAmount,
                                               description, merchant, category, balanceAfter}],
                                               page, size, totalElements, totalPages}
        filters: from, to (YYYY-MM-DD), direction (CREDIT/DEBIT), q, page, size (1-100);
        404 if the account is not the signed-in user's

Safety, because a model chooses the calls:
  * read-only: the only non-GET request is the login that obtains the JWT; there is no
    tool for anything that changes an account, card or transfer
  * confirm first: every tool call is described in plain words and must be approved by a
    person through the `confirm` callback BEFORE any network request. With no callback the
    answer is "declined", so a forgotten wiring fails closed
  * the JWT and the password stay in this module: never returned to the model, never logged
  * account ids are validated (no path tricks) and long digit runs (card / account numbers)
    are masked to the last four digits before the model sees the response
  * responses are capped so a large account cannot flood the model's context

Config (environment):
    PLATFORM_URL         default http://localhost:8080
    PLATFORM_EMAIL       login email         PLATFORM_PASSWORD   login password
    PLATFORM_LOGIN_PATH  default /api/auth/login
                         (POST {"email","password"} -> {"accessToken","tokenType","expiresIn","refreshToken"})
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

MAX_ITEMS = 50
MAX_BYTES = 1_000_000
TIMEOUT = 10
ACCOUNT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
LONG_DIGITS = re.compile(r"\b(\d{8,19})\b")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

Confirm = Callable[[str], bool]
# transport(method, url, headers, body_bytes_or_None) -> (status, body_text)
Transport = Callable[[str, str, dict, "bytes | None"], "tuple[int, str]"]


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, str]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read(MAX_BYTES).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(2000).decode("utf-8", "replace")


def mask_numbers(obj):
    """Keep only the last four digits of any 8-19 digit run, in every string in the response."""
    if isinstance(obj, str):
        return LONG_DIGITS.sub(lambda m: "*" * (len(m.group(1)) - 4) + m.group(1)[-4:], obj)
    if isinstance(obj, list):
        return [mask_numbers(v) for v in obj]
    if isinstance(obj, dict):
        return {k: mask_numbers(v) for k, v in obj.items()}
    if isinstance(obj, int) and not isinstance(obj, bool) and len(str(abs(obj))) >= 8:
        s = str(abs(obj))
        return "*" * (len(s) - 4) + s[-4:]
    return obj


class PlatformClient:
    def __init__(self, base_url=None, email=None, password=None, login_path=None, transport: Transport | None = None):
        self.base = (base_url or os.environ.get("PLATFORM_URL", "http://localhost:8080")).rstrip("/")
        self.email = email or os.environ.get("PLATFORM_EMAIL", "")
        self._password = password or os.environ.get("PLATFORM_PASSWORD", "")
        self.login_path = login_path or os.environ.get("PLATFORM_LOGIN_PATH", "/api/auth/login")
        self.transport = transport or urllib_transport
        self._token: str | None = None

    def _login(self) -> None:
        if not (self.email and self._password):
            raise PermissionError("PLATFORM_EMAIL and PLATFORM_PASSWORD are not set")
        body = json.dumps({"email": self.email, "password": self._password}).encode()
        status, text = self.transport("POST", self.base + self.login_path, {"Content-Type": "application/json"}, body)
        if status != 200:
            raise PermissionError(f"login failed (HTTP {status})")
        data = json.loads(text)
        self._token = data.get("accessToken")
        if not self._token:
            raise PermissionError("login response had no accessToken")

    def get(self, path: str):
        """GET a JSON resource, logging in first or again when the token is missing or expired."""
        for attempt in (1, 2):
            if not self._token:
                self._login()
            status, text = self.transport("GET", self.base + path, {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}, None)
            if status == 401 and attempt == 1:
                self._token = None
                continue
            if status != 200:
                raise RuntimeError(f"HTTP {status} from {path}")
            return json.loads(text)


def _items(data):
    """Cap list responses and say when truncated. Paged responses keep their rows under "content"
    and their paging fields, so the model can ask for the next page."""
    if isinstance(data, dict) and isinstance(data.get("content"), list):
        rows = data["content"]
        paging = {k: data[k] for k in ("page", "size", "totalElements", "totalPages") if k in data}
        return {"content": rows[:MAX_ITEMS], "truncated": len(rows) > MAX_ITEMS, **paging}
    if isinstance(data, list):
        return {"items": data[:MAX_ITEMS], "count": len(data), "truncated": len(data) > MAX_ITEMS}
    return data


class PlatformTools:
    """The two platform tools. `confirm` is asked before every request; no confirm means no call."""

    SPECS = [
        {
            "name": "platform_list_accounts",
            "description": (
                "List the signed-in customer's accounts from card-banking-platform (GET /api/accounts). Read-only. "
                "The user is asked to approve the call first; if they decline you get an error: do not retry."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "platform_get_transactions",
            "description": (
                "List the transactions of one account from card-banking-platform (GET /api/accounts/{id}/transactions). "
                "Returns {content: [...], page, size, totalElements, totalPages}. Read-only. Get the id from "
                "platform_list_accounts first. The user is asked to approve the call first."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "account_id": {"type": "string", "description": "Account id as returned by platform_list_accounts"},
                    "from": {"type": "string", "description": "Earliest posting date, YYYY-MM-DD"},
                    "to": {"type": "string", "description": "Latest posting date, YYYY-MM-DD"},
                    "direction": {"type": "string", "enum": ["CREDIT", "DEBIT"]},
                    "q": {"type": "string", "description": "Text search (max 100 characters)"},
                    "page": {"type": "integer", "description": "Page number, starting at 0"},
                    "size": {"type": "integer", "description": "Rows per page, 1-100"},
                },
                "required": ["account_id"],
            },
        },
    ]
    NAMES = {s["name"] for s in SPECS}

    def __init__(self, client: PlatformClient | None = None, confirm: Confirm | None = None):
        self.client = client or PlatformClient()
        self.confirm = confirm

    def plan(self, name: str, args: dict) -> tuple[str, str]:
        """(plain-words description, path) for a call, validating arguments. Raises ValueError."""
        if name == "platform_list_accounts":
            return "GET /api/accounts (list your accounts)", "/api/accounts"
        if name == "platform_get_transactions":
            aid = str(args.get("account_id", ""))
            if not ACCOUNT_ID.match(aid):
                raise ValueError("account_id must be 1-64 letters, digits, '-' or '_'")
            params = self._filters(args)
            path = f"/api/accounts/{aid}/transactions" + (f"?{urllib.parse.urlencode(params)}" if params else "")
            return f"GET {path} (list transactions of account {aid})", path
        raise ValueError(f"unknown platform tool {name}")

    @staticmethod
    def _filters(args: dict) -> dict:
        """Validated transaction filters; anything invalid is an error, not silently dropped."""
        out = {}
        for key in ("from", "to"):
            if args.get(key) is not None:
                if not DATE.match(str(args[key])):
                    raise ValueError(f"{key} must be YYYY-MM-DD")
                out[key] = str(args[key])
        if args.get("direction") is not None:
            if args["direction"] not in ("CREDIT", "DEBIT"):
                raise ValueError("direction must be CREDIT or DEBIT")
            out["direction"] = args["direction"]
        if args.get("q") is not None:
            if len(str(args["q"])) > 100:
                raise ValueError("q is limited to 100 characters")
            out["q"] = str(args["q"])
        for key, lo, hi in (("page", 0, 100000), ("size", 1, 100)):
            if args.get(key) is not None:
                try:
                    v = int(args[key])
                except (TypeError, ValueError):
                    raise ValueError(f"{key} must be an integer") from None
                if not lo <= v <= hi:
                    raise ValueError(f"{key} must be between {lo} and {hi}")
                out[key] = v
        return out

    def call(self, name: str, args: dict) -> dict:
        try:
            description, path = self.plan(name, args)
        except ValueError as e:
            return {"error": str(e)}
        if self.confirm is None or not self.confirm(f"Allow the assistant to call {self.client.base}: {description}?"):
            return {"error": "declined: the user did not approve this call. Do not retry; say that no call was made."}
        try:
            return {"data": mask_numbers(_items(self.client.get(path)))}
        except Exception as e:  # never include the exception text from login: it could echo credentials
            return {"error": f"platform call failed: {type(e).__name__}: {str(e)[:200]}"}


def cli_confirm(prompt: str) -> bool:
    """Ask on the terminal. Anything but an explicit yes is a no."""
    try:
        return input(f"\n{prompt} [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False
