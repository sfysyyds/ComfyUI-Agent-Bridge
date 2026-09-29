[CmdletBinding()]
param(
    [string]$ComfyUIRoot = "",
    [string]$PythonExe = "",
    [string]$ComfyUIUrl = "http://127.0.0.1:8188",
    [switch]$SkipMcpEnvironment,
    [switch]$SkipCodexConfig,
    [switch]$SkipSkill,
    [switch]$SkipPlugin
)

$ErrorActionPreference = "Stop"
$agentRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$distributionRoot = (Resolve-Path -LiteralPath (Join-Path $agentRoot "..")).Path
$pluginSource = Join-Path $distributionRoot "comfyui-plugin"
$mcpSource = Join-Path $agentRoot "mcp_server"
$skillSource = Join-Path $agentRoot "skills\comfyui-live-control"
$dataRoot = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "ComfyUI-Agent-Bridge"
$runtimeRoot = Join-Path $dataRoot "runtime"
$backupRoot = Join-Path $dataRoot "backups"
$bridgeVersion = "1.3.3"

function Resolve-ComfyRoot {
    param([string]$Candidate)
    if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate)) {
        return $null
    }
    $resolved = (Resolve-Path -LiteralPath $Candidate).Path
    if (Test-Path -LiteralPath (Join-Path $resolved "main.py")) {
        return $resolved
    }
    $nested = Join-Path $resolved "ComfyUI"
    if (Test-Path -LiteralPath (Join-Path $nested "main.py")) {
        return (Resolve-Path -LiteralPath $nested).Path
    }
    return $null
}

function Find-ComfyRoot {
    param([string]$Requested)
    $candidates = @(
        $Requested,
        [Environment]::GetEnvironmentVariable("COMFYUI_ROOT"),
        "C:\ComfyUI",
        "C:\ComfyUI_windows_portable\ComfyUI"
    ) | Where-Object { $_ }
    foreach ($candidate in $candidates) {
        $found = Resolve-ComfyRoot -Candidate $candidate
        if ($found) {
            return $found
        }
    }
    throw "ComfyUI main.py was not found. Pass -ComfyUIRoot with the ComfyUI folder or its parent."
}

function New-UniqueStamp {
    return (Get-Date -Format "yyyyMMdd-HHmmss-fff") + "-" + (
        [Guid]::NewGuid().ToString("N").Substring(0, 8)
    )
}

function Assert-PythonVersion {
    param([string]$Executable)
    if (-not (Test-Path -LiteralPath $Executable)) {
        throw "Python executable does not exist: $Executable"
    }
    & $Executable -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 3)"
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.10+ is required: $Executable"
    }
}

function Assert-ExactChild {
    param([string]$Parent, [string]$Child)
    $parentFull = [System.IO.Path]::GetFullPath($Parent).TrimEnd("\")
    $childFull = [System.IO.Path]::GetFullPath($Child).TrimEnd("\")
    if (-not $childFull.StartsWith($parentFull + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to operate outside the expected parent: $childFull"
    }
}

function ConvertTo-TomlString {
    param([string]$Value)
    return $Value.Replace("\", "\\").Replace('"', '\"')
}

foreach ($required in @($pluginSource, $mcpSource, $skillSource)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Distribution is incomplete; missing: $required"
    }
}

New-Item -ItemType Directory -Force -Path $runtimeRoot, $backupRoot | Out-Null
$comfyRootResolved = Find-ComfyRoot -Requested $ComfyUIRoot
$customNodesRoot = Join-Path $comfyRootResolved "custom_nodes"
if (-not (Test-Path -LiteralPath $customNodesRoot)) {
    throw "custom_nodes does not exist: $customNodesRoot"
}

$pluginTarget = Join-Path $customNodesRoot "ComfyUI-Agent-Bridge"
Assert-ExactChild -Parent $customNodesRoot -Child $pluginTarget
$installStamp = New-UniqueStamp
if (-not $SkipPlugin) {
$pluginStage = Join-Path $customNodesRoot ".ComfyUI-Agent-Bridge-stage-$installStamp"
$pluginBackup = Join-Path $backupRoot "ComfyUI-Agent-Bridge-$installStamp"
Assert-ExactChild -Parent $customNodesRoot -Child $pluginStage
$oldPluginMoved = $false
try {
    Copy-Item -LiteralPath $pluginSource -Destination $pluginStage -Recurse
    foreach ($requiredPluginFile in @(
        "__init__.py",
        "bridge_routes.py",
        "bridge_state.py",
        "web\agent_bridge.js",
        "web\agent_bridge_core.js"
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $pluginStage $requiredPluginFile))) {
            throw "Staged ComfyUI plugin is incomplete: $requiredPluginFile"
        }
    }
    if (Test-Path -LiteralPath $pluginTarget) {
        Move-Item -LiteralPath $pluginTarget -Destination $pluginBackup
        $oldPluginMoved = $true
    }
    Move-Item -LiteralPath $pluginStage -Destination $pluginTarget
} catch {
    if (
        $oldPluginMoved -and
        -not (Test-Path -LiteralPath $pluginTarget) -and
        (Test-Path -LiteralPath $pluginBackup)
    ) {
        Move-Item -LiteralPath $pluginBackup -Destination $pluginTarget
    }
    if (Test-Path -LiteralPath $pluginStage) {
        Move-Item -LiteralPath $pluginStage -Destination (
            Join-Path $backupRoot "failed-plugin-stage-$installStamp"
        )
    }
    throw
}
if ($oldPluginMoved) {
    Write-Host "Existing ComfyUI plugin moved to: $pluginBackup"
}
Write-Host "Installed ComfyUI plugin: $pluginTarget"
} else {
    Write-Host "Skipped ComfyUI plugin; keep the Registry installation."
}

$venvPython = Join-Path $runtimeRoot ".venv\Scripts\python.exe"
$mcpInstallSource = $null
if (-not $SkipMcpEnvironment) {
    if (-not $PythonExe) {
        $portablePython = Join-Path (Split-Path $comfyRootResolved -Parent) "python\python.exe"
        if (Test-Path -LiteralPath $portablePython) {
            $PythonExe = $portablePython
        } else {
            $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
            if (-not $pythonCommand) {
                throw "Python 3.10+ was not found. Pass it with -PythonExe."
            }
            $PythonExe = $pythonCommand.Source
        }
    }
    Assert-PythonVersion -Executable $PythonExe
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & $PythonExe -m venv (Join-Path $runtimeRoot ".venv")
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create the MCP virtual environment."
        }
    }
    Assert-PythonVersion -Executable $venvPython
    $mcpInstallSource = Join-Path $runtimeRoot "mcp-install-source-$installStamp"
    Copy-Item -LiteralPath $mcpSource -Destination $mcpInstallSource -Recurse
    & $venvPython -m pip install --disable-pip-version-check `
        --upgrade $mcpInstallSource
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install the MCP Python package."
    }
    Write-Host "Installed MCP runtime: $venvPython"
} elseif (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Warning "MCP setup was skipped and no installed runtime exists."
}

$configuredCodexRoot = [Environment]::GetEnvironmentVariable("CODEX_HOME")
if (-not $configuredCodexRoot) {
    $configuredCodexRoot = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".codex"
}

if (-not $SkipSkill) {
    $skillsRoot = Join-Path $configuredCodexRoot "skills"
    $skillTarget = Join-Path $skillsRoot "comfyui-live-control"
    New-Item -ItemType Directory -Force -Path $skillsRoot | Out-Null
    $skillStage = Join-Path $skillsRoot ".comfyui-live-control-stage-$installStamp"
    $skillBackup = Join-Path $backupRoot "comfyui-live-control-$installStamp"
    Copy-Item -LiteralPath $skillSource -Destination $skillStage -Recurse
    if (-not (Test-Path -LiteralPath (Join-Path $skillStage "SKILL.md"))) {
        throw "Staged Codex Skill is incomplete."
    }
    if (Test-Path -LiteralPath $skillTarget) {
        Move-Item -LiteralPath $skillTarget -Destination $skillBackup
    }
    try {
        Move-Item -LiteralPath $skillStage -Destination $skillTarget
    } catch {
        if (
            -not (Test-Path -LiteralPath $skillTarget) -and
            (Test-Path -LiteralPath $skillBackup)
        ) {
            Move-Item -LiteralPath $skillBackup -Destination $skillTarget
        }
        throw
    }
    Write-Host "Installed Codex Skill: $skillTarget"
}

if (-not $SkipCodexConfig) {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        throw "Codex MCP configuration needs the installed MCP runtime."
    }
    New-Item -ItemType Directory -Force -Path $configuredCodexRoot | Out-Null
    $configPath = Join-Path $configuredCodexRoot "config.toml"
    if (Test-Path -LiteralPath $configPath) {
        Copy-Item -LiteralPath $configPath -Destination (
            Join-Path $backupRoot "codex-config-$installStamp.toml"
        ) -Force
        $configText = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8
    } else {
        $configText = ""
    }

    $pythonToml = ConvertTo-TomlString -Value $venvPython
    $urlToml = ConvertTo-TomlString -Value $ComfyUIUrl.TrimEnd("/")
    $mcpBlock = @"
[mcp_servers.comfyui_live]
command = "$pythonToml"
args = ["-m", "comfyui_agent_bridge_mcp"]
env = { COMFYUI_URL = "$urlToml" }
startup_timeout_sec = 20
tool_timeout_sec = 120
"@
    $pattern = '(?ms)^\[mcp_servers\.comfyui_live\]\r?\n.*?(?=^\[|\z)'
    if ([regex]::IsMatch($configText, $pattern)) {
        $configText = [regex]::Replace(
            $configText,
            $pattern,
            $mcpBlock.TrimEnd() + "`r`n`r`n"
        )
    } else {
        $configText = $configText.TrimEnd() + "`r`n`r`n" + $mcpBlock.TrimEnd() + "`r`n"
    }
    [System.IO.File]::WriteAllText(
        $configPath,
        $configText,
        [System.Text.UTF8Encoding]::new($false)
    )
    Write-Host "Updated Codex MCP configuration: $configPath"
}

$installState = [ordered]@{
    version = $bridgeVersion
    installed_at = (Get-Date).ToString("o")
    distribution_root = $distributionRoot
    comfyui_root = $comfyRootResolved
    plugin_target = if ($SkipPlugin) { $null } else { $pluginTarget }
    plugin_install_skipped = [bool]$SkipPlugin
    mcp_python = $venvPython
    mcp_install_source = $mcpInstallSource
    comfyui_url = $ComfyUIUrl.TrimEnd("/")
    codex_root = $configuredCodexRoot
}
$installState | ConvertTo-Json | Set-Content -LiteralPath (
    Join-Path $runtimeRoot "install-state.json"
) -Encoding UTF8

Write-Host ""
Write-Host "Installation completed. Fully restart ComfyUI and the agent client."
Write-Host "After restart, open ComfyUI and wait for the Agent Bridge badge."
