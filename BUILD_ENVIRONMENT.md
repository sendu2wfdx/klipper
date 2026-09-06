# GD32 构建说明

本分支以公版 Klipper 为基础，只增加通用 GD32 MCU 平台支持，不包含具体打印机、
探针、总线协议或厂商业务功能。

需要 GNU Make、Python 3、`arm-none-eabi-gcc`、binutils 和 newlib。运行：

```sh
sh scripts/build-gd32-platform.sh
```

默认验证以下目标：

| 目标 | 芯片/接口 | 应用入口 |
|---|---|---|
| `f303-serial` | GD32F303xC，USART0 PA9/PA10 | `0x08000000` |
| `f303-usb` | GD32F303xC，USB PA11/PA12 | `0x08000000` |
| `f303-can` | GD32F303xC，CAN0 PB8/PB9 | `0x08000000` |
| `f303-katapult` | GD32F303xC，USART0 PA9/PA10 | `0x08002000` |
| `e230-serial` | GD32E230x8，USART0 PA9/PA10 | `0x08000000` |

可将目标名作为参数只构建其中一项，例如：

```sh
sh scripts/build-gd32-platform.sh f303-usb
```

产物默认位于 `build-gd32/<目标>/`。脚本只复制示例配置到构建目录，
不会改写仓库中的配置文件。
