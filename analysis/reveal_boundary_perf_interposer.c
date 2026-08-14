#define _POSIX_C_SOURCE 200809L

#include <GLES3/gl3.h>
#include <dlfcn.h>
#include <errno.h>
#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef void(GL_APIENTRYP shader_source_fn)(GLuint, GLsizei, const GLchar* const*, const GLint*);

static shader_source_fn real_shader_source;

static void fail(const char* message)
{
    fprintf(stderr, "reveal boundary performance interposer: %s\n", message);
    exit(EXIT_FAILURE);
}

static void resolve_shader_source(void)
{
    if (real_shader_source != nullptr)
        return;
    void* symbol = dlsym(RTLD_NEXT, "glShaderSource");
    if (symbol == nullptr || sizeof symbol != sizeof real_shader_source)
        fail("cannot resolve glShaderSource");
    memcpy(&real_shader_source, &symbol, sizeof symbol);
}

static char* join_sources(GLsizei count, const GLchar* const* strings, const GLint* lengths)
{
    size_t total = 0;
    for (GLsizei index = 0; index < count; ++index) {
        size_t length = lengths != nullptr && lengths[index] >= 0 ? (size_t)lengths[index]
                                                                  : strlen(strings[index]);
        if (length > SIZE_MAX - total - 1)
            return nullptr;
        total += length;
    }
    char* joined = malloc(total + 1);
    if (joined == nullptr)
        return nullptr;
    size_t offset = 0;
    for (GLsizei index = 0; index < count; ++index) {
        size_t length = lengths != nullptr && lengths[index] >= 0 ? (size_t)lengths[index]
                                                                  : strlen(strings[index]);
        memcpy(joined + offset, strings[index], length);
        offset += length;
    }
    joined[offset] = '\0';
    return joined;
}

static bool production_reveal_fragment(const char* source)
{
    return strstr(source, "#version 320 es") != nullptr
           && strstr(source, "AppleFastSqrtTable") != nullptr
           && strstr(source, "PrimitiveSlots[18]") != nullptr
           && strstr(source, "RevealCoverage") != nullptr;
}

static float requested_margin(void)
{
    const char* text = getenv("WALLE_REVEAL_EARLY_MARGIN");
    if (text == nullptr)
        return -1.0f;
    errno      = 0;
    char* end  = nullptr;
    float value = strtof(text, &end);
    if (errno != 0 || end == text || *end != '\0' || !isfinite(value) || !(value > 0.0f)
        || !(value < 1.0f)) {
        fail("WALLE_REVEAL_EARLY_MARGIN must be finite and between zero and one");
    }
    return value;
}

static char* inject_classifier(const char* source, float margin)
{
    static const char marker[] = "void main() {";
    const char* insertion = strstr(source, marker);
    if (insertion == nullptr)
        fail("production reveal main function marker is absent");
    insertion += sizeof marker - 1;

    float inside = (1.0f - margin) * (1.0f - margin);
    float outside = (1.0f + margin) * (1.0f + margin);
    char classifier[512];
    int classifier_length = snprintf(
        classifier,
        sizeof classifier,
        "\n    float earlyNativeSquared = dot(v_SDF, v_SDF);\n"
        "    if (earlyNativeSquared <= %.9g) { RevealCoverage = 1.0; return; }\n"
        "    if (earlyNativeSquared >= %.9g) { RevealCoverage = 0.0; return; }\n",
        (double)inside,
        (double)outside);
    if (classifier_length < 0 || (size_t)classifier_length >= sizeof classifier)
        fail("cannot format early classifier");

    size_t prefix_length = (size_t)(insertion - source);
    size_t source_length = strlen(source);
    size_t result_length = source_length + (size_t)classifier_length;
    char* result = malloc(result_length + 1);
    if (result == nullptr)
        fail("cannot allocate modified shader");
    memcpy(result, source, prefix_length);
    memcpy(result + prefix_length, classifier, (size_t)classifier_length);
    memcpy(result + prefix_length + (size_t)classifier_length,
           insertion,
           source_length - prefix_length + 1);
    return result;
}

void glShaderSource(GLuint shader,
                    GLsizei count,
                    const GLchar* const* strings,
                    const GLint* lengths)
{
    resolve_shader_source();
    float margin = requested_margin();
    if (!(margin > 0.0f)) {
        real_shader_source(shader, count, strings, lengths);
        return;
    }

    char* joined = join_sources(count, strings, lengths);
    if (joined == nullptr)
        fail("cannot join shader source");
    if (!production_reveal_fragment(joined)) {
        free(joined);
        real_shader_source(shader, count, strings, lengths);
        return;
    }

    char* modified = inject_classifier(joined, margin);
    const GLchar* modified_pointer = modified;
    real_shader_source(shader, 1, &modified_pointer, nullptr);
    free(modified);
    free(joined);
}
