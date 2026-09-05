# PRTouch V2 MCU 官方源码移植与 CCT6 实测

记录日期：2026-09-03

## 结论

创想三维已经公开了 PRTouch V2 的 MCU 端实现。本仓库没有根据接口重新猜写，而是
从官方仓库固定提交
`e09f36e6ada60e5467b0bef731a96263b5d8095b` 中取出 `src/prtouch_v2.c`，仍以官方原名
`src/prtouch_v2.c` 保存。只归一化换行后的 SHA-256 为
`04e2fa4a50f80461bd97cf802893295a4ef0862b126e40e6c9e349769e3570d4`，当前文件与官方
源码在换行归一化后逐字节一致。

这份代码同时包含外置压力 ADC 的时钟/数据采样和 MCU 片上 ADC 分支。配置命令中的
`use_adc=1` 会让 `add_pres_prtouch` 把通道引脚交给 Klipper 公共 ADC 后端，所以 PA0
可以作为 CS1237 不可用时的次级方案；其量程是 MCU 的 12 位 ADC，而不是 CS1237 的
24 位差分转换结果，精度、噪声、输入前端和标定不能等同。

## 与原厂一致的空闲调度

官方 `prtouch_v2.c` 自身没有 `DECL_TASK(prtouch_task)`。这并非代码遗漏：创想三维的
私有 `sched.c` 在空闲循环中直接调用 `prtouch_task()`。若只把官方文件放入公版
Klipper，配置命令仍会出现在字典里，但后台采样任务不会执行，链接器还可能把任务
主体回收。

为保持官方文件可逐字审计，本仓库不修改 `prtouch_v2.c`，只在公版 `sched.c` 的空闲
循环恢复同一处 `prtouch_task()` 直接调用。此前试做的 `DECL_TASK` 桥已删除，因为它会
改变原厂只在 idle 阶段轮询的时序，并可能与空闲直调形成双重调度。V2 与现有 V3
兼容层在 Kconfig 中仍互斥，避免两个协议实现同时占用同名 MCU 命令。

2026-09-05 以固定版本字符串重新构建后的 CCT6 USB 双缓冲目标为：

```text
配置：config/gd32f303cc_test_usb_pa11_pa12_noboot_dbuf_prtouch_v2_adc.config
目标：test-cc-usb-dbuf-prtouch-v2-adc
text/data/bss：49118 / 88 / 4188
BIN 长度：49516 字节
原厂封装 CRC16：0x8424
BIN SHA-256：64f276fffbd4ae67db207d77974bf5793af564100266f2b0120ef3da366671c1
```

链接映射包含 `prtouch_task()`，而对象中不存在
`_DECL_CALLLIST ctr_run_taskfuncs prtouch_task`，证明当前只有原厂式 idle 调度入口。

## 无运动片上 ADC 探针

新增 `scripts/gd32_prtouch_v2_adc_probe_windows.py`。探针只配置 `step_cnt=0`，不会发送
`start_step_prtouch`，因而不会产生步进脉冲。它覆盖：

- PA0 的 `use_adc=1` 固定采样及 12 位范围检查；
- 采样时间戳严格递增；
- `deal_avgs_prtouch` 基线计算；
- 压力触发后的 16 个两点分包及通道掩码；
- `manual_get_pres` 历史值读取和显式停止。

## DL16 与 PA0 接线证据

实物接线为 DL16 `PWM0 -> D0 -> PA0`，并已共地。2026-09-03 使用 DL16 有限缓冲捕获
20 ms、10 MHz、200000 点，PWM0 请求 100 kHz/37%，D0 观测结果为：

| 项目 | 结果 |
|---|---:|
| 频率 | 100000 Hz |
| 高电平占比 | 37.00% |
| 上升/下降沿 | 2000 / 2000 |
| 最短脉宽 | 3.7 us |
| 毛刺候选 | 0 |

原始 CSV：
`../../artifacts/captures/dl16_pa0_pwm0_20260903/pwm0-d0-100khz-37pct.csv`，SHA-256
`09659119c5a24c19f989378f1105d8e2d3a01bdbce82bf8caef1efdaec49cd1d`。分析报告为同目录
`pwm0-d0-100khz-37pct-report.md`，SHA-256
`0a66655cfe808c02b3ca2774f0f48e6a9bdf7fb698d5c955942f252647d1f09b`。

这证明逻辑分析仪、PWM0、D0 以及到 PA0 的物理链路正常；它还没有证明 MCU 已把 PA0
采成正确数值。最后一步仍需把当前 idle 调度镜像完整刷入，再让上述无运动探针与
DL16 波形同时运行。

重新插接 DL16 后又做了一次独立复测。接线仍为 `PWM0 -> D0 -> PA0`、共地，PA0 与
D0 之间没有串联电阻；PWM0 请求 10 kHz/30%，D0 以 10 MHz 连续采集 20 ms。实测为
10.000 kHz、30.00%，上升/下降沿各 200 个，最短脉宽 30 us，毛刺候选为 0。原始
CSV 为
`../../artifacts/captures/dl16_pa0_pwm0_20260903/pwm0-d0-10khz-30pct.csv`，SHA-256
`4e14b76b41551c6ff4c40b12112e1715aed6447a84c8c439d054fc3b66a00a0d`；分析报告为
同目录 `pwm0-d0-10khz-30pct-report.md`，SHA-256
`23aad2adf08a34d2daae1d6f26ab5d0922150e7dc52dbdfa445477c13f554ba2`。这次复测排除了
DL16 重连后设备、输出或输入通道失效，但结论边界不变：PA0 的 MCU ADC 数值仍需在
桥接版固件成功刷入并恢复 USB 通讯后验证。

## 当前验证边界

当前已经证明源码同源、CCT6/E230 可构建、两套 V1/V2 主机包装器均完整引用 MCU 的
12 条命令和 9 条响应，且没有重复任务注册。尚未证明的是四颗 HX711/兼容 ADC 的 K1
调平板实物行为；现有测试硬件只有 GD32F303CCT6，因此不能把 E230 构建通过等同于
K1 调平板实测通过。

## V57 纯源码发布树归档

同一份官方 MCU 文件和原厂式 idle 调度已同步进入 `../Klipper57_Source_Rebuild/src/`，
不再只存在于公版 GD32 开发分支。V57 树新增固定提交来源审计、V2/V3 互斥构建项、独立
T113 构建脚本和消息字典门禁。归档目标采用 GD32F303CCT6、无 Bootloader、USART0
PA9/PA10，仅用于源码可构建性和 ABI 证明，不代表 F009 生产板接线。

2026-09-05 的 CCT6 归档构建为 `text/data/bss=30864/88/3264`，BIN 长度 30952，
CRC16 `0x1E65`，SHA-256
`4635beba31283a80fb60db05f481a0ae7300055bd7fde1d9584f4166cd71328a`；消息字典为
12/12 条命令、9/9 条响应。

## K1 四通道 E230 目标

已按 K1 官方 `bed0_110_G21` 配置增加 GD32E230x8、72 MHz、12 KiB Bootloader 偏移、
USART0 PA9/PA10 230400 的构建目标。固定版本字符串下：

| 源码树 | text/data/bss | BIN 长度 | SHA-256 |
|---|---:|---:|---|
| V57 源码替换树 | 41796 / 100 / 3264 | 41896 | `791f38ebd5ae144cd7bb7176554586de3256d8877a6a8b949639326de6347d9f` |
| 公版 GD32 树 | 30430 / 100 / 2600 | 30880 | `5603cb9c752ef3c390f0394ba32289706e130fe901a607c87d5c28e2259443d8` |

两者均通过 V1、V2 包装器的 12/12 命令和 9/9 响应合同检查。V1 与 V2 使用同一份
MCU 协议实现；V1 与 V2 的差异位于各自主机端算法选择。

上述来源审计和实际 MCU 构建已经纳入 r16 每次发布流程。r16 归档长度 55845273，
SHA-256 为
`44e994e8f5833ed95f873ca5c542662c04039f5003a7bc0b6aad676082f96af7`。其 provenance
包含官方 V2 MCU 文件、专用 defconfig 和构建/字典检查脚本；payload 仍只
保留运行所需、由公开源码本轮重建的 ARM `c_helper.so`，不携带 MCU BIN。
