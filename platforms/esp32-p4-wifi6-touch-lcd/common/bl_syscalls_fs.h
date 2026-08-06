/**
 * @file bl_syscalls_fs.h
 * @brief POSIX VFS types for the ESP32-P4 Specter Bootloader port.
 */

#ifndef ESP32P4_BL_SYSCALLS_FS_H_INCLUDED
#define ESP32P4_BL_SYSCALLS_FS_H_INCLUDED

#include <dirent.h>
#include <stdio.h>

typedef size_t bl_fsize_t;
typedef long bl_foffset_t;
typedef int bl_file_obj_t;
typedef FILE* bl_file_t;

typedef struct bl_ffind_ctx_struct {
  DIR* dir;
  char directory[32];
  char pattern[64];
  char result[320];
} bl_ffind_ctx_t;

#endif  // ESP32P4_BL_SYSCALLS_FS_H_INCLUDED
