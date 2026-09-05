import importlib.util
from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "klippy" / "extras" / "fan_feedback.py"


class FakeCounter:
    frequencies = {"nozzle_mcu:PA8": 100., "nozzle_mcu:PB9": 50.}

    def __init__(self, printer, pin, sample_time, poll_time):
        self.pin = pin
        self.sample_time = sample_time
        self.poll_time = poll_time

    def get_frequency(self):
        return self.frequencies[self.pin]


package = types.ModuleType("fan_feedback_testpkg")
package.__path__ = []
sys.modules["fan_feedback_testpkg"] = package
counter_module = types.ModuleType("fan_feedback_testpkg.pulse_counter")
counter_module.FrequencyCounter = FakeCounter
sys.modules["fan_feedback_testpkg.pulse_counter"] = counter_module
spec = importlib.util.spec_from_file_location(
    "fan_feedback_testpkg.fan_feedback", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakeReactor:
    def __init__(self):
        self.timer = None
        self.waketime = None

    def monotonic(self):
        return 10.

    def register_timer(self, callback, waketime):
        self.timer = callback
        self.waketime = waketime


class FakeGCode:
    def __init__(self):
        self.commands = {}

    def register_command(self, name, callback):
        self.commands[name] = callback


class FakeWebhooks:
    def __init__(self):
        self.endpoints = {}

    def register_endpoint(self, name, callback):
        self.endpoints[name] = callback


class FakePrintStats:
    def __init__(self):
        self.state = "standby"

    def get_status(self, eventtime):
        return {"state": self.state}


class FakePrinter:
    def __init__(self):
        self.reactor = FakeReactor()
        self.gcode = FakeGCode()
        self.webhooks = FakeWebhooks()
        self.print_stats = FakePrintStats()
        self.handlers = {}

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name):
        return {"gcode": self.gcode, "webhooks": self.webhooks}[name]

    def load_object(self, config, name):
        assert name == "print_stats"
        return self.print_stats

    def register_event_handler(self, name, callback):
        self.handlers[name] = callback


class FakeConfig:
    def __init__(self):
        self.printer = FakePrinter()
        self.values = {
            "fan0_pin": "nozzle_mcu:PA8",
            "fan1_pin": "nozzle_mcu:PB9",
            "fan0_ppr": 2,
            "fan1_ppr": 2,
        }

    def get_printer(self):
        return self.printer

    def get(self, name, default=None):
        return self.values.get(name, default)

    def getint(self, name, default, **kwargs):
        return self.values.get(name, default)

    def getfloat(self, name, default, **kwargs):
        return self.values.get(name, default)

    def error(self, message):
        return RuntimeError(message)


class FakeGCmd:
    def __init__(self):
        self.responses = []

    def respond_info(self, message):
        self.responses.append(message)


class FakeWebRequest:
    def __init__(self):
        self.response = None

    def send(self, response):
        self.response = response


def make_feedback():
    config = FakeConfig()
    return module.FanFeedback(config), config.printer


def test_v57_status_command_and_webhook_surface_is_preserved():
    feedback, printer = make_feedback()
    assert set(printer.gcode.commands) == {
        "QUERY_FAN_FEEDBACK", "QUERY_FAN_CHECK"
    }
    assert "get_cx_fan_status" in printer.webhooks.endpoints
    assert set(feedback.get_status(0.)) == {
        "fan0_speed", "fan1_speed", "fan2_speed", "fan3_speed",
        "fan4_speed"
    }


def test_upstream_counters_feed_factory_shaped_rpm_status():
    feedback, printer = make_feedback()
    status = feedback._read_counters()
    assert status == {
        "fan0_speed": 3000., "fan1_speed": 1500.,
        "fan2_speed": 0., "fan3_speed": 0., "fan4_speed": 0.,
    }
    assert printer.webhooks.endpoints["get_cx_fan_status"]() == status
    request = FakeWebRequest()
    printer.webhooks.endpoints["get_cx_fan_status"](request)
    assert request.response == status


def test_periodic_delay_matches_v57_printing_and_idle_intervals():
    feedback, printer = make_feedback()
    printer.handlers["klippy:ready"]()
    assert printer.reactor.waketime == 11.
    assert printer.reactor.timer(20.) == 22.
    printer.print_stats.state = "printing"
    assert printer.reactor.timer(30.) == 35.


def test_both_query_commands_report_live_values():
    feedback, printer = make_feedback()
    for name in ("QUERY_FAN_CHECK", "QUERY_FAN_FEEDBACK"):
        gcmd = FakeGCmd()
        printer.gcode.commands[name](gcmd)
        assert "fan0_speed=3000.0" in gcmd.responses[0]
        assert "fan4_speed=0.0" in gcmd.responses[0]
