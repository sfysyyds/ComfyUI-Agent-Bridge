from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

from aiohttp import web
import folder_paths
from server import PromptServer

from .bridge_state import (
    BridgeState,
    BridgeStateError,
    PLUGIN_VERSION,
    PROTOCOL_VERSION,
    RevisionConflict,
    SessionAmbiguous,
    SessionNotFound,
    SessionOffline,
    validate_undo_payload,
)


API_PREFIX = "/comfy-agent-bridge/v1"
COMMAND_EVENT = "comfy.agent_bridge.command"
MAX_REQUEST_BYTES = 26 * 1024 * 1024
MAX_PENDING_COMMANDS = 64
MAX_RECENT_ERRORS = 100
MAX_ERROR_CODE_CHARS = 128
MAX_ERROR_MESSAGE_CHARS = 1024
MAX_ERROR_DETAILS_CHARS = 4096
ALLOWED_ACTIONS = {
    "apply_operations",
    "export_api",
    "focus_nodes",
    "highlight_nodes",
    "queue_prompt",
    "undo_node",
}
MUTATING_ACTIONS = {"apply_operations", "undo_node"}


@dataclass
class PendingCommand:
    command_id: str
    session_id: str
    action: str
    future: asyncio.Future[dict[str, Any]]
    created_at: float


STATE = BridgeState()
PENDING: dict[str, PendingCommand] = {}
SESSION_LOCKS: dict[str, asyncio.Lock] = {}
RECENT_ERRORS: deque[dict[str, Any]] = deque(maxlen=MAX_RECENT_ERRORS)


def _create_audit_logger() -> logging.Logger:
    logger = logging.getLogger("comfyui_agent_bridge.audit")
    if logger.handlers:
        return logger
    log_dir = Path(folder_paths.get_user_directory()) / "comfyui-agent-bridge"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "audit.jsonl",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


AUDIT_LOGGER = _create_audit_logger()


def _audit(event: str, **fields: Any) -> None:
    safe = {
        "timestamp": time.time(),
        "event": event,
        **fields,
    }
    AUDIT_LOGGER.info(json.dumps(safe, ensure_ascii=False, separators=(",", ":")))


def _json_error(message: str, status: int, code: str, **extra: Any) -> web.Response:
    return web.json_response(
        {"ok": False, "error": {"code": code, "message": message, **extra}},
        status=status,
    )


def _bounded_error_text(value: Any, limit: int) -> tuple[str, bool]:
    text = value if isinstance(value, str) else str(value)
    return text[:limit], len(text) > limit


def _record_command_error(pending: PendingCommand, error: dict[str, Any]) -> None:
    code, code_truncated = _bounded_error_text(
        error.get("code") or "browser_error", MAX_ERROR_CODE_CHARS
    )
    message, message_truncated = _bounded_error_text(
        error.get("message") or "unknown error", MAX_ERROR_MESSAGE_CHARS
    )
    details = error.get("details")
    details_truncated = False
    if details is not None:
        serialized = json.dumps(details, ensure_ascii=False, separators=(",", ":"))
        if len(serialized) > MAX_ERROR_DETAILS_CHARS:
            details = {"preview": serialized[:MAX_ERROR_DETAILS_CHARS]}
            details_truncated = True
    RECENT_ERRORS.append(
        {
            "command_id": pending.command_id,
            "session_id": pending.session_id,
            "action": pending.action,
            "timestamp": time.time(),
            "code": code,
            "message": message,
            "details": details,
            "truncated": code_truncated or message_truncated or details_truncated,
        }
    )


async def _read_json(request: web.Request) -> dict[str, Any]:
    if request.content_length is not None and request.content_length > MAX_REQUEST_BYTES:
        raise BridgeStateError("request exceeds bridge size limit")
    raw = await request.read()
    if len(raw) > MAX_REQUEST_BYTES:
        raise BridgeStateError("request exceeds bridge size limit")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BridgeStateError(f"request body is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise BridgeStateError("request body must be a JSON object")
    return data


@PromptServer.instance.routes.get(f"{API_PREFIX}/status")
async def bridge_status(_request: web.Request) -> web.Response:
    sessions = STATE.list_sessions()
    try:
        active = STATE.resolve(require_online=True).session_id
    except BridgeStateError:
        active = None
    return web.json_response(
        {
            "ok": True,
            "plugin_version": PLUGIN_VERSION,
            "protocol": PROTOCOL_VERSION,
            "session_count": len(sessions),
            "online_count": sum(1 for session in sessions if session["online"]),
            "active_session_id": active,
            "pending_command_count": len(PENDING),
            "recent_error_count": len(RECENT_ERRORS),
            "allowed_actions": sorted(ALLOWED_ACTIONS),
        }
    )


@PromptServer.instance.routes.get(f"{API_PREFIX}/errors")
async def recent_errors(_request: web.Request) -> web.Response:
    return web.json_response(
        {
            "ok": True,
            "count": len(RECENT_ERRORS),
            "limit": MAX_RECENT_ERRORS,
            "errors": list(reversed(RECENT_ERRORS)),
        }
    )


@PromptServer.instance.routes.get(f"{API_PREFIX}/sessions")
async def list_sessions(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "sessions": STATE.list_sessions()})


@PromptServer.instance.routes.get(f"{API_PREFIX}/sessions/{{session_id}}")
async def get_session(request: web.Request) -> web.Response:
    session_id = request.match_info["session_id"]
    include_raw = request.query.get("raw", "1").lower() not in {"0", "false", "no"}
    try:
        session = STATE.get(session_id)
    except SessionNotFound as error:
        return _json_error(str(error), 404, "session_not_found")
    return web.json_response({"ok": True, "session": session.as_dict(include_raw=include_raw)})


@PromptServer.instance.routes.post(f"{API_PREFIX}/snapshot")
async def update_snapshot(request: web.Request) -> web.Response:
    try:
        payload = await _read_json(request)
        session = STATE.update_snapshot(payload)
    except BridgeStateError as error:
        return _json_error(str(error), 400, "invalid_snapshot")
    return web.json_response(
        {
            "ok": True,
            "session_id": session.session_id,
            "revision": session.revision,
            "workflow_sha256": session.workflow_sha256,
            "online": session.online(),
        }
    )


@PromptServer.instance.routes.post(f"{API_PREFIX}/heartbeat")
async def heartbeat(request: web.Request) -> web.Response:
    try:
        payload = await _read_json(request)
        session = STATE.heartbeat(payload)
    except SessionNotFound as error:
        return _json_error(str(error), 404, "session_not_found")
    except BridgeStateError as error:
        return _json_error(str(error), 400, "invalid_heartbeat")
    return web.json_response(
        {"ok": True, "session_id": session.session_id, "revision": session.revision}
    )


@PromptServer.instance.routes.post(f"{API_PREFIX}/command")
async def send_command(request: web.Request) -> web.Response:
    try:
        payload = await _read_json(request)
        action = payload.get("action")
        if action not in ALLOWED_ACTIONS:
            raise BridgeStateError(
                f'action must be one of: {", ".join(sorted(ALLOWED_ACTIONS))}'
            )
        session_id = payload.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            raise BridgeStateError("session_id must be a string or null")
        session = STATE.resolve(session_id, require_online=True)
        if action in MUTATING_ACTIONS:
            STATE.check_revision(session, payload.get("base_revision"))
        command_payload = payload.get("payload", {})
        if not isinstance(command_payload, dict):
            raise BridgeStateError("payload must be a JSON object")
        if action == "undo_node":
            validate_undo_payload(command_payload)
        if len(PENDING) >= MAX_PENDING_COMMANDS:
            return _json_error(
                "too many bridge commands are already pending",
                503,
                "bridge_busy",
                pending_count=len(PENDING),
            )
    except RevisionConflict as error:
        return _json_error(
            str(error),
            409,
            "revision_conflict",
            expected=error.expected,
            current_revision=error.actual,
        )
    except SessionNotFound as error:
        return _json_error(str(error), 404, "session_not_found")
    except SessionOffline as error:
        return _json_error(str(error), 409, "session_offline")
    except SessionAmbiguous as error:
        return _json_error(str(error), 409, "session_ambiguous")
    except BridgeStateError as error:
        return _json_error(str(error), 400, "invalid_command")

    timeout_ms = payload.get("timeout_ms", 15000)
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int):
        return _json_error("timeout_ms must be an integer", 400, "invalid_timeout")
    timeout_ms = max(1000, min(timeout_ms, 30000))
    session_lock = SESSION_LOCKS.setdefault(session.session_id, asyncio.Lock())
    async with session_lock:
        # A queued command may have waited while an earlier command changed the canvas.
        # Resolve the session and revision again immediately before dispatch.
        try:
            session = STATE.resolve(session.session_id, require_online=True)
            if action in MUTATING_ACTIONS:
                STATE.check_revision(session, payload.get("base_revision"))
        except RevisionConflict as error:
            return _json_error(
                str(error),
                409,
                "revision_conflict",
                expected=error.expected,
                current_revision=error.actual,
            )
        except SessionOffline as error:
            return _json_error(str(error), 409, "session_offline")
        except SessionNotFound as error:
            return _json_error(str(error), 404, "session_not_found")

        command_id = str(uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        PENDING[command_id] = PendingCommand(
            command_id=command_id,
            session_id=session.session_id,
            action=action,
            future=future,
            created_at=time.time(),
        )

        frame = {
            "protocol": PROTOCOL_VERSION,
            "command_id": command_id,
            "session_id": session.session_id,
            "workflow_id": session.workflow_id,
            "action": action,
            "base_revision": payload.get("base_revision"),
            "payload": command_payload,
        }
        _audit(
            "command_sent",
            command_id=command_id,
            session_id=session.session_id,
            action=action,
            base_revision=payload.get("base_revision"),
        )
        try:
            PromptServer.instance.send_sync(COMMAND_EVENT, frame, sid=session.client_id)
        except Exception as error:
            PENDING.pop(command_id, None)
            _audit(
                "command_dispatch_failed",
                command_id=command_id,
                session_id=session.session_id,
                action=action,
                error_type=type(error).__name__,
            )
            return _json_error(
                f'failed to dispatch "{action}" to the browser: {error}',
                503,
                "command_dispatch_failed",
                command_id=command_id,
            )

        try:
            result = await asyncio.wait_for(future, timeout=timeout_ms / 1000)
        except asyncio.TimeoutError:
            _audit(
                "command_timeout",
                command_id=command_id,
                session_id=session.session_id,
                action=action,
            )
            return _json_error(
                f'browser did not acknowledge "{action}" within {timeout_ms} ms',
                504,
                "command_timeout",
                command_id=command_id,
            )
        finally:
            PENDING.pop(command_id, None)

    if not result.get("ok", False):
        _audit(
            "command_failed",
            command_id=command_id,
            session_id=session.session_id,
            action=action,
            error_code=result.get("error", {}).get("code")
            if isinstance(result.get("error"), dict)
            else "browser_error",
        )
        return web.json_response(result, status=422)

    _audit(
        "command_completed",
        command_id=command_id,
        session_id=session.session_id,
        action=action,
        revision=result.get("revision"),
    )
    return web.json_response(result)


@PromptServer.instance.routes.post(f"{API_PREFIX}/command-result")
async def command_result(request: web.Request) -> web.Response:
    try:
        payload = await _read_json(request)
    except BridgeStateError as error:
        return _json_error(str(error), 400, "invalid_result")

    command_id = payload.get("command_id")
    session_id = payload.get("session_id")
    if not isinstance(command_id, str) or not isinstance(session_id, str):
        return _json_error(
            "command_id and session_id must be strings", 400, "invalid_result"
        )
    pending = PENDING.get(command_id)
    if pending is None:
        return _json_error("command is unknown or already timed out", 404, "command_not_found")
    if pending.session_id != session_id:
        return _json_error("command belongs to another browser session", 403, "session_mismatch")
    if pending.future.done():
        return web.json_response({"ok": True, "accepted": False, "duplicate": True})

    ok = payload.get("ok", False)
    if not isinstance(ok, bool):
        return _json_error("ok must be a boolean", 400, "invalid_result")
    result = {
        "ok": ok,
        "command_id": command_id,
        "session_id": session_id,
        "revision": payload.get("revision"),
        "workflow_id": payload.get("workflow_id"),
    }
    if result["ok"]:
        command_result_data = payload.get("result", {})
        if not isinstance(command_result_data, dict):
            return _json_error("result must be a JSON object", 400, "invalid_result")
        result["result"] = command_result_data
    else:
        error = payload.get("error")
        if isinstance(error, dict):
            result["error"] = error
        else:
            result["error"] = {"code": "browser_error", "message": str(error or "unknown error")}
        _record_command_error(pending, result["error"])
    pending.future.set_result(result)
    return web.json_response({"ok": True, "accepted": True})
