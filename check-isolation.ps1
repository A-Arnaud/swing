# Read-only inventory. Does not enable Windows features or change permissions.
$ErrorActionPreference = 'Stop'
$result = [ordered]@{
    os = $null
    edition = $null
    build = $null
    memory_gb = $null
    hypervisor_present = $null
    processors = @()
    hyperv_feature = 'unknown'
    sandbox_feature = 'unknown'
    sandbox_executable_present = $false
    hyperv_cmdlets_present = $false
    diagnostics = @()
}
try {
    $version = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    $result.os = $version.ProductName
    $result.edition = $version.EditionID
    $result.build = $version.CurrentBuildNumber
} catch { $result.diagnostics += 'os_registry_unavailable' }
try {
    $system = Get-CimInstance -ClassName Win32_ComputerSystem
    $result.memory_gb = [math]::Round($system.TotalPhysicalMemory / 1GB, 1)
    $result.hypervisor_present = [bool]$system.HypervisorPresent
} catch { $result.diagnostics += 'computer_system_query_unavailable' }
try {
    $result.processors = @(Get-CimInstance -ClassName Win32_Processor | ForEach-Object {
        [ordered]@{
            cores = $_.NumberOfCores
            virtualization_firmware_enabled = $_.VirtualizationFirmwareEnabled
            second_level_address_translation = $_.SecondLevelAddressTranslationExtensions
            vm_monitor_extensions = $_.VMMonitorModeExtensions
        }
    })
} catch { $result.diagnostics += 'processor_query_unavailable' }
try {
    $result.hyperv_feature = [string](Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All).State
} catch { $result.diagnostics += 'hyperv_feature_query_requires_access_or_elevation' }
try {
    $result.sandbox_feature = [string](Get-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM).State
} catch { $result.diagnostics += 'sandbox_feature_query_requires_access_or_elevation' }
$result.sandbox_executable_present = Test-Path -LiteralPath (Join-Path $env:SystemRoot 'System32\WindowsSandbox.exe')
$result.hyperv_cmdlets_present = [bool](Get-Command Get-VM -ErrorAction SilentlyContinue)
$result | ConvertTo-Json -Depth 5
