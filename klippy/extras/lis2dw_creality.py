# Host-side compatibility for the Creality F009 leveling-board LIS2DW firmware.
#
# The factory GD32E230 firmware uses Klipper's pre-generic-bulk protocol:
#   config_lis2dw oid=%c spi_oid=%c
#   query_lis2dw oid=%c clock=%u rest_ticks=%u
#   lis2dw_data / lis2dw_status
# Keep this separate from upstream lis2dw.py so other MCUs continue to use the
# current generic sensor-bulk protocol.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, threading
from . import bus, adxl345, bulk_sensor

REG_LIS2DW_WHO_AM_I_ADDR = 0x0F
REG_LIS2DW_CTRL_REG1_ADDR = 0x20
REG_LIS2DW_CTRL_REG6_ADDR = 0x25
REG_LIS2DW_FIFO_CTRL = 0x2E
REG_MOD_READ = 0x80

LIS2DW_DEV_ID = 0x44
FREEFALL_ACCEL = 9.80665
LIS2DW_SCALE = FREEFALL_ACCEL * 1.952 / 4
MIN_MSG_TIME = 0.100
BYTES_PER_SAMPLE = 6
SAMPLES_PER_BLOCK = 8
BATCH_UPDATES = 0.100


class CrealityLIS2DW:
    def __init__(self, config):
        self.printer = config.get_printer()
        adxl345.AccelCommandHelper(config, self)
        self.axes_map = adxl345.read_axes_map(
            config, LIS2DW_SCALE, LIS2DW_SCALE, LIS2DW_SCALE)
        self.data_rate = 1600
        self.spi = bus.MCU_SPI_from_config(config, 3, default_speed=5000000)
        self.mcu = mcu = self.spi.get_mcu()
        self.oid = oid = mcu.create_oid()
        self.query_rate = 0
        self.query_lis2dw_cmd = self.query_lis2dw_end_cmd = None
        self.query_lis2dw_status_cmd = None

        mcu.add_config_cmd("config_lis2dw oid=%d spi_oid=%d"
                           % (oid, self.spi.get_oid()))
        mcu.add_config_cmd("query_lis2dw oid=%d clock=0 rest_ticks=0"
                           % (oid,), on_restart=True)
        mcu.register_config_callback(self._build_config)
        mcu.register_serial_response(
            self._handle_lis2dw_data, "lis2dw_data", oid)

        self.lock = threading.Lock()
        self.raw_samples = []
        self.last_sequence = self.max_query_duration = 0
        self.last_limit_count = self.last_error_count = 0
        self.clock_sync = bulk_sensor.ClockSyncRegression(self.mcu, 640)
        self.batch_bulk = bulk_sensor.BatchBulkHelper(
            self.printer, self._process_batch,
            self._start_measurements, self._finish_measurements, BATCH_UPDATES)
        self.name = config.get_name().split()[-1]
        header = ('time', 'x_acceleration', 'y_acceleration',
                  'z_acceleration')
        self.batch_bulk.add_mux_endpoint(
            "lis2dw/dump_lis2dw", "sensor", self.name, {'header': header})

    def _build_config(self):
        cmdqueue = self.spi.get_command_queue()
        query_format = "query_lis2dw oid=%c clock=%u rest_ticks=%u"
        status_format = (
            "lis2dw_status oid=%c clock=%u query_ticks=%u next_sequence=%hu"
            " buffered=%c fifo=%c limit_count=%hu")
        self.query_lis2dw_cmd = self.mcu.lookup_command(
            query_format, cq=cmdqueue)
        self.query_lis2dw_end_cmd = self.mcu.lookup_query_command(
            query_format, status_format, oid=self.oid, cq=cmdqueue)
        self.query_lis2dw_status_cmd = self.mcu.lookup_query_command(
            "query_lis2dw_status oid=%c", status_format,
            oid=self.oid, cq=cmdqueue)

    def read_reg(self, reg):
        params = self.spi.spi_transfer([reg | REG_MOD_READ, 0x00])
        return bytearray(params['response'])[1]

    def set_reg(self, reg, value, minclock=0):
        self.spi.spi_send([reg, value & 0xff], minclock=minclock)
        stored = self.read_reg(reg)
        if stored != value:
            raise self.printer.command_error(
                "Failed to set LIS2DW register [0x%x] to 0x%x: got 0x%x. "
                "This is generally indicative of connection problems "
                "(e.g. faulty wiring) or a faulty LIS2DW chip."
                % (reg, value, stored))

    def _handle_lis2dw_data(self, params):
        with self.lock:
            self.raw_samples.append(params)

    def _update_clock(self, minclock=0):
        for retry in range(5):
            params = self.query_lis2dw_status_cmd.send(
                [self.oid], minclock=minclock)
            fifo = params['fifo'] & 0x1f
            if fifo <= 32:
                break
        else:
            raise self.printer.command_error("Unable to query LIS2DW FIFO")

        mcu_clock = self.mcu.clock32_to_clock64(params['clock'])
        sequence = (self.last_sequence & ~0xffff) | params['next_sequence']
        if sequence < self.last_sequence:
            sequence += 0x10000
        self.last_sequence = sequence
        limit_count = ((self.last_limit_count & ~0xffff)
                       | params['limit_count'])
        if limit_count < self.last_limit_count:
            limit_count += 0x10000
        self.last_limit_count = limit_count

        duration = params['query_ticks']
        if duration > self.max_query_duration:
            self.max_query_duration = max(
                2 * self.max_query_duration,
                self.mcu.seconds_to_clock(.000005))
            return
        self.max_query_duration = 2 * duration
        msg_count = (sequence * SAMPLES_PER_BLOCK
                     + params['buffered'] // BYTES_PER_SAMPLE + fifo)
        self.clock_sync.update(mcu_clock + duration // 2, msg_count + 1)

    def _convert_samples(self, raw_samples):
        (x_pos, x_scale), (y_pos, y_scale), (z_pos, z_scale) = self.axes_map
        time_base, chip_base, inv_freq = self.clock_sync.get_time_translation()
        samples = []
        last_chip_clock = None
        for params in raw_samples:
            seq_diff = (self.last_sequence - params['sequence']) & 0xffff
            seq_diff -= (seq_diff & 0x8000) << 1
            sequence = self.last_sequence - seq_diff
            data = bytearray(params['data'])
            msg_cdiff = sequence * SAMPLES_PER_BLOCK - chip_base
            for index in range(len(data) // BYTES_PER_SAMPLE):
                offset = index * BYTES_PER_SAMPLE
                xl, xh, yl, yh, zl, zh = data[offset:offset + 6]
                raw_xyz = (
                    ((xh << 8) | xl) - ((xh & 0x80) << 9),
                    ((yh << 8) | yl) - ((yh & 0x80) << 9),
                    ((zh << 8) | zl) - ((zh & 0x80) << 9))
                ptime = time_base + (msg_cdiff + index) * inv_freq
                samples.append((
                    round(ptime, 6),
                    round(raw_xyz[x_pos] * x_scale, 6),
                    round(raw_xyz[y_pos] * y_scale, 6),
                    round(raw_xyz[z_pos] * z_scale, 6)))
                last_chip_clock = sequence * SAMPLES_PER_BLOCK + index
        if last_chip_clock is not None:
            self.clock_sync.set_last_chip_clock(last_chip_clock)
        return samples

    def _start_measurements(self):
        if self.query_rate:
            return
        device_id = self.read_reg(REG_LIS2DW_WHO_AM_I_ADDR)
        logging.info("Creality LIS2DW device id: %x", device_id)
        if device_id != LIS2DW_DEV_ID:
            raise self.printer.command_error(
                "Invalid LIS2DW id (got %x vs %x). Check the leveling-board "
                "sensor and software-SPI wiring."
                % (device_id, LIS2DW_DEV_ID))

        self.set_reg(REG_LIS2DW_CTRL_REG6_ADDR, 0x34)
        self.set_reg(REG_LIS2DW_FIFO_CTRL, 0xc0)
        self.set_reg(REG_LIS2DW_CTRL_REG1_ADDR, 0x94)
        with self.lock:
            self.raw_samples = []

        systime = self.printer.get_reactor().monotonic()
        print_time = self.mcu.estimated_print_time(systime) + MIN_MSG_TIME
        reqclock = self.mcu.print_time_to_clock(print_time)
        rest_ticks = self.mcu.seconds_to_clock(4. / self.data_rate)
        self.query_rate = self.data_rate
        self.query_lis2dw_cmd.send(
            [self.oid, reqclock, rest_ticks], reqclock=reqclock)
        self.last_sequence = 0
        self.last_limit_count = self.last_error_count = 0
        self.clock_sync.reset(reqclock, 0)
        self.max_query_duration = 1 << 31
        self._update_clock(minclock=reqclock)
        self.max_query_duration = 1 << 31
        logging.info("Creality LIS2DW starting '%s' measurements", self.name)

    def _finish_measurements(self):
        if not self.query_rate:
            return
        self.query_lis2dw_end_cmd.send([self.oid, 0, 0])
        self.query_rate = 0
        with self.lock:
            self.raw_samples = []
        self.set_reg(REG_LIS2DW_FIFO_CTRL, 0x00)
        logging.info("Creality LIS2DW finished '%s' measurements", self.name)

    def _process_batch(self, eventtime):
        self._update_clock()
        with self.lock:
            raw_samples = self.raw_samples
            self.raw_samples = []
        samples = self._convert_samples(raw_samples)
        if not samples:
            return {}
        return {'data': samples, 'errors': self.last_error_count,
                'overflows': self.last_limit_count}

    def start_internal_client(self):
        helper = adxl345.AccelQueryHelper(self.printer)
        self.batch_bulk.add_client(helper.handle_batch)
        return helper


def load_config(config):
    return CrealityLIS2DW(config)


def load_config_prefix(config):
    return CrealityLIS2DW(config)
