# F009 最小 XYZ 运动配置

`printer.cfg` 启用主运动 MCU、XYZ电机、TMC UART、X/Y物理限位，以及
`/dev/ttyS4` 上的 `leveling_mcu` 和它唯一的一颗 LIS2DW加速度计。不启用加热、
挤出、风扇、PRTouch、热床控制或自动刷写。

配置同时提供 Fluidd/Moonraker 所需的 `virtual_sdcard`、`display_status`、
`pause_resume` 和 `respond`，G-code持久目录固定为
`/mnt/UDISK/printer_data/gcodes`。这些段只接通 Host/UI，不增加任何硬件动作。

## 安全边界

- X 限位 `PC7`、Y 限位 `!PB13` 及三轴 STEP/DIR/EN 来自 F009 原厂量产配置。
- Z轴实体驱动芯片是 MS35776N；原厂通过 PA4使用未修改的 Klipper
  `[tmc2208 stepper_z]` 兼容接口配置它。该段名称不代表芯片是 TMC2208，且不支持
  按 TMC2209 StallGuard方式进行无感归位。
- F009 没有已确认的独立 Z 机械限位，量产 Z 归位依赖 PRTouch V3。
- 最小配置里的主板 `PA15` 只是为实例化 `[stepper_z]` 保留的未使用占位脚，
  所有 Z 归位入口都已拦截。它不是 PRTouch 同步信号；原厂同步链是喷头板
  `nozzle_mcu:PA15` 输出到主板 `!PC7` 输入。
- 配置主动拒绝 `G28 Z` 和不带轴参数的 `G28`；不要绕过这道保护。

## 首次上机顺序

1. 架空运动部件或把喷头手动移到远离边界和热床的位置，保持急停/断电可用。
2. 启动后先执行 `STATUS`、`QUERY_ENDSTOPS`，再分别执行
   `DUMP_TMC STEPPER=stepper_x` 和 `DUMP_TMC STEPPER=stepper_z`；后者用于确认
   MS35776N兼容 UART是否应答，不以 TMC2208全部寄存器都存在为前提。
3. 手动按下 X/Y 限位并重复 `QUERY_ENDSTOPS`，确认状态只在对应开关变化。
4. 依次执行 `STEPPER_BUZZ STEPPER=stepper_x`、`stepper_y`、`stepper_z`，确认轴、方向和每毫米位移。
5. 只执行 `G28 X`，确认方向和停止；再单独执行 `G28 Y`。
6. 执行 `CHECK_LEVELING_ACCEL`，静止时应返回接近 1 g 的合加速度；轻触板卡时读数应变化。
7. X/Y 都正确后可执行 `HOME_XY`，再用不超过 10 mm 的低速 `G1` 验证平面运动。

Z 归位必须在 PRTouch V3 的 CS1237 数据、PA15/PC7 联锁极性和触发停止均通过
示波器与实机验证后，改用 `probe:z_virtual_endstop`，再删除当前归位锁。

## 离线验证

离线测试使用主板 `build-gd32/f009-main/klipper.dict` 和调平板
`build-gd32/e230-pa910/klipper.dict`。从原机 64 KiB GD32E230完整 Flash备份内嵌的
Klipper字典已直接恢复出 `RESERVE_PINS_serial=PA9,PA10`、`SERIAL_BAUD=230400`，因此
F009量产调平板固定使用 USART0 PA9/PA10；PA2/PA3只保留为平台开发变体。
