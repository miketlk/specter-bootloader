/*
 * SPDX-FileCopyrightText: 2015-2021 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef BOOTLOADER_HOOKS_H
#define BOOTLOADER_HOOKS_H

/**
 * @file bootloader_hooks.h
 * @brief Optional second-stage bootloader initialization hooks.
 */

/// Hook executed before second-stage bootloader initialization, if provided.
void __attribute__((weak)) bootloader_before_init(void);

/// Hook executed after second-stage bootloader initialization, if provided.
void __attribute__((weak)) bootloader_after_init(void);

#endif /* BOOTLOADER_HOOKS_H */
