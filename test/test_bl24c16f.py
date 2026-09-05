import importlib.util
from pathlib import Path
import struct
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "klippy" / "extras" / "bl24c16f.py"


class FakeI2C:
    def __init__(self, address, memory):
        self.address = address
        self.memory = memory
        self.writes = []

    def get_mcu(self):
        return object()

    def i2c_read(self, register, read_len):
        offset = register[0]
        base = (self.address - 0x50) * 256 + offset
        return {"response": self.memory[base:base + read_len]}

    def i2c_write(self, data):
        self.writes.append(list(data))
        offset = data[0]
        base = (self.address - 0x50) * 256 + offset
        self.memory[base:base + len(data) - 1] = bytes(data[1:])


class FakeGCode:
    def __init__(self):
        self.commands = []

    def register_mux_command(self, command, key, value, callback, desc=None):
        self.commands.append((command, key, value, callback, desc))


class FakePrinter:
    def __init__(self):
        self.gcode = FakeGCode()
        self.objects = {}
        self.handlers = {}
        self.reactor = FakeReactor()

    def lookup_object(self, name):
        assert name == "gcode"
        return self.gcode

    def get_reactor(self):
        return self.reactor

    def add_object(self, name, value):
        self.objects[name] = value

    def register_event_handler(self, event, callback):
        self.handlers[event] = callback


class FakeConfig:
    def __init__(self, printer):
        self.printer = printer

    def get_printer(self):
        return self.printer

    def get_name(self):
        return "bl24c16f"

    def has_section(self, name):
        return name == "bl24c16f"

    def getfloat(self, name, default, minval=None, maxval=None):
        assert name == "write_cycle_time"
        return default


class FakeReactor:
    def __init__(self):
        self.pauses = []

    def monotonic(self):
        return 10.

    def pause(self, waketime):
        self.pauses.append(waketime)


def load_module():
    package = types.ModuleType("bl24c16f_testpkg")
    package.__path__ = []
    sys.modules["bl24c16f_testpkg"] = package
    bus_module = types.ModuleType("bl24c16f_testpkg.bus")
    memory = bytearray([0xff] * 2048)
    devices = []

    def from_config(config, default_addr=None, default_speed=100000):
        assert default_speed == 400000
        device = FakeI2C(default_addr, memory)
        devices.append(device)
        return device

    bus_module.MCU_I2C_from_config = from_config
    sys.modules["bl24c16f_testpkg.bus"] = bus_module
    spec = importlib.util.spec_from_file_location(
        "bl24c16f_testpkg.bl24c16f", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, memory, devices


def create_chip():
    module, memory, devices = load_module()
    printer = FakePrinter()
    chip = module.BL24C16F(FakeConfig(printer))
    return module, printer, chip, memory, devices


def test_v57_command_surface_and_all_eight_addresses():
    _, printer, _, _, devices = create_chip()
    assert [device.address for device in devices] == list(range(0x50, 0x58))
    commands = [item[0] for item in printer.gcode.commands]
    expected = {
        "EEPROM_DEBUG_READ", "EEPROM_DEBUG_WRITE_BYTE",
        "EEPROM_DEBUG_WRITE_INT", "EEPROM_DEBUG_WRITE_FLOAT",
        "EEPROM_READ", "EEPROM_WRITE_BYTE", "EEPROM_WRITE_INT",
        "EEPROM_WRITE_FLOAT", "EEPROM_IS_FIRST_USED", "EEPROM_POS",
        "EEPROM_PRINTER_INFO",
    }
    assert set(commands) == expected
    assert all(commands.count(name) == 2 for name in expected)


def test_reads_across_256_byte_bank_boundary():
    _, _, chip, memory, _ = create_chip()
    memory[254:260] = b"abcdef"
    assert chip.read_reg(254, 6) == b"abcdef"


def test_writes_split_at_page_and_bank_boundaries_without_mutating_input():
    _, _, chip, memory, devices = create_chip()
    values = list(range(20))
    chip.write_reg(250, values)
    assert values == list(range(20))
    assert memory[250:270] == bytes(values)
    assert devices[0].writes == [[250] + list(range(6))]
    assert devices[1].writes == [[0] + list(range(6, 20))]
    chip.write_reg(30, [100, 101, 102, 103])
    assert devices[0].writes[-2:] == [[30, 100, 101], [32, 102, 103]]


def test_power_loss_helper_layout_matches_v57_records():
    _, _, chip, memory, _ = create_chip()
    memory[0] = 7
    memory[1] = 255
    memory[56:60] = (123456).to_bytes(4, "little")
    memory[60:64] = struct.pack("<f", 42.25)
    assert chip.checkEepromFirstEnable() is True
    assert chip.eepromReadHeader() == 7
    assert chip.eepromReadBody(7) == {
        "file_position": 123456,
        "base_position_e": pytest.approx(42.25),
    }
    chip.setEepromDisable()
    assert memory[1] == 255


def test_range_checks_protect_the_2kib_device():
    _, _, chip, _, _ = create_chip()
    with pytest.raises(ValueError):
        chip.read_reg(2047, 2)
    with pytest.raises(ValueError):
        chip.write_reg(-1, 0)
