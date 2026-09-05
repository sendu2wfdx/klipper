$ErrorActionPreference = 'Stop'

$Distro = 'T113'
$WindowsRepo = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$WindowsRepoForWsl = $WindowsRepo.Replace('\', '/')
$WslPathOutput = & wsl.exe -d $Distro -- wslpath -a -u $WindowsRepoForWsl
if ($LASTEXITCODE -ne 0) {
    throw "Unable to map repository path into the $Distro WSL distribution"
}
$WslRepo = ($WslPathOutput | Select-Object -First 1).Trim()
if (-not $WslRepo) {
    throw "Unable to map repository path into the $Distro WSL distribution"
}
$LinuxToolchain = '/home/lenovo/ATK-DLT113IS-V1.0/prebuilt/rootfsbuilt/arm/toolchain-sunxi-glibc-gcc-830/toolchain'
$LinuxPrefix = "$LinuxToolchain/bin/arm-openwrt-linux-gnueabi-"
$Layout = if ($env:APP_LAYOUT) { $env:APP_LAYOUT } else { 'factory' }
if ($Layout -notin @('factory', 'katapult')) {
    throw "APP_LAYOUT must be 'factory' or 'katapult'"
}

$WslArguments = @(
    '-d', $Distro, '--cd', $WslRepo, '--', 'env',
    "APP_LAYOUT=$Layout",
    "LINUX_CROSS_PREFIX=$LinuxPrefix",
    "LINUX_STAGING_DIR=$LinuxToolchain",
    'JOBS=32',
    'sh', 'scripts/build-ender3-v4.sh'
) + @($args)
& wsl.exe @WslArguments
if ($LASTEXITCODE -ne 0) {
    throw "Ender-3 V4 build failed with exit code $LASTEXITCODE"
}
