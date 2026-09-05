import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "klippy" / "extras" / "nozzle_clear.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "nozzle_clear_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CommandError(Exception):
    pass


class FakeGCmd:
    error = CommandError


class FakeGCode:
    def __init__(self):
        self.scripts = []

    def run_script_from_command(self, script):
        self.scripts.append(script)


class FakeToolhead:
    def __init__(self):
        self.position = [110., 110., 10., 0.]
        self.set_calls = []

    def get_status(self, eventtime):
        return {"homed_axes": "xyz"}

    def get_position(self):
        return list(self.position)

    def set_position(self, position, homing_axes=()):
        self.position = list(position)
        self.set_calls.append((list(position), tuple(homing_axes)))


class FakeProbe:
    def get_status(self, eventtime):
        return {"last_z_result": 8.}


class FakeReactor:
    def monotonic(self):
        return 10.


class FakePrinter:
    def __init__(self):
        self.toolhead = FakeToolhead()
        self.probe = FakeProbe()
        self.reactor = FakeReactor()

    def lookup_object(self, name):
        if name == "toolhead":
            return self.toolhead
        if name == "probe":
            return self.probe
        raise AssertionError(name)

    def command_error(self, message):
        return CommandError(message)

    def get_reactor(self):
        return self.reactor


def bare_nozzle(module, enabled=True):
    nozzle = module.NozzleClear.__new__(module.NozzleClear)
    nozzle.printer = FakePrinter()
    nozzle.gcode = FakeGCode()
    nozzle.enabled = enabled
    nozzle.touch_gain = 1.5
    nozzle.trigger_force = 75.
    nozzle.erase_dir = False
    nozzle.random_ofs = (3, 2)
    nozzle.zmax = 200.
    nozzle.pre_clear_enable = True
    nozzle.pre_clear_probe_pos = (85, 224)
    nozzle.pre_clear_start = (85, 224)
    nozzle.pre_clear_temp = 170
    nozzle.pre_clear_touch_speed = 15.
    nozzle.pre_clear_touch_cnt = 3
    nozzle.pre_clear_retract_dist = 2.
    nozzle.clear_enable = True
    nozzle.clear_start = (89, 223)
    nozzle.clear_length = (42, 3)
    nozzle.clear_temp = 170
    nozzle.clear_speed = 12000.
    nozzle.clear_cnt = 8
    nozzle.clear_upraise = 1.5
    nozzle.clear_closure_temp = 130.
    nozzle.rub_enable = True
    nozzle.rub_start = (75, 223)
    nozzle.rub_length = (6, 2)
    nozzle.rub_speed = 6000.
    nozzle.rub_upraise = -.2
    nozzle.inside_nozzle_clear = False
    return nozzle


def test_disabled_nozzle_clear_never_issues_motion():
    module = load_module()
    nozzle = bare_nozzle(module, enabled=False)
    with pytest.raises(CommandError, match="safety-locked"):
        nozzle.cmd_NOZ_CLEAR(FakeGCmd())
    assert nozzle.gcode.scripts == []


def test_public_probe_override_replaces_private_prtouch_threshold_mutation():
    module = load_module()
    nozzle = bare_nozzle(module)
    nozzle._probe()
    assert nozzle.gcode.scripts == [
        "PROBE PROBE_SPEED=15 SAMPLE_RETRACT_DIST=2 "
        "TRIGGER_FORCE=112.5 SAMPLES=1"
    ]


def test_touch_down_accounts_for_public_probe_ascent_retract():
    module = load_module()
    nozzle = bare_nozzle(module)
    nozzle._touch_down()
    assert nozzle.printer.toolhead.set_calls[-1] == (
        [110., 110., 2., 0.], (2,))


def test_f009_sequence_preserves_probe_count_temperatures_and_final_position(
        monkeypatch):
    module = load_module()
    nozzle = bare_nozzle(module)
    monkeypatch.setattr(module.random, "uniform", lambda low, high: high / 2.)

    nozzle.cmd_NOZ_CLEAR(FakeGCmd())

    scripts = nozzle.gcode.scripts
    probes = [line for line in scripts if line.startswith("PROBE ")]
    assert len(probes) == 7
    assert all("TRIGGER_FORCE=112.5" in line for line in probes)
    assert "G90\nG0 Z1.5 F600" in scripts
    assert "G90\nG0 Z-0.2 F600" in scripts
    assert "M109 S170" in scripts
    assert "M109 S158" in scripts
    assert "M109 S130" in scripts
    assert scripts[-3:] == [
        "G90", "G0 Z10 F600", "G0 X110 Y110 Z10 F18000"]
    assert nozzle.inside_nozzle_clear is False


def test_legacy_force_and_endurance_commands_remain_locked():
    module = load_module()
    nozzle = bare_nozzle(module)
    with pytest.raises(CommandError, match="safety-locked"):
        nozzle.cmd_FORECEZ(FakeGCmd())
    with pytest.raises(CommandError, match="safety-locked"):
        nozzle.cmd_NOZ_CLEAR_TEST(FakeGCmd())


def test_sequence_error_restores_absolute_mode_and_stops_fan(monkeypatch):
    module = load_module()
    nozzle = bare_nozzle(module)
    monkeypatch.setattr(module.random, "uniform", lambda low, high: 0.)
    original_run = nozzle._run

    def fail_on_heat(script):
        if script == "M109 S170":
            raise CommandError("heater failed")
        original_run(script)

    nozzle._run = fail_on_heat
    with pytest.raises(CommandError, match="heater failed"):
        nozzle.cmd_NOZ_CLEAR(FakeGCmd())
    assert nozzle.gcode.scripts[-2:] == ["M106 S0", "G90"]
    assert nozzle.inside_nozzle_clear is False
