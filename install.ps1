param(
    [ValidateSet('codex', 'claude')][string]$Agent,
    [string]$Destination,
    [string]$Ref
)

$ErrorActionPreference = 'Stop'
$AlStyleRepository = 'alstyle-vibecoding/al-style-deploy'

function Get-AlStyleSkillFile([string]$Url, [string]$Path) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Path -TimeoutSec 120
}

function Install-AlStyleSkill([string]$SelectedAgent, [string]$Target, [string]$Revision) {
    if ($SelectedAgent -notin @('codex', 'claude')) { throw '-Agent codex or -Agent claude is required.' }
    if (-not $Target) {
        $folder = if ($SelectedAgent -eq 'codex') { '.agents/skills' } else { '.claude/skills' }
        $Target = Join-Path ([Environment]::GetFolderPath('UserProfile')) "$folder/al-style-deploy"
    }
    if (-not [IO.Path]::IsPathRooted($Target)) { throw 'Destination must be an absolute path.' }
    $Target = [IO.Path]::GetFullPath($Target)
    if ([IO.Path]::GetFileName($Target) -ne 'al-style-deploy') { throw 'Destination must end in al-style-deploy.' }
    if (Test-Path -LiteralPath $Target) {
        $existing = Get-Item -LiteralPath $Target -Force
        if ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing to replace a symbolic link.' }
        $identity = Join-Path $Target 'SKILL.md'
        if (-not (Test-Path -LiteralPath $identity -PathType Leaf) -or
            -not (Select-String -LiteralPath $identity -Pattern '^name: al-style-deploy$' -Quiet)) {
            throw 'Destination contains a different skill; it was not changed.'
        }
    }
    $parent = [IO.Path]::GetDirectoryName($Target)
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $lock = Join-Path $parent '.al-style-deploy.install-lock'
    # FileShare.None prevents two installers from publishing to the same directory.
    $guard = [IO.File]::Open($lock, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $stage = Join-Path $parent ('.al-style-deploy-download-' + [Guid]::NewGuid().ToString('N'))
    $backup = $null
    try {
        [IO.Directory]::CreateDirectory($stage) | Out-Null
        if (-not $Revision) {
            $responsePath = Join-Path $stage 'ref.json'
            Get-AlStyleSkillFile "https://api.github.com/repos/$AlStyleRepository/git/ref/heads/main" $responsePath
            $Revision = (Get-Content -LiteralPath $responsePath -Raw | ConvertFrom-Json).object.sha
        }
        if ($Revision -cnotmatch '^[a-f0-9]{40}$') { throw 'GitHub did not return a valid commit.' }
        $base = "https://raw.githubusercontent.com/$AlStyleRepository/$Revision"
        $manifestPath = Join-Path $stage 'manifest'
        Get-AlStyleSkillFile "$base/skill-files.sha256" $manifestPath
        $skill = Join-Path $stage 'skill'
        [IO.Directory]::CreateDirectory($skill) | Out-Null
        $allowed = @('SKILL.md', 'scripts/alstyle.py', 'scripts/run.sh', 'scripts/run.ps1',
            'scripts/install-from-github.sh', 'scripts/install-from-github.ps1',
            'assets/toolchain.conf', 'assets/static.Dockerfile', 'references/setup.md',
            'references/contract.md', 'references/install.md')
        $seen = @{}
        foreach ($entry in (Get-Content -LiteralPath $manifestPath)) {
            if ($entry -cnotmatch '^([a-f0-9]{64})  skills/al-style-deploy/(.+)$') { throw 'Invalid file manifest.' }
            $expected, $relative = $Matches[1], $Matches[2]
            if ($relative -cnotin $allowed -or $seen.ContainsKey($relative)) { throw 'Unexpected or duplicate skill file.' }
            $seen[$relative] = $true
            $path = Join-Path $skill $relative
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($path)) | Out-Null
            Get-AlStyleSkillFile "$base/skills/al-style-deploy/$relative" $path
            if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $expected) {
                throw "Checksum mismatch: $relative. Existing skill was not changed."
            }
        }
        if ($seen.Count -ne $allowed.Count) { throw 'Incomplete skill package.' }
        if (-not (Select-String -LiteralPath (Join-Path $skill 'SKILL.md') -Pattern '^name: al-style-deploy$' -Quiet)) {
            throw 'Unexpected skill identity.'
        }
        [IO.File]::WriteAllText((Join-Path $skill '.github-source'), "$AlStyleRepository`n$Revision`n", [Text.UTF8Encoding]::new($false))
        if (Test-Path -LiteralPath $Target) {
            $backupRoot = Join-Path ([IO.Path]::GetDirectoryName($parent)) '.al-style-deploy-backups'
            [IO.Directory]::CreateDirectory($backupRoot) | Out-Null
            $backup = Join-Path $backupRoot ('al-style-deploy-' + [Guid]::NewGuid().ToString('N'))
            [IO.Directory]::Move($Target, $backup)
        }
        [IO.Directory]::Move($skill, $Target)
        Write-Output "Installed for ${SelectedAgent}: $Target"
        Write-Output "GitHub commit: $Revision"
        if ($backup) { Write-Output "Previous skill saved: $backup" }
        Write-Output 'Client login and projects are preserved. Read SKILL.md and run its setup launcher next.'
    } finally {
        if ($backup -and -not (Test-Path -LiteralPath $Target)) { [IO.Directory]::Move($backup, $Target) }
        if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
        $guard.Dispose()
        # Keep the empty lock file: deleting it can race with a waiting installer.
    }
}

if ($MyInvocation.InvocationName -ne '.') { Install-AlStyleSkill $Agent $Destination $Ref }
