# Digital input for a shared Endstop Bridge
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from .endstop_bridge import BridgedHoming


class EndstopBridgeInput(BridgedHoming):
    def __init__(self, config):
        printer = config.get_printer()
        bridge = printer.load_object(config, 'endstop_bridge ' + config.get('bridge'))
        super().__init__(config, bridge)
        ppins = printer.lookup_object('pins')
        self.pin = ppins.lookup_pin(config.get('input_pin'), can_invert=True,
                                   can_pullup=True)
        if self.pin['chip'] is not bridge.mcu:
            raise config.error('Forwarded input and bridge output must share an MCU')
        self.filter_count = config.getint('filter_count', 2, minval=1, maxval=255)
        self.period = config.getfloat('period', .000050,
                                      minval=.000010, maxval=.010)
        self.oid = bridge.mcu.create_oid()
        self.query_cmd = None
        bridge.mcu.register_config_callback(self._build_config)

    def _build_config(self):
        self.bridge.mcu.add_config_cmd(
            'config_endstop_bridge_input oid=%d bridge_oid=%d input_pin=%s'
            ' pull_up=%d invert=%d filter_count=%d period_ticks=%d'
            % (self.oid, self.bridge.oid, self.pin['pin'], self.pin['pullup'],
               self.pin['invert'], self.filter_count,
               self.bridge.mcu.seconds_to_clock(self.period)))
        self.query_cmd = self.bridge.mcu.lookup_query_command(
            'query_endstop_bridge_input oid=%c',
            'endstop_bridge_input_state oid=%c value=%c', oid=self.oid)

    def prepare_homing(self):
        super().prepare_homing()
        try:
            self.bridge.select_input(self, self.oid)
        except Exception:
            self.bridge.release(self)
            raise

    def get_mcu(self):
        return self.receiver.get_mcu()

    def home_start(self, print_time, sample_time, sample_count, rest_time,
                   triggered=True):
        return self._start_receiver(print_time, sample_time, sample_count,
                                    rest_time, triggered)

    def home_wait(self, home_end_time):
        try:
            result = self.receiver.home_wait(home_end_time)
            self.receiver_active = False
            return result
        finally:
            self._cleanup()

    def query_endstop(self, print_time):
        # QUERY_ENDSTOPS reads each source without switching the shared wire.
        if self.bridge.mcu.is_fileoutput():
            return 0
        clock = self.bridge.mcu.print_time_to_clock(print_time)
        return self.query_cmd.send([self.oid], minclock=clock)['value']


def load_config_prefix(config):
    return EndstopBridgeInput(config)
