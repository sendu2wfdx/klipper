#ifndef __STEPPER_H
#define __STEPPER_H

#include <stdint.h> // uint8_t

uint_fast8_t stepper_event(struct timer *t);

// Read-only helpers used by the Creality PRTouch V3 protocol.
int32_t step_prtouch_get_pos(int32_t step_oid);
uint32_t step_prtouch_get_ivt(int32_t step_oid);
int32_t step_prtouch_get_pos_v65(int32_t step_oid);
int32_t step_prtouch_get_cnt(int32_t step_oid);

#endif // stepper.h
