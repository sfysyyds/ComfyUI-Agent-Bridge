import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import {
  clampConsolePosition,
  jsonSafe,
  operationNodeRefs,
  captureNodeChange,
  nodeChangeStateMatches,
  nodeLinkDelta,
  emitCanvasChangeBoundary,
  restoreNodeChange,
  snapshotNodeState,
  workflowIdentity,
  workflowRevisionHash,
} from "./agent_bridge_core.js";
import {
  applyOperations,
  focusNodes,
} from "./agent_bridge_graph.js";
import { createNodeHighlighter } from "./agent_bridge_highlight.js";

const API_PREFIX = "/comfy-agent-bridge/v1";
const COMMAND_EVENT = "comfy.agent_bridge.command";
const PROTOCOL = "comfy-agent-bridge/v1";
const SESSION_KEY = "comfy-agent-bridge.session-id";
const SYNC_INTERVAL_MS = 300;
const HEARTBEAT_INTERVAL_MS = 5000;
const COMMAND_CACHE_LIMIT = 128;
const HISTORY_LIMIT = 100;

const modificationHistory = [];
const historyPending = new Set();
let modificationCount = 0;

class BridgeCommandError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "BridgeCommandError";
    this.code = code;
  }
}

const state = {
  sessionId: getSessionId(),
  revision: 0,
  lastSnapshotKey: null,
  lastSyncAt: 0,
  lastUserActivityAt: Date.now(),
  connected: false,
  commandRunning: false,
  commandQueue: Promise.resolve(),
  syncPromise: null,
  commandCache: new Map(),
  statusElement: null,
  historyConsole: null,
};
const highlighter = createNodeHighlighter(app);

function getSessionId() {
  let value = sessionStorage.getItem(SESSION_KEY);
  if (!value) {
    value = globalThis.crypto?.randomUUID?.() ??
      `cab-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    sessionStorage.setItem(SESSION_KEY, value);
  }
  return value;
}

function clientId() {
  return api.clientId || sessionStorage.getItem("clientId") || "";
}

function visibleWorkflowTitle() {
  const label = document.querySelector(
    'button[aria-pressed="true"] .workflow-tab .workflow-label',
  );
  const value = label?.textContent?.trim();
  return value || null;
}

function workflowTitle() {
  return visibleWorkflowTitle() ||
    app.extensionManager?.workflow?.activeWorkflow?.filename ||
    app.extensionManager?.workflow?.activeWorkflow?.path ||
    document.title ||
    "Untitled workflow";
}

function noteUserActivity() {
  state.lastUserActivityAt = Date.now();
}

async function requestJson(path, options = {}) {
  const request = {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers ?? {}),
    },
  };
  const response = typeof api.fetchApi === "function"
    ? await api.fetchApi(path, request)
    : await fetch(path, request);
  const text = await response.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new BridgeCommandError("invalid_server_response", `${response.status} ${response.statusText}`);
  }
  if (!response.ok) {
    const detail = data?.error;
    throw new BridgeCommandError(
      detail?.code || `http_${response.status}`,
      detail?.message || `${response.status} ${response.statusText}`,
    );
  }
  return data;
}

function createStatusElement() {
  if (state.statusElement) return state.statusElement;
  if (!document.querySelector('link[data-comfy-agent-bridge="styles"]')) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.dataset.comfyAgentBridge = "styles";
    link.href = new URL("./agent_bridge.css", import.meta.url).href;
    document.head.appendChild(link);
  }
  const element = document.getElementById("comfy-agent-bridge-status") ??
    document.createElement("div");
  element.id = "comfy-agent-bridge-status";
  element.dataset.state = "connecting";
  element.innerHTML = '<span class="cab-dot"></span><span class="cab-label">Agent Bridge 正在连接…</span>';
  element.title = "ComfyUI Agent Bridge：把当前浏览器画布实时同步给 MCP 智能体";
  if (!element.isConnected) document.body.appendChild(element);
  state.statusElement = element;
  return element;
}

function createHistoryConsole() {
  if (state.historyConsole?.isConnected) return state.historyConsole;
  const consoleElement = document.getElementById("comfy-agent-bridge-console") ??
    document.createElement("div");
  consoleElement.id = "comfy-agent-bridge-console";
  consoleElement.innerHTML = `
    <div class="cab-history-bar">
      <button class="cab-history-drag" type="button" aria-label="拖动修改历史面板，或用方向键移动" title="拖动修改历史面板">⋮⋮</button>
      <button class="cab-history-toggle" type="button" aria-expanded="false" aria-controls="cab-history-panel">
        <span class="cab-console-status-slot"></span><span class="cab-history-count">修改次数 <b>0</b></span><span class="cab-history-chevron" aria-hidden="true">⌃</span>
      </button>
    </div>
    <section class="cab-history-panel" id="cab-history-panel" aria-label="AI 修改历史" hidden>
      <header class="cab-history-header"><strong>修改历史</strong><span>单独撤回节点</span></header>
      <ol class="cab-history-list"></ol>
      <p class="cab-history-feedback" role="status" aria-live="polite"></p>
    </section>`;
  if (!consoleElement.isConnected) document.body.appendChild(consoleElement);
  const status = state.statusElement;
  if (status) {
    status.classList.add("cab-status-inline");
    consoleElement.querySelector(".cab-console-status-slot").appendChild(status);
  }
  const toggle = consoleElement.querySelector(".cab-history-toggle");
  const panel = consoleElement.querySelector(".cab-history-panel");
  const grip = consoleElement.querySelector(".cab-history-drag");
  const positionPanel = () => {
    if (panel.hidden) return;
    panel.style.maxHeight = "";
    const rect = consoleElement.getBoundingClientRect();
    const above = Math.max(0, rect.top - 8);
    const below = Math.max(0, window.innerHeight - rect.bottom - 8);
    const showBelow = above < panel.getBoundingClientRect().height && below > above;
    panel.classList.toggle("cab-history-panel-below", showBelow);
    panel.style.maxHeight = `${showBelow ? below : above}px`;
  };
  const moveTo = (left, top) => {
    const rect = consoleElement.getBoundingClientRect();
    const position = clampConsolePosition(left, top, rect.width, rect.height, window.innerWidth, window.innerHeight);
    Object.assign(consoleElement.style, {
      left: `${position.left}px`, top: `${position.top}px`, right: "auto", bottom: "auto",
    });
    positionPanel();
  };
  let drag = null;
  grip.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const rect = consoleElement.getBoundingClientRect();
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top };
    grip.setPointerCapture(event.pointerId);
  });
  grip.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    event.stopPropagation();
    moveTo(drag.left + event.clientX - drag.x, drag.top + event.clientY - drag.y);
  });
  for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) {
    grip.addEventListener(name, (event) => {
      if (drag?.id !== event.pointerId) return;
      event.stopPropagation();
      drag = null;
    });
  }
  grip.addEventListener("keydown", (event) => {
    const delta = { ArrowLeft: [-20, 0], ArrowRight: [20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] }[event.key];
    if (!delta) return;
    event.preventDefault();
    const rect = consoleElement.getBoundingClientRect();
    moveTo(rect.left + delta[0], rect.top + delta[1]);
  });
  window.addEventListener("resize", () => {
    if (consoleElement.style.left) {
      const rect = consoleElement.getBoundingClientRect();
      moveTo(rect.left, rect.top);
    } else positionPanel();
  });
  toggle.addEventListener("click", () => {
    const expanded = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", String(!expanded));
    panel.hidden = expanded;
    positionPanel();
  });
  consoleElement.querySelector(".cab-history-list").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-history-index]");
    if (!button) return;
    const entry = modificationHistory[Number(button.dataset.historyIndex)];
    if (entry) void undoHistoryEntry(entry);
  });
  state.historyConsole = consoleElement;
  renderHistoryConsole();
  return consoleElement;
}

function renderHistoryConsole() {
  const element = state.historyConsole;
  if (!element?.isConnected) return;
  element.querySelector(".cab-history-count b").textContent = String(modificationCount);
  const list = element.querySelector(".cab-history-list");
  list.replaceChildren();
  if (!modificationHistory.length) {
    const empty = document.createElement("li");
    empty.className = "cab-history-empty";
    empty.textContent = "暂无 AI 修改";
    list.appendChild(empty);
    return;
  }
  modificationHistory.forEach((entry, index) => {
    const item = document.createElement("li");
    item.className = "cab-history-item";
    const title = document.createElement("strong");
    title.textContent = `修改了 ${entry.nodeTitle} 节点`;
    const detail = document.createElement("span");
    detail.className = "cab-history-detail";
    detail.textContent = `#${entry.nodeId} · ${entry.action} · ${entry.time}${entry.stale ? " · 状态已变更" : ""}`;
    const undo = document.createElement("button");
    undo.type = "button";
    undo.className = "cab-history-undo";
    undo.dataset.historyIndex = String(index);
    undo.disabled = entry.undone || entry.stale || historyPending.has(entry);
    undo.textContent = entry.undone
      ? entry.undoneBySibling ? "连带撤回" : "已撤回"
      : entry.stale ? "已变更" : historyPending.has(entry) ? "撤回中…" : "撤回";
    item.append(title, detail, undo);
    list.appendChild(item);
  });
}

function nodeFromWorkflow(workflow, nodeId) {
  return (workflow?.nodes ?? []).find((node) => String(node?.id) === String(nodeId)) ?? null;
}

function describeOperation(result, nodeId) {
  const labels = {
    add_node: "新增",
    remove_node: "删除",
    set_widget: "修改参数",
    connect: "修改连线",
    disconnect: "修改连线",
    move_node: "移动",
    set_title: "修改标题",
    set_mode: "修改模式",
  };
  const matching = (result.operations ?? []).find((operation) => {
    const ids = [operation.node_id, operation.from?.node_id, operation.to?.node_id];
    return ids.some((id) => id != null && String(id) === String(nodeId));
  });
  return labels[matching?.op] ?? "修改";
}

function recordNodeHistory(commandId, beforeWorkflow, afterWorkflow, result) {
  const now = new Date();
  const time = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  for (const nodeId of result.affected_node_ids ?? []) {
    const displacedLinks = (result.operations ?? [])
      .filter((operation) => operation.op === "connect" && String(operation.from?.node_id) === String(nodeId))
      .map((operation) => operation.replaced_link_id)
      .filter((id) => id != null);
    const change = captureNodeChange(beforeWorkflow, afterWorkflow, nodeId, displacedLinks);
    if (!change) continue;
    const node = change.after.node ?? change.before.node;
    const entry = {
      historyId: String(commandId),
      workflowId: workflowIdentity(afterWorkflow, workflowTitle()),
      nodeId,
      nodeTitle: node?.title || node?.type || `节点 ${nodeId}`,
      nodeType: node?.type ?? "",
      action: describeOperation(result, nodeId),
      time,
      change,
      undone: false,
    };
    modificationHistory.unshift(entry);
  }
  modificationCount += Math.max(0, Number(result.operation_count) || 0);
  if (modificationHistory.length > HISTORY_LIMIT) modificationHistory.length = HISTORY_LIMIT;
  renderHistoryConsole();
}

function rebaseRelatedHistory(entry, beforeWorkflow, afterWorkflow) {
  for (const sibling of modificationHistory) {
    if (sibling === entry || sibling.historyId !== entry.historyId || sibling.undone) continue;
    if (!nodeChangeStateMatches(beforeWorkflow, sibling.change, "after")) {
      sibling.stale = true;
      continue;
    }
    sibling.change.after = snapshotNodeState(afterWorkflow, sibling.nodeId, sibling.change.linkIds);
    if (nodeChangeStateMatches(afterWorkflow, sibling.change, "before")) {
      sibling.undone = true;
      sibling.undoneBySibling = true;
    }
  }
  renderHistoryConsole();
}

async function undoHistoryEntry(entry) {
  if (entry.undone || historyPending.has(entry)) return;
  historyPending.add(entry);
  const feedback = state.historyConsole?.querySelector(".cab-history-feedback");
  if (feedback) feedback.textContent = "";
  renderHistoryConsole();
  try {
    const response = await requestJson(`${API_PREFIX}/command`, {
      method: "POST",
      body: JSON.stringify({
        action: "undo_node",
        session_id: state.sessionId,
        base_revision: state.revision,
        payload: { history_id: entry.historyId, node_id: entry.nodeId },
      }),
    });
    if (!response.result?.undone) throw new BridgeCommandError("history_restore_failed", "Browser did not confirm the node undo");
    entry.undone = true;
    state.revision = response.revision;
    state.connected = true;
    const message = response.result.sync_pending
      ? `已本地撤回 ${entry.nodeTitle}；同步待重试`
      : `已撤回 ${entry.nodeTitle}`;
    if (feedback) feedback.textContent = message;
    setStatus(response.result.sync_pending ? "error" : "online", `Agent Bridge · r${state.revision}`, message);
  } catch (error) {
    if (entry.undone) {
      const message = `本地已撤回 ${entry.nodeTitle}；服务器确认待重试`;
      if (feedback) feedback.textContent = message;
      setStatus("error", `Agent Bridge · ${message}`, error.message);
      void syncSnapshot(true, "undo-retry", entry.historyId).catch((retryError) => {
        console.error("[Comfy Agent Bridge] Pending undo snapshot retry failed", retryError);
      });
    } else {
      if (feedback) feedback.textContent = `撤回失败：${error.message}`;
    }
  } finally {
    historyPending.delete(entry);
    renderHistoryConsole();
  }
}

function setStatus(status, label, title) {
  const element = createStatusElement();
  element.dataset.state = status;
  element.querySelector(".cab-label").textContent = label;
  if (title) element.title = title;
}

function selectedNodeIds() {
  const canvas = app.canvas;
  if (!canvas) return [];
  const values = [];
  const selectedItems = canvas.selectedItems;
  if (selectedItems && typeof selectedItems[Symbol.iterator] === "function") {
    for (const item of selectedItems) {
      if (item?.id != null && item?.type) values.push(item.id);
    }
  } else {
    for (const node of Object.values(canvas.selected_nodes ?? {})) {
      if (node?.id != null) values.push(node.id);
    }
  }
  return [...new Set(values)];
}

function viewportState() {
  const canvas = app.canvas;
  if (!canvas?.canvas) return {};
  const scale = Number(canvas.ds?.scale ?? 1) || 1;
  const offset = canvas.ds?.offset ?? [0, 0];
  const rect = canvas.canvas.getBoundingClientRect();
  const width = rect.width / scale;
  const height = rect.height / scale;
  return {
    x: -Number(offset[0] ?? 0),
    y: -Number(offset[1] ?? 0),
    width,
    height,
    zoom: scale,
  };
}

function liveNodeSummary(node) {
  return {
    id: node.id,
    type: node.type,
    comfy_class: node.comfyClass ?? node.type,
    title: node.title ?? node.type,
    pos: [Number(node.pos?.[0] ?? 0), Number(node.pos?.[1] ?? 0)],
    size: [Number(node.size?.[0] ?? 0), Number(node.size?.[1] ?? 0)],
    mode: Number(node.mode ?? 0),
    flags: jsonSafe(node.flags ?? {}),
    widgets: (node.widgets ?? []).map((widget, index) => ({
      index,
      name: widget?.name ?? `widget_${index}`,
      type: widget?.type ?? null,
      value: jsonSafe(widget?.value),
      options: jsonSafe(widget?.options ?? {}),
    })),
    inputs: (node.inputs ?? []).map((input, index) => ({
      index,
      name: input?.name ?? `input_${index}`,
      type: input?.type ?? null,
      link: input?.link ?? null,
      shape: input?.shape ?? null,
    })),
    outputs: (node.outputs ?? []).map((output, index) => ({
      index,
      name: output?.name ?? `output_${index}`,
      type: output?.type ?? null,
      links: Array.isArray(output?.links) ? [...output.links] : [],
      shape: output?.shape ?? null,
    })),
    properties: jsonSafe(node.properties ?? {}),
  };
}

function buildView(workflow) {
  const graph = app.graph;
  return {
    nodes: [...(graph?._nodes ?? [])].map(liveNodeSummary),
    links: jsonSafe(workflow.links ?? []),
    groups: jsonSafe(workflow.groups ?? []),
    selection: selectedNodeIds(),
    viewport: viewportState(),
  };
}

function snapshotData(origin, commandId = null) {
  if (!app.graph) throw new BridgeCommandError("graph_unavailable", "ComfyUI graph is unavailable");
  const workflow = app.graph.serialize();
  const title = workflowTitle();
  return {
    payload: {
      session_id: state.sessionId,
      client_id: clientId(),
      workflow_id: workflowIdentity(workflow, title),
      title,
      workflow,
      view: buildView(workflow),
      hash: workflowRevisionHash(workflow),
      activity_at_ms: state.lastUserActivityAt,
      origin,
      command_id: commandId,
      visible: document.visibilityState === "visible",
      focused: document.hasFocus(),
    },
  };
}

async function sendSnapshot(force = false, origin = "user", commandId = null) {
  const id = clientId();
  if (!id || !app.graph) return null;
  const { payload } = snapshotData(origin, commandId);
  const snapshotKey = `${payload.workflow_id}\0${payload.title}\0${payload.hash}`;
  if (!force && snapshotKey === state.lastSnapshotKey) return null;
  const response = await requestJson(`${API_PREFIX}/snapshot`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.lastSnapshotKey = snapshotKey;
  state.revision = response.revision;
  state.lastSyncAt = Date.now();
  state.connected = true;
  if (!state.commandRunning) {
    setStatus(
      "online",
      `Agent Bridge · r${state.revision}`,
      `${payload.title}\n已同步 ${payload.view.nodes.length} 个节点\nrevision ${state.revision}`,
    );
  }
  return response;
}

function syncSnapshot(force = false, origin = "user", commandId = null) {
  if (state.syncPromise && !force) return state.syncPromise;
  const previous = state.syncPromise;
  const current = (previous ? previous.catch(() => undefined) : Promise.resolve())
    .then(() => sendSnapshot(force, origin, commandId));
  state.syncPromise = current;
  const clear = () => {
    if (state.syncPromise === current) state.syncPromise = null;
  };
  void current.then(clear, clear);
  return current;
}

async function heartbeat() {
  if (!clientId()) return;
  try {
    const workflow = app.graph?.serialize?.() ?? {};
    const title = workflowTitle();
    const response = await requestJson(`${API_PREFIX}/heartbeat`, {
      method: "POST",
      body: JSON.stringify({
        session_id: state.sessionId,
        client_id: clientId(),
        workflow_id: workflowIdentity(workflow, title),
        title,
        activity_at_ms: state.lastUserActivityAt,
        visible: document.visibilityState === "visible",
        focused: document.hasFocus(),
        active: document.hasFocus(),
      }),
    });
    state.revision = response.revision;
    state.connected = true;
  } catch (error) {
    if (error.code === "session_not_found") {
      await syncSnapshot(true, "heartbeat");
      return;
    }
    throw error;
  }
}

async function queuePrompt(payload) {
  const count = Math.max(1, Math.min(Number(payload.count) || 1, 16));
  const promptIds = [];
  const responses = [];
  for (let index = 0; index < count; index += 1) {
    const prompt = await app.graphToPrompt();
    const options = {};
    if (Array.isArray(payload.node_ids) && payload.node_ids.length) {
      options.partialExecutionTargets = payload.node_ids;
    }
    const response = await api.queuePrompt(payload.front ? -1 : 0, prompt, options);
    responses.push(jsonSafe(response));
    if (response?.prompt_id) promptIds.push(response.prompt_id);
  }
  return { queued: responses.length, prompt_ids: promptIds, responses };
}

function serializedLinkFields(link) {
  return Array.isArray(link)
    ? { id: link[0], from: link[1], fromSlot: link[2], to: link[3], toSlot: link[4] }
    : {
        id: link?.id,
        from: link?.origin_id ?? link?.originId ?? link?.from_node_id,
        fromSlot: link?.origin_slot ?? link?.originSlot ?? link?.from_slot,
        to: link?.target_id ?? link?.targetId ?? link?.to_node_id,
        toSlot: link?.target_slot ?? link?.targetSlot ?? link?.to_slot,
      };
}

function clearRuntimeLinkSlots(origin, outputSlot, target, inputSlot, linkId) {
  const output = origin.outputs?.[outputSlot];
  if (Array.isArray(output?.links)) {
    output.links = output.links.filter((id) => String(id) !== String(linkId));
  } else if (output) {
    output.links = [];
  }
  const input = target.inputs?.[inputSlot];
  if (input && String(input.link) === String(linkId)) input.link = null;
}

function restoreLinkId(graph, created, expectedId, origin, outputSlot, target, inputSlot) {
  const createdId = typeof created === "object" ? created?.id : created;
  if (createdId == null) throw new BridgeCommandError("history_restore_failed", `Restored link ${expectedId} has no id`);
  if (String(createdId) === String(expectedId)) return;
  const link = graph.links?.get?.(createdId) ?? graph.links?.[createdId] ?? graph.links?.[String(createdId)];
  if (!link) throw new BridgeCommandError("history_restore_failed", `Restored link ${createdId} could not be found`);
  if (typeof graph.links.delete === "function") graph.links.delete(createdId);
  else {
    delete graph.links[createdId];
    delete graph.links[String(createdId)];
  }
  link.id = expectedId;
  if (typeof graph.links.set === "function") graph.links.set(expectedId, link);
  else graph.links[expectedId] = link;
  const output = origin.outputs?.[outputSlot];
  if (Array.isArray(output?.links)) {
    output.links = output.links.map((id) => String(id) === String(createdId) ? expectedId : id);
  }
  if (target.inputs?.[inputSlot]) target.inputs[inputSlot].link = expectedId;
}

async function applyNodeHistoryChange(entry) {
  const graph = app.graph;
  const currentWorkflow = graph.serialize();
  restoreNodeChange(currentWorkflow, entry.change);
  const backup = JSON.parse(JSON.stringify(currentWorkflow));
  const activeWorkflow = app.extensionManager?.workflow?.activeWorkflow ?? null;
  const linkDelta = nodeLinkDelta(entry.change);
  let graphBoundaryOpen = false;
  let canvasBoundaryOpen = false;
  try {
    graph.beforeChange?.();
    graphBoundaryOpen = true;
    emitCanvasChangeBoundary(app.canvas, "before", CustomEvent);
    canvasBoundaryOpen = true;
    for (const serializedLink of linkDelta.remove) {
      const { id, to, toSlot } = serializedLinkFields(serializedLink);
      if (typeof graph.removeLink === "function") graph.removeLink(id);
      else graph.getNodeById(to)?.disconnectInput?.(toSlot);
    }

    let node = graph.getNodeById(entry.nodeId) ??
      (/^-?\d+$/.test(String(entry.nodeId)) ? graph.getNodeById(Number(entry.nodeId)) : null);
    if (entry.change.before.node == null) {
      if (node) graph.remove(node);
      node = null;
    } else if (node) {
      node.configure(entry.change.before.node);
    } else {
      node = globalThis.LiteGraph?.createNode?.(entry.change.before.node.type);
      if (!node) throw new BridgeCommandError("history_restore_failed", `Cannot recreate node ${entry.nodeId}`);
      node.configure(entry.change.before.node);
      graph.add(node);
      if (Array.isArray(graph._nodes)) {
        const currentIndex = graph._nodes.indexOf(node);
        if (currentIndex >= 0) {
          graph._nodes.splice(currentIndex, 1);
          graph._nodes.splice(Math.max(0, Math.min(entry.change.before.index, graph._nodes.length)), 0, node);
        }
      }
    }

    for (const serializedLink of linkDelta.add) {
      const { id, from, fromSlot, to, toSlot } = serializedLinkFields(serializedLink);
      const origin = graph.getNodeById(from);
      const target = graph.getNodeById(to);
      if (!origin || !target) {
        throw new BridgeCommandError("history_conflict", `Connected node ${!origin ? from : to} no longer exists`);
      }
      clearRuntimeLinkSlots(origin, fromSlot, target, toSlot, id);
      const created = origin.connect(fromSlot, target, toSlot);
      if (!created) throw new BridgeCommandError("history_restore_failed", `Could not restore link ${id}`);
      restoreLinkId(graph, created, id, origin, fromSlot, target, toSlot);
    }
    if (!nodeChangeStateMatches(graph.serialize(), entry.change, "before")) {
      throw new BridgeCommandError("history_restore_failed", `Node ${entry.nodeId} did not match its restored state`);
    }
  } catch (error) {
    try {
      await app.loadGraphData(backup, false, false, activeWorkflow, {
        checkForRerouteMigration: false,
        skipAssetScans: true,
        silentAssetErrors: true,
      });
    } catch (restoreError) {
      throw new BridgeCommandError(
        "history_restore_failed",
        `${error.message}; restoring the graph after undo failure also failed: ${restoreError.message}`,
      );
    }
    throw error;
  } finally {
    if (graphBoundaryOpen) graph.afterChange?.();
    if (canvasBoundaryOpen) emitCanvasChangeBoundary(app.canvas, "after", CustomEvent);
    graph.setDirtyCanvas?.(true, true);
  }
}

async function executeCommand(command) {
  if (!command || command.protocol !== PROTOCOL || command.session_id !== state.sessionId) return;
  const commandStartedAt = Date.now();
  state.commandRunning = true;
  setStatus("working", "Agent Bridge · AI 修改中…", `${command.action}\ncommand ${command.command_id}`);
  let result;
  let revision = state.revision;
  let mutationApplied = false;
  let syncPendingError = null;
  let currentWorkflowId = null;
  let pendingNotice = null;
  let confirmationPending = false;
  try {
    const mutating = command.action === "apply_operations" || command.action === "undo_node";
    const preflight = await syncSnapshot(true, "preflight", command.command_id);
    revision = preflight?.revision ?? state.revision;
    const workflow = app.graph?.serialize?.() ?? {};
    currentWorkflowId = workflowIdentity(workflow, workflowTitle());
    if (command.workflow_id && command.workflow_id !== currentWorkflowId) {
      throw new BridgeCommandError(
        "workflow_changed",
        `The active workflow changed before command execution: expected ${command.workflow_id}, current ${currentWorkflowId}`,
      );
    }
    if (mutating && Number(command.base_revision) !== Number(revision)) {
      throw new BridgeCommandError(
        "revision_conflict",
        `Canvas changed before command execution: expected r${command.base_revision}, current r${revision}`,
      );
    }

    const payload = command.payload ?? {};
    if (command.action === "apply_operations") {
      const earlyIds = payload.operations?.flatMap(operationNodeRefs) ?? [];
      highlighter.set(earlyIds, 12000);
      const beforeWorkflow = app.graph.serialize();
      result = await applyOperations(app, payload.operations, highlighter.set);
      mutationApplied = true;
      const afterWorkflow = app.graph.serialize();
      recordNodeHistory(command.command_id, beforeWorkflow, afterWorkflow, result);
      result.command_id = command.command_id;
      result.history_id = command.command_id;
    } else if (command.action === "undo_node") {
      const entry = modificationHistory.find((item) =>
        item.historyId === String(payload.history_id) && String(item.nodeId) === String(payload.node_id),
      );
      if (!entry) throw new BridgeCommandError("history_unavailable", "No matching node history entry is available in this browser session");
      if (entry.workflowId !== currentWorkflowId) throw new BridgeCommandError("history_conflict", "This history entry belongs to a different workflow");
      if (entry.undone) throw new BridgeCommandError("history_conflict", "This node history entry was already undone");
      if (entry.stale) throw new BridgeCommandError("history_conflict", "This history entry is stale and cannot be safely undone");
      const beforeUndoWorkflow = workflow;
      await applyNodeHistoryChange(entry);
      mutationApplied = true;
      entry.undone = true;
      rebaseRelatedHistory(entry, beforeUndoWorkflow, app.graph.serialize());
      renderHistoryConsole();
      highlighter.set([entry.nodeId], 2200);
      result = {
        history_id: entry.historyId,
        node_id: entry.nodeId,
        undone: true,
        affected_node_ids: [entry.nodeId],
      };
    } else if (command.action === "highlight_nodes") {
      result = highlighter.set(payload.node_ids, payload.duration_ms);
    } else if (command.action === "focus_nodes") {
      result = focusNodes(app, payload.node_ids);
      highlighter.set(result.focused, payload.duration_ms ?? 5000);
    } else if (command.action === "export_api") {
      const exported = await app.graphToPrompt();
      result = {
        api_workflow: exported.output,
        workflow: exported.workflow,
        node_count: Object.keys(exported.output ?? {}).length,
      };
    } else if (command.action === "queue_prompt") {
      result = await queuePrompt(payload);
    } else {
      throw new BridgeCommandError("unsupported_action", `Unsupported action "${command.action}"`);
    }

    if (mutating) {
      try {
        const synced = await syncSnapshot(true, "agent", command.command_id);
        revision = synced?.revision ?? state.revision;
      } catch (error) {
        syncPendingError = error;
        result.sync_pending = true;
        result.sync_error = error.message;
        state.connected = false;
      }
    }
    await requestJson(`${API_PREFIX}/command-result`, {
      method: "POST",
      body: JSON.stringify({
        command_id: command.command_id,
        session_id: state.sessionId,
        ok: true,
        revision,
        workflow_id: currentWorkflowId,
        result,
      }),
    });
    if (syncPendingError) {
      const message = `本地已${command.action === "undo_node" ? "撤回" : "修改"}；同步待重试`;
      pendingNotice = message;
      const feedback = state.historyConsole?.querySelector(".cab-history-feedback");
      if (feedback) feedback.textContent = message;
      setStatus("error", `Agent Bridge · ${message}`, syncPendingError.message);
    } else {
      setStatus("online", `Agent Bridge · r${revision}`, `AI 命令完成：${command.action}`);
    }
  } catch (error) {
    const code = error?.code || "browser_error";
    const message = error?.message || String(error);
    if (mutationApplied) {
      const successResult = {
        ...(result ?? {}),
        ...(syncPendingError ? { sync_pending: true, sync_error: syncPendingError.message } : {}),
      };
      try {
        await requestJson(`${API_PREFIX}/command-result`, {
          method: "POST",
          body: JSON.stringify({
            command_id: command.command_id,
            session_id: state.sessionId,
            ok: true,
            revision,
            workflow_id: currentWorkflowId,
            result: successResult,
          }),
        });
      } catch (reportError) {
        confirmationPending = true;
        console.error("[Comfy Agent Bridge] Mutation was applied but its result could not be confirmed", reportError);
      }
      if (!syncPendingError && !confirmationPending) {
        pendingNotice = null;
        setStatus("online", `Agent Bridge · r${revision}`, "已完成修改并确认结果");
      } else {
        const statusMessage = syncPendingError
          ? "本地修改已完成，同步待重试"
          : "本地修改已完成，服务器确认待重试";
        pendingNotice = statusMessage;
        const feedback = state.historyConsole?.querySelector(".cab-history-feedback");
        if (feedback) feedback.textContent = statusMessage;
        setStatus("error", `Agent Bridge · ${statusMessage}`, `${code}: ${message}`);
        if (syncPendingError) {
          void syncSnapshot(true, "agent-retry", command.command_id).catch((retryError) => {
            console.error("[Comfy Agent Bridge] Pending snapshot retry failed", retryError);
          });
        }
      }
    } else {
      try {
        await requestJson(`${API_PREFIX}/command-result`, {
          method: "POST",
          body: JSON.stringify({
            command_id: command.command_id,
            session_id: state.sessionId,
            ok: false,
            revision,
            error: { code, message },
          }),
        });
      } catch (reportError) {
        console.error("[Comfy Agent Bridge] Could not report command failure", reportError);
      }
      setStatus("error", `Agent Bridge · ${message}`, `${code}: ${message}`);
    }
  } finally {
    state.commandRunning = false;
    setTimeout(() => {
      if (state.commandRunning) return;
      if (pendingNotice && (confirmationPending || state.lastSyncAt <= commandStartedAt)) {
        setStatus("error", `Agent Bridge · ${pendingNotice}`, pendingNotice);
      } else if (state.connected) {
        setStatus("online", `Agent Bridge · r${state.revision}`, workflowTitle());
      }
    }, 1600);
  }
}

function onCommand(event) {
  const command = event?.detail ?? event;
  if (!command || command.session_id !== state.sessionId) return;
  if (!command.command_id || state.commandCache.has(command.command_id)) return;
  state.commandCache.set(command.command_id, Date.now());
  while (state.commandCache.size > COMMAND_CACHE_LIMIT) {
    state.commandCache.delete(state.commandCache.keys().next().value);
  }
  state.commandQueue = state.commandQueue
    .catch(() => undefined)
    .then(() => executeCommand(command));
}

function startLoops() {
  window.addEventListener("focus", noteUserActivity, { passive: true });
  window.addEventListener("pointerdown", noteUserActivity, { capture: true, passive: true });
  window.addEventListener("keydown", noteUserActivity, { capture: true, passive: true });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") noteUserActivity();
  });
  setInterval(async () => {
    if (state.commandRunning) return;
    try {
      await syncSnapshot(false, "user");
    } catch (error) {
      state.connected = false;
      setStatus("error", "Agent Bridge · 同步失败", error.message);
    }
  }, SYNC_INTERVAL_MS);
  setInterval(async () => {
    try {
      await heartbeat();
    } catch (error) {
      state.connected = false;
      setStatus("error", "Agent Bridge · 已断开", error.message);
    }
  }, HEARTBEAT_INTERVAL_MS);
}

app.registerExtension({
  name: "Comfy.AgentBridge.Live",
  async setup() {
    createStatusElement();
    createHistoryConsole();
    highlighter.ensureCanvas();
    api.addEventListener(COMMAND_EVENT, onCommand);
    startLoops();
    try {
      await syncSnapshot(true, "user");
    } catch (error) {
      setStatus("error", "Agent Bridge · 等待 ComfyUI WebSocket", error.message);
    }
    globalThis.comfyAgentBridge = {
      sessionId: state.sessionId,
      get revision() { return state.revision; },
      get workflowId() {
        const workflow = app.graph?.serialize?.() ?? {};
        return workflowIdentity(workflow, workflowTitle());
      },
      sync: () => syncSnapshot(true, "user"),
      highlight: (nodeIds, durationMs) => highlighter.set(nodeIds, durationMs),
    };
  },
});
