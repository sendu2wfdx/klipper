# Creality F009 nozzle-cleaning sequence for the public load-cell probe
#
# This module preserves the F009 movement and temperature sequence while
# replacing private prtouch_v3 state mutation with per-probe public Klipper
# parameters.  It is disabled until the target load cell is calibrated.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import math
import random


class NozzleClear:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        self.enabled = config.getboolean("enabled", False)
        if self.enabled and not config.has_section("load_cell_probe"):
            raise config.error(
                "[nozzle_clear] requires [load_cell_probe]")

        self.touch_gain = config.getfloat(
            "touch_gain", 1.5, minval=1., maxval=5.)
        self.trigger_force = config.getfloat(
            "trigger_force", 75., minval=10., maxval=250.)
        if self.trigger_force * self.touch_gain > 250.:
            raise config.error(
                "[nozzle_clear] trigger_force * touch_gain must not "
                "exceed the public probe limit of 250 grams")
        self.erase_dir = config.getboolean("erase_dir", False)
        self.random_ofs = config.getintlist("random_ofs", (3, 2), count=2)
        self.zmax = config.getfloat("zmax", 200., above=0.)

        self.pre_clear_enable = config.getboolean(
            "pre_clear_enable", True)
        self.pre_clear_probe_pos = config.getintlist(
            "pre_clear_probe_pos", (85, 224), count=2)
        self.pre_clear_start = config.getintlist(
            "pre_clear_start", self.pre_clear_probe_pos, count=2)
        self.pre_clear_temp = config.getint(
            "pre_clear_temp", 170, minval=0, maxval=320)
        self.pre_clear_touch_speed = config.getfloat(
            "pre_clear_touch_speed", 15., above=0.)
        self.pre_clear_touch_cnt = config.getint(
            "pre_clear_touch_cnt", 3, minval=1, maxval=20)
        self.pre_clear_retract_dist = config.getfloat(
            "pre_clear_retract_dist", 2., minval=0.)

        self.clear_enable = config.getboolean("clear_enable", True)
        self.clear_start = config.getintlist(
            "clear_start", (89, 223), count=2)
        # Keep Creality's misspelled option name for configuration parity.
        self.clear_length = config.getintlist(
            "clear_lenght", (42, 3), count=2)
        self.clear_temp = config.getint(
            "clear_temp", 170, minval=0, maxval=320)
        self.clear_speed = config.getfloat(
            "clear_speed", 12000., above=0.)
        self.clear_cnt = config.getint(
            "clear_cnt", 8, minval=3, maxval=100)
        self.clear_upraise = config.getfloat("clear_upraise", 1.5)
        self.clear_closure_temp = config.getfloat(
            "clear_closure_temp", 130., minval=0., maxval=320.)

        self.rub_enable = config.getboolean("rub_enable", True)
        self.rub_start = config.getintlist(
            "rub_start", (75, 223), count=2)
        self.rub_length = config.getintlist(
            "rub_lenght", (6, 2), count=2)
        self.rub_speed = config.getfloat("rub_speed", 6000., above=0.)
        self.rub_upraise = config.getfloat("rub_upraise", -.2)

        self.inside_nozzle_clear = False
        self.gcode.register_command(
            "NOZ_CLEAR", self.cmd_NOZ_CLEAR,
            desc="Clear the F009 nozzle on the bed cleaning area")
        self.gcode.register_command(
            "NOZ_CLEAR_TEST", self.cmd_NOZ_CLEAR_TEST,
            desc="Repeated nozzle-clear test (safety locked)")
        self.gcode.register_command(
            "FORECEZ", self.cmd_FORECEZ,
            desc="Legacy raw Z movement (safety locked)")

    def _run(self, script):
        self.gcode.run_script_from_command(script)

    def _set_z_position(self, z_value):
        toolhead = self.printer.lookup_object("toolhead")
        position = list(toolhead.get_position())
        position[2] = float(z_value)
        toolhead.set_position(position, homing_axes=(2,))

    def _probe(self, speed=None, retract=None):
        speed = self.pre_clear_touch_speed if speed is None else speed
        retract = (self.pre_clear_retract_dist
                   if retract is None else retract)
        force = self.trigger_force * self.touch_gain
        self._run(
            "PROBE PROBE_SPEED=%.6g SAMPLE_RETRACT_DIST=%.6g "
            "TRIGGER_FORCE=%.6g SAMPLES=1" % (speed, retract, force))

    def _touch_down(self):
        self._probe()
        eventtime = self.printer.get_reactor().monotonic()
        probe = self.printer.lookup_object("probe")
        contact_z = float(probe.get_status(eventtime)["last_z_result"])
        current_z = float(
            self.printer.lookup_object("toolhead").get_position()[2])
        gap = current_z - contact_z
        if not math.isfinite(gap) or gap < 0.:
            raise self.printer.command_error(
                "Invalid load-cell contact position for nozzle clearing")
        # Public load_cell_probe lifts after contact so it can fit ascent data.
        # Preserve that physical lift, but make the logical coordinate equal
        # to its actual distance above the fitted contact plane.
        self._set_z_position(gap)

    def _rectangle(self, x_length, y_length, speed=None):
        x_first = -x_length if self.erase_dir else x_length
        if speed is None:
            self._run("G0 X%.6g" % x_first)
        else:
            self._run("G0 X%.6g F%.6g" % (x_first, speed))
        self._run("G0 Y%.6g" % y_length)
        self._run("G0 X%.6g" % -x_first)
        self._run("G0 Y%.6g" % -y_length)

    def _check_enabled(self, gcmd):
        if not self.enabled:
            raise gcmd.error(
                "NOZ_CLEAR is safety-locked: calibrate [load_cell_probe] "
                "and then set [nozzle_clear] enabled: True")

    def cmd_NOZ_CLEAR(self, gcmd):
        self._check_enabled(gcmd)
        toolhead = self.printer.lookup_object("toolhead")
        self.inside_nozzle_clear = True
        try:
            dx = random.uniform(0., self.random_ofs[0])
            dy = random.uniform(0., self.random_ofs[1])
            self._run("M204 S10000")
            self._run("M104 S%d" % self.pre_clear_temp)

            homed_axes = toolhead.get_status(
                self.printer.get_reactor().monotonic())["homed_axes"]
            if "x" not in homed_axes or "y" not in homed_axes:
                self._run("G28 X Y")
            if toolhead.get_position()[2] < 5.:
                self._run("G0 Z5 F600")
            self._set_z_position(self.zmax)
            self._run("G0 Z%.6g F600" % (self.zmax - .01))
            self._run("G4 P500")
            self._run("M400")

            if self.pre_clear_enable:
                self._set_z_position(self.zmax)
                self._run("G90\nG0 X%.6g Y%.6g F12000" % (
                    self.pre_clear_probe_pos[0] - dx,
                    self.pre_clear_probe_pos[1] + dy))
                self._touch_down()
                self._run("G0 Z8 F600")
                self._run("G0 X%.6g Y%.6g F18000" % (
                    self.pre_clear_start[0] - dx,
                    self.pre_clear_start[1] + dy))
                self._run("M109 S%d" % self.pre_clear_temp)
                for unused in range(self.pre_clear_touch_cnt):
                    self._probe()
                    x_step = 1.5 if self.erase_dir else -1.5
                    self._run("G91\nG0 Z3 X%.6g F3000" % x_step)
                self._run("G91\nG0 X%d F12000" % (
                    1 if self.erase_dir else -1))

            if self.clear_enable:
                self._run("G90")
                self._run("M106 S255")
                self._run("M104 S%d" % self.clear_temp)
                self._run("M106 S0")
                self._touch_down()
                self._run("M109 S%d" % self.clear_temp)
                self._run("G90\nG0 Z%.6g F600" % self.clear_upraise)
                self._run("G90\nG0 X%d Y%d F%.6g" % (
                    self.clear_start[0], self.clear_start[1],
                    self.clear_speed))
                self._run("G91")
                for unused in range(self.clear_cnt - 3):
                    self._rectangle(*self.clear_length)

            if self.rub_enable:
                self._run("G90\nG0 X%.6g Y%d F%.6g" % (
                    self.rub_start[0] - dx, self.rub_start[1],
                    self.rub_speed))
                self._run("G91\nG0 X%d F12000" % (
                    5 if self.erase_dir else -5))
                self._touch_down()
                self._run("G90\nG0 Z%.6g F600" % self.rub_upraise)
                self._run("G91")
                for unused in range(5):
                    self._rectangle(*self.rub_length, speed=self.rub_speed)
                self._run("M106 S255")
                self._run("M109 S%.6g" % (self.clear_temp - 12.))
                self._run("M106 S0")

            if self.clear_enable:
                self._run("G91\nG0 Z%.6g F600" % self.clear_upraise)
                self._run("G0 Z3 F600")
                self._run("G90\nG0 X%d Y%d F%.6g" % (
                    self.clear_start[0], self.clear_start[1],
                    self.clear_speed))
                self._run("G91\nG0 Z-3 F600")
                self._run("G90\nG0 X%d Y%d F%.6g" % (
                    self.clear_start[0], self.clear_start[1],
                    self.clear_speed))
                self._run("G91")
                for unused in range(3):
                    self._rectangle(*self.clear_length)

            if self.rub_enable:
                self._run("G90\nG0 X%.6g Y%d F%.6g" % (
                    self.rub_start[0] - dx - 10., self.rub_start[1],
                    self.rub_speed))
                self._run("G91\nG0 X%d F12000" % (
                    5 if self.erase_dir else -5))
                self._touch_down()
                self._run("G90\nG0 Z%.6g F600" % self.rub_upraise)
                self._run("G91")
                for unused in range(6):
                    self._rectangle(*self.rub_length, speed=self.rub_speed)
                self._run("M106 S255")
                self._run("M109 S%.6g" % self.clear_closure_temp)
                self._run("M106 S0")

            self._run("G90")
            self._run("G0 Z10 F600")
            self._run("G0 X110 Y110 Z10 F18000")
        except:
            # Leave the machine in absolute positioning with cooling disabled
            # if a probe, heater, or movement command aborts the sequence.
            self._run("M106 S0")
            self._run("G90")
            raise
        finally:
            self.inside_nozzle_clear = False

    def cmd_NOZ_CLEAR_TEST(self, gcmd):
        raise gcmd.error(
            "NOZ_CLEAR_TEST remains safety-locked; validate one NOZ_CLEAR "
            "cycle on the target board first")

    def cmd_FORECEZ(self, gcmd):
        raise gcmd.error(
            "FORECEZ raw dual-Z FORCE_MOVE is intentionally safety-locked")

    def get_status(self, eventtime):
        return {
            "enabled": self.enabled,
            "inside_nozzle_clear": self.inside_nozzle_clear,
            "probe_backend": "load_cell_probe",
        }


def load_config(config):
    return NozzleClear(config)
