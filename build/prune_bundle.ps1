# Strip a built bundle down to runtime-only files, then GUARD against shipping
# anything a corporate DLP scanner would flag (a blocked file makes a released
# zip partly inaccessible on a managed PC).
#
#   powershell -ExecutionPolicy Bypass -File build\prune_bundle.ps1 -Target "dist\TSMIS Branch Identifier"
#   -GuardOnly verifies without deleting.
#
# ASCII only: PowerShell 5.1 parses this BOM-less file as ANSI.

param(
    [Parameter(Mandatory = $true)][string]$Target,
    [switch]$GuardOnly,
    [switch]$Quiet
)
$ErrorActionPreference = "Stop"

function Log($m) { if (-not $Quiet) { Write-Host $m } }

if (-not (Test-Path $Target)) { throw "Target not found: $Target" }
$Target = (Resolve-Path $Target).Path
$internal = Join-Path $Target "_internal"
if (-not (Test-Path $internal)) { throw "Not a built bundle (no _internal): $Target" }

function Test-Luhn([string]$n) {
    $sum = 0; $alt = $false
    for ($i = $n.Length - 1; $i -ge 0; $i--) {
        $d = [int][string]$n[$i]
        if ($alt) { $d *= 2; if ($d -gt 9) { $d -= 9 } }
        $sum += $d; $alt = -not $alt
    }
    return ($sum % 10) -eq 0
}
function Test-CreditCard([string]$n) {
    $len = $n.Length
    $ok = $false
    if ($n -match '^4' -and ($len -eq 13 -or $len -eq 16 -or $len -eq 19)) { $ok = $true }
    elseif ($n -match '^3[47]' -and $len -eq 15) { $ok = $true }
    elseif ($n -match '^5[1-5]' -and $len -eq 16) { $ok = $true }
    elseif ($n -match '^2[2-7]' -and $len -eq 16) { $p = [int]$n.Substring(0, 4); if ($p -ge 2221 -and $p -le 2720) { $ok = $true } }
    elseif ($n -match '^(6011|65|64[4-9])' -and ($len -eq 16 -or $len -eq 19)) { $ok = $true }
    elseif ($n -match '^(30[0-5]|36|38)' -and $len -eq 14) { $ok = $true }
    elseif ($n -match '^35' -and $len -ge 16 -and $len -le 19) { $ok = $true }
    if (-not $ok) { return $false }
    return (Test-Luhn $n)
}

$licenseLike = '(?i)^(license|licence|copying|notice|copyright|third.?party)'

# --- 1. prune --------------------------------------------------------------
if (-not $GuardOnly) {
    $before = (Get-ChildItem $Target -Recurse -File -ErrorAction Ignore | Measure-Object Length -Sum).Sum
    $removed = 0
    # Test suites and type stubs are never imported at runtime.
    Get-ChildItem $internal -Recurse -Directory -ErrorAction Ignore |
        Where-Object { $_.Name -in @("tests", "test") } |
        ForEach-Object { Remove-Item $_.FullName -Recurse -Force; $removed++ }
    Get-ChildItem $internal -Recurse -File -Filter *.pyi -ErrorAction Ignore |
        ForEach-Object { Remove-Item $_.FullName -Force; $removed++ }
    # Third-party prose docs are the DLP surface; license/notice files stay.
    Get-ChildItem $internal -Recurse -File -Include *.md, *.markdown, *.rst -ErrorAction Ignore |
        Where-Object { $_.BaseName -notmatch $licenseLike } |
        ForEach-Object { Remove-Item $_.FullName -Force; $removed++ }
    Get-ChildItem $internal -Recurse -File -ErrorAction Ignore |
        Where-Object { $_.Name -match '(?i)^(readme|changelog|changes|history|authors|contributing|news)(\.|$)' -and $_.BaseName -notmatch $licenseLike } |
        ForEach-Object { Remove-Item $_.FullName -Force; $removed++ }
    # dist-info METADATA embeds each package's whole README; keep the headers only.
    Get-ChildItem $internal -Recurse -File -Filter "METADATA" -ErrorAction Ignore |
        Where-Object { $_.Directory.Name -like "*.dist-info" } |
        ForEach-Object {
            $lines = [System.IO.File]::ReadAllLines($_.FullName)
            $end = [Array]::IndexOf($lines, "")
            if ($end -gt 0 -and $end -lt $lines.Length - 1) {
                [System.IO.File]::WriteAllLines($_.FullName, $lines[0..($end - 1)])
                $removed++
            }
        }
    $after = (Get-ChildItem $Target -Recurse -File -ErrorAction Ignore | Measure-Object Length -Sum).Sum
    Log ("==> Pruned {0} item(s), reclaimed {1:N1} MB" -f $removed, (($before - $after) / 1MB))
}

# --- 2. guard --------------------------------------------------------------
$leftoverDocs = Get-ChildItem $Target -Recurse -File -Include *.md, *.markdown, *.rst -ErrorAction Ignore |
    Where-Object { $_.BaseName -notmatch $licenseLike }
if ($leftoverDocs) {
    throw "GUARD FAILED: documentation still bundled:`n  " + (($leftoverDocs.FullName) -join "`n  ")
}
$textExt = @('.md', '.rst', '.txt', '.json', '.yml', '.yaml', '.js', '.html', '.css', '.xml', '.csv', '.cfg', '.ini', '.toml', '.py', '.pem', '.crt', '.key')
$rxCard = [regex]'(?<![0-9A-Za-z])[0-9](?:[ -]?[0-9]){12,18}(?![0-9A-Za-z])'
$rxKey  = [regex]'-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----'
$rxAws  = [regex]'\bAKIA[0-9A-Z]{16}\b'
$rxSsn  = [regex]'(?<![0-9-])(?!000|666|9[0-9]{2})[0-9]{3}-(?!00)[0-9]{2}-(?!0000)[0-9]{4}(?![0-9-])'
$hits = New-Object System.Collections.Generic.List[string]
Get-ChildItem $Target -Recurse -File -ErrorAction Ignore | ForEach-Object {
    $f = $_
    if (-not (($textExt -contains $f.Extension.ToLower()) -or ($f.Name -eq 'METADATA'))) { return }
    try { $text = [System.IO.File]::ReadAllText($f.FullName) } catch { return }
    $rel = $f.FullName.Substring($Target.Length + 1)
    foreach ($m in $rxCard.Matches($text)) {
        if (Test-CreditCard ($m.Value -replace '[^0-9]', '')) { $hits.Add("  [credit-card] $rel"); break }
    }
    if ($rxKey.IsMatch($text)) { $hits.Add("  [private-key] $rel") }
    if ($rxAws.IsMatch($text)) { $hits.Add("  [aws-key]     $rel") }
    if ($rxSsn.IsMatch($text)) { $hits.Add("  [us-ssn]      $rel") }
}
if ($hits.Count -gt 0) {
    throw ("GUARD FAILED: sensitive data found in the bundle:`n" + (($hits | Select-Object -Unique) -join "`n"))
}
Log "==> Guard passed: no docs and no credit-card / private-key / AWS-key / SSN content in the bundle."
