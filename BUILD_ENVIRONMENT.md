# GD32 Klipper 固定编译环境

## 规范编译环境

- WSL 发行版：`T113`
- `arm-none-eabi-gcc`：9.2.1（Ubuntu `15:9-2019-q4-0ubuntu1`，与历史成功产物一致）
- GNU Make：4.2.1
- Python：仅用于 Klipper 构建命令生成，不影响 MCU ABI
- Windows 工作区：`D:\Documents\ChatGPT\创想三维\mcu\Official_Klipper_GD32`
- WSL 路径：`/mnt/d/Documents/ChatGPT/创想三维/mcu/Official_Klipper_GD32`

GD32 MCU 固件使用 `T113` WSL 内的原生 GCC 9；全志 Linux 用户态继续使用 SDK 自带的 `arm-openwrt-linux-gnueabi-gcc`，两者用途不同。不得把 Windows 版 ARM GCC 和 WSL 的 `make` 混合使用，也不使用 `Klipper` WSL 中会改变链接结果的 GCC 13。

## Windows/WSL 共享工作区换行约定

此仓库同时由 Windows 和 `T113` WSL 访问，克隆后应在仓库内设置：

```powershell
git config --local core.autocrlf false
```

仓库保留公版 Klipper 及其第三方芯片库原有的换行形式，不执行全仓库
`git add --renormalize .`，也不提交覆盖全仓库的强制 LF 属性。这样既避免 Windows
与 WSL 之间出现整库伪差异，也不会为后续同步公版 Klipper 制造数十万行无功能
变化的冲突。新增及修改的自有 C、Python、Shell 和配置文件统一使用 LF。

## Windows 一键编译

```powershell
cd "D:\Documents\ChatGPT\创想三维\mcu\Official_Klipper_GD32"
.\build-gd32.ps1
```

该命令会以 32 线程干净编译六个默认目标：F009 主板、F009 工具头、E230
PA2/PA3、E230 PA9/PA10，以及 GD32F303CCT6 测试板的串口和 USB 版本。前四个
量产板目标使用 8 KiB Katapult 布局，应用入口 `0x08002000`；两份 CCT6 测试固件
为无 Bootloader 布局。产物统一保存在 `build-gd32/`；该目录是可再生成的编译
缓存，发布用 BIN 另存于 MCU 总目录的 `../builds/`。F009 串口、USB、CAN 和原厂 12 KiB
布局仍可作为显式目标单独构建，但不在默认矩阵中。量产调平板保留原厂
12 KiB Bootloader 的精确布局可用 `e230-pa910-factory` 显式构建。
喷头板保留原厂 12 KiB Bootloader 的精确布局可用
`f009-toolhead-factory` 显式构建。

若仍使用原厂 F009 12 KiB Bootloader，应改用单独的 `f009_gd32f303_serial_factory12k.config`，应用入口为 `0x08003000`，不能使用默认 Katapult 配置。

若调平板保留原厂 12 KiB Bootloader，应使用
`gd32e230f8_serial_pa9_pa10_factory12k.config`（一键目标
`e230-pa910-factory`），其应用入口同样是 `0x08003000`。量产板串口固定为
USART0 PA9/PA10；不得把此镜像与 8 KiB Katapult 布局互换。

若带 CS1237 的喷头板保留原厂 12 KiB Bootloader，应使用
`f009_toolhead_gd32f303cb_factory12k.config`（一键目标
`f009-toolhead-factory`），其应用入口为 `0x08003000`；公版 Katapult 版本仍用
`f009_toolhead_gd32f303cb_katapult8k.config` 和 `0x08002000`。

## WSL 不可用时的 Windows 备用构建

当 WSL 后端无法创建实例时，可以用下面的独立入口继续做源码编译、
链接、对象布局和可重复性验证：

```powershell
cd "D:\Documents\ChatGPT\创想三维\mcu\Official_Klipper_GD32"
.\build-gd32-windows.ps1 f009-toolhead
```

需要从两个全新输出目录验证可重复构建时，可临时指定仓库内目录名：

```powershell
$env:GD32_BUILD_ROOT = 'build-gd32-repro-a'
.\build-gd32-windows.ps1 f009-toolhead
```

脚本只接受简单目录名，拒绝绝对路径和 `..`，避免清理构建目录时越出仓库。

备用链固定使用 GNU Arm Embedded 10.3.1、GNU Make 3.81、Git Bash 和
`-flto=1`。产物单独放在 `build-gd32-windows/`，不覆盖 WSL/GCC 9 的
`build-gd32/`。`-flto=1` 是因为 Windows 版 GCC 10 的 `-flto=auto` 会通过
jobserver 重启 `make -j`，在该组合下传入空并行数而链接失败。这是构建工具差异，
不是 MCU 功能差异。

下面是加入固定原厂元数据区之前的历史备用链基线，仅用于追踪旧实验产物，不能
作为当前发布哈希：

```text
text/data/bss: 50424/52/9060
SHA-256: 19550aab83752bda347c05dcf2d3bb0fdc7221d7f119c990943ffb15a5dccc82
```

不同 GCC/LTO 组合的体积和二进制不应互相比较；这一哈希只用于标识当前
Windows 备用链的确定性输出。

## T113/GCC 9 当前可重复构建基线

在两个独立输出目录中固定
`KLIPPER_BUILD_VERSION=gd32-source-rebuild` 后，F009 工具头当前得到：

```text
text/data/bss: 41418/52/9068
SHA-256: 9b5aafb877497ff3761c21d9ee32c43d5970c0e62687f6a7e75fe1cc502f63c3
```

该 2026-09-05 基线包含 PRTouch 下降沿历史保护修正和原厂 CS1237 位操作内联，
构建长度为 41776 字节。

GCC 9 的中间对象使用 LTO，普通 `nm` 可能通过错误的宿主插件读取它。构建门因此
对最终 `klipper.elf` 核对 PRTouch 三个持久状态块和四个 160 字节查询工作区；
函数精确尺寸仍由 Windows/GCC 10 的非最终 LTO 对象门禁负责。两条门分别约束
内存 ABI 和关键控制流，不把不同工具链的链接期内联差异混为一谈。

同一固定版本串下，默认六目标矩阵也已完整通过：

| 目标 | text/data/bss | SHA-256 |
|---|---:|---|
| `f009-main` | `34198/52/1048` | `396464ca31599186e16904ad1c61e911dd55d7c3bad679075be837ada6fbee4e` |
| `f009-toolhead` | `41418/52/9068` | `9b5aafb877497ff3761c21d9ee32c43d5970c0e62687f6a7e75fe1cc502f63c3` |
| `e230-pa23` | `39286/64/1052` | `420ae717637b6fb8497c918ecfc156fc86451dfe05a9f90f260899fec7f3c0b8` |
| `e230-pa910` | `39286/64/1052` | `9b9143e776a8b4a754980c1348967081d3b714dd791c85ef19a1fbb937e3c1f3` |
| `test-cc-serial` | `40606/52/9068` | `c9475abc04c46e61051c20ce0c25759ad01b2279539c6743df2039acc4f02568` |
| `test-cc-usb` | `44434/52/10580` | `9cb356b24d80032ff7670bcd0d94cf2390451bd2e9f3a8c9b65741872388a616` |

显式目标 `e230-pa910-factory` 也已在相同 T113/GCC 9 环境中通过：
`text/data/bss=39286/64/1052`，版本 `bed0_017_000`，长度
`39700 (0x9B14)`，CRC16 `0xB9AD`，SHA-256
`65d8d4b76c9af189ccef4cabde9f226965c978a093ad1b99106477ae3b717591`。

显式目标 `f009-toolhead-factory` 同样通过完整编译、PRTouch 对象布局门和原厂
封装校验：`text/data/bss=41426/52/9068`，入口 `0x08003000`，元数据段
`0x08003200`，版本 `noz0_019_000`，长度 `41784 (0xA338)`，CRC16
`0x6B72`，SHA-256
`35599ceafb1fa67572c502313044f2a9cf267398ce5ab9911c928c090c0a1f94`。

`test-cc-usb` 当前同时编入 USB CDC 和独立 USART0 原厂 F7 从机兼容层，所以体积
大于早期只验证 USB 的基线。2026-09-03 在两个全新输出目录构建，均得到长度
`44796 (0xAEFC)`、CRC16 `0x0A63` 和上表相同 SHA-256；实际刷入
GD32F303CCT6 后，COM11 的 F7 探测与 COM12 的三轮 Klipper identify 均通过。

原厂 Bootloader 使用应用相对偏移 `0x200..0x211` 保存 12 字节应用版本、CRC16
和镜像长度。旧版 `host_crc16` 曾在链接完成后直接改写 `0x20C..0x211`，而通用
链接脚本可能把普通代码放到这里；一次裁剪诊断命令的构建恰好覆盖了
`USART0_IRQHandler`，造成“USB 正常、F7 串口完全失效”。现已用独立
`.gd32_app_metadata` 段固定占用这 18 字节，并在链接时断言向量表不越界、元数据
尺寸必须为 `0x12`。CRC 工具也改为先清零校验字段、检查最小长度和所有读写错误。
主板、喷头板、热床板分别写入还原出的 `mcu0_022_000`、`noz0_019_000`、
`bed0_017_000`；非 Katapult 构建会自动执行原厂格式验证。

F009 默认主板目标按 V57 原厂 G31 BIN 的内嵌字典使用 `gd32f303xc`，而不是早期
借用的 F018/xE 配置。纠正后的原厂 12 KiB 目标同样为
`text/data/bss=34198/52/1048`，版本 `mcu0_022_000`、长度 `34556 (0x86FC)`、
CRC16 `0x447F`、SHA-256
`993a4216ae37144e689ef226ef42f6940057a4680bd0d3607f68f002667aeae4`。
F009 主板实物丝印已由用户再次确认为 GD32F303RCT6：`R` 是 64 引脚封装，`C` 是
256 KiB 容量等级；这与原厂 BIN 的 `gd32f303xc` 字典一致。早期原厂配置注释
写成 RET6 属于料号误记，不再把 xE/512 KiB 配置作为 F009 默认目标。

F009 配置还需要 T113 Linux-process MCU 驱动板载 BL24C16F。可在 `T113` 中运行
`scripts/build-gd32-matrix.sh linuxprocess` 构建；该目标先把最小配置复制到输出
目录再执行 `olddefconfig`，不会改写仓库中的 `test/configs/linuxprocess.config`。
生成的 `build-gd32/linuxprocess/klipper.dict` 已用于 8 KiB 与 12 KiB 整机配置
启动测试。

量产/测试配置默认使用单缓冲 USB IN。双缓冲实现仍保留在
`CONFIG_GD32_USB_DOUBLE_BUFFER_TX` 下作为实验选项，但实测在长 identify 后继续
查询时可能复位或停止响应，因此不能作为替换固件默认值。这个结论不是“GD32
不支持双缓冲”，而是当前经典 PMA USBD 后端的双缓冲切换/完成中断时序还没有
达到可交付稳定度。
