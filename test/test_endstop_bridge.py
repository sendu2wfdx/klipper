"""Host-side ownership, wire checks and native trigger error propagation."""
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / 'klippy'))
from extras.endstop_bridge import (EndstopBridge, HardwareAnalogTrigger,
                                     LocalEndstop)
from extras.endstop_bridge_input import EndstopBridgeInput
from extras.trigger_analog import MCU_trigger_analog
import mcu


class BridgeTests(unittest.TestCase):
    def test_public_chip_and_command_names(self):
        config = Mock()
        config.get_name.return_value = 'endstop_bridge sync'
        config.getfloat.return_value = .000050
        pins, gcode, sender = Mock(), Mock(), Mock()
        config.get_printer.return_value.lookup_object.side_effect = {
            'pins': pins, 'gcode': gcode}.__getitem__
        pins.lookup_pin.side_effect = [
            {'chip': sender, 'pin': 'PA15', 'invert': 1},
            {'chip': Mock(), 'pin': 'PC7', 'invert': 1, 'pullup': 1}]
        bridge = EndstopBridge(config)
        pins.register_chip.assert_called_once_with('endstop_bridge_sync', bridge)
        gcode.register_mux_command.assert_called_once_with(
            'TEST_ENDSTOP_BRIDGE', 'BRIDGE', 'sync',
            bridge.cmd_TEST_ENDSTOP_BRIDGE)

    def setUp(self):
        self.bridge = bridge = EndstopBridge.__new__(EndstopBridge)
        bridge.owner = None
        bridge.name = 'sync'
        bridge.oid = 3
        bridge.poll_interval = .000050
        bridge.printer = SimpleNamespace(command_error=ValueError,
                                      register_event_handler=Mock())
        bridge.mcu = Mock()
        bridge.mcu.is_fileoutput.return_value = False
        bridge.set_cmd = Mock()
        self.receiver = Mock()
        self.receiver.query_endstop.side_effect = [1, 0]

    def test_wire_test_and_exclusive_owner(self):
        owner = object()
        self.bridge.prepare(owner, self.receiver)
        self.assertEqual(self.bridge.set_cmd.send.call_args_list,
                         [call([3, 2, 0, 1]), call([3, 2, 0, 0]),
                          call([3, 0, 0, 0])])
        with self.assertRaisesRegex(ValueError, 'already in use'):
            self.bridge.prepare(object(), self.receiver)
        self.bridge.release(object())
        self.assertIs(self.bridge.owner, owner)
        self.bridge.release(owner)
        self.assertIsNone(self.bridge.owner)

    def test_both_stuck_levels_rejected_and_released(self):
        for values in ([0, 0], [1, 1]):
            self.receiver.query_endstop.side_effect = values
            with self.assertRaisesRegex(ValueError, 'wire test failed'):
                self.bridge.prepare(object(), self.receiver)
            self.assertIsNone(self.bridge.owner)
            self.assertEqual(self.bridge.set_cmd.send.call_args.args[0],
                             [3, 0, 0, 0])

    def make_analog(self):
        self.bridge.new_receiver = Mock(return_value=self.receiver)
        config = Mock()
        config.get_printer.return_value = self.bridge.printer
        config.get_name.return_value = 'load_cell_probe'
        config.error.side_effect = ValueError
        source = Mock()
        source.get_mcu.return_value = self.bridge.mcu
        analog = HardwareAnalogTrigger(config, self.bridge, source)
        return analog, source

    def test_analog_steppers_attach_only_to_receiver(self):
        analog, source = self.make_analog()
        motor = object()
        analog.get_dispatch().add_stepper(motor)
        self.receiver.add_stepper.assert_called_once_with(motor)
        source.get_dispatch.assert_not_called()
        analog.prepare_homing()
        self.assertIs(analog.home_start(10., 0., 0, 0.),
                      self.receiver.home_start.return_value)
        self.receiver.home_start.assert_called_once_with(
            10., .000015, 4, .000050, triggered=True)
        self.receiver.home_wait.return_value = 10.01
        source.home_wait.return_value = 10.009
        self.assertEqual(analog.home_wait(11.), 10.01)
        self.assertIsNone(self.bridge.owner)

    def test_analog_errors_cannot_be_reported_as_contact(self):
        analog, source = self.make_analog()
        analog.prepare_homing()
        analog.home_start(10., 0., 0, 0.)
        self.receiver.home_wait.return_value = 10.01
        source.home_wait.side_effect = ValueError('RAW_RANGE')
        with self.assertRaisesRegex(ValueError, 'RAW_RANGE'):
            analog.home_wait(11.)
        source.abort_home.assert_called_once()
        self.assertIsNone(self.bridge.owner)

    def test_missing_wire_trigger_is_error(self):
        analog, source = self.make_analog()
        analog.prepare_homing()
        self.receiver.home_wait.return_value = 0.
        source.home_wait.return_value = 10.01
        with self.assertRaisesRegex(ValueError, 'disagree'):
            analog.home_wait(11.)
        self.assertIsNone(self.bridge.owner)

    def test_start_failure_aborts_both_ends(self):
        analog, source = self.make_analog()
        analog.prepare_homing()
        source.home_start.side_effect = ValueError('start failed')
        with self.assertRaisesRegex(ValueError, 'start failed'):
            analog.home_start(10., 0., 0, 0.)
        self.receiver.abort_home.assert_called_once()
        source.abort_home.assert_called_once()
        self.assertIsNone(self.bridge.owner)

    def test_arm_uses_sensor_command_queue(self):
        analog, source = self.make_analog()
        self.bridge.owner = analog
        dispatch = Mock()
        analog._arm_wire(dispatch)
        self.bridge.mcu.lookup_command.assert_called_once_with(
            'arm_endstop_bridge oid=%c trsync_oid=%c',
            cq=dispatch.get_command_queue())
        self.bridge.mcu.lookup_command.return_value.send.assert_called_once_with(
            [3, dispatch.get_oid()])

    def test_query_digital_does_not_switch_bus(self):
        route = EndstopBridgeInput.__new__(EndstopBridgeInput)
        route.bridge, route.oid, route.query_cmd = self.bridge, 7, Mock()
        route.query_cmd.send.return_value = {'value': 1}
        self.assertEqual(route.query_endstop(10.), 1)
        self.bridge.set_cmd.send.assert_not_called()

    def test_receiver_rejects_motor_on_other_mcu(self):
        receiver = LocalEndstop.__new__(LocalEndstop)
        receiver._mcu = Mock()
        receiver._mcu.get_printer.return_value.config_error = ValueError
        with self.assertRaisesRegex(ValueError, 'share an MCU'):
            receiver.add_stepper(Mock())

    def test_native_timeouts_unchanged(self):
        self.assertEqual(mcu.TRSYNC_TIMEOUT, .025)
        self.assertEqual(mcu.TRSYNC_SINGLE_MCU_TIMEOUT, .250)

    def test_native_analog_arms_wire_between_dispatch_and_sampling(self):
        source = MCU_trigger_analog.__new__(MCU_trigger_analog)
        events = []
        source._oid = 9
        source._mcu = Mock()
        source._sensor = Mock()
        source._sensor.get_samples_per_second.return_value = 640.
        source._reset_filter = Mock()
        source._dispatch = Mock()
        source._dispatch.start.side_effect = lambda t: events.append('start')
        source.setup_trigger_callback(lambda d: events.append('wire'))
        source._home_cmd = Mock()
        source._home_cmd.send.side_effect = lambda *a, **kw: events.append('adc')
        source.home_start(1., 0., 0, 0.)
        self.assertEqual(events, ['start', 'wire', 'adc'])
        source._clear_home = Mock()
        source.abort_home()
        source.abort_home()
        source._dispatch.stop.assert_called_once()
        source._clear_home.assert_called_once()

    def test_receiver_abort_is_idempotent(self):
        receiver = mcu.MCU_endstop.__new__(mcu.MCU_endstop)
        receiver._homing = True
        receiver._oid = 4
        receiver._home_cmd = Mock()
        receiver._dispatch = Mock()
        receiver.abort_home()
        receiver.abort_home()
        receiver._home_cmd.send.assert_called_once_with([4, 0, 0, 0, 0, 0, 0, 0])
        receiver._dispatch.stop.assert_called_once()

    def test_homing_event_selects_only_its_own_source(self):
        route = EndstopBridgeInput.__new__(EndstopBridgeInput)
        route.bridge, route.oid, route.receiver = self.bridge, 7, self.receiver
        route.name = 'endstop_bridge_input x'
        move = Mock()
        move.get_mcu_endstops.return_value = [object()]
        route._homing_begin(move)
        self.bridge.set_cmd.send.assert_not_called()
        move.get_mcu_endstops.return_value = [route]
        route._homing_begin(move)
        self.assertIs(self.bridge.owner, route)
        self.bridge.set_cmd.send.assert_called_with([3, 1, 7, 0])
        self.bridge.release(route)


if __name__ == '__main__':
    unittest.main()
