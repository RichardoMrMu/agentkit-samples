# Harness Infra: From Resource Creation to a Real Conversation

This pure HTTP example covers the complete Harness Infra onboarding flow:

1. Call the control-plane TOP endpoint with account AK/SK.
2. Create or reuse an Endpoint.
3. Create a Cloud Environment and wait for its automatic Sandbox resource.
4. Create a Harness and RuntimeTemplate.
5. Create a one-time Environment API Key.
6. Create a Session through the Endpoint `BaseUrl`.
7. Send a user message and verify a real model response.
8. Delete the Session, Key, RuntimeTemplate, EnvironmentResource, and Environment,
   then archive the Harness.

The account-level Endpoint is retained.

## Files

| File | Purpose |
| --- | --- |
| `01_create_resources.py` | Create control-plane resources and wait for readiness |
| `02_chat.py` | Create a one-time Key, run a real conversation, and delete the Session and Key |
| `03_cleanup.py` | Clean up the remaining control-plane resources |
| `_clients.py` | TOP HMAC-SHA256 and data-plane REST clients |
| `_common.py` | Redacted output, state, and polling |
| `_workflow.py` | Deterministic lifecycle orchestration |
| `tests/test_flow.py` | Offline protocol tests |

## Control Plane and Data Plane

| Plane | Endpoint | Authentication | Field convention |
| --- | --- | --- | --- |
| Control | `https://open.volcengineapi.com/` | Account AK/SK signed with the published Service and Region | PascalCase; lists use `Items/NextToken` |
| Data | The Endpoint `BaseUrl` | The Environment Key returned once by `CreateAPIKey`, sent as `x-api-key` | Session/Event REST uses snake_case |
| Model credential | Resolved by the Runtime | The Ark API Key **Name** is sent as `x-ark-key-name` | No model key plaintext is sent by this client |

Account credentials, the Environment Key, and the Ark model key are distinct and
cannot replace one another.

## Prerequisites

- The account has access to the published Harness Infra Service.
- The account can create Endpoint, Environment, Harness, RuntimeTemplate, and API
  Key resources.
- An Ark API Key already exists and its **Name** is known. Do not use its plaintext,
  SID, or internal ID as the Name.
- A Runtime execution role already exists. It must trust the Runtime service and
  include the required permissions, including `ark:ListApiKeys` and
  `ark:GetRawApiKey`.
- The selected model ID is available to that Ark API Key.

## Install

```bash
uv venv
uv pip install -r requirements.txt
source .venv/bin/activate
```

The scripts do not load `.env` automatically:

```bash
set -a
source /path/to/.env
set +a
```

## Configuration

```bash
export HARNESS_INFRA_ACCESS_KEY='<account-ak>'
export HARNESS_INFRA_SECRET_KEY='<account-sk>'
export HARNESS_INFRA_REGION='cn-beijing'
export HARNESS_INFRA_SERVICE='agentkit'
export HARNESS_INFRA_CONTROL_HOST='open.volcengineapi.com'

export HARNESS_INFRA_MODEL_ID='<available-model-id>'
export HARNESS_INFRA_MODEL_BASE_URL='https://ark.cn-beijing.volces.com/api/v3'
export HARNESS_INFRA_MODEL_API_KEY_NAME='<ark-api-key-name>'
export HARNESS_INFRA_RUNTIME_ROLE_NAME='<runtime-role-name>'
```

`HARNESS_INFRA_SERVICE` must be the ServiceCode published for the target
environment. Do not infer it from response labels. Internal pre-release
ServiceCodes are intentionally omitted from this public example.

Control credentials also accept the existing `VOLCENGINE_*` and
`VOLCENGINE_AGENTKIT_*` variables. The model URL accepts `ARK_BASE_URL`; the
Runtime role accepts `AGENTKIT_RUNTIME_ROLE_NAME`.

Use an isolated state file:

```bash
export HARNESS_INFRA_STATE="/tmp/harness-infra-chat-$(date +%s).json"
```

The state file is created with mode `0600`. It stores only IDs, names, statuses,
and idempotency tokens. No API Key plaintext is persisted.

## 1. Create Resources

```bash
python 01_create_resources.py \
  --prefix harness-chat \
  --agent-loop adk
```

The script:

- Lists Endpoints and creates one only when none exists.
- Creates a Cloud Environment with 2000 milli CPU and 4096 MiB memory.
- Waits for the automatic EnvironmentResource to become `Ready` with matching
  desired and observed generations.
- Creates a Harness with the configured model and AgentLoop.
- Creates a RuntimeTemplate and waits for `Ready`.

Select an Endpoint explicitly when the account has more than one:

```bash
python 01_create_resources.py --endpoint-id ep_example
```

`--dry-run` performs no network or state writes.

## 2. Run a Real Conversation

The default prompt asks for `17 × 23` and requires the final answer to contain
`391`:

```bash
python 02_chat.py
```

Custom verification:

```bash
python 02_chat.py \
  --prompt 'Reply with only: HARNESS_CHAT_OK' \
  --expected-substring 'HARNESS_CHAT_OK'
```

The script:

1. Creates an Environment API Key bound to the Environment and Endpoint.
2. Keeps the returned plaintext only in the current process.
3. Creates a Session with `x-ark-key-name`.
4. Sends an idempotent `user.message`.
5. Reads persistent events incrementally by sequence.
6. Succeeds only after an `agent.message` followed by
   `session.status_idle` with `stop_reason.type=end_turn`.
7. Archives and deletes the Session, then revokes the one-time Key.

An HTTP 200, accepted Event, or arbitrary idle status is not a completed model
turn.

If the process is forcibly terminated after Key creation, the plaintext cannot
be recovered. Delete the old Key by the `ApiKeyId` in the state and start a new
run; an idempotent replay does not return the plaintext.

## 3. Clean Up

```bash
python 03_cleanup.py
```

Cleanup order:

1. Delete RuntimeTemplate and wait for Get to return 404.
2. Delete the automatic EnvironmentResource and wait for `Deleted`.
3. Delete Environment and wait for Get to return 404.
4. Archive Harness.
5. Retain Endpoint.

Cleanup refuses to continue while the Session is still active.

## Common Stops

| Symptom | Action |
| --- | --- |
| `CreateEndpoint` returns 409 | List and reuse the existing Endpoint; this example handles the single-Endpoint case automatically |
| `CreateAPIKey` reports `The number of the APIKey has exceeded the quota` | Raise the APIG `api_key-per-consumer` quota, or have resource owners remove confirmed unused Keys; never delete unknown Keys from other Environments |
| RuntimeTemplate remains `Building` | Continue Get with the original ID; do not create another template with a new token |
| `x-ark-key-name` resolves zero or multiple Ark Keys | Use the unique complete Name in the same account, region, and project, not plaintext, SID, or a prefix |
| Session returns `requires_action` | This basic example does not process client tool confirmation; use a tool-free Harness or implement the required protocol |
| Environment deletion conflicts | Delete Session and EnvironmentResource before Environment |

## Output and Timeouts

- Default output numbers creation, chat, and cleanup steps (`1, 2, 3…`). Each step keeps the resource name (when available), ID, core parameters, and status under one heading. Questions and answers use Chinese labels (`问 / 答`). Final summaries list names, IDs, and statuses without repeating parameters.
- Reused, deleted, and skipped resources also include their identifiers; Sessions include their title. Older state files reuse saved names without inventing missing metadata. API Keys show only names, IDs, and bindings. `--dry-run` lists numbered actions and explicitly marks names and IDs as not yet obtained.
- Creation requests (Endpoint, Environment, Harness, RuntimeTemplate, API Key, and Session), model conversations, and deletion waits use a single-line spinner in a terminal, then print the result once the target status (such as Ready) is reached. Redirected output and `--json` only log status changes. JSON events remain redacted and include a `step` field for numbered steps.
- Script 03 numbers its six cleanup steps: Session / API Key checks, RuntimeTemplate deletion, EnvironmentResource deletion, Environment deletion, Harness archival, and completion. Missing or already cleaned resources are explicitly skipped; the Endpoint is retained.
- `HARNESS_INFRA_HTTP_TIMEOUT_SECONDS`: control request timeout, default 60.
- `HARNESS_INFRA_DATA_TIMEOUT_SECONDS`: data request timeout, default 60.
- `HARNESS_INFRA_WAIT_TIMEOUT_SECONDS`: asynchronous wait timeout, default 900.
- `HARNESS_INFRA_POLL_INTERVAL_SECONDS`: polling interval, default 5.
- `HARNESS_INFRA_HTTP_RETRIES`: automatic 429/503 retries, default 2.

Automatic and manual retries must preserve the exact body and idempotency token.

## Offline Verification

```bash
uv run --with 'requests>=2.31,<3' \
  python -m unittest discover -s tests -v

uv run --with 'ruff==0.11.12' ruff check .
uv run --with 'ruff==0.11.12' ruff format --check .
```

Tests cover TOP signing and PascalCase bodies, resource convergence, Endpoint path
prefixes, both authentication headers, persistent Event completion, cleanup
ordering, and the no-secret state boundary.

## Contract Sources

- Harness Infra API guide: from resource creation to conversation
- akx `docs/harness-infra/api/`
- akx `docs/harness-infra/control-data-plane.md`
- API Version `2025-10-30`

Documentation, local tests, and a live deployment are separate evidence. Published
Actions, default images, Runtime role permissions, and model access must be
verified against the target environment.
