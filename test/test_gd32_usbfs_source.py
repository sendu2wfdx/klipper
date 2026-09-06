from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
USBFS = (ROOT / "src" / "gd32" / "usbfs.c").read_text(encoding="utf-8")


def test_endpoint_registers_are_accessed_as_32_bit_words():
    """GD32F30x USBFS endpoint registers require word accesses."""
    assert "#define USB_EPR(ep) (((volatile uint32_t *)USB_BASE)[ep])" in USBFS
    assert "volatile uint16_t *)USB_BASE" not in USBFS


def test_irq_handles_one_endpoint_event_per_entry():
    """Do not drain CTR in one tight interrupt loop on GD32F30x."""
    irq = USBFS.split("USB_IRQHandler(void)", 1)[1]
    assert "if (istr & USB_ISTR_CTR)" in irq
    assert "while (istr & USB_ISTR_CTR)" not in irq


def test_usb_peripheral_is_reset_before_pma_rebuild():
    reset_assert = USBFS.index("RCU_APB1RST |= RCU_APB1RST_USBDRST;")
    reset_release = USBFS.index("RCU_APB1RST &= ~RCU_APB1RST_USBDRST;")
    pma_setup = USBFS.index("btable_configure();")
    assert reset_assert < reset_release < pma_setup


def test_endpoint_reset_normalizes_toggle_fields():
    """Writing zero does not clear GD32 toggle-on-write endpoint fields."""
    assert "epr_reset_config(uint32_t ep, uint32_t value)" in USBFS
    assert "EPR_TBITS | EPR_RWBITS | EPR_RWCBITS" in USBFS
    reset = USBFS.split("usb_reset(void)", 1)[1].split(
        "// Main irq handler", 1)[0]
    assert reset.count("epr_reset_config(") == 4
    assert "USB_EPR(ep) = bi_epr_flags;" not in reset
