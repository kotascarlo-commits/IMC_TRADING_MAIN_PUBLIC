$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'IMC.cmd'
$commandDirectory = Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'npm'
$destination = Join-Path $commandDirectory 'IMC.cmd'
$existingCommand = Get-Command IMC -ErrorAction SilentlyContinue
if ($existingCommand -and $existingCommand.Source -ne $destination) {
  throw "IMC already resolves to another command: $($existingCommand.Definition)"
}
if ((Test-Path -LiteralPath $destination) -and
    (Get-Content -LiteralPath $destination -Raw) -ne (Get-Content -LiteralPath $source -Raw)) {
  throw "Existing IMC.cmd differs from the project wrapper: $destination"
}

New-Item -ItemType Directory -Path $commandDirectory -Force | Out-Null
Copy-Item -LiteralPath $source -Destination $destination
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$pathEntries = @($userPath -split ';' | Where-Object { $_ })
$alreadyOnPath = @($pathEntries | Where-Object {
  [Environment]::ExpandEnvironmentVariables($_).TrimEnd('\') -ieq $commandDirectory.TrimEnd('\')
}).Count -gt 0
if (-not $alreadyOnPath) {
  $newUserPath = if ([string]::IsNullOrEmpty($userPath)) { $commandDirectory } else { $userPath.TrimEnd(';') + ';' + $commandDirectory }
  [Environment]::SetEnvironmentVariable('Path', $newUserPath, 'User')
}
if ($env:Path -split ';' -notcontains $commandDirectory) {
  $env:Path = $env:Path.TrimEnd(';') + ';' + $commandDirectory
}
Write-Output "Installed IMC: $destination"
Write-Output 'Open a fresh PowerShell and enter IMC. Setup check: IMC -Check.'
