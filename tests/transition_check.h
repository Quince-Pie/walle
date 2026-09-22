#ifndef WALLE_CPU_TEST_CHECK_H
#define WALLE_CPU_TEST_CHECK_H

#include <stdio.h>
#include <stdlib.h>

/* Deliberately independent of NDEBUG: checks contain the tested calls. */
static char test_case[160] = "initialization";

#define CHECK(condition)                                                            \
    do {                                                                            \
        if (!(condition)) {                                                         \
            fprintf(stderr, "FAIL %s\n%s:%d: %s\n", test_case, __FILE__, __LINE__,       \
                    #condition);                                                    \
            exit(EXIT_FAILURE);                                                     \
        }                                                                           \
    } while (false)

#endif
