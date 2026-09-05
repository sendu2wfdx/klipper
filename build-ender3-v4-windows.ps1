$ErrorActionPreference = 'Stop'

# WSL 不可用时的可重复备用构建链。不覆盖 build-gd32/ 中的
# GCC 9 规范产物，所有输出单独放入 build-gd32-windows/。
$Root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$Bash = 'D:\Program Files\Git\bin\bash.exe'
$MakeBin = 'D:\make-3.81\bin'
$ArmBin = 'D:\RT-ThreadStudio\platform\env_released\env\tools\gnu_gcc\arm_gcc\mingw\bin'
$Python = 'D:\anaconda3\python.exe'
$Layout = if ($env:APP_LAYOUT) { $env:APP_LAYOUT } else { 'factory' }
if ($Layout -notin @('factory', 'katapult')) {
    throw "APP_LAYOUT must be 'factory' or 'katapult'"
}
$BuildRootName = if ($env:GD32_BUILD_ROOT) {
    $env:GD32_BUILD_ROOT
} else {
    'build-gd32-windows'
}
if ($BuildRootName -notmatch '^[A-Za-z0-9._-]+$') {
    throw "GD32_BUILD_ROOT must be a directory name inside the repository"
}

foreach ($Path in @($Bash, "$MakeBin\make.exe", "$ArmBin\arm-none-eabi-gcc.exe", $Python)) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Windows GD32 build dependency is missing: $Path"
    }
}

function Convert-ToMsysPath([string]$Path) {
    $Full = [IO.Path]::GetFullPath($Path).Replace('\', '/')
    if ($Full -notmatch '^([A-Za-z]):/(.*)$') {
        throw "Cannot convert path to MSYS form: $Full"
    }
    return '/' + $Matches[1].ToLowerInvariant() + '/' + $Matches[2]
}

$RootMsys = Convert-ToMsysPath $Root
$MakeMsys = Convert-ToMsysPath $MakeBin
$ArmMsys = Convert-ToMsysPath $ArmBin
$PythonMsys = Convert-ToMsysPath $Python
$RequestedTargets = @($args)
if ($RequestedTargets -contains 'linux') {
    throw "The Linux MCU must be built in T113 WSL, not the Windows fallback"
}
$Targets = if ($RequestedTargets.Count) {
    $RequestedTargets -join ' '
} else {
    'main nozzle bed'
}
$Command = @"
cd '$RootMsys' && \
export PATH='$MakeMsys`:$ArmMsys':`"`$PATH`" && \
export PYTHON='$PythonMsys' LTO_FLAGS='-flto=1' JOBS=2 && \
export BUILD_ROOT=`"`$PWD/$BuildRootName`" && \
export KLIPPER_BUILD_VERSION='gd32-source-rebuild' && \
export APP_LAYOUT='$Layout' && \
sh scripts/build-ender3-v4.sh $Targets
"@

& $Bash -lc $Command
if ($LASTEXITCODE -ne 0) {
    throw "Windows Ender-3 V4 build failed with exit code $LASTEXITCODE"
}
