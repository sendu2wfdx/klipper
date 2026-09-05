# Explicit TMC communication and motor wiring diagnostic for Creality F009

class TMCLineCheck:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.default_register = config.get("register", "DRV_STATUS")
        self.default_mask = config.getint("error_mask", 0xff, minval=0)
        self.default_expected = config.getint("expected", 0, minval=0)
        self.default_distance = config.getfloat("move_distance", .5)
        self.default_velocity = config.getfloat("move_velocity", 10., above=0.)
        self.gcode = self.printer.lookup_object("gcode")
        self.gcode.register_command("READ_TMC_REGISTER",
                                    self.cmd_READ_TMC_REGISTER)
        self.gcode.register_command("CHECK_MOTOR_LINE",
                                    self.cmd_CHECK_MOTOR_LINE)

    def _lookup_driver(self, stepper):
        for driver in ("tmc2209", "tmc2208"):
            obj = self.printer.lookup_object("%s stepper_%s"
                                             % (driver, stepper), None)
            if obj is not None:
                return driver, obj
        raise self.printer.command_error(
            "No supported TMC driver configured for stepper_%s" % stepper)

    def _read_register(self, stepper, register):
        driver, obj = self._lookup_driver(stepper)
        try:
            value = obj.mcu_tmc.get_register(register)
        except KeyError:
            raise self.printer.command_error(
                "Unknown %s register '%s'" % (driver, register))
        return driver, value

    def cmd_READ_TMC_REGISTER(self, gcmd):
        stepper = gcmd.get("STEPPER", "z").lower()
        register = gcmd.get("REGISTER", self.default_register).upper()
        driver, value = self._read_register(stepper, register)
        gcmd.respond_info("%s stepper_%s %s=0x%08x"
                          % (driver, stepper, register, value))

    def cmd_CHECK_MOTOR_LINE(self, gcmd):
        stepper = gcmd.get("STEPPER", "z").lower()
        register = gcmd.get("REGISTER", self.default_register).upper()
        mask = gcmd.get_int("ERROR_MASK", self.default_mask, minval=0)
        expected = gcmd.get_int("EXPECTED", self.default_expected, minval=0)
        distance = gcmd.get_float("DISTANCE", self.default_distance)
        velocity = gcmd.get_float("VELOCITY", self.default_velocity, above=0.)
        if not distance:
            raise gcmd.error("DISTANCE must not be zero")
        toolhead = self.printer.lookup_object("toolhead")
        toolhead.wait_moves()
        moved = False
        try:
            self.gcode.run_script_from_command(
                "FORCE_MOVE STEPPER=stepper_%s DISTANCE=%.6f VELOCITY=%.6f"
                % (stepper, distance, velocity))
            toolhead.wait_moves()
            moved = True
            driver, value = self._read_register(stepper, register)
        finally:
            if moved:
                self.gcode.run_script_from_command(
                    "FORCE_MOVE STEPPER=stepper_%s DISTANCE=%.6f VELOCITY=%.6f"
                    % (stepper, -distance, velocity))
                toolhead.wait_moves()
            self.gcode.run_script_from_command("M84")
        actual = value & mask
        if actual != expected:
            raise gcmd.error(
                "stepper_%s wiring check failed: %s=0x%08x,"
                " masked=0x%x expected=0x%x"
                % (stepper, register, value, actual, expected))
        gcmd.respond_info("stepper_%s wiring check passed (%s %s=0x%08x)"
                          % (stepper, driver, register, value))

def load_config(config):
    return TMCLineCheck(config)

