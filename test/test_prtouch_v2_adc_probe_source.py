import pathlib


ROOT = pathlib.Path(__file__).parents[1]
PROBE = (ROOT / "scripts" / "gd32_prtouch_v2_adc_probe_windows.py").read_text(
    encoding="utf-8")


def test_v2_adc_probe_never_starts_step_generation():
    assert "start_step_prtouch oid=" not in PROBE
    assert "config_step_prtouch oid=0 step_cnt=0" in PROBE


def test_v2_adc_probe_covers_fixed_trigger_and_manual_reads():
    assert "read_pres_prtouch oid=1 acq_ms=1" in PROBE
    assert "start_pres_prtouch oid=1" in PROBE
    assert "manual_get_pres oid=1 index=0" in PROBE
    assert "result_run_pres_prtouch" in PROBE
    assert "range(0, 32, 2)" in PROBE
