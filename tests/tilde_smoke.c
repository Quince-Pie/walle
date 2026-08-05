#define _GNU_SOURCE

#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "tilde.h"

#define CHECK(condition) check((condition), #condition, __LINE__)

static void check(bool condition, const char* expression, int line)
{
    if (condition)
        return;
    fprintf(stderr, "tilde smoke failure at line %d: %s\n", line, expression);
    exit(EXIT_FAILURE);
}

static void expect(const char* input, const char* expected)
{
    char* actual = expand_tilde_alloc(input);
    CHECK(actual);
    CHECK(strcmp(actual, expected) == 0);
    free(actual);
}

int main(void)
{
    CHECK(setenv("HOME", "/tmp/home", 1) == 0);
    CHECK(setenv("PWD", "/tmp/current", 1) == 0);
    CHECK(setenv("OLDPWD", "/tmp/previous", 1) == 0);

    expect("~/image.png", "/tmp/home/image.png");
    expect("~+/image.png", "/tmp/current/image.png");
    expect("~-/image.png", "/tmp/previous/image.png");
    expect("/literal/~", "/literal/~");
    expect("~walle-user-that-does-not-exist/image.png",
           "~walle-user-that-does-not-exist/image.png");
    return 0;
}
