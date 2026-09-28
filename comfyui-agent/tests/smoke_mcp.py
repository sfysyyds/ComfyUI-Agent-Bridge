from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYTHON = Path(
    os.environ.get("COMFYUI_AGENT_BRIDGE_PYTHON")
    or Path(os.environ.get("LOCALAPPDATA", PACKAGE_ROOT))
    / "ComfyUI-Agent-Bridge"
    / "runtime"
    / ".venv"
    / "Scripts"
    / "python.exe"
)

EXPECTED_TOOLS = {
    "bridge_status",
    "list_live_sessions",
    "get_workflow_outline",
    "get_node",
    "search_node_catalog",
    "get_node_schema",
    "list_models",
    "apply_graph_operations",
    "undo_node_modification",
    "focus_nodes",
    "export_current_workflow_api",
    "queue_live_workflow",
    "get_generation_result",
    "get_raw_workflow_session",
    "get_recent_history",
    "wait_for_generation",
    "get_queue_status",
}


def structured(result: Any) -> dict[str, Any]:
    value = getattr(result, "structuredContent", None)
    if isinstance(value, dict):
        return value
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if isinstance(text, str):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    raise RuntimeError(f"MCP result did not contain structured JSON: {result!r}")


async def run(
    comfyui_url: str,
    mutate: bool,
    session_id: str | None,
    python_executable: Path,
    offline: bool,
) -> dict[str, Any]:
    source_root = PACKAGE_ROOT / "mcp_server" / "src"
    python_path = os.pathsep.join(
        value
        for value in (str(source_root), os.environ.get("PYTHONPATH", ""))
        if value
    )
    params = StdioServerParameters(
        command=str(python_executable),
        args=["-m", "comfyui_agent_bridge_mcp"],
        env={
            **os.environ,
            "COMFYUI_URL": comfyui_url.rstrip("/"),
            "PYTHONPATH": python_path,
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}
            missing = sorted(EXPECTED_TOOLS - names)
            unexpected = sorted(names - EXPECTED_TOOLS)
            if missing or unexpected:
                raise RuntimeError(
                    f"MCP tool surface mismatch; missing={missing}, unexpected={unexpected}"
                )
            if offline:
                return {
                    "initialized": True,
                    "tool_count": len(names),
                    "tools": sorted(names),
                }

            bridge = structured(await session.call_tool("bridge_status", {}))
            sessions = structured(await session.call_tool("list_live_sessions", {}))
            selected = {"session_id": session_id} if session_id else {}
            outline = structured(await session.call_tool("get_workflow_outline", selected))
            core_schema = structured(
                await session.call_tool(
                    "get_node_schema",
                    {"class_type": "KSampler", "include_raw": False},
                )
            )
            gguf = structured(
                await session.call_tool(
                    "search_node_catalog",
                    {"query": "GGUF", "limit": 20},
                )
            )
            rgthree = structured(
                await session.call_tool(
                    "search_node_catalog",
                    {"query": "rgthree", "limit": 20},
                )
            )

            mutation = None
            if mutate:
                nodes = [
                    node
                    for node in outline.get("nodes", [])
                    if node.get("class_type") == "KSampler"
                ]
                if not nodes:
                    raise RuntimeError("live workflow has no KSampler for mutation smoke test")
                node_id = str(nodes[0]["id"])
                revision = int(outline["revision"])
                mutation = structured(
                    await session.call_tool(
                        "apply_graph_operations",
                        {
                            "base_revision": revision,
                            "operations": [
                                {
                                    "op": "set_title",
                                    "node_id": node_id,
                                    "title": "KSampler MCP live test",
                                }
                            ],
                            "reason": "end-to-end smoke test",
                            **selected,
                        },
                    )
                )
            return {
                "tool_count": len(names),
                "bridge": bridge,
                "sessions": sessions,
                "outline": outline,
                "ksampler_schema": {
                    "class_type": core_schema.get("class_type"),
                    "category": core_schema.get("category"),
                    "input_groups": sorted(core_schema.get("inputs", {})),
                },
                "third_party": {
                    "gguf_matches": gguf.get("count"),
                    "rgthree_matches": rgthree.get("count"),
                },
                "mutation": mutation,
            }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8188")
    parser.add_argument("--session-id")
    parser.add_argument("--mutate", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    args = parser.parse_args()
    result = asyncio.run(
        run(
            args.url,
            args.mutate,
            args.session_id,
            args.python,
            args.offline,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
