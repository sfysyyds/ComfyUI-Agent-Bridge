from __future__ import annotations

from collections import defaultdict, deque
from itertools import zip_longest
import json
from typing import Any, Iterable


MODE_NAMES = {0: "active", 2: "mute", 4: "bypass"}


def _node_sort_key(node_id: str) -> tuple[int, int | str]:
    try:
        return (0, int(node_id))
    except (TypeError, ValueError):
        return (1, node_id)


def _nodes(session: dict[str, Any]) -> list[dict[str, Any]]:
    view = session.get("view")
    if not isinstance(view, dict):
        return []
    nodes = view.get("nodes")
    return nodes if isinstance(nodes, list) else []


def _link_records(session: dict[str, Any]) -> list[dict[str, Any]]:
    view = session.get("view", {})
    raw_links = view.get("links", []) if isinstance(view, dict) else []
    records: list[dict[str, Any]] = []
    iterable: Iterable[Any]
    if isinstance(raw_links, dict):
        iterable = raw_links.values()
    elif isinstance(raw_links, list):
        iterable = raw_links
    else:
        iterable = []
    for link in iterable:
        if isinstance(link, list) and len(link) >= 6:
            records.append(
                {
                    "id": link[0],
                    "origin_id": link[1],
                    "origin_slot": link[2],
                    "target_id": link[3],
                    "target_slot": link[4],
                    "type": link[5],
                }
            )
        elif isinstance(link, dict):
            records.append(
                {
                    "id": link.get("id"),
                    "origin_id": link.get("origin_id"),
                    "origin_slot": link.get("origin_slot"),
                    "target_id": link.get("target_id"),
                    "target_slot": link.get("target_slot"),
                    "type": link.get("type"),
                }
            )
    return records


def find_node(session: dict[str, Any], node_id: int | str) -> dict[str, Any]:
    wanted = str(node_id)
    for node in _nodes(session):
        if str(node.get("id")) == wanted:
            return node
    raise ValueError(f'no node with id "{node_id}" in the current live workflow')


def workflow_outline(session: dict[str, Any], limit: int = 120) -> dict[str, Any]:
    nodes = _nodes(session)
    by_id = {str(node.get("id")): node for node in nodes}
    links = _link_records(session)
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    indegree = {node_id: 0 for node_id in by_id}
    for link in links:
        source = str(link.get("origin_id"))
        target = str(link.get("target_id"))
        if source not in by_id or target not in by_id:
            continue
        outgoing[source].append(link)
        incoming[target].append(link)
        indegree[target] += 1

    queue = deque(
        sorted(
            (node_id for node_id, degree in indegree.items() if degree == 0),
            key=_node_sort_key,
        )
    )
    ordered: list[str] = []
    while queue:
        node_id = queue.popleft()
        ordered.append(node_id)
        for link in outgoing[node_id]:
            target = str(link["target_id"])
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    ordered_set = set(ordered)
    ordered.extend(
        sorted(
            (node_id for node_id in by_id if node_id not in ordered_set),
            key=_node_sort_key,
        )
    )

    lines: list[str] = []
    summaries: list[dict[str, Any]] = []
    cap = max(1, min(limit, 500))
    for node_id in ordered[:cap]:
        node = by_id[node_id]
        node_type = node.get("comfy_class") or node.get("type")
        title = node.get("title")
        mode = MODE_NAMES.get(node.get("mode"), node.get("mode"))
        widgets = []
        for widget in node.get("widgets", []):
            if not isinstance(widget, dict):
                continue
            value = widget.get("value")
            rendered = json.dumps(value, ensure_ascii=False, default=str)
            if len(rendered) > 100:
                rendered = rendered[:97] + "..."
            widgets.append(f'{widget.get("name")}={rendered}')
        header = f'#{node_id} {node_type} "{title}" [{mode}]'
        if widgets:
            header += " · " + ", ".join(widgets[:8])
            if len(widgets) > 8:
                header += f" (+{len(widgets) - 8} widgets)"
        lines.append(header)

        input_bits = []
        for link in incoming[node_id]:
            source = by_id.get(str(link["origin_id"]), {})
            source_outputs = source.get("outputs", [])
            source_index = link.get("origin_slot")
            source_name = source_index
            if isinstance(source_index, int) and 0 <= source_index < len(source_outputs):
                source_name = source_outputs[source_index].get("name", source_index)
            inputs = node.get("inputs", [])
            target_index = link.get("target_slot")
            target_name = target_index
            if isinstance(target_index, int) and 0 <= target_index < len(inputs):
                target_name = inputs[target_index].get("name", target_index)
            input_bits.append(f'{target_name} <- #{link["origin_id"]}.{source_name}')
        if input_bits:
            lines.append("  " + "; ".join(input_bits))
        summaries.append(
            {
                "id": node.get("id"),
                "class_type": node_type,
                "title": title,
                "mode": mode,
                "position": node.get("pos"),
                "input_link_count": len(incoming[node_id]),
                "output_link_count": len(outgoing[node_id]),
            }
        )

    return {
        "session_id": session.get("session_id"),
        "workflow_id": session.get("workflow_id"),
        "title": session.get("title"),
        "revision": session.get("revision"),
        "node_count": len(nodes),
        "link_count": len(links),
        "selection": session.get("view", {}).get("selection", []),
        "viewport": session.get("view", {}).get("viewport", {}),
        "truncated": len(nodes) > cap,
        "nodes": summaries,
        "outline": "\n".join(lines),
    }


def _compact_input_spec(value: Any) -> Any:
    if not isinstance(value, list) or not value:
        return value
    type_part = value[0]
    metadata = value[1] if len(value) > 1 and isinstance(value[1], dict) else {}
    if isinstance(type_part, list):
        result: dict[str, Any] = {
            "type": "enum",
            "choices": type_part[:200],
            "choice_count": len(type_part),
        }
        if len(type_part) > 200:
            result["choices_truncated"] = True
    else:
        result = {"type": type_part}
    if metadata:
        result["options"] = metadata
    return result


def compact_schema(class_type: str, schema: dict[str, Any], full: bool = False) -> dict[str, Any]:
    inputs = schema.get("input", {})
    compact_inputs: dict[str, Any] = {}
    if isinstance(inputs, dict):
        for section in ("required", "optional", "hidden"):
            values = inputs.get(section)
            if isinstance(values, dict):
                compact_inputs[section] = {
                    name: _compact_input_spec(spec) for name, spec in values.items()
                }
    result = {
        "class_type": class_type,
        "display_name": schema.get("display_name") or schema.get("name") or class_type,
        "category": schema.get("category"),
        "description": schema.get("description"),
        "python_module": schema.get("python_module"),
        "output_node": bool(schema.get("output_node")),
        "inputs": compact_inputs,
        "outputs": [],
    }
    output_types = schema.get("output", [])
    output_names = schema.get("output_name", output_types)
    if not isinstance(output_types, list):
        output_types = []
    if not isinstance(output_names, list):
        output_names = output_types
    for index, (output_type, name) in enumerate(
        zip_longest(output_types, output_names, fillvalue=None)
    ):
        if output_type is None:
            continue
        result["outputs"].append(
            {
                "index": index,
                "type": output_type,
                "name": name if name is not None else output_type,
            }
        )
    if full:
        result["raw"] = schema
    return result


def generation_result(
    prompt_id: str,
    history_entry: dict[str, Any],
    view_url,
) -> dict[str, Any]:
    files = []
    outputs = history_entry.get("outputs", {})
    if isinstance(outputs, dict):
        for node_id, node_output in outputs.items():
            if not isinstance(node_output, dict):
                continue
            for media_key in ("images", "gifs", "audio", "video"):
                values = node_output.get(media_key, [])
                if not isinstance(values, list):
                    continue
                for item in values:
                    if not isinstance(item, dict):
                        continue
                    files.append(
                        {
                            "node_id": node_id,
                            "kind": media_key,
                            **item,
                            "url": view_url(item),
                        }
                    )
    status = history_entry.get("status", {})
    return {
        "prompt_id": prompt_id,
        "status": status,
        "completed": bool(status.get("completed")) if isinstance(status, dict) else bool(outputs),
        "file_count": len(files),
        "files": files,
        "outputs": outputs if len(json.dumps(outputs, default=str)) < 200_000 else None,
    }
