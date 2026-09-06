import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / 'klippy'))
from extras.cs1237 import (CS1237, SAMPLE_ERROR_CONFIG,
                           SAMPLE_ERROR_DESYNC, SAMPLE_ERROR_LONG_READ)


def sensor():
    obj = CS1237.__new__(CS1237)
    obj.last_error_count = 0
    obj.consecutive_fails = 0
    obj.name = 'test'
    obj.sps = 640
    return obj


def test_signed_counts_preserved_for_load_cell_calibration():
    obj = sensor()
    samples = [(1., -8388608), (2., 0), (3., 8388607)]
    obj._convert_samples(samples)
    assert [s[1] for s in samples] == [-8388608, 0, 8388607]
    assert samples[0][2] == -1.
    assert samples[1][2] == 0.
    assert obj.get_range() == (-8388608, 8388607)


@pytest.mark.parametrize('error', [SAMPLE_ERROR_CONFIG, SAMPLE_ERROR_DESYNC,
                                  SAMPLE_ERROR_LONG_READ])
def test_error_and_overflow_remain_visible_after_automatic_restart(error):
    obj = sensor()
    obj.printer = SimpleNamespace(is_shutdown=lambda: False)
    obj.mcu = SimpleNamespace(seconds_to_clock=lambda s: int(s*120000000))
    calls = []
    obj.query_cmd = SimpleNamespace(send=lambda v: calls.append(v),
                                    send_wait_ack=lambda v: calls.append(v))
    obj.oid = 7
    state = {'overflow': 0}

    def pull():
        state['overflow'] = 2
        return [(1., 100), (2., error), (3., error)]

    obj.ffreader = SimpleNamespace(
        get_last_overflows=lambda: state['overflow'], pull_samples=pull,
        note_end=lambda: None,
        note_start=lambda: state.update(overflow=0))
    batch = obj._process_batch(0.)
    assert batch['data'] == [(1., 100, round(100./(1 << 23), 9))]
    assert batch['errors'] == 1
    assert batch['overflows'] == 2
    assert obj.last_error_count == 1
    assert calls == [[7, 0], [7, 18750]]


def test_trigger_attachment_uses_public_analog_trigger_interface():
    obj = sensor()
    commands = []
    obj.oid = 7
    obj.mcu = SimpleNamespace(
        add_config_cmd=lambda command, **kw: commands.append((command, kw)))
    obj.setup_trigger_analog(12)
    assert commands == [('cs1237_attach_trigger_analog oid=7'
                         ' trigger_analog_oid=12', {'is_init': True})]
