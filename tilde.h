#ifndef WALLE_TILDE_H
#define WALLE_TILDE_H

#include <stddef.h>

size_t expand_tilde(const char* restrict path, char* restrict dest, size_t dest_size);

[[nodiscard]]
char* expand_tilde_alloc(const char* path);

#endif
