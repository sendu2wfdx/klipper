import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "klippy" / "extras" / "io_remap.py"


class FakeCommand:
    def __init__(self):
        self.calls = []

    def send(self, values):
        self.calls.append(values)


class FakeMCU:
    def __init__(self):
        self.config_callbacks = []
        self.config_commands = []
        self.command = FakeCommand()

    def create_oid(self):
        return 7

    def register_config_callback(self, callback):
        self.config_callbacks.append(callback)

    def add_config_cmd(self, command, is_init=False):
        self.config_commands.append((command, is_init))

    def seconds_to_clock(self, seconds):
        return int(round(seconds * 1000000.))

    def lookup_command(self, command):
        assert command == (
            "set_io_remap oid=%c enabled=%c filter_count=%c period_ticks=%u")
        return self.command


class FakePins:
    def __init__(self, mcu):
        self.mcu = mcu
        self.calls = []

    def lookup_pin(self, pin, can_invert=False, can_pullup=False):
        self.calls.append((pin, can_invert, can_pullup))
        pullup = pin.startswith("^")
        normalized = pin[1:] if pullup else pin
        chip_name, pin_name = normalized.split(":", 1)
        assert chip_name in ("mcu", "nozzle_mcu")
        return {"chip": self.mcu, "pin": pin_name, "pullup": int(pullup)}


class FakeGCode:
    def __init__(self):
        self.mux_commands = []
        self.commands = []

    def register_mux_command(self, command, key, value, callback):
        self.mux_commands.append((command, key, value, callback))

    def register_command(self, command, callback):
        self.commands.append((command, callback))


class FakePrinter:
    def __init__(self):
        self.mcu = FakeMCU()
        self.pins = FakePins(self.mcu)
        self.gcode = FakeGCode()
        self.handlers = {}

    def lookup_object(self, name):
        return {"pins": self.pins, "gcode": self.gcode}[name]

    def register_event_handler(self, event, callback):
        self.handlers[event] = callback


class FakeConfig:
    def __init__(self, name, values):
        self.name = name
        self.values = dict(values)
        self.printer = FakePrinter()

    def get_printer(self):
        return self.printer

    def get_name(self):
        return self.name

    def get(self, name, default=None):
        return self.values.get(name, default)

    def getboolean(self, name, default=None):
        value = self.values.get(name, default)
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "on")
        return bool(value)

    def getint(self, name, default=None, minval=None, maxval=None):
        value = self.values.get(name, default)
        value = int(value, 0) if isinstance(value, str) else int(value)
        assert minval is None or value >= minval
        assert maxval is None or value <= maxval
        return value

    def getfloat(self, name, default=None, above=None):
        value = float(self.values.get(name, default))
        assert above is None or value > above
        return value

    def error(self, message):
        return ValueError(message)


class FakeGCmd:
    def __init__(self, values):
        self.values = values

    def get_int(self, name, minval=None, maxval=None):
        value = int(self.values[name])
        assert minval is None or value >= minval
        assert maxval is None or value <= maxval
        return value


class FakeStepper:
    def __init__(self, name):
        self.name = name

    def get_name(self):
        return self.name


class FakeEndstop:
    def __init__(self, stepper_name):
        self.stepper = FakeStepper(stepper_name)

    def get_steppers(self):
        return [self.stepper]


class FakeHomingMove:
    def __init__(self, stepper_name):
        self.endstop = FakeEndstop(stepper_name)

    def get_mcu_endstops(self):
        return [self.endstop]


def load_module():
    spec = importlib.util.spec_from_file_location("io_remap_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v57_config_maps_unqualified_pins_to_nozzle_and_preserves_timing():
    module = load_module()
    config = FakeConfig("io_remap", {
        "src_pin": "PB0", "remap_pin": "PA15", "src_pullup": "1",
        "remap_def": "1", "filterNum": "1", "periodTicks": "0",
    })
    remap = module.IORemap(config)
    remap._build_config()

    assert config.printer.pins.calls == [
        ("^nozzle_mcu:PB0", False, True),
        ("nozzle_mcu:PA15", False, False),
    ]
    assert remap.enabled is False
    assert remap._get_effective_filter_count() == 2
    assert remap._get_period_ticks() == 10
    assert config.printer.mcu.config_commands == [
        ("config_io_remap oid=7 src_pin=PB0 src_pullup=1 remap_pin=PA15"
         " default_value=1", False),
        ("set_io_remap oid=7 enabled=0 filter_count=2 period_ticks=10", True),
    ]
    assert [item[:3] for item in config.printer.gcode.mux_commands] == [
        ("SET_IO_REMAP", "REMAP", "io_remap")]
    assert [item[0] for item in config.printer.gcode.commands] == ["SET_IOREMAP"]

    remap.cmd_SET_IO_REMAP(FakeGCmd({"S": 1}))
    assert config.printer.mcu.command.calls[-1] == [7, True, 2, 10]


def test_v57_x_homing_automatically_enables_and_restores_remap():
    module = load_module()
    config = FakeConfig("io_remap", {
        "src_pin": "PB0", "remap_pin": "PA15", "src_pullup": 1,
        "remap_def": 1, "filterNum": 1, "periodTicks": 0,
    })
    remap = module.IORemap(config)
    begin = config.printer.handlers["homing:homing_move_begin"]
    end = config.printer.handlers["homing:homing_move_end"]

    begin(FakeHomingMove("stepper_y"))
    assert config.printer.mcu.command.calls == []
    begin(FakeHomingMove("stepper_x"))
    end(FakeHomingMove("stepper_x"))
    assert config.printer.mcu.command.calls == [
        [7, True, 2, 10], [7, False, 2, 10]]


def test_named_public_config_keeps_modern_semantics():
    module = load_module()
    config = FakeConfig("io_remap guard", {
        "src_pin": "mcu:PB0", "remap_pin": "mcu:PA15",
        "filter_count": 5, "period": .000050, "default_value": 1,
    })
    remap = module.IORemap(config)
    remap._build_config()
    assert remap.enabled is True
    assert remap._get_effective_filter_count() == 5
    assert remap._get_period_ticks() == 50
    assert config.printer.handlers == {}
    assert config.printer.gcode.commands == []
