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
    # --- Runtime pieces this app never loads on Windows 10/11 ------------------
    # (a) The Universal C Runtime forwarders + ucrtbase are part of Windows 10+
    #     itself; PyInstaller bundles them for Windows 7/8. VCRUNTIME140.dll stays
    #     (it is the VC++ redistributable, not in-box).
    Get-ChildItem $internal -File -Filter "api-ms-win-*.dll" -ErrorAction Ignore |
        ForEach-Object { Remove-Item $_.FullName -Force; $removed++ }
    $ucrt = Join-Path $internal "ucrtbase.dll"
    if (Test-Path $ucrt) { Remove-Item $ucrt -Force; $removed++ }
    # (b) pythonnet's netstandard facade assemblies (System.*.dll, netstandard.dll)
    #     are NuGet shims for .NET Framework older than 4.7.2. pythonnet 3 itself
    #     needs 4.7.2+, whose GAC provides netstandard 2.0, so on any host that can
    #     run the app they are never loaded. Python.Runtime.dll (+ its deps.json) stays.
    $pnrt = Join-Path $internal "pythonnet\runtime"
    Get-ChildItem $pnrt -File -ErrorAction Ignore |
        Where-Object { $_.Name -notin @("Python.Runtime.dll", "Python.Runtime.deps.json") } |
        ForEach-Object { Remove-Item $_.FullName -Force; $removed++ }
    # (c) pywebview pieces for other backends/platforms: the legacy MSHTML interop
    #     and the Android jar. The edgechromium backend loads only WebView2.Core +
    #     WebView2.WinForms + the x64 WebView2Loader -- but at import it adds ALL
    #     THREE runtimes\win-* folders to PATH and raises FileNotFoundError if one
    #     is missing (the v0.1.1 frozen self-test caught exactly that), so the
    #     x86 / arm64 loaders (two small files) stay.
    $wvlib = Join-Path $internal "webview\lib"
    foreach ($n in @("WebBrowserInterop.x64.dll", "WebBrowserInterop.x86.dll", "pywebview-android.jar")) {
        $p = Join-Path $wvlib $n
        if (Test-Path $p) { Remove-Item $p -Force; $removed++ }
    }
    # (d) clr_loader's 32-bit loader and every debug-symbol file.
    $clr32 = Join-Path $internal "clr_loader\ffi\dlls\x86"
    if (Test-Path $clr32) { Remove-Item $clr32 -Recurse -Force; $removed++ }
    Get-ChildItem $internal -Recurse -File -Filter *.pdb -ErrorAction Ignore |
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
# The load-bearing files must survive the prune: a pywebview / pythonnet layout
# change must fail HERE, not on a user's first double-click.
$loadBearing = @(
    "python311.dll", "VCRUNTIME140.dll", "ui\index.html",
    "pythonnet\runtime\Python.Runtime.dll",
    "clr_loader\ffi\dlls\amd64\ClrLoader.dll",
    "webview\lib\Microsoft.Web.WebView2.Core.dll",
    "webview\lib\Microsoft.Web.WebView2.WinForms.dll",
    "webview\lib\runtimes\win-x64\native\WebView2Loader.dll",
    "webview\lib\runtimes\win-x86\native\WebView2Loader.dll",
    "webview\lib\runtimes\win-arm64\native\WebView2Loader.dll"
)
$missing = $loadBearing | Where-Object { -not (Test-Path (Join-Path $internal $_)) }
if ($missing) {
    throw "GUARD FAILED: load-bearing file(s) missing after the prune:`n  " + ($missing -join "`n  ")
}
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
