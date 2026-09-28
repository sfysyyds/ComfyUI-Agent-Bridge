export const MODE_VALUES = Object.freeze({
  active: 0,
  mute: 2,
  bypass: 4,
});

export function fnv1a(text) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

export function workflowIdentity(workflow, title = "Untitled workflow") {
  const direct = workflow?.id;
  if (typeof direct === "string" && direct.trim()) return direct.trim();
  if (typeof direct === "number" && Number.isFinite(direct)) return String(direct);
  const extra = workflow?.extra;
  for (const candidate of [extra?.workflow_id, extra?.workflowId, extra?.id]) {
    if (typeof candidate === "string" && candidate.trim()) return candidate.trim();
  }
  return `legacy-${fnv1a(String(title || "Untitled workflow"))}`;
}

export function workflowRevisionPayload(workflow) {
  const normalized = { ...(workflow ?? {}) };
  if (
    workflow?.extra &&
    typeof workflow.extra === "object" &&
    !Array.isArray(workflow.extra)
  ) {
    normalized.extra = { ...workflow.extra };
    delete normalized.extra.ds;
  }
  if (Array.isArray(workflow?.nodes)) {
    normalized.nodes = workflow.nodes.map((node) => {
      if (!node || typeof node !== "object" || Array.isArray(node)) return node;
      const result = { ...node };
      delete result.size;
      return result;
    });
  }
  return normalized;
}

export function workflowRevisionHash(workflow) {
  return fnv1a(JSON.stringify(workflowRevisionPayload(workflow)));
}

export function clampHighlightDuration(durationMs, fallbackMs = 3200) {
  const parsed = Number(durationMs);
  const value = Number.isFinite(parsed) && parsed > 0 ? parsed : fallbackMs;
  return Math.max(1000, Math.min(value, 60000));
}

export function clampConsolePosition(left, top, width, height, viewportWidth, viewportHeight) {
  return {
    left: Math.min(Math.max(0, left), Math.max(0, viewportWidth - width)),
    top: Math.min(Math.max(0, top), Math.max(0, viewportHeight - height)),
  };
}

export function graphRectToScreen(pos, size, scale, offset) {
  const safeScale = Number(scale);
  const zoom = Number.isFinite(safeScale) && safeScale > 0 ? safeScale : 1;
  const x = Number(pos?.[0] ?? 0);
  const y = Number(pos?.[1] ?? 0);
  const width = Number(size?.[0] ?? 0);
  const height = Number(size?.[1] ?? 0);
  const offsetX = Number(offset?.[0] ?? 0);
  const offsetY = Number(offset?.[1] ?? 0);
  return {
    x: (x + offsetX) * zoom,
    y: (y + offsetY) * zoom,
    width: Math.max(0, width * zoom),
    height: Math.max(0, height * zoom),
  };
}

export function nodeRectToScreen(node, scale, offset) {
  const bounds = node.getBounding?.();
  if (bounds?.length >= 4) {
    return graphRectToScreen([bounds[0], bounds[1]], [bounds[2], bounds[3]], scale, offset);
  }
  return graphRectToScreen(node.pos, node.size, scale, offset);
}

function workflowNodeIndex(workflow, nodeId) {
  return (workflow?.nodes ?? []).findIndex((node) => String(node?.id) === String(nodeId));
}

function linkFields(link) {
  if (Array.isArray(link)) {
    return { id: link[0], from: link[1], fromSlot: link[2], to: link[3], toSlot: link[4] };
  }
  return {
    id: link?.id,
    from: link?.origin_id ?? link?.originId ?? link?.from_node_id,
    fromSlot: link?.origin_slot ?? link?.originSlot ?? link?.from_slot,
    to: link?.target_id ?? link?.targetId ?? link?.to_node_id,
    toSlot: link?.target_slot ?? link?.targetSlot ?? link?.to_slot,
  };
}

function nodeLinks(workflow, nodeId, extraLinkIds = []) {
  const extra = new Set(extraLinkIds.map(String));
  return (workflow?.links ?? [])
    .filter((link) => {
      const fields = linkFields(link);
      return String(fields.from) === String(nodeId) || String(fields.to) === String(nodeId) || extra.has(String(fields.id));
    })
    .map((link) => JSON.parse(JSON.stringify(link)))
    .sort((a, b) => String(linkFields(a).id).localeCompare(String(linkFields(b).id)));
}

export function snapshotNodeState(workflow, nodeId, extraLinkIds = []) {
  const index = workflowNodeIndex(workflow, nodeId);
  const node = index < 0 ? null : workflow.nodes[index];
  return {
    node: node ? JSON.parse(JSON.stringify(node)) : null,
    index,
    links: nodeLinks(workflow, nodeId, extraLinkIds),
  };
}

function equalJson(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function historyConflict(message) {
  return Object.assign(new Error(message), { code: "history_conflict" });
}

export function captureNodeChange(beforeWorkflow, afterWorkflow, nodeId, extraLinkIds = []) {
  const linkIds = [...new Set(extraLinkIds.map(String))].sort();
  const before = snapshotNodeState(beforeWorkflow, nodeId, linkIds);
  const after = snapshotNodeState(afterWorkflow, nodeId, linkIds);
  return equalJson(before, after) ? null : { node_id: nodeId, linkIds, before, after };
}

export function nodeChangeStateMatches(workflow, change, side) {
  const expected = change?.[side];
  const current = snapshotNodeState(workflow, change?.node_id, change?.linkIds ?? []);
  return equalJson(current.node, expected?.node) && equalJson(current.links, expected?.links);
}

export function nodeLinkDelta(change) {
  const key = (link) => JSON.stringify(link);
  const before = change?.before?.links ?? [];
  const after = change?.after?.links ?? [];
  return {
    remove: after.filter((link) => !before.some((item) => key(item) === key(link))),
    add: before.filter((link) => !after.some((item) => key(item) === key(link))),
  };
}

function setSerializedEndpointLinks(node, nodeId, links) {
  if (Array.isArray(node.inputs)) {
    node.inputs = node.inputs.map((input, index) => {
      if (!Object.hasOwn(input ?? {}, "link") && !links.some((link) => {
        const fields = linkFields(link);
        return String(fields.to) === String(nodeId) && Number(fields.toSlot) === index;
      })) return input;
      const match = links.find((link) => {
        const fields = linkFields(link);
        return String(fields.to) === String(nodeId) && Number(fields.toSlot) === index;
      });
      return { ...input, link: match ? linkFields(match).id : null };
    });
  }
  if (Array.isArray(node.outputs)) {
    node.outputs = node.outputs.map((output, index) => {
      const matches = links.filter((link) => {
        const fields = linkFields(link);
        return String(fields.from) === String(nodeId) && Number(fields.fromSlot) === index;
      }).map((link) => linkFields(link).id);
      if (!Object.hasOwn(output ?? {}, "links") && !matches.length) return output;
      return { ...output, links: matches.length ? matches : null };
    });
  }
}

export function restoreNodeChange(workflow, change) {
  const nodeId = change?.node_id;
  const currentIndex = workflowNodeIndex(workflow, nodeId);
  const currentNode = currentIndex < 0 ? null : workflow.nodes[currentIndex];
  const linkIds = new Set((change?.linkIds ?? []).map(String));
  const currentLinks = nodeLinks(workflow, nodeId, [...linkIds]);
  if (!equalJson(currentNode, change?.after?.node) || !equalJson(currentLinks, change?.after?.links)) {
    throw historyConflict(`Node ${nodeId} changed after this history entry`);
  }

  const result = JSON.parse(JSON.stringify(workflow));
  const nodes = result.nodes ?? [];
  const resultIndex = workflowNodeIndex(result, nodeId);
  if (change.before.node == null) {
    if (resultIndex >= 0) nodes.splice(resultIndex, 1);
  } else if (resultIndex >= 0) {
    nodes[resultIndex] = JSON.parse(JSON.stringify(change.before.node));
  } else {
    nodes.splice(Math.max(0, Math.min(change.before.index, nodes.length)), 0, JSON.parse(JSON.stringify(change.before.node)));
  }

  const currentWorkflowLinks = result.links ?? [];
  const unrelatedLinks = currentWorkflowLinks.filter((link) => {
    const fields = linkFields(link);
    return String(fields.from) !== String(nodeId) && String(fields.to) !== String(nodeId) && !linkIds.has(String(fields.id));
  });
  const unrelatedIds = new Set(unrelatedLinks.map((link) => String(linkFields(link).id)));
  if (change.before.links.some((link) => unrelatedIds.has(String(linkFields(link).id)))) {
    throw historyConflict(`A link for node ${nodeId} was reused`);
  }
  result.links = [...unrelatedLinks, ...JSON.parse(JSON.stringify(change.before.links))];

  const endpointIds = new Set([String(nodeId)]);
  for (const link of [...currentLinks, ...change.before.links]) {
    const fields = linkFields(link);
    endpointIds.add(String(fields.from));
    endpointIds.add(String(fields.to));
  }
  for (const endpointId of endpointIds) {
    if (endpointId === String(nodeId)) continue;
    const peer = nodes.find((node) => String(node?.id) === endpointId);
    if (!peer) throw historyConflict(`Connected node ${endpointId} no longer exists`);
    setSerializedEndpointLinks(peer, endpointId, result.links);
  }
  return result;
}

export function emitCanvasChangeBoundary(canvas, phase, EventCtor = globalThis.CustomEvent) {
  if (phase !== "before" && phase !== "after") {
    throw new Error('phase must be "before" or "after"');
  }
  const method = phase === "before" ? "emitBeforeChange" : "emitAfterChange";
  if (typeof canvas?.[method] === "function") {
    canvas[method]();
    return "method";
  }
  if (typeof canvas?.canvas?.dispatchEvent === "function" && typeof EventCtor === "function") {
    canvas.canvas.dispatchEvent(
      new EventCtor("litegraph:canvas", {
        bubbles: true,
        detail: { subType: `${phase}-change` },
      }),
    );
    return "event";
  }
  return "unavailable";
}

export function jsonSafe(value) {
  if (value == null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return String(value);
  }
}

export function resolveNodeId(graph, value, refs = new Map()) {
  let resolved = value;
  if (typeof resolved === "string" && resolved.startsWith("$")) {
    const key = resolved.slice(1);
    if (!refs.has(key)) throw new Error(`Unknown node ref "${resolved}"`);
    resolved = refs.get(key);
  }
  const candidates = [resolved];
  if (typeof resolved === "string" && /^-?\d+$/.test(resolved)) {
    candidates.push(Number(resolved));
  }
  for (const candidate of candidates) {
    const node = graph.getNodeById?.(candidate);
    if (node) return node;
  }
  throw new Error(`No node with id "${resolved}"`);
}

export function resolveSlot(slots, reference, kind) {
  const list = Array.isArray(slots) ? slots : [];
  if (!list.length) throw new Error(`Node has no ${kind} slots`);
  if (reference == null || reference === "") return 0;
  if (typeof reference === "number" && Number.isInteger(reference)) {
    if (reference >= 0 && reference < list.length) return reference;
    throw new Error(`${kind} index ${reference} is out of range (0-${list.length - 1})`);
  }
  const text = String(reference);
  if (/^\d+$/.test(text)) return resolveSlot(list, Number(text), kind);
  let index = list.findIndex((slot) => slot?.name === text);
  if (index < 0) {
    const lower = text.toLowerCase();
    index = list.findIndex((slot) => String(slot?.name ?? "").toLowerCase() === lower);
  }
  if (index < 0) {
    const available = list.map((slot, slotIndex) => `${slotIndex}:${slot?.name ?? "unnamed"}`).join(", ");
    throw new Error(`Unknown ${kind} "${text}" (available: ${available})`);
  }
  return index;
}

export function normalizePosition(pos, fallback = [100, 100]) {
  if (pos == null) return [...fallback];
  if (!Array.isArray(pos) || pos.length !== 2) throw new Error("pos must be [x, y]");
  const x = Number(pos[0]);
  const y = Number(pos[1]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) throw new Error("pos values must be finite numbers");
  return [x, y];
}

export function operationNodeRefs(operation) {
  const refs = [];
  for (const key of ["node_id", "from_node_id", "to_node_id"]) {
    const value = operation?.[key];
    if (value != null && !(typeof value === "string" && value.startsWith("$"))) refs.push(value);
  }
  return refs;
}
