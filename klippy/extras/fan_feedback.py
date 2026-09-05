# Standalone fan tachometer inputs using upstream pulse_counter support
from . import pulse_counter

class FanFeedback:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        sample_time = config.getfloat("sample_time", 1., above=0.)
        poll_time = config.getfloat("poll_interval", .0015, above=0.)
        self.print_delay_time = config.getfloat(
            "print_delay_time", 5., above=0.)
        self.current_delay_time = config.getfloat(
            "current_delay_time", 2., above=0.)
        self.counters = []
        for index in range(5):
            pin = config.get("fan%d_pin" % index, None)
            if pin is None:
                continue
            ppr = config.getint("fan%d_ppr" % index, 2, minval=1)
            counter = pulse_counter.FrequencyCounter(
                self.printer, pin, sample_time, poll_time)
            self.counters.append((index, ppr, counter))
        if not self.counters:
            raise config.error("fan_feedback requires at least one fanN_pin")
        self.gcode = self.printer.lookup_object("gcode")
        self.print_stats = self.printer.load_object(config, "print_stats")
        # Preserve the V57 host-visible status shape while the actual pulse
        # acquisition uses upstream FrequencyCounter objects.
        self.cx_fan_status = {
            "fan%d_speed" % index: 0. for index in range(5)
        }
        self.gcode.register_command("QUERY_FAN_FEEDBACK",
                                    self.cmd_QUERY_FAN_FEEDBACK)
        self.gcode.register_command("QUERY_FAN_CHECK",
                                    self.cmd_QUERY_FAN_CHECK)
        webhooks = self.printer.lookup_object("webhooks")
        webhooks.register_endpoint("get_cx_fan_status",
                                   self._get_cx_fan_status)
        self.printer.register_event_handler("klippy:ready",
                                            self._handle_ready)

    def _read_counters(self):
        status = {"fan%d_speed" % index: 0. for index in range(5)}
        for index, ppr, counter in self.counters:
            status["fan%d_speed" % index] = (
                counter.get_frequency() * 60. / ppr)
        self.cx_fan_status = status
        return status

    def _handle_ready(self):
        self.reactor.register_timer(
            self._status_update_event, self.reactor.monotonic() + 1.)

    def _status_update_event(self, eventtime):
        self._read_counters()
        state = self.print_stats.get_status(eventtime).get("state")
        delay = (self.print_delay_time if state == "printing"
                 else self.current_delay_time)
        return eventtime + delay

    def _get_cx_fan_status(self, web_request=None):
        if web_request is not None:
            web_request.send(self.cx_fan_status)
            return
        return self.cx_fan_status

    def get_status(self, eventtime):
        return self.cx_fan_status

    def cmd_QUERY_FAN_FEEDBACK(self, gcmd):
        status = self._read_counters()
        gcmd.respond_info(" ".join("%s=%.1f" % item
                                   for item in sorted(status.items())))

    def cmd_QUERY_FAN_CHECK(self, gcmd):
        self.cmd_QUERY_FAN_FEEDBACK(gcmd)

def load_config(config):
    return FanFeedback(config)
