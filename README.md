# ComfyUI Agent Bridge

让支持 MCP 的 AI 客户端读取和修改**当前打开的 ComfyUI 画布**，而不是猜测某份磁盘工作流的内容。

![ComfyUI Agent Bridge](assets/hero.svg)

[下载 v1.3.2](https://github.com/sfysyyds/ComfyUI-Agent-Bridge/releases/tag/v1.3.2) · [安装](#安装) · [使用说明](使用说明.txt)

## 安装

目前提供经过验证的 Windows 安装流程。下载 [发布包](https://github.com/sfysyyds/ComfyUI-Agent-Bridge/releases/download/v1.3.2/ComfyUI-Agent-Bridge-1.3.2.zip) 并解压，在解压目录打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File ".\comfyui-agent\scripts\install.ps1" -ComfyUIRoot "C:\你的ComfyUI目录"
```

完全重启 ComfyUI 和 AI 客户端，然后运行诊断：

```powershell
powershell -ExecutionPolicy Bypass -File ".\comfyui-agent\scripts\diagnose.ps1" -ComfyUIRoot "C:\你的ComfyUI目录"
```

安装器会备份旧文件，不修改 ComfyUI 核心。其他支持本地 stdio MCP 的客户端见 [AI 安装说明](AI安装说明.md#非-codex-mcp-客户端)。

## 使用

打开 ComfyUI 页面后，可以直接让 AI：

> 读取当前工作流，说明模型、采样器和输出链路。

> 检查最近一次生成失败的原始报错，只修复出错的节点。

也可以从空白画布搭建工作流。遇到第三方节点时，桥接从正在运行的 ComfyUI 读取节点定义和实际状态，不依赖预设节点名单。

桥接能读取当前画布、节点与连线、运行队列、近期执行结果和错误。修改会核对目标工作流及版本；批量修改可以用 ComfyUI 的 Ctrl+Z 撤销，页面内的历史记录还支持按节点撤回。**历史记录仅保存在当前页面，刷新后会清空。**

## 组成与边界

- `comfyui-plugin/`：安装到 ComfyUI 的 `custom_nodes`，负责画布同步和执行图操作。
- `comfyui-agent/`：本地 MCP 服务、Skill 及安装脚本。Skill 约束读取、修改和核对顺序。

桥接不会主动保存工作流，也不会执行任意浏览器 JavaScript 或自动安装节点。默认连接 `127.0.0.1:8188`；接口没有公网身份认证，**不要把 ComfyUI 控制端口暴露到互联网**。ComfyUI 自带的自动保存设置仍会生效。

项目为社区作品，并非 ComfyUI 官方插件。故障排查、卸载和开发说明见 [使用说明](使用说明.txt) 与 [AI 维护说明](AI维护说明.md)。
