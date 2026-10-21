<# Run the development suites through a trusted Linux Docker supervisor. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$PackSha,
    [ValidateSet('both', 'client-smoke', 'client-server-smoke')][string]$Suite = 'both',
    [string]$PackRepo = 'Cha0sCollective/Create-Ch4os-Packwiz',
    [string]$PackPath = 'pack',
    [switch]$AcceptEula,
    [switch]$BuildClient,
    [switch]$BuildServer,
    [string]$Output,
    [string]$WorkerVolume = 'mvt-worker-data',
    [string]$ServerImage = 'itzg/minecraft-server:java21',
    [ValidateSet('production', 'development')][string]$ServerMode = 'production',
    [string]$ServerHeap = '4G',
    [string]$ServerMemory = '6G',
    [string]$ClientMemory = '8G'
)
$ErrorActionPreference = 'Stop'
if (-not $AcceptEula) { throw 'Read the Minecraft EULA and supply -AcceptEula before running games.' }
if ($PackRepo -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' -or $PackPath -notmatch '^[A-Za-z0-9_./-]+$' -or $PackPath.Split('/') -contains '..') {
    throw 'Supply a repository owner/name and a safe pack path.'
}
if ($WorkerVolume -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$') { throw 'Invalid worker volume name.' }
$repoRoot = Split-Path -Parent $PSScriptRoot
$runId = [guid]::NewGuid().ToString('N')
if (-not $Output) { $Output = Join-Path $repoRoot "mvt-results-local-$runId" }
$outputPath = [IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $outputPath) { throw 'Output must be a new directory.' }
$docker = (Get-Command docker -ErrorAction Stop).Source
function Invoke-Docker {
    param([string[]]$DockerArgs)
    & $docker @DockerArgs
    if ($LASTEXITCODE -ne 0) { throw "Docker $($DockerArgs[0]) failed with exit $LASTEXITCODE" }
}
$engine = Invoke-Docker -DockerArgs @('info', '--format', '{{.OSType}}')
if ($engine -ne 'linux') { throw 'Select Docker Desktop Linux containers.' }
$manifest = Invoke-RestMethod "https://raw.githubusercontent.com/$PackRepo/$PackSha/$PackPath/pack.toml"
if ($manifest -notmatch '(?m)^minecraft\s*=\s*"1\.21\.1"\s*$' -or $manifest -notmatch '(?m)^neoforge\s*=\s*"(21\.1\.\d+)"\s*$') {
    throw 'Require Minecraft 1.21.1 and exact NeoForge 21.1.x in the pinned manifest.'
}
$neo = $Matches[1]
$clientTag = "mvt-client-dev:$neo"
if ($BuildClient -or -not (& $docker image ls --quiet $clientTag)) {
    Invoke-Docker -DockerArgs @('build', '-f', (Join-Path $PSScriptRoot 'Dockerfile'), '--build-arg', "NEOFORGE_VERSION=$neo", '-t', $clientTag, $repoRoot)
}
$developmentServerId = $null
if ($ServerMode -eq 'development') {
    $serverTag = "mvt-server-dev:$neo"
    if ($BuildServer -or $BuildClient -or -not (& $docker image ls --quiet $serverTag)) {
        Invoke-Docker -DockerArgs @('build', '-f', (Join-Path $PSScriptRoot 'Dockerfile.server'),
            '--build-arg', "CLIENT_RUNTIME_IMAGE=$clientTag", '--build-arg', "NEOFORGE_VERSION=$neo", '-t', $serverTag, $repoRoot)
    }
    $developmentServerId = Invoke-Docker -DockerArgs @('image', 'inspect', '--format', '{{.Id}}', $serverTag)
}
Invoke-Docker -DockerArgs @('build', '-f', (Join-Path $PSScriptRoot 'Dockerfile.runner'), '-t', 'mvt-worker:local', $repoRoot)
$clientId = Invoke-Docker -DockerArgs @('image', 'inspect', '--format', '{{.Id}}', $clientTag)
$workerId = Invoke-Docker -DockerArgs @('image', 'inspect', '--format', '{{.Id}}', 'mvt-worker:local')
$existing = & $docker volume ls --quiet --filter "name=^${WorkerVolume}$"
if ($existing) {
    $label = Invoke-Docker -DockerArgs @('volume', 'inspect', '--format', '{{index .Labels "org.cha0s.mvt.worker"}}', $WorkerVolume)
    if ($label -ne 'development') { throw 'Existing volume is not owned by the MVT development worker.' }
} else {
    Invoke-Docker -DockerArgs @('volume', 'create', '--label', 'org.cha0s.mvt.worker=development', $WorkerVolume)
}
New-Item -ItemType Directory -Path $outputPath | Out-Null
$metadata = [ordered]@{ run_id = $runId; pack_repository = $PackRepo; pack_path = $PackPath; pack_sha = $PackSha;
    minecraft = '1.21.1'; neoforge = $neo; client_mode = 'development'; suite = $Suite;
    worker_image_id = $workerId; client_image_id = $clientId; server_image_requested = $ServerImage;
    server_mode = $ServerMode; development_server_image_id = $developmentServerId;
    worker_volume = $WorkerVolume; client_memory = $ClientMemory; server_memory = $ServerMemory; server_heap = $ServerHeap }
[IO.File]::WriteAllText((Join-Path $outputPath 'worker-run.json'), ($metadata | ConvertTo-Json))
$suites = if ($Suite -eq 'both') { @('client-smoke', 'client-server-smoke') } else { @($Suite) }
$resultCode = 0
foreach ($currentSuite in $suites) {
    $container = "mvt-worker-$runId-$currentSuite"
    $remoteResult = "/mvt-worker/results/$runId-$currentSuite"
    $runArgs = @('run', '--name', $container, '--init', '--memory', '1g', '--stop-timeout', '300',
        '--label', "org.cha0s.mvt.worker=$runId", '--mount', 'type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock',
        '--mount', "type=volume,src=$WorkerVolume,dst=/mvt-worker", '-e', "MVT_SHARED_VOLUME=$WorkerVolume", $workerId,
        'run', '--accept-eula', '--pack-sha', $PackSha, '--pack-repo', $PackRepo, '--pack-path', $PackPath,
        '--suite', $currentSuite, '--client-mode', 'development', '--client-image', $clientId, '--image', $ServerImage,
        '--heap', $ServerHeap, '--container-memory', $ServerMemory, '--client-memory', $ClientMemory,
        '--cache', '/mvt-worker/cache', '--output', $remoteResult)
    # A client-only suite has no server and must not request a server runtime.
    if ($currentSuite -eq 'client-server-smoke' -and $ServerMode -eq 'development') {
        $runArgs += @('--server-mode', 'development', '--development-server-image', $developmentServerId)
    }
    try {
        Write-Host "Running $currentSuite for $PackSha / NeoForge $neo"
        & $docker @runArgs
        $code = $LASTEXITCODE
        if ($code -ne 0) { $resultCode = [Math]::Max($resultCode, $code) }
    } finally {
        $owned = & $docker inspect --format '{{index .Config.Labels "org.cha0s.mvt.worker"}}' $container 2>$null
        if ($owned -eq $runId) {
            Invoke-Docker -DockerArgs @('stop', '--time', '300', $container) | Out-Null
            $localResult = Join-Path $outputPath $currentSuite
            New-Item -ItemType Directory -Path $localResult | Out-Null
            try {
                Invoke-Docker -DockerArgs @('cp', "${container}:${remoteResult}/.", $localResult)
            } catch {
                Write-Warning "Could not export evidence: $_"
                $resultCode = 2
            } finally {
                Invoke-Docker -DockerArgs @('rm', $container) | Out-Null
            }
        }
    }
}
Write-Host "Evidence saved to $outputPath. Images and verified download cache remain reusable."
exit $resultCode
