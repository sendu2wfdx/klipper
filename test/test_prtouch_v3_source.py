import pathlib
import unittest


SOURCE = (pathlib.Path(__file__).parents[1] / "src" /
          "prtouch_v3.c").read_text(encoding="utf-8")
SCHED = (pathlib.Path(__file__).parents[1] / "src" /
         "sched.c").read_text(encoding="utf-8")


class PRTouchV3SourceTest(unittest.TestCase):
    def test_original_commands_are_preserved(self):
        commands = (
            "config_prtouch_pres", "start_prtouch_pres",
            "stop_prtouch_pres", "read_prtouch_pres",
            "config_prtouch_step", "start_prtouch_step",
            "stop_prtouch_step", "read_prtouch_step", "cont_prtouch_step",
            "config_prtouch_apax", "start_prtouch_apax",
            "stop_prtouch_apax",
        )
        for command in commands:
            self.assertIn(f'"{command} ', SOURCE)

    def test_original_response_spelling_is_preserved(self):
        for response in ("ack_prtouch", "resault_prtouch_pres",
                         "resault_prtouch_step",
                         "resault_prtouch_step_cnt",
                         "resault_prtouch_apax"):
            self.assertIn(f'"{response} ', SOURCE)

    def test_adc_mode_selection_matches_factory_global_flag(self):
        self.assertIn("pr_pres.use_adcx = args[3] == args[4]", SOURCE)
        self.assertIn("if (pr_pres.use_adcx)", SOURCE)
        self.assertNotIn("pressure channel out of range", SOURCE)

    def test_factory_uses_idle_poll_dispatch_not_private_timers(self):
        self.assertIn("pr_delay_due", SOURCE)
        # The factory object's real .compile_time_request section contains
        # command/response declarations but no ctr_run_taskfuncs call-list
        # entry.  DECL_TASK text visible in DWARF is only the macro definition.
        self.assertNotIn("DECL_TASK(prtouch_task)", SOURCE)
        self.assertNotIn("sched_add_timer", SOURCE)
        self.assertNotIn("sched_del_timer", SOURCE)
        self.assertNotIn("sched_wake_task", SOURCE)
        self.assertNotIn("DECL_TIMER", SOURCE)

    def test_factory_pressure_task_keeps_polling_while_scheduler_is_idle(self):
        self.assertIn(
            "#if CONFIG_HAVE_PRTOUCH_V1_V2 || "
            "CONFIG_HAVE_PRTOUCH_V3", SCHED)
        idle_dispatch = SCHED[SCHED.index("#if CONFIG_HAVE_PRTOUCH_V1_V2"):
                              SCHED.index("#else", SCHED.index(
                                  "#if CONFIG_HAVE_PRTOUCH_V1_V2"))]
        self.assertLess(idle_dispatch.index("irq_enable();"),
                        idle_dispatch.index("irq_poll();"))
        self.assertLess(idle_dispatch.index("irq_poll();"),
                        idle_dispatch.index("prtouch_task();"))

    def test_adc_and_cs1237_poll_limits_match_object(self):
        self.assertIn("PR_ADC_POLL_LIMIT 502", SOURCE)
        self.assertIn("#if CONFIG_CLOCK_FREQ == 120000000", SOURCE)
        self.assertIn("PR_CS_CFG_READY_TICKS 14400000u", SOURCE)
        self.assertIn("PR_CS_DATA_READY_TICKS 600000u", SOURCE)
        self.assertIn("PR_CS_CFG_READY_TICKS timer_from_us(120000u)",
                      SOURCE)
        self.assertIn("PR_CS_DATA_READY_TICKS timer_from_us(5000u)",
                      SOURCE)
        self.assertNotIn("timer_from_us(PR_CS_", SOURCE)
        self.assertIn(
            "while (gpio_adc_sample(pr_pres.adc_pin[channel]) && --retries)",
            SOURCE)
        self.assertIn("#define PR_CS_DELAY_LOOPS 10u", SOURCE)
        self.assertIn("volatile uint32_t delay = PR_CS_DELAY_LOOPS", SOURCE)
        self.assertIn("__attribute__((always_inline))", SOURCE)

    def test_probe_and_continuous_stream_paths_are_separate(self):
        self.assertIn("if (pr_pres.min_hold == pr_pres.max_hold)", SOURCE)
        self.assertIn("PR_SAMPLES - 1, channel, 0, 0", SOURCE)

    def test_pressure_task_keeps_factory_unused_timer_read(self):
        body = SOURCE[SOURCE.index("prtouch_pres_task(void)"):
                      SOURCE.index("prtouch_step_task(void)")]
        self.assertIn("(void)timer_read_time();", body)

    def test_reversed_filter_windows_are_preserved(self):
        self.assertIn("wrapped_sum += (uint32_t)hftr[15]", SOURCE)
        self.assertIn("pr_nearest(edge[0], &edge[1], 3)", SOURCE)
        self.assertIn("pr_nearest(edge[1], &edge[2], 3)", SOURCE)
        self.assertIn("ticks[i] = ticks[i + 1]", SOURCE)
        self.assertIn("raw[i] = raw[i + 1]", SOURCE)
        self.assertIn("filtered[i] = filtered[i + 1]", SOURCE)

    def test_zero_length_query_keeps_factory_len_quirk(self):
        self.assertIn("return cursor - index + 1", SOURCE)

    def test_read_query_preserves_factory_unchecked_channel_rule(self):
        self.assertIn("uint32_t index = args[3]", SOURCE)
        self.assertIn("uint32_t index = args[1]", SOURCE)
        self.assertIn("uint32_t end = index + args[4]", SOURCE)
        self.assertNotIn("channel >= PR_CHANNELS", SOURCE)
        self.assertNotIn("channel >= pr_pres.sensor_count", SOURCE)

    def test_adc_hold_preserves_factory_unchecked_count(self):
        self.assertIn("cursor - pr_pres.min_hold", SOURCE)
        self.assertIn("if (*--cursor >= pr_pres.max_hold)", SOURCE)
        self.assertIn("matches == pr_pres.min_hold", SOURCE)
        self.assertNotIn("min_hold > PR_SAMPLES", SOURCE)

    def test_apax_start_resets_shared_pressure_state(self):
        self.assertIn("pr_reset_pressure_buffers();", SOURCE)

    def test_apax_uses_extruder_step_interval(self):
        self.assertIn("step_prtouch_get_ivt(pr_apax.oid_estp)", SOURCE)

    def test_factory_delta_stream_is_emitted(self):
        self.assertIn("pr_zip_write", SOURCE)
        self.assertIn("zip->data_start - 1 - selector_group", SOURCE)
        self.assertIn("(zip->item_count & 3) * 2", SOURCE)
        self.assertIn("PR_ZIP_LIMIT 41", SOURCE)
        self.assertIn("value == PR_INVALID", SOURCE)

    def test_empty_delta_stream_keeps_factory_padding(self):
        self.assertIn("if (!count)", SOURCE)
        self.assertIn("groups++", SOURCE)

    def test_sync_pin_directions_match_factory(self):
        self.assertIn("pr_pres.swp_out = gpio_out_setup(args[2], 1)", SOURCE)
        self.assertIn("pr_step.swp = gpio_in_setup(args[4], 1)", SOURCE)
        self.assertIn("gpio_out_write(pr_pres.swp_out, 0)", SOURCE)

    def test_cs1237_register_protocol_is_present(self):
        self.assertIn("pr_cs_write_bits(channel, data, 0x65, 7)", SOURCE)
        self.assertIn("pr_cs_write_bits(channel, data, 0x56, 7)", SOURCE)
        self.assertIn("pr_cs_read_bits(channel, 27)", SOURCE)
        self.assertIn("pr_cs_read_cfg_bits(channel)", SOURCE)
        cfg_reader = SOURCE[SOURCE.index("pr_cs_read_cfg_bits"):
                            SOURCE.index("// CS1237 register access")]
        self.assertIn("pr_cs_clock(channel, 0)", cfg_reader)
        self.assertIn("gpio_in_read(pr_pres.sdi_pin[channel]) << bit",
                      cfg_reader)

    def test_factory_state_layout_is_compile_time_checked(self):
        expected = (
            "sizeof(struct pr_zip) == 160",
            "sizeof(struct pr_pres_buf) == 3488",
            "sizeof(struct pr_pres) == 4900",
            "sizeof(struct pr_step) == 540",
            "sizeof(struct pr_apax) == 1944",
            "offsetof(struct pr_pres, buffer) == 20",
            "offsetof(struct pr_pres, zip_tick) == 3512",
            "offsetof(struct pr_pres, sdo_pin_number) == 4824",
            "offsetof(struct pr_apax, zip_tick) == 16",
        )
        for marker in expected:
            self.assertIn(marker, SOURCE)

    def test_streaming_paths_use_factory_incremental_compressors(self):
        self.assertIn("struct pr_zip zip_tick[PR_CHANNELS]", SOURCE)
        self.assertIn("static struct pr_zip pr_read_pres_zip_tick", SOURCE)
        self.assertIn("static struct pr_zip pr_read_step_zip_tick", SOURCE)
        self.assertIn("zip->data_start = (uint8_t *)zip + 32", SOURCE)
        self.assertIn("zip->data_start - 1 - selector_group", SOURCE)
        self.assertIn("&pr_pres.zip_tick[channel], tick", SOURCE)
        self.assertIn("&pr_apax.zip_tick[channel], tick", SOURCE)
        self.assertNotIn("factory_tail", SOURCE)
        self.assertNotIn("zip_state", SOURCE)

    def test_cont_step_returns_xyz_counts(self):
        self.assertIn("resault_prtouch_step_cnt", SOURCE)
        self.assertIn("step_prtouch_get_cnt(pr_step.oid_x)", SOURCE)

    def test_step_sampler_checks_swap_pin_twice(self):
        body = SOURCE[SOURCE.index("prtouch_step_task(void)"):
                      SOURCE.index("prtouch_apax_task(void)")]
        self.assertEqual(body.count("gpio_in_read(pr_step.swp)"), 2)
        self.assertIn("!pr_delay_due(pr_step.acq_tick, &pr_step.delay_tick)\n"
                      "        && gpio_in_read(pr_step.swp)", body)
        self.assertIn("pr_step.ticks[i] = pr_step.ticks[i + 1]", body)
        self.assertIn("pr_step.zpos[i] = pr_step.zpos[i + 1]", body)


if __name__ == "__main__":
    unittest.main()
