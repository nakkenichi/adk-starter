# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Serve the {class_method, input} agent runtime contract over HTTP."""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator
from typing import Protocol

from fastapi import FastAPI, HTTPException, Request, encoders, responses
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool
from vertexai.agent_engines.templates.adk import AdkApp

from app.agent import app as adk_app
from app.app_utils import services


class _RuntimeMethod(Protocol):
    """An AdkApp operation invoked with the keyword arguments of the request `input`."""

    def __call__(self, **kwargs: object) -> object: ...


def _no_op_instrumentor_builder(project_id: str) -> None:
    """No-op instrumentor builder preserving startup telemetry configuration."""
    del project_id
    return None


async def _parse_request_payload(request: Request) -> tuple[str, dict[str, object]]:
    """Validates and extracts `(class_method, kwargs)` from a runtime HTTP request."""
    try:
        body: object = await request.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object.")
    raw_method = body.get("class_method", "")
    class_method = raw_method if isinstance(raw_method, str) else str(raw_method)
    raw_input = body.get("input")
    if raw_input is not None and not isinstance(raw_input, dict):
        raise HTTPException(
            status_code=400,
            detail="Field 'input' must be a JSON object when provided.",
        )
    kwargs: dict[str, object] = (
        {str(k): v for k, v in raw_input.items()} if isinstance(raw_input, dict) else {}
    )
    return class_method, kwargs


def attach_reasoning_engine_routes(app: FastAPI) -> None:
    """Register /api/reasoning_engine and /api/stream_reasoning_engine routes on FastAPI.

    Args:
        app: Target FastAPI application instance.
    """
    runtime: AdkApp | None = None
    streaming_methods: set[str] = set()
    sync_methods: set[str] = set()

    def get_runtime() -> AdkApp:
        nonlocal runtime, streaming_methods, sync_methods
        if runtime is None:
            candidate = AdkApp(
                app=adk_app,
                session_service_builder=services.get_session_service,
                artifact_service_builder=services.get_artifact_service,
                memory_service_builder=services.get_memory_service,
                instrumentor_builder=_no_op_instrumentor_builder,
            )
            candidate.set_up()  # type: ignore[no-untyped-call]  # vertexai AdkApp.set_up lacks type annotations
            operations: dict[str, list[str]] = candidate.register_operations()
            streaming_methods = set(operations.get("stream", [])) | set(
                operations.get("async_stream", [])
            )
            sync_methods = set(operations.get("", [])) | set(operations.get("async", []))
            runtime = candidate
        return runtime

    def resolve_method(class_method: str, *, streaming: bool) -> _RuntimeMethod:
        rt = get_runtime()
        allowed = streaming_methods if streaming else sync_methods
        if class_method not in allowed:
            raise HTTPException(
                status_code=404,
                detail=f"Unsupported reasoning_engine method: {class_method!r}",
            )
        method_obj: object = getattr(rt, class_method)
        if not callable(method_obj):
            raise HTTPException(
                status_code=404,
                detail=f"Attribute is not callable: {class_method!r}",
            )
        return method_obj

    @app.post("/api/stream_reasoning_engine")
    async def stream_query(request: Request) -> responses.StreamingResponse:
        class_method, kwargs = await _parse_request_payload(request)
        method = resolve_method(class_method, streaming=True)
        try:
            stream: object = (
                await method(**kwargs) if inspect.iscoroutinefunction(method) else method(**kwargs)
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid method input: {exc}") from exc

        async def generator() -> AsyncIterator[str]:
            if hasattr(stream, "__aiter__"):
                async for event in stream:
                    yield json.dumps(encoders.jsonable_encoder(event)) + "\n"
            elif hasattr(stream, "__iter__"):
                async for event in iterate_in_threadpool(stream):
                    yield json.dumps(encoders.jsonable_encoder(event)) + "\n"

        return responses.StreamingResponse(content=generator(), media_type="application/json")

    @app.post("/api/reasoning_engine")
    async def query(request: Request) -> responses.JSONResponse:
        class_method, kwargs = await _parse_request_payload(request)
        method = resolve_method(class_method, streaming=False)
        try:
            if inspect.iscoroutinefunction(method):
                output: object = await method(**kwargs)
            else:
                output = await run_in_threadpool(method, **kwargs)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid method input: {exc}") from exc
        return responses.JSONResponse(content=encoders.jsonable_encoder({"output": output}))
