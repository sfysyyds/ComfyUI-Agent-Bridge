[CmdletBinding()]
param(
    [string]$ComfyUIRoot = "",
    [string]$ComfyUIUrl = "http://127.0.0.1:8188",
    [switch]$RequireBrowser,
    [switch]$Strict
)

$ErrorActionPreference = "Continue"
$dataRoot = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "ComfyUI-Agent-Bridge"
$runtimeRoot = Join-Path $dataRoot "runtime"
$results = [ordered]@{}

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
    $comfyRootResolved = Resolve-ComfyRoot -Candidate (
        [Environment]::GetEnvironmentVariable("COMFYUI_ROOT")
    )
}
if (-not $comfyRootResolved) {
    $statePath = Join-Path $runtimeRoot "install-state.json"
    if (Test-Path -LiteralPath $statePath) {
        try {
            $state = Get-Content -Raw -Encoding UTF8 -LiteralPath $statePath |
                ConvertFrom-Json
            $comfyRootResolved = Resolve-ComfyRoot -Candidate $state.comfyui_root
        } catch {
            $comfyRootResolved = $null
        }
    }
}
$pluginCandidates = if ($comfyRootResolved) {
    @("ComfyUI-Agent-Bridge", "agent-bridge") | ForEach-Object {
        Join-Path $comfyRootResolved "custom_nodes\$_"
    } | Where-Object {
        Test-Path -LiteralPath (Join-Path $_ "bridge_state.py")
    }
} else { @() }
$pluginTarget = @($pluginCandidates)[0]
$requiredPluginFiles = @(
    "__init__.py",
    "bridge_routes.py",
    "bridge_state.py",
    "web\agent_bridge.js",
    "web\agent_bridge_core.js",
    "web\agent_bridge_graph.js",
    "web\agent_bridge_highlight.js"
)
$missingPluginFiles = @()
if ($pluginTarget) {
    $missingPluginFiles = @($requiredPluginFiles | Where-Object {
        -not (Test-Path -LiteralPath (Join-Path $pluginTarget $_))
    })
}
$installedPluginVersion = $null
if ($pluginTarget -and (Test-Path -LiteralPath (Join-Path $pluginTarget "bridge_state.py"))) {
    $stateText = Get-Content -Raw -Encoding UTF8 -LiteralPath (
        Join-Path $pluginTarget "bridge_state.py"
    )
    if ($stateText -match 'PLUGIN_VERSION\s*=\s*"([^"]+)"') {
        $installedPluginVersion = $Matches[1]
    }
}
$results.comfyui_plugin = @{
    ok = [bool]($pluginTarget -and @($pluginCandidates).Count -eq 1 -and $missingPluginFiles.Count -eq 0)
    path = $pluginTarget
    version = $installedPluginVersion
    missing_files = $missingPluginFiles
    duplicate_paths = if (@($pluginCandidates).Count -gt 1) { @($pluginCandidates) } else { @() }
}

$venvPython = Join-Path $runtimeRoot ".venv\Scripts\python.exe"
$results.mcp_runtime = @{
    ok = Test-Path -LiteralPath $venvPython
    path = $venvPython
}
if (Test-Path -LiteralPath $venvPython) {
    $versionText = & $venvPython -c (
        "import comfyui_agent_bridge_mcp,sys;" +
        "print(comfyui_agent_bridge_mcp.__version__, sys.version.split()[0])"
    ) 2>&1
    $results.mcp_import = @{
        ok = $LASTEXITCODE -eq 0
        detail = ($versionText | Out-String).Trim()
    }
}

$configuredCodexRoot = [Environment]::GetEnvironmentVariable("CODEX_HOME")
if (-not $configuredCodexRoot) {
    $configuredCodexRoot = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".codex"
}
$configPath = Join-Path $configuredCodexRoot "config.toml"
$configText = if (Test-Path -LiteralPath $configPath) {
    Get-Content -LiteralPath $configPath -Raw -Encoding UTF8
} else {
    ""
}
$results.codex = @{
    mcp_configured = $configText -match '(?m)^\[mcp_servers\.comfyui_live\]$'
    skill_installed = Test-Path -LiteralPath (
        Join-Path $configuredCodexRoot "skills\comfyui-live-control\SKILL.md"
    )
    config_path = $configPath
}

$baseUrl = $ComfyUIUrl.TrimEnd("/")
try {
    $bridge = Invoke-RestMethod -Uri (
        "$baseUrl/api/comfy-agent-bridge/v1/status"
    ) -TimeoutSec 5
    $sessions = Invoke-RestMethod -Uri (
        "$baseUrl/api/comfy-agent-bridge/v1/sessions"
    ) -TimeoutSec 5
    $results.live_bridge = @{
        ok = $true
        plugin_version = $bridge.plugin_version
        protocol = $bridge.protocol
        online_count = $bridge.online_count
        active_session_id = $bridge.active_session_id
        sessions = @($sessions.sessions | ForEach-Object {
            @{
                session_id = $_.session_id
                workflow_id = $_.workflow_id
                title = $_.title
                node_count = $_.node_count
                revision = $_.revision
                focused = $_.focused
            }
        })
        browser_online = [bool]($bridge.online_count -gt 0)
    }
} catch {
    $results.live_bridge = @{
        ok = $false
        error = $_.Exception.Message
    }
}

New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$diagnosticPath = Join-Path $runtimeRoot "diagnostic-latest.json"
$results | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $diagnosticPath -Encoding UTF8
$results | ConvertTo-Json -Depth 8
Write-Host "Diagnostic report saved: $diagnosticPath"

$failed = -not $results.comfyui_plugin.ok -or
    -not $results.mcp_runtime.ok -or
    -not $results.mcp_import.ok -or
    -not $results.live_bridge.ok
if ($RequireBrowser -and -not $results.live_bridge.browser_online) {
    $failed = $true
}
if ($Strict -and (
    -not $results.codex.mcp_configured -or
    -not $results.codex.skill_installed
)) {
    $failed = $true
}
if (($Strict -or $RequireBrowser) -and $failed) {
    exit 1
}
