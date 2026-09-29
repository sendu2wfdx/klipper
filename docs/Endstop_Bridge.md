# Endstop Bridge

Cross-MCU homing may place trigger detection and motor control on different
MCUs. Relaying the stop notification over the communication link involves
host coordination. Resource-constrained or heavily loaded hosts may encounter
excessive latency or communication timeouts on this path.

Endstop Bridge provides an optional GPIO trigger path directly between the
MCUs. Its goal is to reduce trigger-to-stop latency and improve homing and
probing reliability on resource-constrained hosts by removing the host relay
from the critical stop path. These benefits still require hardware measurement.

Multiple trigger sources can time-share the same signal wire. Digital axis
endstops and native load-cell probe triggers use the connection during their
respective operations, reducing wiring and GPIO requirements. This is source
selection, not simultaneous transmission of independent signals.

Similar hardware paths exist in Creality printers: K1, K1 Max, K1C and K1 SE
use GPIO signaling for PRTouch pressure triggers, as does CR-10 SE. Creality
Hi uses `io_remap` for a digital endstop. These are precedents for hardware
trigger transport, not claims that all these models support shared XYZ inputs.
See the [K1 firmware](https://github.com/CrealityOfficial/K1_Series_Klipper/blob/main/src/prtouch_v2.c),
[CR-10 SE configuration](https://github.com/CrealityOfficial/CR-10SE_Klipper/blob/main/config/F003/printer.cfg),
and [Hi configuration](https://github.com/CrealityOfficial/Hi_Klipper/blob/main/config/F018_CR4NU200360C20/printer.cfg).

The public interface is `[endstop_bridge name]`,
`[endstop_bridge_input name]` with `bridge: name`, and the load-cell probe's
`trigger_bridge: name`. Virtual endstops use `endstop_bridge_<name>:<input>`;
the stationary wire check is `TEST_ENDSTOP_BRIDGE BRIDGE=<name>`.

This optional extension forwards multiple endstop sources, **one at a time**,
over a single GPIO signal wire between MCUs (with a common ground). It is
intended for hosts such as the T113 where load spikes can violate native
multi-MCU homing's communication deadline. It is not a serial bus, does not
encode XYZ simultaneously, and does not carry pressure sample data.

The digital path is:

`selected source GPIO -> sender MCU timer -> wire -> receiver MCU endstop -> motors`

The force-probe path is:

`ADC -> native trigger_analog -> local trsync callback -> wire -> receiver endstop -> motors`

Neither stop path requires a Linux host round trip after the MCU detects a
trigger. The host still prepares each move, performs tare/calibration, feeds
motion queues, maintains watchdog communication and reports the result.
This does not make a stalled or disconnected host safe to continue printing.
Native timeout constants are unchanged: 25ms for multi-MCU dispatch and
250ms for single-MCU dispatch. With this extension the sensor dispatch and
motor dispatch each contain only one MCU; motors must all be on the receiver.

## Configuration: digital XYZ example

The following illustrative pin map is **not an F009 hardware profile**.
Connect all selected input signals to the sender MCU. Connect its PA15 to
the receiving MCU's PC7 and verify voltage compatibility and common ground.
`!` means active low; input and output polarities are independent.

```ini
[endstop_bridge sync]
output_pin: !nozzle_mcu:PA15
receive_pin: ^!PC7
poll_interval: 0.000050

[endstop_bridge_input x]
bridge: sync
input_pin: ^nozzle_mcu:PB0
filter_count: 2
period: 0.000050

[endstop_bridge_input y]
bridge: sync
input_pin: ^nozzle_mcu:PB1

[endstop_bridge_input z]
bridge: sync
input_pin: ^nozzle_mcu:PB2

[stepper_x]
endstop_pin: endstop_bridge_sync:x
# Keep the other motor/axis settings from the printer configuration.

[stepper_y]
endstop_pin: endstop_bridge_sync:y

[stepper_z]
endstop_pin: endstop_bridge_sync:z
```

These are fragments to merge into existing sections, not duplicate sections.
More named inputs may be added, subject to MCU memory/OID limits. The virtual
pin chooses its source automatically for homing; there is no manual AXES
command to forget. Home axes separately (`G28 X`, `G28 Y`, `G28 Z`). Normal
Cartesian/CoreXY homing sequences can use `G28` because they home axes in
separate moves. Simultaneous endstops sharing this wire (for example a Delta
homing move or independently triggered multi-Z alignment) are rejected.
For CoreXY, both participating XY motors must be on the receiver MCU.

Digital inputs default to two consecutive active samples at 50us intervals;
`filter_count` accepts 1..255 and `period` accepts 10us..10ms. Deactivation
takes one inactive sample. This is an asymmetric activation filter, not
a latch. Do not use it for pulses shorter than the sampling/filter window.
`poll_interval` sets the receiver's maximum idle sampling interval and
accepts 10us..10ms. Receiver confirmation also uses native endstop sampling.
These settings add MCU-local detection latency; they are not a measured
end-to-end stopping guarantee.

Compared with the K2 dual-input Python interface, this implementation has
one output owner and named input channels rather than two independent
output-writing objects selected with `AXES=0/1`. It additionally integrates
native force-probe triggers, rejects simultaneous use and checks continuity
before motion. It is an independent implementation of the shared
wire idea, not a port of K2's private MCU/PRTouch implementation.

`QUERY_ENDSTOPS` reads each digital source directly without selecting it on
the wire. It reports the logical input, not proof of continuity of the wire.

## F009: existing X wire plus native force Z probing

F009's existing connection is `nozzle PB0 -> PA15 -> main MCU PC7`.
Its Y switch is on the main MCU PB13, so **leave Y unchanged** unless the
hardware is rewired. Routing Y in software alone cannot move that physical
signal to the toolhead. Only one signal wire is shared; ground and normal
MCU communication/power connections are still needed.

Use the bridge and X sections above, but verify PB0's active polarity on the
actual board; add `!` to `input_pin` if the switch is active low. Use the
virtual X endstop rather than a direct `PC7` endstop declaration. Remove the
old `[gpio_forward ...]` / `io_remap` output owner and any PRTouch swap-pin
owner before assigning PA15/PC7 to the bridge. Do not use duplicate-pin
overrides to retain multiple owners.

For force Z, do **not** add a digital Z source. Instead merge this into the
native load-cell probe configuration:

```ini
[load_cell_probe]
sensor_type: cs1237
sclk_pin: nozzle_mcu:PB13
dout_pin: nozzle_mcu:PB14
trigger_bridge: sync
# Supply measured calibration, z_offset and safe force/speed limits.

[stepper_z]
endstop_pin: probe:z_virtual_endstop
```

The ADC and bridge output must be on the same MCU. Native filters, tare,
force limits, probe/bed-mesh algorithms and calibration remain in use.
Omitting `trigger_bridge` retains the existing native synchronization path.
Pressure values still travel over the regular MCU communications link;
the wire transports only the stop indication. This does not import the
private PRTouch pressure algorithm or handshake protocol.

## Ownership and failure behavior

* Every move acquires exclusive ownership of the wire. A second owner is
  rejected before scheduling its movement.
* Before a move is scheduled, the host commands active and inactive levels
  and verifies both at the receiver. A stuck level, inverted configuration,
  or disconnected wire that cannot follow both levels aborts preparation.
* Native analog trigger success, raw-range/filter/sensor errors and sensor
  monitoring/watchdog timeout all assert the same wire, stopping locally.
  The host subsequently checks the original analog trigger reason; an error
  must not be accepted as a successful bed contact.
* Analog output is latched until both sides have finished. Normal completion
  and command-error cleanup release the bridge. Sender firmware shutdown
  asserts the active output.
* A single level wire is **not a safety-rated link**. A wire that breaks
  after self-test, or a sender losing power with an inactive pull-up level,
  may hide the contact. A receiving MCU reset and wiring faults also need
  hardware consideration. Communication watchdogs remain enabled but are
  not a replacement for immediate contact detection. Never increase a
  timeout to conceal wiring or sensor problems.

Run `TEST_ENDSTOP_BRIDGE BRIDGE=sync` while stationary to test both levels.
It does not move motors or test force calibration. Do not connect actuators
or another device that could respond dangerously to these test levels.
Software debug-output mode skips physical level assertions because no
MCU is attached; its success is not a wiring test.

## Validation and commissioning

Both MCUs and Klippy must use matching firmware/protocol versions. This
implementation adds `config_endstop_bridge`, `config_endstop_bridge_input`,
`set_endstop_bridge`, `arm_endstop_bridge` and
`query_endstop_bridge_input`.

Automated tests cover host ownership/wire checks, source error propagation,
and actual firmware `trigger_analog.c`, `trsync.c`, `endstop_bridge.c` and
`endstop.c` with simulated hardware. Firmware tests inject contact, range,
filter, sensor, sample-monitor and watchdog events and check the receiving
stop callback without intervening host commands. They do not simulate ADC
analog characteristics, real interrupts, signal integrity or Linux latency.

Before deployment, with an operator at the printer: verify inactive/active
levels and source polarity with motors disabled, calibrate force, test each
axis separately at low speed with clearance, then measure trigger-to-stop
timing under T113 CPU/I/O load. Test error handling without driving the nozzle
into the bed. No physical commissioning is implied by the software tests.

To reproduce the offline tests on Linux/WSL with Python's serial, cffi,
greenlet, NumPy and SciPy dependencies plus GCC/AVR toolchains:

```sh
sh scripts/test-endstop-bridge.sh
```

Hardware validation and loaded-host latency measurements remain outstanding.

## Review goals

Feedback is welcome on the architecture, configuration interface, exclusive
channel ownership, and failure handling, as well as the additional tests and
hardware measurements required before an upstream submission.
