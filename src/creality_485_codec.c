// Creality RS-485 framing recovered from the Ender-3 V4 factory image.
#include "creality_485_codec.h"

uint8_t
creality_485_crc8(const uint8_t *data, uint16_t len)
{
    uint8_t crc = 0;
    while (len--) {
        crc ^= *data++;
        for (uint_fast8_t bit = 0; bit < 8; bit++)
            crc = crc & 0x80 ? (crc << 1) ^ 0x07 : crc << 1;
    }
    return crc;
}

int
creality_485_encode(uint8_t addr, uint8_t state, uint8_t function,
                    const uint8_t *payload, uint8_t payload_len,
                    uint8_t *out, uint16_t out_size)
{
    uint16_t body_len = (uint16_t)payload_len + CREALITY_485_MIN_BODY;
    uint16_t frame_len = body_len + CREALITY_485_FRAME_OVERHEAD;
    if (body_len > CREALITY_485_MAX_BODY || out_size < frame_len)
        return CREALITY_485_PARSE_BAD_LENGTH;
    out[0] = CREALITY_485_HEAD;
    out[1] = addr;
    out[2] = body_len;
    out[3] = state;
    out[4] = function;
    for (uint_fast8_t i = 0; i < payload_len; i++)
        out[5 + i] = payload[i];
    // The factory parser excludes both the 0xf7 head and slave address.
    out[frame_len - 1] = creality_485_crc8(&out[2], frame_len - 3);
    return frame_len;
}

int
creality_485_parse(const uint8_t *buf, uint16_t buf_len,
                   uint16_t *consumed, uint8_t *addr,
                   uint8_t *state, uint8_t *function,
                   const uint8_t **payload, uint8_t *payload_len)
{
    uint16_t skip = 0;
    while (skip < buf_len && buf[skip] != CREALITY_485_HEAD)
        skip++;
    if (skip) {
        *consumed = skip;
        return CREALITY_485_PARSE_INCOMPLETE;
    }
    if (buf_len < CREALITY_485_FRAME_OVERHEAD) {
        *consumed = 0;
        return CREALITY_485_PARSE_INCOMPLETE;
    }
    uint8_t body_len = buf[2];
    if (body_len < CREALITY_485_MIN_BODY) {
        *consumed = 1;
        return CREALITY_485_PARSE_BAD_LENGTH;
    }
    uint16_t frame_len = (uint16_t)body_len + CREALITY_485_FRAME_OVERHEAD;
    if (buf_len < frame_len) {
        *consumed = 0;
        return CREALITY_485_PARSE_INCOMPLETE;
    }
    *consumed = frame_len;
    if (creality_485_crc8(&buf[2], frame_len - 3) != buf[frame_len - 1])
        return CREALITY_485_PARSE_BAD_CRC;
    *addr = buf[1];
    *state = buf[3];
    *function = buf[4];
    *payload = &buf[5];
    *payload_len = body_len - CREALITY_485_MIN_BODY;
    return CREALITY_485_PARSE_OK;
}
