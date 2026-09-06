import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / 'klippy'))
from extras.gpio_forward import GPIOForward
from test_io_remap import FakeConfig, FakeGCmd, FakeHomingMove


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
        ('set_io_remap oid=7 enabled=0 filter_count=2 period_ticks=10', True)]
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
    forward.cmd_SET_IO_REMAP(FakeGCmd({'ENABLE': 1}))
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
