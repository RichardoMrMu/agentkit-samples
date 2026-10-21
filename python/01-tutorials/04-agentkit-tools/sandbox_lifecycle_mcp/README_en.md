# Sandbox Session lifecycle through MCP

Five standalone Python scripts, each calling one business tool through the standard MCP Client:

| Script | Tool |
| --- | --- |
| `01_create_session.py` | `createSession` |
| `02_list_sessions.py` | `listSessions` |
| `03_get_session.py` | `getSession` |
| `04_invoke_tool.py` | `invokeTool` |
| `05_delete_session.py` | `deleteSession` |

## Setup and authentication profiles

Python 3.11+ is required. From the repository root:

```bash
cd python/01-tutorials/04-agentkit-tools/sandbox_lifecycle_mcp
uv venv --python 3.12
uv pip install -r requirements.txt
# Only when there is no existing local configuration:
cp config.yaml.example config.yaml
chmod 600 config.yaml
```

The template separates endpoints and credentials:

```yaml
mcp:
  mode: keyauth # keyauth / oauth
  keyauth:
    url: "https://your-keyauth-gateway.example.com/mcp"
    api_key: "<your-api-key>"
  oauth:
    volcengine_access_key: "<your-access-key>"
    volcengine_secret_key: "<your-secret-key>"
    url: "https://your-oauth-gateway.example.com/mcp"
    user_pool_uid: "<your-user-pool-uid>"
    client_uid: "" # Optional; specify when multiple M2M clients exist
    host: open.volcengineapi.com
    api_version: "2025-10-30"
```

Set the required `mcp.mode` to `keyauth` or `oauth`. `--auth` overrides the selected endpoint and credentials for one run.
Missing profile settings produce errors rather than borrowing another profile's configuration.
Use complete Streamable HTTP URLs including their Gateway paths.

Key Auth reads `mcp.keyauth.api_key` or `MCP_API_KEY` and sends `Authorization: Bearer <API Key>`.
Supply a raw key without the Bearer prefix. It does not require AK/SK.

OAuth queries Identity using `mcp.oauth.volcengine_access_key` and `mcp.oauth.volcengine_secret_key`.
`VOLCENGINE_ACCESS_KEY` / `VOLCENGINE_SECRET_KEY` override those values. Temporary credentials also require
`VOLCENGINE_SESSION_TOKEN` or `mcp.oauth.volcengine_session_token`. Region comes from `VOLCENGINE_REGION`, then `mcp.oauth.region`, defaulting
to `cn-beijing`. The OAuth flow is:
GetUserPool/GetUserPoolClient with signing service `id` → GET TokenUrl with `grant_type=client_credentials`
and HTTP Basic → Bearer access token. The Client must be MACHINE_TO_MACHINE and the Gateway must accept
its pool's JWT. ClientSecret and tokens remain in process memory.
`client_uid` is optional: when omitted, `ListUserPoolClients` selects the unique M2M Client.
Zero clients produces a setup error; multiple clients require an explicit UID. Discovery requires
`id:ListUserPoolClients` permission; an explicit UID skips discovery.

Optional `mcp.oauth.token_url/client_id/client_secret` settings skip Identity queries. An existing
`MCP_TOKEN` can be used with `--auth bearer`, selecting `mcp.oauth.url`.

## Run

The following commands assume `mcp.mode: keyauth`.

```bash
uv run --active python 01_create_session.py
uv run --active python 02_list_sessions.py
uv run --active python 03_get_session.py --wait ready
uv run --active python 04_invoke_tool.py --code 'print(sum([1, 2, 3]))'
uv run --active python 05_delete_session.py
uv run --active python 03_get_session.py --wait deleted
```

To use OAuth, add `--auth oauth --state-file .sandbox_mcp_oauth_state.json` to each command. Separate
state files retain independent Gateway resources. A read-only authentication check is:

```bash
uv run --active python 02_list_sessions.py --auth oauth --state-file .sandbox_mcp_oauth_state.json
```

Creation defaults to eight hours. Creation/deletion report acceptance; script 03 confirms readiness or
deletion and updates the tracked instance's local status. Authentication failures never count as deletion.
Script 04 requires a Jupyter-capable image and defaults to `python3`. Optional `sandbox.ttl_seconds`,
`code`, `kernel_name` and `invoke_timeout_seconds` remain supported when omitted from the minimal template.

## Configuration and state

Business calls display cyan REQUEST and green RESPONSE panels with indented, highlighted JSON.
Errors use red panels. Embedded JSON strings are expanded only for display; wire types stay unchanged.
Credentials and signed URLs are redacted. `--json` emits plain NDJSON request/response events, preserving string types.

All scripts support `--help`, `--json`, `--dry-run`, `--config`, `--state-file` and `--auth`.
Scripts 03/04/05 accept `--session-id`; script 01 accepts `--user-session-id`.
Dry-run does not access the network, acquire credentials or write state.

`--config` or `SANDBOX_MCP_CONFIG` selects YAML. Relative state paths resolve against the config directory;
`--state-file` takes precedence. `MCP_URL` explicitly overrides the selected profile's URL; leave it unset
when using two different URLs from YAML. Other settings support
`SANDBOX_MCP_<UPPERCASE_PATH_WITH_UNDERSCORES>` overrides, except the explicit credential/URL aliases above.

The new layout assumes bound Gateways and ignores unrelated `AGENTKIT_TOOL_ID` values. If a server requires
ToolId, set `mcp.keyauth.tool_id` or `mcp.oauth.tool_id`. Configure `tools` aliases for namespaced tool names.
The old `mcp.url`/`auth`/`identity`/`volcengine` layout remains compatible, including `m2m` as an OAuth alias
and its original `AGENTKIT_TOOL_ID` behavior.

State is atomically written with mode `0600` and excludes credentials/full responses. Different endpoints
or Tools require different state files. Active records cannot be overwritten by creation. Pending mutations
are not automatically retried after errors. Recover a lost create response by correlating UserSessionId
with script 02's results before cleanup. Default configuration/state files are ignored by Git.

## Verification

```bash
uv pip install pytest ruff
uv run --active python -m pytest -q tests
uv run --active ruff check .
uv run --active ruff format --check .
```

Tests cover both layouts, endpoint/credential selection, state isolation, protocol calls and redaction.
On 2026-10-21, both Key Auth and OAuth Gateways passed creation, listing, readiness, code execution,
deletion and deletion confirmation; all test sessions were cleaned up. OAuth discovery with a blank
`client_uid` also passed a live list call. See the [Chinese guide](README.md).
