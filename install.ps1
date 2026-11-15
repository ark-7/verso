#Requires -Version 5.1
<#
.SYNOPSIS
Installs Verso from a GitHub Release on Windows.

.DESCRIPTION
Downloads the release wheel, verifies its SHA-256 checksum, and installs it
with uv, pipx, or pip. Requires Python 3.11 or newer.

.PARAMETER Version
Install a specific release tag, for example v0.1.0. Defaults to the latest
stable release.

.PARAMETER Installer
auto, uv, pipx, or pip. The default prefers uv, then pipx, then pip.

.PARAMETER WithProviders
Also install the optional Anthropic and OpenAI SDKs.

.PARAMETER DryRun
Resolve the release and print the download details without installing.

.EXAMPLE
irm https://github.com/ark-7/verso/releases/latest/download/install.ps1 | iex

.EXAMPLE
.\install.ps1 -Version v0.1.0 -WithProviders
#>
[CmdletBinding()]
param(
    [string]$Version,
    [ValidateSet('auto', 'uv', 'pipx', 'pip')]
    [string]$Installer = 'auto',
    [switch]$WithProviders,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'

$Repo = 'ark-7/verso'
$Api = "https://api.github.com/repos/$Repo"
$Headers = @{
    Accept     = 'application/vnd.github+json'
    'User-Agent' = 'verso-installer'
}

function Write-Info {
    param([string]$Message)
    Write-Host $Message
}

function Stop-Install {
    param([string]$Message)
    [Console]::Error.WriteLine("error: $Message")
    exit 1
}

function Test-Python {
    param(
        [string]$Exe,
        [string[]]$PrefixArgs
    )
    try {
        & $Exe @PrefixArgs -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

$PythonExe = $null
$PythonArgs = @()

if ($env:VIRTUAL_ENV) {
    $venvPython = Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'
    if ((Test-Path -LiteralPath $venvPython) -and (Test-Python $venvPython @())) {
        $PythonExe = $venvPython
    }
    else {
        Stop-Install 'the active virtualenv uses Python older than 3.11; create a new virtualenv with Python 3.11 or newer'
    }
}

if (-not $PythonExe) {
    $candidates = @(
        @{ Exe = 'py'; Args = @('-3.13') },
        @{ Exe = 'py'; Args = @('-3.12') },
        @{ Exe = 'py'; Args = @('-3.11') },
        @{ Exe = 'python'; Args = @() },
        @{ Exe = 'python3'; Args = @() }
    )
    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate.Exe -ErrorAction SilentlyContinue
        if ($command -and (Test-Python $candidate.Exe $candidate.Args)) {
            $PythonExe = $command.Source
            $PythonArgs = $candidate.Args
            break
        }
    }
}

if (-not $PythonExe) {
    Stop-Install 'Python 3.11 or newer is required. Install it from https://www.python.org/downloads/ or install uv: https://docs.astral.sh/uv/getting-started/installation/'
}

try {
    if (-not $Version) {
        $release = Invoke-RestMethod -Uri "$Api/releases/latest" -Headers $Headers
    }
    else {
        $tag = $Version
        if (-not $tag.StartsWith('v')) {
            $tag = "v$tag"
        }
        $release = Invoke-RestMethod -Uri "$Api/releases/tags/$tag" -Headers $Headers
    }
}
catch {
    Stop-Install "could not resolve the Verso release: $($_.Exception.Message)"
}

$Tag = $release.tag_name
if (-not $Tag) {
    Stop-Install 'could not determine the release tag'
}

$wheelAsset = $release.assets |
    Where-Object { $_.name -match '^verso_cli-.+-py3-none-any\.whl$' } |
    Select-Object -First 1
$checksumsAsset = $release.assets |
    Where-Object { $_.name -eq 'SHA256SUMS' } |
    Select-Object -First 1

if (-not $wheelAsset) {
    Stop-Install "release $Tag does not contain a verso_cli wheel"
}
if (-not $checksumsAsset) {
    Stop-Install "release $Tag does not contain SHA256SUMS"
}

$WheelName = $wheelAsset.name
$WheelVersion = $WheelName.Substring('verso_cli-'.Length)
$WheelVersion = $WheelVersion.Substring(0, $WheelVersion.Length - '-py3-none-any.whl'.Length)

$SelectedInstaller = $Installer
if ($SelectedInstaller -eq 'auto') {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $SelectedInstaller = 'uv'
    }
    elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
        $SelectedInstaller = 'pipx'
    }
    else {
        $SelectedInstaller = 'pip'
    }
}

switch ($SelectedInstaller) {
    'uv' {
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            Stop-Install 'uv is not installed: https://docs.astral.sh/uv/getting-started/installation/'
        }
    }
    'pipx' {
        if (-not (Get-Command pipx -ErrorAction SilentlyContinue)) {
            Stop-Install 'pipx is not installed: https://pipx.pypa.io/stable/installation/'
        }
    }
    'pip' {
        & $PythonExe @PythonArgs -m pip --version 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Stop-Install "pip is not available for $PythonExe"
        }
    }
}

$TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("verso-install-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $TempDir -Force | Out-Null

try {
    $ChecksumsPath = Join-Path $TempDir 'SHA256SUMS'
    Invoke-WebRequest -Uri $checksumsAsset.browser_download_url -OutFile $ChecksumsPath `
        -UseBasicParsing -Headers $Headers

    $expected = $null
    foreach ($line in Get-Content -LiteralPath $ChecksumsPath) {
        $parts = $line -split '\s+', 2
        if ($parts.Count -lt 2) {
            continue
        }
        $file = $parts[1].Trim()
        if ($file.StartsWith('*')) {
            $file = $file.Substring(1)
        }
        $file = $file.TrimEnd([char]13)
        if ($file -eq $WheelName) {
            $expected = $parts[0].ToLowerInvariant()
            break
        }
    }
    if (-not $expected) {
        Stop-Install "SHA256SUMS does not list $WheelName"
    }

    if ($DryRun) {
        Write-Info 'Verso installer dry run'
        Write-Info "  tag:       $Tag"
        Write-Info "  wheel:     $($wheelAsset.browser_download_url)"
        Write-Info "  checksums: $($checksumsAsset.browser_download_url)"
        Write-Info "  sha256:    $expected"
        Write-Info "  python:    $PythonExe $($PythonArgs -join ' ')"
        Write-Info "  installer: $SelectedInstaller"
        return
    }

    $WheelPath = Join-Path $TempDir $WheelName
    Invoke-WebRequest -Uri $wheelAsset.browser_download_url -OutFile $WheelPath `
        -UseBasicParsing -Headers $Headers

    $actual = (Get-FileHash -LiteralPath $WheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        Stop-Install "checksum mismatch for $WheelName (expected $expected, got $actual)"
    }

    switch ($SelectedInstaller) {
        'uv' {
            $uvArgs = @('tool', 'install', '--force')
            if ($WithProviders) {
                $uvArgs += @('--with', 'anthropic', '--with', 'openai')
            }
            $uvArgs += $WheelPath
            & uv @uvArgs
            if ($LASTEXITCODE -ne 0) {
                Stop-Install 'uv tool install failed'
            }
        }
        'pipx' {
            & pipx install --force $WheelPath
            if ($LASTEXITCODE -ne 0) {
                Stop-Install 'pipx install failed'
            }
            if ($WithProviders) {
                & pipx inject verso-cli anthropic openai
                if ($LASTEXITCODE -ne 0) {
                    Stop-Install 'pipx inject failed'
                }
            }
        }
        'pip' {
            $userArgs = @()
            if (-not $env:VIRTUAL_ENV) {
                $marker = & $PythonExe @PythonArgs -c "import pathlib, sysconfig; raise SystemExit(0 if (pathlib.Path(sysconfig.get_path('stdlib')) / 'EXTERNALLY-MANAGED').exists() else 1)" 2>$null
                if ($LASTEXITCODE -eq 0) {
                    Stop-Install 'this Python is externally managed (PEP 668). Install uv or pipx, or use a virtualenv, then rerun this script'
                }
                $userArgs = @('--user')
            }
            $installArgs = @($PythonArgs) + @('-m', 'pip', 'install') + $userArgs + @('--upgrade', $WheelPath)
            & $PythonExe @installArgs
            if ($LASTEXITCODE -ne 0) {
                Stop-Install 'pip install failed'
            }
            if ($WithProviders) {
                $extraArgs = @($PythonArgs) + @('-m', 'pip', 'install') + $userArgs + @('anthropic', 'openai')
                & $PythonExe @extraArgs
                if ($LASTEXITCODE -ne 0) {
                    Stop-Install 'pip install of provider SDKs failed'
                }
            }
        }
    }

    $expectedOutput = "verso $WheelVersion"

    if ($SelectedInstaller -eq 'pip') {
        $output = (& $PythonExe @PythonArgs -m verso --version 2>&1 | Out-String).Trim()
        if ($output -ne $expectedOutput) {
            Stop-Install "installed verso reported '$output', expected '$expectedOutput'"
        }
        Write-Info "Installed $output"
        if (-not (Get-Command verso -ErrorAction SilentlyContinue)) {
            $scriptsDir = (& $PythonExe @PythonArgs -c "import sysconfig; print(sysconfig.get_path('scripts', scheme='nt_user'))").Trim()
            Write-Info "The 'verso' command is not on PATH yet. Add: $scriptsDir"
        }
        return
    }

    if (Get-Command verso -ErrorAction SilentlyContinue) {
        $output = (& verso --version 2>&1 | Out-String).Trim()
        if ($output -ne $expectedOutput) {
            Stop-Install "installed verso reported '$output', expected '$expectedOutput'"
        }
        Write-Info "Installed $output"
        return
    }

    if ($SelectedInstaller -eq 'uv') {
        Write-Info 'verso was installed with uv but is not on PATH yet.'
        Write-Info 'Run: uv tool update-shell'
    }
    else {
        Write-Info 'verso was installed with pipx but is not on PATH yet.'
        Write-Info 'Run: pipx ensurepath'
    }
    Write-Info 'Open a new terminal, then run: verso --version'
}
finally {
    Remove-Item -LiteralPath $TempDir -Recurse -Force -ErrorAction SilentlyContinue
}
