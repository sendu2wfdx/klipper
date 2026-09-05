# GD32F303 Klipper CAN 移植记录

日期：2026-08-31

## 实现范围

本实现为 GD32F303 的 CAN0 增加原生 Klipper CAN 通信。上层直接使用当前官方 Klipper 的：

- `generic/canserial.c`：Klipper 串行协议在 CAN 帧上的封装；
- `generic/canbus.c`：队列、状态和过滤器管理；
- `fast-hash`：由 96 位芯片 UID 生成6字节 CAN UUID。

底层 `src/gd32/can.c` 负责 GD32 CAN0 邮箱、FIFO0、过滤器、位时序、中断和错误状态。它不是 Modbus/RS485，也不能用于原厂485物理接口。

## 支持的引脚映射

| 配置 | RX | TX | AFIO 映射 |
|---|---|---|---|
| `GD32_CANBUS_PA11_PA12` | PA11 | PA12 | 默认 |
| `GD32_CANBUS_PB8_PB9` | PB8 | PB9 | 部分重映射 |
| `GD32_CANBUS_PD0_PD1` | PD0 | PD1 | 完全重映射 |

通信接口在 Kconfig 中互斥，因此 PA11/PA12 CAN 不会和 USB 同时编入同一个固件。PB8/PB9 会和 `i2c0a` 冲突；选 CAN 后不要在 Klipper 配置中再次使用该 I²C 映射。

F009 示例配置使用 PB8/PB9：

`config/f009_gd32f303_can_pb8_pb9.config`

## 物理层要求

MCU 的 CAN_RX/CAN_TX 不能直接连接 CANH/CANL。板上至少需要：

- 3.3 V 逻辑兼容的 CAN 收发器，例如 TCAN332、SN65HVD230 一类器件；
- CANH/CANL 双绞线；
- 总线两端各120欧终端电阻；
- 需要热插拔或长线时增加 TVS、共模电感及合适的接地/隔离设计。

若采用5 V 收发器，必须确认 RXD 输出不会超过 GD32 引脚允许电压。

## 1 Mbps 位时序

F303 当前 APB1 为60 MHz。默认 `CONFIG_CANBUS_FREQUENCY=1000000` 时：

- BRP：4；
- 每位：15 TQ；
- BS1：12 TQ；
- BS2：2 TQ；
- SJW：2 TQ；
- 采样点：约86.7%；
- BTR：`0x011b0003`。

计算器只接受 APB1 时钟能够整除、且能找到10～18 TQ合法组合的速率；不支持的速率会在初始化阶段安全停机。

## 已实现能力

- 标准帧和扩展帧收发；
- RTR 标志处理；
- 三个发送邮箱；
- FIFO0接收；
- Klipper管理ID及节点ID过滤器；
- 自动 bus-off 恢复；
- 发送邮箱按提交顺序处理；
- warning、error-passive、bus-off 状态上报；
- ACK、位、格式、填充和 CRC 错误的软件计数；
- CAN初始化进入/退出超时保护；
- 使用 F303 96位 UID 生成 Klipper CAN UUID；
- 8 KiB Katapult Bootloader 布局，Klipper应用从 `0x08002000` 启动。
- Katapult应用镜像使用标准裸 BIN，不附加原厂Bootloader专用CRC16尾部。

## 构建

```powershell
cd "D:\Documents\ChatGPT\创想三维\mcu\Official_Klipper_GD32"
wsl.exe -d T113 -- bash -lc "cd '/mnt/d/Documents/ChatGPT/创想三维/mcu/Official_Klipper_GD32' && cp config/f009_gd32f303_can_pb8_pb9.config /tmp/f009-can.config && make KCONFIG_CONFIG=/tmp/f009-can.config OUT=build-gd32/development/can/ clean && make KCONFIG_CONFIG=/tmp/f009-can.config OUT=build-gd32/development/can/ olddefconfig && make KCONFIG_CONFIG=/tmp/f009-can.config OUT=build-gd32/development/can/ -j32"
```

正式一键入口不会发布 CAN 镜像。开发者应把
`config/f009_gd32f303_can_pb8_pb9.config` 复制为临时 `.config` 后，在独立输出目录
执行 Klipper 的 `olddefconfig` 和 `make`；不要改写三份正式配置。

2026-08-31 最近一次回归构建结果：

- text/data/bss：35320 / 52 / 1292 bytes；
- BIN长度：`0x8a2c`；
- 镜像格式：Katapult标准裸 BIN，无原厂CRC尾部。
- ELF入口及 `.text` 起始地址：`0x08002000`；
- SHA-256：`413e64ee929d55e829aec31eb5b584596bc59ddc7cffeb6fd6a76de51afd3b5c`。

Klipper 固件版本字符串包含构建时间，因此重新编译后 CRC16 和 SHA-256 会变化；应以当次构建脚本输出为准。代码不变时 text/data/bss 与 BIN长度更适合用于回归比较。

## 尚未完成的实机验证

1. 确认目标 F009 PCB 实际引出了哪组 CAN0 引脚；不能仅凭 MCU 数据手册判断接口连线。
2. 核对板上是否已有 CAN 收发器；RS485 收发器不能代替 CAN 收发器。
3. 首次在独立 GD32F303 开发板或备用板上测试，先不要连接加热和电机负载。
4. 使用 USB-CAN 适配器运行 `canbus_query.py`，确认能获得稳定 UUID。
5. 验证1 Mbps收发、节点分配、复位、断线重连和持续通信。
6. 测试无终端、单端终端、总线短路、ACK缺失和 bus-off 恢复。
7. 用示波器确认 CANH/CANL 差分幅度、采样点和边沿质量。
8. Katapult的GD32F303平台必须先完成并经过SWD恢复验证，不能把上游STM32F103构建直接刷入GD32F303。
