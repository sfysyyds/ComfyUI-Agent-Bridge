---
name: comfyui-live-control
description: Inspect, edit, highlight, and run the exact ComfyUI workflow currently visible in the user's browser through ComfyUI Agent Bridge. Use for current-canvas questions, building a workflow on a blank tab, modifying official or third-party nodes, adding models or LoRAs, changing prompts, or generating outputs.
---

# ComfyUI Live Control

Treat the visible browser canvas as the only workflow source. Never substitute a file, remembered graph, or newly loaded workflow for the tab the user is viewing.

## Required start

1. Call `bridge_status`.
2. If more than one browser session is online, call `list_live_sessions`. Use the clearly focused/recent session; if the target is still ambiguous, ask the user to focus it.
3. Call `get_workflow_outline` and keep its `session_id`, `workflow_id`, and `revision`.
4. An outline with zero nodes is a valid current blank workflow. Build on it with `add_node`; do not create or load another workflow.
5. Call `get_node` for every existing node whose widget, title, mode, position, or connection will change.

If no live session exists, ask the user to open ComfyUI and wait for the Agent Bridge badge. Do not edit a guessed workflow file.

## Read-only diagnostics

- `bridge_status` independently reads the bridge, system, queue, and recent bridge command errors. Successful `bridge`, `system`, `queue`, and `recent_errors` fields retain their raw response shape; a failed component is `null`, with its `source`, `code`, `message`, `status_code`, and `details` under `diagnostics`. Report partial failures without discarding the components that succeeded.
- `get_recent_history(max_items=5, offset=-1)` returns bounded raw ComfyUI history (1–20 entries). The default `offset=-1` asks ComfyUI for the newest entries; `offset>=0` is an absolute index from the oldest record, not “next page from newest.” Preserve the original `status`, `status.messages`, and `execution_error` payload; do not replace raw traceback/error messages with a paraphrase. Use a prompt ID for one exact result.
- `get_raw_workflow_session` returns the selected live browser session with both serialized `workflow` and full live `view`; use it when the compact outline is insufficient. Treat its `revision` and `workflow_id` as current-canvas identity.
- `recent_errors` in `bridge_status` reports retained failed bridge command results; it is separate from ComfyUI prompt execution errors in `get_recent_history`.

## Nodes and models

- Before adding a node, search `search_node_catalog`, then call `get_node_schema` for the exact runtime `class_type`.
- For an unfamiliar or third-party class, call `get_node_schema(class_type, include_raw=true)` and inspect its runtime description, required/optional inputs, enum choices, metadata, and outputs. This is the installed ComfyUI's `/object_info` response, not a guessed official-node template.
- Before editing an existing unfamiliar node, call `get_node`; its live widget names/values and socket names/types/order are authoritative for this canvas. Use those exact names and compatible live slot types. Do not invent widget names, enum values, or connection types.
- If a third-party schema is incomplete, use the live node's actual widgets/slots and let ComfyUI's native `connect` accept or reject the connection. If intent or a required value still cannot be established, ask instead of guessing.
- Use `list_models` and write an exact returned filename.
- Compare the live source output and target input types before connecting. Prefer slot names over array indexes.
- Prefer an existing compatible loader over adding a duplicate.

## Editing contract

Use one minimal `apply_graph_operations` batch with the latest `base_revision`. A batch marks the workflow modified and automatically highlights affected nodes. Keep the returned `history_id` if the user may want to undo one node later.

Supported operations:

- `add_node`: `class_type`; optional `ref`, `pos`, `title`, `widgets`
- `remove_node`: `node_id`
- `set_widget`: `node_id`, `widget`, `value`
- `connect`: `from_node_id`, `from_output`, `to_node_id`, `to_input`
- `disconnect`: `node_id`, `input`
- `move_node`: `node_id`, `pos`
- `set_title`: `node_id`, `title`
- `set_mode`: `node_id`, `mode` (`active`, `mute`, `bypass`)

Use `$ref` for nodes created earlier in the same batch. Prefer slot names over indices.

After editing, read the outline or affected nodes again. Use `focus_nodes` when the user needs the edited section centered.

To undo one node from a recorded batch, call `undo_node_modification` with that batch's `history_id`, exact `node_id`, and the current `base_revision` (plus `session_id` when selected). It restores that node and its incident links while preserving unrelated nodes. A cross-node connection is tracked on both endpoints. The browser rejects stale/unavailable history or a target node/incident-link state changed since the recorded edit (`history_conflict`); re-read the live workflow rather than retrying with the old revision.

On `revision_conflict` or `workflow_changed`, re-read the outline and affected nodes, recompute the smallest patch, and retry once against the new identity/revision. Never reuse a stale patch.

## Running

- Use `export_current_workflow_api` to diagnose missing inputs or validation.
- Check `get_queue_status` before adding work to a busy queue.
- Queue the visible graph with `queue_live_workflow`.
- Record each returned `prompt_id`, then call `wait_for_generation`.
- Report real status and output filenames/URLs. Do not claim visual quality without inspecting output media.

For prompt variants, set one variant, queue it, record its prompt ID, then proceed to the next. Leave the last requested variant on the canvas unless the user asks to restore values.

## Safety

- Preserve unrelated nodes, branches, modes, and user edits.
- Do not clear the canvas, replace the whole workflow, save it, install nodes, download models, or modify ComfyUI files unless the user explicitly requests that separate action.
- The bridge never needs a whole-workflow load to build on a blank canvas.
- Keep strengths and generation settings reasonable unless the user supplies exact values.
