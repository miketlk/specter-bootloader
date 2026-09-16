/** @file storage_faults.c
 * @brief Inject storage failures at each publication boundary.
 */
#define SDU_HOST_IMPLEMENTATION
#include <assert.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "storage.h"
#include "storage_host.h"
int host_fault;
size_t host_fwrite(const void* p, size_t size, size_t n, FILE* f) {
  if (host_fault == 1) {
    errno = ENOSPC;
    return n ? n - 1 : 0;
  }
  return fwrite(p, size, n, f);
}
size_t host_fread(void* p, size_t size, size_t n, FILE* f) {
  if (host_fault == 5) {
    errno = EIO;
    return 0;
  }
  return fread(p, size, n, f);
}
int host_ferror(FILE* f) { return host_fault == 5 || ferror(f); }
int host_fflush(FILE* f) {
  int r = fflush(f);
  return host_fault == 2 ? EOF : r;
}
int host_fsync(int fd) {
  int r = fsync(fd);
  return host_fault == 3 ? -1 : r;
}
int host_fclose(FILE* f) {
  int r = fclose(f);
  return host_fault == 4 ? EOF : r;
}
int host_unlink(const char* p) {
  if (host_fault == 8) {
    errno = EIO;
    return -1;
  }
  return unlink(p);
}
int host_rename(const char* from, const char* to) {
  if (host_fault == 6) {
    errno = EIO;
    return -1;
  }
  int r = rename(from, to);
  if (host_fault == 7 && !r) {
    FILE* f = fopen(to, "wb");
    assert(f);
    fputs("bad", f);
    fclose(f);
  }
  return r;
}
int main(int argc, char** argv) {
  assert(argc == 2);
  int scenario = atoi(argv[1]);
  uint8_t session[16] = {1}, digest[32];
  SHA256((const unsigned char*)"abc", 3, digest);
  assert(sdu_mount() == 0);
  if (scenario == 12) {
    const char* temporary = "_sdu_0123456789abcdef0123456789abcdef.part";
    FILE* abandoned = fopen(temporary, "wb");
    assert(abandoned);
    int closed = fclose(abandoned);
    assert(closed == 0);
    assert(sdu_cleanup("fixture.bin") == SDU_PATH);
    assert(sdu_cleanup("_sdu_0123456789abcdef0123456789abcdeg.part") ==
           SDU_PATH);
    assert(sdu_cleanup(temporary) == 0);
    assert(access(temporary, F_OK) != 0);
    assert(sdu_release() == 0);
    return 0;
  }
  if (scenario == 11) {
    host_fault = 11;
    assert(sdu_begin("fixture.bin", 3, digest, session) == SDU_SPACE);
    return 0;
  }
  assert(sdu_begin("fixture.bin", 3, digest, session) == 0);
  assert(sdu_write(1, (const uint8_t*)"abc", 3) == SDU_OFFSET);
  assert(sdu_write(0, (const uint8_t*)"abcd", 4) == SDU_RANGE);
  if (scenario == 1) host_fault = 1;
  int result = sdu_write(0, (const uint8_t*)"abc", 3);
  if (scenario == 1) {
    assert(result == SDU_SPACE);
    assert(sdu_release() != 0);
    return 0;
  }
  assert(result == 0);
  if (scenario == 8) {
    host_fault = 8;
    assert(sdu_abort() != 0);
    assert(sdu_release() != 0);
    return 0;
  }
  if (scenario >= 2 && scenario <= 7) host_fault = scenario;
  result = sdu_commit(NULL);
  if (scenario >= 2 && scenario <= 7) {
    assert(result != 0);
    assert(!strcmp(sdu_state, "UNCERTAIN"));
    assert(sdu_release() != 0);
    return 0;
  }
  assert(result == 0);
  assert(sdu_commit(NULL) == 0);
  assert(sdu_begin("fixture.bin", 3, digest, session) == SDU_EXISTS);
  assert(sdu_remove("../fixture.bin") == SDU_PATH);
  if (scenario == 9 || scenario == 10) {
    host_fault = scenario;
    assert(sdu_release() != 0);
    return 0;
  }
  assert(sdu_release() == 0);
  assert(sdu_release() == 0);
  FILE* file = fopen("fixture.bin", "rb");
  assert(file);
  char bytes[4] = {0};
  assert(fread(bytes, 1, 3, file) == 3);
  assert(!strcmp(bytes, "abc"));
  fclose(file);
  return 0;
}
