#ifndef TEST_FREERTOS_TASK_H_INCLUDED
#define TEST_FREERTOS_TASK_H_INCLUDED

#include <stdint.h>

static inline void vTaskDelay(uint32_t ticks) { (void)ticks; }

#endif
