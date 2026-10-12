param([string]$Destination = "$env:LOCALAPPDATA\Programs\codex-drive-sync")
$ErrorActionPreference = 'Stop'
$target = [IO.Path]::GetFullPath($Destination)
for ($check=$target; $check; $check=Split-Path -Parent $check) {
    if ((Test-Path -LiteralPath $check) -and ((Get-Item -LiteralPath $check).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'مسار الإزالة يتضمن رابطًا؛ ألغيت العملية.' }
    if ($check -eq [IO.Path]::GetPathRoot($check)) { break }
}
$marker = Join-Path $target '.installation-marker'
if (-not (Test-Path -LiteralPath $marker) -or (Get-Content -LiteralPath $marker -Raw).Trim() -ne 'codex-drive-sync-install-v1') { throw 'لا توجد علامة تثبيت موثوقة؛ ألغيت الإزالة.' }
$venv = [IO.Path]::GetFullPath((Join-Path $target 'venv'))
if (-not $venv.StartsWith($target + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'مسار إزالة غير آمن.' }
if (Test-Path -LiteralPath $venv) {
    if ((Get-Item -LiteralPath $venv).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'جذر بيئة التثبيت مرتبط؛ ألغيت الإزالة.' }
    if ((Get-ChildItem -LiteralPath $venv -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })) { throw 'بيئة التثبيت تتضمن رابطًا؛ ألغيت الإزالة.' }
}
$python = Join-Path $target 'venv\Scripts\python.exe'
if (Test-Path -LiteralPath $python) {
    & $python -m codex_drive_sync.cli service stop
    if ($LASTEXITCODE -ne 0) { throw 'لم يتأكد توقف الخدمة؛ لم يُحذف البرنامج.' }
}
$task = Get-ScheduledTask -TaskName 'CodexDriveSync' -ErrorAction SilentlyContinue
if ($task -and $task.Actions.Execute -eq $python) { Unregister-ScheduledTask -TaskName 'CodexDriveSync' -Confirm:$false }
# Remove only the dedicated venv after checking its resolved absolute location.
$venv = [IO.Path]::GetFullPath((Join-Path $target 'venv'))
if (-not $venv.StartsWith($target + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'مسار إزالة غير آمن.' }
if (Test-Path -LiteralPath $venv) {
    if ((Get-Item -LiteralPath $venv).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'جذر بيئة التثبيت مرتبط؛ ألغيت الإزالة.' }
    if ((Get-ChildItem -LiteralPath $venv -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })) { throw 'بيئة التثبيت تتضمن رابطًا؛ ألغيت الإزالة.' }
    Remove-Item -LiteralPath $venv -Recurse -Force
}
# Leave the configured plugin definition for manual Codex removal, avoiding live app mutation.
foreach ($name in @('codex-drive-sync.cmd','.installation-marker')) { Remove-Item -LiteralPath (Join-Path $target $name) -Force -ErrorAction SilentlyContinue }
Write-Output 'أزيل البرنامج. بقيت المشاريع واللقطات والنسخ الاحتياطية والمفاتيح المحلية محفوظة.'
