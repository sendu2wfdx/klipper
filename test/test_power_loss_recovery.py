import importlib.util
import json
from pathlib import Path
import struct

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "klippy" / "extras" / "power_loss_recovery.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "power_loss_recovery_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeEEPROM:
    def __init__(self):
        self.memory = bytearray([0xff] * 2048)
        self.writes = []

    def read_reg(self, addr, size):
        return self.memory[addr:addr + size]

    def write_reg(self, addr, data):
        values = bytes([data]) if isinstance(data, int) else bytes(data)
        self.writes.append((addr, values))
        self.memory[addr:addr + len(values)] = values


def test_v57_erased_journal_first_record_uses_header_slot_255():
    module = load_module()
    eeprom = FakeEEPROM()
    journal = module.V57EEPROMJournal(eeprom)

    assert journal.read() is None
    assert journal.record(0x12345678, 42.25) == 255
    assert eeprom.memory[0] == 255
    assert eeprom.memory[1] == 1
    assert eeprom.memory[2040:2044] == b"\x78\x56\x34\x12"
    assert struct.unpack("<f", eeprom.memory[2044:2048])[0] == 42.25
    assert journal.read() == {
        "slot": 255,
        "file_position": 0x12345678,
        "base_position_e": pytest.approx(42.25),
    }


def test_v57_journal_rotates_after_255_updates_and_wraps_to_slot_one():
    module = load_module()
    eeprom = FakeEEPROM()
    journal = module.V57EEPROMJournal(eeprom)

    for position in range(255):
        assert journal.record(position, -3.5) == 255
    assert journal.write_count == 256
    assert journal.record(255, 7.0) == 1
    assert eeprom.memory[0] == 1
    assert int.from_bytes(eeprom.memory[8:12], "little") == 255
    assert struct.unpack("<f", eeprom.memory[12:16])[0] == 7.0


def test_journal_clear_and_value_validation():
    module = load_module()
    eeprom = FakeEEPROM()
    journal = module.V57EEPROMJournal(eeprom)
    journal.record(12, 1.5)
    journal.clear()
    assert journal.read() is None
    assert journal.write_count == 1
    with pytest.raises(ValueError):
        journal.record(-1, 0.)
    with pytest.raises(ValueError):
        journal.record(0x100000000, 0.)
    with pytest.raises(ValueError):
        journal.record(1, float("nan"))


class FakeCoord:
    def __init__(self, z):
        self.z = z


class FakeGCodeMove:
    def __init__(self, z=1.0, base_e=12.345):
        self.z = z
        self.base_position = [0., 0., 0., base_e]

    def get_status(self, eventtime):
        return {"gcode_position": FakeCoord(self.z)}


class FakeReactor:
    def __init__(self, now=100.):
        self.now = now

    def monotonic(self):
        return self.now


class FakeToolhead:
    def __init__(self):
        self.position = [0., 0., 0., 0.]
        self.set_calls = []

    def get_position(self):
        return list(self.position)

    def set_position(self, position, homing_axes=()):
        self.position = list(position)
        self.set_calls.append((list(position), tuple(homing_axes)))


class FakePrinter:
    def __init__(self):
        self.toolhead = FakeToolhead()

    def lookup_object(self, name):
        if name == "toolhead":
            return self.toolhead
        raise AssertionError(name)


class FakeVSD:
    def __init__(self, path, position=1234):
        self._path = path
        self.file_position = position
        self.continue_print = False

    def file_path(self):
        return self._path


def bare_recovery(tmp_path):
    module = load_module()
    recovery = module.PowerLossRecovery.__new__(module.PowerLossRecovery)
    recovery.enabled = True
    recovery.state_path = str(tmp_path / "power-loss.json")
    recovery.record_interval = 5.
    recovery.metadata_interval = 15.
    recovery.min_z = .6
    recovery.min_g1 = 18
    recovery.pending_recovery = None
    recovery.file_identity = None
    recovery.automatic_restore = False
    recovery.z_restore_strategy = "disabled"
    recovery.preheat_temp = 185.
    recovery.safe_z_lift = 5.
    recovery.prime_length = 9.3
    recovery.resume_xy_speed = 3000.
    recovery.resume_z_speed = 600.
    recovery.slow_percent = 20.
    recovery.slow_restore_lines = 200
    recovery.allow_tool_restore = False
    recovery.restore_state = "idle"
    recovery.restore_error = None
    recovery.recovery_layer = recovery.recovery_lines = None
    recovery.restore_speed_factor = recovery.restore_flow_factor = 100.
    recovery.reactor = FakeReactor()
    recovery.printer = FakePrinter()
    recovery.gcode_move = FakeGCodeMove()
    recovery.eeprom = FakeEEPROM()
    recovery.journal = module.V57EEPROMJournal(recovery.eeprom)
    recovery._reset_tracking()
    return module, recovery


def test_new_print_clears_stale_state_and_matching_continue_is_safety_locked(
        tmp_path):
    _, recovery = bare_recovery(tmp_path)
    gcode_path = tmp_path / "cube.gcode"
    gcode_data = b"G90\nG1 X10 Y20 Z0.8 E2.5\n"
    gcode_path.write_bytes(gcode_data)
    vsd = FakeVSD(str(gcode_path))
    recovery.journal.record(len(gcode_data), 2.5)
    recovery._write_state_file(vsd.file_path())

    class GCodeError(Exception):
        pass

    class FakeGCode:
        error = GCodeError

    recovery.gcode = FakeGCode()
    with pytest.raises(GCodeError, match="safety-locked"):
        recovery._prepare_print(vsd, True)
    assert recovery.pending_recovery["file_position"] == len(gcode_data)
    assert recovery.pending_recovery["gcode_state"]["position"] == {
        "X": 10., "Y": 20., "Z": .8, "E": 2.5}
    assert recovery.journal.is_active()

    recovery._prepare_print(vsd, False)
    assert not recovery.journal.is_active()
    assert not Path(recovery.state_path).exists()
    assert recovery.pending_recovery is None


def test_mismatched_continue_is_rejected_and_invalidated(tmp_path):
    _, recovery = bare_recovery(tmp_path)
    recovery.journal.record(55, 1.)
    recovery._write_state_file("/gcodes/other.gcode")

    class GCodeError(Exception):
        pass

    class FakeGCode:
        error = GCodeError

    recovery.gcode = FakeGCode()
    with pytest.raises(GCodeError, match="No matching"):
        recovery._prepare_print(FakeVSD("/gcodes/cube.gcode"), True)
    assert not recovery.journal.is_active()
    assert not Path(recovery.state_path).exists()


def test_same_path_modified_file_is_rejected_by_sha256_identity(tmp_path):
    _, recovery = bare_recovery(tmp_path)
    path = tmp_path / "cube.gcode"
    original = b"G90\nM109 S200\nG1 X1 Y2 Z3 E4\n"
    path.write_bytes(original)
    recovery.journal.record(len(original), 1.)
    recovery._write_state_file(str(path))
    path.write_bytes(b"G90\nM109 S250\nG1 X9 Y9 Z9 E9\n")

    class GCodeError(Exception):
        pass

    class FakeGCode:
        error = GCodeError

    recovery.gcode = FakeGCode()
    with pytest.raises(GCodeError, match="No matching"):
        recovery._prepare_print(FakeVSD(str(path)), True)
    assert not recovery.journal.is_active()


def test_line_tracking_records_position_and_metadata_then_completion_clears(
        tmp_path):
    _, recovery = bare_recovery(tmp_path)
    vsd = FakeVSD("/gcodes/cube.gcode", 4321)
    recovery._print_start(vsd)
    for _ in range(18):
        recovery._gcode_line(vsd, "G1 X1")
    recovery._gcode_line(vsd, "G1 Z0.8 E1")

    info = recovery.journal.read()
    assert info["file_position"] == 4321
    assert info["base_position_e"] == pytest.approx(12.35)
    with open(recovery.state_path, encoding="utf-8") as state_file:
        assert json.load(state_file) == {"file_path": vsd.file_path()}

    recovery._print_end(vsd, "paused")
    assert recovery.journal.is_active()
    recovery._print_end(vsd, "error")
    assert recovery.journal.is_active()
    recovery._print_end(vsd, "complete")
    assert not recovery.journal.is_active()
    assert not Path(recovery.state_path).exists()


def test_cancel_clears_both_journal_and_metadata(tmp_path):
    _, recovery = bare_recovery(tmp_path)
    recovery.journal.record(20, 1.)
    recovery._write_state_file("/gcodes/cube.gcode")
    recovery._cancel_event(FakeVSD("/gcodes/cube.gcode"))
    assert not recovery.journal.is_active()
    assert not Path(recovery.state_path).exists()


def test_recovery_scanner_reconstructs_modes_motion_and_process_state(
        tmp_path):
    module = load_module()
    path = tmp_path / "state.gcode"
    data = (
        ";LAYER:4\n"
        "G90\nM82\n"
        "G1 X10 Y20 Z0.8 E4 F6000\n"
        "G91\nM83\n"
        "G1 X2 Y-3 Z0.2 E0.5 F1200\n"
        "G92 E7\n"
        "M140 S60\nM109 S215\n"
        "M106 P1 S180\nM107 P0\n"
        "M220 S80\nM221 S95\n"
        "M204 S5000\n"
        "SET_PRESSURE_ADVANCE ADVANCE=0.035 SMOOTH_TIME=0.04\n"
        "BED_MESH_PROFILE LOAD=default\n"
        "T2\n").encode()
    path.write_bytes(data)

    state = module.GCodeRecoveryScanner().scan(str(path), len(data))

    assert state["position"] == {
        "X": 12., "Y": 17., "Z": 1., "E": 7.}
    assert state["seen_axes"] == ["E", "X", "Y", "Z"]
    assert state["absolute_coordinates"] is False
    assert state["absolute_extrude"] is False
    assert state["feedrate"] == 1200.
    assert state["bed_temp"] == 60.
    assert state["extruder_temp"] == 215.
    assert state["fan_state"] == {
        0: "M106 P0 S0", 1: "M106 P1 S180"}
    assert state["speed_factor"] == 80.
    assert state["flow_factor"] == 95.
    assert state["m204"] == "M204 S5000"
    assert state["pressure_advance"].startswith("SET_PRESSURE_ADVANCE ")
    assert state["bed_mesh_profile"] == "BED_MESH_PROFILE LOAD=default"
    assert state["tool"] == "T2"
    assert state["layer"] == 4


def test_recovery_scanner_honors_independent_xyz_and_e_modes(tmp_path):
    module = load_module()
    path = tmp_path / "modes.gcode"
    data = b"G90\nM83\nG1 X5 E2\nG1 X8 E3\nG91\nM82\nG1 X2 E10\n"
    path.write_bytes(data)
    state = module.GCodeRecoveryScanner().scan(str(path), len(data))
    assert state["position"] == {
        "X": 10., "Y": 0., "Z": 0., "E": 10.}
    assert state["absolute_coordinates"] is False
    assert state["absolute_extrude"] is True


def test_recovery_scanner_rejects_out_of_range_and_midline_offsets(tmp_path):
    module = load_module()
    path = tmp_path / "bad-offset.gcode"
    data = b"G90\nG1 X10 Y20\n"
    path.write_bytes(data)
    scanner = module.GCodeRecoveryScanner()
    with pytest.raises(ValueError, match="outside"):
        scanner.scan(str(path), 0)
    with pytest.raises(ValueError, match="line boundary"):
        module.GCodeRecoveryScanner().scan(str(path), 8)
    with pytest.raises(ValueError, match="outside"):
        module.GCodeRecoveryScanner().scan(str(path), len(data) + 1)


def test_invalid_matching_record_is_cleared_before_motion(tmp_path):
    _, recovery = bare_recovery(tmp_path)
    path = tmp_path / "cube.gcode"
    path.write_text("G1 X1 Y1\n", encoding="utf-8")
    recovery.journal.record(3, 0.)
    recovery._write_state_file(str(path))

    class GCodeError(Exception):
        pass

    class FakeGCode:
        error = GCodeError

    recovery.gcode = FakeGCode()
    with pytest.raises(GCodeError, match="Invalid power-loss"):
        recovery._prepare_print(FakeVSD(str(path)), True)
    assert not recovery.journal.is_active()
    assert not Path(recovery.state_path).exists()


def full_resume_state():
    return {
        "absolute_coordinates": True,
        "absolute_extrude": True,
        "position": {"X": 101., "Y": 102., "Z": 12.4, "E": 88.},
        "seen_axes": ["E", "X", "Y", "Z"],
        "extruder_temp": 215.,
        "bed_temp": 60.,
        "fan_state": {0: "M106 P0 S128", 1: "M106 P1 S200"},
        "speed_factor": 85.,
        "flow_factor": 97.,
        "m204": "M204 S5000",
        "pressure_advance": "SET_PRESSURE_ADVANCE ADVANCE=0.035",
        "bed_mesh_profile": "BED_MESH_PROFILE LOAD=default",
        "tool": None,
        "layer": 9,
    }


def test_recovery_plan_has_safe_lift_heat_restore_and_modal_order():
    module = load_module()
    commands = module.RecoveryCommandPlan.build(full_resume_state())
    assert commands[:8] == [
        "M140 S60", "M104 S185", "G90",
        ("set_kinematic_z", 12.4), "G91", "G0 Z5 F600",
        "G90", "G28 X Y"]
    assert commands.index("M190 S60") < commands.index("M109 S215")
    assert commands.index("G28 X Y") < commands.index(
        "G1 X101 Y102 F3000")
    assert commands.index("G1 X101 Y102 F3000") < commands.index(
        "G1 Z12.4 F600")
    assert "G1 E9.3 F300" in commands
    assert "G92 E88" in commands
    assert commands[-4:] == ["M221 S97", "M220 S20", "G90", "M400"]


def test_recovery_plan_rejects_missing_axis_temperature_and_tool_state():
    module = load_module()
    state = full_resume_state()
    state["seen_axes"] = ["X", "Y"]
    with pytest.raises(ValueError, match="saved Z"):
        module.RecoveryCommandPlan.build(state)
    state = full_resume_state()
    state["extruder_temp"] = None
    with pytest.raises(ValueError, match="hotend"):
        module.RecoveryCommandPlan.build(state)
    state = full_resume_state()
    state["tool"] = "T1"
    with pytest.raises(ValueError, match="485 toolboard"):
        module.RecoveryCommandPlan.build(state)


def test_enabled_resume_seeks_executes_and_restores_speed_on_next_layer(
        tmp_path):
    _, recovery = bare_recovery(tmp_path)
    data = (
        ";LAYER:9\nG90\nM82\nM140 S60\nM109 S215\n"
        "G1 X101 Y102 Z12.4 E88\nM220 S85\nM221 S97\n").encode()
    path = tmp_path / "resume.gcode"
    path.write_bytes(data)
    vsd = FakeVSD(str(path), 0)
    vsd.continue_print = True
    recovery.journal.record(len(data), 10.)
    recovery._write_state_file(str(path))
    recovery.automatic_restore = True
    recovery.z_restore_strategy = "assume_unchanged"

    class GCodeError(Exception):
        pass

    class FakeGCode:
        error = GCodeError

        def __init__(self):
            self.scripts = []

        def run_script_from_command(self, script):
            self.scripts.append(script)

    recovery.gcode = FakeGCode()
    recovery._prepare_print(vsd, True)
    assert vsd.file_position == len(data)
    assert recovery.restore_state == "ready"
    recovery._print_start(vsd)
    assert recovery.restore_state == "resumed"
    assert "G28 X Y" in recovery.gcode.scripts
    assert recovery.printer.toolhead.set_calls == [
        ([0., 0., 12.4, 0.], (2,))]
    assert recovery.gcode.scripts[-2:] == ["G90", "M400"]

    recovery._gcode_line(vsd, ";LAYER:10")
    assert recovery.gcode.scripts[-3:] == [
        "M220 S85", "M221 S97", "M400"]
    assert recovery.recovery_lines is None


def test_manual_m24_cannot_bypass_disabled_automatic_restore(tmp_path):
    _, recovery = bare_recovery(tmp_path)
    recovery.pending_recovery = {"gcode_state": full_resume_state()}

    class GCodeError(Exception):
        pass

    class FakeGCode:
        error = GCodeError

        def __init__(self):
            self.scripts = []

        def run_script_from_command(self, script):
            self.scripts.append(script)

    recovery.gcode = FakeGCode()
    with pytest.raises(GCodeError, match="safety-locked"):
        recovery._execute_recovery()
    assert recovery.gcode.scripts == []
    assert recovery.printer.toolhead.set_calls == []


def test_disabled_connect_has_no_runtime_object_dependencies():
    module = load_module()
    recovery = module.PowerLossRecovery.__new__(module.PowerLossRecovery)
    recovery.enabled = False

    class NoLookupPrinter:
        def lookup_object(self, name):
            raise AssertionError("disabled module attempted object lookup")

    recovery.printer = NoLookupPrinter()
    recovery._connect()
