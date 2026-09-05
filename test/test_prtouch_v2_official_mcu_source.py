import hashlib
import pathlib


ROOT = pathlib.Path(__file__).parents[1]
SOURCE = ROOT / "src" / "prtouch_v2.c"
EXPECTED_SHA256 = \
    "04e2fa4a50f80461bd97cf802893295a4ef0862b126e40e6c9e349769e3570d4"


def normalized_bytes(path):
    return path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def test_prtouch_v2_mcu_source_is_pinned_official_file():
    assert hashlib.sha256(normalized_bytes(SOURCE)).hexdigest() == \
        EXPECTED_SHA256


def test_prtouch_v2_mcu_source_exposes_full_host_protocol():
    source = SOURCE.read_text(encoding="utf-8")
    for command in (
            "config_step_prtouch", "add_step_prtouch",
            "read_swap_prtouch", "start_step_prtouch",
            "manual_get_steps", "config_pres_prtouch",
            "add_pres_prtouch", "write_swap_prtouch",
            "read_pres_prtouch", "deal_avgs_prtouch",
            "start_pres_prtouch", "manual_get_pres"):
        assert '"%s ' % command in source or '"%s"' % command in source
    assert "#define PR_VERSION (307)" in source
    assert "gpio_adc_setup" in source
    assert "gpio_adc_sample" in source
    assert "gpio_adc_read" in source


def test_prtouch_v2_and_v3_are_mutually_exclusive_build_options():
    kconfig = (ROOT / "src" / "Kconfig").read_text(encoding="utf-8")
    makefile = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
    choice = kconfig[kconfig.index(
        'prompt "Archived Creality PRTouch MCU protocol"'):]
    choice = choice[:choice.index("endchoice")]
    assert "config WANT_PRTOUCH_V2_COMPAT" in choice
    assert "config WANT_PRTOUCH_V3_COMPAT" in choice
    assert "CONFIG_WANT_PRTOUCH_V2_COMPAT" in makefile
    assert "CONFIG_WANT_PRTOUCH_V3_COMPAT" in makefile
    assert "prtouch_v2_task_shim.c" not in makefile


def test_factory_idle_dispatch_does_not_modify_official_source():
    source = SOURCE.read_text(encoding="utf-8")
    scheduler = (ROOT / "src" / "sched.c").read_text(encoding="utf-8")
    assert "void prtouch_task(void)" in source
    assert "DECL_TASK(prtouch_task)" not in source
    assert "CONFIG_WANT_PRTOUCH_V2_COMPAT || " \
           "CONFIG_WANT_PRTOUCH_V3_COMPAT" in scheduler
    assert "prtouch_task();" in scheduler


def test_prtouch_v2_adc_cct6_build_target_is_explicit():
    config = (ROOT / "config" /
              "gd32f303cc_test_usb_pa11_pa12_noboot_dbuf_prtouch_v2_adc.config"
              ).read_text(encoding="utf-8")
    matrix = (ROOT / "scripts" / "build-gd32-matrix.sh").read_text(
        encoding="utf-8")
    assert "CONFIG_WANT_PRTOUCH_V2_COMPAT=y" in config
    assert "# CONFIG_WANT_PRTOUCH_V3_COMPAT is not set" in config
    assert "CONFIG_WANT_ADC=y" in config
    assert "test-cc-usb-dbuf-prtouch-v2-adc" in matrix


def test_k1_leveling_e230_target_uses_factory_transport_and_v2_only():
    config = (ROOT / "config" /
              "k1_leveling_gd32e230x8_factory12k_prtouch_v2.config"
              ).read_text(encoding="utf-8")
    for setting in (
            'CONFIG_MCU="gd32e230x8"',
            "CONFIG_CLOCK_FREQ=72000000",
            "CONFIG_FLASH_APPLICATION_ADDRESS=0x08003000",
            "CONFIG_GD32_SERIAL_USART0_PA9_PA10=y",
            "CONFIG_SERIAL_BAUD=230400",
            "CONFIG_WANT_PRTOUCH_V2_COMPAT=y",
            "# CONFIG_WANT_PRTOUCH_V3_COMPAT is not set"):
        assert setting in config
    gd32_kconfig = (ROOT / "src" / "gd32" / "Kconfig").read_text(
        encoding="utf-8")
    gd32_makefile = (ROOT / "src" / "gd32" / "Makefile").read_text(
        encoding="utf-8")
    e230_choice = gd32_kconfig[gd32_kconfig.index(
        "config MACH_GD32E230X6"):gd32_kconfig.index("endchoice")]
    assert e230_choice.count("select HAVE_LIMITED_CODE_SIZE") == 2
    assert "CFLAGS-$(CONFIG_HAVE_LIMITED_CODE_SIZE) += -Os" in gd32_makefile
    root_kconfig = (ROOT / "src" / "Kconfig").read_text(encoding="utf-8")
    optional = root_kconfig[root_kconfig.index(
        'menu "Optional features (to reduce code size)"'):]
    assert 'bool "Support CS1237 ADC chips"' in optional
    bulk_dep = root_kconfig[root_kconfig.index(
        "config NEED_SENSOR_BULK"):root_kconfig.index(
        "config WANT_TRIGGER_ANALOG")]
    assert "WANT_CS1237" in bulk_dep
    matrix = (ROOT / "scripts" / "build-gd32-matrix.sh").read_text(
        encoding="utf-8")
    assert 'k1-leveling-v2) build_one "$target" ' in matrix
    assert "check_prtouch_v2_dictionary.py" in matrix


def test_prtouch_v2_dictionary_checker_has_full_contract():
    checker = (ROOT / "scripts" /
               "check_prtouch_v2_dictionary.py").read_text(
                   encoding="utf-8")
    for name in (
            "config_step_prtouch", "add_step_prtouch",
            "read_swap_prtouch", "start_step_prtouch",
            "manual_get_steps", "config_pres_prtouch",
            "add_pres_prtouch", "write_swap_prtouch",
            "read_pres_prtouch", "deal_avgs_prtouch",
            "start_pres_prtouch", "manual_get_pres",
            "debug_prtouch", "result_read_swap_prtouch",
            "result_run_step_prtouch", "result_run_pres_prtouch"):
        assert '"%s"' % name in checker
