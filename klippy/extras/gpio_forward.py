# MCU-local GPIO forwarding with optional homing activation
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from .io_remap import IORemap


class GPIOForward(IORemap):
    # Share the MCU timer/control ABI and homing lifecycle with the legacy
    # driver, but expose explicit polarity and standard pin names.
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name().split()[1]
        self.legacy = False
        self.legacy_filter_semantics = False
        self.legacy_period_ticks = 0
        self.enabled = config.getboolean('enable', True)
        self.filter_count = config.getint('filter_count', 1,
                                          minval=1, maxval=255)
        self.period = config.getfloat('period', .000050, above=0.)
        self.home_x = config.getboolean('home_x', False)
        ppins = self.printer.lookup_object('pins')
        src = ppins.lookup_pin(config.get('input_pin'), can_invert=True,
                               can_pullup=True)
        dst = ppins.lookup_pin(config.get('output_pin'), can_invert=True)
        if src['chip'] is not dst['chip']:
            raise config.error('gpio_forward pins must be on the same MCU')
        self.mcu = src['chip']
        self.oid = self.mcu.create_oid()
        self.src_pin = src['pin']
        self.src_pullup = src['pullup']
        self.src_invert = src['invert']
        self.dst_pin = dst['pin']
        self.default_value = dst['invert']
        self.mcu.register_config_callback(self._build_config)
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_mux_command('SET_GPIO_FORWARD', 'NAME', self.name,
                                        self.cmd_SET_IO_REMAP)
        self._homing_restore_enabled = None
        if self.home_x:
            self.printer.register_event_handler(
                'homing:homing_move_begin', self._handle_homing_move_begin)
            self.printer.register_event_handler(
                'homing:homing_move_end', self._handle_homing_move_end)
            self.printer.register_event_handler(
                'gcode:command_error', self._handle_command_error)

    def _get_period_ticks(self):
        ticks = self.mcu.seconds_to_clock(self.period)
        if ticks < 1:
            raise self.printer.config_error(
                'gpio_forward period is shorter than one MCU clock tick')
        return ticks

    def _build_config(self):
        self.mcu.add_config_cmd(
            'config_gpio_forward oid=%d input_pin=%s input_pullup=%d'
            ' input_invert=%d output_pin=%s output_invert=%d'
            % (self.oid, self.src_pin, self.src_pullup, self.src_invert,
               self.dst_pin, self.default_value))
        self.mcu.add_config_cmd(
            'set_io_remap oid=%d enabled=%d filter_count=%d period_ticks=%d'
            % (self.oid, self.enabled, self.filter_count,
               self._get_period_ticks()), is_init=True)


def load_config_prefix(config):
    return GPIOForward(config)
