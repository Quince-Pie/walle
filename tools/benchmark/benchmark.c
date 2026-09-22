#define _GNU_SOURCE
/* Include the locked backend so this test can report the actually selected
 * device identity without adding introspection to the production API. */
#include <sys/stat.h>
#include <time.h>

#include "transition.h"
#include "vulkan_renderer.c"

static uint64_t bench_now(void)
{
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &t))
        abort();
    return (uint64_t)t.tv_sec * UINT64_C(1000000000) + (uint64_t)t.tv_nsec;
}
static void bench_string(const char* value)
{
    putchar('"');
    for (const unsigned char* p = (const unsigned char*)value; *p; p++) {
        if (*p == '"' || *p == '\\') {
            putchar('\\');
            putchar(*p);
        } else if (*p < 32)
            printf("\\u%04x", *p);
        else
            putchar(*p);
    }
    putchar('"');
}
static void bench_hex(const uint8_t* bytes, size_t count)
{
    putchar('"');
    for (size_t i = 0; i < count; i++)
        printf("%02x", bytes[i]);
    putchar('"');
}
static void bench_device(struct walle_vk_renderer* renderer, unsigned run)
{
    VkPhysicalDeviceIDProperties ids = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ID_PROPERTIES};
    VkPhysicalDeviceDriverProperties driver
        = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES, .pNext = &ids};
    VkPhysicalDeviceProperties2 properties
        = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &driver};
    vkGetPhysicalDeviceProperties2(renderer->physical_device, &properties);
    printf("{\"type\":\"device\",\"run\":%u,\"name\":", run);
    bench_string(properties.properties.deviceName);
    printf(
        ",\"vendor_id\":%u,\"device_id\":%u,\"api_version\":%u,\"driver_version\":%u,\"driver_id\":"
        "%u,\"driver_name\":",
        properties.properties.vendorID,
        properties.properties.deviceID,
        properties.properties.apiVersion,
        properties.properties.driverVersion,
        driver.driverID);
    bench_string(driver.driverName);
    printf(",\"driver_info\":");
    bench_string(driver.driverInfo);
    printf(",\"device_uuid\":");
    bench_hex(ids.deviceUUID, VK_UUID_SIZE);
    printf(",\"driver_uuid\":");
    bench_hex(ids.driverUUID, VK_UUID_SIZE);
    printf(",\"pipeline_cache_uuid\":");
    bench_hex(properties.properties.pipelineCacheUUID, VK_UUID_SIZE);
    printf(",\"timestamp_period_ns\":%.9g,\"queue_family\":%u}\n",
           (double)properties.properties.limits.timestampPeriod,
           renderer->queue_family);
}
static bool bench_memory(struct walle_vk_output* output, unsigned run, const char* checkpoint)
{
    struct walle_vk_diagnostics d;
    if (!walle_vk_output_diagnostics(output, false, &d))
        return false;
    printf("{\"type\":\"memory\",\"run\":%u,\"checkpoint\":", run);
    bench_string(checkpoint);
    printf(
        ",\"renderer_bytes\":%llu,\"renderer_peak_bytes\":%llu,\"renderer_allocations\":%u,"
        "\"output_bytes\":%llu,\"source_bytes\":%llu,\"backdrop_bytes\":%llu,\"scratch_bytes\":%"
        "llu,\"effect_bytes\":%llu,\"frame_buffer_bytes\":%llu,\"readback_bytes\":%llu,\"present_"
        "bytes\":%llu,\"present_images\":%u}\n",
        (unsigned long long)d.renderer_memory.allocated_bytes,
        (unsigned long long)d.renderer_memory.peak_bytes,
        d.renderer_memory.allocation_count,
        (unsigned long long)d.output_bytes,
        (unsigned long long)d.source_bytes,
        (unsigned long long)d.backdrop_bytes,
        (unsigned long long)d.scratch_bytes,
        (unsigned long long)d.effect_bytes,
        (unsigned long long)d.frame_buffer_bytes,
        (unsigned long long)d.readback_bytes,
        (unsigned long long)d.present_bytes,
        d.present_image_count);
    if (d.readback_bytes)
        return false;
    if (!strcmp(checkpoint, "promoted_idle") || !strcmp(checkpoint, "aborted_idle"))
        return d.output_bytes == d.present_bytes && d.present_image_count == 1;
    return true;
}
struct bench_sample
{
    uint64_t                    build_ns, render_ns, retry_wait_ns, completion_ns, total_ns;
    unsigned                    retries;
    struct walle_vk_diagnostics gpu;
};
static bool bench_frame(struct walle_vk_output*  output,
                        struct walle_transition* transition,
                        double                   progress,
                        bool                     timed,
                        struct bench_sample*     sample)
{
    *sample                            = (struct bench_sample){};
    uint64_t                     start = bench_now();
    const struct walle_vk_frame* frame;
    if (!walle_transition_build(transition, progress, false, &frame) || frame->composition_readback)
        return false;
    sample->build_ns = bench_now() - start;
    enum walle_vk_frame_status status;
    do {
        uint64_t before = bench_now();
        status          = walle_vk_output_render(output, frame);
        sample->render_ns += bench_now() - before;
        if (status == WALLE_VK_FRAME_RETRY) {
            sample->retries++;
            before                = bench_now();
            struct timespec delay = {.tv_nsec = 100000};
            while (nanosleep(&delay, &delay) && errno == EINTR) {
            }
            sample->retry_wait_ns += bench_now() - before;
        }
        if (bench_now() - start > UINT64_C(30000000000))
            return false;
    } while (status == WALLE_VK_FRAME_RETRY);
    if (status != WALLE_VK_FRAME_OK)
        return false;
    uint64_t before = bench_now();
    if (timed && !walle_vk_output_diagnostics(output, true, &sample->gpu))
        return false;
    sample->completion_ns = bench_now() - before;
    sample->total_ns      = bench_now() - start;
    return !timed
           || (sample->gpu.timing_available && !sample->gpu.timing_pending
               && sample->gpu.gpu_total_ns > 0);
}
static void bench_sample_record(
    unsigned run, const char* phase, unsigned index, double progress, const struct bench_sample* s)
{
    printf("{\"type\":\"sample\",\"run\":%u,\"phase\":", run);
    bench_string(phase);
    printf(
        ",\"index\":%u,\"progress\":%.17g,\"built_backdrop\":%s,\"timed_frame_id\":%llu,\"gpu_"
        "total_ns\":%.9f,\"gpu_frame_ns\":%.9f,\"gpu_capture_ns\":%.9f,\"gpu_draw_ns\":%.9f,\"gpu_"
        "tail_ns\":%.9f,\"cpu_build_ns\":%llu,\"cpu_render_calls_ns\":%llu,\"cpu_retry_wait_ns\":%"
        "llu,\"cpu_completion_wait_ns\":%llu,\"cpu_total_ns\":%llu,\"retries\":%u}\n",
        index,
        progress,
        s->gpu.built_backdrop ? "true" : "false",
        (unsigned long long)s->gpu.timed_frame_id,
        s->gpu.gpu_total_ns,
        s->gpu.gpu_frame_ns,
        s->gpu.gpu_capture_ns,
        s->gpu.gpu_draw_ns,
        s->gpu.gpu_tail_ns,
        (unsigned long long)s->build_ns,
        (unsigned long long)s->render_ns,
        (unsigned long long)s->retry_wait_ns,
        (unsigned long long)s->completion_ns,
        (unsigned long long)s->total_ns,
        s->retries);
}
static bool bench_run(unsigned                               run,
                      const char*                            selector,
                      uint32_t                               width,
                      uint32_t                               height,
                      int                                    source_a,
                      int                                    source_b,
                      const struct walle_transition_options* options)
{
    bool                      timed = run != 0, ok = false;
    unsigned                  warmup_retries = 0;
    struct walle_vk_renderer* renderer       = nullptr;
    struct walle_vk_output*   output         = nullptr;
    struct walle_transition*  transition     = nullptr;
    if (!walle_vk_renderer_create_offscreen(selector, &renderer)
        || !walle_vk_renderer_validation_active(renderer)
        || !walle_vk_output_create_offscreen(renderer, width, height, &output))
        goto done;
    bench_device(renderer, run);
    const struct walle_vk_image_layer layer
        = {.size = (size_t)width * height * 4, .width = (int32_t)width, .height = (int32_t)height};
    if (!walle_vk_output_restore_current(output, source_a, &layer)
        || !walle_vk_output_upload(output, source_b, &layer)
        || !walle_transition_create(width, height, 1, options, &transition))
        goto done;
    struct bench_sample sample;
    if (!timed) {
        for (unsigned i = 0; i <= 120; i++) {
            if (!bench_frame(output, transition, (double)i / 120, false, &sample))
                goto done;
            warmup_retries += sample.retries;
        }
        walle_vk_output_promote(output);
        ok = true;
        printf(
            "{\"type\":\"warmup\",\"run\":0,\"frames\":121,\"retries\":%u,\"timestamps_enabled\":"
            "false}\n",
            warmup_retries);
        goto done;
    }
    if (!walle_vk_output_enable_timing(output, true)
        || !bench_frame(output, transition, .5, true, &sample))
        goto done;
    bench_sample_record(run, "first_use", 0, .5, &sample);
    if (!sample.gpu.built_backdrop || !bench_memory(output, run, "active_first"))
        goto done;
    for (unsigned i = 1; i < 120; i++) {
        double progress = (double)i / 120;
        if (!bench_frame(output, transition, progress, true, &sample))
            goto done;
        bench_sample_record(run, "warm", i, progress, &sample);
        if (sample.gpu.built_backdrop)
            goto done;
    }
    if (!bench_memory(output, run, "active_after_warm")
        || !walle_vk_output_enable_timing(output, false)
        || !bench_frame(output, transition, 1, false, &sample))
        goto done;
    walle_vk_output_promote(output);
    if (!bench_memory(output, run, "promoted_idle")
        || !walle_vk_output_restore_current(output, source_a, &layer)
        || !walle_vk_output_upload(output, source_b, &layer)
        || !bench_frame(output, transition, .5, false, &sample)
        || !bench_memory(output, run, "active_before_abort"))
        goto done;
    walle_vk_output_abort_transition(output);
    if (!bench_memory(output, run, "aborted_idle"))
        goto done;
    ok = true;
done:
    walle_transition_destroy(transition);
    walle_vk_output_destroy(output);
    struct walle_vk_memory_stats memory;
    walle_vk_renderer_memory_stats(renderer, &memory);
    uint64_t errors = walle_vk_renderer_destroy_checked(renderer);
    ok = ok && errors == 0 && memory.allocated_bytes == 0 && memory.allocation_count == 0;
    printf(
        "{\"type\":\"run_end\",\"run\":%u,\"owned_bytes_after_output_destroy\":%llu,\"owned_"
        "allocations_after_output_destroy\":%u,\"peak_owned_bytes\":%llu,\"validation_errors\":%"
        "llu,\"status\":\"%s\"}\n",
        run,
        (unsigned long long)memory.allocated_bytes,
        memory.allocation_count,
        (unsigned long long)memory.peak_bytes,
        (unsigned long long)errors,
        ok ? "PASS" : "FAIL");
    fflush(stdout);
    return ok;
}
int main(int argc, char** argv)
{
    if (argc != 11) {
        fprintf(stderr,
                "usage: benchmark DEVICE WIDTH HEIGHT A.rgba B.rgba STYLE MOTION DARK TINT_HEX "
                "CADENCE_MS\n");
        return 2;
    }
    uint32_t width  = (uint32_t)strtoul(argv[2], nullptr, 10),
             height = (uint32_t)strtoul(argv[3], nullptr, 10);
    if (!width || !height || width > 16384 || height > 16384)
        return 2;
    int         af = open(argv[4], O_RDONLY | O_CLOEXEC), bf = open(argv[5], O_RDONLY | O_CLOEXEC);
    struct stat as, bs;
    size_t      bytes = (size_t)width * height * 4;
    if (af < 0 || bf < 0 || fstat(af, &as) || fstat(bf, &bs) || as.st_size != (off_t)bytes
        || bs.st_size != (off_t)bytes)
        return 2;
    unsigned long                   tint    = strtoul(argv[9], nullptr, 16);
    struct walle_transition_options options = {
        .style     = atoi(argv[6]) ? WM_CLEAR : WM_REGULAR,
        .motion    = atoi(argv[7]) ? WALLE_TRANSITION_LENS : WALLE_TRANSITION_SWEEP,
        .dark      = atoi(argv[8]) != 0,
        .active    = true,
        .origin    = {.5, .5},
        .direction = {1, 0},
        .tint
        = {.present = tint != 0,
           .srgb
           = {(uint8_t)(tint >> 24), (uint8_t)(tint >> 16), (uint8_t)(tint >> 8), (uint8_t)tint}}};
    printf("{\"type\":\"case\",\"schema\":1,\"selector\":");
    bench_string(argv[1]);
    printf(
        ",\"width\":%u,\"height\":%u,\"scale\":1,\"style\":%u,\"motion\":%u,\"dark\":%s,\"tint_"
        "hex\":",
        width,
        height,
        options.style,
        options.motion,
        options.dark ? "true" : "false");
    bench_string(argv[9]);
    printf(
        ",\"scenario\":\"actual_app_horizontal_center\",\"origin\":[%.17g,%.17g],\"direction\":[%."
        "17g,%.17g]",
        options.origin[0],
        options.origin[1],
        options.direction[0],
        options.direction[1]);
    printf(
        ",\"cadence_ms\":%.9g,\"first_use_progress\":0.5,\"warm_progress_denominator\":120,\"warm_"
        "samples_per_run\":119,\"runs\":5,\"readback\":false,\"validation_required\":true,\"source_"
        "a\":",
        strtod(argv[10], nullptr));
    bench_string(argv[4]);
    printf(",\"source_b\":");
    bench_string(argv[5]);
    printf("}\n");
    bool ok = true;
    for (unsigned run = 0; ok && run <= 5; run++)
        ok = bench_run(run, argv[1], width, height, af, bf, &options);
    close(af);
    close(bf);
    return ok ? 0 : 1;
}
