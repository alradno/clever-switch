# Flow edition only. Check is read-only; Repair elevates just this helper.
[CmdletBinding()]
param(
    [ValidateSet('Check', 'Repair')][string]$Mode = 'Check',
    [string]$Program,
    [string]$Network,
    [switch]$Elevated
)

$ErrorActionPreference = 'Stop'

function ConvertTo-HomeNetwork([string]$Value) {
    if ($Value -notmatch '^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$') { throw 'Subred IPv4 invalida.' }
    $parts = $Value.Split('/')
    $octets = @($parts[0].Split('.') | ForEach-Object { [int]$_ })
    $prefix = [int]$parts[1]
    if (($octets | Where-Object { $_ -gt 255 }).Count -or $prefix -lt 8 -or $prefix -gt 30) {
        throw 'Selecciona la subred domestica, no una direccion individual.'
    }
    $number = [uint64]0
    foreach ($octet in $octets) { $number = ($number -shl 8) -bor $octet }
    $size = [uint64][math]::Pow(2, 32 - $prefix)
    $base = $number - ($number % $size)
    $last = $base + $size - 1
    $private = ($base -ge 167772160 -and $last -le 184549375) -or
               ($base -ge 2886729728 -and $last -le 2887778303) -or
               ($base -ge 3232235520 -and $last -le 3232301055)
    if (-not $private -or $base -ne $number) { throw 'La subred debe ser privada y canonica.' }
    return (($octets -join '.') + '/' + $prefix)
}

function Get-FlowRuleSpecs([string]$Exe, [string]$Subnet) {
    $Subnet = ConvertTo-HomeNetwork $Subnet
    if (-not [IO.Path]::IsPathRooted($Exe) -or [IO.Path]::GetFileName($Exe) -ne 'CleverSwitchTray.exe' -or
        $Exe.Contains('"')) { throw 'Ruta de CleverSwitch invalida.' }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Exe.ToLowerInvariant())) }
    finally { $sha.Dispose() }
    $id = ([BitConverter]::ToString($hash)).Replace('-', '').Substring(0, 12)
    foreach ($protocol in @('TCP', 'UDP')) {
        @{
            Name = "CleverSwitchFlow-$id-$protocol"
            DisplayName = "CleverSwitch Flow $protocol"
            Group = 'CleverSwitch Flow'
            Program = $Exe
            Protocol = $protocol
            LocalPort = $(if ($protocol -eq 'TCP') { 47654 } else { 47655 })
            RemotePort = 'Any'
            # No fixed DHCP address, no all-network/LocalSubnet expansion.
            LocalAddress = $Subnet
            RemoteAddress = $Subnet
            Profile = @('Private', 'Public')
            Direction = 'Inbound'
            Action = 'Allow'
            Enabled = 'True'
            EdgeTraversalPolicy = 'Block'
        }
    }
}

function Test-FlowRule($Rule, $Spec) {
    if (-not $Rule -or $Rule.Group -ne $Spec.Group) { return $false }
    $app = $Rule | Get-NetFirewallApplicationFilter
    $port = $Rule | Get-NetFirewallPortFilter
    $address = $Rule | Get-NetFirewallAddressFilter
    $protocol = $(if ($Spec.Protocol -eq 'TCP') { @('TCP', '6') } else { @('UDP', '17') })
    # NetSecurity can render CIDR as a dotted netmask, depending on Windows version.
    $cidr = $Spec.RemoteAddress
    $prefix = [int]$cidr.Split('/')[1]
    $mask = @()
    for ($i = 0; $i -lt 4; $i++) {
        $bits = [math]::Min(8, [math]::Max(0, $prefix - 8 * $i))
        $mask += [int](256 - [math]::Pow(2, 8 - $bits))
    }
    $addresses = @($cidr, ($cidr.Split('/')[0] + '/' + ($mask -join '.')))
    $profiles = (($Rule.Profile.ToString().Split(',') | ForEach-Object { $_.Trim() } | Sort-Object) -join ',')
    return $Rule.Enabled.ToString() -eq 'True' -and $Rule.Direction.ToString() -eq 'Inbound' -and
        $Rule.Action.ToString() -eq 'Allow' -and $Rule.EdgeTraversalPolicy.ToString() -eq 'Block' -and
        $profiles -eq 'Private,Public' -and $app.Program -eq $Spec.Program -and
        $port.Protocol.ToString() -in $protocol -and (@($port.LocalPort) -join ',') -eq $Spec.LocalPort.ToString() -and
        (@($port.RemotePort) -join ',') -eq 'Any' -and
        @($address.LocalAddress).Count -eq 1 -and @($address.LocalAddress)[0] -in $addresses -and
        @($address.RemoteAddress).Count -eq 1 -and @($address.RemoteAddress)[0] -in $addresses
}

function Get-FlowFirewallStatus([string]$Exe, [string]$Subnet) {
    $pending = @()
    foreach ($spec in (Get-FlowRuleSpecs $Exe $Subnet)) {
        $rule = Get-NetFirewallRule -PolicyStore ActiveStore -Name $spec.Name -ErrorAction SilentlyContinue
        if (-not (Test-FlowRule $rule $spec)) { $pending += $spec.Name }
    }
    # Never remove a user/administrator deny rule. It wins over an allow rule.
    $blocked = @(Get-NetFirewallApplicationFilter -PolicyStore ActiveStore |
        Where-Object { $_.Program -eq $Exe } | Get-NetFirewallRule |
        Where-Object { $_.Enabled.ToString() -eq 'True' -and $_.Direction.ToString() -eq 'Inbound' -and
            $_.Action.ToString() -eq 'Block' })
    $state = $(if ($blocked.Count) { 'blocked' } elseif ($pending.Count) { 'repair' } else { 'ready' })
    return @{ state = $state; pending = $pending.Count; explicit_blocks = $blocked.Count }
}

function Set-FlowFirewallRules([string]$Exe, [string]$Subnet) {
    foreach ($spec in (Get-FlowRuleSpecs $Exe $Subnet)) {
        $rule = Get-NetFirewallRule -PolicyStore PersistentStore -Name $spec.Name -ErrorAction SilentlyContinue
        if ($rule -and $rule.Group -ne $spec.Group) { throw 'El nombre de regla pertenece a otra aplicacion.' }
        if (Test-FlowRule $rule $spec) { continue }
        if ($rule) {
            $update = $spec.Clone()
            $update.Remove('Group')
            $update.Remove('DisplayName')
            $update.NewDisplayName = $spec.DisplayName
            Set-NetFirewallRule -PolicyStore PersistentStore @update | Out-Null
        } else {
            New-NetFirewallRule -PolicyStore PersistentStore @spec | Out-Null
        }
    }
}

function Test-FlowAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$identity).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-FlowFirewall {
    # Validate before elevation. The helper never reads another user's private profile.
    $subnet = ConvertTo-HomeNetwork $Network
    $null = Get-FlowRuleSpecs $Program $subnet
    if (-not (Test-Path -LiteralPath $Program -PathType Leaf)) { throw 'No se encuentra la app instalada.' }
    if ($Mode -eq 'Repair' -and -not (Test-FlowAdministrator)) {
        if ($Elevated) { throw 'Se necesita permiso de administrador para configurar el firewall.' }
        $shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $arguments = '-NoProfile -NonInteractive -File "{0}" -Mode Repair -Program "{1}" -Network "{2}" -Elevated' -f
            $PSCommandPath, $Program, $subnet
        $child = Start-Process -FilePath $shell -Verb RunAs -ArgumentList $arguments -Wait -PassThru
        if ($child.ExitCode -ne 0) { throw 'No se pudo aplicar la configuracion del firewall.' }
    } elseif ($Mode -eq 'Repair') {
        Set-FlowFirewallRules $Program $subnet
    }
    Get-FlowFirewallStatus $Program $subnet | ConvertTo-Json -Compress
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'Ejecuta este archivo en Windows.' }
        Invoke-FlowFirewall
    } catch {
        # No profile data or secret JSON is printed.
        Write-Error $_.Exception.Message
        exit 1
    }
}
