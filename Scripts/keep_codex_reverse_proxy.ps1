param(
    [string]$Alias = "example-gpu-root-2224",
    [int]$LocalPort = 7897,
    [int]$RemotePort = 7897,
    [string]$LocalBindAddress = "127.0.0.1",
    [string]$RemoteBindAddress = "127.0.0.1",
    [int]$RestartDelaySeconds = 5,
    [string]$LogPath = "$env:LOCALAPPDATA\CodexReverseProxy\keepalive.log"
)

$ErrorActionPreference = "Continue"

$mutexName = "Local\CodexReverseProxy-$Alias-$RemotePort"
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

function Clear-RemoteStaleListener {
    $remoteScript = @"
port=$RemotePort
if command -v lsof >/dev/null 2>&1; then
  pids=`$(lsof -tiTCP:`$port -sTCP:LISTEN -nP 2>/dev/null || true)
  for pid in `$pids; do
    comm=`$(ps -p "`$pid" -o comm= 2>/dev/null | tr -d ' ')
    if [ "`$comm" = "sshd" ]; then
      kill "`$pid" 2>/dev/null || true
      echo "killed stale sshd listener pid `$pid on port `$port"
    fi
  done
fi
"@

    try {
        $output = & ssh -o BatchMode=yes -o ConnectTimeout=10 $Alias $remoteScript 2>&1
        foreach ($line in $output) {
            if ($line) {
                Write-Log "remote cleanup: $line"
            }
        }
    }
    catch {
        Write-Log "remote cleanup failed: $($_.Exception.Message)"
    }
}

try {
    Write-Log "keepalive started: alias=$Alias remote=$RemoteBindAddress`:$RemotePort local=$LocalBindAddress`:$LocalPort"

    while ($true) {
        if (-not (Test-LocalPort)) {
            Write-Log "warning: local proxy $LocalBindAddress`:$LocalPort is not reachable"
        }

        Clear-RemoteStaleListener

        $forwardSpec = "$RemoteBindAddress`:$RemotePort`:$LocalBindAddress`:$LocalPort"
        $sshArgs = @(
            "-N",
            "-T",
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
