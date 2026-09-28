# ComfyUI Agent Bridge 1.3.2 — AI 安装说明

本文给能够操作本机文件和 PowerShell 的智能体使用。目标是安装分发包，不修改 ComfyUI 核心。

## 交付结构

包根目录必须只有：

```text
comfyui-agent/     # MCP、Skill、Codex 清单、安装/测试脚本
comfyui-plugin/    # 复制到 custom_nodes/ComfyUI-Agent-Bridge
使用说明.txt
AI维护说明.md
AI安装说明.md
```

MCP 和 Skill 互补：MCP 提供实时工具，Skill 约束正确的读取、schema、revision 和修改顺序。不要删掉其中任意一个。

## 安装前检查

1. 定位包根目录，确认以上两个文件夹和三份说明都存在。
2. 定位 ComfyUI 根目录：
   - 根目录内应有 `main.py`、`custom_nodes/`。
   - 若用户给的是整合包目录，可能需要再进入其 `ComfyUI/` 子目录。
3. 确认 Python 3.10+：
   - 整合包常见位置是 ComfyUI 上一级的 `python/python.exe`；
   - 否则使用系统 `python.exe`。
4. 读取现有 `%USERPROFILE%\.codex\config.toml`，不要覆盖与本插件无关的配置。
5. 若 ComfyUI 正在运行，安装文件后需要完全重启；不要只刷新浏览器。

## 推荐安装

在包根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File ".\comfyui-agent\scripts\install.ps1" `
  -ComfyUIRoot "C:\实际路径\ComfyUI"
```

非默认端口：

```powershell
powershell -ExecutionPolicy Bypass -File ".\comfyui-agent\scripts\install.ps1" `
  -ComfyUIRoot "C:\实际路径\ComfyUI" `
  -ComfyUIUrl "http://127.0.0.1:8190"
```

安装器会先把旧的 ComfyUI 插件、Skill 和 Codex 配置备份到：

```text
%LOCALAPPDATA%\ComfyUI-Agent-Bridge\backups
```

ComfyUI 插件先复制到 `custom_nodes` 内的临时 staging 目录，完整性检查通过后才替换旧目录；替换失败会恢复上一版。MCP 也从 runtime 内的独立源码副本构建，不会在分发目录生成 `build` 或 `*.egg-info`。

MCP 独立环境安装到：

```text
%LOCALAPPDATA%\ComfyUI-Agent-Bridge\runtime\.venv
```

不要把分发包里的 Python 包用 editable 模式作为最终安装；安装器使用普通 pip 安装，用户移动或删除解压目录后 MCP 仍可运行。

## 非 Codex MCP 客户端

给支持本地 stdio MCP 的客户端配置：

```json
{
  "mcpServers": {
    "comfyui_live": {
      "command": "C:\\Users\\USERNAME\\AppData\\Local\\ComfyUI-Agent-Bridge\\runtime\\.venv\\Scripts\\python.exe",
      "args": ["-m", "comfyui_agent_bridge_mcp"],
      "env": {
        "COMFYUI_URL": "http://127.0.0.1:8188"
      }
    }
  }
}
```

若客户端支持可复用指令/Skill，再安装或导入：

```text
comfyui-agent\skills\comfyui-live-control\SKILL.md
```

## 重启与验收

1. 完全退出 ComfyUI 和智能体客户端。
2. 启动 ComfyUI，打开网页。
3. 确认右下角出现 `Agent Bridge · rN`。
4. 重启智能体客户端并新建一个任务，使 MCP/Skill 重新加载。
5. 执行诊断：

```powershell
powershell -ExecutionPolicy Bypass -File ".\comfyui-agent\scripts\diagnose.ps1" `
  -ComfyUIRoot "C:\实际路径\ComfyUI"
```

通过标准：

- `comfyui_plugin.ok = true`
- `mcp_runtime.ok = true`
- `mcp_import.ok = true`
- `codex.mcp_configured = true`（Codex 安装）
- `codex.skill_installed = true`（Codex 安装）
- `live_bridge.ok = true`
- `live_bridge.plugin_version = 1.3.2`
- `live_bridge.online_count >= 1`
- 活动 session 含非空 `workflow_id`

## 必做交互烟雾测试

1. 在 ComfyUI 内点击“创建空白工作流”。
2. 调用 `bridge_status`、`list_live_sessions`（若多会话）、`get_workflow_outline`。
3. 确认当前 outline 为 0 节点且 workflow_id 与之前工作流不同。
4. 读取 `EmptyLatentImage` schema，用一个 `add_node` 操作添加测试节点。
5. 确认节点出现在当前空白 tab，没有新增另一个 workflow tab。
6. 确认节点光效肉眼可见，至少持续 1 秒。
7. 在画布获得焦点后按一次 Ctrl+Z，确认测试节点消失。
8. 对一张已保存工作流做无害标题修改时，确认 tab 出现未保存标记；不要调用保存。
9. 测试完用 Ctrl+Z 还原。

## 安装失败处理

- `ComfyUI main.py was not found`：重新定位并传 `-ComfyUIRoot`。
- Python venv 创建失败：显式传 `-PythonExe "...\python.exe"`。
- MCP 导入失败：用 venv Python 执行 `-m pip install --upgrade .\comfyui-agent\mcp_server`。
- 网页无状态标签：检查自定义节点目录、启动日志，完全重启后 Ctrl+F5。
- 多个会话导致歧义：让用户点击目标页面，再列出会话并使用准确 session_id。

## 安装边界

- 不修改 `ComfyUI/main.py`、`server.py`、`execution.py` 或前端包文件。
- 不安装其他自定义节点，不下载模型。
- 不删除用户工作流。
- 不把 8188 暴露到公网。
- 不宣称安装完成，除非诊断和至少一次空白画布 smoke test 通过。
