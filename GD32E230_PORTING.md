# GD32E230 平台移植记录

本分支为 Klipper 增加 GD32E230x6/x8（Cortex-M23）平台支持。实现范围限定在 MCU
通用能力，不绑定任何板卡或整机型号。

已接入的底层能力：

- 72 MHz 时钟、启动、复位和 NVIC；
- 自由运行定时器及比较中断；
- GPIO、外部中断和 ADC；
- USART0/USART1；
- 硬件 SPI、I2C 以及 Klipper 公共软件总线；
- 0、8、12、20、28、32 KiB 应用偏移。

E230 没有原生 USB 和 CAN，因此配置菜单只开放串口通信。示例
`config/gd32e230f8_serial.config` 使用 GD32E230x8、USART0 PA9/PA10、无
Bootloader 偏移。

平台定时器使用连续计数与比较事件模型，不依赖周期性自动重装载；Cortex-M23
启动代码会按实际中断优先级位数清理 NVIC 状态。
