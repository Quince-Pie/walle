/*
 * Analysis-only macOS interposer for discovering the current AGX IOKit ABI.
 * The default path records only call metadata.  A separately named opt-in
 * mode patches one authenticated standalone probe shader so its existing
 * result stores expose the coefficient triples returned by LDCF.  That mode
 * is fail-closed and must never be used with a production renderer.
 */

#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IODataQueueClient.h>
#include <IOKit/IOKitLib.h>
#include <mach/mach.h>

#include <inttypes.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

struct interpose_tuple {
    const void *replacement;
    const void *replacee;
};

struct shmem_record {
    uint32_t identifier;
    uint32_t size;
    const uint8_t *bytes;
};

struct allocation_record {
    uint64_t gpu_address;
    uint32_t handle;
    uint64_t size;
    const uint8_t *bytes;
};

static pthread_mutex_t shmem_lock = PTHREAD_MUTEX_INITIALIZER;
static struct shmem_record shmem_records[64];
static size_t shmem_record_count;
static struct allocation_record allocation_records[128];
static size_t allocation_record_count;
static uint32_t submission_sequence;
static bool coefficient_export_patch_attempted;
static bool coefficient_export_patch_applied;

#ifndef WALLE_AGX_NO_QUEUE_INTERPOSE
extern kern_return_t IOGPUCommandQueueSubmitCommandBuffers(
    CFTypeRef queue, uint32_t flags, uint32_t command_count,
    const void *commands, size_t command_stride, uint32_t *submission_id);
#endif

#define INTERPOSE(replacement, replacee)                                      \
    __attribute__((used)) static const struct interpose_tuple                 \
        interpose_##replacee __attribute__((section("__DATA,__interpose"))) = { \
            (const void *)(uintptr_t)&replacement,                            \
            (const void *)(uintptr_t)&replacee,                               \
        }

static void
trace_line(const char *format, ...)
{
    char buffer[512];
    va_list args;

    va_start(args, format);
    int length = vsnprintf(buffer, sizeof(buffer), format, args);
    va_end(args);

    if (length <= 0)
        return;

    size_t count = (size_t)length;
    if (count >= sizeof(buffer))
        count = sizeof(buffer) - 1;

    (void)write(STDERR_FILENO, buffer, count);
}

static void
trace_bytes(const char *label, const void *bytes, size_t size)
{
    const uint8_t *cursor = bytes;

    if (cursor == NULL || size == 0)
        return;

    for (size_t offset = 0; offset < size; offset += 16) {
        size_t line_size = size - offset;
        if (line_size > 16)
            line_size = 16;

        char line[160];
        int used = snprintf(line, sizeof(line), "%s +0x%04zx:", label, offset);
        if (used < 0)
            return;

        for (size_t index = 0; index < line_size; ++index) {
            int written = snprintf(line + used, sizeof(line) - (size_t)used,
                                   " %02x", cursor[offset + index]);
            if (written < 0 || (size_t)written >= sizeof(line) - (size_t)used)
                return;
            used += written;
        }
        if ((size_t)used + 1 >= sizeof(line))
            return;
        line[used++] = '\n';
        (void)write(STDERR_FILENO, line, (size_t)used);
    }
}

static void
save_bytes(const char *directory, uint32_t submission,
           const struct shmem_record *record)
{
    if (directory == NULL || directory[0] == '\0')
        return;

    char path[1024];
    int length = snprintf(path, sizeof(path),
                          "%s/submit-%03u-shmem-%u.bin", directory,
                          submission, record->identifier);
    if (length <= 0 || (size_t)length >= sizeof(path))
        return;

    int descriptor = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (descriptor < 0) {
        trace_line("AGX_IO save failed path=%s errno=%d\n", path, errno);
        return;
    }

    size_t written = 0;
    while (written < record->size) {
        ssize_t count = write(descriptor, record->bytes + written,
                              (size_t)record->size - written);
        if (count > 0) {
            written += (size_t)count;
        } else if (count < 0 && errno == EINTR) {
            continue;
        } else {
            trace_line("AGX_IO write failed path=%s errno=%d\n", path, errno);
            break;
        }
    }
    (void)close(descriptor);
}

static void
save_allocation(const char *directory, const struct allocation_record *record)
{
    if (directory == NULL || directory[0] == '\0' || record->bytes == NULL ||
        record->size == 0 || record->size > 8 * 1024 * 1024)
        return;

    char path[1024];
    int length = snprintf(path, sizeof(path),
                          "%s/alloc-%03u-gpu-%016" PRIx64 "-size-%" PRIu64
                          ".bin",
                          directory, record->handle, record->gpu_address,
                          record->size);
    if (length <= 0 || (size_t)length >= sizeof(path))
        return;

    int descriptor = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (descriptor < 0) {
        trace_line("AGX_IO allocation save failed path=%s errno=%d\n", path,
                   errno);
        return;
    }

    size_t written = 0;
    while (written < record->size) {
        ssize_t count = write(descriptor, record->bytes + written,
                              (size_t)record->size - written);
        if (count > 0) {
            written += (size_t)count;
        } else if (count < 0 && errno == EINTR) {
            continue;
        } else {
            trace_line("AGX_IO allocation write failed path=%s errno=%d\n",
                       path, errno);
            break;
        }
    }
    (void)close(descriptor);
}

static bool
patch_coefficient_export_allocation(struct allocation_record *record)
{
    static const uint8_t shader_signature[] = {
        0xa1, 0xa9, 0x02, 0x40, 0x00, 0x40, 0x10, 0x00,
        0x61, 0x95, 0x03, 0x80, 0x00, 0x40, 0x00, 0x00,
    };
    static const struct {
        size_t offset;
        uint8_t expected[8];
        uint8_t replacement[8];
        size_t size;
    } edits[] = {
        {
            .offset = 0xe2,
            .expected = {0xa1, 0xb9, 0x06, 0x40, 0x00, 0x40, 0x00, 0x00},
            .replacement = {0xa1, 0xf5, 0x06, 0x40, 0x00, 0x40, 0x00, 0x00},
            .size = 8,
        },
        {
            .offset = 0xea,
            .expected = {0xa1, 0xa9, 0x07, 0x40, 0x00, 0x40, 0x00, 0x00},
            .replacement = {0xa1, 0xe5, 0x07, 0x40, 0x00, 0x40, 0x00, 0x00},
            .size = 8,
        },
        {
            .offset = 0xf2,
            .expected = {0xa1, 0x85, 0x08, 0x40, 0x00, 0x40, 0x00, 0x01},
            .replacement = {0xa1, 0xd5, 0x08, 0x40, 0x00, 0x40, 0x00, 0x00},
            .size = 8,
        },
        {
            .offset = 0xfa,
            .expected = {0xa1, 0x95, 0x09, 0x40, 0x00, 0x40, 0x00, 0x00},
            .replacement = {0xa1, 0xc5, 0x09, 0x40, 0x00, 0x40, 0x00, 0x00},
            .size = 8,
        },
        {
            .offset = 0x102,
            .expected = {0xfe, 0x25, 0x9c, 0x62, 0x9b, 0x00},
            /* jmp_any +0x3b8, from shader offset 0x102 to 0x4ba. */
            .replacement = {0x00, 0xc0, 0xb8, 0x03, 0x00, 0x00},
            .size = 6,
        },
    };

    if (record->bytes == NULL || record->size < sizeof(shader_signature))
        return false;

    size_t signature_offset = SIZE_MAX;
    for (size_t offset = 0;
         offset <= record->size - sizeof(shader_signature); ++offset) {
        if (__builtin_memcmp(record->bytes + offset, shader_signature,
                             sizeof(shader_signature)) != 0)
            continue;
        if (signature_offset != SIZE_MAX) {
            trace_line("AGX_IO coefficient export signature is not unique\n");
            return false;
        }
        signature_offset = offset;
    }
    if (signature_offset == SIZE_MAX)
        return false;

    for (size_t index = 0; index < sizeof(edits) / sizeof(edits[0]); ++index) {
        const size_t offset = signature_offset + edits[index].offset;
        if (offset > record->size || edits[index].size > record->size - offset ||
            __builtin_memcmp(record->bytes + offset, edits[index].expected,
                             edits[index].size) != 0) {
            trace_line("AGX_IO coefficient export preimage differs edit=%zu\n",
                       index);
            return false;
        }
    }

    uint8_t *bytes = (uint8_t *)(uintptr_t)record->bytes;
    for (size_t index = 0; index < sizeof(edits) / sizeof(edits[0]); ++index)
        __builtin_memcpy(bytes + signature_offset + edits[index].offset,
                         edits[index].replacement, edits[index].size);

    trace_line("AGX_IO coefficient export patched handle=%u shader=0x%zx\n",
               record->handle, signature_offset);
    return true;
}

static void
patch_coefficient_export(void)
{
    const char *requested = getenv("WALLE_AGX_EXPORT_LDCF");
    if (requested == NULL || requested[0] != '1' || requested[1] != '\0')
        return;
    if (coefficient_export_patch_attempted)
        return;
    coefficient_export_patch_attempted = true;

    size_t matches = 0;
    for (size_t index = 0; index < allocation_record_count; ++index) {
        if (patch_coefficient_export_allocation(&allocation_records[index]))
            ++matches;
    }
    coefficient_export_patch_applied = matches == 1;
    trace_line("AGX_IO coefficient export matches=%zu applied=%u\n", matches,
               coefficient_export_patch_applied ? 1u : 0u);
}

static kern_return_t
trace_IOConnectCallMethod(mach_port_t connection, uint32_t selector,
                          const uint64_t *input, uint32_t input_count,
                          const void *input_struct, size_t input_struct_size,
                          uint64_t *output, uint32_t *output_count,
                          void *output_struct, size_t *output_struct_size)
{
    size_t output_size = output_struct_size == NULL ? 0 : *output_struct_size;
    trace_line("AGX_IO method enter connection=0x%x selector=0x%x "
               "scalar_in=%u struct_in=%zu scalar_out=%u struct_out=%zu\n",
               connection, selector, input_count, input_struct_size,
               output_count == NULL ? 0 : *output_count, output_size);

    if (connection != 0 && (selector == 0x7 || selector == 0x9))
        trace_bytes("AGX_IO input", input_struct, input_struct_size);

    kern_return_t result = IOConnectCallMethod(
        connection, selector, input, input_count, input_struct,
        input_struct_size, output, output_count, output_struct,
        output_struct_size);

    trace_line("AGX_IO method leave connection=0x%x selector=0x%x "
               "result=0x%x scalar_out=%u struct_out=%zu\n",
               connection, selector, result,
               output_count == NULL ? 0 : *output_count,
               output_struct_size == NULL ? 0 : *output_struct_size);
    if (result == KERN_SUCCESS &&
        (selector == 0x7 || selector == 0x9 || selector == 0xe ||
         selector == 0x10))
        trace_bytes("AGX_IO output", output_struct,
                    output_struct_size == NULL ? 0 : *output_struct_size);
    if (result == KERN_SUCCESS && selector == 0xe && output_struct != NULL &&
        output_struct_size != NULL && *output_struct_size == 16) {
        const uint64_t *pointer = output_struct;
        const uint32_t *words = (const uint32_t *)(pointer + 1);
        pthread_mutex_lock(&shmem_lock);
        if (shmem_record_count <
            sizeof(shmem_records) / sizeof(shmem_records[0])) {
            shmem_records[shmem_record_count++] = (struct shmem_record){
                .identifier = words[1],
                .size = words[0],
                .bytes = (const uint8_t *)(uintptr_t)*pointer,
            };
        }
        pthread_mutex_unlock(&shmem_lock);
    }
    if (result == KERN_SUCCESS && selector == 0x9 && output_struct != NULL &&
        output_struct_size != NULL && *output_struct_size == 88) {
        const uint8_t *response = output_struct;
        uint64_t gpu_address;
        uint64_t cpu_address;
        uint32_t handle;
        uint64_t size;

        __builtin_memcpy(&gpu_address, response, sizeof(gpu_address));
        __builtin_memcpy(&cpu_address, response + 8, sizeof(cpu_address));
        __builtin_memcpy(&handle, response + 36, sizeof(handle));
        __builtin_memcpy(&size, response + 40, sizeof(size));

        pthread_mutex_lock(&shmem_lock);
        if (cpu_address != 0 &&
            allocation_record_count <
                sizeof(allocation_records) / sizeof(allocation_records[0])) {
            allocation_records[allocation_record_count++] =
                (struct allocation_record){
                    .gpu_address = gpu_address,
                    .handle = handle,
                    .size = size,
                    .bytes = (const uint8_t *)(uintptr_t)cpu_address,
                };
        }
        pthread_mutex_unlock(&shmem_lock);
    }
    return result;
}

static kern_return_t
trace_IOConnectCallAsyncMethod(
    mach_port_t connection, uint32_t selector, mach_port_t wake_port,
    uint64_t *reference, uint32_t reference_count, const uint64_t *input,
    uint32_t input_count, const void *input_struct, size_t input_struct_size,
    uint64_t *output, uint32_t *output_count, void *output_struct,
    size_t *output_struct_size)
{
    trace_line("AGX_IO async enter connection=0x%x selector=0x%x wake=0x%x "
               "refs=%u scalar_in=%u struct_in=%zu\n",
               connection, selector, wake_port, reference_count, input_count,
               input_struct_size);

    kern_return_t result = IOConnectCallAsyncMethod(
        connection, selector, wake_port, reference, reference_count, input,
        input_count, input_struct, input_struct_size, output, output_count,
        output_struct, output_struct_size);

    trace_line("AGX_IO async leave connection=0x%x selector=0x%x result=0x%x\n",
               connection, selector, result);
    return result;
}

static kern_return_t
trace_IOConnectCallStructMethod(mach_port_t connection, uint32_t selector,
                                const void *input_struct,
                                size_t input_struct_size, void *output_struct,
                                size_t *output_struct_size)
{
    trace_line("AGX_IO struct enter connection=0x%x selector=0x%x "
               "struct_in=%zu struct_out=%zu\n",
               connection, selector, input_struct_size,
               output_struct_size == NULL ? 0 : *output_struct_size);
    kern_return_t result = IOConnectCallStructMethod(
        connection, selector, input_struct, input_struct_size, output_struct,
        output_struct_size);
    trace_line("AGX_IO struct leave connection=0x%x selector=0x%x "
               "result=0x%x struct_out=%zu\n",
               connection, selector, result,
               output_struct_size == NULL ? 0 : *output_struct_size);
    return result;
}

static kern_return_t
trace_IOConnectCallAsyncStructMethod(
    mach_port_t connection, uint32_t selector, mach_port_t wake_port,
    uint64_t *reference, uint32_t reference_count, const void *input_struct,
    size_t input_struct_size, void *output_struct, size_t *output_struct_size)
{
    trace_line("AGX_IO async-struct enter connection=0x%x selector=0x%x "
               "wake=0x%x refs=%u struct_in=%zu\n",
               connection, selector, wake_port, reference_count,
               input_struct_size);
    kern_return_t result = IOConnectCallAsyncStructMethod(
        connection, selector, wake_port, reference, reference_count,
        input_struct, input_struct_size, output_struct, output_struct_size);
    trace_line("AGX_IO async-struct leave connection=0x%x selector=0x%x "
               "result=0x%x\n",
               connection, selector, result);
    return result;
}

static kern_return_t
trace_IOConnectCallScalarMethod(mach_port_t connection, uint32_t selector,
                                const uint64_t *input, uint32_t input_count,
                                uint64_t *output, uint32_t *output_count)
{
    trace_line("AGX_IO scalar enter connection=0x%x selector=0x%x "
               "scalar_in=%u scalar_out=%u\n",
               connection, selector, input_count,
               output_count == NULL ? 0 : *output_count);
    kern_return_t result = IOConnectCallScalarMethod(
        connection, selector, input, input_count, output, output_count);
    trace_line("AGX_IO scalar leave connection=0x%x selector=0x%x "
               "result=0x%x scalar_out=%u\n",
               connection, selector, result,
               output_count == NULL ? 0 : *output_count);
    return result;
}

static kern_return_t
trace_IOConnectCallAsyncScalarMethod(
    mach_port_t connection, uint32_t selector, mach_port_t wake_port,
    uint64_t *reference, uint32_t reference_count, const uint64_t *input,
    uint32_t input_count, uint64_t *output, uint32_t *output_count)
{
    trace_line("AGX_IO async-scalar enter connection=0x%x selector=0x%x "
               "wake=0x%x refs=%u scalar_in=%u\n",
               connection, selector, wake_port, reference_count, input_count);
    kern_return_t result = IOConnectCallAsyncScalarMethod(
        connection, selector, wake_port, reference, reference_count, input,
        input_count, output, output_count);
    trace_line("AGX_IO async-scalar leave connection=0x%x selector=0x%x "
               "result=0x%x\n",
               connection, selector, result);
    return result;
}

#ifndef WALLE_AGX_NO_QUEUE_INTERPOSE
static kern_return_t
trace_IOGPUCommandQueueSubmitCommandBuffers(
    CFTypeRef queue, uint32_t flags, uint32_t command_count,
    const void *commands, size_t command_stride, uint32_t *submission_id)
{
    trace_line("AGX_IO submit queue=%p flags=0x%x count=%u stride=%zu "
               "submission=%p\n",
               queue, flags, command_count, command_stride,
               (void *)submission_id);

    size_t command_bytes = command_stride * (size_t)command_count;
    if (command_count != 0 && command_stride != 0 &&
        command_bytes / command_stride == command_count)
        trace_bytes("AGX_IO commands", commands, command_bytes);

    pthread_mutex_lock(&shmem_lock);
    for (size_t index = 0; index < shmem_record_count; ++index) {
        char label[64];
        int length = snprintf(label, sizeof(label), "AGX_IO shmem-%u",
                              shmem_records[index].identifier);
        if (length > 0 && (size_t)length < sizeof(label)) {
            size_t dump_size = shmem_records[index].size;
            if (dump_size > 256)
                dump_size = 256;
            trace_bytes(label, shmem_records[index].bytes, dump_size);
        }
    }
    pthread_mutex_unlock(&shmem_lock);

    return IOGPUCommandQueueSubmitCommandBuffers(
        queue, flags, command_count, commands, command_stride, submission_id);
}
#endif

static kern_return_t
trace_IOConnectTrap4(io_connect_t connection, uint32_t index,
                     uintptr_t argument_1, uintptr_t argument_2,
                     uintptr_t argument_3, uintptr_t argument_4)
{
    trace_line("AGX_IO trap4 connection=0x%x index=%u arg1=0x%" PRIxPTR
               " arg2=0x%" PRIxPTR " arg3=0x%" PRIxPTR
               " arg4=0x%" PRIxPTR "\n",
               connection, index, argument_1, argument_2, argument_3,
               argument_4);

    const bool recognized_submission =
        index == 0 && argument_2 != 0 && argument_2 <= 64 && argument_3 != 0;
    if (recognized_submission) {
        trace_bytes("AGX_IO trap-command", (const void *)argument_3,
                    (size_t)argument_2);
        pthread_mutex_lock(&shmem_lock);
        uint32_t submission = submission_sequence++;
        const char *trace_directory = getenv("WALLE_AGX_TRACE_DIR");
        if (submission == 0)
            patch_coefficient_export();
        for (size_t record = 0; record < shmem_record_count; ++record) {
            char label[64];
            int length = snprintf(label, sizeof(label), "AGX_IO trap-shmem-%u",
                                  shmem_records[record].identifier);
            if (length > 0 && (size_t)length < sizeof(label)) {
                size_t dump_size = shmem_records[record].size;
                if (dump_size > 256)
                    dump_size = 256;
                trace_bytes(label, shmem_records[record].bytes, dump_size);
            }
            save_bytes(trace_directory, submission, &shmem_records[record]);
        }
        if (submission == 0) {
            for (size_t allocation = 0;
                allocation < allocation_record_count; ++allocation)
                save_allocation(trace_directory,
                                &allocation_records[allocation]);
        }
        pthread_mutex_unlock(&shmem_lock);
    }

    if (recognized_submission && getenv("WALLE_AGX_EXPORT_LDCF") != NULL &&
        !coefficient_export_patch_applied) {
        trace_line("AGX_IO coefficient export refused trap submission\n");
        return KERN_FAILURE;
    }

    return IOConnectTrap4(connection, index, argument_1, argument_2,
                          argument_3, argument_4);
}

INTERPOSE(trace_IOConnectCallMethod, IOConnectCallMethod);
INTERPOSE(trace_IOConnectCallAsyncMethod, IOConnectCallAsyncMethod);
INTERPOSE(trace_IOConnectCallStructMethod, IOConnectCallStructMethod);
INTERPOSE(trace_IOConnectCallAsyncStructMethod, IOConnectCallAsyncStructMethod);
INTERPOSE(trace_IOConnectCallScalarMethod, IOConnectCallScalarMethod);
INTERPOSE(trace_IOConnectCallAsyncScalarMethod, IOConnectCallAsyncScalarMethod);
#ifndef WALLE_AGX_NO_QUEUE_INTERPOSE
INTERPOSE(trace_IOGPUCommandQueueSubmitCommandBuffers,
          IOGPUCommandQueueSubmitCommandBuffers);
#endif
INTERPOSE(trace_IOConnectTrap4, IOConnectTrap4);
