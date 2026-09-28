import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  clampConsolePosition,
  clampHighlightDuration,
  captureNodeChange,
  emitCanvasChangeBoundary,
  fnv1a,
  graphRectToScreen,
  nodeRectToScreen,
  nodeChangeStateMatches,
  nodeLinkDelta,
  normalizePosition,
  resolveNodeId,
  resolveSlot,
  restoreNodeChange,
  snapshotNodeState,
  workflowIdentity,
  workflowRevisionHash,
} from "../../comfyui-plugin/web/agent_bridge_core.js";
import {
  applyOperation,
  defaultNodePosition,
} from "../../comfyui-plugin/web/agent_bridge_graph.js";
import { createNodeHighlighter } from "../../comfyui-plugin/web/agent_bridge_highlight.js";

test("undo canvas boundary helper is bound from the shared module", async () => {
  const source = await readFile(new URL("../../comfyui-plugin/web/agent_bridge.js", import.meta.url), "utf8");
  assert.match(source, /import\s*\{[^}]*\bemitCanvasChangeBoundary\b[^}]*\}\s*from\s*["']\.\/agent_bridge_core\.js["']/s);
});

test("fnv1a is deterministic", () => {
  assert.equal(fnv1a("workflow"), fnv1a("workflow"));
  assert.notEqual(fnv1a("workflow"), fnv1a("workflow-2"));
});

test("resolveSlot accepts exact names, case-insensitive names, and indexes", () => {
  const slots = [{ name: "MODEL" }, { name: "CLIP" }];
  assert.equal(resolveSlot(slots, "MODEL", "output"), 0);
  assert.equal(resolveSlot(slots, "clip", "output"), 1);
  assert.equal(resolveSlot(slots, 1, "output"), 1);
});

test("resolveNodeId expands transaction refs", () => {
  const node = { id: 42 };
  const graph = { getNodeById: (id) => id === 42 ? node : null };
  assert.equal(resolveNodeId(graph, "$new", new Map([["new", 42]])), node);
});

test("normalizePosition rejects invalid coordinates", () => {
  assert.deepEqual(normalizePosition([1, 2]), [1, 2]);
  assert.throws(() => normalizePosition(["x", 2]), /finite numbers/);
});

test("workflowIdentity prefers the native workflow UUID", () => {
  assert.equal(workflowIdentity({ id: "workflow-uuid" }, "Old title"), "workflow-uuid");
  assert.match(workflowIdentity({}, "Unsaved Workflow (7)"), /^legacy-/);
});

test("revision hash ignores viewport and rendered size but tracks position", () => {
  const base = {
    id: "w1",
    extra: { ds: { scale: 1 }, stable: true },
    nodes: [{ id: 1, type: "Example", size: [200, 100], pos: [1, 2] }],
  };
  const resized = {
    ...base,
    extra: { ds: { scale: 2 }, stable: true },
    nodes: [{ ...base.nodes[0], size: [300, 150] }],
  };
  const moved = {
    ...resized,
    nodes: [{ ...resized.nodes[0], pos: [3, 4] }],
  };
  assert.equal(workflowRevisionHash(base), workflowRevisionHash(resized));
  assert.notEqual(workflowRevisionHash(base), workflowRevisionHash(moved));
});

test("highlight duration is always visible for at least one second", () => {
  assert.equal(clampHighlightDuration(20), 1000);
  assert.equal(clampHighlightDuration(2500), 2500);
  assert.equal(clampHighlightDuration(999999), 60000);
});

test("history console drag stays inside the viewport", () => {
  assert.deepEqual(clampConsolePosition(-20, 900, 360, 36, 800, 600), { left: 0, top: 564 });
  assert.deepEqual(clampConsolePosition(900, -20, 360, 36, 800, 600), { left: 440, top: 0 });
  assert.deepEqual(clampConsolePosition(20, 20, 360, 36, 300, 20), { left: 0, top: 0 });
});

test("graph coordinates map through LiteGraph offset then scale", () => {
  assert.deepEqual(
    graphRectToScreen([120, 120], [270, 144], 0.5, [280, 350]),
    { x: 200, y: 235, width: 135, height: 72 },
  );
});

test("highlight uses the rendered node bounds including its title", () => {
  const node = {
    pos: [120, 120],
    size: [270, 144],
    getBounding: () => new Float32Array([120, 90, 270, 174]),
  };
  assert.deepEqual(
    nodeRectToScreen(node, 0.5, [280, 350]),
    { x: 200, y: 220, width: 135, height: 87 },
  );
});

test("node highlight moves one purple tracer around the node with a soft glow", () => {
  const original = {
    document: globalThis.document,
    HTMLCanvasElement: globalThis.HTMLCanvasElement,
    requestAnimationFrame: globalThis.requestAnimationFrame,
    devicePixelRatio: globalThis.devicePixelRatio,
    now: Date.now,
  };
  let frame;
  const strokes = [];
  const context = {
    lineDash: [],
    setTransform() {}, clearRect() {}, save() {}, restore() {}, beginPath() {},
    moveTo() {}, lineTo() {}, quadraticCurveTo() {}, closePath() {},
    setLineDash(value) { this.lineDash = [...value]; },
    stroke() {
      strokes.push({
        lineDash: [...this.lineDash],
        lineDashOffset: this.lineDashOffset,
        strokeStyle: this.strokeStyle,
        shadowColor: this.shadowColor,
        shadowBlur: this.shadowBlur,
      });
    },
  };
  class FakeCanvas {
    constructor() { this.isConnected = true; this.style = {}; }
    setAttribute() {}
    getContext() { return context; }
  }
  const canvas = new FakeCanvas();
  globalThis.HTMLCanvasElement = FakeCanvas;
  globalThis.document = {
    getElementById: () => null,
    createElement: () => canvas,
    body: { appendChild() {} },
  };
  globalThis.requestAnimationFrame = (callback) => { frame = callback; return 1; };
  globalThis.devicePixelRatio = 1;
  let now = 1000;
  Date.now = () => now;
  try {
    const node = { id: 3, getBounding: () => [10, 20, 100, 60] };
    const highlighter = createNodeHighlighter({
      canvas: {
        canvas: { getBoundingClientRect: () => ({ left: 100, top: 100, width: 800, height: 600 }) },
        ds: { scale: 1, offset: [0, 0] },
      },
      graph: { getNodeById: () => node },
    });
    highlighter.set([3], 5000);
    now = 2000;
    frame();

    const [tracer] = strokes.filter((stroke) => stroke.lineDash.length === 2);
    assert.equal(strokes[0].shadowBlur, 9);
    assert.ok(tracer);
    assert.ok(tracer.lineDash[0] > 0);
    assert.ok(tracer.lineDash[1] > tracer.lineDash[0]);
    assert.ok(tracer.lineDashOffset < 0);
    assert.equal(tracer.shadowBlur, 14);
    assert.match(tracer.strokeStyle, /192, 132, 252/);
    assert.match(tracer.shadowColor, /168, 85, 247/);
    assert.doesNotMatch(tracer.strokeStyle, /255, 255, 255|white/i);
  } finally {
    Date.now = original.now;
    for (const [key, value] of Object.entries(original)) {
      if (key === "now") continue;
      if (value === undefined) delete globalThis[key];
      else globalThis[key] = value;
    }
  }
});

test("node history can undo A and B independently while preserving unrelated edits", () => {
  const baseline = {
    nodes: [
      { id: 1, type: "A", widgets_values: ["a0"], inputs: [], outputs: [] },
      { id: 2, type: "B", widgets_values: ["b0"], inputs: [], outputs: [] },
    ],
    links: [],
  };
  const afterAi = structuredClone(baseline);
  afterAi.nodes[0].widgets_values[0] = "a1";
  afterAi.nodes[1].widgets_values[0] = "b1";
  const changeA = captureNodeChange(baseline, afterAi, 1);
  const changeB = captureNodeChange(baseline, afterAi, 2);

  const afterUndoA = restoreNodeChange(afterAi, changeA);
  assert.equal(afterUndoA.nodes[0].widgets_values[0], "a0");
  assert.equal(afterUndoA.nodes[1].widgets_values[0], "b1");
  const afterUndoB = restoreNodeChange(afterUndoA, changeB);
  assert.deepEqual(afterUndoB.nodes.map((node) => node.widgets_values[0]), ["a0", "b0"]);

  const independentEdit = structuredClone(afterAi);
  independentEdit.nodes[1].widgets_values[0] = "b-manual";
  const preserved = restoreNodeChange(independentEdit, changeA);
  assert.equal(preserved.nodes[0].widgets_values[0], "a0");
  assert.equal(preserved.nodes[1].widgets_values[0], "b-manual");
});

test("node history refuses to overwrite a later manual edit to that node", () => {
  const before = { nodes: [{ id: 1, type: "A", widgets_values: ["before"] }], links: [] };
  const after = structuredClone(before);
  after.nodes[0].widgets_values[0] = "ai";
  const change = captureNodeChange(before, after, 1);
  const manuallyEdited = structuredClone(after);
  manuallyEdited.nodes[0].widgets_values[0] = "manual";
  assert.throws(
    () => restoreNodeChange(manuallyEdited, change),
    (error) => error.code === "history_conflict",
  );
});

test("undoing a node connection restores only that edge and peer slot", () => {
  const before = {
    nodes: [
      { id: 1, type: "A", inputs: [], outputs: [{ links: null }] },
      { id: 2, type: "B", inputs: [{ link: null }], outputs: [] },
    ],
    links: [],
  };
  const after = structuredClone(before);
  after.nodes[0].outputs[0].links = [3];
  after.nodes[1].inputs[0].link = 3;
  after.links = [[3, 1, 0, 2, 0, "FLOAT"]];
  const changeA = captureNodeChange(before, after, 1);
  const restored = restoreNodeChange(after, changeA);
  assert.deepEqual(restored.links, []);
  assert.equal(restored.nodes[1].inputs[0].link, null);
  assert.equal(restored.nodes[0].outputs[0].links, null);
});

test("undoing a replacement connection restores the displaced third-party edge", () => {
  const before = {
    nodes: [
      { id: 1, type: "NewOrigin", inputs: [], outputs: [{ links: null }] },
      { id: 2, type: "Target", inputs: [{ link: 7 }], outputs: [] },
      { id: 3, type: "OldOrigin", inputs: [], outputs: [{ links: [7] }] },
      { id: 4, type: "Other", widgets_values: ["untouched"], inputs: [], outputs: [] },
    ],
    links: [[7, 3, 0, 2, 0, "FLOAT"]],
  };
  const after = structuredClone(before);
  after.nodes[0].outputs[0].links = [8];
  after.nodes[1].inputs[0].link = 8;
  after.nodes[2].outputs[0].links = null;
  after.links = [[8, 1, 0, 2, 0, "FLOAT"]];
  const change = captureNodeChange(before, after, 1, [7]);
  const restored = restoreNodeChange(after, change);
  assert.deepEqual(restored.nodes, before.nodes);
  assert.deepEqual(restored.links, before.links);
});

test("node history restores removed nodes and their links", () => {
  const before = {
    nodes: [
      { id: 1, type: "A", outputs: [{ links: [3] }] },
      { id: 2, type: "B", inputs: [{ link: 3 }] },
    ],
    links: [[3, 1, 0, 2, 0, "FLOAT"]],
  };
  const after = {
    nodes: [{ id: 2, type: "B", inputs: [{ link: null }] }],
    links: [],
  };
  const change = captureNodeChange(before, after, 1);
  const restored = restoreNodeChange(after, change);
  assert.deepEqual(restored.nodes.map((node) => node.id), [1, 2]);
  assert.deepEqual(restored.links, before.links);
  assert.equal(restored.nodes[1].inputs[0].link, 3);
});

test("node history removes a node created by the recorded change", () => {
  const before = { nodes: [{ id: 2, type: "B" }], links: [] };
  const after = { nodes: [{ id: 1, type: "A" }, { id: 2, type: "B" }], links: [] };
  const change = captureNodeChange(before, after, 1);
  const restored = restoreNodeChange(after, change);
  assert.deepEqual(restored.nodes.map((node) => node.id), [2]);
});

test("widget undo keeps unchanged connections instead of rebuilding them", () => {
  const before = {
    nodes: [
      { id: 1, type: "A", widgets_values: ["old"], inputs: [], outputs: [{ links: [3] }] },
      { id: 2, type: "B", inputs: [{ link: 3 }], outputs: [] },
    ],
    links: [[3, 1, 0, 2, 0, "FLOAT"]],
  };
  const after = structuredClone(before);
  after.nodes[0].widgets_values[0] = "new";
  const change = captureNodeChange(before, after, 1);
  assert.deepEqual(nodeLinkDelta(change), { remove: [], add: [] });
});

test("connected sibling history rebases after the other endpoint is undone", () => {
  const before = {
    nodes: [
      { id: 1, type: "A", outputs: [{ links: null }] },
      { id: 2, type: "B", widgets_values: ["b0"], inputs: [{ link: null }] },
    ],
    links: [],
  };
  const after = structuredClone(before);
  after.nodes[1].widgets_values[0] = "b1";
  after.nodes[0].outputs[0].links = [7];
  after.nodes[1].inputs[0].link = 7;
  after.links = [[7, 1, 0, 2, 0, "FLOAT"]];
  const changeA = captureNodeChange(before, after, 1);
  const changeB = captureNodeChange(before, after, 2);
  const afterUndoA = restoreNodeChange(after, changeA);
  changeB.after = snapshotNodeState(afterUndoA, 2);
  assert.equal(nodeChangeStateMatches(afterUndoA, changeB, "after"), true);
  assert.equal(nodeChangeStateMatches(afterUndoA, changeB, "before"), false);
  const afterUndoB = restoreNodeChange(afterUndoA, changeB);
  assert.deepEqual(afterUndoB.nodes[1].widgets_values, ["b0"]);
  assert.deepEqual(afterUndoB.links, []);
});

test("undo boundaries notify the frontend change tracker", () => {
  const calls = [];
  const canvas = {
    emitBeforeChange: () => calls.push("before"),
    emitAfterChange: () => calls.push("after"),
  };
  assert.equal(emitCanvasChangeBoundary(canvas, "before"), "method");
  assert.equal(emitCanvasChangeBoundary(canvas, "after"), "method");
  assert.deepEqual(calls, ["before", "after"]);
});

test("undo boundaries fall back to the litegraph canvas event", () => {
  const events = [];
  class FakeEvent {
    constructor(type, options) {
      this.type = type;
      this.detail = options.detail;
    }
  }
  const canvas = { canvas: { dispatchEvent: (event) => events.push(event) } };
  assert.equal(emitCanvasChangeBoundary(canvas, "before", FakeEvent), "event");
  assert.equal(events[0].type, "litegraph:canvas");
  assert.deepEqual(events[0].detail, { subType: "before-change" });
});

test("default node placement starts after the rightmost node", () => {
  const graph = {
    _nodes: [
      { pos: [20, 40], size: [100, 60] },
      { pos: [200, 50], size: [240, 120] },
    ],
  };
  assert.deepEqual(defaultNodePosition(graph), [520, 50]);
});

test("add_node registers a transaction ref and initializes widgets", () => {
  const originalLiteGraph = globalThis.LiteGraph;
  const added = [];
  const node = {
    id: 7,
    type: "Example",
    pos: [0, 0],
    widgets: [{ name: "value", value: 1 }],
  };
  globalThis.LiteGraph = { createNode: () => node };
  try {
    const graph = {
      _nodes: [],
      add(value) {
        added.push(value);
      },
    };
    const refs = new Map();
    const affected = new Set();
    const result = applyOperation(
      { canvas: {} },
      graph,
      {
        op: "add_node",
        class_type: "Example",
        ref: "new",
        pos: [10, 20],
        widgets: { value: 9 },
      },
      refs,
      affected,
    );
    assert.equal(result.node_id, 7);
    assert.deepEqual(node.pos, [10, 20]);
    assert.equal(node.widgets[0].value, 9);
    assert.equal(refs.get("new"), 7);
    assert.deepEqual([...affected], [7]);
    assert.deepEqual(added, [node]);
  } finally {
    globalThis.LiteGraph = originalLiteGraph;
  }
});

test("remove_node is recorded as affecting the removed node", () => {
  const removed = [];
  const node = { id: 8, type: "Example" };
  const graph = {
    getNodeById: (id) => id === 8 ? node : null,
    remove: (value) => removed.push(value),
  };
  const affected = new Set();
  applyOperation({}, graph, { op: "remove_node", node_id: 8 }, new Map(), affected);
  assert.deepEqual([...affected], [8]);
  assert.deepEqual(removed, [node]);
});

test("connect history records both endpoint nodes", () => {
  const origin = { id: 1, outputs: [{ name: "out", type: "FLOAT" }], connect: () => 3 };
  const target = { id: 2, inputs: [{ name: "in", type: "FLOAT" }] };
  const graph = { getNodeById: (id) => id === 1 ? origin : id === 2 ? target : null };
  const affected = new Set();
  const result = applyOperation(
    {}, graph, { op: "connect", from_node_id: 1, to_node_id: 2, from_output: 0, to_input: 0 }, new Map(), affected,
  );
  assert.deepEqual([...affected], [1, 2]);
  assert.deepEqual(result.from, { node_id: 1, output: "out" });
  assert.deepEqual(result.to, { node_id: 2, input: "in" });
});
