from __future__ import annotations

import asyncio
import os
from functools import lru_cache
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import BridgeHttpError, ComfyBridgeClient
from .workflow import (
    compact_schema,
    find_node,
    generation_result,
    workflow_outline,
)


mcp = FastMCP(
    "ComfyUI Live Agent Bridge",
    instructions=(
        "Control the workflow currently visible in the user's ComfyUI browser. "
        "Start with bridge_status and get_workflow_outline; keep their session_id, "
        "workflow_id, and revision. Read affected nodes and runtime schemas before editing. "
        "Mutations must pass base_revision and use apply_graph_operations, which edits the "
        "current tab as one transaction and highlights changes. Keep its history_id for "
        "per-node undo. On any conflict, re-read."
    ),
)


@lru_cache(maxsize=1)
def _client() -> ComfyBridgeClient:
    return ComfyBridgeClient()


def _raise_friendly(error: BridgeHttpError) -> None:
    details = f" details={error.details}" if error.details else ""
    raise RuntimeError(f"{error.code}: {error.message}{details}") from error


@mcp.tool()
async def bridge_status() -> dict[str, Any]:
    """Read bridge, system, queue, and recent command errors independently; inspect diagnostics for per-source failures."""
    client = _client()
    async def read(source: str, operation):
        try:
            return await operation, None
        except BridgeHttpError as error:
            return None, {
                "source": source,
                "status_code": error.status_code,
                "code": error.code,
                "message": error.message,
                "details": error.details,
            }

    names_and_reads = (
        ("bridge", "comfyui.bridge_status", client.bridge_status()),
        ("system", "comfyui.system_stats", client.system_stats()),
        ("queue", "comfyui.queue", client.queue()),
        ("recent_errors", "comfyui_agent_bridge.errors", client.recent_errors()),
    )
    results = await asyncio.gather(
        *(read(source, operation) for _, source, operation in names_and_reads)
    )
    components = {
        name: {"value": value, "error": error}
        for (name, _, _), (value, error) in zip(names_and_reads, results)
    }
    return {
        "comfyui_url": client.base_url,
        **{name: component["value"] for name, component in components.items()},
        "diagnostics": {
            name: (
                {"ok": True}
                if component["error"] is None
                else {"ok": False, "error": component["error"]}
            )
            for name, component in components.items()
        },
    }


@mcp.tool()
async def list_live_sessions() -> dict[str, Any]:
    """List ComfyUI browser canvases, their titles, online state, focus, node count, and revision."""
    try:
        sessions = await _client().sessions()
    except BridgeHttpError as error:
        _raise_friendly(error)
    return {"count": len(sessions), "sessions": sessions}


@mcp.tool()
async def get_workflow_outline(
    session_id: str | None = None,
    limit: int = 120,
) -> dict[str, Any]:
    """Read a compact dependency-ordered outline of the workflow the user is viewing. Call this first."""
    try:
        session = await _client().session(session_id, raw=False)
    except BridgeHttpError as error:
        _raise_friendly(error)
    return workflow_outline(session, limit=limit)


@mcp.tool()
async def get_raw_workflow_session(session_id: str | None = None) -> dict[str, Any]:
    """Read the full active browser session, including raw serialized workflow and live canvas view."""
    try:
        return await _client().session(session_id, raw=True)
    except BridgeHttpError as error:
        _raise_friendly(error)


@mcp.tool()
async def get_node(
    node_id: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Read one live node with exact dynamic widgets, slots, values, geometry, mode, and links."""
    try:
        session = await _client().session(session_id, raw=False)
        node = find_node(session, node_id)
    except BridgeHttpError as error:
        _raise_friendly(error)
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    return {
        "session_id": session["session_id"],
        "workflow_id": session.get("workflow_id"),
        "revision": session["revision"],
        "node": node,
    }


@mcp.tool()
async def search_node_catalog(
    query: str,
    category: str | None = None,
    limit: int = 40,
) -> dict[str, Any]:
    """Search all node classes installed in the running ComfyUI, including third-party packs."""
    try:
        catalog = await _client().object_info()
    except BridgeHttpError as error:
        _raise_friendly(error)
    needle = query.casefold().strip()
    category_needle = category.casefold().strip() if category else None
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    cap = max(1, min(limit, 200))
    for class_type, schema in catalog.items():
        if not isinstance(schema, dict):
            continue
        category_value = str(schema.get("category") or "")
        if category_needle and category_needle not in category_value.casefold():
            continue
        display_name = str(schema.get("display_name") or "")
        searchable = " ".join(
            [
                class_type,
                display_name,
                category_value,
                str(schema.get("description") or ""),
                str(schema.get("python_module") or ""),
            ]
        ).casefold()
        if needle and needle not in searchable:
            continue
        summary = compact_schema(class_type, schema, full=False)
        input_count = sum(len(values) for values in summary["inputs"].values())
        class_folded = class_type.casefold()
        display_folded = display_name.casefold()
        score = 0
        if needle:
            if class_folded == needle or display_folded == needle:
                score = 100
            elif class_folded.startswith(needle) or display_folded.startswith(needle):
                score = 80
            elif needle in class_folded or needle in display_folded:
                score = 60
            elif needle in category_value.casefold():
                score = 30
            else:
                score = 10
        ranked.append(
            (
                score,
                class_folded,
                {
                    "class_type": class_type,
                    "display_name": summary["display_name"],
                    "category": summary["category"],
                    "description": (summary["description"] or "")[:300],
                    "python_module": summary["python_module"],
                    "input_count": input_count,
                    "outputs": summary["outputs"],
                },
            )
        )
    ranked.sort(key=lambda item: (-item[0], item[1]))
    matches = [item[2] for item in ranked[:cap]]
    return {
        "query": query,
        "installed_node_class_count": len(catalog),
        "count": len(matches),
        "matched": len(ranked),
        "truncated": len(ranked) > cap,
        "matches": matches,
    }


@mcp.tool()
async def get_node_schema(class_type: str, include_raw: bool = False) -> dict[str, Any]:
    """Get the runtime input/output schema for an exact official or third-party node class."""
    try:
        data = await _client().object_info(class_type)
    except BridgeHttpError as error:
        _raise_friendly(error)
    schema = data.get(class_type)
    if not isinstance(schema, dict):
        raise RuntimeError(
            f'node class "{class_type}" is not installed; use search_node_catalog for exact names'
        )
    return compact_schema(class_type, schema, full=include_raw)


@mcp.tool()
async def list_models(
    folder: str = "loras",
    query: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    """List model filenames exposed by ComfyUI. Typical folders: loras, checkpoints, vae, controlnet, diffusion_models."""
    try:
        values = await _client().models(folder)
    except BridgeHttpError as error:
        _raise_friendly(error)
    needle = query.casefold().strip()
    filtered = [value for value in values if not needle or needle in value.casefold()]
    cap = max(1, min(limit, 1000))
    return {
        "folder": folder,
        "query": query,
        "total": len(values),
        "matched": len(filtered),
        "truncated": len(filtered) > cap,
        "models": filtered[:cap],
    }


@mcp.tool()
async def apply_graph_operations(
    base_revision: int,
    operations: list[dict[str, Any]],
    session_id: str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """Apply one workflow transaction; return its history_id for optional per-node undo."""
    try:
        result = await _client().command(
            "apply_operations",
            {"operations": operations, "reason": reason[:500]},
            session_id=session_id,
            base_revision=base_revision,
            timeout_ms=20000,
        )
    except BridgeHttpError as error:
        _raise_friendly(error)
    if result.get("command_id"):
        result.setdefault("history_id", result["command_id"])
    return result


@mcp.tool()
async def undo_node_modification(
    history_id: str,
    node_id: str,
    base_revision: int,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Undo one node's change from an apply_graph_operations history entry."""
    try:
        return await _client().command(
            "undo_node",
            {"history_id": history_id, "node_id": node_id},
            session_id=session_id,
            base_revision=base_revision,
            timeout_ms=20000,
        )
    except BridgeHttpError as error:
        _raise_friendly(error)


@mcp.tool()
async def focus_nodes(
    node_ids: list[str],
    session_id: str | None = None,
    duration_ms: int = 5000,
) -> dict[str, Any]:
    """Select, center, and highlight nodes in the browser so the user can see the target."""
    try:
        return await _client().command(
            "focus_nodes",
            {"node_ids": node_ids, "duration_ms": duration_ms},
            session_id=session_id,
        )
    except BridgeHttpError as error:
        _raise_friendly(error)


@mcp.tool()
async def export_current_workflow_api(session_id: str | None = None) -> dict[str, Any]:
    """Convert the currently viewed UI graph to ComfyUI API/prompt format in the browser."""
    try:
        return await _client().command("export_api", {}, session_id=session_id, timeout_ms=20000)
    except BridgeHttpError as error:
        _raise_friendly(error)


@mcp.tool()
async def queue_live_workflow(
    session_id: str | None = None,
    count: int = 1,
    front: bool = False,
    output_node_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Queue the exact workflow open in the browser and return prompt ids for progress/result checks."""
    try:
        return await _client().command(
            "queue_prompt",
            {
                "count": count,
                "front": front,
                "node_ids": output_node_ids or [],
            },
            session_id=session_id,
            timeout_ms=30000,
        )
    except BridgeHttpError as error:
        _raise_friendly(error)


@mcp.tool()
async def get_generation_result(prompt_id: str) -> dict[str, Any]:
    """Read a finished prompt's status and generated media URLs from ComfyUI history."""
    client = _client()
    try:
        history = await client.history(prompt_id)
    except BridgeHttpError as error:
        _raise_friendly(error)
    entry = history.get(prompt_id)
    if not isinstance(entry, dict):
        return {"prompt_id": prompt_id, "completed": False, "found": False}
    return {"found": True, **generation_result(prompt_id, entry, client.view_url)}


@mcp.tool()
async def get_recent_history(max_items: int = 5, offset: int = -1) -> dict[str, Any]:
    """Read bounded raw recent ComfyUI prompt history, preserving status.messages and execution_error details."""
    max_items = max(1, min(int(max_items), 20))
    offset = max(-1, int(offset))
    try:
        history = await _client().recent_history(max_items=max_items, offset=offset)
    except BridgeHttpError as error:
        _raise_friendly(error)
    return {
        "max_items": max_items,
        "offset": offset,
        "count": len(history),
        "history": history,
    }


@mcp.tool()
async def wait_for_generation(
    prompt_id: str,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Wait for one queued prompt to finish, then return status and generated media URLs."""
    client = _client()
    timeout_seconds = max(1, min(timeout_seconds, 1800))
    try:
        entry = await client.wait_for_history(prompt_id, timeout_seconds=timeout_seconds)
    except BridgeHttpError as error:
        _raise_friendly(error)
    return generation_result(prompt_id, entry, client.view_url)


@mcp.tool()
async def get_queue_status() -> dict[str, Any]:
    """Read ComfyUI's currently running and pending queues."""
    try:
        return await _client().queue()
    except BridgeHttpError as error:
        _raise_friendly(error)


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport != "stdio":
        raise RuntimeError("This package currently supports MCP stdio transport only")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
