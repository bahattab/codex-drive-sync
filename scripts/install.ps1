param([string]$Destination = "$env:LOCALAPPDATA\Programs\codex-drive-sync", [switch]$EnableStartup)
$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $PSScriptRoot
$destinationFull = [IO.Path]::GetFullPath($Destination)
if ($destinationFull -eq [IO.Path]::GetPathRoot($destinationFull)) { throw 'لا يمكن تثبيت البرنامج في جذر القرص.' }
for ($check = $destinationFull; $check; $check = Split-Path -Parent $check) {
    if ((Test-Path -LiteralPath $check) -and ((Get-Item -LiteralPath $check).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'مسار التثبيت يتضمن رابطًا؛ ألغيت العملية.' }
    if ($check -eq [IO.Path]::GetPathRoot($check)) { break }
}
if ((Test-Path -LiteralPath $destinationFull) -and (Get-ChildItem -LiteralPath $destinationFull -Force) -and -not (Test-Path -LiteralPath (Join-Path $destinationFull '.installation-marker'))) { throw 'المجلد غير فارغ ولا يخص تثبيت البرنامج.' }
New-Item -ItemType Directory -Force -Path $destinationFull | Out-Null
$venv = Join-Path $destinationFull 'venv'
if (Test-Path -LiteralPath $venv) {
    if ((Get-Item -LiteralPath $venv).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'بيئة تثبيت مرتبطة؛ ألغيت العملية.' }
    if ((Get-ChildItem -LiteralPath $venv -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })) { throw 'بيئة تثبيت تتضمن روابط؛ ألغيت العملية.' }
}
if (Test-Path -LiteralPath (Join-Path $destinationFull '.installation-marker')) {
    $existingPython = Join-Path $venv 'Scripts\python.exe'
    if (Test-Path -LiteralPath $existingPython) {
        & $existingPython -m codex_drive_sync.cli service stop
        if ($LASTEXITCODE -ne 0) { throw 'العامل لم يتوقف؛ ألغيت إعادة التثبيت.' }
    }
}
python -m venv $venv
if ($LASTEXITCODE -ne 0) { throw 'تعذر إنشاء بيئة Python.' }
$python = Join-Path $venv 'Scripts\python.exe'
& $python -m pip install -r (Join-Path $source 'requirements.lock')
if ($LASTEXITCODE -ne 0) { throw 'تعذر تثبيت الاعتماديات.' }
& $python -m pip install --no-deps $source
if ($LASTEXITCODE -ne 0) { throw 'تعذر تثبيت الحزمة.' }
$launcher = Join-Path $destinationFull 'codex-drive-sync.cmd'
@"
@echo off
"$python" -m codex_drive_sync.cli %*
"@ | Set-Content -LiteralPath $launcher -Encoding utf8
'codex-drive-sync-install-v1' | Set-Content -LiteralPath (Join-Path $destinationFull '.installation-marker')
$pluginSource = Join-Path $source 'plugin'
$pluginTarget = Join-Path $destinationFull 'plugin'
if (Test-Path -LiteralPath $pluginTarget) {
    if ((Get-Item -LiteralPath $pluginTarget).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'مجلد الإضافة مرتبط؛ ألغيت العملية.' }
    if ((Get-ChildItem -LiteralPath $pluginTarget -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })) { throw 'الإضافة تتضمن روابط.' }
}
New-Item -ItemType Directory -Force -Path $pluginTarget | Out-Null
Copy-Item -Path (Join-Path $pluginSource '*') -Destination $pluginTarget -Recurse -Force
# Copy dot directories explicitly, and bind stdio to the installed interpreter.
Copy-Item -LiteralPath (Join-Path $pluginSource '.codex-plugin') -Destination $pluginTarget -Recurse -Force
$mcp = @{ mcpServers = @{ 'codex-drive-sync' = @{ command = $python; args = @('-m','codex_drive_sync.cli','mcp') } } }
$mcp | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $pluginTarget '.mcp.json') -Encoding utf8
$portable = @{ '$schema' = 'https://agent-plugins.org/schemas/1.0.0/mcp.schema.json'; mcpServers = @{ 'codex-drive-sync' = @{ type='stdio'; command=$python; args=@('-m','codex_drive_sync.cli','mcp') } } }
$portable | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $pluginTarget 'mcp.json') -Encoding utf8
$marketplacePath = Join-Path $destinationFull '.agents\plugins'
New-Item -ItemType Directory -Force -Path $marketplacePath | Out-Null
Copy-Item -LiteralPath (Join-Path $source '.agents\plugins\marketplace.json') -Destination (Join-Path $marketplacePath 'marketplace.json') -Force
if ($EnableStartup) {
    $action = New-ScheduledTaskAction -Execute $python -Argument '-m codex_drive_sync.cli service run'
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
    Register-ScheduledTask -TaskName 'CodexDriveSync' -Action $action -Trigger $trigger -Description 'User-selected encrypted project synchronization' | Out-Null
}
Write-Output "التثبيت ناجح. شغّل: `"$launcher`" doctor"
Write-Output "الإضافة المعدة محليًا: $pluginTarget . راجع codex plugin add --help قبل تثبيتها؛ لم تُثبت تلقائيًا."
Write-Output "لتثبيت الإضافة بعد مراجعتها: codex plugin marketplace add `"$destinationFull`" ثم codex plugin add codex-drive-sync@codex-drive-sync-local"
