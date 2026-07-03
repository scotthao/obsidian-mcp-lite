#!/usr/bin/env python3
"""
Self-healing Obsidian MCP connector — proxies the Local REST API plugin.

Improvements over the stock community connector:
- Single local file, zero dependencies: nothing fetched at startup, so it
  can't fail because a package registry or network was unavailable.
- Health-checks the REST port before every operation.
- If Obsidian isn't running, launches it automatically and waits for the
  REST API to come up (launching an app needs no macOS permissions).
- Clear, specific error messages instead of silent failures.

Config (env vars, same names the stock connector uses):
    OBSIDIAN_API_KEY   (required) API key from the Local REST API plugin
    OBSIDIAN_HOST      default 127.0.0.1
    OBSIDIAN_PORT      default 27124
    OBSIDIAN_PROTOCOL  default https
    OBSIDIAN_LAUNCH_CMD default: platform-appropriate launch command

Requires only Python 3.8+ standard library.
"""

import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SERVER_NAME = "obsidian-mcp-lite"
SERVER_VERSION = "1.0.0"

API_KEY = os.environ.get("OBSIDIAN_API_KEY", "")
HOST = os.environ.get("OBSIDIAN_HOST", "127.0.0.1")
PORT = os.environ.get("OBSIDIAN_PORT", "27124")
PROTO = os.environ.get("OBSIDIAN_PROTOCOL", "https")
def _default_launch_cmd():
    """Platform-appropriate command to launch Obsidian."""
    if sys.platform == "darwin":
        return "open -a Obsidian"
    if sys.platform.startswith("win"):
        return "cmd /c start obsidian://open"
    return "xdg-open obsidian://open"  # Linux


LAUNCH_CMD = os.environ.get("OBSIDIAN_LAUNCH_CMD", _default_launch_cmd())
STARTUP_WAIT_S = int(os.environ.get("OBSIDIAN_STARTUP_WAIT_S", "30"))

BASE = f"{PROTO}://{HOST}:{PORT}"
SSL_CTX = ssl._create_unverified_context()  # plugin uses a self-signed cert


class ServerDown(Exception):
    pass


def _request(method, path, body=None, headers=None, query=None, timeout=20):
    url = BASE + urllib.parse.quote(path)
    if query:
        url += "?" + urllib.parse.urlencode(query)
    h = {"Authorization": f"Bearer {API_KEY}"}
    if headers:
        h.update(headers)
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # Server is UP but returned an error status — not a connectivity issue.
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, ConnectionError, OSError, TimeoutError) as e:
        raise ServerDown(str(e))


def _is_up():
    try:
        _request("GET", "/", timeout=3)
        return True
    except ServerDown:
        return False


def ensure_up():
    """Health-check; if down, launch Obsidian and wait for the API."""
    if _is_up():
        return
    try:
        subprocess.Popen(
            LAUNCH_CMD.split(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        raise RuntimeError(f"REST API down and couldn't launch Obsidian: {e}")
    deadline = time.time() + STARTUP_WAIT_S
    while time.time() < deadline:
        time.sleep(1)
        if _is_up():
            time.sleep(1)  # small grace period for the vault to finish loading
            return
    raise RuntimeError(
        f"Launched Obsidian but the REST API on port {PORT} did not respond "
        f"within {STARTUP_WAIT_S}s. Check that the 'Local REST API' community "
        f"plugin is enabled in the vault Obsidian opens by default."
    )


def call_api(method, path, body=None, headers=None, query=None):
    """Optimistic fast path: send the request directly (zero added latency).
    Only if the connection fails do we heal (launch Obsidian, wait) and retry."""
    try:
        return _request(method, path, body, headers, query)
    except ServerDown:
        ensure_up()
        return _request(method, path, body, headers, query)


def check_status(status, text, action):
    if status in (200, 201, 204):
        return None
    if status == 401:
        return (
            "Authentication failed (401). The API key in the connector config "
            "no longer matches the Local REST API plugin. Copy the current key "
            "from Obsidian Settings -> Local REST API and update the config."
        )
    if status == 404:
        return f"Not found (404) while trying to {action}."
    return f"HTTP {status} while trying to {action}: {text[:300]}"


# ---------------- tool implementations ----------------

def tool_list_notes(args):
    folder = (args.get("folder") or "").strip().strip("/")
    path = f"/vault/{folder}/" if folder else "/vault/"
    status, text = call_api("GET", path)
    err = check_status(status, text, f"list '{folder or '/'}'")
    if err:
        return err
    try:
        files = json.loads(text).get("files", [])
    except json.JSONDecodeError:
        return f"Unexpected response: {text[:300]}"
    return "\n".join(files) if files else "(empty)"


def tool_read_note(args):
    p = args["path"].strip().lstrip("/")
    if "." not in os.path.basename(p):
        p += ".md"
    status, text = call_api("GET", f"/vault/{p}", headers={"Accept": "text/markdown"})
    err = check_status(status, text, f"read '{p}'")
    return err if err else text


def tool_write_note(args):
    p = args["path"].strip().lstrip("/")
    if "." not in os.path.basename(p):
        p += ".md"
    status, text = call_api(
        "PUT", f"/vault/{p}", body=args["content"],
        headers={"Content-Type": "text/markdown"},
    )
    err = check_status(status, text, f"write '{p}'")
    return err if err else f"Saved: {p}"


def tool_append_note(args):
    p = args["path"].strip().lstrip("/")
    if "." not in os.path.basename(p):
        p += ".md"
    status, text = call_api(
        "POST", f"/vault/{p}", body=args["content"],
        headers={"Content-Type": "text/markdown"},
    )
    err = check_status(status, text, f"append to '{p}'")
    return err if err else f"Appended to: {p}"


def tool_search_notes(args):
    query = args["query"]
    ctx = int(args.get("context_length", 120))
    status, text = call_api(
        "POST", "/search/simple/",
        query={"query": query, "contextLength": ctx},
        body=b"",
    )
    err = check_status(status, text, f"search '{query}'")
    if err:
        return err
    try:
        results = json.loads(text)
    except json.JSONDecodeError:
        return f"Unexpected response: {text[:300]}"
    if not results:
        return f"No matches for: {query}"
    out = []
    for r in results[:50]:
        fname = r.get("filename", "?")
        snippets = []
        for m in (r.get("result") or [])[:3]:
            if isinstance(m, dict) and "context" in m:
                snippets.append("  " + " ".join(m["context"].split())[:250])
        out.append(fname + ("\n" + "\n".join(snippets) if snippets else ""))
    return "\n\n".join(out)


def tool_connection_status(args):
    up = _is_up()
    if not up:
        return f"REST API at {BASE}: DOWN (Obsidian closed or plugin disabled)."
    status, text = call_api("GET", "/")
    try:
        info = json.loads(text)
    except json.JSONDecodeError:
        info = {}
    auth = info.get("authenticated", False)
    ver = (info.get("versions") or {}).get("obsidian", "?")
    return (
        f"REST API at {BASE}: UP. Obsidian {ver}. "
        f"API key {'valid' if auth else 'NOT accepted - update OBSIDIAN_API_KEY'}."
    )


TOOLS = {
    "list_notes": {
        "fn": tool_list_notes,
        "description": "List files in the vault root or a folder (one level).",
        "schema": {
            "type": "object",
            "properties": {"folder": {"type": "string", "description": "Vault-relative folder ('' = root)"}},
        },
    },
    "read_note": {
        "fn": tool_read_note,
        "description": "Read a note's full contents by vault-relative path ('.md' optional).",
        "schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Vault-relative note path"}},
            "required": ["path"],
        },
    },
    "write_note": {
        "fn": tool_write_note,
        "description": "Create or overwrite a note with the given markdown content.",
        "schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Vault-relative note path"},
                "content": {"type": "string", "description": "Full note content"},
            },
            "required": ["path", "content"],
        },
    },
    "append_note": {
        "fn": tool_append_note,
        "description": "Append content to the end of a note (creates it if missing).",
        "schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Vault-relative note path"},
                "content": {"type": "string", "description": "Content to append"},
            },
            "required": ["path", "content"],
        },
    },
    "search_notes": {
        "fn": tool_search_notes,
        "description": "Full-text search across the vault. Returns matching files with context snippets.",
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search for"},
                "context_length": {"type": "integer", "description": "Snippet context chars (default 120)"},
            },
            "required": ["query"],
        },
    },
    "connection_status": {
        "fn": tool_connection_status,
        "description": "Check whether the Obsidian REST API is reachable and the API key is valid.",
        "schema": {"type": "object", "properties": {}},
    },
}


# ---------------- JSON-RPC / MCP plumbing ----------------

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def reply(req_id, result):
    send({"jsonrpc": "2.0", "id": req_id, "result": result})


def reply_error(req_id, code, message):
    send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def handle(msg):
    method = msg.get("method")
    req_id = msg.get("id")
    is_notification = "id" not in msg

    if method == "initialize":
        client_ver = (msg.get("params") or {}).get("protocolVersion", "2024-11-05")
        reply(req_id, {
            "protocolVersion": client_ver,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    elif method == "ping":
        reply(req_id, {})
    elif method == "tools/list":
        reply(req_id, {"tools": [
            {"name": name, "description": t["description"], "inputSchema": t["schema"]}
            for name, t in TOOLS.items()
        ]})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        tool = TOOLS.get(name)
        if not tool:
            reply_error(req_id, -32602, f"Unknown tool: {name}")
            return
        if not API_KEY:
            reply(req_id, {"content": [{"type": "text", "text":
                "OBSIDIAN_API_KEY is not set in the connector config. Copy the key "
                "from Obsidian Settings -> Local REST API into the config env."}],
                "isError": True})
            return
        try:
            text = tool["fn"](params.get("arguments") or {})
            reply(req_id, {"content": [{"type": "text", "text": text}]})
        except Exception as e:
            reply(req_id, {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
    elif is_notification:
        pass
    else:
        reply_error(req_id, -32601, f"Method not found: {method}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            handle(msg)
        except Exception as e:
            if isinstance(msg, dict) and "id" in msg:
                reply_error(msg["id"], -32603, str(e))


if __name__ == "__main__":
    main()
