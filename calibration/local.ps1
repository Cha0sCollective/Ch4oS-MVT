<# Trusted calibration supervisor. Game containers never receive the Docker socket. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('reference','faults','inputs','auxiliary')][string]$Phase,
    [Parameter(Mandatory)][string]$Output,
    [switch]$AcceptEula,
    [string]$Case,
    [string]$WorkerVolume = 'mvt-worker-data'
)
$ErrorActionPreference = 'Stop'
if (-not $AcceptEula) { throw 'Supply -AcceptEula before running calibration.' }
if ($WorkerVolume -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$') { throw 'Invalid worker volume name.' }
$repoRoot = Split-Path -Parent $PSScriptRoot
$outputPath = [IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $outputPath) { throw 'Calibration output must be a new directory.' }
$docker = (Get-Command docker).Source
function Invoke-Docker {
    param([string[]]$DockerArgs)
    & $docker @DockerArgs
    if ($LASTEXITCODE -ne 0) { throw "Docker $($DockerArgs[0]) failed with exit $LASTEXITCODE" }
}
$info = Invoke-Docker -DockerArgs @('volume','inspect',$WorkerVolume) | ConvertFrom-Json
if ($info[0].Labels.'org.cha0s.mvt.worker' -ne 'development') { throw 'Use an owned MVT development volume.' }
Invoke-Docker -DockerArgs @('build','-f',(Join-Path $repoRoot 'worker/Dockerfile.runner'),'-t','mvt-worker:local',$repoRoot)
Invoke-Docker -DockerArgs @('build','-f',(Join-Path $repoRoot 'worker/Dockerfile.calibration'),'-t','mvt-calibration:local',$repoRoot)
$imageId = Invoke-Docker -DockerArgs @('image','inspect','--format','{{.Id}}','mvt-calibration:local')
$runId = [guid]::NewGuid().ToString('N')
$container = "mvt-calibration-$runId"
$remote = "/mvt-worker/results/calibration-$runId"
New-Item -ItemType Directory -Path $outputPath | Out-Null
[IO.File]::WriteAllText((Join-Path $outputPath 'worker-run.json'), (@{run_id=$runId; phase=$Phase; case=$Case; image_id=$imageId; worker_volume=$WorkerVolume} | ConvertTo-Json))
$runArgs = @('run','--name',$container,'--init','--memory','1g','--stop-timeout','300',
    '--label',"org.cha0s.mvt.calibration=$runId",'--mount','type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock',
    '--mount',"type=volume,src=$WorkerVolume,dst=/mvt-worker",'-e',"MVT_SHARED_VOLUME=$WorkerVolume",$imageId,
    '--phase',$Phase,'--output',$remote,'--accept-eula')
if ($Case) { $runArgs += @('--case',$Case) }
$code = 2
try {
    & $docker @runArgs
    $code = $LASTEXITCODE
} finally {
    $owned = & $docker inspect $container 2>$null | ConvertFrom-Json
    if ($owned -and $owned[0].Config.Labels.'org.cha0s.mvt.calibration' -eq $runId) {
        Invoke-Docker -DockerArgs @('stop','--timeout','300',$container) | Out-Null
        try {
            Invoke-Docker -DockerArgs @('cp',"${container}:${remote}/.",$outputPath)
        } catch {
            Write-Warning "Could not export calibration evidence: $_"
            $code = 2
        } finally {
            Invoke-Docker -DockerArgs @('rm',$container) | Out-Null
        }
    }
}
Write-Host "Calibration evidence: $outputPath"
exit $code
