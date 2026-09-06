# CS1237 with upstream Klipper probing

The `gd32-port` branch includes a CS1237 sensor backend and
MCU-local GPIO forwarding. It does not contain Creality PRTouch, its private
protocol, probing algorithm, PA15 pressure handshake, or bed-mesh traversal.

The pressure path is:

`CS1237 -> sensor_bulk / trigger_analog -> load_cell_probe -> bed_mesh`

The only changes to upstream `load_cell.py` and `load_cell_probe.py` register
the new sensor type. `bed_mesh.py`, `probe.py`, `homing.py`, `trigger_analog`
and their algorithms remain the platform baseline versions. MCU-side analog
triggering and standard multi-MCU synchronization stop probing moves.

## Sensor configuration

Both `[load_cell name]` and `[load_cell_probe]` accept `sensor_type: cs1237`.
Supported `sample_rate` values are 10, 40, 640 (default), and 1280 SPS.
Supported `gain` values are 1, 2, 64, and 128 (default). `reference_output`
defaults to True. CLK and bidirectional DOUT must be on the same MCU.

For F009, set these sensor options inside the upstream probe section:

```ini
[load_cell_probe]
sensor_type: cs1237
sclk_pin: nozzle_mcu:PB13
dout_pin: nozzle_mcu:PB14
sample_rate: 640
gain: 128
reference_output: True
# Supply measured counts_per_gram, reference_tare_counts and z_offset.
# Set trigger_force and force_safety_limit for the calibrated mechanism.
```

This is a configuration fragment, not a calibrated ready-to-print profile.
Use the standard load-cell calibration, `probe:z_virtual_endstop`, and
`[bed_mesh]` configuration. `BED_MESH_CALIBRATE` uses the upstream mesh flow.
Do not assign PB13/PB14 to another sensor object at the same time. Software
tests do not establish physical calibration or contact stopping accuracy.

## GPIO forwarding

`gpio_forward` is a downstream extension. The MCU samples the input and drives
the output without waiting for the host. Input inversion is independent of
pull-up/pull-down bias; output inversion controls physical output polarity.

```ini
[gpio_forward x_endstop]
input_pin: ^!nozzle_mcu:PB0
output_pin: !nozzle_mcu:PA15
enable: False
home_x: True
filter_count: 2
period: 0.000010
```

This preserves the F009 X switch route `PB0 -> PA15 -> mainboard PC7`:
two consecutive low input samples drive PA15 low, one high sample releases
it high, and disabling forwarding holds it high. PA15 is only for X here;
it has no role in pressure triggering or Z probing.

Both pins must belong to the same MCU and must not be assigned elsewhere.
`filter_count` is 1..255 (default 1). `period` defaults to 50us.
The output becomes inactive on the first inactive sample; this is an
asymmetric activation filter, not a delay applied to both edges.
Disabled/shutdown output is logically inactive (physical high with `!`).
`enable` defaults to True. `home_x` defaults to False; when enabled it
temporarily enables forwarding for X homing and restores the previous state
on completion or G-code error. Use `enable: False` for homing-only forwarding.

Runtime control: `SET_GPIO_FORWARD NAME=x_endstop ENABLE=0|1`.

The old `[io_remap]` / `SET_IOREMAP S=0|1` configuration syntax is supported
by an adapter for this GPIO function only. It does not load PRTouch or make
this firmware binary-compatible with Creality firmware. Do not configure
both interfaces for the same pair of pins.

## Firmware boundary

This is standard Klipper firmware with GD32 platform support. The original
Creality application image packaging and bootloader update protocol are not
included. Building successfully does not make a plain application binary
suitable for the factory update tool; deployment is a separate task.

## Software validation

2026-09-06: 16 host tests passed, covering CS1237 sample/error reporting,
analog trigger attachment, GPIO polarity, X homing restoration and legacy
GPIO configuration compatibility. GD32F303xB and ATmega2560 compiled and
linked. The GD32 dictionary includes CS1237/analog-trigger/forwarding commands
and no PRTouch commands. Two Klippy integration tests passed, including
`G28`, `PROBE`, `BED_MESH_CALIBRATE` and GPIO runtime control. These tests
use simulated MCU output; physical sensor timing, calibration and probing
remain unverified on this branch. No printer was flashed or moved.
