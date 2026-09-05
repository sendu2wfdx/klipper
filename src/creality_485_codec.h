#ifndef __CREALITY_485_CODEC_H
#define __CREALITY_485_CODEC_H

#include <stdint.h>

#define CREALITY_485_HEAD 0xf7
#define CREALITY_485_MIN_BODY 3
#define CREALITY_485_MAX_BODY 255
#define CREALITY_485_FRAME_OVERHEAD 3

enum creality_485_parse_result {
    CREALITY_485_PARSE_INCOMPLETE = 0,
    CREALITY_485_PARSE_OK = 1,
    CREALITY_485_PARSE_BAD_CRC = -1,
    CREALITY_485_PARSE_BAD_LENGTH = -2,
};

uint8_t creality_485_crc8(const uint8_t *data, uint16_t len);
int creality_485_encode(uint8_t addr, uint8_t state, uint8_t function,
                        const uint8_t *payload, uint8_t payload_len,
                        uint8_t *out, uint16_t out_size);
int creality_485_parse(const uint8_t *buf, uint16_t buf_len,
                       uint16_t *consumed, uint8_t *addr,
                       uint8_t *state, uint8_t *function,
                       const uint8_t **payload, uint8_t *payload_len);

#endif
