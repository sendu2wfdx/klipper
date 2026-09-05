# F009 / GD32F303 向当前官方 Klipper 移植记录

## 目标与基线

- 目标：把 Creality F009 所需的 GD32F303 平台、启动偏移、串口通信和固件校验能力移植到当前官方 Klipper。
- 官方 Klipper 基线：`f0892d82b0f1c1228454f09eb508eddde2250f4b`（2026-08-27）。
- 工作分支：`gd32-f009`。
- 平台底座不引入闭源对象；后续已加入自主源码的 PRTouch V3 兼容层。BOX/CFS
  不属于当前 MCU 阶段，485 工具板另行设计。

## 已完成的移植

### GD32F303 平台

- 增加 GD32 架构入口、芯片型号、时钟、Flash/RAM 和启动偏移配置。
- 当前支持 GD32F303xB、xC、xE，F009 使用 `GD32F303xC`。
- 系统时钟为 120 MHz。
- 平台同时支持原厂 12 KiB Bootloader（应用入口 `0x08003000`）和当前 Katapult 8 KiB 布局（应用入口 `0x08002000`）。默认一键构建已切换为 Katapult 布局。
- 保留原厂固件需要的尾部 CRC16 生成流程；自动刷写仍禁用，避免误写原厂板卡。
- 已接入 GPIO、ADC、SPI、I²C 和串口底层。I²C 已替换创想仓库的空实现，支持起始/重复起始、读写、STOP、NACK 和超时返回；当前仍属“编译验证”，需要实机电气和传输测试。

### I²C 补全

- GD32F303：`i2c0` PB6/PB7、`i2c0a` PB8/PB9（重映射）、`i2c1` PB10/PB11。
- GD32E230：`i2c0` PB6/PB7，AF1。
- 支持 100 kHz 标准模式和最高 400 kHz 快速模式的时钟计算。
- 接入官方 Klipper `I2C_BUS_*` 返回码，上位机能区分地址 NACK、读起始 NACK 和超时。
- F303 与 E230 配置均已完成交叉编译及链接。
- 仍需用逻辑分析仪验证实际上拉、电平、引脚复用、单字节/多字节读取、NACK 恢复和总线锁死恢复。

2026-08-30 完善项：

- I²C BUSY 或传输超时后会切换为开漏 GPIO，最多输出 9 个 SCL 脉冲并生成 STOP。
- 恢复后软复位 I²C 外设，恢复时钟参数、引脚复用和 ACK 状态。
- 阻止 `i2c0`/`i2c0a` 同时占用同一控制器，也阻止同一总线以不同速率重复初始化。

### SPI 补全

#### 原始状态

Creality GitHub 的 `src/gd32/spi.c` 只有空的 `spi_setup()`、`spi_prepare()` 和 `spi_transfer()` 框架：没有总线表、引脚复用、时钟分频、数据收发或异常处理，不具备实际 SPI 通信能力。

#### 已实现总线

| Klipper 总线名 | 控制器 | MISO | MOSI | SCK | 外设时钟 |
|---|---|---|---|---|---|
| `spi0` | SPI0 | PA6 | PA7 | PA5 | APB2 |
| `spi1` | SPI1 | PB14 | PB15 | PB13 | APB1 |

F303 使用浮空输入 MISO 和推挽复用 MOSI/SCK；E230 使用 AF0、无内部上下拉、推挽输出。片选 CS 由 Klipper 通用 GPIO 层控制，不由 SPI 外设 NSS 自动控制。

#### 传输能力

- 实现 Klipper 的 `spi_setup()`、`spi_prepare()` 和 `spi_transfer()` 完整接口。
- 支持 SPI mode 0、1、2、3（CPOL/CPHA）。
- 默认 MSB first、8 bit、主机、全双工、软件 NSS。
- 根据 APB 时钟在 2、4、8、16、32、64、128、256 分频中，选择不超过请求速率的最快 SCK。
- `spi_prepare()` 允许同一 SPI 控制器上的多个设备切换 mode 和速率。
- `receive_data=0` 时仍正常读空接收寄存器，避免 RX 溢出；`receive_data=1` 时用接收字节覆盖输入缓冲区。

#### 错误和超时处理

- TBE（发送空）、RBNE（接收非空）和 TRANS/BUSY 均有 5 ms 硬超时。
- 超时后禁用 SPI，读取 STAT/DATA 清理残留状态，按当前 mode/分频重新启用外设。
- 恢复后调用 Klipper `shutdown("SPI transfer timeout")`，防止运动或加热状态下 MCU 永久死等。

#### 验证状态

- F009/GD32F303 串口版：干净编译、链接和 CRC16 打包通过。
- F009/GD32F303 USB 版：干净编译、链接和 CRC16 打包通过。
- GD32E230 PA2/PA3 与 PA9/PA10 版：干净编译、链接和 CRC16 打包通过。
- 编译环境固定为 `T113` WSL、GCC 9.2.1、GNU Make 4.2.1；构建入口见 `BUILD_ENVIRONMENT.md`。

尚未完成实机电气验证：需要用逻辑分析仪核对 mode 0–3、SCK 频率、MISO/MOSI 时序、多设备切换、长时间传输和拔除从机时的超时停机。E230 还需按实际封装核对 PB13/PB14/PB15 是否引出。

### F009 原厂通信路径

- 通信接口：USART1。
- 引脚：PA2（TX）、PA3（RX）。
- 波特率：230400。
- 配置文件：`config/f009_gd32f303_serial.config`。

### USB CDC 补全

Creality 仓库中的 GD32 USB 文件混有 AT32/LPC 寄存器和未定义端点符号，不能视为完整实现。本移植没有沿用该文件，而是：

- 以当前官方 Klipper 的 USB CDC 协议栈为主体；
- 为 GD32F303 的 USBD 外设增加寄存器映射和中断/时钟初始化；
- 使用 PA11（DM）和 PA12（DP）；
- 120 MHz PLL 经 2.5 分频得到 48 MHz USB 时钟；
- USB 序列号取自芯片 UID；
- 配置文件：`config/f009_gd32f303_usb.config`。

USB 版本已在 GD32F303CCT6 开发板完成枚举、双向 Klipper 消息和完整 PRTouch
协议实测。移植修复包括 32 位端点槽访问、单事件 CTR ISR、重建 PMA 前的 USBD
外设复位、GD32 SysTick 重装载，以及公版 Klipper 的双缓冲 push/pop 竞争模型。
双缓冲现已通过 100 次 COM 重开、1700 个只读请求和 PRTouch 协议共存回归；它仍是
F009 量产前的候选项，需补物理拔插、挂起恢复、长时间大流量和 RCT6 原板门禁。只有目标板实际
引出了 PA11/PA12、USB 上拉和接口电气设计匹配时才能使用；开发板结果不能
证明原厂 F009 现有插口已经连接到这两个引脚。

### CAN 补全

GD32F303 CAN0 已接入官方 Klipper `canserial/canbus` 协议层，支持 PA11/PA12、PB8/PB9 和 PD0/PD1 三种映射。F009 示例构建选择 PB8/PB9、1 Mbps、8 KiB Katapult 偏移，并使用芯片 UID 生成 CAN UUID。

CAN 需要独立 CAN 收发器、CANH/CANL 和正确终端电阻；原厂485接口的收发器和物理层不能直接复用。详细实现及测试要求见 `GD32F303_CAN_PORTING.md`。

## 构建结果

| 版本 | text | data | bss | BIN SHA-256 | CRC16 |
|---|---:|---:|---:|---|---|
| F009 USART1（Katapult 8 KiB） | 34198 | 52 | 1048 | `396464ca31599186e16904ad1c61e911dd55d7c3bad679075be837ada6fbee4e` | 无（Katapult 裸 BIN） |
| GD32F303 USB CDC（Katapult 8 KiB） | 35846 | 52 | 1168 | `b575d433ed88e1f0f784650edb2b1817c6ba00fe4bcbe8fd6b2b94d6878e22a7` | 无（Katapult 裸 BIN） |
| GD32F303 CAN0 PB8/PB9（1 Mbps，Katapult 8 KiB） | 35766 | 52 | 1292 | `99651e72e52d7a7f6aad604b0d32df41cb4e9f187620ebe4f68f1d240ce65137` | 无（Katapult 裸 BIN） |

启用自主 PRTouch V3 兼容层的 F009 工具头构建位于
`build-gd32/f009-toolhead/klipper.bin`，当前 `text/data/bss=41418/52/9068`，
SHA-256 为
`9b5aafb877497ff3761c21d9ee32c43d5970c0e62687f6a7e75fe1cc502f63c3`。
该镜像面向 8 KiB Katapult、USART1 PA2/PA3、230400 baud。保留原厂 12 KiB
Bootloader 的独立目标为 `f009-toolhead-factory`：入口 `0x08003000`，版本
`noz0_019_000`，长度 `41784 (0xA338)`，CRC16 `0x6B72`，SHA-256
`35599ceafb1fa67572c502313044f2a9cf267398ce5ab9911c928c090c0a1f94`。
两种镜像功能源码相同，但链接入口和封装不同，不可互换。

上述工具头值使用固定版本串 `gd32-source-rebuild`，对应 2026-09-05 的
PRTouch 下降沿修正后 T113/GCC 9 构建。Katapult 应用 BIN 不使用原厂 CRC16 封装。

### GPIO 重构（2026-08-30）

- 输出引脚先预装输出锁存值，再切换到输出模式，避免初始化瞬间产生反向毛刺。
- JTAG 关闭改为只更新 `SWJ_CFG` 字段：保留 SWD，并保留已经设置的 I²C 等其它复用位。
- 删除厂商通用重映射函数中会大范围覆盖 AFIO 寄存器的路径；当前只允许本平台实际使用的 I²C0 重映射，不支持的映射会安全停机。
- F303 使用官方 Klipper 的 Cortex-M DWT 32 位定时器，本轮无需另写硬件定时器后端。

归档产物位于 `../builds/klipper-gd32-20260901/`：

- `f009-gd32f303-katapult8k-uart-pa2-pa3.bin`
- `f009-gd32f303-katapult8k-usb.bin`
- `f009-gd32f303-katapult8k-can-pb8-pb9.bin`

## 已验证与尚未验证

### GD32F303CCT6 开发板实测（2026-09-01）

使用一块 GD32F303CCT6 开发板、ST-Link V2 和板载/外接 USB 完成了首次
硬件通信验证。测试固件均采用无 Bootloader 布局，直接链接到
`0x08000000`，避免把平台通信测试与 Katapult 跳转混在一起。

- SWD 连接电压 3.24 V，完整 256 KiB Flash 已在烧写前备份；
- USART0 PA9/PA10、230400 baud 通过 Windows `COM11` 完成 Klipper
  `identify` 分块读取和字典解压；固件报告 `MCU=gd32f303xc`、
  `CLOCK_FREQ=120000000`、`SERIAL_BAUD=230400`；
- USB PA11/PA12 成功枚举为 `VID=1d50`、`PID=614e` 的 CDC 设备，Windows
  分配 `COM12`，芯片 UID 序列号为 `B3F23B871C00B0014A333532`；
- USB CDC 同样完成 Klipper `identify` 分块读取和字典解压，证明该开发板上
  48 MHz USB 时钟、PMA 端点、中断和双向 Klipper 消息传输可工作；
- 压力、STEP、APAX 完整会话连续三次硬件复位通过；早期双缓冲失稳已追到端点
  所有权、SysTick 和主机序号歧义，当前候选完成 100 次重开/1700 请求联合回归；
- 回归结束后 Cortex-M3 `CFSR=0`，未出现早期调试 ISR 紧循环造成的
  imprecise bus fault；
- 串口测试使用 PA9/PA10，刻意避开 F009/T113 量产链路使用的 PA2/PA3。

本次结果把 F303 UART 和 USB 从“仅编译验证”推进到“CCT6 开发板通信实测
通过”。随后 V57 源码兼容应用已在 F009 RCT6 主板经原厂 12 KiB Bootloader
刷写，并完成 PA2/PA3 通信、三轴、TMC、限位、PRTouch 联动、配置上限运动、急停
恢复和 45℃热床闭环。这些证据关闭了目标硬件和 GD32 平台的基础疑问，但不能直接
代替本目录公版功能版二进制的三板组合验收，也尚未验证 Katapult Flash 擦写与应用跳转。

已验证：

- 串口、USB、CAN 三套 F303 配置均能在现有 ARM GCC 9 工具链下完成编译、链接及 BIN 生成。
- 默认 F009 串口/USB/CAN 版使用 8 KiB Katapult 偏移和普通 Katapult 应用 BIN，不附加原厂 CRC16 封装。原厂 12 KiB/CRC16 路线仅保留为迁移前兼容配置。
- USB 版使用当前官方 Klipper 的 CDC 消息层，而不是 Creality 未完成的旧 USB 文件。

仍需实机验证：

1. 原厂三块 MCU 完整 Flash 已备份，主板和床身板也已反证原厂 BL 保留更新链可用；
   公版线首次上板仍需按单板、单变量方式执行，不跳过恢复件复核。
2. 先用原厂 12 KiB BL 布局验证本目录的公版主板/喷头/床身应用，再在独立可恢复板
   验证 8 KiB Katapult，避免同时改变应用协议与 Bootloader。
3. PA2/PA3 的 Klipper 通信已在量产主板运行通过；原始边沿、电平余量和连接器针序
   仍可由示波器或逻辑分析仪补档。
4. USB 枚举、消息、单/双缓冲和 100 次句柄重开已在独立开发板通过；双缓冲仍需
   补长时间大流量、反复物理热插拔、挂起恢复，以及目标 F009 板的 USB 电气确认。
5. 核对 UID 地址、USB D+ 上拉拓扑、PA11/PA12 是否实际引出。
6. 对公版固件重新验证看门狗复位、急停、步进、热敏 ADC、CS1237、LIS2DW、风扇
   测速和 I²C；V57 源码线的通过结果只提供对照，不能直接授予公版线无人值守资格。

## 与后续 CS1237 工作的边界

本阶段只解决 MCU 平台及通信。下一阶段的 CS1237 应分为两层：

- MCU 侧增加确定时序的采样、时间戳和批量上报；
- 主机侧接入官方 `[load_cell_probe]` 的滤波、基线、阈值和触发状态机。

喷头触底、压片刚接触活塞、活塞到底不是单靠三个固定力度阈值就能稳健区分。应结合运动方向、当前执行阶段、力值斜率、持续时间和位置窗口识别，并为机械硬限位或电机堵转保留独立安全保护。
