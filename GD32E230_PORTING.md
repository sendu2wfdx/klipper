# GD32E230 移植与验证记录

## 目标

把创想分支中的 GD32E230 平台层移植到当前官方 Klipper，使 Ender-3 V4 使用的 GD32E230F8P6 类下位机可以基于上游代码继续开发。

## 已实现

- 新增 GD32E230x6 / GD32E230x8 Kconfig 目标。
- GD32E230F8P6 参数：Cortex-M23、72 MHz、64 KiB Flash、8 KiB SRAM。
- 移植 GD32E23x 厂商外设库、启动、GPIO、ADC、UART 和 16 位定时器扩展代码。
- 重写 Creality 原本的空 SPI/I²C 框架，接入当前官方 Klipper 外设接口。
- 补入 CMSIS `core_cm23.h`。
- 修正当前上游 `armcm_boot.c` 对 Cortex-M23 的 NVIC/SCB 寄存器成员兼容。
- 两套 UART 映射均通过编译；原机固件已确认量产板使用 USART0 PA9/PA10，
  USART1 PA2/PA3仅作为平台开发/改板变体保留。
- 新增的 CS1237 MCU 后端也参与 E230 构建并链接成功。
- E230x6/x8 现在选择 `HAVE_LIMITED_CODE_SIZE` 并使用 `-Os`，确保保留 12 KiB
  Bootloader 后的 52 KiB 应用区不会被公版默认功能集挤爆。
- 新增 K1 四通道调平板 V1/V2 目标，并接入 K1 官方公开的 PRTouch MCU 源码。

## GPIO 重构

- GD32E230x6 和 x8 都生成 GPIO 端口表，不再出现 x6 构建缺失底层定义的问题。
- 所有模式、输出类型和复用配置统一经过端口有效性检查；不存在的 D/E 端口会进入 Klipper shutdown，而不是访问地址 0。
- 输出引脚先写锁存值、再切换输出模式，减少启动时 STEP、EN、电磁阀等信号的毛刺风险。
- 输入的三态语义已修正：正数为上拉、零为下拉、负数为浮空，避免 Klipper 请求浮空时被误设为上拉。
- 修正 `gpio_out_read()` 对 PA8～PA15 等高位引脚的返回值截断，现在稳定返回 0 或 1。

端口枚举表示芯片系列能力，并不保证某一封装引出了全部 PA/PB/PC/PF 引脚；工具板布线仍应以完整料号的数据手册为准。

## 定时器重构

原 Creality E230 定时器把 Klipper 的下一事件写进自动重装载寄存器，并依赖更新中断；同时预分频值会把 72 MHz 除以二。这不符合 Klipper 所要求的自由运行计数器加比较事件模型。

现实现改为：

- TIMER2 保持 16 位自由运行，自动重装载值固定为 `0xffff`。
- 预分频为 0，计数频率与 `CONFIG_CLOCK_FREQ=72 MHz` 一致。
- 使用通道 0 比较寄存器和比较中断安排下一次调度事件。
- 每半个 16 位周期维护一次高位扩展，将硬件计数器扩展成 Klipper 32 位时钟域。
- 比较标志按 GD32 的写零清除语义处理，`timer_kick()` 可在短延时后强制唤醒调度器。

该实现已通过编译和链接，但属于实时核心代码。实机必须验证 MCU 时钟同步、计数回绕、长时间步进、高中断负载、急停和重连；在这些测试完成前不能承担加热或无人值守运动。

## I²C 实现

- Klipper 总线：`i2c0`。
- 控制器：GD32E230 I2C0。
- 引脚：PB6=SCL、PB7=SDA，AF1，开漏并启用内部上拉；板上仍应配置合适的外部上拉。
- 支持 100 kHz 标准模式及最高 400 kHz 快速模式。
- 支持 START、repeated START、STOP、寄存器写后读、单/多字节读写。
- 向 Klipper 返回成功、地址 NACK、读地址 NACK 和超时状态。
- BUSY/超时时切换为开漏 GPIO，输出最多 9 个 SCL 恢复脉冲并生成 STOP，随后软复位外设并恢复时钟/复用配置。
- 禁止同一控制器以冲突速率重复初始化。

## SPI 实现

Creality 源码中的 GD32 `spi.c` 只返回空配置，传输函数直接返回。本移植已实现：

| Klipper 总线 | 控制器 | MISO | MOSI | SCK | E230 复用 |
|---|---|---|---|---|---|
| `spi0` | SPI0 | PA6 | PA7 | PA5 | AF0 |
| `spi1` | SPI1 | PB14 | PB15 | PB13 | AF0 |

- 支持 mode 0–3、MSB first、8 bit、主机、全双工和软件 NSS。
- 根据 SPI0/APB2 或 SPI1/APB1 时钟自动选择 2–256 分频，SCK 不超过 Klipper 请求速率。
- 同一控制器可在传输前切换不同设备的 mode 和速率。
- TBE、RBNE 和 TRANS/BUSY 等待均有 5 ms 超时。超时后重置 SPI 状态并进入 Klipper shutdown，不会无限阻塞 MCU。

注意：代码层支持 SPI1 不等于 GD32E230F8P6 的实际封装已引出 PB13/PB14/PB15。制板前必须根据完整料号的 pinout 表确认；未引出时只使用 `spi0`。

## 构建配置

- `config/gd32e230f8_serial_pa2_pa3.config`
- `config/gd32e230f8_serial_pa9_pa10.config`
- `config/gd32e230f8_serial_pa9_pa10_factory12k.config`（量产板原厂 BL 保留方案）
- `config/k1_leveling_gd32e230x8_factory12k_prtouch_v2.config`（K1 四通道
  压力调平板，V1/V2 共用 MCU 协议）

无 Bootloader 的纯平台验证配置另存为：

- `config/gd32e230f8_serial_pa2_pa3_noboot.config`
- `config/gd32e230f8_serial_pa9_pa10_noboot.config`

默认两套配置当前选择 8 KiB Katapult，应用入口 `0x08002000`。另保留明确命名的 `*_noboot.config` 用于纯平台验证。量产板若保留原厂 Bootloader，则必须使用 `gd32e230f8_serial_pa9_pa10_factory12k.config`，应用入口为 `0x08003000`。原机 `CR0NN200360C10_gd32e230_64K_backup_20260831.bin` 在偏移 `0x9154` 含可解压的 Klipper数据字典，其中明确记录：

```text
MCU=gd32e230x8
RESERVE_PINS_serial=PA9,PA10
SERIAL_BAUD=230400
```

因此 F009量产调平板的应用串口已经收敛为 USART0 PA9/PA10。完整 Flash 在 `0x0000` 与 `0x3000` 都有合法 Cortex-M 向量表，预打包应用向量表又与完整备份的 `+0x3000` 区域一致；原厂 Bootloader 为 12 KiB、应用入口为 `0x08003000` 已有独立双重证据。

## 验证结果

| 配置 | ELF 占用（text/data/bss） | BIN 长度 | 入口地址 |
|---|---:|---:|---:|
| PA2/PA3（Katapult 8 KiB） | 38828 / 64 / 1052 B | 38892 | `0x08002000` |
| PA9/PA10（Katapult 8 KiB） | 38812 / 64 / 1052 B | 38876 | `0x08002000` |
| PA9/PA10（原厂 BL 12 KiB） | 39286 / 64 / 1052 B | 39700 | `0x08003000` |

两个目标均为 ELF32 ARM，使用 GCC 9 `arm-none-eabi` 工具链编译通过。

2026-09-05 新增的 K1 V1/V2 目标也已在 T113 中完成冷构建：
`text/data/bss=30430/100/2600`，BIN 长度 30880，SHA-256
`5603cb9c752ef3c390f0394ba32289706e130fe901a607c87d5c28e2259443d8`。消息字典与 K1
官方 V1、V2 两份主机包装器均匹配 12/12 条命令、9/9 条响应；尚缺四通道调平板实物
验证，不能与 F009 的单纯 Y 加速度计床身小板混为一块硬件。

本轮 BIN SHA-256：

- PA2/PA3：`3c34d81c53a9984be11c15ba7f5c922099b969a774b45b7a2e3832aecbbcbeb5`
- PA9/PA10：`05cbeac0f8669f84350a59ec169fff007235f00d749e486a8e4173f83a3f7a69`
- PA9/PA10、原厂 12 KiB BL：`65d8d4b76c9af189ccef4cabde9f226965c978a093ad1b99106477ae3b717591`

原厂 12 KiB 目标已由封装校验器确认：版本 `bed0_017_000`、长度
`39700 (0x9B14)`、CRC16 `0xB9AD`；链接和向量入口均从 `0x08003000`
开始，未占用 Bootloader 的 `0x08000000..0x08002fff` 区间。

## F009 V57 源码替换实机结果（2026-09-04）

F009 量产机上的 E230 仅承载 Y 向 LIS2DW 加速度计，不负责热床加热或测温。本次实测
使用 `../Klipper57_Source_Rebuild/src/configs/F018_bed0_110_G21_defconfig` 构建的 V57
等价源码应用，而不是上表中用于通用上游移植验证的较大镜像。实刷文件为：

```text
../builds/bed-e230-live-candidate-20260904/out/klipper.bin
长度：28260 B
版本：bed0_017_000
CRC16：0x209F
SHA-256：af772eb46c65550a081bf773866a37b58e9383a096bf99e5f2c4f0d22be03b12
运行版本：ender3v4-v57-source
```

刷写走量产机原厂流程：先向运行中的 Klipper MCU 发送标准 `reset` 命令进入保留的原厂
BL，再用 `/usr/bin/mcu_util -i /dev/ttyS4 -u -f <应用文件>` 更新应用。刷写退出码为 0，
没有使用 SWD、整片擦除或从 `0x08000000` 写入。

刷后验证结果：

- Klipper 识别为 `ender3v4-v57-source`、`gd32e230x8`、230400 baud，并保持 Ready；
- Y 向 `ACCELEROMETER_QUERY` 连续 10 次成功，BL 复位闭环后再成功 1 次，静态矢量
  约为 1 g；
- `bytes_invalid=0`，没有 MCU shutdown 或串口通信错误；
- 再次从源码应用复位后，原厂 BL 仍能握手并返回
  `bed0_110_G21-bed0_017_000`，还能用 `mcu_util -s` 正常启动源码应用。

所以原厂 12 KiB BL 已获得刷后实证，不只是链接地址推断。原厂恢复应用已从 64 KiB
完整备份的 `+0x3000` 区域提取为
`../builds/bed-e230-live-candidate-20260904/factory-bed0_017_000-recovery.bin`，长度
28616 B、CRC16 `0x8C8E`、SHA-256
`ac40e42f6c97ad9352ed761cde835189c86434cda20d8526de1a11a92a947651`。回退仍必须使用
原厂 BL 的应用更新流程，不能把该应用文件当作完整 Flash 镜像写入起始地址。

## USB 结论

GD32E230 的厂商器件头文件和外设库中没有 USB/USBFS 控制器定义，F8P6 这个目标不能靠软件补出原生 USB。因此 E230 目标只开放 UART；如果工具板必须原生 USB，应选择 RP2040、STM32F072 或具有 USB 外设的 GD32/STM32 型号。

## 上板前必须确认

1. 按实际安装的引导程序选择布局：公版 Katapult 使用 `0x08002000`，保留原厂 12 KiB Bootloader 使用 `0x08003000`，无引导版本从 Flash 起点运行；三者不可混刷。
2. SWD 连接、复位脚和读保护状态，并先备份原 MCU Flash。
3. 核对板上晶振/时钟方案；当前平台初始化沿用创想 E230 的 72 MHz配置。
4. 首次测试先只验证 `get_config`/心跳和 GPIO，不连接加热、运动等高风险负载。
5. 核对 PB6/PB7 的 I²C外部上拉、逻辑电压和 AF1波形。
6. 核对 PA5/PA6/PA7 及可选 PB13/PB14/PB15 是否在实际封装引出。
7. 用逻辑分析仪验证 I²C 100/400 kHz、SPI mode 0–3、分频、长时间传输、NACK及总线恢复。

## 构建命令

```powershell
cd "D:\Documents\ChatGPT\创想三维\mcu\Official_Klipper_GD32"
.\build-gd32.ps1 e230-pa23
.\build-gd32.ps1 e230-pa910
```

固定使用 `T113` WSL 内的 GCC 9.2.1 和 GNU Make 4.2.1，产物位于 `build-gd32/e230-pa23/` 和 `build-gd32/e230-pa910/`。详见 `BUILD_ENVIRONMENT.md`。
