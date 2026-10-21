"""Offline regression tests; no credentials or sandbox requests are needed."""

import asyncio
import unittest
from builtins import anext
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from a2a.client.errors import A2AClientJSONRPCError
from a2a.types import (
    Artifact,
    JSONRPCError,
    JSONRPCErrorResponse,
    Message,
    Part,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.sessions import Session
from google.genai import types
from sandbox_stream import SkillSandboxA2aAgent, StreamFailure, StreamState


def status(text=None, partial=None, state="working"):
    return TaskStatusUpdateEvent(
        task_id="task",
        context_id="context",
        final=state != "working",
        status=TaskStatus(
            state=state,
            message=Message(
                message_id="message",
                role="agent",
                parts=[Part(root=TextPart(text=text))],
            )
            if text is not None
            else None,
        ),
        metadata={"adk_partial": partial} if partial is not None else None,
    )


def artifact(text, append=False, last_chunk=True):
    return TaskArtifactUpdateEvent(
        task_id="task",
        context_id="context",
        append=append,
        last_chunk=last_chunk,
        artifact=Artifact(artifact_id="answer", parts=[Part(root=TextPart(text=text))]),
    )


class StreamStateTest(unittest.TestCase):
    def test_deltas_full_message_and_final_artifact_are_not_duplicated(self):
        state = StreamState()
        self.assertEqual([], state.consume(status("Task submitted", state="submitted")))
        first = state.consume(status("你好", True))
        self.assertTrue(first[0][1])
        state.consume(status("世界", True))
        final = state.consume(status("你好世界", False))
        self.assertEqual("你好世界", final[0][0][0].text)
        self.assertFalse(final[0][1])
        self.assertEqual([], state.consume(artifact("你好世界")))
        self.assertEqual([], state.consume(status(state="completed")))
        self.assertTrue(state.done)

    def test_artifact_chunks_are_combined(self):
        state = StreamState()
        state.consume(artifact("hello ", last_chunk=False))
        final = state.consume(artifact("world", append=True))
        self.assertEqual("hello world", final[0][0][0].text)

    def test_final_task_snapshot_does_not_repeat_multiple_artifacts(self):
        state = StreamState()
        first = artifact("first")
        second = artifact("second")
        second.artifact.artifact_id = "second"
        state.consume(first)
        state.consume(second)
        task = Task(
            id="task",
            context_id="context",
            status=TaskStatus(state="completed"),
            artifacts=[first.artifact, second.artifact],
        )
        self.assertEqual([], state.consume(task))

    def test_terminal_status_finishes_pending_text(self):
        state = StreamState()
        state.consume(status("hello ", True))
        state.consume(status("world", True))
        final = state.consume(status(state="completed"))
        self.assertEqual("hello world", final[0][0][0].text)
        self.assertFalse(final[0][1])

    def test_thought_is_preserved_without_marking_answer_as_thought(self):
        update = status("answer", True)
        answer = StreamState().consume(update)[0][0][0]
        self.assertFalse(answer.thought)
        update.status.message.parts[0].root.metadata = {"adk_thought": True}
        self.assertTrue(StreamState().consume(update)[0][0][0].thought)

    def test_failure_is_not_success(self):
        for state in (
            "failed",
            "canceled",
            "rejected",
            "input-required",
            "auth-required",
        ):
            with self.subTest(state=state), self.assertRaises(StreamFailure):
                StreamState().consume(status(state=state))


class CardTest(unittest.IsolatedAsyncioTestCase):
    async def test_cold_start_probe_preserves_auth_query_and_retries_only_get(self):
        requests = []

        def handle(request):
            requests.append(request)
            self.assertEqual("GET", request.method)
            self.assertEqual("secret", request.url.params["token"])
            if len(requests) == 1:
                return httpx.Response(502)
            self.assertEqual("/session/.well-known/agent-card.json", request.url.path)
            return httpx.Response(200, json={"capabilities": {"streaming": True}})

        agent = SkillSandboxA2aAgent(name="test")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with patch("sandbox_stream.asyncio.sleep", new_callable=AsyncMock):
                self.assertTrue(
                    await agent._supports_streaming(
                        client, "https://example.test/session/a2a?token=secret"
                    )
                )
        self.assertEqual(2, len(requests))

    async def test_nonstreaming_card_is_respected(self):
        agent = SkillSandboxA2aAgent(name="test")
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"capabilities": {"streaming": False}}
                )
            )
        ) as client:
            self.assertFalse(
                await agent._supports_streaming(client, "https://example.test")
            )


class AgentTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.agent = SkillSandboxA2aAgent(
            name="skill_agent", guardrails="guardrails", timeout=2
        )
        self.ctx = SimpleNamespace(
            invocation_id="invocation",
            branch=None,
            agent=self.agent,
            user_id="user",
            session=Session(id="session", app_name="skill_agent", user_id="user"),
            run_config=RunConfig(streaming_mode=StreamingMode.SSE),
            user_content=types.Content(
                role="user", parts=[types.Part(text="workflow")]
            ),
        )
        self.tool_context = SimpleNamespace(state={}, _invocation_context=self.ctx)
        self.patches = [
            patch("sandbox_stream.ToolContext", return_value=self.tool_context),
            patch("sandbox_stream.resolve_agentkit_tool_id", return_value="tool"),
            patch(
                "sandbox_stream.ensure_agentkit_session_endpoint",
                return_value="https://example.test/?token=secret",
            ),
            patch("sandbox_stream._inbound_auth_token", return_value="test-token"),
            patch.object(
                SkillSandboxA2aAgent,
                "_supports_streaming",
                AsyncMock(return_value=True),
            ),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def collect(self):
        return [event async for event in self.agent._run_async_impl(self.ctx)]

    async def test_stream_reaches_runner_before_remote_finishes(self):
        released = asyncio.Event()
        closed = asyncio.Event()

        async def stream(params):
            self.assertIn("guardrails", params.message.parts[0].root.text)
            self.assertEqual("user", params.metadata["user_id"])
            self.assertEqual("workflow", params.message.parts[-1].root.text)
            try:
                yield status("hello", True)
                await released.wait()
                yield status("hello", False)
                yield artifact("hello")
                yield status(state="completed")
            finally:
                closed.set()

        with patch("sandbox_stream.JsonRpcTransport") as factory:
            factory.return_value.send_message_streaming = stream
            events = self.agent._run_async_impl(self.ctx)
            first = await anext(events)
            self.assertTrue(first.partial)
            self.assertFalse(closed.is_set())
            released.set()
            rest = [event async for event in events]
            self.assertEqual(1, sum(bool(event.content) for event in rest))
            self.assertTrue(closed.is_set())
            saved = rest[-1].actions.state_delta["skill_sandbox:tool:context_id"]
            self.assertEqual("context", saved["context_id"])
            factory.assert_called_once()
            self.assertIn("token=secret", factory.call_args.kwargs["url"])

    async def test_clean_disconnect_is_error_and_never_retries_submission(self):
        async def stream(params):
            yield status("unfinished", True)

        with patch("sandbox_stream.JsonRpcTransport") as factory:
            factory.return_value.send_message_streaming = stream
            factory.return_value.send_message = AsyncMock()
            factory.return_value.cancel_task = AsyncMock()
            events = await self.collect()
            self.assertEqual("SKILL_SANDBOX_ERROR", events[-1].error_code)
            self.assertIn("before task completion", events[-1].error_message)
            factory.return_value.send_message.assert_not_awaited()
            factory.return_value.cancel_task.assert_awaited_once()

    async def test_explicit_unsupported_stream_uses_existing_sync_path(self):
        async def stream(params):
            raise A2AClientJSONRPCError(
                JSONRPCErrorResponse(
                    id="request", error=JSONRPCError(code=-32004, message="unsupported")
                )
            )
            yield

        with patch("sandbox_stream.JsonRpcTransport") as factory:
            factory.return_value.send_message_streaming = stream
            factory.return_value.send_message = AsyncMock(
                return_value=Message(
                    message_id="reply",
                    role="agent",
                    parts=[Part(root=TextPart(text="sync result"))],
                )
            )
            events = await self.collect()
            self.assertEqual("sync result", events[-1].content.parts[0].text)
            factory.return_value.send_message.assert_awaited_once()

    async def test_nonstreaming_request_preserves_sync_execution(self):
        self.ctx.run_config.streaming_mode = StreamingMode.NONE
        with patch("sandbox_stream.JsonRpcTransport") as transport:
            transport.return_value.send_message = AsyncMock(
                return_value=Message(
                    message_id="reply",
                    role="agent",
                    parts=[Part(root=TextPart(text="sync result"))],
                )
            )
            events = await self.collect()
            self.assertFalse(events[0].partial)
            self.assertEqual("sync result", events[0].content.parts[0].text)
            transport.return_value.send_message.assert_awaited_once()
            transport.return_value.send_message_streaming.assert_not_called()

    async def test_nonstreaming_preserves_files_context_and_polling(self):
        self.ctx.run_config.streaming_mode = StreamingMode.NONE
        self.ctx.user_content.parts.append(
            types.Part(
                file_data=types.FileData(
                    file_uri="https://example.test/input.txt", mime_type="text/plain"
                )
            )
        )
        saved = {
            "context_id": "previous",
            "endpoint_id": sha256(b"example.test/").hexdigest(),
        }
        self.tool_context.state["skill_sandbox:tool:context_id"] = saved
        with (
            patch("sandbox_stream.JsonRpcTransport") as factory,
            patch("sandbox_stream.asyncio.sleep", new_callable=AsyncMock),
        ):
            transport = factory.return_value
            transport.send_message = AsyncMock(
                return_value=Task(
                    id="task", context_id="previous", status=TaskStatus(state="working")
                )
            )
            transport.get_task = AsyncMock(
                return_value=Task(
                    id="task",
                    context_id="previous",
                    status=TaskStatus(state="completed"),
                    artifacts=[artifact("result").artifact],
                )
            )
            await self.collect()
            params = transport.send_message.call_args.args[0]
            self.assertEqual("previous", params.message.context_id)
            self.assertEqual("file", params.message.parts[-1].root.kind)
            self.assertFalse(params.configuration.blocking)
            transport.get_task.assert_awaited_once()

    async def test_new_endpoint_does_not_reuse_old_context(self):
        self.ctx.run_config.streaming_mode = StreamingMode.NONE
        self.tool_context.state["skill_sandbox:tool:context_id"] = {
            "context_id": "stale",
            "endpoint_id": "previous-endpoint",
        }
        with patch("sandbox_stream.JsonRpcTransport") as factory:
            factory.return_value.send_message = AsyncMock(
                return_value=Message(
                    message_id="reply",
                    role="agent",
                    parts=[Part(root=TextPart(text="ok"))],
                )
            )
            await self.collect()
            self.assertIsNone(
                factory.return_value.send_message.call_args.args[0].message.context_id
            )

    async def test_nonstreaming_timeout_cancels_known_task(self):
        self.ctx.run_config.streaming_mode = StreamingMode.NONE
        self.agent.timeout = 1
        with patch("sandbox_stream.JsonRpcTransport") as factory:
            transport = factory.return_value
            transport.send_message = AsyncMock(
                return_value=Task(
                    id="task", context_id="context", status=TaskStatus(state="working")
                )
            )
            transport.cancel_task = AsyncMock()
            events = await self.collect()
            self.assertEqual("TimeoutError", events[-1].error_message)
            transport.cancel_task.assert_awaited_once()

    async def test_network_error_does_not_leak_signed_url(self):
        async def stream(params):
            raise RuntimeError("https://example.test/?token=secret")
            yield

        with patch("sandbox_stream.JsonRpcTransport") as factory:
            factory.return_value.send_message_streaming = stream
            events = await self.collect()
            self.assertNotIn("secret", events[-1].model_dump_json())
            self.assertEqual("RuntimeError", events[-1].error_message)

    async def test_cancellation_closes_stream_and_cancels_known_task(self):
        closed = asyncio.Event()

        async def stream(params):
            try:
                yield status("started", True)
                await asyncio.Event().wait()
            finally:
                closed.set()

        with patch("sandbox_stream.JsonRpcTransport") as factory:
            factory.return_value.send_message_streaming = stream
            factory.return_value.cancel_task = AsyncMock()
            events = self.agent._run_async_impl(self.ctx)
            await anext(events)
            waiter = asyncio.create_task(anext(events))
            await asyncio.sleep(0)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            self.assertTrue(closed.is_set())
            factory.return_value.cancel_task.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
