from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TIMER = (ROOT / "src" / "generic" / "armcm_timer.c").read_text(
    encoding="utf-8")


def test_gd32_keeps_systick_reload_until_the_irq_reprograms_it():
    """GD32 loses the pending one-shot if LOAD is cleared after VAL."""
    setter = TIMER.split("timer_set_diff(uint32_t value)", 1)[1].split(
        "// Return the current time", 1)[0]
    assert "SysTick->LOAD = value;" in setter
    assert "SysTick->VAL = 0;" in setter
    assert "#if !CONFIG_MACH_GD32F303XX" in setter
    assert setter.index("SysTick->LOAD = value;") < setter.index(
        "#if !CONFIG_MACH_GD32F303XX")
    assert setter.index("#if !CONFIG_MACH_GD32F303XX") < setter.index(
        "SysTick->LOAD = 0;")


def test_timer_kick_still_uses_explicit_pendst_for_all_platforms():
    kick = TIMER.split("timer_kick(void)", 1)[1].split(
        "// Implement simple early-boot", 1)[0]
    assert "SCB->ICSR = SCB_ICSR_PENDSTSET_Msk;" in kick
