// Local filtered GPIO input to output interlock
#include "basecmd.h" // oid_alloc
#include "board/gpio.h" // gpio_in_setup
#include "board/misc.h" // timer_from_us
#include "command.h" // DECL_COMMAND
#include "sched.h" // sched_add_timer

struct io_remap {
    struct timer timer;
    struct gpio_in source;
    struct gpio_out output;
    uint32_t period_ticks;
    uint8_t oid, source_active, default_value, filter_count, active_count;
    uint8_t enabled;
};

static uint_fast8_t
io_remap_event(struct timer *timer)
{
    struct io_remap *ir = container_of(timer, struct io_remap, timer);
    uint8_t active = !!gpio_in_read(ir->source) == ir->source_active;
    if (active) {
        if (ir->active_count < ir->filter_count)
            ir->active_count++;
    } else {
        ir->active_count = 0;
    }
    gpio_out_write(ir->output,
                   ir->active_count >= ir->filter_count
                   ? !ir->default_value : ir->default_value);
    if (!ir->enabled)
        return SF_DONE;
    ir->timer.waketime += ir->period_ticks;
    return SF_RESCHEDULE;
}

void
command_config_io_remap(uint32_t *args)
{
    struct io_remap *ir = oid_alloc(args[0], command_config_io_remap,
                                    sizeof(*ir));
    ir->oid = args[0];
    ir->source_active = !args[2];
    ir->source = gpio_in_setup(args[1], !!args[2]);
    ir->default_value = !!args[4];
    ir->output = gpio_out_setup(args[3], ir->default_value);
    ir->timer.func = io_remap_event;
}
DECL_COMMAND(command_config_io_remap,
             "config_io_remap oid=%c src_pin=%c src_pullup=%c"
             " remap_pin=%c default_value=%c");

// Generic forwarding keeps electrical bias independent of logical polarity.
// Both interfaces share the timer, control command and shutdown behavior.
void
command_config_gpio_forward(uint32_t *args)
{
    struct io_remap *ir = oid_alloc(args[0], command_config_io_remap,
                                    sizeof(*ir));
    ir->oid = args[0];
    ir->source = gpio_in_setup(args[1], (int8_t)args[2]);
    ir->source_active = !args[3];
    ir->default_value = !!args[5];
    ir->output = gpio_out_setup(args[4], ir->default_value);
    ir->timer.func = io_remap_event;
}
DECL_COMMAND(command_config_gpio_forward,
             "config_gpio_forward oid=%c input_pin=%u input_pullup=%c"
             " input_invert=%c output_pin=%u output_invert=%c");

void
command_set_io_remap(uint32_t *args)
{
    struct io_remap *ir = oid_lookup(args[0], command_config_io_remap);
    sched_del_timer(&ir->timer);
    ir->enabled = !!args[1];
    ir->filter_count = args[2] ? args[2] : 5;
    ir->period_ticks = args[3] ? args[3] : timer_from_us(50);
    ir->active_count = 0;
    gpio_out_write(ir->output, ir->default_value);
    if (ir->enabled) {
        ir->timer.waketime = timer_read_time() + ir->period_ticks;
        sched_add_timer(&ir->timer);
    }
    sendf("io_remap_state oid=%c enabled=%c", ir->oid, ir->enabled);
}
DECL_COMMAND(command_set_io_remap,
             "set_io_remap oid=%c enabled=%c filter_count=%c"
             " period_ticks=%u");

void
io_remap_shutdown(void)
{
    uint8_t oid;
    struct io_remap *ir;
    foreach_oid(oid, ir, command_config_io_remap) {
        ir->enabled = 0;
        ir->active_count = 0;
        gpio_out_write(ir->output, ir->default_value);
    }
}
DECL_SHUTDOWN(io_remap_shutdown);
