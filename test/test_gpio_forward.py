import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / 'klippy'))
from extras.gpio_forward import GPIOForward



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
            "set_gpio_forward oid=%c enabled=%c filter_count=%c period_ticks=%u")
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



class Pins:
    def __init__(self, mcu):
        self.mcu = mcu

    def lookup_pin(self, pin, can_invert=False, can_pullup=False):
        modifiers = pin[:len(pin) - len(pin.lstrip('^~!'))]
        chip, name = pin.lstrip('^~!').split(':')
        return {'chip': self.mcu if chip == 'nozzle_mcu' else object(),
                'pin': name, 'invert': int('!' in modifiers),
                'pullup': 1 if '^' in modifiers else -int('~' in modifiers)}


def make_forward(**values):
    options = {'input_pin': '^!nozzle_mcu:PB0',
               'output_pin': '!nozzle_mcu:PA15', 'enable': False,
               'home_x': True, 'filter_count': 2, 'period': .000010}
    options.update(values)
    config = FakeConfig('gpio_forward x_endstop', options)
    config.printer.pins = Pins(config.printer.mcu)
    config.printer.config_error = ValueError
    return GPIOForward(config), config.printer


def test_f009_polarity_and_filter_match_factory_wiring():
    forward, printer = make_forward()
    forward._build_config()
    assert printer.mcu.config_commands == [
        ('config_gpio_forward oid=7 input_pin=PB0 input_pullup=1'
         ' input_invert=1 output_pin=PA15 output_invert=1', False),
        ('set_gpio_forward oid=7 enabled=0 filter_count=2 period_ticks=10', True)]
    assert printer.gcode.mux_commands[0][:3] == (
        'SET_GPIO_FORWARD', 'NAME', 'x_endstop')


@pytest.mark.parametrize('pin,pullup,invert', [
    ('nozzle_mcu:PB0', 0, 0), ('^nozzle_mcu:PB0', 1, 0),
    ('!nozzle_mcu:PB0', 0, 1), ('~!nozzle_mcu:PB0', -1, 1)])
def test_bias_is_independent_of_polarity(pin, pullup, invert):
    forward, _ = make_forward(input_pin=pin)
    assert (forward.src_pullup, forward.src_invert) == (pullup, invert)


def test_normal_and_failed_x_homing_restore_disabled_output():
    forward, printer = make_forward()
    begin = printer.handlers['homing:homing_move_begin']
    end = printer.handlers['homing:homing_move_end']
    begin(FakeHomingMove('stepper_z'))
    assert printer.mcu.command.calls == []
    begin(FakeHomingMove('stepper_x'))
    end(FakeHomingMove('stepper_x'))
    begin(FakeHomingMove('stepper_x'))
    printer.handlers['gcode:command_error']()
    assert [c[1] for c in printer.mcu.command.calls] == [True, False]*2
    assert not forward.enabled


def test_runtime_toggle_and_preserve_already_enabled():
    forward, printer = make_forward()
    forward.cmd_SET_GPIO_FORWARD(FakeGCmd({'ENABLE': 1}))
    forward._handle_homing_move_begin(FakeHomingMove('stepper_x'))
    forward._handle_homing_move_end(FakeHomingMove('stepper_x'))
    assert forward.enabled
    assert len(printer.mcu.command.calls) == 1


def test_reject_cross_mcu_and_unrepresentable_period():
    with pytest.raises(ValueError, match='same MCU'):
        make_forward(output_pin='mcu:PA15')
    forward, _ = make_forward(period=.000000001)
    with pytest.raises(ValueError, match='clock tick'):
        forward._build_config()
