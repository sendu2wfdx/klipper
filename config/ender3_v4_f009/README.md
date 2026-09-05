# Ender-3 V4 F009 公版 Klipper 配置

本目录是 F009 机型的自主可控配置工作区。配置只使用公版 Klipper
接口以及本仓库内采用 GPL 协议实现的 GD32、CS1237 等功能，不加载
创想三维的 Python 私有模块或预编译动态库。

## 当前完成范围

- 已完成映射：三路 MCU 通讯、XYZ/E 运动、TMC UART、热端及热床加热、
  热敏电阻、断料开关、模型风扇、热端风扇、两路风扇转速反馈，以及
  喷头板和热床板上的两颗 LIS2DW 加速度计；T113 Linux-process MCU 与
  主板 BL24C16F 断电续打 EEPROM 的硬件访问和原厂命令接口也已接入。
- 原厂 `[io_remap]` 的 PB0→PA15 映射已按真实宿主 MCU 还原到喷头板；公版
  MCU 协议保持新 ABI，主机兼容层接受原厂配置项和 `SET_IOREMAP S=`，并在
  X 归零运动前后自动启停。`filterNum=1` 仍保持原厂两次连续采样后翻转，
  `periodTicks=0` 仍对应原厂 10 μs 默认周期。
- F009 `[nozzle_clear]` 已迁移到公版 `load_cell_probe`：保留原厂清理区坐标、
  随机偏移、3 次预压、8 轮粗擦、两段精擦和 170→158→130 ℃ 温度阶段；原厂
  对 `prtouch_v3.pres.tri_hold` 的直接修改改为每次 `PROBE` 传入等效的
  `TRIGGER_FORCE=112.5`。探测后的公版拟合回撤会根据 `last_z_result` 换算成相对
  接触面的真实间隙，不会把回撤位置误设为 Z=0。当前 CS1237 尚未标定，模块默认关闭；原厂 `FORECEZ`
  强制移动和耐久循环命令保持安全锁定。
- 已通过编译：GD32F303RCT6 主板（软件目标 `gd32f303xc`）、GD32F303CB 喷头板，以及热床板
  GD32E230F8 的两种候选串口映射。
- 已由热端板实物丝印确认压力 ADC 为 CS1237，不再按 HX711 或兼容芯片
  处理；PB13/PB14 的公版 CS1237 后端保持为 F009 当前唯一有板级证据的采样实现。
  原厂 MCU 对象中 `clk_pin==sdo_pin` 的片上 ADC 次级路径也已恢复并做成自动
  反汇编门禁；公版另有可选 `mcu_adc` 后端，但在找到已放大的模拟节点前不写入
  F009 量产配置，也不能把应变桥直接接到 MCU ADC。
- 尚不能安全生产使用：PRTouch V3 的 PB13/PB14 已映射到 CS1237，
  公版采样链路也已接通；原厂 ABI 存档层已恢复压力、STEP、APAX 和
  PA15/PC7 同步脚行为，增量压缩格式和 MCU 控制流也已按原厂对象复核；仍需
  原板波形确认的是同步线传播延迟、边沿质量、CS1237 电气时序和机械触发效果。
  `sensors.cfg` 中的载荷传感器
  标定数值只是占位值，不能用于实际触发。
- 已从原机 GD32E230完整 Flash备份的 Klipper内嵌字典确认：量产调平板串口为
  USART0 PA9/PA10、230400 baud；PA2/PA3只保留作平台开发变体。
- 断电续打第一阶段已接入：按原厂 8 字节格式记录文件位置与挤出机基准、每槽
  255 次写入后轮转，保留检查/取消续打的原厂 Web API，并在完成或取消时清理。
  恢复规划器现能从 EEPROM 精确字节边界重建 XYZE 模态、温度、风扇、网床、
  M204、速度/流量和压力提前量，并执行抬 Z、XY 归零、预热、回位、挤出坐标及
  下一层恢复速度的完整顺序；状态文件还记录文件大小与 SHA-256，防止同路径文件
  被替换。源代码路径已经闭合，但 `automatic_restore` 与 Z 策略默认双重关闭，
  只有明确选择 `assume_unchanged` 才允许执行。实体机尚未证明断电后 Z 绝对不滑移，
  因此当前仍不能宣称整机断电续打可用。

在 PRTouch 和温控验证完成之前，禁止执行 `G28 Z`、`PROBE`、
`BED_MESH_CALIBRATE`，也不要进行无人值守的加热测试。
当前配置已通过 `[homing_override]` 主动拒绝所有包含 Z 的归零请求；完成
实机标定后必须由开发者明确删除这道保护，不能通过普通打印宏绕过。

## 固件布局

- 主板使用公版 Katapult：`f009_gd32f303_serial.config`，
  Klipper 应用入口为 `0x08002000`。
- 主板暂时保留原厂 Bootloader：
  `f009_gd32f303_serial_factory12k.config`，应用入口为 `0x08003000`。
- 喷头板使用公版 Katapult：`f009_toolhead_gd32f303cb_katapult8k.config`。
- 喷头板保留原厂 12 KiB Bootloader：`f009_toolhead_gd32f303cb_factory12k.config`，应用入口 `0x08003000`。
- 量产调平板使用公版 Katapult：`gd32e230f8_serial_pa9_pa10.config`，应用入口 `0x08002000`。
- 量产调平板保留原厂 12 KiB Bootloader：`gd32e230f8_serial_pa9_pa10_factory12k.config`，应用入口 `0x08003000`。
- `gd32e230f8_serial_pa2_pa3.config` 仅作为 GD32E230平台开发/改板变体保留。

## 当前验证结果

- 六个常用板级固件目标均可使用固定 GCC 9 工具链完整编译（主板、喷头、
  热床两种串口候选、F303CC 串口测试和 USB CDC 测试）。
- 本目录配置已使用三份板级 MCU 字典和一份 T113 Linux-process MCU 字典
  通过 Klippy 完整启动解析测试；8 KiB 与原厂 12 KiB 两套布局均覆盖。
- PRTouch V3 离线算法测试和协议 ABI 源码测试各 4 项均通过；喷头固件
  字典已包含全部压力、STEP、APAX 命令及 `resault_*` 返回消息。
- 该测试证明配置、引脚名称和 MCU 命令可以装载，不代表尚未验证的
  电气极性、压力阈值或热控参数已经具备上机安全性。
- 公版主机源码测试当前为 `128 passed, 1 skipped`；其中 19 项覆盖断电续打与
  virtual-SD 生命周期，
  6 项覆盖擦嘴禁用门、等效触发力、回撤坐标修正、完整动作合同、危险命令锁和异常收尾。
  新增 4 项覆盖原厂 PRTouch 对象的状态块、片上 ADC 导入、502 次轮询常量和
  CS1237/ADC 双分支反汇编证据。
