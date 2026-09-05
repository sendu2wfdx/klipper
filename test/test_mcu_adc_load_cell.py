import importlib.util
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).parents[1]
KLIPPY = ROOT / "klippy"
if str(KLIPPY) not in sys.path:
    sys.path.insert(0, str(KLIPPY))

PACKAGE = "extras"
MODULE_PATH = KLIPPY / "extras" / "mcu_adc_load_cell.py"
SPEC = importlib.util.spec_from_file_location(
    PACKAGE + ".mcu_adc_load_cell", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeADC:
    def __init__(self, mcu):
        self.mcu = mcu
        self.sample_args = None
        self.callback = None
        self.trigger_oid = None

    def get_mcu(self):
        return self.mcu

    def setup_adc_sample(self, *args, **kwargs):
        self.sample_args = (args, kwargs)

    def setup_adc_callback(self, callback):
        self.callback = callback

    def setup_trigger_analog(self, oid):
        self.trigger_oid = oid


class FakePins:
    def __init__(self, adc):
        self.adc = adc
        self.calls = []

    def setup_pin(self, pin_type, pin):
        self.calls.append((pin_type, pin))
        return self.adc


class FakeMCU:
    def __init__(self):
        self.callbacks = []
        self.printer = None

    def register_config_callback(self, callback):
        self.callbacks.append(callback)

    def get_constant_float(self, name):
        if name != "ADC_MAX":
            raise KeyError(name)
        return 4095.

    def get_printer(self):
        return self.printer


class FakePrinter:
    command_error = RuntimeError

    def __init__(self, pins):
        self.pins = pins

    def lookup_object(self, name):
        if name == "pins":
            return self.pins
        raise KeyError(name)

    def config_error(self, message):
        return RuntimeError(message)


class FakeConfig:
    def __init__(self, printer):
        self.printer = printer

    def get_printer(self):
        return self.printer

    def get_name(self):
        return "load_cell_probe secondary"

    def getint(self, name, default, **kwargs):
        return {"sample_rate": 400, "adc_sample_count": 4}.get(
            name, default)

    def getfloat(self, name, default, **kwargs):
        return default

    def get(self, name):
        if name == "adc_pin":
            return "nozzle_mcu:PA0"
        raise KeyError(name)


class MCUADCLoadCellTest(unittest.TestCase):
    def make_sensor(self):
        mcu = FakeMCU()
        adc = FakeADC(mcu)
        pins = FakePins(adc)
        printer = FakePrinter(pins)
        mcu.printer = printer
        sensor = MODULE.MCUADLoadCell(FakeConfig(printer))
        return sensor, mcu, adc, pins

    def test_opt_in_sensor_setup_and_trigger_attach(self):
        sensor, mcu, adc, pins = self.make_sensor()
        self.assertEqual(pins.calls, [("adc", "nozzle_mcu:PA0")])
        self.assertAlmostEqual(adc.sample_args[0][0], 1. / 400.)
        self.assertEqual(adc.sample_args[0][2], 4)
        sensor.setup_trigger_analog(17)
        self.assertEqual(adc.trigger_oid, 17)
        self.assertEqual(MODULE.MCU_ADC_SENSOR_TYPE,
                         {"mcu_adc": MODULE.MCUADLoadCell})

    def test_raw_count_reconstruction_and_batch_shape(self):
        sensor, mcu, adc, pins = self.make_sensor()
        for callback in mcu.callbacks:
            callback()
        self.assertEqual(sensor.get_range(), (0, 16380))
        sensor._start_measurements()
        adc.callback([(1.25, .25), (1.2525, .75)])
        batch = sensor._process_batch(2.)
        self.assertEqual(batch["data"], [
            (1.25, 4095, .25),
            (1.2525, 12285, .75),
        ])
        self.assertEqual(batch["errors"], 0)
        self.assertEqual(batch["overflows"], 0)
        self.assertIsNone(sensor._process_batch(3.))

    def test_status_identifies_coarse_adc_mode(self):
        sensor, mcu, adc, pins = self.make_sensor()
        status = sensor.get_status(0.)
        self.assertEqual(status["sample_rate"], 400)
        self.assertEqual(status["adc_sample_count"], 4)

    def test_mcu_adc_batch_decoder_consumes_every_u16(self):
        source = (ROOT / "klippy" / "mcu.py").read_text(encoding="utf-8")
        self.assertIn("range(0, len(raw_values), 2)", source)
        self.assertIn("analog_in_attach_trigger_analog", source)


if __name__ == "__main__":
    unittest.main()
