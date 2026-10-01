param(
    [switch]$SetupOnly,
    [switch]$CheckOnly,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ClientArguments
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$AlStyleConfig = ConvertFrom-StringData (Get-Content -LiteralPath "$PSScriptRoot/../assets/toolchain.conf" -Raw)
$AlStyleState = if ($env:ALSTYLE_CLIENT_STATE) { $env:ALSTYLE_CLIENT_STATE } else {
    Join-Path ([Environment]::GetFolderPath('UserProfile')) '.al-style-deploy'
}

function Test-AlStylePython([string]$Executable) {
    if (-not $Executable -or -not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $null }
    # Microsoft Store aliases can open a store window; skip the aliases.
    if ($Executable -match '[\\/]Microsoft[\\/]WindowsApps[\\/]python[^\\/]*\.exe$') { return $null }
    try {
        $output = & $Executable -I -c 'import sys, ssl; sys.exit(1) if sys.version_info < (3, 12) else print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $output) { return ([string]$output).Trim() }
    } catch { return $null }
    return $null
}

function Find-AlStylePython {
    $pointer = Join-Path $AlStyleState 'python-path.txt'
    if (Test-Path -LiteralPath $pointer) {
        $found = Test-AlStylePython (Get-Content -LiteralPath $pointer -Raw).Trim()
        if ($found) { return $found }
    }
    foreach ($name in @('python3', 'python3.14', 'python3.13', 'python3.12', 'python')) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) {
            $found = Test-AlStylePython $command.Source
            if ($found) { return $found }
        }
    }
    $launcher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher) {
        try {
            # List installed runtimes only. `py -3` can auto-install a runtime with the new manager.
            $inventory = & $launcher.Source -0p 2>$null
            if ($LASTEXITCODE -eq 0) {
                foreach ($entry in $inventory) {
                    if ($entry -match '\s((?:[a-z]:\\|\\\\).+\.exe)\s*$') {
                        $found = Test-AlStylePython $Matches[1]
                        if ($found) { return $found }
                    }
                }
            }
        } catch { return $null }
    }
    return $null
}

function Find-AlStyleGit {
    $command = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $candidates = @()
    if ($command) { $candidates += $command.Source }
    foreach ($directory in @(Get-ChildItem -LiteralPath (Join-Path $AlStyleState 'tools') -Directory -Filter 'git-*' -ErrorAction SilentlyContinue)) {
        $candidates += Join-Path $directory.FullName 'cmd/git.exe'
    }
    foreach ($candidate in $candidates) {
        try {
            if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
            $null = & $candidate --version 2>$null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch { continue }
    }
    return $null
}

function Get-AlStyleArchitecture {
    $architecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    switch ($architecture) {
        'ARM64' { return 'aarch64' }
        'AMD64' { return 'x86_64' }
        default { throw 'Automatic Windows setup supports x64 and ARM64; ask IT about this architecture.' }
    }
}

function Expand-AlStyleDownload([string]$Url, [string]$Asset, [string]$Destination) {
    $expected = $AlStyleConfig["sha256.$Asset"]
    if ($expected -notmatch '^[a-f0-9]{64}$') { throw "No pinned checksum for $Asset; installation stopped." }
    $temporary = Join-Path $AlStyleState ('.download-' + [Guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $temporary -Force
    try {
        $archive = Join-Path $temporary $Asset
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $Url -OutFile $archive -UseBasicParsing -TimeoutSec 600
        $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { throw "Checksum mismatch for $Asset; installation stopped." }
        # Publish the directory only after a complete extraction, so failed downloads can be retried.
        $extracted = Join-Path $temporary 'extracted'
        Expand-Archive -LiteralPath $archive -DestinationPath $extracted
        if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Recurse -Force }
        Move-Item -LiteralPath $extracted -Destination $Destination
    } finally {
        Remove-Item -LiteralPath $temporary -Recurse -Force
    }
}

function Install-AlStyleGit {
    $architecture = Get-AlStyleArchitecture
    $suffix = if ($architecture -eq 'aarch64') { 'arm64' } else { '64-bit' }
    $version = $AlStyleConfig.git_version
    $directory = Join-Path $AlStyleState "tools/git-$version-$suffix"
    $executable = Join-Path $directory 'cmd/git.exe'
    if (-not (Test-Path -LiteralPath $executable)) {
        $asset = "MinGit-$version-$suffix.zip"
        Write-Host 'Installing private Git for AL-STYLE deployment...'
        Expand-AlStyleDownload "https://github.com/git-for-windows/git/releases/download/$($AlStyleConfig.git_release)/$asset" $asset $directory
    }
    $env:PATH = (Join-Path $directory 'cmd') + [IO.Path]::PathSeparator + $env:PATH
}

function Install-AlStylePython {
    $architecture = Get-AlStyleArchitecture
    $version = $AlStyleConfig.uv_version
    $directory = Join-Path $AlStyleState "tools/uv-$version-$architecture"
    $executable = Join-Path $directory 'uv.exe'
    if (-not (Test-Path -LiteralPath $executable)) {
        $asset = "uv-$architecture-pc-windows-msvc.zip"
        Expand-AlStyleDownload "https://github.com/astral-sh/uv/releases/download/$version/$asset" $asset $directory
    }
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $AlStyleState 'python'
    Write-Host 'Installing a private Python runtime for AL-STYLE deployment...'
    & $executable --no-config python install --no-bin --no-registry 3.13
    if ($LASTEXITCODE -ne 0) { throw 'Python installation failed; no deployment was started.' }
    $found = & $executable --no-config python find --managed-python 3.13
    if ($LASTEXITCODE -ne 0) { throw 'Python runtime discovery failed; no deployment was started.' }
    $python = Test-AlStylePython ([string]$found).Trim()
    if (-not $python) { throw 'Python installation did not produce a working Python 3.12+ runtime.' }
    Set-Content -LiteralPath (Join-Path $AlStyleState 'python-path.txt') -Value $python -Encoding UTF8
}

function Initialize-AlStyleRuntime([switch]$ReadOnly) {
    $python = Find-AlStylePython
    $git = Find-AlStyleGit
    if ($ReadOnly -and (-not $python -or -not $git)) {
        throw 'Setup needed: Python 3.12+ or Git is missing. Rerun without -CheckOnly to install it.'
    }
    if (-not $ReadOnly) {
        $null = New-Item -ItemType Directory -Path (Join-Path $AlStyleState 'tools') -Force
        if (-not $git) { Install-AlStyleGit; $git = Find-AlStyleGit }
        if (-not $git) { throw 'Git installation did not finish; no deployment was started.' }
        if (-not $python) { Install-AlStylePython; $python = Find-AlStylePython }
        if (-not $python) { throw 'Python installation did not finish; no deployment was started.' }
    }
    return @{ Python = $python; Git = $git }
}

# Dot-sourcing exposes the helpers for operator checks; normal invocation runs the client.
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $runtime = Initialize-AlStyleRuntime -ReadOnly:$CheckOnly
        Write-Host 'Ready: Python 3.12+ and Git verified.'
        if ($SetupOnly -or $CheckOnly) { exit 0 }
        $env:PATH = (Split-Path -Parent $runtime.Git) + [IO.Path]::PathSeparator + $env:PATH
        & $runtime.Python -I (Join-Path $PSScriptRoot 'alstyle.py') @ClientArguments
        exit $LASTEXITCODE
    } catch {
        Write-Error $_ -ErrorAction Continue
        exit 1
    }
}
