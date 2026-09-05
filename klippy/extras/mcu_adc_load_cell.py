# MCU internal ADC adapter for the public load_cell_probe stack
#
# This is an opt-in secondary/coarse sensor path.  It intentionally remains
# separate from the factory PRTouch V3 compatibility implementation.
#
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.

import collections
import threading

from . import bulk_sensor


UPDATE_INTERVAL = .100


class MCUADLoadCell:
    def __init__(self, config):
        self.printer = printer = config.get_printer()
        self.name = config.get_name().split()[-1]
        self.sps = config.getint('sample_rate', 400, minval=10, maxval=2000)
        self.sample_count = config.getint(
            'adc_sample_count', 1, minval=1, maxval=15)
        self.sample_time = config.getfloat(
            'adc_sample_time', 0., minval=0., maxval=.001)
        adc_pin = config.get('adc_pin')
        ppins = printer.lookup_object('pins')
        self.mcu_adc = ppins.setup_pin('adc', adc_pin)
        self.mcu = self.mcu_adc.get_mcu()
        self.max_count = None
        self.last_error_count = 0
        self.overflow_count = 0
        self._collecting = False
        self._lock = threading.Lock()
        self._samples = collections.deque(maxlen=max(32, self.sps * 2))

        batch_num = max(1, min(24, int(self.sps * UPDATE_INTERVAL)))
        self.mcu_adc.setup_adc_sample(
            1. / self.sps, self.sample_time, self.sample_count,
            batch_num=batch_num)
        self.mcu_adc.setup_adc_callback(self._handle_samples)
        self.mcu.register_config_callback(self._build_config)
        self.batch_bulk = bulk_sensor.BatchBulkHelper(
            printer, self._process_batch, self._start_measurements,
            self._finish_measurements, UPDATE_INTERVAL)

    def _build_config(self):
        adc_max = int(self.mcu.get_constant_float('ADC_MAX'))
        self.max_count = adc_max * self.sample_count
        if self.max_count >= (1 << 16):
            raise self.printer.config_error(
                "mcu_adc ADC sample sum exceeds 16-bit analog_in storage")

    def setup_trigger_analog(self, trigger_analog_oid):
        self.mcu_adc.setup_trigger_analog(trigger_analog_oid)

    def get_mcu(self):
        return self.mcu

    def get_samples_per_second(self):
        return self.sps

    def get_range(self):
        if self.max_count is None:
            # Configuration consumers normally call this after MCU identify.
            # Use the protocol storage range as a conservative early value.
            return 0, 0xffff
        return 0, self.max_count

    def get_status(self, eventtime):
        return {'errors': self.last_error_count,
                'overflows': self.overflow_count,
                'sample_rate': self.sps,
                'adc_sample_count': self.sample_count}

    def lookup_sensor_error(self, error_code):
        return "Unknown MCU ADC error %d" % (error_code,)

    def add_client(self, callback):
        self.batch_bulk.add_client(callback)

    def _handle_samples(self, samples):
        max_count = self.max_count
        if max_count is None or not self._collecting:
            return
        with self._lock:
            for ptime, fraction in samples:
                if len(self._samples) == self._samples.maxlen:
                    self.overflow_count += 1
                raw = max(0, min(max_count,
                                 int(round(fraction * max_count))))
                self._samples.append((round(ptime, 6), raw,
                                      round(fraction, 9)))

    def _start_measurements(self):
        self.last_error_count = 0
        self.overflow_count = 0
        with self._lock:
            self._samples.clear()
            self._collecting = True

    def _finish_measurements(self):
        # analog_in is shared infrastructure configured at MCU startup.  Stop
        # client delivery without disabling the ADC or its safety trigger.
        with self._lock:
            self._collecting = False
            self._samples.clear()

    def _process_batch(self, eventtime):
        with self._lock:
            samples = list(self._samples)
            self._samples.clear()
        if not samples:
            return None
        return {'data': samples, 'errors': self.last_error_count,
                'overflows': self.overflow_count}


MCU_ADC_SENSOR_TYPE = {'mcu_adc': MCUADLoadCell}
