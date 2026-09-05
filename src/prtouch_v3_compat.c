// Clean-room compatibility implementation of the Creality PRTouch V3 MCU ABI.
// The command surface, buffer/filter layout, delta stream and synchronization
// behavior are reconstructed from the GPL-covered V71 MCU object.
// Copyright (C) 2026
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include "basecmd.h"
#include "board/gpio.h"
#include "board/misc.h"
#include "command.h"
#include "sched.h"
#include "stepper.h"

#define PR_CHANNELS 4
#define PR_SAMPLES 64
#define PR_HFTR_SAMPLES 21
#define PR_ZIP_LIMIT 41
#define PR_VERSION 71
#define PR_INVALID INT32_MIN
// These are deliberately fixed MCU tick counts, not time conversions.  The
// factory V71 object contains the literal thresholds 14400000 and 599999;
// therefore a transaction is allowed at 14400000/600000 ticks respectively.
// The V57 nozzle reports a 120MHz clock but still uses those literals.  Scaling
// them through timer_from_us() changes the CS1237 ready timeout by one sixth
// and can clock conversion data before DOUT is ready.
#define PR_CS_CFG_READY_TICKS 14400000u
#define PR_CS_DATA_READY_TICKS 600000u
#define PR_ADC_POLL_LIMIT 502
#define PR_CS_DELAY_LOOPS 10u
#define PR_FACTORY_LOOP \
    __attribute__((optimize("no-tree-loop-distribute-patterns")))
#define PR_ALWAYS_INLINE static inline __attribute__((always_inline))

// Exact V71 incremental delta-compressor layout recovered from DWARF and
// checked against prtouch_reset_zip()/write_zip()/read_zip() disassembly.
struct pr_zip {
    int32_t last_data;
    uint32_t item_count, data_len;
    uint8_t *data_start;
    uint8_t work[80];
    uint8_t result[64];
};

// Factory pressure storage is structure-of-arrays, not one large object per
// channel.  Keeping this shape matters because APAX shares the same channel
// setup/read state while owning only its compressor banks.
struct pr_pres_buf {
    uint32_t ready_tick[PR_CHANNELS];
    int32_t hftr_sum[PR_CHANNELS];
    uint32_t cfg_state[PR_CHANNELS];
    uint32_t cfg_value[PR_CHANNELS];
    uint32_t sample_count[PR_CHANNELS];
    uint32_t ticks[PR_CHANNELS][PR_SAMPLES];
    int32_t raw[PR_CHANNELS][PR_SAMPLES];
    int32_t filtered[PR_CHANNELS][PR_SAMPLES];
    int32_t hftr[PR_CHANNELS][PR_HFTR_SAMPLES];
};

struct pr_pres {
    uint32_t oid, use_adcx, sensor_count, cfg_regs, tri_chxs;
    struct pr_pres_buf buffer;
    uint32_t delay_tick;
    struct pr_zip zip_tick[PR_CHANNELS];
    struct pr_zip zip_data[PR_CHANNELS];
    uint32_t acq_tick, ned_tftr, ned_hftr;
    float lowpass;
    int32_t min_hold, max_hold, add_hold;
    uint32_t lmt_dead;
    uint32_t sdo_pin_number[PR_CHANNELS];
    struct gpio_out swp_out;
    struct gpio_in swp_in;
    struct gpio_adc adc_pin[PR_CHANNELS];
    struct gpio_out clk_pin[PR_CHANNELS];
    struct gpio_in sdi_pin[PR_CHANNELS];
    struct gpio_out sdo_pin[PR_CHANNELS];
};

struct pr_step {
    uint32_t oid;
    int32_t oid_x, oid_y, oid_z;
    uint32_t acq_tick, delay_tick;
    uint32_t ticks[PR_SAMPLES];
    int32_t zpos[PR_SAMPLES];
    struct gpio_in swp;
};

struct pr_apax {
    uint32_t oid, oid_estp, acq_tick, cfg_regs;
    struct pr_zip zip_tick[PR_CHANNELS];
    struct pr_zip zip_data[PR_CHANNELS];
    struct pr_zip zip_interval[PR_CHANNELS];
    uint32_t delay_tick, acc_count;
};

#if UINTPTR_MAX == UINT32_MAX
#define PR_LAYOUT_ASSERT(name, condition) \
    typedef char pr_layout_assert_##name[(condition) ? 1 : -1]
PR_LAYOUT_ASSERT(zip_size, sizeof(struct pr_zip) == 160);
PR_LAYOUT_ASSERT(zip_data_start_offset,
                 offsetof(struct pr_zip, data_start) == 12);
PR_LAYOUT_ASSERT(zip_work_offset, offsetof(struct pr_zip, work) == 16);
PR_LAYOUT_ASSERT(zip_result_offset, offsetof(struct pr_zip, result) == 96);
PR_LAYOUT_ASSERT(buffer_size, sizeof(struct pr_pres_buf) == 3488);
PR_LAYOUT_ASSERT(buffer_sum_offset,
                 offsetof(struct pr_pres_buf, hftr_sum) == 16);
PR_LAYOUT_ASSERT(buffer_state_offset,
                 offsetof(struct pr_pres_buf, cfg_state) == 32);
PR_LAYOUT_ASSERT(buffer_value_offset,
                 offsetof(struct pr_pres_buf, cfg_value) == 48);
PR_LAYOUT_ASSERT(buffer_index_offset,
                 offsetof(struct pr_pres_buf, sample_count) == 64);
PR_LAYOUT_ASSERT(buffer_ticks_offset,
                 offsetof(struct pr_pres_buf, ticks) == 80);
PR_LAYOUT_ASSERT(buffer_raw_offset,
                 offsetof(struct pr_pres_buf, raw) == 1104);
PR_LAYOUT_ASSERT(buffer_filtered_offset,
                 offsetof(struct pr_pres_buf, filtered) == 2128);
PR_LAYOUT_ASSERT(buffer_hftr_offset,
                 offsetof(struct pr_pres_buf, hftr) == 3152);
PR_LAYOUT_ASSERT(pres_size, sizeof(struct pr_pres) == 4900);
PR_LAYOUT_ASSERT(step_size, sizeof(struct pr_step) == 540);
PR_LAYOUT_ASSERT(apax_size, sizeof(struct pr_apax) == 1944);
PR_LAYOUT_ASSERT(pres_buffer_offset,
                 offsetof(struct pr_pres, buffer) == 20);
PR_LAYOUT_ASSERT(pres_delay_offset,
                 offsetof(struct pr_pres, delay_tick) == 3508);
PR_LAYOUT_ASSERT(pres_zip_tick_offset,
                 offsetof(struct pr_pres, zip_tick) == 3512);
PR_LAYOUT_ASSERT(pres_zip_data_offset,
                 offsetof(struct pr_pres, zip_data) == 4152);
PR_LAYOUT_ASSERT(pres_acq_offset,
                 offsetof(struct pr_pres, acq_tick) == 4792);
PR_LAYOUT_ASSERT(pres_threshold_offset,
                 offsetof(struct pr_pres, min_hold) == 4808);
PR_LAYOUT_ASSERT(pres_pins_offset,
                 offsetof(struct pr_pres, sdo_pin_number) == 4824);
PR_LAYOUT_ASSERT(pres_swap_offset,
                 offsetof(struct pr_pres, swp_out) == 4840);
PR_LAYOUT_ASSERT(pres_adc_offset,
                 offsetof(struct pr_pres, adc_pin) == 4848);
PR_LAYOUT_ASSERT(pres_clk_offset,
                 offsetof(struct pr_pres, clk_pin) == 4864);
PR_LAYOUT_ASSERT(pres_sdi_offset,
                 offsetof(struct pr_pres, sdi_pin) == 4880);
PR_LAYOUT_ASSERT(pres_sdo_offset,
                 offsetof(struct pr_pres, sdo_pin) == 4884);
PR_LAYOUT_ASSERT(step_buffer_offset,
                 offsetof(struct pr_step, ticks) == 24);
PR_LAYOUT_ASSERT(step_swap_offset,
                 offsetof(struct pr_step, swp) == 536);
PR_LAYOUT_ASSERT(apax_zip_offset,
                 offsetof(struct pr_apax, zip_tick) == 16);
PR_LAYOUT_ASSERT(apax_data_offset,
                 offsetof(struct pr_apax, zip_data) == 656);
PR_LAYOUT_ASSERT(apax_interval_offset,
                 offsetof(struct pr_apax, zip_interval) == 1296);
PR_LAYOUT_ASSERT(apax_delay_offset,
                 offsetof(struct pr_apax, delay_tick) == 1936);
#undef PR_LAYOUT_ASSERT
#endif

static struct pr_pres pr_pres;
static struct pr_step pr_step;
static struct pr_apax pr_apax;
// The two query commands each own a pair of static compressor workspaces in
// the factory object (zip_tick/data.558x and zip_tick/data.570x).
static struct pr_zip pr_read_pres_zip_tick, pr_read_pres_zip_data;
static struct pr_zip pr_read_step_zip_tick, pr_read_step_zip_data;

static void
pr_ack(uint8_t oid, uint8_t err, uint32_t p0, uint32_t p1)
{
    sendf("ack_prtouch oid=%c err=%c expar0=%u expar1=%u",
          oid, err, p0, p1);
}

static uint_fast8_t
pr_delta_width(uint32_t delta)
{
    if (delta + 0x80u <= 0xffu)
        return 1;
    if (delta + 0x8000u < 0x10000u)
        return 2;
    if (delta + 0x800000u < 0x1000000u)
        return 3;
    return 4;
}

static void
pr_zip_reset(struct pr_zip *zip)
{
    zip->last_data = 0;
    zip->item_count = 0;
    zip->data_len = 0;
    // V71 deliberately leaves sixteen bytes in front of payload data so
    // selector groups can grow backwards without moving prior samples.
    zip->data_start = (uint8_t *)zip + 32;
    memset(zip->work, 0, sizeof(zip->work));
}

static uint_fast8_t
pr_zip_write(struct pr_zip *zip, int32_t value)
{
    uint32_t selector_group = zip->item_count >> 2;
    if (value == PR_INVALID)
        return zip->data_len + selector_group + 2;

    uint32_t delta = (uint32_t)value - (uint32_t)zip->last_data;
    uint_fast8_t width = pr_delta_width(delta);
    memcpy(zip->data_start + zip->data_len, &delta, width);
    uint8_t *selector = zip->data_start - 1 - selector_group;
    *selector |= (width - 1) << ((zip->item_count & 3) * 2);
    zip->last_data = value;
    zip->item_count++;
    zip->data_len += width;
    return zip->data_len + selector_group + 2;
}

static uint8_t *
pr_zip_read(struct pr_zip *zip)
{
    uint32_t count = zip->item_count;
    uint32_t groups = (count + 3) >> 2;
    // The object uses clz(count) >> 5 as a one-byte empty-stream pad.
    // Consequently both an empty stream and a one-to-four-item stream start
    // two bytes before data_start.  For count==0 this produces the exact
    // two-byte result {0, 0}, even though query callers transmit zero bytes.
    if (!count)
        groups++;
    uint8_t *start = zip->data_start - groups - 1;
    *start = count;
    // The factory copy includes one otherwise-unused trailing byte when the
    // item count is an exact multiple of four.  The caller still transmits
    // the length returned by pr_zip_write(), so this remains unobservable.
    uint32_t copy_len = zip->data_len + 2 + (count >> 2);
    memcpy(zip->result, start, copy_len);
    pr_zip_reset(zip);
    return zip->result;
}

static uint_fast8_t
pr_zip_pair_range(struct pr_zip *zip_tick, struct pr_zip *zip_data,
                  const uint32_t *ticks, const int32_t *data,
                  uint32_t index, uint32_t end,
                  uint_fast8_t *tick_len, uint_fast8_t *data_len)
{
    pr_zip_reset(zip_tick);
    pr_zip_reset(zip_data);
    *tick_len = *data_len = 0;
    uint32_t cursor = index;
    while (cursor < end) {
        *tick_len = pr_zip_write(zip_tick, ticks[cursor]);
        *data_len = pr_zip_write(zip_data, data[cursor]);
        if (*tick_len + *data_len > PR_ZIP_LIMIT)
            break;
        cursor++;
    }
    // V71 reports one past the loop counter.  Thus a completed range (and an
    // empty range) carries an extra one, while a threshold-crossing sample
    // reports its exact included count.
    return cursor - index + 1;
}

static int32_t
pr_nearest(int32_t previous, int32_t *samples, uint_fast8_t count)
{
    uint_fast8_t best = 0, i;
    int32_t best_delta = 0x00ffffff;
    for (i = 0; i < count; i++) {
        // Factory Thumb code performs a wrapping 32-bit SUBS followed by a
        // conditional 32-bit NEG and signed LT comparison.  Express the
        // wrap through unsigned arithmetic so the C source itself has no
        // signed-overflow undefined behavior.  INT32_MIN therefore remains
        // negative after the NEG, exactly as on the original MCU.
        int32_t magnitude = (int32_t)((uint32_t)samples[i]
                                      - (uint32_t)previous);
        if (magnitude < 0)
            magnitude = (int32_t)(0u - (uint32_t)magnitude);
        if (magnitude < best_delta) {
            best_delta = magnitude;
            best = i;
        }
    }
    samples[0] = samples[best];
    return samples[0];
}

PR_ALWAYS_INLINE void
pr_cs_delay(void)
{
    // prtouch_delay_us(1) in V71 is not a ten-NOP delay: it decrements a
    // volatile counter from ten, forcing repeated loads/stores and branches.
    volatile uint32_t delay = PR_CS_DELAY_LOOPS;
    while (delay)
        delay--;
}

// The V71 object uses a cooperative polling task instead of sched timers.  Its
// wrap handling intentionally treats a wrapped counter as an interval from 0.
static uint_fast8_t
pr_delay_due(uint32_t interval, uint32_t *last_tick)
{
    uint32_t now = timer_read_time();
    uint32_t elapsed = now > *last_tick ? now - *last_tick : now;
    if (elapsed < interval)
        return 0;
    *last_tick = now;
    return 1;
}

PR_ALWAYS_INLINE uint_fast8_t
pr_cs_ready_ticks(uint_fast8_t channel, uint32_t timeout_ticks)
{
    gpio_out_write(pr_pres.clk_pin[channel], 0);
    pr_cs_delay();
    if (gpio_in_read(pr_pres.sdi_pin[channel])) {
        uint32_t now = timer_read_time();
        uint32_t elapsed = now > pr_pres.buffer.ready_tick[channel]
                           ? now - pr_pres.buffer.ready_tick[channel] : now;
        if (elapsed < timeout_ticks)
            return 0;
    }
    pr_pres.buffer.ready_tick[channel] = timer_read_time();
    pr_cs_delay();
    return 1;
}

PR_ALWAYS_INLINE void
pr_cs_clock(uint_fast8_t channel, uint_fast8_t output)
{
    gpio_out_write(pr_pres.clk_pin[channel], 1);
    pr_cs_delay();
    gpio_out_write(pr_pres.clk_pin[channel], 0);
    pr_cs_delay();
    (void)output;
}

PR_ALWAYS_INLINE uint32_t
pr_cs_read_bits(uint_fast8_t channel, uint_fast8_t count)
{
    uint32_t value = 0;
    uint_fast8_t i;
    for (i = 0; i < count; i++) {
        gpio_out_write(pr_pres.clk_pin[channel], 1);
        pr_cs_delay();
        value = (value << 1) | gpio_in_read(pr_pres.sdi_pin[channel]);
        gpio_out_write(pr_pres.clk_pin[channel], 0);
        pr_cs_delay();
    }
    return value;
}

PR_ALWAYS_INLINE void
pr_cs_write_bits(uint_fast8_t channel, struct gpio_out data,
                 uint32_t value, uint_fast8_t count)
{
    uint_fast8_t i;
    for (i = 0; i < count; i++) {
        gpio_out_write(pr_pres.clk_pin[channel], 1);
        pr_cs_delay();
        gpio_out_write(data, (value >> (count - 1 - i)) & 1);
        pr_cs_delay();
        gpio_out_write(pr_pres.clk_pin[channel], 0);
        pr_cs_delay();
    }
}

PR_ALWAYS_INLINE uint32_t
pr_cs_read_cfg_bits(uint_fast8_t channel)
{
    uint32_t value = 0;
    int_fast8_t bit;
    // Unlike conversion data, V71 samples each configuration bit after CLK
    // has completed its high-to-low pulse.
    for (bit = 7; bit >= 0; bit--) {
        pr_cs_clock(channel, 0);
        value |= gpio_in_read(pr_pres.sdi_pin[channel]) << bit;
    }
    return value;
}

// CS1237 register access sequence from V71: consume 29 conversion/status bits,
// issue a seven-bit 0x65 write or 0x56 read opcode, then transfer one byte.
static int32_t
pr_cs_write_cfg(uint_fast8_t channel, uint8_t cfg)
{
    if (!pr_cs_ready_ticks(channel, PR_CS_CFG_READY_TICKS))
        return PR_INVALID;
    uint32_t conversion = pr_cs_read_bits(channel, 29);
    struct gpio_out data = gpio_out_setup(
        pr_pres.sdo_pin_number[channel], 0);
    pr_pres.sdo_pin[channel] = data;
    pr_cs_write_bits(channel, data, 0x65, 7);
    pr_cs_clock(channel, 0);
    pr_cs_write_bits(channel, data, cfg, 8);
    pr_pres.sdi_pin[channel] = gpio_in_setup(
        pr_pres.sdo_pin_number[channel], 1);
    pr_cs_clock(channel, 0);
    pr_pres.buffer.ready_tick[channel] = timer_read_time();
    if (conversion & (1u << 28))
        conversion |= 0xe0000000u;
    return (int32_t)conversion >> 5;
}

static int32_t
pr_cs_read_cfg(uint_fast8_t channel, uint32_t *cfg)
{
    *cfg = 0;
    if (!pr_cs_ready_ticks(channel, PR_CS_CFG_READY_TICKS))
        return PR_INVALID;
    uint32_t conversion = pr_cs_read_bits(channel, 29);
    struct gpio_out data = gpio_out_setup(
        pr_pres.sdo_pin_number[channel], 0);
    pr_pres.sdo_pin[channel] = data;
    pr_cs_write_bits(channel, data, 0x56, 7);
    pr_cs_clock(channel, 0);
    pr_pres.sdi_pin[channel] = gpio_in_setup(
        pr_pres.sdo_pin_number[channel], 1);
    *cfg = pr_cs_read_cfg_bits(channel);
    pr_cs_clock(channel, 0);
    pr_pres.buffer.ready_tick[channel] = timer_read_time();
    if (conversion & (1u << 28))
        conversion |= 0xe0000000u;
    return (int32_t)conversion >> 5;
}

static int32_t
pr_cs1237_read(uint_fast8_t channel)
{
    if (!pr_cs_ready_ticks(channel, PR_CS_DATA_READY_TICKS))
        return PR_INVALID;
    uint32_t value = pr_cs_read_bits(channel, 27);
    value >>= 3;
    if (value & 0x800000)
        value |= 0xff000000;
    return value;
}

static int32_t
pr_channel_read(uint_fast8_t channel)
{
    if (pr_pres.use_adcx) {
        uint_fast16_t retries = PR_ADC_POLL_LIMIT;
        while (gpio_adc_sample(pr_pres.adc_pin[channel]) && --retries)
            pr_cs_delay();
        return gpio_adc_read(pr_pres.adc_pin[channel]);
    }
    return pr_cs1237_read(channel);
}

// Reconstructed V71 trigger pipeline.  The 16-sample high-pass accumulator
// consumes a five-sample delayed candidate stream; the final digital test is
// the original three-level bidirectional edge plus its asymmetric 61-sample
// history guard.
static uint_fast8_t PR_FACTORY_LOOP
pr_filter_and_trigger(uint_fast8_t channel, uint32_t tick, int32_t value)
{
    uint32_t *ticks = pr_pres.buffer.ticks[channel];
    int32_t *raw = pr_pres.buffer.raw[channel];
    int32_t *filtered = pr_pres.buffer.filtered[channel];
    int32_t *hftr = pr_pres.buffer.hftr[channel];
    uint_fast8_t i;
    // V71 clears this field on every channel evaluation.  The caller keeps a
    // separate OR accumulator while the public response exposes the last
    // channel evaluation, including that original quirk.
    pr_pres.tri_chxs = 0;
    // The object moves all three 64-point rows in one interleaved loop.
    for (i = 0; i < PR_SAMPLES - 1; i++) {
        ticks[i] = ticks[i + 1];
        raw[i] = raw[i + 1];
        filtered[i] = filtered[i + 1];
    }
    ticks[PR_SAMPLES - 1] = tick;
    raw[PR_SAMPLES - 1] = value;
    filtered[PR_SAMPLES - 1] = value;
    pr_pres.buffer.sample_count[channel]++;

    if (pr_pres.ned_hftr) {
        int32_t oldest = hftr[0];
        for (i = 0; i < PR_HFTR_SAMPLES - 1; i++)
            hftr[i] = hftr[i + 1];
        hftr[PR_HFTR_SAMPLES - 1] = raw[20];
        // The object loads hftr[15] after the shift, so this is the old
        // hftr[16], not the old hftr[15].
        uint32_t wrapped_sum = (uint32_t)pr_pres.buffer.hftr_sum[channel]
                               - (uint32_t)oldest;
        wrapped_sum += (uint32_t)hftr[15];
        pr_pres.buffer.hftr_sum[channel] = (int32_t)wrapped_sum;
        pr_nearest(hftr[15], &hftr[16], 5);
        int32_t average = pr_pres.buffer.hftr_sum[channel] / 16;
        filtered[PR_SAMPLES - 1] = (int32_t)(
            (uint32_t)filtered[PR_SAMPLES - 1] - (uint32_t)average);
    }
    if (pr_pres.ned_tftr)
        pr_nearest(filtered[58], &filtered[59], 5);
    if ((pr_pres.lowpass > .001f || pr_pres.lowpass < -.001f)
        && pr_pres.buffer.sample_count[channel] > 5) {
        filtered[59] = (int32_t)(filtered[58]
                          * (1.f - pr_pres.lowpass)
                          + filtered[59] * pr_pres.lowpass);
    }
    if (pr_pres.buffer.sample_count[channel] < pr_pres.lmt_dead)
        return 0;

    if (pr_pres.use_adcx) {
        // The factory code scans backwards, counts all matching samples, and
        // only then compares the count.  It intentionally does not clamp a
        // host-supplied hold length to the 64-sample allocation.
        int32_t matches = 0;
        if (pr_pres.min_hold > 0) {
            int32_t *cursor = &filtered[PR_SAMPLES];
            int32_t *begin = cursor - pr_pres.min_hold;
            do {
                if (*--cursor >= pr_pres.max_hold)
                    matches++;
            } while (cursor != begin);
        }
        if (matches == pr_pres.min_hold)
            pr_pres.tri_chxs |= 1 << channel;
        return pr_pres.tri_chxs;
    }

    int32_t edge[5];
    memcpy(edge, &filtered[59], sizeof(edge));
    pr_nearest(edge[0], &edge[1], 3);
    pr_nearest(edge[1], &edge[2], 3);
    uint_fast8_t inverted = filtered[0] > filtered[PR_SAMPLES - 1];
    if (inverted) {
        for (i = 0; i < 5; i++)
            edge[i] = (int32_t)(0u - (uint32_t)edge[i]);
    }
    if (!(edge[2] <= edge[3] && edge[3] <= edge[4]))
        return 0;
    int32_t threshold_high = (int32_t)(
        (uint32_t)pr_pres.min_hold + 2u * (uint32_t)pr_pres.add_hold);
    int32_t threshold_mid = (int32_t)(
        (uint32_t)pr_pres.min_hold + (uint32_t)pr_pres.add_hold);
    if (edge[4] < threshold_high
        || edge[3] < threshold_mid
        || edge[2] < pr_pres.min_hold)
        return 0;
    // The factory only normalizes the five-point edge above.  Its history
    // guard compares the original signed filtered[0..60] values even on a
    // falling edge; negating this history rejects valid negative excursions.
    for (i = 0; i <= 60; i++) {
        if (edge[2] < filtered[i])
            return 0;
    }
    pr_pres.tri_chxs |= 1 << channel;
    return pr_pres.tri_chxs;
}

static void
pr_send_pres_batch(uint_fast8_t channel, uint_fast8_t tick_len,
                   uint_fast8_t data_len)
{
    uint8_t *ticks = pr_zip_read(&pr_pres.zip_tick[channel]);
    uint8_t *data = pr_zip_read(&pr_pres.zip_data[channel]);
    sendf("resault_prtouch_pres oid=%c tri_chxs=%c buf_len=%c ch=%c idx=%c len=%c ticks=%.*s datas=%.*s",
          pr_pres.oid, pr_pres.tri_chxs, PR_SAMPLES - 1, channel, 0, 0,
          tick_len, ticks, data_len, data);
}

static void
pr_send_apax_batch(uint_fast8_t channel, uint_fast8_t tick_len,
                   uint_fast8_t data_len, uint_fast8_t interval_len)
{
    uint_fast8_t count = pr_apax.zip_tick[channel].item_count;
    uint8_t *ticks = pr_zip_read(&pr_apax.zip_tick[channel]);
    uint8_t *data = pr_zip_read(&pr_apax.zip_data[channel]);
    uint8_t *intervals = pr_zip_read(&pr_apax.zip_interval[channel]);
    sendf("resault_prtouch_apax oid=%c ch=%c len=%c ticks=%.*s datas=%.*s espds=%.*s",
          pr_apax.oid, channel, count, tick_len, ticks,
          data_len, data, interval_len, intervals);
}

static void
pr_reset_pressure_buffers(void)
{
    memset(&pr_pres.buffer, 0, sizeof(pr_pres.buffer));
}

void
command_config_prtouch_pres(uint32_t *args)
{
    uint_fast8_t index = args[1];
    pr_pres.oid = args[0];
    // V57 assigns idx+1 and indexes the four fixed rows directly, without an
    // explicit bounds check.  Valid host configurations enumerate 0..1.
    pr_pres.sensor_count = index + 1;
    pr_pres.swp_in = gpio_in_setup(args[2], 0);
    pr_pres.swp_out = gpio_out_setup(args[2], 1);
    pr_pres.use_adcx = args[3] == args[4];
    if (pr_pres.use_adcx)
        pr_pres.adc_pin[index] = gpio_adc_setup(args[3]);
    else {
        pr_pres.clk_pin[index] = gpio_out_setup(args[3], 1);
        pr_pres.sdo_pin_number[index] = args[4];
        pr_pres.sdi_pin[index] = gpio_in_setup(args[4], 1);
    }
    pr_ack(pr_pres.oid, 0, 0, 0);
}
DECL_COMMAND(command_config_prtouch_pres,
             "config_prtouch_pres oid=%c idx=%c swp_pin=%u clk_pin=%u sdo_pin=%u");

void
command_start_prtouch_pres(uint32_t *args)
{
    pr_pres.cfg_regs = args[1];
    pr_pres.acq_tick = args[2];
    pr_pres.ned_tftr = args[3];
    pr_pres.ned_hftr = args[4];
    pr_pres.lowpass = args[5] / 1000.f;
    pr_pres.min_hold = args[6];
    pr_pres.max_hold = args[7];
    pr_pres.add_hold = args[8];
    pr_pres.lmt_dead = args[9] < PR_SAMPLES ? PR_SAMPLES : args[9];
    pr_pres.tri_chxs = 0;
    pr_pres.delay_tick = timer_read_time();
    uint_fast8_t i;
    for (i = 0; i < PR_CHANNELS; i++) {
        pr_zip_reset(&pr_pres.zip_tick[i]);
        pr_zip_reset(&pr_pres.zip_data[i]);
    }
    pr_reset_pressure_buffers();
    for (i = 0; i < pr_pres.sensor_count; i++)
        pr_pres.buffer.ready_tick[i] = timer_read_time();
    gpio_out_write(pr_pres.swp_out, 1);
    pr_ack(pr_pres.oid, 0, PR_SAMPLES, 0);
}
DECL_COMMAND(command_start_prtouch_pres,
             "start_prtouch_pres oid=%c cfg_regs=%c acq_tick=%u ned_tftr=%c ned_hftr=%c ned_lftr=%u min_hold=%i max_hold=%i add_hold=%i lmt_dead=%u");

void
command_stop_prtouch_pres(uint32_t *args)
{
    pr_pres.acq_tick = 0;
    pr_ack(pr_pres.oid, 0, 0, PR_VERSION);
    gpio_out_write(pr_pres.swp_out, args[1]);
}
DECL_COMMAND(command_stop_prtouch_pres, "stop_prtouch_pres oid=%c sta_swap=%c");

void
command_read_prtouch_pres(uint32_t *args)
{
    uint_fast8_t channel = args[2];
    // V71 rejects is_src values above one, but does not bounds-check ch.
    if (args[1] > 1) {
        sendf("resault_prtouch_pres oid=%c tri_chxs=%c buf_len=%c ch=%c idx=%c len=%c ticks=%.*s datas=%.*s",
              pr_pres.oid, pr_pres.tri_chxs, PR_SAMPLES, 0, 0, 0,
              0, "", 0, "");
        return;
    }
    uint32_t index = args[3];
    uint32_t end = index + args[4];
    if (end >= PR_SAMPLES)
        end = PR_SAMPLES;
    const int32_t *source = args[1] ? pr_pres.buffer.raw[channel]
                                    : pr_pres.buffer.filtered[channel];
    uint_fast8_t tick_len, data_len;
    uint_fast8_t response_count = pr_zip_pair_range(
        &pr_read_pres_zip_tick, &pr_read_pres_zip_data,
        pr_pres.buffer.ticks[channel], source, index, end,
        &tick_len, &data_len);
    uint8_t *ticks = pr_zip_read(&pr_read_pres_zip_tick);
    uint8_t *data = pr_zip_read(&pr_read_pres_zip_data);
    sendf("resault_prtouch_pres oid=%c tri_chxs=%c buf_len=%c ch=%c idx=%c len=%c ticks=%.*s datas=%.*s",
          pr_pres.oid, pr_pres.tri_chxs, PR_SAMPLES, channel, index,
          response_count, tick_len, ticks, data_len, data);
}
DECL_COMMAND(command_read_prtouch_pres,
             "read_prtouch_pres oid=%c is_src=%c ch=%c idx=%c len=%c");

void
command_config_prtouch_step(uint32_t *args)
{
    pr_step.oid = args[0];
    pr_step.oid_x = args[1];
    pr_step.oid_y = args[2];
    pr_step.oid_z = args[3];
    pr_step.swp = gpio_in_setup(args[4], 1);
    pr_ack(pr_step.oid, 0, 0, 0);
}
DECL_COMMAND(command_config_prtouch_step,
             "config_prtouch_step oid=%c oid_xstp=%u oid_ystp=%u oid_zstp=%u swp_pin=%u");

void
command_start_prtouch_step(uint32_t *args)
{
    pr_step.acq_tick = args[1];
    memset(pr_step.ticks, 0, sizeof(pr_step.ticks));
    memset(pr_step.zpos, 0, sizeof(pr_step.zpos));
    pr_step.delay_tick = timer_read_time();
    pr_ack(pr_step.oid, 0, 0, 0);
}
DECL_COMMAND(command_start_prtouch_step, "start_prtouch_step oid=%c aqc_tick=%u");

void
command_stop_prtouch_step(uint32_t *args)
{
    (void)args;
    pr_step.acq_tick = 0;
    pr_ack(pr_step.oid, 0, gpio_in_read(pr_step.swp), PR_VERSION);
}
DECL_COMMAND(command_stop_prtouch_step, "stop_prtouch_step oid=%c");

void
command_cont_prtouch_step(uint32_t *args)
{
    (void)args;
    sendf("resault_prtouch_step_cnt oid=%c cnt_x=%i cnt_y=%i cnt_z=%i",
          pr_step.oid, step_prtouch_get_cnt(pr_step.oid_x),
          step_prtouch_get_cnt(pr_step.oid_y),
          step_prtouch_get_cnt(pr_step.oid_z));
}
DECL_COMMAND(command_cont_prtouch_step, "cont_prtouch_step oid=%c");

void
command_read_prtouch_step(uint32_t *args)
{
    uint32_t index = args[1];
    uint32_t end = index + args[2];
    if (end >= PR_SAMPLES)
        end = PR_SAMPLES;
    uint_fast8_t tick_len, data_len;
    uint_fast8_t response_count = pr_zip_pair_range(
        &pr_read_step_zip_tick, &pr_read_step_zip_data,
        pr_step.ticks, pr_step.zpos, index, end, &tick_len, &data_len);
    uint8_t *ticks = pr_zip_read(&pr_read_step_zip_tick);
    uint8_t *data = pr_zip_read(&pr_read_step_zip_data);
    sendf("resault_prtouch_step oid=%c buf_len=%c idx=%c len=%c ticks=%.*s datas=%.*s",
          pr_step.oid, PR_SAMPLES, index, response_count,
          tick_len, ticks, data_len, data);
}
DECL_COMMAND(command_read_prtouch_step, "read_prtouch_step oid=%c idx=%c len=%c");

void
command_config_prtouch_apax(uint32_t *args)
{
    pr_apax.oid = args[0];
    pr_apax.oid_estp = args[1];
}
DECL_COMMAND(command_config_prtouch_apax, "config_prtouch_apax oid=%c oid_estp=%c");

void
command_start_prtouch_apax(uint32_t *args)
{
    uint_fast8_t i;
    for (i = 0; i < PR_CHANNELS; i++) {
        pr_zip_reset(&pr_apax.zip_tick[i]);
        pr_zip_reset(&pr_apax.zip_data[i]);
        pr_zip_reset(&pr_apax.zip_interval[i]);
    }
    pr_reset_pressure_buffers();
    pr_apax.delay_tick = timer_read_time();
    pr_apax.cfg_regs = args[1];
    pr_apax.acq_tick = args[2];
    pr_ack(pr_apax.oid, 0, 0, pr_apax.acq_tick);
}
DECL_COMMAND(command_start_prtouch_apax,
             "start_prtouch_apax oid=%c cfg_regs=%c acq_tick=%u");

void
command_stop_prtouch_apax(uint32_t *args)
{
    (void)args;
    pr_apax.acq_tick = 0;
    pr_ack(pr_apax.oid, 0, 0, 0);
}
DECL_COMMAND(command_stop_prtouch_apax, "stop_prtouch_apax oid=%c");

void
prtouch_pres_task(void)
{
    if (!pr_pres.acq_tick)
        return;
    if (pr_pres.use_adcx
        && !pr_delay_due(pr_pres.acq_tick, &pr_pres.delay_tick))
        return;

    // V71 performs this additional timer read after the optional ADC delay
    // gate, before testing sensor_count and before the per-channel timestamp.
    // Its result is unused, but retaining the call preserves task timing.
    (void)timer_read_time();

    uint_fast8_t channel;
    uint8_t triggered_channels = 0;
    for (channel = 0; channel < pr_pres.sensor_count; channel++) {
        if (!pr_pres.acq_tick)
            break;
        uint32_t tick = timer_read_time();
        int32_t value;
        if (!pr_pres.use_adcx
            && pr_pres.buffer.cfg_value[channel] != pr_pres.cfg_regs) {
            if (pr_pres.buffer.cfg_state[channel] & 1)
                value = pr_cs_read_cfg(
                    channel, &pr_pres.buffer.cfg_value[channel]);
            else
                value = pr_cs_write_cfg(channel, pr_pres.cfg_regs);
            // V71 advances on every non-zero return, including PR_INVALID.
            if (value)
                pr_pres.buffer.cfg_state[channel]++;
        } else {
            value = pr_channel_read(channel);
        }
        if (value == PR_INVALID)
            continue;

        if (pr_pres.min_hold == pr_pres.max_hold) {
            uint_fast8_t tick_len = pr_zip_write(
                &pr_pres.zip_tick[channel], tick);
            uint_fast8_t data_len = pr_zip_write(
                &pr_pres.zip_data[channel], value);
            if (tick_len + data_len > PR_ZIP_LIMIT)
                pr_send_pres_batch(channel, tick_len, data_len);
        } else {
            triggered_channels |= pr_filter_and_trigger(channel, tick, value);
        }
    }
    if (pr_pres.min_hold != pr_pres.max_hold && triggered_channels) {
        pr_pres.acq_tick = 0;
        gpio_out_write(pr_pres.swp_out, 0);
    }
}

void PR_FACTORY_LOOP
prtouch_step_task(void)
{
    if (!pr_step.acq_tick)
        return;
    // Periodic history samples are taken regardless of the swap input.  When
    // the period is not yet due, a low swap input forces one immediate final
    // sample.  V71 then reads the input again and stops only if it remains
    // low.  The short-circuit is intentional: the first GPIO read does not
    // occur on the normal periodic path.
    if (!pr_delay_due(pr_step.acq_tick, &pr_step.delay_tick)
        && gpio_in_read(pr_step.swp))
        return;
    uint_fast8_t i;
    for (i = 0; i < PR_SAMPLES - 1; i++) {
        pr_step.ticks[i] = pr_step.ticks[i + 1];
        pr_step.zpos[i] = pr_step.zpos[i + 1];
    }
    pr_step.ticks[PR_SAMPLES - 1] = timer_read_time();
    pr_step.zpos[PR_SAMPLES - 1]
        = step_prtouch_get_pos_v65(pr_step.oid_z);
    uint_fast8_t swap_state = gpio_in_read(pr_step.swp);
    if (!swap_state)
        pr_step.acq_tick = swap_state;
}

void
prtouch_apax_task(void)
{
    if (!pr_apax.acq_tick)
        return;
    if (pr_pres.use_adcx
        && !pr_delay_due(pr_apax.acq_tick, &pr_apax.delay_tick))
        return;

    uint_fast8_t channel;
    for (channel = 0; channel < pr_pres.sensor_count; channel++) {
        uint32_t tick = timer_read_time();
        int32_t value;
        if (!pr_pres.use_adcx
            && pr_pres.buffer.cfg_value[channel] != pr_apax.cfg_regs) {
            if (pr_pres.buffer.cfg_state[channel] & 1)
                value = pr_cs_read_cfg(
                    channel, &pr_pres.buffer.cfg_value[channel]);
            else
                value = pr_cs_write_cfg(channel, pr_apax.cfg_regs);
            if (value)
                pr_pres.buffer.cfg_state[channel]++;
        } else {
            value = pr_channel_read(channel);
        }
        if (value == PR_INVALID)
            continue;
        uint_fast8_t tick_len = pr_zip_write(
            &pr_apax.zip_tick[channel], tick);
        uint_fast8_t data_len = pr_zip_write(
            &pr_apax.zip_data[channel], value);
        uint_fast8_t interval_len = pr_zip_write(
            &pr_apax.zip_interval[channel],
            step_prtouch_get_ivt(pr_apax.oid_estp));
        if (tick_len + data_len + interval_len > PR_ZIP_LIMIT)
            pr_send_apax_batch(channel, tick_len, data_len, interval_len);
    }
}

void
prtouch_task(void)
{
    prtouch_pres_task();
    prtouch_step_task();
    prtouch_apax_task();
}
