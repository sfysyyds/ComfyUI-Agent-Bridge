# ComfyUI Agent Bridge 1.3.2 — AI 维护说明

本文是项目的完整技术入口。未来 ComfyUI、前端、MCP SDK 或智能体客户端更新后，维护者应先读完本文，再修改代码。

## 1. 目标与不变量

本项目只控制用户当前浏览器中可见的 ComfyUI 工作流，不修改 ComfyUI 核心。

必须保持：

1. 浏览器当前 workflow 是唯一 UI 真源，磁盘 JSON 不是替代品。
2. 空白 workflow 是合法目标；从 0 搭建必须使用 `add_node`，不得加载另一张图。
3. 每个浏览器页面有 `session_id`，每个 ComfyUI 内部 workflow 有原生 `workflow_id`。
4. 所有画布命令都绑定 session_id + workflow_id；结构修改还绑定 base_revision。
5. 一批 `apply_graph_operations` 是一个原生 ChangeTracker 撤销单元。
6. 修改成功后工作流处于 modified/未保存状态；桥接不调用保存接口。
7. 受影响节点自动显示可见光效，任何高亮至少 1000 ms。
8. 节点 schema 来自运行时 `/object_info`，模型名来自 `/models/{folder}`。
9. 命令是固定白名单，绝不执行智能体传来的任意 JavaScript。
10. MCP stdout 只用于协议；日志不得污染 stdout。

## 2. 分发结构

顶层固定只有两个文件夹和三份说明：

```text
comfyui-agent/
  .codex-plugin/plugin.json
  .mcp.json
  mcp_server/
  skills/comfyui-live-control/
  scripts/
  tests/
comfyui-plugin/
  __init__.py
  bridge_routes.py
  bridge_state.py
  requirements.txt
  web/
使用说明.txt
AI维护说明.md
AI安装说明.md
```

不要把以下内容放进发布包：

- `.venv`、`runtime`、`__pycache__`、`.pyc`
- `*.egg-info`、测试输出、审计日志
- 本机绝对路径配置、用户工作流、模型或生成图片
- 旧 TODO、旧测试报告、重复架构文档

## 3. 组件职责

### 3.1 ComfyUI 浏览器扩展

`comfyui-plugin/web/agent_bridge.js`

- 负责扩展注册、快照调度、命令队列和状态标签。
- 每 300 ms 比较 workflow UUID、标题和结构哈希；改变时上传快照。
- 同一时刻只允许一个快照请求；命令 preflight 的强制快照会排在已有请求后并等待完成。
- 每 5 秒发送在线状态与用户活动时间。
- 从 `app.graph.serialize().id` 读取稳定 workflow_id。
- 用可见 workflow tab 的 DOM 标签作为标题优先来源，规避 activeWorkflow 切换滞后。
- 接收 ComfyUI 原生 WebSocket 自定义事件。
- 对最近 128 个 command_id 去重，避免重复 WebSocket 帧造成二次修改。

`agent_bridge_core.js` 只放可离线测试的纯函数：ID、revision hash、slot、位置、光效时长和坐标映射。

`agent_bridge_graph.js` 负责白名单图操作、事务边界、失败回滚和节点聚焦。图修改实现不得重新塞回主入口。

`agent_bridge_highlight.js` 独占透明 canvas、动画生命周期和坐标绘制，避免画布逻辑污染命令调度。

### 3.2 ComfyUI Python 插件

`bridge_state.py`

- 保存每个浏览器 session 的最新快照。
- workflow SHA 或 workflow_id 改变时递增 revision。
- 用用户 activity_at_ms，而不是“页面可见”心跳，判断最近活动会话。
- 旧 session 15 秒后离线，5 分钟后清理。

`bridge_routes.py`

- 提供 `/comfy-agent-bridge/v1` HTTP 路由。
- 把命令定向到准确 client_id。
- 在发命令前检查在线、歧义和 revision。
- 同一 session 的命令在服务端串行；真正派发前再次检查在线状态和 revision。
- pending 命令有全局上限，派发异常会立即清理 future，不泄漏等待项。
- 命令帧包含目标 workflow_id。
- 等待浏览器 ACK；超时或失败不得假装成功。

### 3.3 MCP

`comfyui-agent/mcp_server`

提供 17 个聚焦工具：

- 状态/会话/诊断：`bridge_status`、`list_live_sessions`、`get_recent_history`
- 画布读取：`get_workflow_outline`、`get_raw_workflow_session`、`get_node`
- 节点/模型发现：`search_node_catalog`、`get_node_schema`、`list_models`
- 编辑/撤回/定位：`apply_graph_operations`、`undo_node_modification`、`focus_nodes`
- 运行：`export_current_workflow_api`、`queue_live_workflow`、`get_queue_status`、`get_generation_result`、`wait_for_generation`

默认用 outline + get_node 精确读取，只有需要全部原始状态时才调用 get_raw_workflow_session；修改自动高亮，focus_nodes 可手动定位。

MCP HTTP client 在进程内复用连接池；`COMFYUI_URL` 会规范化 `/api` 后缀并拒绝相对 URL。节点目录搜索按精确、前缀、名称、分类相关性排序。outline 同时返回可读文本和紧凑 `nodes` 数组，供后续精确工具调用。

### 3.4 Skill

Skill 只保存跨工具的正确顺序和安全边界，不重复所有协议细节。MCP 是执行能力，Skill 是行为约束，因此两者必须一起保留。

## 4. 活动工作流识别

### 4.1 为什么 1.0 会出错

1.0 仅绑定浏览器 session。ComfyUI 内部切换 workflow tab 时，`activeWorkflow` 指针存在短暂滞后；失败回滚又调用了不带目标 workflow 对象的 `app.loadGraphData(backup)`，可能触发默认的新工作流加载路径。

### 4.2 1.1+ 处理方式

快照包含：

```json
{
  "session_id": "browser-page-uuid",
  "workflow_id": "comfy-native-workflow-uuid",
  "title": "Unsaved Workflow (6)",
  "workflow": {},
  "view": {},
  "activity_at_ms": 1785130000000,
  "origin": "user"
}
```

命令帧包含同一 workflow_id。浏览器收到命令后强制提交 preflight 快照，再比较：

- workflow_id 不同：`workflow_changed`
- revision 不同：`revision_conflict`
- 两者都相同：才执行命令

整图 `load_workflow` 不在 ALLOWED_ACTIONS 中，也没有 MCP 工具。

失败回滚使用：

```js
app.loadGraphData(backup, false, false, activeWorkflow, options)
```

它把备份恢复到同一个 ComfyWorkflow 对象，并在仍打开的 beforeChange/afterChange 事务内完成，不创建新 workflow tab。

## 5. 撤销、未保存与保存语义

ComfyUI 前端的 ChangeTracker 监听画布 DOM 上的 LiteGraph `before-change` / `after-change` 事件；只调用 `graph.beforeChange()/afterChange()` 不足以通知当前前端。插件的一批操作必须同时调用一对 graph 边界和一对 canvas 边界：

```text
graph.beforeChange()
app.canvas.emitBeforeChange()
  -> N 个同步图操作
graph.afterChange()
app.canvas.emitAfterChange()
```

因此：

- N 个操作进入一个 undoQueue 条目；
- 一次 Ctrl+Z 恢复整个批次；
- 连续两个 apply_graph_operations 进入两个独立条目，不得被合并；
- `undo_node` 使用原批次 `command_id` 和 `node_id` 选择历史条目；只有当前节点及关联连线仍与该条目修改后的状态一致时才允许单独撤回，否则返回 `history_conflict`，不得用整图快照覆盖其他节点。
- ChangeTracker 比较 initialState 与 activeState，并设置 workflow.isModified；
- 插件没有保存工作流 action，也不调用 workflow persistence API。

注意：ComfyUI 自身的 auto-save 功能若由用户启用，可能在 modified 后自行保存。插件不应篡改或关闭用户的 auto-save 设置。

视觉命令 `focus_nodes`、光效和 viewport 不属于结构 hash，不应增加 revision。

诊断读取：`bridge_status` 分组件报告桥接状态、系统设备、队列和近期桥接错误，单一端点失败不得吞掉其他可用信息；`get_raw_workflow_session` 返回当前会话完整工作流/视图；`get_recent_history` 分页透传 ComfyUI 原始执行历史（包括 `status.messages` 中的 `execution_error`）。近期桥接错误仅含有界的命令失败记录，不等于 ComfyUI 进程启动日志。

## 6. 节点光效

旧 `node.onDrawForeground` 在已验证的前端 1.45.21 上接口返回成功但不可见，因此 1.1 改为自有透明 canvas：

- fixed 定位到 ComfyUI 主画布的 `getBoundingClientRect()`；
- pointer-events: none，不影响操作；
- 只在存在高亮时运行 `requestAnimationFrame`；
- 最多使用 2 倍 devicePixelRatio，控制显存和带宽；
- 优先用 `node.getBounding()` 获取包含标题栏的实际节点外框，再按 `(graphPos + ds.offset) * ds.scale` 转为屏幕坐标；
- 紫色围边 + 沿边流光 + 轻微自发光；
- `clampHighlightDuration()` 强制 1000–60000 ms；
- 高亮结束立即隐藏 canvas、停止动画。

维护时必须用真实截图肉眼验证，不能只相信 ACK 中的 `highlighted` 数组。

## 7. HTTP 和 WebSocket 协议

协议版本：`comfy-agent-bridge/v1`  
插件版本：`1.3.2`  
HTTP 前缀：`/comfy-agent-bridge/v1`

路由：

- `GET /status`
- `GET /errors`（最近 100 条桥接命令失败，字段有界且标记截断）
- `GET /sessions`
- `GET /sessions/{session_id}?raw=0|1`
- `POST /snapshot`
- `POST /heartbeat`
- `POST /command`
- `POST /command-result`

WebSocket 事件：

```text
comfy.agent_bridge.command
```

允许 action：

- `apply_operations`
- `highlight_nodes`（内部协议保留，MCP 不单独暴露）
- `focus_nodes`
- `export_api`
- `queue_prompt`

`apply_operations` 最多 100 个操作：

- `add_node`
- `remove_node`
- `set_widget`
- `connect`
- `disconnect`
- `move_node`
- `set_title`
- `set_mode`

协议破坏性变化应新增 v2；仅添加可选字段且旧端安全拒绝/忽略时可保持 v1。

## 8. ComfyUI 更新后的检查点

已验证基线（2026-07-27）：

- ComfyUI 0.28.0
- comfyui_frontend_package 1.45.21
- Python 3.13.11
- MCP Python SDK 1.28.1

高风险接点：

- `app.graph.serialize()` 是否继续包含稳定 `id`
- `app.graph.beforeChange()/afterChange()`
- `app.canvas.emitBeforeChange()/emitAfterChange()` 或 `litegraph:canvas` 事件
- `app.loadGraphData(data, clean, restoreView, workflow, options)`
- `app.extensionManager.workflow.activeWorkflow`
- workflow tab DOM 的 `.workflow-tab .workflow-label`
- `api.addEventListener()` 和 `PromptServer.send_sync()`
- `app.graphToPrompt()`、`api.queuePrompt()`
- `app.canvas.ds.scale/offset` 和主 canvas DOM
- live `node.widgets/inputs/outputs`

若 workflow tab DOM 变化，标题可以退回 activeWorkflow；身份仍必须优先使用序列化 workflow.id。

## 9. 必测矩阵

### 离线

```powershell
powershell -ExecutionPolicy Bypass -File ".\comfyui-agent\scripts\test.ps1"
```

要求：

- Python unittest 全过
- Node test 全过
- 全部前端 JavaScript module syntax check 通过
- Python AST syntax check 通过且不生成 `__pycache__`
- 版本一致性、Skill frontmatter、Codex manifest 和发布树清洁检查通过
- MCP stdio initialize + tools/list 通过，工具数 17

### 实机

1. `/status` 200，版本和协议正确。
2. 页面显示状态标签，无桥接 Console error。
3. 新建空白 workflow，outline 为 0 且 UUID 改变。
4. 在当前空白 tab 添加节点，不增加 workflow tab。
5. 修改节点时光效截图可见，至少 1 秒。
6. 一个多操作批次一次 Ctrl+Z 全部恢复；连续两个批次需分两次撤销。
7. 修改后显示 dirty；插件不发保存请求。
8. 旧 revision 返回冲突。
9. 读取后切换 workflow 再写，返回 workflow_changed。
10. 故意无效操作整批回滚，仍留在原 workflow tab。
11. 两个浏览器页面时会话可列出，最近真实交互页面优先。
12. 官方节点与至少一个第三方节点 schema/连接可读。
13. 导出 API、低成本排队、history 和输出 URL 闭环。

## 10. 发布流程

1. 更新 ComfyUI 插件、MCP、Skill 和三份说明。
2. 同步版本：
   - `bridge_state.py` 的 PLUGIN_VERSION
   - `mcp_server/pyproject.toml`
   - MCP 包 `__version__`
   - `.codex-plugin/plugin.json`
3. 运行完整离线与实机矩阵。
4. 确认发布树顶层严格为两个文件夹、三个说明文件。
5. 排除缓存、venv、egg-info 和本机数据。
6. 生成 zip，列出压缩包内容再做一次结构检查。
7. 在一台未安装机器或干净用户目录做安装烟雾测试。

## 11. 安全与隐私

- 默认 URL 为 `http://127.0.0.1:8188`。
- 不提供公网认证；远程访问必须由反向代理提供 TLS 和认证。
- 工作流快照会被本机 MCP 读取，用户应把 MCP 当作本机高权限工具。
- 审计日志只记录命令元数据，不记录完整提示词或工作流。
- 不执行 shell、任意 JS、模型下载、节点安装或任意文件读取。
- 安装脚本所有替换先做可恢复备份。

## 12. 许可证与参考

项目代码以 MIT License 分发：

Copyright (c) 2026 ComfyUI Agent Bridge contributors

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files, to deal in the Software without restriction, including use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies, subject to inclusion of this notice. The software is provided “AS IS”, without warranty of any kind.

设计与兼容性调研参考：

- Comfy-Org/ComfyUI
- Comfy-Org/ComfyUI_frontend
- artokun/comfyui-mcp
- artokun/comfyui-mcp-panel
- ConstantineB6/Comfy-Pilot

本项目不复制这些项目的源码；更新时应重新核对各自当前许可证和公开接口。
