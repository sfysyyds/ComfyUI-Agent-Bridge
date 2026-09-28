[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [string]$NodeExe = "",
    [switch]$IncludeLiveDiagnostics
)

$ErrorActionPreference = "Stop"
$agentRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$distributionRoot = (Resolve-Path -LiteralPath (Join-Path $agentRoot "..")).Path
$comfyPlugin = Join-Path $distributionRoot "comfyui-plugin"
$env:PYTHONDONTWRITEBYTECODE = "1"

if (-not $PythonExe) {
    $PythonExe = [Environment]::GetEnvironmentVariable("COMFYUI_PYTHON")
}
if (-not $PythonExe) {
    $PythonExe = (Get-Command python.exe -ErrorAction Stop).Source
}
if (-not $NodeExe) {
    $nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($nodeCommand) {
        $NodeExe = $nodeCommand.Source
    }
}

& $PythonExe -m unittest discover -s (Join-Path $agentRoot "tests") -p "test_*.py" -v
if ($LASTEXITCODE -ne 0) {
    throw "Python tests failed."
}

if ($NodeExe -and (Test-Path -LiteralPath $NodeExe)) {
    & $NodeExe --test (Join-Path $agentRoot "tests\test_bridge_core.mjs")
    if ($LASTEXITCODE -ne 0) {
        throw "JavaScript tests failed."
    }
    foreach ($script in Get-ChildItem -LiteralPath (Join-Path $comfyPlugin "web") -Filter "*.js") {
        & $NodeExe --check $script.FullName
        if ($LASTEXITCODE -ne 0) {
            throw "JavaScript syntax check failed: $($script.Name)"
        }
    }
} else {
    Write-Warning "Node.js was not found; JavaScript checks were skipped."
}

& $PythonExe -c @"
import ast
from pathlib import Path
roots = [Path(r'$($comfyPlugin.Replace("\", "/"))'), Path(r'$((Join-Path $agentRoot "mcp_server\src").Replace("\", "/"))')]
for root in roots:
    for path in root.rglob('*.py'):
        ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
"@
if ($LASTEXITCODE -ne 0) {
    throw "Python syntax validation failed."
}

$powerShellParseErrors = @()
foreach ($script in Get-ChildItem -LiteralPath (Join-Path $agentRoot "scripts") -Filter "*.ps1") {
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $script.FullName,
        [ref]$tokens,
        [ref]$errors
    )
    if ($errors) {
        $powerShellParseErrors += @($errors | ForEach-Object {
            "$($script.Name): $($_.Message)"
        })
    }
}
if ($powerShellParseErrors) {
    throw "PowerShell syntax validation failed: $($powerShellParseErrors -join '; ')"
}

if ($IncludeLiveDiagnostics) {
    & (Join-Path $PSScriptRoot "diagnose.ps1")
}

& $PythonExe (Join-Path $agentRoot "tests\smoke_mcp.py") `
    --offline `
    --python $PythonExe
if ($LASTEXITCODE -ne 0) {
    throw "MCP stdio contract test failed."
}

Write-Host "All offline tests passed."
