# CS1237 在官方 Klipper 中的实现与验证

## 当前状态

CS1237 已作为独立载荷传感器后端加入当前官方 Klipper，并可被以下两种配置使用：

- `[load_cell]`：连续读取、标定和力值监控。
- `[load_cell_probe]`：在 MCU 侧完成实时滤波与触发，作为 Z 探针使用。

它不是按 HX711 兼容器件处理。驱动会通过双向 `DRDY/DOUT` 写入 CS1237 的 Config 寄存器，设置 PGA 和采样率。

原厂 PRTouch V3 MCU 对象还包含一条独立的片上 ADC 路径：
`config_prtouch_pres` 的 `clk_pin` 与 `sdo_pin` 参数相同时调用
`gpio_adc_setup()`，采样时最多轮询 502 次 `gpio_adc_sample()`，然后调用
`gpio_adc_read()`。两参数不同时才建立 CLK/DOUT 并走 CS1237。该双分支已经由
原厂 `prtouch_v3.o` 的符号表和 Thumb 反汇编直接确认，并由
`scripts/audit_prtouch_v3_factory_object_T113.py` 自动门禁。

这条软件能力不等于 F009 量产热端板具备可用的模拟旁路。量产配置和当前实物证据
只确认 PB13/PB14 上的 CS1237 数字链路；片上 12 位 ADC 没有 CS1237 的低噪声 PGA，
不能直接读取毫伏级应变桥。公版树中的 `mcu_adc` 后端只作为自研板或已经具有
放大、偏置和限幅的模拟节点的次级/粗测方案，F009 默认配置仍使用 CS1237。

2026-09-03 已在 GD32F303CCT6 测试板完成 PA0 实测。DL16 的 PWM0 直接送入
PA0，D0 同步观察：公版 Klipper ADC 在 99% 高电平下连续 16 点均为 4095；
2 Hz/50% 方波下，PRTouch ADC 历史能在约 0 与 4095 之间切换。这证明 GD32
片上 ADC 和 PRTouch 的 ADC 分支电气上可用，但不改变上述模拟前端边界。

PRTouch V71 的 ADC 分支不自行建立定时器，`acq_tick` 是轮询中的到期门槛。
2026-09-05 复审进一步确认：原厂对象并没有 `DECL_TASK(prtouch_task)`，而是由
原厂 `sched.c` 在 idle 循环中直接连续调用 `prtouch_task()`。早期测试板固件额外
注册了 `DECL_TASK`，其约 100 ms 的空闲样本间隔不能代表原厂调度节奏。自研次级
方案仍应使用公版 `config_analog_in` 定时 ADC 或当前 `mcu_adc` 后端；PRTouch
分支只负责原厂 ABI 和 idle 轮询行为兼容。

完整接线、波形哈希和分级结论见
`../../artifacts/captures/dl16_pa0_pwm0_20260903/PA0_ADC_实测记录.md`。

## 支持的配置

```ini
[load_cell_probe]
sensor_type: cs1237
dout_pin: toolboard:PA1
sclk_pin: toolboard:PA2
sample_rate: 640
gain: 128
reference_output: True

counts_per_gram: 1000.0
reference_tare_counts: 0
trigger_force: 75
force_safety_limit: 2000
```

参数范围：

- `sample_rate`：10、40、640、1280 SPS，默认 640 SPS。
- `gain`：1、2、64、128，默认 128。
- `reference_output`：默认开启；只有外部基准电路明确要求关闭 REFOUT 时才设为 `False`。

上面的 PA1/PA2 只是工具板示例，必须换成实际原理图引脚。`DRDY/DOUT` 是双向线，不要在板上串接只允许单向传输的电平转换器。

## MCU 驱动行为

1. 停止采样时将 SCLK 保持为高，CS1237 进入低功耗状态。
2. 开始采样时拉低 SCLK 唤醒芯片。
3. 等待第一帧数据就绪，读出并丢弃上电配置产生的样本。
4. 按数据手册的 46 时钟序列写 Config 寄存器。
5. 下一帧检查 `update1` 配置更新标志、保留位和第 27 时钟强制高电平。
6. 后续每帧读取 24 位有符号 ADC 数据及 3 位状态，并批量上报主机。
7. MCU 侧直接把样本送入官方 `trigger_analog`，所以探针触发不依赖 Linux 调度延迟。

时钟高、低电平各至少保持 600 ns，满足数据手册给出的不小于 455 ns 要求。

## 错误检测

驱动会识别并触发自动重启：

- 串行帧失步；
- Config 写入未得到更新确认；
- MCU 未及时完成读取造成的采样溢出。

这些错误会出现在载荷传感器状态中的 `errors` 和 `overflows`。

## 已完成的软件验证

- F009 GD32F303 USART1、USB CDC、CAN 和两种 E230 UART 配置均编译及链接通过；当前发布矩阵使用 8 KiB Katapult 布局。
- Python 模块语法检查通过。
- `git diff --check` 未发现补丁空白错误。
- ATmega2560 交叉编译通过，证明驱动未依赖 GD32 私有 GPIO 实现。
- 官方 Klipper `load_cell` 集成回归用例通过；测试中的
  `[load_cell_probe]` 后端已改为 CS1237，覆盖探针对象创建、MCU 命令字典和运动调用路径。
- GD32F303CCT6 的 PA0 已完成数字高低电平、标准 ADC 满量程连续采样及
  PRTouch ADC 方波跟随实测；标准 ADC 连续 16 点为 4095，动态样本覆盖约 0/4095。

构建结果：

| 通信方式 | 布局 | BIN SHA-256 |
|---|---|---|
| F009 USART1 | Katapult 8 KiB | `101a71d0e8c6b1dda8d51bb2b394ad10809132ffd5274865700a11dcb566d160` |
| GD32 USB CDC | Katapult 8 KiB | `b569a4ab08818771d19945441a850a0558ec070aab7febfa9a2f693841beac62` |

这些是普通 Katapult 应用 BIN，不使用原厂 Bootloader 的 CRC16 尾部格式。

## 核心板实测顺序

### 第一步：只测数字通信

先不连接机械负载，使用 3.3 V 逻辑，并确认模块的数字高电平不会超过 MCU 容限。

逻辑分析仪至少观察：

- SCLK 空闲高、开始采样后变低；
- DRDY/DOUT 周期与所选 SPS 基本一致；
- 单次普通读取为 27 个 SCLK；
- 启动配置交易总计 46 个 SCLK；
- 每个 SCLK 高、低电平不短于约 600 ns；
- 配置后的下一帧，第 25/26/27 位应为 `1/0/1`。

### 第二步：验证数据质量

依次执行空载、手压、固定砝码测试：

1. 检查正负方向和饱和范围。
2. 在 640 SPS 下记录静态噪声、零点漂移和工频干扰。
3. 标定 `counts_per_gram` 与 `reference_tare_counts`。
4. 再评估 1280 SPS；如果工具板 MCU 还承担 WS2812、I²C 或通信任务，应重点检查溢出计数。

### 第三步：验证探针安全

先把 `trigger_force` 设低、Z 速度设慢，并准备实体急停。确认 MCU 触发能停止运动后，再逐步提高速度。不要在首次测试时安装装满材料的注射器。

## 三个机械事件的后续实现

标准 `[load_cell_probe]` 只定义一次探针运动中的一个停止触发。点胶机构需要识别：

1. 上压片刚接触活塞；
2. 注射器活塞到底；
3. 喷头接触工件。

CS1237 能提供实现这些判断所需的连续力值，但三者不能仅靠固定力度可靠区分。后续主机状态机应同时使用：

- 当前运动轴与方向；
- 当前流程阶段；
- 力值相对基线的变化；
- 力值斜率和持续时间；
- Z/E（推杆轴）位置窗口；
- 软限位和独立最大力保护。

建议保留 `[load_cell_probe]` 专门负责喷头触底；压片接触和活塞到底由独立的点胶执行器模块判断，避免复用探针状态机造成动作语义混乱。
