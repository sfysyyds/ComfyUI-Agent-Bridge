[CmdletBinding()]
param(
    [string]$ComfyUIUrl = ""
)

$ErrorActionPreference = "Stop"
$dataRoot = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "ComfyUI-Agent-Bridge"
$venvPython = Join-Path $dataRoot "runtime\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    [Console]::Error.WriteLine(
        "ComfyUI Agent Bridge MCP is not installed. Run scripts\install.ps1 first."
    )
    exit 2
}
if ($ComfyUIUrl) {
    $env:COMFYUI_URL = $ComfyUIUrl.TrimEnd("/")
} elseif (-not $env:COMFYUI_URL) {
    $env:COMFYUI_URL = "http://127.0.0.1:8188"
}
& $venvPython -m comfyui_agent_bridge_mcp
exit $LASTEXITCODE
