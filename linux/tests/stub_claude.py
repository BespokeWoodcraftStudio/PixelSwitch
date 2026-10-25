"""A stub ``claude`` for the tests and for the scratch-HOME integration run (spec 5.7 rule 4: the real
``claude`` binary never runs with a scratch HOME, and ``claude.binaryPath`` points at this).

It behaves like the CLI where PixelSwitch looks, and only against ``$HOME`` (a scratch HOME):

``claude --version``
    prints ``2.1.310 (Claude Code)``.
``claude auth status``
    reads ``~/.claude/.credentials.json`` and ``~/.claude.json`` the way the real CLI does: logged
    in when ``claudeAiOauth.accessToken`` is non-empty, ``email`` from ``oauthAccount.emailAddress``,
    ``subscriptionType`` from the credential. So a switch that wrote both files shows in the status.
    ``STUB_STATUS_SHADOW=1`` answers ``authMethod`` ``apiKey`` with no email (a shadowing credential
    source); ``STUB_STATUS_HANG=1`` never answers; ``STUB_STATUS_EXIT1=1`` exits 1 when logged out.
``claude auth login [--email X]``
    prints the lines Claude Code 2.1.280+ prints (``Opening browser to sign in...``, the ``visit:`` line
    with the MANUAL link, the prompt with no newline), runs ``$BROWSER <automatic link>`` (a localhost
    callback), then waits for the "browser" to finish: the file ``$HOME/stub-login-approve`` appears,
    or a line ``code#state`` arrives on stdin. Then it writes a FABRICATED login for
    ``$HOME/stub-login.json`` (``email``, ``uuid``, ``org``, ``subscription``) into both files, keeping
    every other key, and exits 0. ``STUB_LOGIN_EXIT=<n>`` exits n instead; ``STUB_LOGIN_NO_LINKS=1``
    prints no link and never calls ``$BROWSER``.

Every run appends one line to ``$HOME/stub-calls.log``: the arguments, whether ``CLAUDE_CONFIG_DIR`` /
``CLAUDE_SECURESTORAGE_CONFIG_DIR`` were set, and the ``BROWSER`` it was given.
"""
from __future__ import annotations

import os
import stat

STUB_SOURCE = r'''#!/usr/bin/env python3
import json, os, secrets, signal, subprocess, sys, time

home = os.environ["HOME"]
args = sys.argv[1:]
creds_path = os.path.join(home, ".claude", ".credentials.json")
config_path = os.path.join(home, ".claude.json")


def read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, value, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = None
    try:
        existing = os.stat(path).st_mode & 0o7777
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle)
    os.chmod(path, existing if existing is not None else mode)


with open(os.path.join(home, "stub-calls.log"), "a", encoding="utf-8") as log:
    log.write(json.dumps({"args": args, "CLAUDE_CONFIG_DIR": "CLAUDE_CONFIG_DIR" in os.environ,
                          "CLAUDE_SECURESTORAGE_CONFIG_DIR": "CLAUDE_SECURESTORAGE_CONFIG_DIR" in os.environ,
                          "BROWSER": os.environ.get("BROWSER"), "cwd": os.getcwd()}) + "\n")

if args == ["--version"]:
    print("2.1.310 (Claude Code)")
    sys.exit(0)

if args[:2] == ["auth", "status"]:
    if os.environ.get("STUB_STATUS_HANG") == "1":
        time.sleep(3600)
    oauth = read_json(creds_path).get("claudeAiOauth")
    logged_in = isinstance(oauth, dict) and bool(oauth.get("accessToken"))
    if os.environ.get("STUB_STATUS_SHADOW") == "1":
        print(json.dumps({"loggedIn": True, "authMethod": "apiKey", "apiProvider": "firstParty"}))
        sys.exit(0)
    if not logged_in:
        print(json.dumps({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"}))
        sys.exit(1 if os.environ.get("STUB_STATUS_EXIT1") == "1" else 0)
    account = read_json(config_path).get("oauthAccount")
    account = account if isinstance(account, dict) else {}
    out = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}
    if account.get("emailAddress"):
        out["email"] = account["emailAddress"]
        out["orgId"] = account.get("organizationUuid", "org-stub")
        out["orgName"] = account.get("organizationName", account["emailAddress"])
    if oauth.get("subscriptionType"):
        out["subscriptionType"] = oauth["subscriptionType"]
    print(json.dumps(out))
    sys.exit(0)

if args[:2] == ["auth", "login"]:
    config = read_json(os.path.join(home, "stub-login.json"))
    email = config.get("email")
    if "--email" in args:
        email = email or args[args.index("--email") + 1]
    challenge = "TESTCHALLENGE123"
    state = "TESTSTATE456"
    base = ("https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e"
            "&response_type=code&redirect_uri={redirect}&scope=org%3Acreate_api_key+user%3Aprofile+user%3Ainference"
            "&code_challenge=" + challenge + "&code_challenge_method=S256&state=" + state)
    manual = base.format(redirect="https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback")
    automatic = base.format(redirect="http%3A%2F%2Flocalhost%3A54545%2Fcallback")
    if os.environ.get("STUB_LOGIN_NO_LINKS") != "1":
        sys.stdout.write("Opening browser to sign in…\n")
        sys.stdout.write("If the browser didn't open, visit: " + manual + "\n")
        sys.stdout.write("Paste code here if prompted > ")
        sys.stdout.flush()
        browser = os.environ.get("BROWSER")
        if browser:
            subprocess.run([browser, automatic], check=False)
    approve = os.path.join(home, "stub-login-approve")
    deadline = time.time() + float(os.environ.get("STUB_LOGIN_TIMEOUT", "60"))
    import select
    approved = False
    while time.time() < deadline and not approved:
        if os.path.exists(approve):
            approved = True
            break
        ready, _, _ = select.select([sys.stdin], [], [], 0.1)
        if ready:
            line = sys.stdin.readline()
            if not line:
                time.sleep(0.1)
                continue
            if line.strip() == "CODE#" + state:
                approved = True
            else:
                sys.stderr.write("Invalid code\n")
                sys.exit(1)
    if not approved:
        sys.exit(1)
    exit_code = os.environ.get("STUB_LOGIN_EXIT")
    if exit_code:
        sys.exit(int(exit_code))
    creds = read_json(creds_path)
    creds["claudeAiOauth"] = {
        "accessToken": "sk-ant-oat01-stub-" + secrets.token_hex(12),
        "refreshToken": "sk-ant-ort01-stub-" + secrets.token_hex(12),
        "expiresAt": int(time.time() * 1000) + 8 * 3600 * 1000,
        "refreshTokenExpiresAt": int(time.time() * 1000) + 90 * 86400 * 1000,
        "scopes": ["user:inference", "user:profile"],
        "subscriptionType": config.get("subscription", "max"),
        "rateLimitTier": "default_claude_max_20x",
    }
    write_json(creds_path, creds)
    cfg = read_json(config_path)
    cfg["oauthAccount"] = {
        "accountUuid": config.get("uuid", "stub-uuid"), "emailAddress": email or "stub@example.invalid",
        "organizationUuid": "org-stub", "organizationName": config.get("org", email or "stub"),
    }
    write_json(config_path, cfg)
    with open(os.path.join(home, "stub-login-result.json"), "w", encoding="utf-8") as handle:
        json.dump({"access": creds["claudeAiOauth"]["accessToken"], "refresh": creds["claudeAiOauth"]["refreshToken"]}, handle)
    print("Login successful.")
    sys.exit(0)

sys.stderr.write("stub claude: unsupported arguments\n")
sys.exit(2)
'''


def install(directory: str, name: str = "claude") -> str:
    """Writes the stub into ``directory`` and makes it executable; returns its path."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(STUB_SOURCE)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IRUSR)
    return path
