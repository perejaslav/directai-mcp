<#
.SYNOPSIS
  Сверяет версию DirectAI MCP с версиями, на которых проверены скиллы семейства.

.DESCRIPTION
  Каждый скилл семейства объявляет в frontmatter поле metadata.directai_mcp —
  версию сервера, на которой его проверяли. Скрипт читает это поле, берёт
  реальную версию из `directai-mcp --version` и сравнивает.

  Вердикты:
    OK    — совпало, работаем как обычно.
    WARN  — сервер новее скиллов: могли появиться новые действия или поля.
            Перед записью перепроверить describe_action, не доверять слепо.
    FAIL  — сервер старее скиллов: часть действий может отсутствовать.
            Выполнять write-сценарии нельзя.

  Только чтение: ни сети, ни вызовов API, ни записи файлов.

.PARAMETER SkillsRoot
  Папка со скиллами. По умолчанию — два уровня выше scripts/.

.PARAMETER ServerVersion
  Версия сервера строкой, если нет доступа к exe (например, «1.17.2»).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/check-versions.ps1
#>
param(
    [string]$SkillsRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [string]$ServerVersion = ''
)

$ErrorActionPreference = 'Stop'

function Get-DeclaredVersion {
    param([string]$Path)
    $text = Get-Content -Path $Path -Raw -Encoding UTF8
    $front = [regex]::Match($text, '(?s)\A---\r?\n(.*?)\r?\n---')
    if (-not $front.Success) { return $null }
    $m = [regex]::Match($front.Groups[1].Value, '(?m)^\s*directai_mcp:\s*"?([0-9]+(?:\.[0-9]+){1,3})"?\s*$')
    if ($m.Success) { return $m.Groups[1].Value }
    return $null
}

if (-not (Test-Path $SkillsRoot)) {
    Write-Output "FAIL: папка скиллов не найдена: $SkillsRoot"
    exit 3
}

if (-not $ServerVersion) {
    $exe = Get-Command directai-mcp -ErrorAction SilentlyContinue
    if (-not $exe) {
        Write-Output 'FAIL: directai-mcp не найден в PATH. Версию сервера проверить нечем.'
        exit 3
    }
    $raw = (& $exe.Source --version 2>&1 | Out-String)
    $m = [regex]::Match($raw, '([0-9]+\.[0-9]+\.[0-9]+)')
    if (-not $m.Success) {
        Write-Output "FAIL: не удалось разобрать версию из вывода: $($raw.Trim())"
        exit 3
    }
    $ServerVersion = $m.Groups[1].Value
}

$server = [version]$ServerVersion
$rows = @()
$worst = 0

foreach ($dir in Get-ChildItem -Path $SkillsRoot -Directory) {
    $skill = Join-Path $dir.FullName 'SKILL.md'
    if (-not (Test-Path $skill)) { continue }
    $declared = Get-DeclaredVersion -Path $skill
    if (-not $declared) { continue }

    $ver = [version]$declared
    if ($ver -eq $server) {
        $verdict = 'OK'
    }
    elseif ($server -gt $ver) {
        $verdict = 'WARN'
        if ($worst -lt 2) { $worst = 2 }
    }
    else {
        $verdict = 'FAIL'
        $worst = 1
    }
    $rows += [pscustomobject]@{ Skill = $dir.Name; Declared = $declared; Verdict = $verdict }
}

if ($rows.Count -eq 0) {
    Write-Output 'FAIL: ни один скилл не объявил metadata.directai_mcp.'
    exit 3
}

Write-Output "Сервер: $ServerVersion"
$rows | Sort-Object Skill | Format-Table -AutoSize | Out-String -Width 120 | Write-Output

if ($worst -eq 0) {
    Write-Output 'Итог: OK — версии сходятся.'
    exit 0
}
if ($worst -eq 2) {
    Write-Output 'Итог: WARN — сервер новее скиллов. Перед записью перепроверить describe_action.'
    exit 2
}
Write-Output 'Итог: FAIL — сервер старее скиллов. Write-сценарии не выполнять, сначала обновить сервер.'
exit 1
