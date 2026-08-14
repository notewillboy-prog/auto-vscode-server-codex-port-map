param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Alias,

    [ValidateRange(1, 65535)]
    [int]$LocalPort = 7897,

    [ValidateRange(0, 65535)]
    [int]$RemotePort = 0,

    [string]$LocalBindAddress = "127.0.0.1",
    [string]$RemoteBindAddress = "127.0.0.1",

    [ValidateRange(1, 3600)]
    [int]$RestartDelaySeconds = 5,

    [string]$LogPath = ""
)

$ErrorActionPreference = "Continue"

if ($RemotePort -eq 0) {
    $RemotePort = $LocalPort
}

$safeAlias = $Alias -replace '[^A-Za-z0-9_.-]', '_'
if (-not $LogPath) {
    $LogPath = Join-Path $env:LOCALAPPDATA "CodexReverseProxy\keepalive-$safeAlias-$RemotePort.log"
}

$mutexName = "Local\CodexReverseProxy-$safeAlias-$RemotePort"
$mutex = New-Object System.Threading.Mutex($false, $mutexName)
if (-not $mutex.WaitOne(0)) {
    exit 0
}

function Write-Log {
    param([string]$Message)

    $dir = Split-Path -Parent $LogPath
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$timestamp] $Message" | Out-File -FilePath $LogPath -Append -Encoding utf8
}

function Test-LocalPort {
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $async = $client.BeginConnect($LocalBindAddress, $LocalPort, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(1000)) {
            $client.Close()
            return $false
        }
        $client.EndConnect($async)
        $client.Close()
        return $true
    }
    catch {
        return $false
    }
}

try {
    Write-Log "keepalive started: alias=$Alias remote=$RemoteBindAddress`:$RemotePort local=$LocalBindAddress`:$LocalPort"

    while ($true) {
        if (-not (Test-LocalPort)) {
            Write-Log "warning: local proxy $LocalBindAddress`:$LocalPort is not reachable"
        }

        $forwardSpec = "$RemoteBindAddress`:$RemotePort`:$LocalBindAddress`:$LocalPort"
        $sshArgs = @(
            "-N",
            "-T",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", "TCPKeepAlive=yes",
            "-R", $forwardSpec,
            $Alias
        )

        Write-Log "starting ssh reverse forward: ssh $($sshArgs -join ' ')"
        & ssh @sshArgs 2>&1 | ForEach-Object {
            if ($_ -ne $null) {
                Write-Log "ssh: $_"
            }
        }

        $exitCode = $LASTEXITCODE
        Write-Log "ssh exited with code $exitCode; restarting in $RestartDelaySeconds seconds"
        Start-Sleep -Seconds $RestartDelaySeconds
    }
}
finally {
    Write-Log "keepalive stopped"
    $mutex.ReleaseMutex() | Out-Null
    $mutex.Dispose()
}
