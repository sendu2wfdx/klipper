# Ender-3 V4 Klipper 构建环境

## 正式目标

`scripts/build-ender3-v4.sh` 的默认目标固定为 `main nozzle bed linux`：

| 目标 | 配置 | MCU/接口 | APP 入口 |
|---|---|---|---:|
| `main` | `config/f009_main.config` | GD32F303RCT6（xC/256 KiB），USART1 PA2/PA3，230400 | `0x08003000` |
| `nozzle` | `config/f009_nozzle.config` | GD32F303CB（xB/128 KiB），USART1 PA2/PA3，230400 | `0x08003000` |
| `bed` | `config/f009_bed.config` | GD32E230F8（x8/64 KiB），USART0 PA9/PA10，230400 | `0x08003000` |
| `linux` | `config/f009_linux.config` | Linux process MCU（整机配置中的 `rpi`） | 不适用 |

这三个 GD32 配置就是 Ender-3 V4 的正式 MCU 配置。早期把主板记录成
GD32F303RET6/xE/512 KiB 是料号误判；`f009_mainboard_gd32f303re_*` 仅作为历史
开发变体保留，不能用于 F009 实物。热床正式串口是 PA9/PA10，PA2/PA3 配置也只作
平台开发验证。

## T113 规范构建

- WSL 发行版：`T113`
- MCU 编译器：`arm-none-eabi-gcc` 9.2.1
- GNU Make：4.2.1
- T113 SDK：`/home/lenovo/ATK-DLT113IS-V1.0`
- Linux MCU 交叉编译器：SDK 内的 `arm-openwrt-linux-gnueabi-` 工具链
- Windows 工作区：`D:\Documents\ChatGPT\创想三维\mcu\Official_Klipper_GD32`
- WSL 工作区：`/mnt/d/Documents/ChatGPT/创想三维/mcu/Official_Klipper_GD32`

在 Windows 中运行：

```powershell
cd "D:\Documents\ChatGPT\创想三维\mcu\Official_Klipper_GD32"
git submodule update --init --recursive
.\build-ender3-v4.ps1
```

PowerShell 入口固定使用 `T113`，并把 SDK 的 ARM Linux 交叉编译前缀和对应的
`LINUX_STAGING_DIR` 明确传给构建脚本。因此本地
`build-gd32/linux/klipper.elf` 是可供 T113 验证的 ARM ELF，不是 WSL 的 x86-64
程序。手工指定 `LINUX_CROSS_PREFIX` 时也必须同时指定 `LINUX_STAGING_DIR`；这样
不会依赖 OpenWrt 编译器包装器从路径猜测环境。

默认输出为：

```text
build-gd32/
├── main/       klipper.bin, klipper.dict, klipper.elf, bootloader.bin
├── nozzle/     klipper.bin, klipper.dict, klipper.elf, bootloader.bin
├── bed/        klipper.bin, klipper.dict, klipper.elf, bootloader.bin
├── linux/      klipper.elf, klipper.dict
└── reports/    build-report.txt
```

三个 `klipper.bin` 都会检查向量表、实际复位地址、RAM 栈顶、目标版本、容量、声明
长度和 CRC16。三个 `bootloader.bin` 会检查 12 KiB 上界、向量、RAM 栈顶，以及
`mcu0_140_G31`、`noz0_110_G30`、`bed0_110_G21` 三个目标标识。报告中的哈希从
本次布局对应的输出目录取得，不会复用另一布局的旧文件。

## 12 KiB Bootloader 的边界

`src/bootloader` 是固定提交的子模块，内容为根据公开代码和实测行为独立重构的 UART
兼容 Bootloader。它能随三块正式 APP 一起编译，但不是创想三维原厂源码，也不保证
与原厂 BL 逐字节相同，当前仍属于实验复刻实现。

生产板原厂 Bootloader 位于 `0x08000000..0x08002fff`，普通在线升级只应写入从
`0x08003000` 开始的 `klipper.bin`。除非已经连接 SWD、保存完整 Flash 和选项字节，
并明确要测试兼容复刻 BL，否则不要写入构建出的 `bootloader.bin`。

## Katapult 开发布局

同一套 `main/nozzle/bed` 配置可显式切换为 8 KiB Katapult APP：

```powershell
wsl.exe -d T113 -- bash -lc "cd '/mnt/d/Documents/ChatGPT/创想三维/mcu/Official_Klipper_GD32' && APP_LAYOUT=katapult JOBS=32 sh scripts/build-ender3-v4.sh main nozzle bed"
```

Katapult 输出位于 `build-gd32/katapult/main`、`nozzle`、`bed`，入口为
`0x08002000`，不会编译 12 KiB 兼容 BL。原厂 12 KiB APP 与 Katapult 8 KiB APP
不可互换。

## Windows 备用构建

WSL 暂时不可用时，可运行：

```powershell
.\build-ender3-v4-windows.ps1
```

备用入口只编译 `main nozzle bed`，使用 Windows GNU Arm Embedded 工具链，输出到
`build-gd32-windows/`。它明确拒绝 `linux` 目标；Linux MCU 必须在 Linux 环境中
构建。备用链用于源码、链接和布局诊断，不取代 T113/GCC 9 的正式结果。

## GitHub Actions 的 Linux 边界

公开 CI 会递归检出 Bootloader 子模块，运行 Klipper 与子模块测试，构建三块 F009
MCU 和一个 Linux process MCU，并加载整机配置。GitHub runner 上没有 T113 SDK，
所以其中 Linux ELF 仅是宿主机编译 smoke test，不是 T113 可部署固件，也不会上传。

工作流只上传以下显式文件：

- `main/nozzle/bed` 的 `klipper.bin` 与 `klipper.dict`；
- 三块板各自的兼容复刻 `bootloader.bin`；
- `reports/build-report.txt`。

归档名为 `ender3-v4-source-builds`，文档和报告均明确区分兼容复刻 BL 与原厂 BL。

## 换行和可重复构建

共享工作区应设置：

```powershell
git config --local core.autocrlf false
```

不要对公版 Klipper 全仓执行 `git add --renormalize .`。新增或修改的自有 C、Python、
Shell 和配置文件使用 LF。构建脚本先把跟踪的配置复制到输出目录，再执行
`olddefconfig`，因此验证构建不会改写仓库内的配置文件。

GD32 的 Windows 与 WSL GCC/LTO 版本不同，ELF 体积和哈希不应跨工具链比较。固定
`KLIPPER_BUILD_VERSION` 并在两个全新输出目录中比较实际烧录的 BIN，才是有效的
可重复性检查。
