# Install ClaudeLight hooks into %USERPROFILE%\.claude\settings.json
# Preserves existing hooks (e.g., oh-my-claudecode).

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Hook = Join-Path $Root "claude_light_hook.py"
$SettingsDir = Join-Path $env:USERPROFILE ".claude"
$SettingsJson = Join-Path $SettingsDir "settings.json"

if (-not (Test-Path $SettingsDir)) {
    New-Item -ItemType Directory -Path $SettingsDir | Out-Null
}

# Resolve full path for hook command
$HookFullPath = (Resolve-Path $Hook).Path

# Use python (not python3) on Windows
$PythonCmd = "python"
$Command = "$PythonCmd `"$HookFullPath`""

$Events = @(
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "Notification"
)

# Read or init settings.json
if (Test-Path $SettingsJson) {
    $Data = Get-Content $SettingsJson -Raw | ConvertFrom-Json
    $Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $Backup = "$SettingsJson.bak-$Timestamp"
    Copy-Item $SettingsJson $Backup
} else {
    $Data = @{}
    $Backup = $null
}

# Ensure hooks property exists
if (-not ($Data.PSObject.Properties.Name -contains "hooks")) {
    $Data | Add-Member -NotePropertyName "hooks" -NotePropertyValue @{} -Force
}

foreach ($Event in $Events) {
    if (-not ($Data.hooks.PSObject.Properties.Name -contains $Event)) {
        $Data.hooks | Add-Member -NotePropertyName $Event -NotePropertyValue @() -Force
    }

    $Entries = $Data.hooks.$Event
    $Found = $false

    foreach ($Entry in $Entries) {
        if ($Entry.hooks) {
            foreach ($Item in $Entry.hooks) {
                if ($Item.command -eq $Command) {
                    $Found = $true
                    break
                }
            }
        }
        if ($Found) { break }
    }

    if (-not $Found) {
        $NewEntry = @{
            matcher = ""
            hooks = @(
                @{
                    type = "command"
                    command = $Command
                    timeout = 2
                }
            )
        }

        # ConvertTo-Json depth must be deep enough for nested structure
        $Entries += $NewEntry
        $Data.hooks.$Event = $Entries
    }
}

# Write back — preserve formatting
$Output = $Data | ConvertTo-Json -Depth 10
# ConvertTo-Json indents with 4 spaces; normalize to 2
$Output = $Output -replace '(?m)^    ', '  '
Set-Content -Path $SettingsJson -Value $Output -Encoding UTF8

Write-Host ""
Write-Host "Installed ClaudeLight hook: $Command"
if ($Backup) {
    Write-Host "Backup: $Backup"
}
Write-Host "Updated: $SettingsJson"

Write-Host ""
Write-Host "ClaudeLight installed! Next steps:"
Write-Host ""
Write-Host "1. Start the state server:"
Write-Host "   python $Root\claude_light_state.py serve"
Write-Host ""
Write-Host "2. Or start the desktop app (auto-starts server):"
Write-Host "   python $Root\claude_light_app.py"
Write-Host ""
Write-Host "3. For VSCode, install the extension:"
Write-Host "   cd $Root\claude-light-vscode; npm install; npm run compile"
Write-Host "   Then: code --install-extension $Root\claude-light-vscode"
Write-Host ""
Write-Host "4. Verify hook:"
Write-Host "   echo '{"session_id":"test","hook_event_name":"PreToolUse","tool_name":"Bash"}' | python $HookFullPath"
Write-Host ""
Write-Host "Log: $env:LOCALAPPDATA\claude-light\hook.log"