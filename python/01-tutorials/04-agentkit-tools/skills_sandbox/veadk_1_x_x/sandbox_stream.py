"""Stream Skill Sandbox A2A events into the ADK Runner without an extra LLM."""

import asyncio
from contextlib import aclosing
from dataclasses import dataclass, field
from hashlib import sha256
from typing import AsyncIterator
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from a2a.client.errors import A2AClientJSONRPCError
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.types import (
    Message,
    MessageSendConfiguration,
    MessageSendParams,
    Part,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskQueryParams,
    TextPart,
)
from google.adk.a2a.converters.part_converter import (
    convert_a2a_part_to_genai_part,
    convert_genai_part_to_a2a_part,
)
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.run_config import StreamingMode
from google.adk.events import Event, EventActions
from google.adk.tools import ToolContext
from google.genai import types
from pydantic import Field
from veadk.tools.builtin_tools._agentkit import (
    ensure_agentkit_session_endpoint,
    resolve_agentkit_tool_id,
)
from veadk.tools.builtin_tools.execute_skills import (
    _a2a_jsonrpc_url,
    _a2a_task_result_text,
    _inbound_auth_token,
    _skill_api_url,
    _tool_user_session_id,
)


class StreamFailure(RuntimeError):
    """A remote task failure; sanitize its message before displaying it."""


def _parts(parts: list[Part]) -> list[types.Part]:
    return [
        converted
        for part in parts
        if (converted := convert_a2a_part_to_genai_part(part)) is not None
    ]


def _coalesce(parts: list[types.Part]) -> list[types.Part]:
    result = []
    for part in parts:
        if (
            part.text is not None
            and result
            and result[-1].text is not None
            and bool(part.thought) == bool(result[-1].thought)
        ):
            result[-1] = result[-1].model_copy(
                update={"text": result[-1].text + part.text}
            )
        else:
            result.append(part)
    return result


def _signature(parts: list[types.Part]) -> list[dict]:
    return [
        part.model_dump(exclude={"part_metadata", "thought"}, exclude_none=True)
        | {"thought": bool(part.thought)}
        for part in _coalesce(parts)
    ]


@dataclass
class StreamState:
    """Per-invocation state; never share buffers or task IDs across users."""

    task_id: str | None = None
    context_id: str | None = None
    state: str | None = None
    received: bool = False
    done: bool = False
    last_full: list[types.Part] = field(default_factory=list)
    pending: list[types.Part] = field(default_factory=list)
    artifacts: dict[str, list[Part]] = field(default_factory=dict)
    published_artifacts: dict[str, list[dict]] = field(default_factory=dict)

    def content(
        self, parts: list[types.Part], partial: bool
    ) -> list[tuple[list[types.Part], bool]]:
        if not parts:
            return []
        if partial:
            self.pending.extend(parts)
            return [(parts, True)]
        self.pending.clear()
        if _signature(parts) == _signature(self.last_full):
            return []
        self.last_full = _coalesce(parts)
        return [(self.last_full, False)]

    def consume(self, update) -> list[tuple[list[types.Part], bool]]:
        self.received = True
        self.context_id = getattr(update, "context_id", None) or self.context_id
        if isinstance(update, Message):
            self.done = True
            self.state = "completed"
            return self.content(_parts(update.parts), False)

        self.task_id = update.id if isinstance(update, Task) else update.task_id
        if isinstance(update, TaskArtifactUpdateEvent):
            key = update.artifact.artifact_id
            previous = self.artifacts.get(key, []) if update.append else []
            self.artifacts[key] = previous + update.artifact.parts
            if update.last_chunk or (not update.append and update.last_chunk is None):
                converted = _parts(self.artifacts[key])
                signature = _signature(converted)
                if self.published_artifacts.get(key) == signature:
                    return []
                self.published_artifacts[key] = signature
                return self.content(converted, False)
            return self.content(_parts(update.artifact.parts), True)

        self.state = update.status.state.value
        self.done = self.state in {
            "completed",
            "failed",
            "canceled",
            "rejected",
            "input-required",
            "auth-required",
        }
        if self.done and self.state != "completed":
            raise StreamFailure(f"Skill Sandbox task ended with state: {self.state}")

        if isinstance(update, Task) and update.artifacts:
            result = []
            for artifact in update.artifacts:
                parts = _parts(artifact.parts)
                signature = _signature(parts)
                if self.published_artifacts.get(artifact.artifact_id) != signature:
                    self.published_artifacts[artifact.artifact_id] = signature
                    result.extend(self.content(parts, False))
            return result
        if self.state == "submitted":
            return []
        message = update.status.message
        if message and message.role.value == "agent":
            partial = bool((update.metadata or {}).get("adk_partial"))
            return self.content(_parts(message.parts), partial)
        if self.done and self.pending:
            # An artifact may end with a status event instead of lastChunk.
            return self.content(list(self.pending), False)
        if isinstance(update, Task) and self.done and not self.last_full:
            text = _a2a_task_result_text(update.model_dump(mode="json", by_alias=True))
            return self.content([types.Part(text=text)], False) if text else []
        return []


class SkillSandboxA2aAgent(BaseAgent):
    """A2A bridge using the pinned SDK transport and explicit delta conversion."""

    guardrails: str = ""
    timeout: int = Field(default=1800, ge=1, le=1800)

    async def _poll(self, transport, params, state):
        """Preserve A2A context and file parts even without streaming support."""
        params = params.model_copy(
            update={
                "configuration": MessageSendConfiguration(
                    blocking=False, history_length=20
                )
            }
        )
        result = await transport.send_message(params)
        interval = 2
        while isinstance(result, Task):
            state.received = True
            state.task_id = result.id
            state.context_id = result.context_id
            if result.status.state.value not in {"submitted", "working"}:
                break
            await asyncio.sleep(interval)
            result = await transport.get_task(
                TaskQueryParams(id=result.id, history_length=20)
            )
            interval = min(interval * 2, 16)
        return result

    async def _supports_streaming(self, client, endpoint):
        # Ignore the card's advertised URL: the session endpoint carries the
        # routing prefix and query authentication that must be preserved.
        parts = urlsplit(endpoint)
        path = parts.path.rstrip("/")
        if path.endswith("/a2a"):
            path = path[:-4]
        base = urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))
        for card_path in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
            # AgentKit may mark a new session Ready before its HTTP application
            # is listening. Retry only this read-only probe, never submissions.
            for attempt in range(30):
                response = await client.get(_skill_api_url(base, card_path))
                if response.status_code not in {502, 503, 504} or attempt == 29:
                    break
                await asyncio.sleep(1)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            return bool(response.json().get("capabilities", {}).get("streaming"))
        # Older endpoints may expose JSON-RPC without a discoverable card.
        return True

    async def _cancel(self, transport: JsonRpcTransport, state: StreamState) -> None:
        if state.task_id and not state.done:
            try:
                await asyncio.wait_for(
                    transport.cancel_task(TaskIdParams(id=state.task_id)), timeout=5
                )
            except Exception:
                # Some sandbox executors do not support tasks/cancel. Closing
                # the local stream must still succeed; no completion is implied.
                pass

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncIterator[Event]:
        state = StreamState()
        transport = None
        token = None
        tool_context = ToolContext(ctx)

        def event(parts=None, partial=False, **kwargs):
            return Event(
                author=self.name,
                invocation_id=ctx.invocation_id,
                branch=ctx.branch,
                partial=partial,
                content=types.Content(role="model", parts=parts) if parts else None,
                **kwargs,
            )

        try:
            async with asyncio.timeout(self.timeout):
                tool_id = resolve_agentkit_tool_id("AGENTKIT_TOOL_ID_SKILLS")
                context_key = f"skill_sandbox:{tool_id}:context_id"
                endpoint = await asyncio.to_thread(
                    ensure_agentkit_session_endpoint,
                    tool_id=tool_id,
                    tool_user_session_id=_tool_user_session_id(tool_context),
                    tool_state=tool_context.state,
                    ttl=max(self.timeout, 1800),
                    wait_until_ready=True,
                )
                user_parts = ctx.user_content.parts if ctx.user_content else []
                prompt = self.guardrails + "\n\n用户请求 / User request:\n"
                parts = [Part(root=TextPart(text=prompt))]
                for part in user_parts or []:
                    converted = convert_genai_part_to_a2a_part(part)
                    if converted:
                        parts.append(converted)
                endpoint_parts = urlsplit(endpoint)
                endpoint_id = sha256(
                    f"{endpoint_parts.netloc}{endpoint_parts.path}".encode()
                ).hexdigest()
                saved = tool_context.state.get(context_key, {})
                context_id = (
                    saved.get("context_id")
                    if (
                        isinstance(saved, dict)
                        and saved.get("endpoint_id") == endpoint_id
                    )
                    else None
                )
                token = await asyncio.to_thread(_inbound_auth_token, tool_context)
                headers = {"inbound_auth": token} if token else {}
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout, connect=10), headers=headers
                ) as client:
                    transport = JsonRpcTransport(client, url=_a2a_jsonrpc_url(endpoint))
                    params = MessageSendParams(
                        message=Message(
                            message_id=uuid4().hex,
                            role="user",
                            parts=parts,
                            context_id=context_id,
                        ),
                        metadata={"user_id": ctx.user_id, "session_id": ctx.session.id},
                    )
                    try:
                        supports_streaming = await self._supports_streaming(
                            client, endpoint
                        )
                        streaming = (
                            ctx.run_config.streaming_mode != StreamingMode.NONE
                            and supports_streaming
                        )
                        if streaming:
                            try:
                                async with aclosing(
                                    transport.send_message_streaming(params)
                                ) as stream:
                                    async for update in stream:
                                        for converted, partial in state.consume(update):
                                            yield event(converted, partial)
                                        if state.done:
                                            break
                                if not state.done:
                                    raise StreamFailure(
                                        "Skill Sandbox stream closed before task completion"
                                    )
                            except A2AClientJSONRPCError as exc:
                                # An ambiguous failure must never resubmit work.
                                if state.received or exc.error.code not in {
                                    -32004,
                                    -32601,
                                }:
                                    raise StreamFailure(
                                        f"Skill Sandbox A2A error: {exc.error.code}"
                                    ) from None
                                streaming = False
                        if not streaming:
                            result = await self._poll(transport, params, state)
                            for converted, partial in state.consume(result):
                                yield event(converted, partial)
                        if state.context_id:
                            yield event(
                                actions=EventActions(
                                    state_delta={
                                        context_key: {
                                            "context_id": state.context_id,
                                            "endpoint_id": endpoint_id,
                                        }
                                    }
                                )
                            )
                    finally:
                        await self._cancel(transport, state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            detail = str(exc) if isinstance(exc, StreamFailure) else type(exc).__name__
            yield event(
                [types.Part(text=f"Skill Sandbox 调用失败：{detail}")],
                error_code="SKILL_SANDBOX_ERROR",
                error_message=detail,
            )
