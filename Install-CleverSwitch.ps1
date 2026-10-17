# Run in the normal user's PowerShell. Only the firewall helper requests UAC.
[CmdletBinding()]
param([string]$PackageDirectory = $PSScriptRoot, [switch]$NoLaunch)
$ErrorActionPreference = 'Stop'

function Get-FlowPackage([string]$Directory) {
    $manifest = Get-Content -LiteralPath (Join-Path $Directory 'cleverswitch-package.json') -Raw | ConvertFrom-Json
    if ($manifest.edition -notmatch '^\d+\.\d+\.\d+$' -or $manifest.protocol -ne 2) {
        throw 'Paquete de CleverSwitch incompatible.'
    }
    $files = @('CleverSwitchTray.exe', 'Configure-CleverSwitchFirewall.ps1', 'Install-CleverSwitch.ps1')
    if ((($manifest.files.PSObject.Properties.Name | Sort-Object) -join ',') -ne (($files | Sort-Object) -join ',')) {
        throw 'Lista de archivos del paquete invalida.'
    }
    foreach ($name in $files) {
        $expected = $manifest.files.$name
        if ($expected -notmatch '^[a-fA-F0-9]{64}$' -or
            (Get-FileHash -LiteralPath (Join-Path $Directory $name) -Algorithm SHA256).Hash -ne $expected) {
            throw "El paquete esta incompleto o modificado: $name"
        }
    }
    return $manifest
}

function Get-FlowStartupMigration([string]$Existing, [string]$Destination) {
    # Absence means disabled. Preserve Task Manager's StartupApproved value too.
    if ([string]::IsNullOrWhiteSpace($Existing)) { return $null }
    $old = $Existing.Trim()
    if ($old.StartsWith('"') -and $old.EndsWith('"')) { $old = $old.Substring(1, $old.Length - 2) }
    if (-not [IO.Path]::IsPathRooted($old) -or [IO.Path]::GetFileName($old) -ne 'CleverSwitchTray.exe' -or
        $old.Contains('"')) {
        throw 'El inicio CleverSwitchTray tiene un comando personalizado; no se ha sustituido.'
    }
    return '"' + $Destination + '"'
}

function Install-FlowFiles([string]$Source, [string]$Destination) {
    $null = Get-FlowPackage $Source
    $names = @('CleverSwitchTray.exe', 'Configure-CleverSwitchFirewall.ps1', 'Install-CleverSwitch.ps1',
        'cleverswitch-package.json')
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    # A fixed install path keeps firewall and login references valid across releases.
    # The profile directories are never copied, rewritten or included in the package.
    $lock = [IO.File]::Open((Join-Path $Destination 'install.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    $stage = Join-Path $Destination ('.update-' + [guid]::NewGuid().ToString('N'))
    $backup = Join-Path $stage 'previous'
    $changed = @()
    $safeToClean = $true
    try {
        [IO.Directory]::CreateDirectory($backup) | Out-Null
        foreach ($name in $names) {
            Copy-Item -LiteralPath (Join-Path $Source $name) -Destination (Join-Path $stage $name)
        }
        $null = Get-FlowPackage $stage
        foreach ($name in $names) {
            $target = Join-Path $Destination $name
            if (Test-Path -LiteralPath $target) { Copy-Item -LiteralPath $target -Destination (Join-Path $backup $name) }
            $changed += $name
            Copy-Item -LiteralPath (Join-Path $stage $name) -Destination $target -Force
        }
        $null = Get-FlowPackage $Destination
    } catch {
        $safeToClean = $false
        foreach ($name in $changed) {
            $saved = Join-Path $backup $name
            $target = Join-Path $Destination $name
            if (Test-Path -LiteralPath $saved) { Copy-Item -LiteralPath $saved -Destination $target -Force }
            else { Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue }
        }
        $safeToClean = $true
        throw
    } finally {
        $lock.Dispose()
        if ($safeToClean -and (Test-Path -LiteralPath $stage)) { Remove-Item -LiteralPath $stage -Recurse -Force }
    }
}

function Install-Flow {
    if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'Ejecuta este instalador en Windows.' }
    $package = Get-FlowPackage $PackageDirectory
    . (Join-Path $PackageDirectory 'Configure-CleverSwitchFirewall.ps1')
    if (Test-FlowAdministrator) {
        throw 'Abre PowerShell sin administrador. El firewall pedira permiso aparte; la app usa tu usuario habitual.'
    }
    if (Get-Process -Name CleverSwitchTray -ErrorAction SilentlyContinue |
        Where-Object { $_.SessionId -eq (Get-Process -Id $PID).SessionId }) {
        throw 'Cierra CleverSwitch desde su icono y vuelve a ejecutar el instalador.'
    }
    $destination = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs\CleverSwitchFlow'
    $exe = Join-Path $destination 'CleverSwitchTray.exe'
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    $existing = (Get-ItemProperty -Path $runKey -Name CleverSwitchTray -ErrorAction SilentlyContinue).CleverSwitchTray
    $startup = Get-FlowStartupMigration $existing $exe
    Install-FlowFiles $PackageDirectory $destination
    if ($null -ne $startup) {
        Set-ItemProperty -Path $runKey -Name CleverSwitchTray -Value $startup
    }
    $shortcutPath = Join-Path ([Environment]::GetFolderPath('Programs')) 'CleverSwitch.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $exe
    $shortcut.WorkingDirectory = $destination
    $shortcut.Save()
    $profilePath = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.config\cleverswitch-flow\link.json'
    try {
      if (Test-Path -LiteralPath $profilePath) {
        # Only the subnet is used. Identity, peer, edges and router data stay untouched.
        $subnet = (Get-Content -LiteralPath $profilePath -Raw | ConvertFrom-Json).network
        $subnet = ConvertTo-HomeNetwork $subnet
        $status = Get-FlowFirewallStatus $exe $subnet
        if ($status.state -eq 'repair') {
            $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
            & $powershell -NoProfile -NonInteractive -File (Join-Path $destination 'Configure-CleverSwitchFirewall.ps1') `
                -Mode Repair -Program $exe -Network $subnet
            if ($LASTEXITCODE -ne 0) {
                Write-Warning 'App instalada. Queda pendiente Revisar firewall de Windows desde su icono.'
            }
        } elseif ($status.state -eq 'blocked') {
            Write-Warning 'Hay una regla de bloqueo explicita. Se conserva; revisala con el administrador.'
        }
      } else {
        Write-Host 'Al vincular por primera vez, la app ofrecera configurar el firewall para la red elegida.'
      }
    } catch {
        Write-Warning 'App instalada; no se ha podido comprobar el firewall. Usa Revisar firewall de Windows.'
    }
    Write-Host "CleverSwitch $($package.edition) instalado en $destination. Configuracion e Inicio conservados."
    if (-not $NoLaunch) { Start-Process -FilePath $exe }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Install-Flow } catch { Write-Error $_.Exception.Message; exit 1 }
}
