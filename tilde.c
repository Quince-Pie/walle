#ifndef _GNU_SOURCE
#define _GNU_SOURCE 1 // Enables secure_getenv, strchrnul, and getcwd(NULL, 0)
#endif

#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pwd.h>
#include <errno.h>
#include <sys/types.h>

/* CPU Instruction Pipeline Branch-Prediction Hints */
#if defined(__GNUC__) || defined(__clang__)
# define LIKELY(x)   __builtin_expect(!!(x), 1)
# define UNLIKELY(x) __builtin_expect(!!(x), 0)
#else
# define LIKELY(x)   (x)
# define UNLIKELY(x) (x)
#endif

/**
 * @brief Resolves tilde (~) in Linux paths (POSIX semantics + Bash extensions ~+, ~-).
 *
 * Behaves identically to snprintf: Returns the number of characters
 * that WOULD have been written if dest_size was infinitely large (excluding \0).
 * If the return value >= dest_size, truncation safely occurred.
 */
size_t expand_tilde(const char *restrict path, char *restrict dest, size_t dest_size) {
    if (UNLIKELY(path == NULL)) return 0;

    // FAST-PATH: No tilde prefix, immediately copy and return. (Cost: ~10 cycles)
    if (LIKELY(path[0] != '~')) {
        size_t len = strlen(path);
        if (dest_size > 0 && dest != NULL) {
            size_t copy_len = LIKELY(len < dest_size) ? len : dest_size - 1;
            memcpy(dest, path, copy_len);
            dest[copy_len] = '\0';
        }
        return len;
    }

    // EVOLVE-BLOCK-START
    const char *slash;
    size_t prefix_len;

    // Fast-path branch resolution for common prefixes
    if (LIKELY(path[1] == '\0' || path[1] == '/')) {
        prefix_len = 1;
        slash = path + 1;
    } else if ((path[1] == '+' || path[1] == '-') && (path[2] == '\0' || path[2] == '/')) {
        prefix_len = 2;
        slash = path + 2;
    } else {
        slash = path + 2;
        while (*slash && *slash != '/') {
            slash++;
        }
        prefix_len = (size_t)(slash - path);
    }

    const char *home = NULL;
    int resolve_failed = 0;

    char stack_pw_buf[2048]; // Reduced to fit L1 cache lines better while keeping standard limits
    char *pw_buf = stack_pw_buf;
    size_t bufsize = sizeof(stack_pw_buf);
    struct passwd pwd;
    struct passwd *result = NULL;

    char cwd_buf[2048];
    char *dyn_cwd = NULL;

    if (LIKELY(prefix_len == 1)) {
        home = secure_getenv("HOME");
        if (UNLIKELY(home == NULL)) {
            int s;
            while (UNLIKELY((s = getpwuid_r(geteuid(), &pwd, pw_buf, bufsize, &result)) == ERANGE)) {
                bufsize *= 2;
                if (UNLIKELY(bufsize > 1048576)) break;
                if (pw_buf == stack_pw_buf) {
                    pw_buf = malloc(bufsize);
                    if (UNLIKELY(!pw_buf)) break;
                } else {
                    char *new_buf = realloc(pw_buf, bufsize);
                    if (UNLIKELY(!new_buf)) break;
                    pw_buf = new_buf;
                }
            }
            if (LIKELY(s == 0 && result != NULL)) {
                home = result->pw_dir;
            } else {
                resolve_failed = 1;
            }
        }
    } else if (prefix_len == 2 && path[1] == '+') {
        home = secure_getenv("PWD");
        if (UNLIKELY(home == NULL)) {
            home = getcwd(cwd_buf, sizeof(cwd_buf));
            if (UNLIKELY(home == NULL && errno == ERANGE)) {
                dyn_cwd = getcwd(NULL, 0);
                home = dyn_cwd;
            }
            if (UNLIKELY(home == NULL)) resolve_failed = 1;
        }
    } else if (prefix_len == 2 && path[1] == '-') {
        home = secure_getenv("OLDPWD");
        if (UNLIKELY(home == NULL)) resolve_failed = 1;
    } else {
        size_t ulen = prefix_len - 1;
        char user_stack[128];
        char *user = user_stack;

        if (UNLIKELY(ulen >= sizeof(user_stack))) {
            user = malloc(ulen + 1);
            if (UNLIKELY(!user)) {
                resolve_failed = 1;
                goto fallback;
            }
        }
        memcpy(user, path + 1, ulen);
        user[ulen] = '\0';

        int s;
        while (UNLIKELY((s = getpwnam_r(user, &pwd, pw_buf, bufsize, &result)) == ERANGE)) {
            bufsize *= 2;
            if (UNLIKELY(bufsize > 1048576)) break;
            if (pw_buf == stack_pw_buf) {
                pw_buf = malloc(bufsize);
                if (UNLIKELY(!pw_buf)) break;
            } else {
                char *new_buf = realloc(pw_buf, bufsize);
                if (UNLIKELY(!new_buf)) break;
                pw_buf = new_buf;
            }
        }

        if (UNLIKELY(user != user_stack)) free(user);

        if (LIKELY(s == 0 && result != NULL)) {
            home = result->pw_dir;
        } else {
            resolve_failed = 1;
        }
    }

fallback:;
    size_t rest_len = strlen(slash);

    if (UNLIKELY(resolve_failed)) {
        size_t len = prefix_len + rest_len;
        if (dest_size > 0 && dest != NULL) {
            size_t copy_len = LIKELY(len < dest_size) ? len : dest_size - 1;
            memcpy(dest, path, copy_len);
            dest[copy_len] = '\0';
        }
        if (UNLIKELY(pw_buf != stack_pw_buf)) free(pw_buf);
        if (UNLIKELY(dyn_cwd != NULL)) free(dyn_cwd);
        return len;
    }

    size_t home_len = strlen(home);
    size_t total_len = home_len + rest_len;

    if (dest_size > 0 && dest != NULL) {
        if (LIKELY(total_len < dest_size)) {
            memcpy(dest, home, home_len);
            memcpy(dest + home_len, slash, rest_len + 1);
        } else {
            size_t avail = dest_size - 1;
            size_t copy_home = LIKELY(home_len <= avail) ? home_len : avail;
            memcpy(dest, home, copy_home);

            if (avail > copy_home) {
                memcpy(dest + copy_home, slash, avail - copy_home);
            }
            dest[avail] = '\0';
        }
    }

    if (UNLIKELY(pw_buf != stack_pw_buf)) free(pw_buf);
    if (UNLIKELY(dyn_cwd != NULL)) free(dyn_cwd);
    return total_len;
    // EVOLVE-BLOCK-END
}

/**
 * @brief Allocating wrapper for expand_tilde.
 *
 * Expands the tilde into a dynamically allocated string.
 * Leverages the stack buffer API to guarantee EXACTLY 1 malloc in 99.99% of cases.
 */
char *expand_tilde_alloc(const char *path) {
    if (UNLIKELY(path == NULL)) return NULL;

    char stack_buf[4096];
    size_t req_len = expand_tilde(path, stack_buf, sizeof(stack_buf));

    char *res = malloc(req_len + 1);
    if (UNLIKELY(!res)) return NULL;

    if (LIKELY(req_len < sizeof(stack_buf))) {
        memcpy(res, stack_buf, req_len + 1);
    } else {
        expand_tilde(path, res, req_len + 1);
    }

    return res;
}
