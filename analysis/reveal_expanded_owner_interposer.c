#define glDrawElements reveal_compute_glDrawElements
#include "reveal_direct_compute_interposer.c"
#undef glDrawElements

/*
 * Analysis-only follow-up: keep the native draw and append every
 * setup-representable canonical postguard child to one expanded owner table.
 * The original geometry supplies fragment invocations. A child-owned center
 * locks all three distance samples to that child, matching the sequential
 * single-child overlay semantics of the canonical compute prototype.
 */

enum
{
    EXPANDED_OWNER_SLOT_COUNT = 12,
    EXPANDED_RASTER_COUNT     = 13,
};

static const char expanded_vertex_source[] = {
#embed "../shaders/reveal_mask.vert.glsl" limit(4096) if_empty(0) suffix(, )
    0};

static GLuint expanded_program;
static GLuint expanded_axis_texture;
static uint64_t expanded_draw_count;
static uint64_t expanded_slot_sum;
static uint64_t expanded_setup_nanoseconds;
static size_t   expanded_max_slot_count;

static char* replace_once(char* source, const char* original, const char* replacement)
{
    char* match = strstr(source, original);
    if (match == nullptr)
        fail("expanded-owner shader marker is absent");
    size_t source_size      = strlen(source);
    size_t original_size    = strlen(original);
    size_t replacement_size = strlen(replacement);
    size_t prefix_size      = (size_t)(match - source);
    size_t result_size;
    if (source_size < original_size
        || __builtin_add_overflow(source_size - original_size, replacement_size, &result_size)
        || __builtin_add_overflow(result_size, 1u, &result_size)) {
        fail("expanded-owner shader size overflows");
    }
    char* result = malloc(result_size);
    if (result == nullptr)
        fail("cannot allocate expanded-owner shader");
    memcpy(result, source, prefix_size);
    memcpy(result + prefix_size, replacement, replacement_size);
    memcpy(result + prefix_size + replacement_size,
           match + original_size,
           source_size - prefix_size - original_size + 1u);
    free(source);
    return result;
}

static char* expanded_fragment_source(void)
{
    size_t source_size = sizeof production_fragment_source;
    char*  source      = malloc(source_size);
    if (source == nullptr)
        fail("cannot allocate source fragment copy");
    memcpy(source, production_fragment_source, source_size);

    source = replace_once(source, "uniform int AxisStarts[4];", "uniform int AxisStarts[12];");
    source = replace_once(source,
                          "uniform highp usampler2D AxisTable;",
                          "uniform highp usampler2D AxisTable;\n"
                          "uniform highp usampler2D ChildAxisTable;");
    source = replace_once(source,
                          "uniform int OwnerSlotCount;",
                          "uniform int OwnerSlotCount;\nuniform int BaseOwnerSlotCount;");
    source = replace_once(source,
                          "uniform ivec2 OwnerOriginFixed[4];",
                          "uniform ivec2 OwnerOriginFixed[12];");
    source = replace_once(source,
                          "uniform ivec2 OwnerExtentFixed[4];",
                          "uniform ivec2 OwnerExtentFixed[12];");
    source = replace_once(source,
                          "uniform ivec4 OwnerBounds[4];",
                          "uniform ivec4 OwnerBounds[12];");
    source = replace_once(source,
                          "uniform int OwnerAscending[4];",
                          "uniform int OwnerAscending[12];");
    source = replace_once(source,
                          "uniform int OwnerActiveMask[4];",
                          "uniform int OwnerActiveMask[12];");
    source = replace_once(source,
                          "for (int slot = 0; slot < 4; ++slot)",
                          "for (int slot = 0; slot < 12; ++slot)");

    static const char replacement_main[] =
        "int scopedOwnerCode(ivec2 coordinate,int limit){\n"
        " int code=0;for(int slot=0;slot<12;++slot){if(slot>=limit)break;\n"
        "  ivec4 b=OwnerBounds[slot];if(coordinate.x<b.x||coordinate.y<b.y"
        "||coordinate.x>=b.z||coordinate.y>=b.w)continue;\n"
        "  int p=ownerPrimitive(slot,coordinate);"
        "if((OwnerActiveMask[slot]&(1<<p))!=0)code=slot*2+p+1;}return code;}\n"
        "vec2 axisCoordinates(ivec2 coordinate,int slot,int primitive){\n"
        " int start=AxisStarts[slot];uvec4 xa;uvec4 ya;"
        "if(slot<BaseOwnerSlotCount){"
        "xa=texelFetch(AxisTable,ivec2(coordinate.x-start,slot*2+primitive),0);"
        "ya=texelFetch(AxisTable,ivec2(coordinate.y-start,slot*2+primitive),0);}else{"
        "int child=slot-BaseOwnerSlotCount;"
        "xa=texelFetch(ChildAxisTable,ivec2(coordinate.x-start,child*2+primitive),0);"
        "ya=texelFetch(ChildAxisTable,ivec2(coordinate.y-start,child*2+primitive),0);}"
        "return vec2(uintBitsToFloat(xa.x),uintBitsToFloat(ya.y));}\n"
        "vec2 expandedCoordinates(ivec2 coordinate,int fallback_slot,"
        "int fallback_primitive,bool child_center){\n"
        " int slot=fallback_slot,primitive=fallback_primitive;"
        "if(child_center){ivec4 b=OwnerBounds[slot];"
        "if(coordinate.x>=b.x&&coordinate.y>=b.y&&coordinate.x<b.z&&coordinate.y<b.w){"
        "int p=ownerPrimitive(slot,coordinate);"
        "if((OwnerActiveMask[slot]&(1<<p))!=0)primitive=p;}}else{"
        "int code=scopedOwnerCode(coordinate,BaseOwnerSlotCount);"
        "if(code>0){--code;slot=code/2;primitive=code&1;}}"
        "return axisCoordinates(coordinate,slot,primitive);}\n"
        "void main(){ivec2 c=ivec2(int(gl_FragCoord.x),"
        "int(RevealResolution.y)-1-int(gl_FragCoord.y));"
        "int code=scopedOwnerCode(c,OwnerSlotCount);"
        "int slot=PrimitiveSlots[gl_PrimitiveID];"
        "int primitive=PrimitiveRows[gl_PrimitiveID];"
        "if(code>0){--code;slot=code/2;primitive=code&1;}"
        "bool child_center=slot>=BaseOwnerSlotCount;"
        "float d=appleLength(expandedCoordinates(c,slot,primitive,child_center));"
        "float dx=appleLength(expandedCoordinates(ivec2(c.x^1,c.y),slot,primitive,child_center));"
        "float dy=appleLength(expandedCoordinates(ivec2(c.x,c.y^1),slot,primitive,child_center));"
        "if(v_SDF.x>1.0e30)d=v_SDF.x;"
        "float feather=max(abs(dx-d)+abs(dy-d),1.0e-4);"
        "float alpha=clamp((1.0-d)/feather+0.5,0.0,1.0);"
        "float half_alpha=alpha==0.0||alpha==1.0?alpha:"
        "unpackHalf2x16(float32ToFloat16RNEBits(alpha)).x;"
        "RevealCoverage=roundEven(half_alpha*255.0)/255.0;}\n";

    char* main = strstr(source, "void main() {");
    if (main == nullptr)
        fail("expanded-owner fragment main is absent");
    size_t prefix_size = (size_t)(main - source);
    size_t result_size;
    if (__builtin_add_overflow(prefix_size, sizeof replacement_main, &result_size))
        fail("expanded-owner replacement main overflows");
    char* result = malloc(result_size);
    if (result == nullptr)
        fail("cannot allocate expanded-owner replacement main");
    memcpy(result, source, prefix_size);
    memcpy(result + prefix_size, replacement_main, sizeof replacement_main);
    free(source);
    return result;
}

static GLuint expanded_compile_shader(GLenum type, const char* source)
{
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, nullptr);
    glCompileShader(shader);
    GLint compiled = GL_FALSE;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &compiled);
    if (compiled == GL_TRUE)
        return shader;
    char    log[8192];
    GLsizei length = 0;
    glGetShaderInfoLog(shader, sizeof log, &length, log);
    fprintf(stderr, "expanded-owner shader compilation failed:\n%.*s\n", (int)length, log);
    exit(EXIT_FAILURE);
}

static GLuint expanded_link_program(void)
{
    char*  fragment_source = expanded_fragment_source();
    GLuint vertex          = expanded_compile_shader(GL_VERTEX_SHADER, expanded_vertex_source);
    GLuint fragment        = expanded_compile_shader(GL_FRAGMENT_SHADER, fragment_source);
    free(fragment_source);
    GLuint program = glCreateProgram();
    glAttachShader(program, vertex);
    glAttachShader(program, fragment);
    glLinkProgram(program);
    glDeleteShader(vertex);
    glDeleteShader(fragment);
    GLint linked = GL_FALSE;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (linked == GL_TRUE)
        return program;
    char    log[8192];
    GLsizei length = 0;
    glGetProgramInfoLog(program, sizeof log, &length, log);
    fprintf(stderr, "expanded-owner program link failed:\n%.*s\n", (int)length, log);
    exit(EXIT_FAILURE);
}

struct raster_collection
{
    struct walle_lg_reveal_raster rasters[EXPANDED_RASTER_COUNT];
    size_t                         raster_count;
    size_t                         slot_count;
};

static void destroy_collection(struct raster_collection* collection)
{
    for (size_t index = 0; index < collection->raster_count; ++index)
        walle_lg_reveal_raster_destroy(&collection->rasters[index]);
    *collection = (struct raster_collection){};
}

static struct walle_lg_raster_calibration expanded_calibration(void)
{
    return (struct walle_lg_raster_calibration){
        .p25_ceil_bits          = reveal_raster_p25,
        .p25_selector_bit_count = UINT64_C(1) << 24,
    };
}

static bool append_child_raster(struct raster_collection*     collection,
                                const struct clip_vertex       triangle[static 3],
                                bool                           compact)
{
    if (collection->raster_count >= EXPANDED_RASTER_COUNT)
        fail("expanded-owner raster collection is full");
    struct walle_lg_reveal_mask_geometry geometry = {
        .family = compact ? WALLE_LG_REVEAL_MASK_COMPACT_VISIBLE_ARCS
                          : WALLE_LG_REVEAL_MASK_BORDER_GRID,
        .vertex_count = 3,
        .index_count  = 6,
        .indices      = {0, 1, 2, 0, 0, 0},
    };
    for (size_t vertex = 0; vertex < 3; ++vertex) {
        memcpy(geometry.vertices[vertex].position,
               triangle[vertex].position,
               sizeof triangle[vertex].position);
        float* coordinates = compact ? geometry.vertices[vertex].first_coordinates
                                     : geometry.vertices[vertex].second_coordinates;
        memcpy(coordinates, triangle[vertex].sdf, sizeof triangle[vertex].sdf);
    }
    struct walle_lg_reveal_raster* raster = &collection->rasters[collection->raster_count];
    const struct walle_lg_raster_calibration calibration = expanded_calibration();
    if (!walle_lg_reveal_raster_construct(&geometry, &calibration, raster)) {
        ++unsupported_overlay_count;
        return false;
    }
    if (raster->quad_count == 0) {
        walle_lg_reveal_raster_destroy(raster);
        return true;
    }
    if (raster->quad_count != 1 || raster->packed_width == 0 || raster->packed_words == nullptr)
        fail("expanded-owner child raster is malformed");
    if (collection->slot_count >= EXPANDED_OWNER_SLOT_COUNT)
        fail("expanded-owner slot table is full");
    ++collection->raster_count;
    ++collection->slot_count;
    ++overlay_triangle_count;
    return true;
}

static bool construct_collection(GLuint source_program,
                                 struct raster_collection* collection,
                                 bool*                      compact_result)
{
    if (captured_vertex_count == 0 || captured_index_count == 0
        || captured_index_count % 6 != 0) {
        fail("captured reveal geometry is incomplete");
    }
    GLfloat compact_value = 0.0f;
    glGetUniformfv(
        source_program, location(source_program, "RevealCompactFamily"), &compact_value);
    bool compact = compact_value == 1.0f;
    *compact_result = compact;

    size_t group_count = captured_index_count / 6;
    if (!compact && group_count > 4)
        group_count = 4;
    for (size_t group = 0; group < group_count; ++group) {
        struct clip_vertex source[6];
        for (size_t local = 0; local < 6; ++local) {
            uint16_t index = captured_indices[group * 6 + local];
            if (index >= captured_vertex_count)
                fail("captured reveal index is out of range");
            const struct walle_lg_reveal_mask_vertex* vertex = &captured_vertices[index];
            memcpy(source[local].position, vertex->position, sizeof source[local].position);
            const float* coordinates
                = compact ? vertex->first_coordinates : vertex->second_coordinates;
            memcpy(source[local].sdf, coordinates, sizeof source[local].sdf);
        }

        size_t first_triangle = 0;
        size_t triangle_count = 2;
        if (compact) {
            bool first_active  = triangle_area(source) != 0.0;
            bool second_active = triangle_area(source + 3) != 0.0;
            if (first_active == second_active)
                fail("compact group does not have exactly one active triangle");
            first_triangle = first_active ? 0 : 1;
            triangle_count = 1;
        }
        for (size_t ordinal = first_triangle; ordinal < first_triangle + triangle_count; ++ordinal) {
            const struct clip_vertex* triangle = source + ordinal * 3;
            if (!source_triangle_outside_guard(triangle))
                continue;
            struct clip_vertex clipped[8];
            size_t clipped_count = clip_triangle(triangle, clipped);
            if (clipped_count == 3) {
                if (triangle_area(clipped) != 0.0)
                    (void)append_child_raster(collection, clipped, compact);
            } else if (clipped_count == 4) {
                struct clip_vertex first[3]  = {clipped[0], clipped[1], clipped[2]};
                struct clip_vertex second[3] = {clipped[0], clipped[2], clipped[3]};
                if (triangle_area(first) != 0.0)
                    (void)append_child_raster(collection, first, compact);
                if (triangle_area(second) != 0.0)
                    (void)append_child_raster(collection, second, compact);
            } else if (clipped_count > 4) {
                fail("expanded-owner clip produced an unsupported polygon");
            }
        }
    }
    return true;
}

static uint32_t* pack_collection(const struct raster_collection* collection,
                                 size_t base_slot_count,
                                 GLint axis_starts[static EXPANDED_OWNER_SLOT_COUNT],
                                 GLint origins[static EXPANDED_OWNER_SLOT_COUNT * 2],
                                 GLint extents[static EXPANDED_OWNER_SLOT_COUNT * 2],
                                 GLint bounds[static EXPANDED_OWNER_SLOT_COUNT * 4],
                                 GLint ascending[static EXPANDED_OWNER_SLOT_COUNT],
                                 GLint active_masks[static EXPANDED_OWNER_SLOT_COUNT],
                                 uint32_t* packed_width_result)
{
    uint32_t packed_width = 0;
    for (size_t index = 0; index < collection->raster_count; ++index) {
        if (packed_width < collection->rasters[index].packed_width)
            packed_width = collection->rasters[index].packed_width;
    }
    if (packed_width == 0 || packed_width > (uint32_t)INT32_MAX)
        fail("expanded-owner packed width is invalid");
    size_t word_count;
    if (__builtin_mul_overflow(collection->slot_count, 2u, &word_count)
        || __builtin_mul_overflow(word_count, (size_t)packed_width, &word_count)
        || __builtin_mul_overflow(word_count, 2u, &word_count)) {
        fail("expanded-owner packed word count overflows");
    }
    uint32_t* packed_words = calloc(word_count, sizeof *packed_words);
    if (packed_words == nullptr)
        fail("cannot allocate expanded-owner packed words");

    size_t child_slot = 0;
    for (size_t index = 0; index < collection->raster_count; ++index) {
        const struct walle_lg_reveal_raster* raster = &collection->rasters[index];
        for (size_t source_slot = 0; source_slot < raster->quad_count; ++source_slot) {
            const struct walle_lg_reveal_raster_quad* quad = &raster->quads[source_slot];
            size_t owner_slot = base_slot_count + child_slot;
            axis_starts[owner_slot] = quad->axis_start;
            for (size_t axis = 0; axis < 2; ++axis) {
                origins[owner_slot * 2 + axis] = quad->origin_fixed[axis];
                extents[owner_slot * 2 + axis] = quad->extent_fixed[axis];
            }
            for (size_t component = 0; component < 4; ++component)
                bounds[owner_slot * 4 + component] = quad->visible_bounds[component];
            ascending[owner_slot]    = quad->ascending_diagonal ? 1 : 0;
            active_masks[owner_slot] = quad->active_primitive_mask;
            for (size_t primitive = 0; primitive < 2; ++primitive) {
                size_t source_offset = (source_slot * 2 + primitive)
                                       * (size_t)raster->packed_width * 2;
                size_t destination_offset = (child_slot * 2 + primitive)
                                            * (size_t)packed_width * 2;
                memcpy(packed_words + destination_offset,
                       raster->packed_words + source_offset,
                       (size_t)raster->packed_width * 2 * sizeof *packed_words);
            }
            ++child_slot;
        }
    }
    if (child_slot != collection->slot_count)
        fail("expanded-owner slot packing differs");
    *packed_width_result = packed_width;
    return packed_words;
}

static void read_scalar_uniforms(GLuint source,
                                 const char* base,
                                 size_t      count,
                                 GLint       values[static count])
{
    char name[64];
    for (size_t index = 0; index < count; ++index) {
        int length = snprintf(name, sizeof name, "%s[%zu]", base, index);
        if (length < 0 || (size_t)length >= sizeof name)
            fail("expanded-owner scalar uniform name overflows");
        glGetUniformiv(source, location(source, name), &values[index]);
    }
}

static void read_vector_uniforms(GLuint source,
                                 const char* base,
                                 size_t      count,
                                 size_t      components,
                                 GLint       values[static count * components])
{
    char name[64];
    for (size_t index = 0; index < count; ++index) {
        int length = snprintf(name, sizeof name, "%s[%zu]", base, index);
        if (length < 0 || (size_t)length >= sizeof name)
            fail("expanded-owner vector uniform name overflows");
        glGetUniformiv(source, location(source, name), &values[index * components]);
    }
}

static void draw_expanded_owners(GLuint       source_program,
                                 GLenum       mode,
                                 GLsizei      count,
                                 GLenum       type,
                                 const void*  indices)
{
    struct raster_collection collection = {};
    bool compact;
    struct timespec setup_start;
    struct timespec setup_finish;
    if (clock_gettime(CLOCK_MONOTONIC, &setup_start) != 0)
        fail("cannot start expanded-owner setup clock");
    (void)construct_collection(source_program, &collection, &compact);
    if (clock_gettime(CLOCK_MONOTONIC, &setup_finish) != 0)
        fail("cannot stop expanded-owner setup clock");
    expanded_setup_nanoseconds += elapsed_nanoseconds(setup_start, setup_finish);
    GLint base_slot_count_value;
    glGetUniformiv(source_program,
                   location(source_program, "OwnerSlotCount"),
                   &base_slot_count_value);
    if (base_slot_count_value <= 0 || base_slot_count_value > 4)
        fail("source base owner count is invalid");
    size_t base_slot_count = (size_t)base_slot_count_value;
    size_t total_slot_count;
    if (__builtin_add_overflow(base_slot_count, collection.slot_count, &total_slot_count)
        || total_slot_count > EXPANDED_OWNER_SLOT_COUNT) {
        fail("expanded-owner combined slot count is invalid");
    }
    expanded_slot_sum += total_slot_count;
    if (expanded_max_slot_count < total_slot_count)
        expanded_max_slot_count = total_slot_count;
    if (collection.slot_count == 0) {
        real_draw_elements(mode, count, type, indices);
        destroy_collection(&collection);
        require_gl("draw unchanged base owners");
        ++expanded_draw_count;
        ++dispatch_count;
        return;
    }
    if (expanded_program == 0)
        expanded_program = expanded_link_program();

    GLint axis_starts[EXPANDED_OWNER_SLOT_COUNT]      = {};
    GLint origins[EXPANDED_OWNER_SLOT_COUNT * 2]      = {};
    GLint extents[EXPANDED_OWNER_SLOT_COUNT * 2]      = {};
    GLint bounds[EXPANDED_OWNER_SLOT_COUNT * 4]       = {};
    GLint ascending[EXPANDED_OWNER_SLOT_COUNT]        = {};
    GLint active_masks[EXPANDED_OWNER_SLOT_COUNT]     = {};
    GLint primitive_slots[WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT] = {};
    GLint primitive_rows[WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT]  = {};
    read_scalar_uniforms(source_program, "AxisStarts", base_slot_count, axis_starts);
    read_vector_uniforms(source_program, "OwnerOriginFixed", base_slot_count, 2, origins);
    read_vector_uniforms(source_program, "OwnerExtentFixed", base_slot_count, 2, extents);
    read_vector_uniforms(source_program, "OwnerBounds", base_slot_count, 4, bounds);
    read_scalar_uniforms(source_program, "OwnerAscending", base_slot_count, ascending);
    read_scalar_uniforms(source_program, "OwnerActiveMask", base_slot_count, active_masks);
    read_scalar_uniforms(source_program,
                         "PrimitiveSlots",
                         WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT,
                         primitive_slots);
    read_scalar_uniforms(source_program,
                         "PrimitiveRows",
                         WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT,
                         primitive_rows);
    uint32_t packed_width;
    uint32_t* packed_words = pack_collection(&collection,
                                             base_slot_count,
                                             axis_starts,
                                             origins,
                                             extents,
                                             bounds,
                                             ascending,
                                             active_masks,
                                             &packed_width);

    GLint prior_active;
    GLint prior_binding;
    GLint prior_unpack_buffer;
    glGetIntegerv(GL_ACTIVE_TEXTURE, &prior_active);
    glGetIntegerv(GL_PIXEL_UNPACK_BUFFER_BINDING, &prior_unpack_buffer);
    glBindBuffer(GL_PIXEL_UNPACK_BUFFER, 0);
    glActiveTexture(GL_TEXTURE13);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &prior_binding);
    if (expanded_axis_texture == 0)
        glGenTextures(1, &expanded_axis_texture);
    glBindTexture(GL_TEXTURE_2D, expanded_axis_texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexImage2D(GL_TEXTURE_2D,
                 0,
                 GL_RG32UI,
                 (GLsizei)packed_width,
                 (GLsizei)(collection.slot_count * 2),
                 0,
                 GL_RG_INTEGER,
                 GL_UNSIGNED_INT,
                 packed_words);
    free(packed_words);

    GLfloat resolution[2];
    GLint   axis_unit;
    GLint   sqrt_unit;
    glGetUniformfv(
        source_program, location(source_program, "RevealResolution"), resolution);
    glGetUniformiv(source_program, location(source_program, "AxisTable"), &axis_unit);
    glGetUniformiv(
        source_program, location(source_program, "AppleFastSqrtTable"), &sqrt_unit);

    glUseProgram(expanded_program);
    glUniform2fv(location(expanded_program, "RevealResolution"), 1, resolution);
    glUniform1f(location(expanded_program, "RevealCompactFamily"), compact ? 1.0f : 0.0f);
    glUniform1i(location(expanded_program, "AxisTable"), axis_unit);
    glUniform1i(location(expanded_program, "ChildAxisTable"), 13);
    glUniform1i(location(expanded_program, "AppleFastSqrtTable"), sqrt_unit);
    glUniform1iv(location(expanded_program, "AxisStarts"),
                 EXPANDED_OWNER_SLOT_COUNT,
                 axis_starts);
    glUniform1iv(location(expanded_program, "PrimitiveSlots"),
                 WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT,
                 primitive_slots);
    glUniform1iv(location(expanded_program, "PrimitiveRows"),
                 WALLE_LG_REVEAL_RASTER_MAX_PRIMITIVE_COUNT,
                 primitive_rows);
    glUniform1i(location(expanded_program, "BaseOwnerSlotCount"), base_slot_count_value);
    glUniform1i(location(expanded_program, "OwnerSlotCount"), (GLint)total_slot_count);
    glUniform2iv(location(expanded_program, "OwnerOriginFixed"),
                 EXPANDED_OWNER_SLOT_COUNT,
                 origins);
    glUniform2iv(location(expanded_program, "OwnerExtentFixed"),
                 EXPANDED_OWNER_SLOT_COUNT,
                 extents);
    glUniform4iv(location(expanded_program, "OwnerBounds"),
                 EXPANDED_OWNER_SLOT_COUNT,
                 bounds);
    glUniform1iv(location(expanded_program, "OwnerAscending"),
                 EXPANDED_OWNER_SLOT_COUNT,
                 ascending);
    glUniform1iv(location(expanded_program, "OwnerActiveMask"),
                 EXPANDED_OWNER_SLOT_COUNT,
                 active_masks);
    require_gl("upload expanded-owner state");

    real_draw_elements(mode, count, type, indices);
    require_gl("draw expanded owners");
    ++expanded_draw_count;
    ++dispatch_count;

    glUseProgram(source_program);
    glBindTexture(GL_TEXTURE_2D, (GLuint)prior_binding);
    glActiveTexture((GLenum)prior_active);
    glBindBuffer(GL_PIXEL_UNPACK_BUFFER, (GLuint)prior_unpack_buffer);
    destroy_collection(&collection);
    require_gl("restore expanded-owner state");
}

__attribute__((visibility("default"))) GL_APICALL void GL_APIENTRY
glDrawElements(GLenum mode, GLsizei count, GLenum type, const void* indices)
{
    if (real_draw_elements == nullptr)
        load_real_draw();
    if (!enabled() || mode != GL_TRIANGLES || type != GL_UNSIGNED_SHORT || indices != nullptr
        || count <= 0 || (size_t)count != captured_index_count) {
        real_draw_elements(mode, count, type, indices);
        return;
    }
    GLint source_program = 0;
    glGetIntegerv(GL_CURRENT_PROGRAM, &source_program);
    if (source_program <= 0
        || glGetUniformLocation((GLuint)source_program, "OwnerSlotCount") < 0) {
        real_draw_elements(mode, count, type, indices);
        return;
    }
    draw_expanded_owners((GLuint)source_program, mode, count, type, indices);
}

__attribute__((destructor)) static void report_expanded(void)
{
    if (!enabled())
        return;
    fprintf(stderr,
            "REVEAL_EXPANDED_OWNER_DRAWS=%llu\n"
            "REVEAL_EXPANDED_OWNER_SLOT_SUM=%llu\n"
            "REVEAL_EXPANDED_OWNER_SETUP_NANOSECONDS=%llu\n"
            "REVEAL_EXPANDED_OWNER_MAX_SLOTS=%zu\n",
            (unsigned long long)expanded_draw_count,
            (unsigned long long)expanded_slot_sum,
            (unsigned long long)expanded_setup_nanoseconds,
            expanded_max_slot_count);
}
