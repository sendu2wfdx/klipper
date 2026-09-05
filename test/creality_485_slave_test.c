#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "creality_485_codec.h"
#include "creality_485_slave.h"

struct storage {
    uint8_t data[16];
    uint32_t length;
    uint32_t last_offset;
    uint8_t last_length;
    uint8_t last_is_final;
    uint8_t address;
    uint8_t set_address_calls;
    uint8_t erase_calls;
    uint8_t begin_calls;
    uint8_t write_calls;
    uint8_t start_calls;
    int set_address_result;
    int erase_result;
    int begin_result;
    int write_result;
    int start_result;
};

static const uint8_t test_uuid[CREALITY_485_UUID_SIZE] = {
    0x00, 0x11, 0x22, 0x33, 0x44, 0x55,
    0x66, 0x77, 0x88, 0x99, 0xaa, 0xbb,
};
static const uint8_t test_version[CREALITY_485_VERSION_SIZE] =
    "motor_001_00-motor_002_00";

static int
set_address(void *ctx, uint8_t address)
{
    struct storage *storage = ctx;
    storage->set_address_calls++;
    storage->address = address;
    return storage->set_address_result;
}

static int
erase_application(void *ctx)
{
    struct storage *storage = ctx;
    storage->erase_calls++;
    return storage->erase_result;
}

static int
begin_application(void *ctx, uint32_t length)
{
    struct storage *storage = ctx;
    storage->begin_calls++;
    storage->length = length;
    return storage->begin_result;
}

static int
write_application(void *ctx, uint32_t offset,
                  const uint8_t *data, uint8_t length, uint8_t is_last)
{
    struct storage *storage = ctx;
    storage->write_calls++;
    storage->last_offset = offset;
    storage->last_length = length;
    storage->last_is_final = is_last;
    if (storage->write_result)
        return storage->write_result;
    assert(offset + length <= sizeof(storage->data));
    memcpy(&storage->data[offset], data, length);
    return 0;
}

static int
start_application(void *ctx)
{
    struct storage *storage = ctx;
    storage->start_calls++;
    return storage->start_result;
}

static const struct creality_485_slave_ops test_ops = {
    .set_address = set_address,
    .erase_application = erase_application,
    .begin_application = begin_application,
    .write_application = write_application,
    .start_application = start_application,
};
static const struct creality_485_slave_ops empty_ops;

static void
init_slave(struct creality_485_slave *slave, struct storage *storage,
           uint8_t upgrade_enabled, uint8_t initial_mode)
{
    struct creality_485_slave_config config = {
        .group_address = 0xfd,
        .device_type = 2,
        .initial_mode = initial_mode,
        .sector_code = 2,
        .upgrade_enabled = upgrade_enabled,
        .maximum_application_length = sizeof(storage->data),
        .ops = &test_ops,
        .ops_context = storage,
    };
    memcpy(config.uuid, test_uuid, sizeof(test_uuid));
    memcpy(config.version, test_version, sizeof(test_version));
    creality_485_slave_init(slave, &config);
}

static int
transact_state(struct creality_485_slave *slave, uint8_t address,
               uint8_t state, uint8_t function, const uint8_t *payload,
               uint8_t payload_len, uint8_t *response, uint16_t response_size)
{
    uint8_t request[258];
    int request_len = creality_485_encode(address, state, function, payload,
                                           payload_len, request,
                                           sizeof(request));
    assert(request_len > 0);
    return creality_485_slave_handle(slave, request, request_len,
                                     response, response_size);
}

static int
transact(struct creality_485_slave *slave, uint8_t address,
         uint8_t function, const uint8_t *payload, uint8_t payload_len,
         uint8_t *response)
{
    return transact_state(slave, address, 0, function, payload, payload_len,
                          response, 258);
}

static const uint8_t *
response_payload(const uint8_t *frame, int frame_len, uint8_t address,
                 uint8_t state, uint8_t function, uint8_t expected_len)
{
    uint16_t consumed;
    uint8_t got_address, got_state, got_function, payload_len;
    const uint8_t *payload;
    assert(frame_len > 0);
    assert(creality_485_parse(frame, frame_len, &consumed, &got_address,
                              &got_state, &got_function, &payload,
                              &payload_len) == CREALITY_485_PARSE_OK);
    assert(consumed == frame_len);
    assert(got_address == address);
    assert(got_state == state);
    assert(got_function == function);
    assert(payload_len == expected_len);
    return payload;
}

static void
check_identity(const uint8_t *frame, int frame_len, uint8_t address,
               uint8_t state, uint8_t function, uint8_t mode)
{
    const uint8_t *payload = response_payload(frame, frame_len, address,
                                               state, function, 14);
    assert(payload[0] == 2 && payload[1] == mode);
    assert(!memcmp(&payload[2], test_uuid, sizeof(test_uuid)));
}

static uint8_t
response_byte(const uint8_t *frame, int frame_len, uint8_t state)
{
    return response_payload(frame, frame_len, 0x85, state,
                            CREALITY_485_FUNC_UPGRADE, 1)[0];
}

static void
assign_address(struct creality_485_slave *slave, struct storage *storage,
               uint8_t *response)
{
    uint8_t payload[13] = { 0x85 };
    memcpy(&payload[1], test_uuid, sizeof(test_uuid));
    int len = transact(slave, 0xfd, CREALITY_485_FUNC_SET_ADDRESS,
                       payload, sizeof(payload), response);
    assert(storage->set_address_calls == 1);
    assert(storage->address == 0x85 && slave->address == 0x85);
    check_identity(response, len, 0x85, 0,
                   CREALITY_485_FUNC_SET_ADDRESS, slave->mode);
}

static void
test_discovery_and_addressing(void)
{
    struct storage storage = {0};
    struct creality_485_slave slave;
    uint8_t response[258], payload[16];
    init_slave(&slave, &storage, 1, CREALITY_485_MODE_APP);

    payload[0] = payload[1] = 0xfd;
    int len = transact_state(&slave, 0xfd, 0x34,
                             CREALITY_485_FUNC_DISCOVER, payload, 2,
                             response, sizeof(response));
    check_identity(response, len, 0xfd, 0x34,
                   CREALITY_485_FUNC_DISCOVER, CREALITY_485_MODE_APP);

    payload[0] = 0xfc;
    assert(transact(&slave, 0xfd, CREALITY_485_FUNC_DISCOVER,
                    payload, 2, response) == 0);
    payload[0] = payload[1] = 0xfd;
    assert(transact(&slave, 0xfc, CREALITY_485_FUNC_DISCOVER,
                    payload, 2, response) == 0);
    assert(transact(&slave, 0xfd, CREALITY_485_FUNC_DISCOVER,
                    payload, 1, response) == 0);

    payload[0] = 0x85;
    memset(&payload[1], 0xee, CREALITY_485_UUID_SIZE);
    assert(transact(&slave, 0xfd, CREALITY_485_FUNC_SET_ADDRESS,
                    payload, 13, response) == 0);
    assert(slave.address == 0 && storage.set_address_calls == 0);

    memcpy(&payload[1], test_uuid, sizeof(test_uuid));
    storage.set_address_result = CREALITY_485_STORAGE_FLASH_ERROR;
    assert(transact(&slave, 0xfd, CREALITY_485_FUNC_SET_ADDRESS,
                    payload, 13, response) == 0);
    assert(slave.address == 0 && storage.set_address_calls == 1);
    storage.set_address_result = 0;
    storage.set_address_calls = 0;
    assign_address(&slave, &storage, response);

    len = transact_state(&slave, 0x85, 0x77,
                         CREALITY_485_FUNC_ONLINE_CHECK, 0, 0,
                         response, sizeof(response));
    check_identity(response, len, 0x85, 0x77,
                   CREALITY_485_FUNC_ONLINE_CHECK, CREALITY_485_MODE_APP);
    len = transact(&slave, 0x85, CREALITY_485_FUNC_ADDRESS_TABLE,
                   0, 0, response);
    check_identity(response, len, 0x85, 0,
                   CREALITY_485_FUNC_ADDRESS_TABLE, CREALITY_485_MODE_APP);
    assert(transact(&slave, 0x84, CREALITY_485_FUNC_ONLINE_CHECK,
                    0, 0, response) == 0);
    payload[0] = 0;
    assert(transact(&slave, 0x85, CREALITY_485_FUNC_ADDRESS_TABLE,
                    payload, 1, response) == 0);
}

static void
test_upgrade_happy_path(void)
{
    struct storage storage = {0};
    struct creality_485_slave slave;
    uint8_t response[258], payload[16];
    init_slave(&slave, &storage, 1, CREALITY_485_MODE_APP);
    assign_address(&slave, &storage, response);

    payload[0] = CREALITY_485_UPGRADE_GET_VERSION;
    int len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                       payload, 1, response);
    assert(!memcmp(response_payload(response, len, 0x85, 0,
                                    CREALITY_485_FUNC_UPGRADE,
                                    CREALITY_485_VERSION_SIZE),
                   test_version, sizeof(test_version)));

    payload[0] = CREALITY_485_UPGRADE_GET_SECTOR_SIZE;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 1, response);
    assert(response_byte(response, len, 0) == 2);

    payload[0] = CREALITY_485_UPGRADE_ERASE_FLASH;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 1, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_CONTINUE);
    assert(storage.erase_calls == 1);

    payload[0] = CREALITY_485_UPGRADE_REQUEST;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 1, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_CONTINUE);
    assert(slave.mode == CREALITY_485_MODE_LOADER);
    assert(slave.upgrade_phase == CREALITY_485_UPGRADE_WAIT_LENGTH);

    payload[0] = 5;
    payload[1] = payload[2] = payload[3] = 0;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 4, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_CONTINUE);
    assert(storage.begin_calls == 1 && storage.length == 5);
    assert(slave.application_length == 5 && !slave.application_received);

    memcpy(payload, "abc", 3);
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 3, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_CONTINUE);
    assert(storage.last_offset == 0 && storage.last_length == 3);
    assert(!storage.last_is_final && slave.application_received == 3);
    memcpy(payload, "de", 2);
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 2, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_FINISHED);
    assert(storage.last_offset == 3 && storage.last_length == 2);
    assert(storage.last_is_final && slave.application_received == 5);
    assert(slave.upgrade_phase == CREALITY_485_UPGRADE_COMPLETE);
    assert(!memcmp(storage.data, "abcde", 5));

    payload[0] = CREALITY_485_UPGRADE_START_APP;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 1, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_CONTINUE);
    assert(slave.mode == CREALITY_485_MODE_APP);
    assert(slave.upgrade_phase == CREALITY_485_UPGRADE_IDLE);
    assert(storage.start_calls == 1);
}

static void
enter_wait_length(struct creality_485_slave *slave, uint8_t *response)
{
    uint8_t request = CREALITY_485_UPGRADE_REQUEST;
    int len = transact(slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                       &request, 1, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_CONTINUE);
    assert(slave->upgrade_phase == CREALITY_485_UPGRADE_WAIT_LENGTH);
}

static void
test_upgrade_error_paths(void)
{
    struct storage storage = {0};
    struct creality_485_slave slave;
    uint8_t response[258], payload[17] = {0};
    init_slave(&slave, &storage, 1, CREALITY_485_MODE_APP);
    assign_address(&slave, &storage, response);

    payload[0] = CREALITY_485_UPGRADE_ERASE_FLASH;
    storage.erase_result = CREALITY_485_STORAGE_BAD_DATA;
    int len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                       payload, 1, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_BAD_DATA);
    storage.erase_result = CREALITY_485_STORAGE_FLASH_ERROR;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 1, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_FLASH_ERROR);

    payload[0] = 0x7f;
    assert(transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                    payload, 1, response) == 0);
    assert(transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                    payload, 2, response) == 0);

    enter_wait_length(&slave, response);
    assert(transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                    payload, 3, response) == 0);
    memset(payload, 0, 4);
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 4, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_BAD_DATA);
    assert(slave.upgrade_phase == CREALITY_485_UPGRADE_WAIT_LENGTH);

    payload[0] = 17;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 4, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_BAD_DATA);
    storage.begin_result = CREALITY_485_STORAGE_FLASH_ERROR;
    payload[0] = 5;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 4, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_FLASH_ERROR);
    assert(slave.upgrade_phase == CREALITY_485_UPGRADE_WAIT_LENGTH);

    storage.begin_result = 0;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 4, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_CONTINUE);
    assert(slave.upgrade_phase == CREALITY_485_UPGRADE_RECEIVE_DATA);

    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 0, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_BAD_DATA);
    memset(payload, 0x55, 6);
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 6, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_BAD_DATA);
    assert(!slave.application_received && !storage.write_calls);

    storage.write_result = CREALITY_485_STORAGE_BAD_DATA;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 2, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_BAD_DATA);
    assert(!slave.application_received && storage.write_calls == 1);
    storage.write_result = CREALITY_485_STORAGE_FLASH_ERROR;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 2, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_FLASH_ERROR);
    assert(!slave.application_received && storage.write_calls == 2);

    storage.write_result = 0;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 5, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_FINISHED);
    storage.start_result = CREALITY_485_STORAGE_FLASH_ERROR;
    payload[0] = CREALITY_485_UPGRADE_START_APP;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 1, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_FLASH_ERROR);
    assert(slave.mode == CREALITY_485_MODE_LOADER);
    assert(slave.upgrade_phase == CREALITY_485_UPGRADE_COMPLETE);
}

static void
test_upgrade_disabled_and_loader_broadcast(void)
{
    struct storage storage = {0};
    struct creality_485_slave slave;
    uint8_t response[258], payload[4] = {0};
    init_slave(&slave, &storage, 0, CREALITY_485_MODE_APP);
    assign_address(&slave, &storage, response);

    payload[0] = CREALITY_485_UPGRADE_REQUEST;
    int len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                       payload, 1, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_FLASH_ERROR);
    assert(slave.mode == CREALITY_485_MODE_APP);
    payload[0] = CREALITY_485_UPGRADE_ERASE_FLASH;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 1, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_FLASH_ERROR);
    assert(!storage.erase_calls);

    slave.mode = CREALITY_485_MODE_LOADER;
    slave.upgrade_phase = CREALITY_485_UPGRADE_WAIT_LENGTH;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 4, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_FLASH_ERROR);
    slave.upgrade_phase = CREALITY_485_UPGRADE_RECEIVE_DATA;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 1, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_FLASH_ERROR);

    payload[0] = 1;
    assert(transact(&slave, 0xfe, CREALITY_485_FUNC_LOADER_TO_APP,
                    payload, 1, response) == 0);
    assert(slave.mode == CREALITY_485_MODE_LOADER);
    assert(transact(&slave, 0xff, CREALITY_485_FUNC_LOADER_TO_APP,
                    payload, 0, response) == 0);
    assert(slave.mode == CREALITY_485_MODE_LOADER);
    assert(transact(&slave, 0xff, CREALITY_485_FUNC_LOADER_TO_APP,
                    payload, 1, response) == 0);
    assert(slave.mode == CREALITY_485_MODE_APP);
    assert(slave.upgrade_phase == CREALITY_485_UPGRADE_IDLE);
    assert(storage.start_calls == 1);
}

static void
test_framing_guards(void)
{
    struct storage storage = {0};
    struct creality_485_slave slave;
    uint8_t request[258], response[258], payload[2] = { 0xfd, 0xfd };
    init_slave(&slave, &storage, 1, CREALITY_485_MODE_APP);
    int len = creality_485_encode(0xfd, 0, CREALITY_485_FUNC_DISCOVER,
                                  payload, sizeof(payload), request,
                                  sizeof(request));
    assert(len > 0);
    request[len - 1] ^= 0x01;
    assert(creality_485_slave_handle(&slave, request, len, response,
                                     sizeof(response))
           == CREALITY_485_PARSE_BAD_CRC);
    request[len - 1] ^= 0x01;
    request[len] = 0;
    assert(creality_485_slave_handle(&slave, request, len + 1, response,
                                     sizeof(response))
           == CREALITY_485_PARSE_BAD_LENGTH);
    assert(creality_485_slave_handle(&slave, request, 2, response,
                                     sizeof(response))
           == CREALITY_485_PARSE_INCOMPLETE);
    assert(creality_485_slave_handle(&slave, request, len, response, 5)
           == CREALITY_485_PARSE_BAD_LENGTH);
}

static void
test_optional_ops_and_short_circuit_paths(void)
{
    struct storage storage = {0};
    struct creality_485_slave slave;
    uint8_t response[258], payload[16] = {0};
    init_slave(&slave, &storage, 1, CREALITY_485_MODE_APP);
    slave.config.ops = 0;

    payload[0] = 0xfd;
    payload[1] = 0xfc;
    assert(transact(&slave, 0xfd, CREALITY_485_FUNC_DISCOVER,
                    payload, 2, response) == 0);

    payload[0] = 0x85;
    memcpy(&payload[1], test_uuid, sizeof(test_uuid));
    assert(transact(&slave, 0xfc, CREALITY_485_FUNC_SET_ADDRESS,
                    payload, 13, response) == 0);
    assert(transact(&slave, 0xfd, CREALITY_485_FUNC_SET_ADDRESS,
                    payload, 12, response) == 0);
    int len = transact(&slave, 0xfd, CREALITY_485_FUNC_SET_ADDRESS,
                       payload, 13, response);
    assert(slave.address == 0x85 && storage.set_address_calls == 0);
    check_identity(response, len, 0x85, 0,
                   CREALITY_485_FUNC_SET_ADDRESS, CREALITY_485_MODE_APP);

    slave.address = 0;
    assert(transact(&slave, 0x85, CREALITY_485_FUNC_ONLINE_CHECK,
                    0, 0, response) == 0);
    slave.address = 0x85;
    payload[0] = CREALITY_485_UPGRADE_ERASE_FLASH;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 1, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_CONTINUE);
    assert(storage.erase_calls == 0);

    payload[0] = CREALITY_485_UPGRADE_REQUEST;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 1, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_CONTINUE);
    slave.config.maximum_application_length = 0;
    payload[0] = 3;
    payload[1] = payload[2] = payload[3] = 0;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 4, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_CONTINUE);
    assert(storage.begin_calls == 0);

    memcpy(payload, "xyz", 3);
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 3, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_FINISHED);
    assert(storage.write_calls == 0 && slave.application_received == 3);
    payload[0] = CREALITY_485_UPGRADE_START_APP;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 1, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_CONTINUE);
    assert(storage.start_calls == 0 && slave.mode == CREALITY_485_MODE_APP);

    slave.mode = CREALITY_485_MODE_LOADER;
    slave.upgrade_phase = CREALITY_485_UPGRADE_COMPLETE;
    payload[0] = 2;
    assert(transact(&slave, 0xff, CREALITY_485_FUNC_LOADER_TO_APP,
                    payload, 1, response) == 0);
    assert(slave.mode == CREALITY_485_MODE_LOADER);
    payload[0] = 1;
    assert(transact(&slave, 0xff, CREALITY_485_FUNC_LOADER_TO_APP,
                    payload, 1, response) == 0);
    assert(slave.mode == CREALITY_485_MODE_APP);
    assert(slave.upgrade_phase == CREALITY_485_UPGRADE_IDLE);
}

static void
test_null_callback_paths(void)
{
    struct storage storage = {0};
    struct creality_485_slave slave;
    uint8_t response[258], payload[13] = { 0x85 };
    init_slave(&slave, &storage, 1, CREALITY_485_MODE_APP);
    slave.config.ops = &empty_ops;

    memcpy(&payload[1], test_uuid, sizeof(test_uuid));
    int len = transact(&slave, 0xfd, CREALITY_485_FUNC_SET_ADDRESS,
                       payload, 13, response);
    check_identity(response, len, 0x85, 0,
                   CREALITY_485_FUNC_SET_ADDRESS, CREALITY_485_MODE_APP);

    payload[0] = CREALITY_485_UPGRADE_ERASE_FLASH;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 1, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_CONTINUE);
    payload[0] = CREALITY_485_UPGRADE_REQUEST;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 1, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_CONTINUE);
    payload[0] = 2;
    payload[1] = payload[2] = payload[3] = 0;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 4, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_CONTINUE);
    payload[0] = 0xaa;
    payload[1] = 0x55;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 2, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_FINISHED);
    payload[0] = CREALITY_485_UPGRADE_START_APP;
    len = transact(&slave, 0x85, CREALITY_485_FUNC_UPGRADE,
                   payload, 1, response);
    assert(response_byte(response, len, 0) == CREALITY_485_ACK_CONTINUE);

    payload[0] = 1;
    assert(transact(&slave, 0xff, CREALITY_485_FUNC_LOADER_TO_APP,
                    payload, 1, response) == 0);
    assert(slave.mode == CREALITY_485_MODE_APP);
    slave.mode = CREALITY_485_MODE_LOADER;
    assert(transact(&slave, 0xff, CREALITY_485_FUNC_LOADER_TO_APP,
                    payload, 1, response) == 0);
    assert(slave.mode == CREALITY_485_MODE_APP);
}

int
main(void)
{
    test_discovery_and_addressing();
    test_upgrade_happy_path();
    test_upgrade_error_paths();
    test_upgrade_disabled_and_loader_broadcast();
    test_framing_guards();
    test_optional_ops_and_short_circuit_paths();
    test_null_callback_paths();
    return 0;
}
