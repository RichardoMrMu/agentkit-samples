"""Harness Infra 资源到对话示例的离线协议测试。"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = {
    "create": "01_create_resources.py",
    "chat": "02_chat.py",
    "cleanup": "03_cleanup.py",
}


def top(result):
    return {
        "ResponseMetadata": {"RequestId": "request-fixture"},
        "Result": result,
    }


def top_error(code, message="not found"):
    return {
        "ResponseMetadata": {
            "RequestId": "request-fixture",
            "Error": {"Code": code, "Message": message},
        }
    }


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.state = Path(self.temporary.name) / "state.json"
        suffix = uuid.uuid4().hex
        self.control_access = f"test-access-{suffix}"
        self.control_secret = f"test-signing-{suffix}"
        self.environment_key = f"test-data-auth-{suffix}"
        self.requests = []
        self.responses = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def do_DELETE(self):
                self.respond()

            def respond(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                request = {
                    "method": self.command,
                    "path": self.path,
                    "headers": dict(self.headers),
                    "raw": raw,
                    "body": json.loads(raw) if raw else None,
                    "query": {
                        key: values[0]
                        for key, values in parse_qs(urlsplit(self.path).query).items()
                    },
                }
                owner.requests.append(request)
                if not owner.responses:
                    status, body = 500, {"message": "unexpected request"}
                else:
                    status, body = owner.responses.pop(0)
                if callable(body):
                    body = body(request)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("request-id", "header-request-id")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(
                (
                    "HARNESS_INFRA_",
                    "VOLCENGINE_",
                    "ARK_",
                    "http_proxy",
                    "https_proxy",
                    "all_proxy",
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "ALL_PROXY",
                )
            )
        }
        self.env.update(
            {
                "HARNESS_INFRA_ACCESS_KEY": self.control_access,
                "HARNESS_INFRA_SECRET_KEY": self.control_secret,
                "HARNESS_INFRA_REGION": "cn-beijing",
                "HARNESS_INFRA_SERVICE": "agentkit_test",
                "HARNESS_INFRA_CONTROL_HOST": f"127.0.0.1:{self.server.server_port}",
                "HARNESS_INFRA_CONTROL_SCHEME": "http",
                "HARNESS_INFRA_STATE": str(self.state),
                "HARNESS_INFRA_HTTP_RETRIES": "0",
                "HARNESS_INFRA_WAIT_TIMEOUT_SECONDS": "3",
                "HARNESS_INFRA_POLL_INTERVAL_SECONDS": "1",
                "HARNESS_INFRA_DATA_TIMEOUT_SECONDS": "3",
                "HARNESS_INFRA_MODEL_ID": "model-fixture",
                "HARNESS_INFRA_MODEL_BASE_URL": "https://ark.example.com/api/v3",
                "HARNESS_INFRA_RUNTIME_ROLE_NAME": "RuntimeRole",
                "HARNESS_INFRA_MODEL_API_KEY_NAME": "model-key-name",
            }
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def call(self, script, *args, code=0, env=None):
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / SCRIPTS[script]),
                "--json",
                *args,
            ],
            env=self.env if env is None else env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(
            completed.returncode, code, completed.stdout + completed.stderr
        )
        output = completed.stdout + completed.stderr
        for secret in (
            self.control_access,
            self.control_secret,
            self.environment_key,
        ):
            self.assertNotIn(secret, output)
        return completed

    def create_responses(self):
        return [
            (
                200,
                top(
                    {
                        "Items": [
                            {
                                "EndpointId": "ep_demo",
                                "BaseUrl": self.base + "/gateway",
                                "Status": "ready",
                            }
                        ],
                        "NextToken": None,
                    }
                ),
            ),
            (
                200,
                top(
                    {
                        "EnvironmentId": "env_demo",
                        "Status": "Active",
                    }
                ),
            ),
            (
                200,
                top(
                    {
                        "EnvironmentId": "env_demo",
                        "Status": "Active",
                    }
                ),
            ),
            (
                200,
                top(
                    {
                        "Items": [
                            {
                                "ResourceId": "er_demo",
                                "Status": "Creating",
                            }
                        ],
                        "NextToken": None,
                    }
                ),
            ),
            (
                200,
                top(
                    {
                        "ResourceId": "er_demo",
                        "Status": "Ready",
                        "Revision": 1,
                        "DesiredGeneration": 1,
                        "ObservedGeneration": 1,
                    }
                ),
            ),
            (
                200,
                top(
                    {
                        "HarnessId": "agent_demo",
                        "HarnessVersion": 1,
                        "Status": "Active",
                    }
                ),
            ),
            (
                200,
                top(
                    {
                        "RuntimeTemplateId": "rt_demo",
                        "Status": "Building",
                    }
                ),
            ),
            (
                200,
                top(
                    {
                        "RuntimeTemplateId": "rt_demo",
                        "Status": "Ready",
                    }
                ),
            ),
        ]

    def chat_responses(self):
        return [
            (
                200,
                top(
                    {
                        "ApiKeyId": "key_demo",
                        "Key": self.environment_key,
                    }
                ),
            ),
            (
                200,
                {
                    "id": "sesn_demo",
                    "status": "idle",
                    "ark_api_key_name": "model-key-name",
                },
            ),
            (
                200,
                {
                    "data": [
                        {
                            "id": "user-event",
                            "type": "user.message",
                            "sequence": 1,
                        }
                    ],
                    "has_more": False,
                    "next_page": None,
                },
            ),
            (
                200,
                {
                    "data": [
                        {
                            "id": "answer",
                            "type": "agent.message",
                            "sequence": 2,
                            "content": [{"type": "text", "text": "391"}],
                        },
                        {
                            "id": "idle",
                            "type": "session.status_idle",
                            "sequence": 3,
                            "stop_reason": {"type": "end_turn"},
                        },
                    ],
                    "has_more": False,
                    "next_page": None,
                },
            ),
            (200, {"id": "sesn_demo", "status": "terminated"}),
            (200, {"id": "sesn_demo", "type": "session_deleted"}),
            (
                404,
                {
                    "error": {
                        "type": "not_found_error",
                        "message": "not found",
                    }
                },
            ),
            (200, top({"ApiKeyId": "key_demo"})),
        ]

    def cleanup_responses(self):
        return [
            (200, top({"RuntimeTemplateId": "rt_demo"})),
            (404, top_error("ResourceNotFound")),
            (200, top({"ResourceId": "er_demo"})),
            (
                200,
                top(
                    {
                        "ResourceId": "er_demo",
                        "Status": "Deleted",
                        "Revision": 2,
                    }
                ),
            ),
            (200, top({"EnvironmentId": "env_demo"})),
            (404, top_error("InvalidEnvironment.NotFound")),
            (
                200,
                top(
                    {
                        "HarnessId": "agent_demo",
                        "Status": "Archived",
                    }
                ),
            ),
        ]

    def test_full_control_chat_cleanup_lifecycle(self):
        self.responses = self.create_responses()
        self.call("create", "--prefix", "offline")
        state = json.loads(self.state.read_text())
        self.assertEqual(state["status"], "resources_ready")
        self.assertEqual(state["resources"]["environment"]["id"], "env_demo")
        self.assertEqual(state["resources"]["environment_resource"]["status"], "Ready")

        top_requests = self.requests[:]
        actions = [request["query"]["Action"] for request in top_requests]
        self.assertEqual(
            actions,
            [
                "ListEndpoints",
                "CreateEnvironment",
                "GetEnvironment",
                "ListEnvironmentResources",
                "GetEnvironmentResource",
                "CreateHarness",
                "CreateRuntimeTemplate",
                "GetRuntimeTemplate",
            ],
        )
        create_environment = top_requests[1]["body"]
        self.assertEqual(
            create_environment["Config"]["Resources"],
            {"CpuMilli": 2000, "MemoryMb": 4096},
        )
        create_harness = top_requests[5]["body"]
        self.assertEqual(create_harness["AgentLoop"], "adk")
        self.assertEqual(
            create_harness["Model"],
            {
                "Id": "model-fixture",
                "BaseUrl": "https://ark.example.com/api/v3",
            },
        )
        create_runtime = top_requests[6]["body"]
        self.assertEqual(create_runtime["RoleName"], "RuntimeRole")
        self.assertEqual(create_runtime["Resources"]["MemoryMiB"], 2048)

        self.responses = self.chat_responses()
        output = self.call("chat")
        self.assertIn('"answer": "391"', output.stdout)
        data_requests = [
            request
            for request in self.requests
            if urlsplit(request["path"]).path.startswith("/gateway/")
        ]
        self.assertEqual(
            [request["method"] for request in data_requests],
            ["POST", "POST", "GET", "POST", "DELETE", "GET"],
        )
        session_create = data_requests[0]
        self.assertEqual(session_create["body"]["runtime_template_id"], "rt_demo")
        self.assertEqual(session_create["headers"]["x-ark-key-name"], "model-key-name")
        self.assertEqual(
            session_create["headers"]["x-api-key"],
            self.environment_key,
        )
        state_text = self.state.read_text()
        self.assertNotIn(self.environment_key, state_text)
        state = json.loads(state_text)
        self.assertEqual(state["resources"]["session"]["status"], "deleted")
        self.assertEqual(state["resources"]["api_key"]["status"], "deleted")

        self.responses = self.cleanup_responses()
        self.call("cleanup")
        state = json.loads(self.state.read_text())
        self.assertTrue(state["cleaned"])
        self.assertEqual(state["resources"]["runtime_template"]["status"], "deleted")
        self.assertEqual(
            state["resources"]["environment_resource"]["status"], "Deleted"
        )
        self.assertEqual(state["resources"]["environment"]["status"], "deleted")
        self.assertEqual(state["resources"]["harness"]["status"], "Archived")
        self.assertEqual(state["resources"]["endpoint"]["status"], "ready")
        self.assertEqual(self.responses, [])

    def test_signing_headers_cover_pascal_body(self):
        self.responses = self.create_responses()
        self.call("create")
        request = self.requests[0]
        self.assertTrue(
            request["headers"]["Authorization"].startswith(
                f"HMAC-SHA256 Credential={self.control_access}/"
            )
        )
        self.assertEqual(
            request["headers"]["X-Content-Sha256"],
            hashlib.sha256(request["raw"]).hexdigest(),
        )
        self.assertIn(
            "/cn-beijing/agentkit_test/request", request["headers"]["Authorization"]
        )

    def test_all_dry_runs_need_no_credentials_or_state(self):
        env = {
            key: value
            for key, value in self.env.items()
            if not key.startswith("HARNESS_INFRA_")
        }
        for script in ("create", "chat", "cleanup"):
            self.call(script, "--dry-run", env=env)
        self.assertEqual(self.requests, [])
        self.assertFalse(self.state.exists())

    def test_state_is_bound_to_control_scope(self):
        self.state.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "control_scope": "https://other.example.com",
                }
            )
        )
        output = self.call("chat", code=1)
        self.assertIn("another control endpoint", output.stderr)
        self.assertEqual(self.requests, [])

    def test_chat_rejects_idle_without_answer(self):
        self.responses = self.create_responses()
        self.call("create")
        responses = self.chat_responses()
        responses[3] = (
            200,
            {
                "data": [
                    {
                        "id": "idle",
                        "type": "session.status_idle",
                        "sequence": 2,
                        "stop_reason": {"type": "requires_action"},
                    }
                ],
                "has_more": False,
                "next_page": None,
            },
        )
        self.responses = responses
        output = self.call("chat", code=1)
        self.assertIn("requires an unsupported client action", output.stderr)
        state = json.loads(self.state.read_text())
        self.assertEqual(state["resources"]["session"]["status"], "deleted")
        self.assertEqual(state["resources"]["api_key"]["status"], "deleted")

    def test_api_key_quota_failure_keeps_control_resources_cleanable(self):
        self.responses = self.create_responses()
        self.call("create")
        self.responses = [
            (
                400,
                top_error(
                    "InvalidRequest",
                    "The number of the APIKey has exceeded the quota.",
                ),
            )
        ]
        output = self.call("chat", code=1)
        self.assertIn("exceeded the quota", output.stderr)
        state = json.loads(self.state.read_text())
        self.assertNotIn("api_key", state["resources"])

        self.responses = self.cleanup_responses()
        self.call("cleanup")
        state = json.loads(self.state.read_text())
        self.assertTrue(state["cleaned"])

    def test_malformed_top_success_is_rejected(self):
        self.responses = [(200, {"ResponseMetadata": {}})]
        output = self.call("create", code=1)
        self.assertIn("missing object Result", output.stderr)
        state = json.loads(self.state.read_text())
        self.assertEqual(state["status"], "initializing")


if __name__ == "__main__":
    unittest.main()
