import {
  MODE_VALUES,
  emitCanvasChangeBoundary,
  jsonSafe,
  normalizePosition,
  resolveNodeId,
  resolveSlot,
} from "./agent_bridge_core.js";

export const MAX_OPERATIONS = 100;

export class GraphOperationError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "GraphOperationError";
    this.code = code;
  }
}

export function defaultNodePosition(graph) {
  const nodes = graph?._nodes ?? [];
  if (!nodes.length) return [100, 100];
  const rightmost = nodes.reduce((best, node) =>
    Number(node.pos?.[0] ?? 0) > Number(best.pos?.[0] ?? 0) ? node : best);
  return [
    Number(rightmost.pos?.[0] ?? 0) + Number(rightmost.size?.[0] ?? 220) + 80,
    Number(rightmost.pos?.[1] ?? 0),
  ];
}

export function setWidget(app, node, widgetName, value) {
  const text = String(widgetName);
  let widget = (node.widgets ?? []).find((item) => item?.name === text);
  if (!widget) {
    const lower = text.toLowerCase();
    widget = (node.widgets ?? []).find(
      (item) => String(item?.name ?? "").toLowerCase() === lower,
    );
  }
  if (!widget) {
    const available = (node.widgets ?? [])
      .map((item) => item?.name)
      .filter(Boolean)
      .join(", ");
    throw new Error(
      `Node ${node.id} (${node.type}) has no widget "${text}" ` +
      `(available: ${available || "none"})`,
    );
  }
  const previous = jsonSafe(widget.value);
  widget.value = value;
  widget.callback?.(value, app.canvas, node, node.pos, undefined);
  node.onWidgetChanged?.(widget.name, value, previous, widget);
  return {
    node_id: node.id,
    widget: widget.name,
    previous,
    value: jsonSafe(widget.value),
  };
}

export function applyOperation(app, graph, operation, refs, affected) {
  if (!operation || typeof operation !== "object" || Array.isArray(operation)) {
    throw new Error("Every operation must be an object");
  }
  const kind = operation.op;
  switch (kind) {
    case "add_node": {
      if (typeof operation.class_type !== "string" || !operation.class_type.trim()) {
        throw new Error("add_node.class_type is required");
      }
      const liteGraph = globalThis.LiteGraph;
      if (!liteGraph?.createNode) throw new Error("LiteGraph.createNode is unavailable");
      const node = liteGraph.createNode(operation.class_type.trim());
      if (!node) throw new Error(`Unknown node class_type "${operation.class_type}"`);
      node.pos = normalizePosition(operation.pos, defaultNodePosition(graph));
      if (typeof operation.title === "string" && operation.title) node.title = operation.title;
      graph.add(node);
      if (operation.ref != null) {
        const ref = String(operation.ref).replace(/^\$/, "");
        if (!ref) throw new Error("add_node.ref cannot be empty");
        if (refs.has(ref)) throw new Error(`Duplicate node ref "$${ref}"`);
        refs.set(ref, node.id);
      }
      if (operation.widgets != null) {
        if (
          !operation.widgets ||
          typeof operation.widgets !== "object" ||
          Array.isArray(operation.widgets)
        ) {
          throw new Error("add_node.widgets must be an object keyed by widget name");
        }
        for (const [name, value] of Object.entries(operation.widgets)) {
          setWidget(app, node, name, value);
        }
      }
      affected.add(node.id);
      return { op: kind, node_id: node.id, ref: operation.ref ?? null, type: node.type };
    }
    case "remove_node": {
      const node = resolveNodeId(graph, operation.node_id, refs);
      const result = { op: kind, node_id: node.id, type: node.type };
      affected.add(node.id);
      graph.remove(node);
      return result;
    }
    case "set_widget": {
      const node = resolveNodeId(graph, operation.node_id, refs);
      const result = setWidget(app, node, operation.widget, operation.value);
      affected.add(node.id);
      return { op: kind, ...result };
    }
    case "connect": {
      const origin = resolveNodeId(graph, operation.from_node_id, refs);
      const target = resolveNodeId(graph, operation.to_node_id, refs);
      const outputIndex = resolveSlot(origin.outputs, operation.from_output, "output");
      const inputIndex = resolveSlot(target.inputs, operation.to_input, "input");
      const replacedLinkId = target.inputs?.[inputIndex]?.link ?? null;
      const replacedLink = replacedLinkId == null
        ? null
        : graph.links?.get?.(replacedLinkId) ?? graph.links?.[replacedLinkId] ?? null;
      const replacedOriginId = replacedLink?.origin_id ?? replacedLink?.originId ?? replacedLink?.from_node_id ?? null;
      const link = origin.connect(outputIndex, target, inputIndex);
      if (!link) {
        const output = origin.outputs?.[outputIndex];
        const input = target.inputs?.[inputIndex];
        throw new Error(
          `Connection refused: ${origin.id}.${output?.name} (${output?.type}) -> ` +
          `${target.id}.${input?.name} (${input?.type})`,
        );
      }
      affected.add(origin.id);
      affected.add(target.id);
      if (replacedOriginId != null) affected.add(replacedOriginId);
      const linkId = link?.id ?? link;
      return {
        op: kind,
        link_id: linkId ?? null,
        replaced_link_id: replacedLinkId,
        replaced_origin_node_id: replacedOriginId,
        from_output_index: outputIndex,
        to_input_index: inputIndex,
        from: { node_id: origin.id, output: origin.outputs?.[outputIndex]?.name ?? outputIndex },
        to: { node_id: target.id, input: target.inputs?.[inputIndex]?.name ?? inputIndex },
      };
    }
    case "disconnect": {
      const node = resolveNodeId(graph, operation.node_id, refs);
      const inputIndex = resolveSlot(node.inputs, operation.input, "input");
      const previousLink = node.inputs?.[inputIndex]?.link ?? null;
      const link = previousLink == null
        ? null
        : graph.links?.get?.(previousLink) ?? graph.links?.[previousLink] ?? null;
      const originId = link?.origin_id ?? link?.originId ?? link?.from_node_id ?? null;
      node.disconnectInput(inputIndex);
      affected.add(node.id);
      if (originId != null) affected.add(originId);
      return {
        op: kind,
        node_id: node.id,
        input: node.inputs?.[inputIndex]?.name ?? inputIndex,
        previous_link: previousLink,
        previous_origin_node_id: originId,
      };
    }
    case "move_node": {
      const node = resolveNodeId(graph, operation.node_id, refs);
      const previous = [...node.pos];
      node.pos = normalizePosition(operation.pos);
      affected.add(node.id);
      return { op: kind, node_id: node.id, previous, pos: [...node.pos] };
    }
    case "set_title": {
      const node = resolveNodeId(graph, operation.node_id, refs);
      if (typeof operation.title !== "string") {
        throw new Error("set_title.title must be a string");
      }
      const previous = node.title;
      node.title = operation.title.slice(0, 500);
      affected.add(node.id);
      return { op: kind, node_id: node.id, previous, title: node.title };
    }
    case "set_mode": {
      const node = resolveNodeId(graph, operation.node_id, refs);
      if (!Object.hasOwn(MODE_VALUES, operation.mode)) {
        throw new Error('set_mode.mode must be "active", "mute", or "bypass"');
      }
      const previous = Number(node.mode ?? 0);
      node.mode = MODE_VALUES[operation.mode];
      affected.add(node.id);
      return { op: kind, node_id: node.id, previous, mode: operation.mode };
    }
    default:
      throw new Error(`Unsupported graph operation "${kind}"`);
  }
}

export async function applyOperations(app, operations, highlightNodes) {
  if (!Array.isArray(operations) || !operations.length) {
    throw new GraphOperationError(
      "invalid_operations",
      "operations must be a non-empty array",
    );
  }
  if (operations.length > MAX_OPERATIONS) {
    throw new GraphOperationError(
      "too_many_operations",
      `At most ${MAX_OPERATIONS} operations are allowed`,
    );
  }
  const graph = app.graph;
  if (!graph) {
    throw new GraphOperationError("graph_unavailable", "ComfyUI graph is unavailable");
  }
  const backup = JSON.parse(JSON.stringify(graph.serialize()));
  const activeWorkflow = app.extensionManager?.workflow?.activeWorkflow ?? null;
  const refs = new Map();
  const affected = new Set();
  const results = [];
  let graphBoundaryOpen = false;
  let canvasBoundaryOpen = false;
  try {
    graph.beforeChange?.();
    graphBoundaryOpen = true;
    emitCanvasChangeBoundary(app.canvas, "before", CustomEvent);
    canvasBoundaryOpen = true;
    for (const operation of operations) {
      results.push(applyOperation(app, graph, operation, refs, affected));
    }
    graph.afterChange?.();
    graphBoundaryOpen = false;
    emitCanvasChangeBoundary(app.canvas, "after", CustomEvent);
    canvasBoundaryOpen = false;
    graph.setDirtyCanvas?.(true, true);
  } catch (error) {
    try {
      await app.loadGraphData(
        backup,
        false,
        false,
        activeWorkflow,
        {
          checkForRerouteMigration: false,
          skipAssetScans: true,
          silentAssetErrors: true,
        },
      );
      if (graphBoundaryOpen) {
        graph.afterChange?.();
        graphBoundaryOpen = false;
      }
      if (canvasBoundaryOpen) {
        emitCanvasChangeBoundary(app.canvas, "after", CustomEvent);
        canvasBoundaryOpen = false;
      }
    } catch (restoreError) {
      if (graphBoundaryOpen) graph.afterChange?.();
      if (canvasBoundaryOpen) {
        emitCanvasChangeBoundary(app.canvas, "after", CustomEvent);
      }
      throw new GraphOperationError(
        "operation_failed_restore_failed",
        `${error.message}; restoring the previous graph also failed: ${restoreError.message}`,
      );
    }
    throw new GraphOperationError(
      "operation_failed",
      `${error.message}; previous graph restored`,
    );
  }
  const highlighted = highlightNodes([...affected], 5500);
  return {
    operation_count: results.length,
    operations: results,
    refs: Object.fromEntries(refs),
    affected_node_ids: [...affected],
    highlighted: highlighted.highlighted,
  };
}

export function focusNodes(app, nodeIds) {
  const nodes = (Array.isArray(nodeIds) ? nodeIds : [])
    .map((id) => {
      try {
        return resolveNodeId(app.graph, id);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
  if (!nodes.length) {
    throw new GraphOperationError("nodes_not_found", "No matching nodes to focus");
  }
  if (typeof app.canvas?.selectItems === "function") app.canvas.selectItems(nodes);
  else if (typeof app.canvas?.selectNodes === "function") app.canvas.selectNodes(nodes);
  if (nodes.length === 1 && typeof app.canvas?.centerOnNode === "function") {
    app.canvas.centerOnNode(nodes[0]);
  } else if (typeof app.canvas?.fitViewToSelection === "function") {
    app.canvas.fitViewToSelection(nodes);
  }
  app.canvas?.setDirty?.(true, true);
  return { focused: nodes.map((node) => node.id) };
}
