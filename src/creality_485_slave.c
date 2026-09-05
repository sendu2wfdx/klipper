// Verified portion of the Creality F7 RS-485 slave state machine.
#include <string.h>
#include "creality_485_codec.h"
#include "creality_485_slave.h"

static int
reply(struct creality_485_slave *slave, uint8_t state, uint8_t function,
      const uint8_t *payload, uint8_t payload_len,
      uint8_t *response, uint16_t response_size)
{
    uint8_t address = slave->address;
    if (!address)
        address = slave->config.group_address;
    return creality_485_encode(address, state, function, payload, payload_len,
                               response, response_size);
}

static int
identity_reply(struct creality_485_slave *slave, uint8_t state,
               uint8_t function, uint8_t *response, uint16_t response_size)
{
    uint8_t payload[2 + CREALITY_485_UUID_SIZE];
    payload[0] = slave->config.device_type;
    payload[1] = slave->mode;
    memcpy(&payload[2], slave->config.uuid, CREALITY_485_UUID_SIZE);
    return reply(slave, state, function, payload, sizeof(payload), response,
                 response_size);
}

void
creality_485_slave_init(struct creality_485_slave *slave,
                        const struct creality_485_slave_config *config)
{
    memset(slave, 0, sizeof(*slave));
    memcpy(&slave->config, config, sizeof(*config));
    slave->address = config->initial_address;
    slave->mode = config->initial_mode;
}

static uint8_t
storage_status(int result, uint8_t success)
{
    if (result == CREALITY_485_STORAGE_BAD_DATA)
        return CREALITY_485_ACK_BAD_DATA;
    if (result)
        return CREALITY_485_ACK_FLASH_ERROR;
    return success;
}

static int
handle_upgrade(struct creality_485_slave *slave, uint8_t state,
               const uint8_t *payload, uint8_t payload_len,
               uint8_t *response, uint16_t response_size)
{
    const struct creality_485_slave_ops *ops = slave->config.ops;
    int result = CREALITY_485_STORAGE_OK;
    uint8_t status;

    if (slave->upgrade_phase == CREALITY_485_UPGRADE_WAIT_LENGTH) {
        if (!slave->config.upgrade_enabled) {
            status = CREALITY_485_ACK_FLASH_ERROR;
            return reply(slave, state, CREALITY_485_FUNC_UPGRADE, &status, 1,
                         response, response_size);
        }
        if (payload_len != 4)
            return 0;
        uint32_t length = (uint32_t)payload[0]
            | (uint32_t)payload[1] << 8 | (uint32_t)payload[2] << 16
            | (uint32_t)payload[3] << 24;
        if (!length || (slave->config.maximum_application_length
                        && length > slave->config.maximum_application_length))
            result = CREALITY_485_STORAGE_BAD_DATA;
        else if (ops && ops->begin_application)
            result = ops->begin_application(slave->config.ops_context, length);
        if (!result) {
            slave->application_length = length;
            slave->application_received = 0;
            slave->upgrade_phase = CREALITY_485_UPGRADE_RECEIVE_DATA;
        }
        status = storage_status(result, CREALITY_485_ACK_CONTINUE);
        return reply(slave, state, CREALITY_485_FUNC_UPGRADE, &status, 1,
                     response, response_size);
    }

    if (slave->upgrade_phase == CREALITY_485_UPGRADE_RECEIVE_DATA) {
        if (!slave->config.upgrade_enabled) {
            status = CREALITY_485_ACK_FLASH_ERROR;
            return reply(slave, state, CREALITY_485_FUNC_UPGRADE, &status, 1,
                         response, response_size);
        }
        uint32_t remaining = slave->application_length
            - slave->application_received;
        if (!payload_len || payload_len > remaining)
            result = CREALITY_485_STORAGE_BAD_DATA;
        uint8_t is_last = !result && payload_len == remaining;
        if (!result && ops && ops->write_application)
            result = ops->write_application(slave->config.ops_context,
                                             slave->application_received,
                                             payload, payload_len, is_last);
        if (!result) {
            slave->application_received += payload_len;
            if (is_last)
                slave->upgrade_phase = CREALITY_485_UPGRADE_COMPLETE;
        }
        status = storage_status(result, is_last ? CREALITY_485_ACK_FINISHED
                                                : CREALITY_485_ACK_CONTINUE);
        return reply(slave, state, CREALITY_485_FUNC_UPGRADE, &status, 1,
                     response, response_size);
    }

    if (payload_len != 1)
        return 0;
    switch (payload[0]) {
    case CREALITY_485_UPGRADE_GET_VERSION:
        return reply(slave, state, CREALITY_485_FUNC_UPGRADE,
                     slave->config.version, CREALITY_485_VERSION_SIZE,
                     response, response_size);
    case CREALITY_485_UPGRADE_GET_SECTOR_SIZE:
        status = slave->config.sector_code;
        return reply(slave, state, CREALITY_485_FUNC_UPGRADE, &status, 1,
                     response, response_size);
    case CREALITY_485_UPGRADE_ERASE_FLASH:
        if (!slave->config.upgrade_enabled) {
            status = CREALITY_485_ACK_FLASH_ERROR;
            return reply(slave, state, CREALITY_485_FUNC_UPGRADE, &status, 1,
                         response, response_size);
        }
        if (ops && ops->erase_application)
            result = ops->erase_application(slave->config.ops_context);
        status = storage_status(result, CREALITY_485_ACK_CONTINUE);
        return reply(slave, state, CREALITY_485_FUNC_UPGRADE, &status, 1,
                     response, response_size);
    case CREALITY_485_UPGRADE_REQUEST:
        if (!slave->config.upgrade_enabled) {
            status = CREALITY_485_ACK_FLASH_ERROR;
            return reply(slave, state, CREALITY_485_FUNC_UPGRADE, &status, 1,
                         response, response_size);
        }
        slave->mode = CREALITY_485_MODE_LOADER;
        slave->upgrade_phase = CREALITY_485_UPGRADE_WAIT_LENGTH;
        status = CREALITY_485_ACK_CONTINUE;
        return reply(slave, state, CREALITY_485_FUNC_UPGRADE, &status, 1,
                     response, response_size);
    case CREALITY_485_UPGRADE_START_APP:
        if (ops && ops->start_application)
            result = ops->start_application(slave->config.ops_context);
        if (!result) {
            slave->mode = CREALITY_485_MODE_APP;
            slave->upgrade_phase = CREALITY_485_UPGRADE_IDLE;
        }
        status = storage_status(result, CREALITY_485_ACK_CONTINUE);
        return reply(slave, state, CREALITY_485_FUNC_UPGRADE, &status, 1,
                     response, response_size);
    default:
        return 0;
    }
}

int
creality_485_slave_handle(struct creality_485_slave *slave,
                          const uint8_t *request, uint16_t request_length,
                          uint8_t *response, uint16_t response_size)
{
    uint16_t consumed;
    uint8_t address, state, function, payload_len;
    const uint8_t *payload;
    int parsed = creality_485_parse(request, request_length, &consumed,
                                    &address, &state, &function,
                                    &payload, &payload_len);
    if (parsed != CREALITY_485_PARSE_OK)
        return parsed;
    if (consumed != request_length)
        return CREALITY_485_PARSE_BAD_LENGTH;

    if (function == CREALITY_485_FUNC_DISCOVER) {
        if (address != slave->config.group_address || payload_len != 2
            || payload[0] != slave->config.group_address
            || payload[1] != slave->config.group_address)
            return 0;
        return identity_reply(slave, state, function, response, response_size);
    }
    if (function == CREALITY_485_FUNC_SET_ADDRESS) {
        if (address != slave->config.group_address || payload_len != 13
            || memcmp(&payload[1], slave->config.uuid,
                      CREALITY_485_UUID_SIZE))
            return 0;
        if (slave->config.ops && slave->config.ops->set_address
            && slave->config.ops->set_address(slave->config.ops_context,
                                              payload[0]))
            return 0;
        slave->address = payload[0];
        return identity_reply(slave, state, function, response, response_size);
    }
    if (function == CREALITY_485_FUNC_LOADER_TO_APP) {
        if (address != 0xff || payload_len != 1 || payload[0] != 1
            || slave->mode != CREALITY_485_MODE_LOADER)
            return 0;
        if (slave->config.ops && slave->config.ops->start_application)
            slave->config.ops->start_application(slave->config.ops_context);
        slave->mode = CREALITY_485_MODE_APP;
        slave->upgrade_phase = CREALITY_485_UPGRADE_IDLE;
        return 0;
    }
    if (!slave->address || address != slave->address)
        return 0;
    if ((function == CREALITY_485_FUNC_ONLINE_CHECK
         || function == CREALITY_485_FUNC_ADDRESS_TABLE)
        && !payload_len)
        return identity_reply(slave, state, function, response, response_size);
    if (function == CREALITY_485_FUNC_UPGRADE)
        return handle_upgrade(slave, state, payload, payload_len,
                              response, response_size);
    return 0;
}
