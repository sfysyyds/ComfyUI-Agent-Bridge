from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import time
from typing import Any


PROTOCOL_VERSION = "comfy-agent-bridge/v1"
PLUGIN_VERSION = "1.3.3"
ONLINE_TTL_SECONDS = 15.0
SESSION_RETENTION_SECONDS = 300.0
MAX_WORKFLOW_BYTES = 24 * 1024 * 1024


class BridgeStateError(ValueError):
    pass


class SessionNotFound(BridgeStateError):
    pass


class SessionAmbiguous(BridgeStateError):
    pass


class SessionOffline(BridgeStateError):
    pass


class RevisionConflict(BridgeStateError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(f"revision conflict: expected {expected}, current revision is {actual}")
        self.expected = expected
        self.actual = actual


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise BridgeStateError(f"workflow is not valid JSON data: {error}") from error


def _revision_bytes(workflow: dict[str, Any]) -> bytes:
    normalized = dict(workflow)
    extra = workflow.get("extra")
    if isinstance(extra, dict) and "ds" in extra:
        normalized["extra"] = {key: value for key, value in extra.items() if key != "ds"}
    nodes = workflow.get("nodes")
    if isinstance(nodes, list):
        normalized["nodes"] = [
            {key: value for key, value in node.items() if key != "size"}
            if isinstance(node, dict)
            else node
            for node in nodes
        ]
    return _canonical_bytes(normalized)


@dataclass
class SessionState:
    session_id: str
    client_id: str
    workflow_id: str
    title: str
    workflow: dict[str, Any]
    view: dict[str, Any]
    workflow_sha256: str
    revision: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    visible: bool = True
    focused: bool = True
    origin: str = "user"
    last_command_id: str | None = None
    client_activity_at_ms: float = 0.0

    def online(self, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        return current - self.last_seen_at <= ONLINE_TTL_SECONDS

    def as_dict(
        self,
        include_raw: bool = False,
        include_view: bool = True,
        now: float | None = None,
    ) -> dict[str, Any]:
        current = time.time() if now is None else now
        result = {
            "session_id": self.session_id,
            "client_id": self.client_id,
            "workflow_id": self.workflow_id,
            "title": self.title,
            "revision": self.revision,
            "workflow_sha256": self.workflow_sha256,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_seen_at": self.last_seen_at,
            "last_active_at": self.last_active_at,
            "visible": self.visible,
            "focused": self.focused,
            "online": self.online(current),
            "last_seen_age_seconds": round(max(0.0, current - self.last_seen_at), 3),
            "last_active_age_seconds": round(max(0.0, current - self.last_active_at), 3),
            "origin": self.origin,
            "last_command_id": self.last_command_id,
            "node_count": len(self.view.get("nodes", [])),
            "link_count": len(self.view.get("links", [])),
        }
        if include_view:
            result["view"] = self.view
        if include_raw:
            result["workflow"] = self.workflow
        return result


class BridgeState:
    def __init__(self) -> None:
        self.sessions: dict[str, SessionState] = {}

    def update_snapshot(self, payload: dict[str, Any], now: float | None = None) -> SessionState:
        current = time.time() if now is None else now
        session_id = payload.get("session_id")
        client_id = payload.get("client_id")
        workflow = payload.get("workflow")
        view = payload.get("view", {})

        if not isinstance(session_id, str) or not session_id.strip():
            raise BridgeStateError("session_id must be a non-empty string")
        if len(session_id) > 160:
            raise BridgeStateError("session_id is too long")
        if not isinstance(client_id, str) or not client_id.strip():
            raise BridgeStateError("client_id must be a non-empty string")
        if len(client_id) > 256:
            raise BridgeStateError("client_id is too long")
        if not isinstance(workflow, dict):
            raise BridgeStateError("workflow must be a JSON object")
        if not isinstance(workflow.get("nodes", []), list):
            raise BridgeStateError("workflow.nodes must be an array")
        if not isinstance(view, dict):
            raise BridgeStateError("view must be a JSON object")

        workflow_bytes = _canonical_bytes(workflow)
        if len(workflow_bytes) > MAX_WORKFLOW_BYTES:
            raise BridgeStateError(
                f"workflow exceeds {MAX_WORKFLOW_BYTES // (1024 * 1024)} MiB bridge limit"
            )
        workflow_sha256 = hashlib.sha256(_revision_bytes(workflow)).hexdigest()

        title = payload.get("title")
        if not isinstance(title, str):
            title = "Untitled workflow"
        title = title.strip()[:300] or "Untitled workflow"
        visible = bool(payload.get("visible", True))
        focused = bool(payload.get("focused", True))
        origin = payload.get("origin")
        if origin not in {"user", "agent", "heartbeat", "preflight"}:
            origin = "user"
        command_id = payload.get("command_id")
        if command_id is not None and not isinstance(command_id, str):
            command_id = None

        existing = self.sessions.get(session_id)
        raw_workflow_id = payload.get("workflow_id")
        if raw_workflow_id is None:
            raw_workflow_id = workflow.get("id")
        if isinstance(raw_workflow_id, (str, int)):
            workflow_id = str(raw_workflow_id).strip()
        else:
            workflow_id = ""
        if not workflow_id:
            workflow_id = existing.workflow_id if existing else f"legacy:{session_id}"
        if len(workflow_id) > 256:
            raise BridgeStateError("workflow_id is too long")

        activity_at_ms = payload.get("activity_at_ms", 0)
        if isinstance(activity_at_ms, bool) or not isinstance(activity_at_ms, (int, float)):
            activity_at_ms = 0
        activity_at_ms = max(0.0, float(activity_at_ms))

        if existing is None:
            session = SessionState(
                session_id=session_id,
                client_id=client_id,
                workflow_id=workflow_id,
                title=title,
                workflow=workflow,
                view=view,
                workflow_sha256=workflow_sha256,
                created_at=current,
                updated_at=current,
                last_seen_at=current,
                last_active_at=current,
                visible=visible,
                focused=focused,
                origin=origin,
                last_command_id=command_id,
                client_activity_at_ms=activity_at_ms,
            )
            self.sessions[session_id] = session
            return session

        changed = (
            existing.workflow_sha256 != workflow_sha256
            or existing.workflow_id != workflow_id
        )
        focused_transition = focused and not existing.focused
        activity_advanced = activity_at_ms > existing.client_activity_at_ms
        existing.client_id = client_id
        existing.workflow_id = workflow_id
        existing.title = title
        existing.workflow = workflow
        existing.view = view
        existing.workflow_sha256 = workflow_sha256
        existing.last_seen_at = current
        existing.visible = visible
        existing.focused = focused
        existing.origin = origin
        existing.last_command_id = command_id
        existing.client_activity_at_ms = max(
            existing.client_activity_at_ms, activity_at_ms
        )
        if focused_transition or activity_advanced:
            existing.last_active_at = current
        elif not payload.get("activity_at_ms") and focused and changed and origin == "user":
            # Compatibility with 1.0 browser extensions, which did not report activity.
            existing.last_active_at = current
        if changed:
            existing.revision += 1
            existing.updated_at = current
        return existing

    def heartbeat(self, payload: dict[str, Any], now: float | None = None) -> SessionState:
        current = time.time() if now is None else now
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or session_id not in self.sessions:
            raise SessionNotFound("browser session has not submitted a workflow snapshot yet")
        session = self.sessions[session_id]
        client_id = payload.get("client_id")
        if isinstance(client_id, str) and client_id:
            session.client_id = client_id
        title = payload.get("title")
        if isinstance(title, str) and title.strip():
            session.title = title.strip()[:300]
        activity_at_ms = payload.get("activity_at_ms", 0)
        if isinstance(activity_at_ms, bool) or not isinstance(activity_at_ms, (int, float)):
            activity_at_ms = 0
        activity_at_ms = max(0.0, float(activity_at_ms))
        focused_before = session.focused
        session.visible = bool(payload.get("visible", session.visible))
        session.focused = bool(payload.get("focused", session.focused))
        session.last_seen_at = current
        activity_advanced = activity_at_ms > session.client_activity_at_ms
        session.client_activity_at_ms = max(
            session.client_activity_at_ms, activity_at_ms
        )
        if activity_advanced or (session.focused and not focused_before):
            session.last_active_at = current
        return session

    def prune(self, now: float | None = None) -> None:
        current = time.time() if now is None else now
        expired = [
            session_id
            for session_id, session in self.sessions.items()
            if current - session.last_seen_at > SESSION_RETENTION_SECONDS
        ]
        for session_id in expired:
            del self.sessions[session_id]

    def list_sessions(self, now: float | None = None) -> list[dict[str, Any]]:
        current = time.time() if now is None else now
        self.prune(current)
        sessions = [
            session.as_dict(include_raw=False, include_view=False, now=current)
            for session in self.sessions.values()
        ]
        sessions.sort(
            key=lambda item: (
                bool(item["online"]),
                bool(item["focused"]),
                bool(item["visible"]),
                float(item["last_active_at"]),
            ),
            reverse=True,
        )
        return sessions

    def get(self, session_id: str) -> SessionState:
        self.prune()
        try:
            return self.sessions[session_id]
        except KeyError as error:
            raise SessionNotFound(f'no browser session "{session_id}"') from error

    def resolve(self, session_id: str | None = None, require_online: bool = True) -> SessionState:
        self.prune()
        if session_id:
            session = self.get(session_id)
            if require_online and not session.online():
                raise SessionOffline(f'browser session "{session_id}" is offline')
            return session

        online = [session for session in self.sessions.values() if session.online()]
        if not online:
            raise SessionNotFound(
                "no live ComfyUI browser session; open ComfyUI in a browser and wait for the Agent Bridge badge"
            )
        if len(online) == 1:
            return online[0]

        focused = [session for session in online if session.focused and session.visible]
        if len(focused) == 1:
            return focused[0]
        candidates = focused or [session for session in online if session.visible] or online
        candidates.sort(key=lambda session: session.last_active_at, reverse=True)
        if (
            len(candidates) > 1
            and abs(candidates[0].last_active_at - candidates[1].last_active_at) < 0.5
        ):
            raise SessionAmbiguous(
                "multiple ComfyUI browser sessions are active; call list_live_sessions and pass session_id"
            )
        return candidates[0]

    @staticmethod
    def check_revision(session: SessionState, expected: int | None) -> None:
        if expected is None:
            raise BridgeStateError("base_revision is required for graph mutations")
        if isinstance(expected, bool) or not isinstance(expected, int):
            raise BridgeStateError("base_revision must be an integer")
        if expected != session.revision:
            raise RevisionConflict(expected, session.revision)


def validate_undo_payload(payload: dict[str, Any]) -> None:
    """Validate the stable, browser-owned per-node undo command envelope."""
    history_id = payload.get("history_id")
    if (
        not isinstance(history_id, str)
        or not history_id.strip()
        or len(history_id) > 160
    ):
        raise BridgeStateError(
            "undo_node.history_id must be a non-empty string of at most 160 characters"
        )
    node_id = payload.get("node_id")
    if (
        isinstance(node_id, bool)
        or not isinstance(node_id, (int, str))
        or not str(node_id).strip()
        or len(str(node_id)) > 256
    ):
        raise BridgeStateError("undo_node.node_id must be a non-empty string or integer")
