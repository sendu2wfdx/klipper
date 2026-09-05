from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UART = (ROOT / "src" / "gd32" / "creality_485_uart.c").read_text(
    encoding="utf-8"
)
CONFIG = (
    ROOT / "config" / "gd32f303cc_test_usb_pa11_pa12_noboot.config"
).read_text(encoding="utf-8")


def test_usb_test_image_reserves_an_independent_usart0():
    assert 'DECL_CONSTANT_STR("RESERVE_PINS_creality_485_uart", "PA9,PA10")' in UART
    assert "gpio_peripheral(GPIO('A', 10), 1, 3);" in UART
    assert "gpio_peripheral(GPIO('A', 9), 3, 1);" in UART
    assert "CONFIG_GD32_CREALITY_485_UART=y" in CONFIG
    assert "CONFIG_GD32_CREALITY_485_BAUD=230400" in CONFIG


def test_test_image_does_not_allow_f0_flash_mutation():
    assert ".upgrade_enabled = 0" in UART


def test_read_only_link_diagnostics_remain_in_the_dictionary():
    assert "DECL_COMMAND_FLAGS(command_query_creality_485_uart, HF_IN_SHUTDOWN" in UART
    assert "DECL_COMMAND_FLAGS(command_query_creality_485_uart_errors," in UART
    assert "HF_IN_SHUTDOWN" in UART
    assert "query_creality_485_uart_errors" in UART
    assert "creality_485_uart_status rx_frames=%u tx_frames=%u" in UART
    assert "creality_485_uart_errors invalid=%u dropped=%u" in UART


def test_production_usb_path_defaults_to_single_buffer():
    assert "# CONFIG_GD32_USB_DOUBLE_BUFFER_TX is not set" in CONFIG
