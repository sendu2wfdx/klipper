// Desktop validation transport for the recovered Creality F7 slave protocol.
// USB remains Klipper's console; USART0 PA9/PA10 is an independent 230400 8N1
// TTL port. An external RS-485 transceiver and DE/RE control are deliberately
// not implied by this driver.
#include <string.h>
#include "autoconf.h"
#include "board/armcm_boot.h"
#include "board/irq.h"
#include "command.h"
#include "creality_485_slave.h"
#include "internal.h"
#include "sched.h"

#define RX_RING_SIZE 512
#define TX_RING_SIZE 512
#define RING_MASK (RX_RING_SIZE - 1)
#define MAX_FRAME_SIZE 258

DECL_CONSTANT_STR("RESERVE_PINS_creality_485_uart", "PA9,PA10");

static struct {
    volatile uint16_t rx_head, rx_tail;
    volatile uint16_t tx_head, tx_tail;
    uint8_t rx_ring[RX_RING_SIZE];
    uint8_t tx_ring[TX_RING_SIZE];
    uint8_t frame[MAX_FRAME_SIZE];
    uint16_t frame_len, expected_len;
    uint32_t rx_frames, tx_frames, invalid_frames, dropped_bytes;
    struct task_wake wake;
    struct creality_485_slave slave;
} Uart485;

static void
uart485_queue_response(const uint8_t *data, uint16_t length)
{
    irqstatus_t flags = irq_save();
    uint16_t free_space = (Uart485.tx_tail - Uart485.tx_head - 1) & RING_MASK;
    if (length > free_space) {
        Uart485.dropped_bytes += length;
        irq_restore(flags);
        return;
    }
    for (uint16_t i = 0; i < length; i++) {
        Uart485.tx_ring[Uart485.tx_head] = data[i];
        Uart485.tx_head = (Uart485.tx_head + 1) & RING_MASK;
    }
    USART_REG_VAL(USART0, USART_INT_TBE) |= BIT(USART_BIT_POS(USART_INT_TBE));
    irq_restore(flags);
    Uart485.tx_frames++;
}

void
USART0_IRQHandler(void)
{
    if (USART_REG_VAL2(USART0, USART_INT_FLAG_RBNE)
        & BIT(USART_BIT_POS2(USART_INT_FLAG_RBNE))) {
        uint8_t data = GET_BITS(USART_DATA(USART0), 0U, 8U);
        uint16_t next = (Uart485.rx_head + 1) & RING_MASK;
        if (next == Uart485.rx_tail)
            Uart485.dropped_bytes++;
        else {
            Uart485.rx_ring[Uart485.rx_head] = data;
            Uart485.rx_head = next;
            sched_wake_task(&Uart485.wake);
        }
    }
    if (USART_REG_VAL2(USART0, USART_INT_FLAG_TBE)
        & BIT(USART_BIT_POS2(USART_INT_FLAG_TBE))) {
        if (Uart485.tx_tail == Uart485.tx_head)
            USART_REG_VAL(USART0, USART_INT_TBE)
                &= ~BIT(USART_BIT_POS(USART_INT_TBE));
        else {
            USART_DATA(USART0) = USART_DATA_DATA
                & Uart485.tx_ring[Uart485.tx_tail];
            Uart485.tx_tail = (Uart485.tx_tail + 1) & RING_MASK;
        }
    }
}

static void
uart485_reset_frame(void)
{
    Uart485.frame_len = 0;
    Uart485.expected_len = 0;
}

static void
uart485_feed(uint8_t data)
{
    if (!Uart485.frame_len) {
        if (data != 0xf7) {
            Uart485.dropped_bytes++;
            return;
        }
        Uart485.frame[Uart485.frame_len++] = data;
        return;
    }
    if (Uart485.frame_len >= sizeof(Uart485.frame)) {
        Uart485.invalid_frames++;
        uart485_reset_frame();
        return;
    }
    Uart485.frame[Uart485.frame_len++] = data;
    if (Uart485.frame_len == 3) {
        if (Uart485.frame[2] < 3) {
            Uart485.invalid_frames++;
            uart485_reset_frame();
            return;
        }
        Uart485.expected_len = Uart485.frame[2] + 3;
    }
    if (!Uart485.expected_len || Uart485.frame_len < Uart485.expected_len)
        return;

    uint8_t response[MAX_FRAME_SIZE];
    int result = creality_485_slave_handle(&Uart485.slave, Uart485.frame,
                                            Uart485.frame_len, response,
                                            sizeof(response));
    if (result > 0) {
        Uart485.rx_frames++;
        uart485_queue_response(response, result);
    } else if (result < 0) {
        Uart485.invalid_frames++;
    } else {
        Uart485.rx_frames++;
    }
    uart485_reset_frame();
}

void
creality_485_uart_task(void)
{
    if (!sched_check_wake(&Uart485.wake))
        return;
    while (Uart485.rx_tail != Uart485.rx_head) {
        uint8_t data = Uart485.rx_ring[Uart485.rx_tail];
        Uart485.rx_tail = (Uart485.rx_tail + 1) & RING_MASK;
        uart485_feed(data);
    }
}
DECL_TASK(creality_485_uart_task);

// Read-only counters intentionally stay available in test and integration
// images so physical-link failures remain observable over the independent
// Klipper console, including after a shutdown.
void
command_query_creality_485_uart(uint32_t *args)
{
    (void)args;
    sendf("creality_485_uart_status rx_frames=%u tx_frames=%u",
          Uart485.rx_frames, Uart485.tx_frames);
}
DECL_COMMAND_FLAGS(command_query_creality_485_uart, HF_IN_SHUTDOWN,
                   "query_creality_485_uart");

void
command_query_creality_485_uart_errors(uint32_t *args)
{
    (void)args;
    sendf("creality_485_uart_errors invalid=%u dropped=%u",
          Uart485.invalid_frames, Uart485.dropped_bytes);
}
DECL_COMMAND_FLAGS(command_query_creality_485_uart_errors, HF_IN_SHUTDOWN,
                   "query_creality_485_uart_errors");

void
creality_485_uart_init(void)
{
    static const uint8_t uuid[12] = "GD32F303CCT6";
    static const uint8_t version[25] = "test_485_001-test_485_001";
    struct creality_485_slave_config config = {
        .group_address = 0xfd,
        .initial_address = 0,
        .device_type = 2,
        .initial_mode = CREALITY_485_MODE_APP,
        .sector_code = 2,
        .upgrade_enabled = 0,
    };
    memcpy(config.uuid, uuid, sizeof(uuid));
    memcpy(config.version, version, sizeof(version));
    creality_485_slave_init(&Uart485.slave, &config);

    gpio_peripheral(GPIO('A', 10), 1, 3);
    gpio_peripheral(GPIO('A', 9), 3, 1);
    enable_pclock(RCU_USART0);
    USART_CTL0(USART0) &= ~USART_CTL0_UEN;
    USART_CTL0(USART0) = (USART_CTL0(USART0)
                          & ~(USART_CTL0_WL | USART_CTL0_PM | USART_CTL0_PCEN))
                         | USART_WL_8BIT | USART_PM_NONE;
    USART_CTL1(USART0) = (USART_CTL1(USART0) & ~USART_CTL1_STB)
                         | USART_STB_1BIT;
    uint32_t divider = (get_pclock_frequency(0)
                        + CONFIG_GD32_CREALITY_485_BAUD / 2)
                       / CONFIG_GD32_CREALITY_485_BAUD;
    USART_BAUD(USART0) = divider & (USART_BAUD_FRADIV | USART_BAUD_INTDIV);
    USART_CTL0(USART0) |= USART_RECEIVE_ENABLE | USART_TRANSMIT_ENABLE
                          | USART_CTL0_UEN;
    armcm_enable_irq(USART0_IRQHandler, USART0_IRQn, 0);
    USART_REG_VAL(USART0, USART_INT_RBNE)
        |= BIT(USART_BIT_POS(USART_INT_RBNE));
}
DECL_INIT(creality_485_uart_init);
