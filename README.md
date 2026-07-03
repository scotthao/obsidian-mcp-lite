# obsidian-mcp-lite

A zero-dependency, self-healing MCP connector for [Obsidian](https://obsidian.md).
One Python file, standard library only. Works on macOS, Windows, and Linux.

Connects AI apps that speak [MCP](https://modelcontextprotocol.io) (Claude
Desktop, Claude Code, and others) to your Obsidian vault through the
[Local REST API](https://github.com/coddingtonbear/obsidian-local-rest-api)
community plugin.

## Why this exists

The stock community connector is fetched from a package registry every time
your AI app starts it — a network hiccup at launch kills vault access for the
whole session, silently. This connector fixes the reliability problems:

- **Nothing to download, ever.** One local file, Python standard library only.
  If Python runs, the connector runs.
- **Self-healing.** If Obsidian isn't running when a request arrives, the
  connector launches it, waits for the REST API, and completes the request.
- **Zero added latency.** Requests go straight through on the happy path;
  health checks only happen after a failure.
- **Diagnosable.** A `connection_status` tool and specific error messages
  ("API key rejected", "port down", "note not found") replace silent failures.

## How it works (a 60-second MCP primer)

```
Claude ⇄ JSON over stdin/stdout ⇄ this script ⇄ HTTP on localhost ⇄ Obsidian plugin ⇄ your notes
```

An MCP "server" is just a program your AI app launches and talks to in JSON,
one message per line, over standard input/output. The conversation has three
beats:

1. **Handshake** — the app sends `initialize`, the server introduces itself.
2. **Menu** — the app sends `tools/list`, the server replies with the tools
   it offers and what arguments they take.
3. **Orders** — during chats the app sends `tools/call` ("run `search_notes`
   with query=X"), the server does the work and returns text.

This server's "work" is translating each tool call into an HTTP request to
the Local REST API plugin running inside Obsidian, which reads and writes the
vault. The whole implementation is ~370 lines — read `obsidian_mcp_lite.py`
top to bottom to see every piece.

## Tools

| Tool | What it does |
|---|---|
| `list_notes` | List files in the vault root or a folder |
| `read_note` | Read a note's full contents |
| `write_note` | Create or overwrite a note |
| `append_note` | Append to a note (creates it if missing) |
| `search_notes` | Full-text search with context snippets |
| `connection_status` | One-line health check: port, Obsidian version, key validity |

## Setup

1. In Obsidian, install and enable the **Local REST API** community plugin,
   and copy its API key from the plugin settings.
2. Save `obsidian_mcp_lite.py` somewhere stable (e.g. `~/scripts/`).
3. Add to your MCP client config — for Claude Desktop that's
   `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "obsidian": {
      "command": "python3",
      "args": ["/path/to/obsidian_mcp_lite.py"],
      "env": { "OBSIDIAN_API_KEY": "your-key-from-the-plugin" }
    }
  }
}
```

4. Restart your MCP client. Ask it to run `connection_status` to verify.

On Windows, use `"command": "python"` and a Windows-style path.

## Configuration (env vars)

| Variable | Default | Purpose |
|---|---|---|
| `OBSIDIAN_API_KEY` | — (required) | API key from the Local REST API plugin |
| `OBSIDIAN_HOST` | `127.0.0.1` | REST API host |
| `OBSIDIAN_PORT` | `27124` | REST API port |
| `OBSIDIAN_PROTOCOL` | `https` | `https` (default) or `http` |
| `OBSIDIAN_LAUNCH_CMD` | platform default | Command used to launch Obsidian when down |
| `OBSIDIAN_STARTUP_WAIT_S` | `30` | How long to wait for Obsidian to come up |

## Security notes

- Talks only to `localhost` — nothing leaves your machine.
- The Local REST API plugin uses a self-signed certificate; this connector
  accepts it without verification, which is standard for localhost use.
- Your API key lives in your MCP client config file. Anyone with access to
  your user account can read it — same trust model as the stock connector.
- No delete tool is exposed, deliberately: worst case is a created or
  modified note.

## License

MIT

## Credits

Written by Claude (Anthropic) in collaboration with Scott Hao, who designed the requirements, tested it against a real vault, and maintains it.