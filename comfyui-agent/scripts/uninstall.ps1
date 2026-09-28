[CmdletBinding()]
param(
    [string]$ComfyUIRoot,
    [switch]$KeepMcpEnvironment,
    [switch]$KeepSkill,
    [switch]$KeepCodexConfig
)

$ErrorActionPreference = "Stop"
$dataRoot = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "ComfyUI-Agent-Bridge"
$stamp = (Get-Date -Format "yyyyMMdd-HHmmss-fff") + "-" + (
    [Guid]::NewGuid().ToString("N").Substring(0, 8)
)
$uninstallBackup = Join-Path $dataRoot "backups\uninstall-$stamp"
New-Item -ItemType Directory -Force -Path $uninstallBackup | Out-Null

function Resolve-ComfyRoot {
    param([string]$Candidate)
    if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate)) {
        return $null
    }
    $resolved = (Resolve-Path -LiteralPath $Candidate).Path
    if (Test-Path -LiteralPath (Join-Path $resolved "main.py")) {
        return $resolved
    }
    if (Test-Path -LiteralPath (Join-Path $resolved "ComfyUI\main.py")) {
        return (Resolve-Path -LiteralPath (Join-Path $resolved "ComfyUI")).Path
    }
    return $null
}

$comfyRootResolved = Resolve-ComfyRoot -Candidate $ComfyUIRoot
if (-not $comfyRootResolved) {
    $statePath = Join-Path $dataRoot "runtime\install-state.json"
    if (Test-Path -LiteralPath $statePath) {
        $state = Get-Content -Raw -Encoding UTF8 -LiteralPath $statePath | ConvertFrom-Json
        $comfyRootResolved = Resolve-ComfyRoot -Candidate $state.comfyui_root
    }
}
if (-not $comfyRootResolved) {
    throw "ComfyUI location is unknown. Pass -ComfyUIRoot."
}

$customNodesRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $comfyRootResolved "custom_nodes")
).TrimEnd("\")
$pluginTarget = [System.IO.Path]::GetFullPath(
    (Join-Path $customNodesRoot "ComfyUI-Agent-Bridge")
).TrimEnd("\")
if (-not $pluginTarget.StartsWith(
    $customNodesRoot + "\",
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Refusing to operate outside custom_nodes: $pluginTarget"
}
if (Test-Path -LiteralPath $pluginTarget) {
    Move-Item -LiteralPath $pluginTarget -Destination (
        Join-Path $uninstallBackup "ComfyUI-Agent-Bridge"
    )
}

$configuredCodexRoot = [Environment]::GetEnvironmentVariable("CODEX_HOME")
if (-not $configuredCodexRoot) {
    $configuredCodexRoot = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".codex"
}

if (-not $KeepSkill) {
    $skillTarget = Join-Path $configuredCodexRoot "skills\comfyui-live-control"
    if (Test-Path -LiteralPath $skillTarget) {
        Move-Item -LiteralPath $skillTarget -Destination (
            Join-Path $uninstallBackup "comfyui-live-control"
        )
    }
}

if (-not $KeepCodexConfig) {
    $configPath = Join-Path $configuredCodexRoot "config.toml"
    if (Test-Path -LiteralPath $configPath) {
        Copy-Item -LiteralPath $configPath -Destination (
            Join-Path $uninstallBackup "codex-config.toml"
        ) -Force
        $configText = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8
        $pattern = '(?ms)^\[mcp_servers\.comfyui_live\]\r?\n.*?(?=^\[|\z)'
        $configText = [regex]::Replace($configText, $pattern, "")
        [System.IO.File]::WriteAllText(
            $configPath,
            $configText.TrimEnd(),
            [System.Text.UTF8Encoding]::new($false)
        )
    }
}

$venvTarget = Join-Path $dataRoot "runtime\.venv"
if (-not $KeepMcpEnvironment -and (Test-Path -LiteralPath $venvTarget)) {
    Move-Item -LiteralPath $venvTarget -Destination (
        Join-Path $uninstallBackup "mcp-venv"
    )
}

Write-Host "Uninstall completed. Removed content is recoverable at: $uninstallBackup"
Write-Host "Restart ComfyUI and the agent client."
