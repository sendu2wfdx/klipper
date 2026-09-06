# Filtered MCU-local GPIO input to output interlock

class IORemap:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name().split()[-1]
        self.legacy = config.get_name().strip() == "io_remap"
        self.enabled = config.getboolean("enable", not self.legacy)
        legacy_filter = config.get("filterNum", None)
        if legacy_filter is not None and config.get("filter_count", None) is None:
            legacy_filter = (int(legacy_filter, 0)
                             if isinstance(legacy_filter, str)
                             else int(legacy_filter))
            self.filter_count = legacy_filter if legacy_filter else 5
        else:
            self.filter_count = config.getint(
                "filter_count", 5, minval=1, maxval=255)
        self.legacy_filter_semantics = config.getboolean(
            "legacy_filter_semantics", self.legacy)
        self.period = config.getfloat(
            "period", .000010 if self.legacy else .000050, above=0.)
        self.legacy_period_ticks = config.getint(
            "periodTicks", 0, minval=0) if self.legacy else 0
        legacy_default = config.get("remap_def", None)
        if (legacy_default is not None
                and config.get("default_value", None) is None):
            self.default_value = (int(legacy_default, 0)
                                  if isinstance(legacy_default, str)
                                  else int(legacy_default))
            if self.default_value not in (0, 1):
                raise config.error("remap_def must be 0 or 1")
        else:
            self.default_value = config.getint(
                "default_value", 1, minval=0, maxval=1)
        self.home_x = config.getboolean("home_x", self.legacy)
        ppins = self.printer.lookup_object("pins")
        src_pin = config.get("src_pin")
        remap_pin = config.get("remap_pin")
        # The original unnamed [io_remap] module hard-coded nozzle_mcu even
        # though the V57 config used unqualified PB0/PA15 pin names.
        if self.legacy:
            if ":" not in src_pin:
                src_pin = "nozzle_mcu:" + src_pin
            if ":" not in remap_pin:
                remap_pin = "nozzle_mcu:" + remap_pin
            src_pullup = config.getint("src_pullup", 0,
                                       minval=0, maxval=1)
            if src_pullup:
                src_pin = "^" + src_pin
        src = ppins.lookup_pin(src_pin, can_invert=False,
                               can_pullup=True)
        dst = ppins.lookup_pin(remap_pin, can_invert=False)
        if src["chip"] is not dst["chip"]:
            raise config.error("io_remap pins must be on the same MCU")
        self.mcu = src["chip"]
        self.oid = self.mcu.create_oid()
        self.src_pin = src["pin"]
        self.src_pullup = src["pullup"]
        self.dst_pin = dst["pin"]
        self.mcu.register_config_callback(self._build_config)
        self.gcode = self.printer.lookup_object("gcode")
        self.gcode.register_mux_command("SET_IO_REMAP", "REMAP",
                                        self.name,
                                        self.cmd_SET_IO_REMAP)
        if self.legacy:
            self.gcode.register_command("SET_IOREMAP", self.cmd_SET_IO_REMAP)
        self._homing_restore_enabled = None
        if self.home_x:
            self.printer.register_event_handler(
                "homing:homing_move_begin", self._handle_homing_move_begin)
            self.printer.register_event_handler(
                "homing:homing_move_end", self._handle_homing_move_end)
            self.printer.register_event_handler(
                "gcode:command_error", self._handle_command_error)

    def _get_period_ticks(self):
        if self.legacy_period_ticks:
            return self.legacy_period_ticks
        return self.mcu.seconds_to_clock(self.period)

    def _get_effective_filter_count(self):
        if self.legacy_filter_semantics and self.filter_count < 255:
            # V57 compares the pre-increment count, so N trips after N+1
            # consecutive active samples. The public MCU implements N samples.
            return self.filter_count + 1
        return self.filter_count

    def _build_config(self):
        self.mcu.add_config_cmd(
            "config_io_remap oid=%d src_pin=%s src_pullup=%d"
            " remap_pin=%s default_value=%d"
            % (self.oid, self.src_pin, self.src_pullup, self.dst_pin,
               self.default_value))
        self.mcu.add_config_cmd(
            "set_io_remap oid=%d enabled=%d filter_count=%d period_ticks=%d"
            % (self.oid, self.enabled, self._get_effective_filter_count(),
               self._get_period_ticks()),
            is_init=True)

    def cmd_SET_IO_REMAP(self, gcmd):
        parameter = "S" if self.legacy else "ENABLE"
        self._set_enabled(gcmd.get_int(parameter, minval=0, maxval=1))

    def _set_enabled(self, enabled):
        cmd = self.mcu.lookup_command(
            "set_io_remap oid=%c enabled=%c filter_count=%c period_ticks=%u")
        cmd.send([self.oid, bool(enabled), self._get_effective_filter_count(),
                  self._get_period_ticks()])
        self.enabled = bool(enabled)

    @staticmethod
    def _is_x_homing_move(homing_move):
        return any(stepper.get_name() == "stepper_x"
                   for endstop in homing_move.get_mcu_endstops()
                   for stepper in endstop.get_steppers())

    def _handle_homing_move_begin(self, homing_move):
        if not self._is_x_homing_move(homing_move):
            return
        self._homing_restore_enabled = self.enabled
        if not self.enabled:
            self._set_enabled(True)

    def _handle_homing_move_end(self, homing_move):
        if (self._homing_restore_enabled is None
                or not self._is_x_homing_move(homing_move)):
            return
        restore = self._homing_restore_enabled
        self._homing_restore_enabled = None
        if self.enabled != restore:
            self._set_enabled(restore)

    def _handle_command_error(self):
        # Homing may fail before homing_move_end (for example in home_start).
        if self._homing_restore_enabled is None:
            return
        restore = self._homing_restore_enabled
        self._homing_restore_enabled = None
        if self.enabled != restore:
            self._set_enabled(restore)

    def get_status(self, eventtime):
        return {
            "enabled": bool(self.enabled),
            "filter_count": self.filter_count,
            "period": self.period,
            "legacy_abi": self.legacy,
        }

def load_config(config):
    return IORemap(config)

def load_config_prefix(config):
    return IORemap(config)
