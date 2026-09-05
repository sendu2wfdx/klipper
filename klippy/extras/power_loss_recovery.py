# Creality V57 compatible BL24C16F power-loss journal
#
# This source restoration records the original EEPROM journal, exposes the
# original availability/cancel webhooks, reconstructs G-Code state, and has an
# opt-in restore executor.  The target config keeps execution locked until the
# unchanged-Z assumption and physical recovery sequence are validated.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json
import hashlib
import logging
import math
import os
import re
import struct


ERASED = 0xff
ACTIVE = 1
SLOT_SIZE = 8
FIRST_SLOT = 1
LAST_SLOT = 255


class GCodeRecoveryScanner:
    """Reconstruct the public Klipper state at an exact file byte offset."""
    _word_re = re.compile(
        r"([A-Za-z])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))")

    def __init__(self):
        self.state = {
            "absolute_coordinates": True,
            "absolute_extrude": True,
            "position": {"X": 0., "Y": 0., "Z": 0., "E": 0.},
            "seen_axes": [],
            "feedrate": None,
            "extruder_temp": None,
            "bed_temp": None,
            "fan_state": {},
            "speed_factor": 100.,
            "flow_factor": 100.,
            "m204": None,
            "pressure_advance": None,
            "bed_mesh_profile": None,
            "tool": None,
            "layer": 0,
            "line_count": 0,
            "file_position": 0,
        }
        self._seen_axes = set()

    @classmethod
    def _words(cls, line):
        return {name.upper(): float(value)
                for name, value in cls._word_re.findall(line)}

    def _parse_line(self, raw_line):
        line = raw_line.lstrip("\ufeff").strip()
        if not line:
            return
        layer = PowerLossRecovery._parse_layer(line)
        if layer is not None:
            self.state["layer"] = layer
        code = line.split(";", 1)[0].strip()
        if not code:
            return
        command = code.split(None, 1)[0].upper()
        words = self._words(code)
        if command == "G90":
            self.state["absolute_coordinates"] = True
        elif command == "G91":
            self.state["absolute_coordinates"] = False
        elif command == "M82":
            self.state["absolute_extrude"] = True
        elif command == "M83":
            self.state["absolute_extrude"] = False
        elif command == "G92":
            for axis in "XYZE":
                if axis in words:
                    self.state["position"][axis] = words[axis]
                    self._seen_axes.add(axis)
        elif command in ("G0", "G00", "G1", "G01"):
            position = self.state["position"]
            for axis in "XYZ":
                if axis not in words:
                    continue
                if self.state["absolute_coordinates"]:
                    position[axis] = words[axis]
                else:
                    position[axis] += words[axis]
                self._seen_axes.add(axis)
            if "E" in words:
                if self.state["absolute_extrude"]:
                    position["E"] = words["E"]
                else:
                    position["E"] += words["E"]
                self._seen_axes.add("E")
            if "F" in words:
                self.state["feedrate"] = words["F"]
        elif command in ("M104", "M109") and "S" in words:
            self.state["extruder_temp"] = words["S"]
        elif command in ("M140", "M190") and "S" in words:
            self.state["bed_temp"] = words["S"]
        elif command == "M106":
            fan = int(words.get("P", 0.))
            self.state["fan_state"][fan] = code
        elif command == "M107":
            fan = int(words.get("P", 0.))
            self.state["fan_state"][fan] = "M106 P%d S0" % fan
        elif command == "M220" and "S" in words:
            self.state["speed_factor"] = words["S"]
        elif command == "M221" and "S" in words:
            self.state["flow_factor"] = words["S"]
        elif command == "M204":
            self.state["m204"] = code
        elif command == "SET_PRESSURE_ADVANCE":
            self.state["pressure_advance"] = code
        elif command == "BED_MESH_PROFILE":
            self.state["bed_mesh_profile"] = code
        else:
            tool = re.match(r"^T(\d+)$", command)
            if tool is not None:
                self.state["tool"] = "T%s" % tool.group(1)

    def scan(self, file_path, file_position):
        file_position = int(file_position)
        file_size = os.path.getsize(file_path)
        if file_position <= 0 or file_position > file_size:
            raise ValueError(
                "recovery file position %d outside 1..%d" %
                (file_position, file_size))
        with open(file_path, "rb") as gcode_file:
            while gcode_file.tell() < file_position:
                raw_line = gcode_file.readline()
                if not raw_line:
                    raise ValueError("recovery position is beyond EOF")
                if gcode_file.tell() > file_position:
                    raise ValueError(
                        "recovery position is not on a G-Code line boundary")
                self._parse_line(raw_line.decode("utf-8"))
                self.state["line_count"] += 1
        self.state["file_position"] = file_position
        self.state["seen_axes"] = sorted(self._seen_axes)
        return self.state


class RecoveryCommandPlan:
    """Build the ordered, reviewable command sequence for a V57 resume."""
    @staticmethod
    def build(state, preheat_temp=185., safe_z_lift=5., prime_length=9.3,
              xy_speed=3000., z_speed=600., slow_percent=20.,
              allow_tool_restore=False):
        missing = set("XYZ") - set(state.get("seen_axes", []))
        if missing:
            raise ValueError(
                "recovery G-Code has no saved %s position" %
                ",".join(sorted(missing)))
        if state.get("tool") and not allow_tool_restore:
            raise ValueError(
                "multi-tool recovery is disabled until the 485 toolboard "
                "protocol is integrated")
        position = state["position"]
        commands = []
        bed_temp = state.get("bed_temp")
        hotend_temp = state.get("extruder_temp")
        if hotend_temp is None or hotend_temp <= 0.:
            raise ValueError("recovery G-Code has no valid hotend target")
        if bed_temp is not None and bed_temp > 0.:
            commands.append("M140 S%.6g" % bed_temp)
        commands.extend([
            "M104 S%.6g" % min(hotend_temp, preheat_temp),
            "G90",
            ("set_kinematic_z", position["Z"]),
            "G91",
            "G0 Z%.6g F%.6g" % (safe_z_lift, z_speed),
            "G90",
            "G28 X Y",
        ])
        mesh = state.get("bed_mesh_profile")
        if mesh:
            commands.append(mesh)
        if bed_temp is not None and bed_temp > 0.:
            commands.append("M190 S%.6g" % bed_temp)
        commands.append("M109 S%.6g" % hotend_temp)
        if state.get("tool"):
            commands.append(state["tool"])
        if prime_length > 0.:
            commands.extend([
                "M83", "G92 E0",
                "G1 E%.6g F300" % prime_length,
            ])
        commands.append("G92 E%.6g" % position["E"])
        commands.append("M82" if state["absolute_extrude"] else "M83")
        commands.extend([
            "G1 X%.6g Y%.6g F%.6g" %
            (position["X"], position["Y"], xy_speed),
            "G1 Z%.6g F%.6g" % (position["Z"], z_speed),
        ])
        for unused, command in sorted(state.get("fan_state", {}).items()):
            commands.append(command)
        for key in ("m204", "pressure_advance"):
            if state.get(key):
                commands.append(state[key])
        commands.append("M221 S%.6g" % state.get("flow_factor", 100.))
        speed = (slow_percent if state.get("layer", 0) > 1
                 else state.get("speed_factor", 100.))
        commands.append("M220 S%.6g" % speed)
        commands.append("G90" if state["absolute_coordinates"] else "G91")
        commands.append("M400")
        return commands


class V57EEPROMJournal:
    def __init__(self, eeprom, writes_per_slot=255):
        self.eeprom = eeprom
        self.writes_per_slot = writes_per_slot
        self.write_count = 1

    def is_active(self):
        return self.eeprom.read_reg(1, 1)[0] != ERASED

    def get_slot(self):
        return self.eeprom.read_reg(0, 1)[0]

    def clear(self):
        self.eeprom.write_reg(1, ERASED)
        self.write_count = 1

    def read(self):
        if not self.is_active():
            return None
        slot = self.get_slot()
        offset = slot * SLOT_SIZE
        file_position = int.from_bytes(
            self.eeprom.read_reg(offset, 4), "little")
        base_position_e = self.eeprom.read_reg(offset + 4, 4)
        return {
            "slot": slot,
            "file_position": file_position,
            "base_position_e": struct.unpack("<f", base_position_e)[0],
        }

    def record(self, file_position, base_position_e):
        file_position = int(file_position)
        base_position_e = float(base_position_e)
        if file_position < 0 or file_position > 0xffffffff:
            raise ValueError("file_position outside unsigned 32-bit range")
        if not math.isfinite(base_position_e):
            raise ValueError("base_position_e must be finite")
        active = self.is_active()
        slot = self.get_slot()
        if active and self.write_count >= self.writes_per_slot + 1:
            self.write_count = 1
            slot += 1
            if slot > LAST_SLOT:
                slot = FIRST_SLOT
        offset = slot * SLOT_SIZE
        if not active:
            self.eeprom.write_reg(1, ACTIVE)
        self.eeprom.write_reg(
            offset, file_position.to_bytes(4, "little"))
        self.eeprom.write_reg(offset + 4, struct.pack("<f", base_position_e))
        if not active or slot != self.get_slot():
            self.eeprom.write_reg(0, slot)
        self.write_count += 1
        return slot


class PowerLossRecovery:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.enabled = config.getboolean("enabled", False)
        self.automatic_restore = config.getboolean(
            "automatic_restore", False)
        self.z_restore_strategy = config.get(
            "z_restore_strategy", "disabled").strip().lower()
        if self.z_restore_strategy not in ("disabled", "assume_unchanged"):
            raise config.error(
                "z_restore_strategy must be disabled or assume_unchanged")
        if self.automatic_restore and self.z_restore_strategy == "disabled":
            raise config.error(
                "automatic_restore requires an explicit z_restore_strategy")
        self.preheat_temp = config.getfloat(
            "preheat_temp", 185., minval=0., maxval=320.)
        self.safe_z_lift = config.getfloat(
            "safe_z_lift", 5., above=0.)
        self.prime_length = config.getfloat(
            "prime_length", 9.3, minval=0., maxval=50.)
        self.resume_xy_speed = config.getfloat(
            "resume_xy_speed", 3000., above=0.)
        self.resume_z_speed = config.getfloat(
            "resume_z_speed", 600., above=0.)
        self.slow_percent = config.getfloat(
            "slow_percent", 20., above=0., maxval=100.)
        self.slow_restore_lines = config.getint(
            "slow_restore_lines", 200, minval=1)
        self.allow_tool_restore = config.getboolean(
            "allow_tool_restore", False)
        self.state_path = os.path.expanduser(config.get(
            "state_path",
            "/mnt/UDISK/printer_data/config/power_loss_recovery.json"))
        self.record_interval = config.getfloat(
            "record_interval", 5., above=0.)
        self.metadata_interval = config.getfloat(
            "metadata_interval", 15., above=0.)
        self.min_z = config.getfloat("min_z", .6, minval=0.)
        self.min_g1 = config.getint("min_g1", 18, minval=0)
        self.writes_per_slot = config.getint(
            "writes_per_slot", 255, minval=1, maxval=255)
        self.vsd = self.eeprom = self.journal = None
        self.gcode_move = self.print_stats = None
        self.pending_recovery = None
        self.file_identity = None
        self.restore_state = "idle"
        self.restore_error = None
        self.recovery_layer = self.recovery_lines = None
        self.restore_speed_factor = self.restore_flow_factor = 100.
        self._reset_tracking()
        self.printer.register_event_handler("klippy:connect", self._connect)
        self.printer.register_event_handler(
            "virtual_sdcard:prepare_print", self._prepare_print)
        self.printer.register_event_handler(
            "virtual_sdcard:print_start", self._print_start)
        self.printer.register_event_handler(
            "virtual_sdcard:gcode_line", self._gcode_line)
        self.printer.register_event_handler(
            "virtual_sdcard:print_end", self._print_end)
        self.printer.register_event_handler(
            "virtual_sdcard:cancel", self._cancel_event)
        self.gcode.register_command(
            "POWER_LOSS_RECOVERY_STATUS", self.cmd_STATUS)
        self.gcode.register_command(
            "POWER_LOSS_RECOVERY_CLEAR", self.cmd_CLEAR)
        webhooks = self.printer.lookup_object("webhooks")
        webhooks.register_endpoint(
            "pause_resume/check_continue_print_state", self._web_check)
        webhooks.register_endpoint(
            "pause_resume/cancel_continue_print", self._web_cancel)

    def _reset_tracking(self):
        self.g1_count = 0
        self.layer = self.last_layer = 0
        self.z_move_count = self.last_z_move_count = 0
        self.last_record_time = self.last_metadata_time = 0.
        self.fan_state = {}

    def _connect(self):
        # A disabled compatibility module must remain inert so that minimal
        # MCU/config validation does not need the virtual_sdcard stack.
        if not self.enabled:
            return
        self.vsd = self.printer.lookup_object("virtual_sdcard")
        self.eeprom = self.printer.lookup_object("bl24c16f")
        self.gcode_move = self.printer.lookup_object("gcode_move")
        self.print_stats = self.printer.lookup_object("print_stats")
        self.journal = V57EEPROMJournal(
            self.eeprom, self.writes_per_slot)

    def _remove_state_file(self):
        try:
            if os.path.exists(self.state_path):
                os.remove(self.state_path)
        except OSError:
            logging.exception("Unable to remove power-loss state file")

    def _get_file_identity(self, file_path, force=False):
        if (not force and self.file_identity is not None
                and self.file_identity.get("file_path") == file_path):
            return self.file_identity
        before = os.stat(file_path)
        digest = hashlib.sha256()
        with open(file_path, "rb") as source:
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        after = os.stat(file_path)
        if (before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns):
            raise OSError("G-Code file changed while fingerprinting")
        self.file_identity = {
            "file_path": file_path,
            "file_size": after.st_size,
            "file_sha256": digest.hexdigest(),
        }
        return self.file_identity

    def _write_state_file(self, file_path):
        directory = os.path.dirname(self.state_path)
        try:
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp_path = self.state_path + ".tmp"
            data = {"file_path": file_path}
            try:
                data.update(self._get_file_identity(file_path))
            except OSError:
                # Preserve V57 path-only compatibility for synthetic/debug
                # streams that do not expose a normal host file.
                logging.debug("Unable to fingerprint virtual SD file %s",
                              file_path)
            with open(tmp_path, "w", encoding="utf-8") as state_file:
                json.dump(data, state_file, separators=(",", ":"))
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(tmp_path, self.state_path)
        except OSError:
            logging.exception("Unable to write power-loss state file")

    def _state_file_matches(self, file_path):
        try:
            with open(self.state_path, "r", encoding="utf-8") as state_file:
                saved = json.load(state_file)
            if saved.get("file_path") != file_path:
                return False
            if "file_size" not in saved and "file_sha256" not in saved:
                return True
            current = self._get_file_identity(file_path, force=True)
            return (saved.get("file_size") == current["file_size"]
                    and saved.get("file_sha256") ==
                    current["file_sha256"])
        except (OSError, ValueError, AttributeError):
            return False

    def _prepare_print(self, vsd, is_continue):
        if not self.enabled:
            return
        if not is_continue:
            self.journal.clear()
            self._remove_state_file()
            self.pending_recovery = None
            self.file_identity = None
            self.restore_state = "idle"
            self.restore_error = None
            self.recovery_layer = self.recovery_lines = None
            return
        info = self.journal.read()
        file_path = vsd.file_path()
        if info is None or not self._state_file_matches(file_path):
            self.journal.clear()
            self._remove_state_file()
            raise self.gcode.error("No matching power-loss recovery record")
        try:
            gcode_state = GCodeRecoveryScanner().scan(
                file_path, info["file_position"])
        except (OSError, UnicodeError, ValueError) as err:
            self.journal.clear()
            self._remove_state_file()
            raise self.gcode.error(
                "Invalid power-loss recovery position: %s" % err)
        # Do not perform an unsafe position-only seek. A later stage will
        # restore XYZE modes, temperatures, fan state, and pressure advance,
        # then explicitly apply info['file_position'].
        self.pending_recovery = dict(info)
        self.pending_recovery["gcode_state"] = gcode_state
        if not self.automatic_restore:
            raise self.gcode.error(
                "Power-loss record verified, but automatic motion recovery "
                "is safety-locked")
        if self.z_restore_strategy != "assume_unchanged":
            raise self.gcode.error(
                "Power-loss recovery has no approved Z restore strategy")
        try:
            RecoveryCommandPlan.build(
                gcode_state, self.preheat_temp, self.safe_z_lift,
                self.prime_length, self.resume_xy_speed,
                self.resume_z_speed, self.slow_percent,
                self.allow_tool_restore)
        except ValueError as err:
            raise self.gcode.error("Unsafe power-loss record: %s" % err)
        vsd.file_position = info["file_position"]
        self.restore_state = "ready"
        self.restore_error = None

    def _execute_recovery(self):
        if (not self.automatic_restore
                or self.z_restore_strategy != "assume_unchanged"):
            raise self.gcode.error(
                "Automatic power-loss recovery is safety-locked")
        state = self.pending_recovery["gcode_state"]
        commands = RecoveryCommandPlan.build(
            state, self.preheat_temp, self.safe_z_lift,
            self.prime_length, self.resume_xy_speed,
            self.resume_z_speed, self.slow_percent,
            self.allow_tool_restore)
        self.restore_state = "restoring"
        try:
            for command in commands:
                if isinstance(command, tuple):
                    action, value = command
                    if action != "set_kinematic_z":
                        raise ValueError("Unknown recovery action %s" % action)
                    toolhead = self.printer.lookup_object("toolhead")
                    position = list(toolhead.get_position())
                    position[2] = value
                    toolhead.set_position(position, homing_axes=(2,))
                    continue
                self.gcode.run_script_from_command(command)
        except Exception as err:
            self.restore_state = "error"
            self.restore_error = str(err)
            try:
                self.gcode.run_script_from_command("M106 S0")
                self.gcode.run_script_from_command("G90")
            except Exception:
                logging.exception("Power-loss recovery cleanup failed")
            raise
        self.restore_state = "resumed"
        self.restore_error = None
        self.recovery_layer = state.get("layer", 0)
        self.recovery_lines = 0
        self.restore_speed_factor = state.get("speed_factor", 100.)
        self.restore_flow_factor = state.get("flow_factor", 100.)

    def _print_start(self, vsd):
        if not self.enabled:
            return
        self._reset_tracking()
        now = self.reactor.monotonic()
        self.last_record_time = self.last_metadata_time = now
        if (self.pending_recovery is not None
                and getattr(vsd, "continue_print", False)):
            self._execute_recovery()

    @staticmethod
    def _parse_layer(line):
        match = re.match(
            r"\s*;\s*(?:LAYER\s*:|layer\s*#?\s*)(-?\d+)",
            line, re.IGNORECASE)
        return int(match.group(1)) if match else None

    def _gcode_line(self, vsd, line):
        if not self.enabled or self.journal is None:
            return
        stripped = line.strip()
        layer = self._parse_layer(stripped)
        if layer is not None:
            self.layer = layer
        if self.recovery_lines is not None:
            self.recovery_lines += 1
            next_layer = (layer is not None and self.recovery_layer is not None
                          and layer > self.recovery_layer)
            line_limit = self.recovery_lines >= self.slow_restore_lines
            if next_layer or line_limit:
                self.gcode.run_script_from_command(
                    "M220 S%.6g" % self.restore_speed_factor)
                self.gcode.run_script_from_command(
                    "M221 S%.6g" % self.restore_flow_factor)
                self.gcode.run_script_from_command("M400")
                self.recovery_lines = self.recovery_layer = None
        if stripped.startswith("M106"):
            key = stripped.split(" S", 1)[0]
            self.fan_state[key] = stripped
        if re.match(r"^G0?1(?:\s|$)", stripped, re.IGNORECASE):
            self.g1_count += 1
            if re.search(r"(?:^|\s)Z[-+.0-9]", stripped,
                         re.IGNORECASE):
                self.z_move_count += 1
        now = self.reactor.monotonic()
        status = self.gcode_move.get_status(now)
        z_pos = status["gcode_position"].z
        eligible = self.layer > 2 or (
            self.g1_count > self.min_g1 and z_pos > self.min_z)
        layer_changed = self.layer > 2 and self.layer > self.last_layer
        no_layer_timeout = self.layer == 0 and (
            now - self.last_record_time > self.record_interval)
        z_changed = self.z_move_count > self.last_z_move_count and eligible
        if eligible and (layer_changed or no_layer_timeout or z_changed):
            base_e = round(self.gcode_move.base_position[-1], 2)
            self.journal.record(vsd.file_position, base_e)
            self.last_layer = self.layer
            self.last_z_move_count = self.z_move_count
            self.last_record_time = now
        if (self.g1_count == 19
                or now - self.last_metadata_time > self.metadata_interval):
            self._write_state_file(vsd.file_path())
            self.last_metadata_time = now

    def _print_end(self, vsd, outcome):
        if not self.enabled:
            return
        if outcome == "complete":
            self.journal.clear()
            self._remove_state_file()
        if outcome != "paused":
            self.pending_recovery = None
            self.file_identity = None
            self.recovery_lines = self.recovery_layer = None

    def _cancel_event(self, vsd):
        if self.enabled and self.journal is not None:
            self.journal.clear()
            self._remove_state_file()
            self.pending_recovery = None
            self.file_identity = None
            self.restore_state = "idle"
            self.restore_error = None
            self.recovery_layer = self.recovery_lines = None

    def _availability(self):
        file_state = False
        try:
            with open(self.state_path, "r", encoding="utf-8") as state_file:
                file_state = bool(json.load(state_file))
        except (OSError, ValueError):
            pass
        eeprom_state = bool(self.journal and self.journal.is_active())
        if self.print_stats is not None and self.print_stats.state != "standby":
            file_state = eeprom_state = False
        return {"file_state": file_state, "eeprom_state": eeprom_state}

    def _web_check(self, web_request):
        result = self._availability()
        web_request.send(result)

    def _web_cancel(self, web_request):
        if self.journal is not None:
            self.journal.clear()
        self._remove_state_file()
        self.pending_recovery = None
        self.file_identity = None
        self.restore_state = "idle"
        self.restore_error = None
        self.recovery_layer = self.recovery_lines = None
        web_request.send({"result": "success"})

    def cmd_STATUS(self, gcmd):
        gcmd.respond_info("power_loss_recovery=%s" % self._availability())

    def cmd_CLEAR(self, gcmd):
        if self.journal is not None:
            self.journal.clear()
        self._remove_state_file()
        self.pending_recovery = None
        self.file_identity = None
        self.restore_state = "idle"
        self.restore_error = None
        self.recovery_layer = self.recovery_lines = None
        gcmd.respond_info("Power-loss recovery record cleared")

    def get_status(self, eventtime):
        status = self._availability()
        status.update({
            "enabled": self.enabled,
            "automatic_restore": self.automatic_restore,
            "z_restore_strategy": self.z_restore_strategy,
            "restore_state": self.restore_state,
            "restore_error": self.restore_error,
            "pending_recovery": self.pending_recovery,
        })
        return status


def load_config(config):
    return PowerLossRecovery(config)
