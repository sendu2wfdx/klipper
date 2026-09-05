# Klipper GD32 / F009 开发分支

本目录基于官方 Klipper `f0892d82b0f1c1228454f09eb508eddde2250f4b`，增加独立 `src/gd32` 平台、GD32F303/GD32E230、UART/USB/CAN、I²C/SPI/看门狗以及 CS1237 支持。

当前默认构建配置面向本项目移植的 8 KiB Katapult，应用入口为 `0x08002000`。原厂 F009 12 KiB Bootloader 使用 `0x08003000`，两类固件不得混刷。固定构建环境见 `BUILD_ENVIRONMENT.md`，平台状态见 `GD32_F009_PORTING.md` 和 `GD32_PLATFORM_AUDIT.md`。

当前状态：GD32F303CCT6 开发板 USART0、USB CDC 稳定单缓冲和 PRTouch
压力/STEP/APAX 协议已实测通过；修复后的 USB 双缓冲已完成 100 次端口重开、
1700 次只读请求及完整 PRTouch/APAX 连续协议测试，但仍作为显式实验选项，默认
配置暂不切换。F009 的硬件事实已经由 V57 兼容线实机确认：主板 USART1、三轴运动、
TMC、限位、热床和 PRTouch 联动通过，床身 E230 的 Flash/复位和 LIS2DW 通过。
这些结果可证明底层硬件可用，但不等于本目录的公版固件已经完成三板整机替换；公版线
仍需喷头 CS1237/PA15/PC7、热端/风扇/挤出和三板组合验收。首次刷写必须保留 SWD
和原始 MCU dump。

另有显式构建目标 `k1-leveling-v2`，用于 K1 的 GD32E230x8 四通道压力调平板。它采用
K1 官方公开的 `src/prtouch_v2.c`、原厂 12 KiB 应用偏移和 PA9/PA10 串口，并自动检查
V1/V2 共用的 12 条命令、9 条响应；该目标尚未经过四通道调平板实测。

型号边界已经按实物锁定：上面的 `CCT6` 只指当前可测试开发板；F009 主板实物是
`GD32F303RCT6`（64 引脚、256 KiB），其正确软件目标为 `gd32f303xc`。仓库内
`f009_mainboard_gd32f303re_*` 仅保留作早期 512 KiB/xE 历史变体，不参与 F009
构建矩阵。

所有 GD32 镜像都在应用相对偏移 `0x200` 固定保留原厂 18 字节应用元数据区，
避免 CRC 后处理覆盖普通代码。原厂布局构建会自动填写并验证版本、CRC16 和长度；
8 KiB Katapult 布局保留相同版本字段，但由 Katapult 管理应用校验与更新。

---

Welcome to the Klipper project!

[![Klipper](docs/img/klipper-logo-small.png)](https://www.klipper3d.org/)

https://www.klipper3d.org/

The Klipper firmware controls 3d-Printers. It combines the power of a
general purpose computer with one or more micro-controllers. See the
[features document](https://www.klipper3d.org/Features.html) for more
information on why you should use the Klipper software.

Start by [installing Klipper software](https://www.klipper3d.org/Installation.html).

Klipper software is Free Software. See the [license](COPYING) or read
the [documentation](https://www.klipper3d.org/Overview.html). We
depend on the generous support from our
[sponsors](https://www.klipper3d.org/Sponsors.html).
