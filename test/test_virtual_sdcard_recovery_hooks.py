import importlib.util
import io
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "klippy" / "extras" / "virtual_sdcard.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "virtual_sdcard_recovery_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeReactor:
    NOW = 0.
    NEVER = 1.e30

    def unregister_timer(self, timer):
        pass

    def pause(self, waketime):
        pass

    def monotonic(self):
        return 10.


class FakePrintStats:
    def __init__(self):
        self.events = []

    def note_start(self):
        self.events.append("start")

    def note_error(self, message):
        self.events.append(("error", message))

    def note_complete(self):
        self.events.append("complete")

    def note_pause(self):
        self.events.append("pause")


class FakeMutex:
    def test(self):
        return False


class FakeGCode:
    error = RuntimeError

    def __init__(self):
        self.responses = []

    def get_mutex(self):
        return FakeMutex()

    def respond_raw(self, message):
        self.responses.append(message)


class FakePrinter:
    def __init__(self, fail_start=False):
        self.fail_start = fail_start
        self.events = []

    def send_event(self, event, *args):
        self.events.append((event, args[1:] if len(args) > 1 else ()))
        if event == "virtual_sdcard:print_start" and self.fail_start:
            raise RuntimeError("restore failed")


def bare_virtual_sd(module, fail_start=False, content=""):
    vsd = module.VirtualSD.__new__(module.VirtualSD)
    vsd.current_file = io.StringIO(content)
    vsd.file_position = 0
    vsd.file_size = len(content.encode())
    vsd.must_pause_work = False
    vsd.cmd_from_sd = False
    vsd.next_file_position = 0
    vsd.work_timer = object()
    vsd.reactor = FakeReactor()
    vsd.print_stats = FakePrintStats()
    vsd.printer = FakePrinter(fail_start)
    vsd.gcode = FakeGCode()
    return vsd


def test_failed_recovery_hook_stops_before_first_gcode_line():
    module = load_module()
    vsd = bare_virtual_sd(module, fail_start=True, content="G1 X10\n")
    result = vsd.work_handler(0.)
    assert result == vsd.reactor.NEVER
    assert vsd.work_timer is None
    assert vsd.file_position == 0
    assert vsd.print_stats.events == ["start", ("error", "restore failed")]
    assert [event for event, unused in vsd.printer.events] == [
        "virtual_sdcard:print_start", "virtual_sdcard:print_end"]


def test_empty_file_emits_start_then_complete_lifecycle():
    module = load_module()
    vsd = bare_virtual_sd(module, content="")
    result = vsd.work_handler(0.)
    assert result == vsd.reactor.NEVER
    assert vsd.print_stats.events == ["start", "complete"]
    assert vsd.gcode.responses == ["Done printing file"]
    assert [event for event, unused in vsd.printer.events] == [
        "virtual_sdcard:print_start", "virtual_sdcard:print_end"]
    assert vsd.printer.events[-1][1] == ("complete",)
