#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "creality_485_codec.h"

int
main(void)
{
    static const uint8_t factory_frame[] = {
        0xf7, 0xeb, 0x03, 0xff, 0x56, 0xcf
    };
    assert(creality_485_crc8(&factory_frame[2], 3) == 0xcf);

    uint8_t encoded[16];
    int len = creality_485_encode(0xeb, 0xff, 0x56, 0, 0,
                                  encoded, sizeof(encoded));
    assert(len == sizeof(factory_frame));
    assert(!memcmp(encoded, factory_frame, sizeof(factory_frame)));

    uint16_t consumed;
    uint8_t addr, state, function, payload_len;
    const uint8_t *payload;
    assert(creality_485_parse(encoded, len, &consumed, &addr, &state,
                              &function, &payload, &payload_len)
           == CREALITY_485_PARSE_OK);
    assert(consumed == sizeof(factory_frame));
    assert(addr == 0xeb && state == 0xff && function == 0x56);
    assert(payload_len == 0);

    encoded[5] ^= 1;
    assert(creality_485_parse(encoded, len, &consumed, &addr, &state,
                              &function, &payload, &payload_len)
           == CREALITY_485_PARSE_BAD_CRC);
    return 0;
}
