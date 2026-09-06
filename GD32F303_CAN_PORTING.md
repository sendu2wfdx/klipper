# GD32F303 CAN 支持

GD32F303 平台提供 CAN0 底层驱动，并复用 Klipper 公共 `canserial`、过滤器和
节点 ID 机制。可选择三组引脚：

- PA11/PA12；
- PB8/PB9 重映射；
- PD0/PD1 重映射。

示例 `config/gd32f303cc_can_pb8_pb9.config` 使用 GD32F303xC、PB8/PB9 和
1 Mbit/s。编译命令：

```sh
sh scripts/build-gd32-platform.sh f303-can
```

MCU 引脚是逻辑接口，实际硬件仍需 CAN 收发器、正确终端电阻和匹配的总线速率。
该驱动不实现任何上层私有协议，也不把 RS485 当作 CAN 使用。
