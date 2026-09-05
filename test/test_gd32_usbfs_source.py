from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
USBFS = (ROOT / "src" / "gd32" / "usbfs.c").read_text(encoding="utf-8")
STABLE_CONFIG = (ROOT / "config" / "gd32f303cc_test_usb_pa11_pa12_noboot.config").read_text(encoding="utf-8")
DBUF_CONFIG = (ROOT / "config" / "gd32f303cc_test_usb_pa11_pa12_noboot_dbuf.config").read_text(encoding="utf-8")


def test_endpoint_registers_are_accessed_as_32_bit_words():
    """GD32F30x USBFS endpoint registers reject the old halfword access."""
    assert "#define USB_EPR(ep) (((volatile uint32_t *)USB_BASE)[ep])" in USBFS
    assert "volatile uint16_t *)USB_BASE" not in USBFS


def test_irq_handles_one_endpoint_event_per_entry():
    """Draining CTR in a tight ISR loop caused an imprecise bus fault."""
    irq = USBFS.split("USB_IRQHandler(void)", 1)[1]
    assert "if (istr & USB_ISTR_CTR)" in irq
    assert "while (istr & USB_ISTR_CTR)" not in irq


def test_double_buffer_tx_uses_public_race_handoff_model():
    assert "CONFIG_GD32_USB_DOUBLE_BUFFER_TX" in USBFS
    sender = USBFS.split("usb_send_bulk_in_double_buffer", 1)[1].split(
        "// Send bulk usb packet", 1)[0]
    assert "if (readl(&bulk_in_pop_flag))" in sender
    assert "bulk_in_push_pos = bipp ^ 1;" in sender
    assert "writel(&bulk_in_pop_flag, USB_EP_DTOG_RX);" in sender
    assert "epr_is_dbuf_blocking(epr)" in sender
    assert "writel(&bulk_in_pop_flag, 0);" in sender
    irq = USBFS.split("USB_IRQHandler(void)", 1)[1].split(
        "if (istr & USB_ISTR_RESET)", 1)[0]
    assert "ne |= bulk_in_pop_flag;" in irq
    assert "bulk_in_pop_flag = 0;" in irq


def test_double_buffer_primes_two_packets_before_startup():
    """The public algorithm waits for two packets before setting TX VALID."""
    assert "#define BI_START 2" in USBFS
    sender = USBFS.split("usb_send_bulk_in_double_buffer", 1)[1].split(
        "// Send bulk usb packet", 1)[0]
    assert "if (unlikely(bipp & BI_START))" in sender
    assert "if (bipp == (BI_START | 1))" in sender
    assert "bulk_in_push_pos = 0;" in sender
    assert "USB_EP_TX_VALID" in sender


def test_double_buffer_selector_model_alternates_after_startup():
    """Model the documented selector transitions after two-buffer priming."""
    tx_dtog = 0
    sw_buf = 0
    tx_dtog ^= 1                 # first primed buffer completes
    tx_dtog ^= 1                 # second primed buffer completes
    assert tx_dtog == sw_buf
    used = []
    for _ in range(8):
        assert tx_dtog == sw_buf
        used.append(1 if tx_dtog else 0)
        sw_buf ^= 1              # software hands the filled buffer to USB
        assert tx_dtog != sw_buf
        tx_dtog ^= 1             # hardware completes that buffer
        assert tx_dtog == sw_buf
    assert used == [0, 1, 0, 1, 0, 1, 0, 1]


def test_usb_peripheral_is_reset_before_pma_rebuild():
    reset_assert = USBFS.index("RCU_APB1RST |= RCU_APB1RST_USBDRST;")
    reset_release = USBFS.index("RCU_APB1RST &= ~RCU_APB1RST_USBDRST;")
    pma_setup = USBFS.index("btable_configure();")
    assert reset_assert < reset_release < pma_setup


def test_endpoint_reset_normalizes_toggle_fields():
    """Writing zero directly does not clear GD32's toggle-on-write fields."""
    assert "epr_reset_config(uint32_t ep, uint32_t value)" in USBFS
    assert "EPR_TBITS | EPR_RWBITS | EPR_RWCBITS" in USBFS
    reset = USBFS.split("usb_reset(void)", 1)[1].split(
        "// Main irq handler", 1)[0]
    assert reset.count("epr_reset_config(") == 4
    assert "USB_EPR(ep) = bi_epr_flags;" not in reset


def test_double_buffer_enables_high_priority_usb_irq():
    """GD32 routes double-buffered bulk completion to the HP USB vector."""
    assert "#define USBx_HP_IRQn USBD_HP_CAN0_TX_IRQn" in USBFS
    assert "if (CONFIG_GD32_USB_DOUBLE_BUFFER_TX)" in USBFS
    assert "armcm_enable_irq(USB_IRQHandler, USBx_HP_IRQn, 1);" in USBFS


def test_double_buffer_stays_opt_in_for_test_board():
    assert "# CONFIG_GD32_USB_DOUBLE_BUFFER_TX is not set" in STABLE_CONFIG
    assert "CONFIG_GD32_USB_DOUBLE_BUFFER_TX=y" in DBUF_CONFIG
