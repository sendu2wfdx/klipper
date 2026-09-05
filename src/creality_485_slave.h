#ifndef __CREALITY_485_SLAVE_H
#define __CREALITY_485_SLAVE_H

#include <stdint.h>

#define CREALITY_485_UUID_SIZE 12
#define CREALITY_485_VERSION_SIZE 25

#define CREALITY_485_FUNC_SET_ADDRESS 0xa0
#define CREALITY_485_FUNC_DISCOVER 0xa1
#define CREALITY_485_FUNC_ONLINE_CHECK 0xa2
#define CREALITY_485_FUNC_ADDRESS_TABLE 0xa3
#define CREALITY_485_FUNC_LOADER_TO_APP 0x0b
#define CREALITY_485_FUNC_UPGRADE 0xf0

#define CREALITY_485_UPGRADE_GET_VERSION 0x00
#define CREALITY_485_UPGRADE_REQUEST 0x01
#define CREALITY_485_UPGRADE_START_APP 0x02
#define CREALITY_485_UPGRADE_GET_SECTOR_SIZE 0x03
#define CREALITY_485_UPGRADE_ERASE_FLASH 0x06

#define CREALITY_485_ACK_CONTINUE 0x75
#define CREALITY_485_ACK_FINISHED 0x20
#define CREALITY_485_ACK_BAD_DATA 0x1f
#define CREALITY_485_ACK_FLASH_ERROR 0x21

enum creality_485_slave_mode {
    CREALITY_485_MODE_APP = 0,
    CREALITY_485_MODE_LOADER = 1,
};

enum creality_485_upgrade_phase {
    CREALITY_485_UPGRADE_IDLE = 0,
    CREALITY_485_UPGRADE_WAIT_LENGTH = 1,
    CREALITY_485_UPGRADE_RECEIVE_DATA = 2,
    CREALITY_485_UPGRADE_COMPLETE = 3,
};

enum creality_485_storage_result {
    CREALITY_485_STORAGE_OK = 0,
    CREALITY_485_STORAGE_BAD_DATA = -1,
    CREALITY_485_STORAGE_FLASH_ERROR = -2,
};

struct creality_485_slave_ops {
    int (*set_address)(void *ctx, uint8_t address);
    int (*erase_application)(void *ctx);
    int (*begin_application)(void *ctx, uint32_t length);
    int (*write_application)(void *ctx, uint32_t offset,
                             const uint8_t *data, uint8_t length,
                             uint8_t is_last);
    int (*start_application)(void *ctx);
};

struct creality_485_slave_config {
    uint8_t group_address;
    uint8_t initial_address;
    uint8_t device_type;
    uint8_t initial_mode;
    uint8_t sector_code;
    uint8_t upgrade_enabled;
    uint32_t maximum_application_length;
    uint8_t uuid[CREALITY_485_UUID_SIZE];
    uint8_t version[CREALITY_485_VERSION_SIZE];
    const struct creality_485_slave_ops *ops;
    void *ops_context;
};

struct creality_485_slave {
    struct creality_485_slave_config config;
    uint8_t address;
    uint8_t mode;
    uint8_t upgrade_phase;
    uint32_t application_length;
    uint32_t application_received;
};

void creality_485_slave_init(struct creality_485_slave *slave,
                             const struct creality_485_slave_config *config);

// Return the encoded response length, zero when the request is intentionally
// ignored, or a negative creality_485_parse_result on malformed input/output.
int creality_485_slave_handle(struct creality_485_slave *slave,
                              const uint8_t *request, uint16_t request_length,
                              uint8_t *response, uint16_t response_size);

#endif
