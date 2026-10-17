# Runs on PowerShell 5.1/7. NetSecurity and registry calls are simulated, never applied.
param([string]$Case, [string]$Workspace)
$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. (Join-Path $root 'Configure-CleverSwitchFirewall.ps1')
. (Join-Path $root 'Install-CleverSwitch.ps1')

function Assert($Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Reject([scriptblock]$Action) {
    $failed = $false
    try { & $Action | Out-Null } catch { $failed = $true }
    Assert $failed 'Expected rejection'
}
$exe = Join-Path $Workspace 'Programs/CleverSwitchFlow/CleverSwitchTray.exe'
$script:rules = @{}
$script:mutations = 0
$script:denied = $false
function Get-NetFirewallRule {
    [CmdletBinding()]param($Name, $PolicyStore, [Parameter(ValueFromPipeline)]$InputObject)
    process {
        if ($Name) { return $script:rules[$Name] }
        if ($script:denied) {
            [pscustomobject]@{ Enabled = 'True'; Direction = 'Inbound'; Action = 'Block' }
        }
    }
}
function Get-NetFirewallApplicationFilter {
    param($PolicyStore, [Parameter(ValueFromPipeline)]$InputObject)
    process {
        if ($InputObject) { [pscustomobject]@{ Program = $InputObject.Program } }
        elseif ($script:denied) { [pscustomobject]@{ Program = $exe } }
    }
}
function Get-NetFirewallPortFilter {
    param([Parameter(ValueFromPipeline)]$InputObject)
    process { $InputObject }
}
function Get-NetFirewallAddressFilter {
    param([Parameter(ValueFromPipeline)]$InputObject)
    process { $InputObject }
}
function New-NetFirewallRule {
    param($Name, $DisplayName, $Group, $Program, $Protocol, $LocalPort, $RemotePort, $LocalAddress, $RemoteAddress,
        $Profile, $Direction, $Action, $Enabled, $EdgeTraversalPolicy, $PolicyStore)
    $data = @{} + $PSBoundParameters
    $data.Profile = $Profile -join ', '
    $data.LocalAddress = @($LocalAddress)
    $data.RemoteAddress = @($RemoteAddress)
    $script:rules[$Name] = [pscustomobject]$data
    $script:mutations++
}
function Set-NetFirewallRule {
    # Deliberately reject New-NetFirewallRule-only Group/DisplayName parameters.
    [CmdletBinding()]param($Name, $NewDisplayName, $Program, $Protocol, $LocalPort, $RemotePort, $LocalAddress,
        $RemoteAddress, $Profile, $Direction, $Action, $Enabled, $EdgeTraversalPolicy, $PolicyStore)
    $data = @{} + $PSBoundParameters
    $data.Remove('NewDisplayName')
    $data.Group = $script:rules[$Name].Group
    $data.DisplayName = $NewDisplayName
    New-NetFirewallRule @data
}
function New-TestPackage([string]$Directory, [string]$Version = '0.4.5') {
    [IO.Directory]::CreateDirectory($Directory) | Out-Null
    $files = @{}
    foreach ($name in @('CleverSwitchTray.exe', 'Configure-CleverSwitchFirewall.ps1', 'Install-CleverSwitch.ps1')) {
        [IO.File]::WriteAllText((Join-Path $Directory $name), "test payload $Version $name")
        $files[$name] = (Get-FileHash (Join-Path $Directory $name)).Hash
    }
    @{ edition = $Version; protocol = 2; files = $files } | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $Directory 'cleverswitch-package.json')
}

switch ($Case) {
    'networks' {
        foreach ($good in @('192.168.1.0/24', '10.0.0.0/8', '172.16.0.0/12', '172.31.255.252/30')) {
            Assert ((ConvertTo-HomeNetwork $good) -eq $good) 'Private network rejected'
        }
        foreach ($bad in @('0.0.0.0/0', 'LocalSubnet', '192.168.1.230/32', '192.168.1.1/24',
            '192.168.0.0/15', '172.16.0.0/11', '10.0.0.0/7', '8.8.8.0/24', '192.168.900.0/24',
            '192.168.1.0/24; calc', '::1/64')) { Reject { ConvertTo-HomeNetwork $bad } }
    }
    'dhcp' {
        $specs = @(Get-FlowRuleSpecs $exe '192.168.1.0/24')
        Assert ($specs.Count -eq 2) 'Two rules required'
        Assert (($specs.LocalPort | Sort-Object) -join ',' -eq '47654,47655') 'Wrong ports'
        foreach ($spec in $specs) {
            Assert ($spec.Program -eq $exe) 'Wrong program'
            Assert ($spec.LocalAddress -eq '192.168.1.0/24') 'Local IP must follow DHCP'
            Assert ($spec.RemoteAddress -eq '192.168.1.0/24') 'Peer IP must follow DHCP'
            Assert (($spec.Profile | Sort-Object) -join ',' -eq 'Private,Public') 'Public home network unsupported'
            Assert ($spec.EdgeTraversalPolicy -eq 'Block') 'No edge traversal'
        }
        $otherUser = @(Get-FlowRuleSpecs (Join-Path $Workspace 'another-user/CleverSwitchTray.exe') '192.168.1.0/24')
        Assert ($otherUser[0].Name -ne $specs[0].Name) 'Must not change another user installation rules'
    }
    'idempotent' {
        Assert ((Get-FlowFirewallStatus $exe '192.168.1.0/24').state -eq 'repair') 'Fresh rules missing'
        Assert ($script:mutations -eq 0) 'Check mutated firewall'
        Set-FlowFirewallRules $exe '192.168.1.0/24'
        Assert ((Get-FlowFirewallStatus $exe '192.168.1.0/24').state -eq 'ready') 'New rules not recognized'
        Set-FlowFirewallRules $exe '192.168.1.0/24'
        Assert ($script:mutations -eq 2) 'Repeated update must not modify correct rules'
    }
    'subnet-change' {
        Set-FlowFirewallRules $exe '192.168.1.0/24'
        Assert ((Get-FlowFirewallStatus $exe '192.168.2.0/24').state -eq 'repair') 'New subnet undetected'
        Set-FlowFirewallRules $exe '192.168.2.0/24'
        Assert ($script:rules.Count -eq 2) 'Update duplicated rules'
        Assert ((Get-FlowFirewallStatus $exe '192.168.2.0/24').state -eq 'ready') 'Subnet did not update'
    }
    'old-ip-or-path' {
        Set-FlowFirewallRules $exe '192.168.1.0/24'
        foreach ($rule in $script:rules.Values) {
            $rule.RemoteAddress = @('192.168.1.230')
            $rule.Program = Join-Path $Workspace 'old-download/CleverSwitchTray.exe'
        }
        Assert ((Get-FlowFirewallStatus $exe '192.168.1.0/24').state -eq 'repair') 'Old bindings undetected'
        Set-FlowFirewallRules $exe '192.168.1.0/24'
        Assert ((Get-FlowFirewallStatus $exe '192.168.1.0/24').state -eq 'ready') 'Old bindings not repaired'
    }
    'netmask-format' {
        Set-FlowFirewallRules $exe '192.168.1.0/24'
        foreach ($rule in $script:rules.Values) {
            $rule.RemoteAddress = @('192.168.1.0/255.255.255.0')
            $rule.LocalAddress = @('192.168.1.0/255.255.255.0')
        }
        Set-FlowFirewallRules $exe '192.168.1.0/24'
        Assert ($script:mutations -eq 2) 'Dotted netmask caused another UAC request'
    }
    'foreign-rule' {
        Set-FlowFirewallRules $exe '192.168.1.0/24'
        foreach ($rule in $script:rules.Values) { $rule.Group = 'Another app' }
        Reject { Set-FlowFirewallRules $exe '192.168.2.0/24' }
        Assert ($script:mutations -eq 2) 'Foreign rules changed'
    }
    'explicit-deny' {
        Set-FlowFirewallRules $exe '192.168.1.0/24'
        $script:denied = $true
        Assert ((Get-FlowFirewallStatus $exe '192.168.1.0/24').state -eq 'blocked') 'Deny hidden by allow'
        Assert ($script:mutations -eq 2) 'Deny must not be removed'
    }
    'startup' {
        Assert ($null -eq (Get-FlowStartupMigration '' $exe)) 'Disabled startup enabled'
        $old = '"' + (Join-Path $Workspace 'old version/CleverSwitchTray.exe') + '"'
        Assert ((Get-FlowStartupMigration $old $exe) -eq ('"' + $exe + '"')) 'Enabled startup not migrated'
        Reject { Get-FlowStartupMigration 'other-app.exe' $exe }
        Reject { Get-FlowStartupMigration ($old + ' --unknown') $exe }
    }
    'update' {
        $source = Join-Path $Workspace 'download v2'
        $destination = Join-Path $Workspace 'stable'
        New-TestPackage $destination '0.4.4'
        New-TestPackage $source
        $profile = Join-Path $Workspace '.config/cleverswitch-flow'
        [IO.Directory]::CreateDirectory($profile) | Out-Null
        $saved = Join-Path $profile 'link.json'
        [IO.File]::WriteAllText($saved, 'fixture identity, peer, edges and router')
        $before = (Get-FileHash $saved).Hash
        Install-FlowFiles $source $destination
        Assert ((Get-FlowPackage $destination).edition -eq '0.4.5') 'Update not installed'
        Assert ((Get-FileHash $saved).Hash -eq $before) 'Profile changed'
        Install-FlowFiles $source $destination
        Assert ((Get-FlowPackage $destination).edition -eq '0.4.5') 'Repeated update failed'
    }
    'tampered-package' {
        $source = Join-Path $Workspace 'download'
        $destination = Join-Path $Workspace 'stable'
        New-TestPackage $source
        New-TestPackage $destination '0.4.4'
        Add-Content (Join-Path $source 'CleverSwitchTray.exe') 'corruption'
        Reject { Install-FlowFiles $source $destination }
        Assert ((Get-FlowPackage $destination).edition -eq '0.4.4') 'Corrupted update replaced previous app'
    }
    'rollback' {
        $source = Join-Path $Workspace 'download'
        $destination = Join-Path $Workspace 'stable'
        New-TestPackage $source
        New-TestPackage $destination '0.4.4'
        $script:once = $true
        function Copy-Item {
            param($LiteralPath, $Destination, [switch]$Force)
            if ($Force -and $script:once -and $LiteralPath -like '*Install-CleverSwitch.ps1') {
                $script:once = $false
                throw 'Simulated interrupted copy'
            }
            Microsoft.PowerShell.Management\Copy-Item @PSBoundParameters
        }
        Reject { Install-FlowFiles $source $destination }
        Assert ((Get-FlowPackage $destination).edition -eq '0.4.4') 'Rollback lost prior version'
    }
    default { throw 'Unknown case' }
}
Write-Output "PASS $Case"
