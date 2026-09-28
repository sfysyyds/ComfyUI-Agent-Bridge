import {
  clampHighlightDuration,
  nodeRectToScreen,
} from "./agent_bridge_core.js";

function roundedRect(context, x, y, width, height, radius) {
  const safeRadius = Math.min(radius, width / 2, height / 2);
  context.beginPath();
  context.moveTo(x + safeRadius, y);
  context.lineTo(x + width - safeRadius, y);
  context.quadraticCurveTo(x + width, y, x + width, y + safeRadius);
  context.lineTo(x + width, y + height - safeRadius);
  context.quadraticCurveTo(x + width, y + height, x + width - safeRadius, y + height);
  context.lineTo(x + safeRadius, y + height);
  context.quadraticCurveTo(x, y + height, x, y + height - safeRadius);
  context.lineTo(x, y + safeRadius);
  context.quadraticCurveTo(x, y, x + safeRadius, y);
  context.closePath();
}

export function createNodeHighlighter(app) {
  const highlights = new Map();
  let animationFrame = null;
  let canvas = null;
  let context = null;

  function ensureCanvas() {
    if (canvas?.isConnected && context) return canvas;
    canvas = document.getElementById("comfy-agent-bridge-highlight-layer");
    if (!(canvas instanceof HTMLCanvasElement)) {
      canvas = document.createElement("canvas");
      canvas.id = "comfy-agent-bridge-highlight-layer";
      canvas.setAttribute("aria-hidden", "true");
      document.body.appendChild(canvas);
    }
    context = canvas.getContext("2d");
    return canvas;
  }

  function resizeCanvas() {
    const graphCanvas = app.canvas?.canvas;
    const overlay = ensureCanvas();
    if (!graphCanvas || !context) return null;
    const rect = graphCanvas.getBoundingClientRect();
    const pixelRatio = Math.max(
      1,
      Math.min(Number(globalThis.devicePixelRatio) || 1, 2),
    );
    const pixelWidth = Math.max(1, Math.round(rect.width * pixelRatio));
    const pixelHeight = Math.max(1, Math.round(rect.height * pixelRatio));
    if (overlay.width !== pixelWidth || overlay.height !== pixelHeight) {
      overlay.width = pixelWidth;
      overlay.height = pixelHeight;
    }
    overlay.style.left = `${rect.left}px`;
    overlay.style.top = `${rect.top}px`;
    overlay.style.width = `${rect.width}px`;
    overlay.style.height = `${rect.height}px`;
    overlay.style.display = "block";
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    context.clearRect(0, 0, rect.width, rect.height);
    return { rect, context };
  }

  function drawNodeGlow(drawContext, node, highlight, now) {
    const scale = Number(app.canvas?.ds?.scale ?? 1) || 1;
    const offset = app.canvas?.ds?.offset ?? [0, 0];
    const rect = nodeRectToScreen(node, scale, offset);
    if (rect.width < 2 || rect.height < 2) return;
    const age = Math.max(0, now - highlight.startedAt);
    const inset = 1.5;
    const x = rect.x + inset;
    const y = rect.y + inset;
    const width = Math.max(1, rect.width - inset * 2);
    const height = Math.max(1, rect.height - inset * 2);
    const radius = Math.min(10, width / 2, height / 2);
    const perimeter = 2 * (width + height - 4 * radius) + 2 * Math.PI * radius;
    const streakLength = Math.min(perimeter * 0.22, 88);
    const flowOffset = -(age / 4200) * perimeter;

    drawContext.save();
    drawContext.lineWidth = 1.5;
    drawContext.strokeStyle = "rgba(168, 85, 247, 0.48)";
    drawContext.shadowColor = "rgba(168, 85, 247, 0.48)";
    drawContext.shadowBlur = 9;
    roundedRect(drawContext, x, y, width, height, radius);
    drawContext.stroke();

    // One seamless purple tracer travels the rounded perimeter; its shadow is the soft outer glow.
    drawContext.lineWidth = 3;
    drawContext.strokeStyle = "rgba(192, 132, 252, 0.96)";
    drawContext.shadowColor = "rgba(168, 85, 247, 0.82)";
    drawContext.shadowBlur = 14;
    drawContext.lineCap = "round";
    drawContext.setLineDash([streakLength, perimeter - streakLength]);
    drawContext.lineDashOffset = flowOffset;
    roundedRect(drawContext, x, y, width, height, radius);
    drawContext.stroke();
    drawContext.restore();
  }

  function startAnimation() {
    if (animationFrame != null) return;
    const draw = () => {
      const now = Date.now();
      for (const [id, highlight] of highlights) {
        if (highlight.expiresAt <= now) highlights.delete(id);
      }
      if (!highlights.size) {
        if (canvas) canvas.style.display = "none";
        animationFrame = null;
        return;
      }
      const layer = resizeCanvas();
      if (layer) {
        for (const [id, highlight] of highlights) {
          const node = app.graph?.getNodeById?.(id) ??
            (/^-?\d+$/.test(id) ? app.graph?.getNodeById?.(Number(id)) : null);
          if (node) drawNodeGlow(layer.context, node, highlight, now);
        }
      }
      animationFrame = requestAnimationFrame(draw);
    };
    animationFrame = requestAnimationFrame(draw);
  }

  function set(nodeIds, durationMs = 5000) {
    const duration = clampHighlightDuration(durationMs);
    const startedAt = Date.now();
    const expiresAt = startedAt + duration;
    const found = [];
    const missing = [];
    for (const id of Array.isArray(nodeIds) ? nodeIds : []) {
      const node = app.graph?.getNodeById?.(id) ??
        (/^-?\d+$/.test(String(id)) ? app.graph?.getNodeById?.(Number(id)) : null);
      if (!node) {
        missing.push(id);
        continue;
      }
      highlights.set(String(node.id), { startedAt, expiresAt });
      found.push(node.id);
    }
    if (found.length) startAnimation();
    return { highlighted: found, missing, duration_ms: duration };
  }

  return {
    ensureCanvas,
    set,
    get activeCount() {
      return highlights.size;
    },
  };
}
