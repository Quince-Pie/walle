#version 300 es
precision highp float;

in vec2 v_UV;
out vec4 FragColor;

/*
 * Liquid Glass transition — regular and clear variants.
 *
 * The optical model is fitted to Apple's primary sources: HIG "Materials"
 * (Sep 9 2025 revision) prose plus direct pixel measurements of its variant
 * photos (materials-ios-liquid-glass-{over-light,over-dark,clear}@2x.png,
 * D = element diameter ~316 px). Measured, not folklore:
 *
 *  - REGULAR ("blurs and adjusts the luminosity of background content"):
 *    blur sigma ~ 0.038*D; luminosity is tone-mapped onto a PLATTER whose
 *    level is bimodal in background luminance — over light content the body
 *    sits at ~0.78 linear (p5 luma lifted 0.09 -> 0.51!), over dark content
 *    it is a near-flat dark platter (sRGB ~0.11, interior std 0.4%) that
 *    keeps a hue hint (chroma ~1.9x the blurred bg). Content modulation
 *    survives at ~38% (light) / ~10% (dark).
 *  - CLEAR ("highly translucent... visually rich backgrounds remain
 *    prominent"): blur sigma ~ 0.013*D, chroma ~86% and contrast ~78%
 *    retained, only a whisper of lift (+0.01 linear). HIG mandates an
 *    optional dark dimming layer of 35% opacity when the underlying content
 *    is bright — implemented content-adaptively below.
 *  - HIGHLIGHTS: one specular ring of radius 0.973*R displaced 0.021*R
 *    screen-down; where it meets the rim (bottom) it is a ~0.007*R razor
 *    line, where it floats inside (top) it widens to a ~0.02*R soft glow.
 *    Two intensity lobes on the light axis: +0.20 linear (upper), +0.13
 *    (lower), +0.04 ambient ring. The axis travels during the transition
 *    ("lights move in space, causing light to travel around the material").
 *  - LENSING: refraction band ~0.10*R at the rim. The extreme edge
 *    CONCENTRATES light ("bends, shapes, and concentrates light in real
 *    time"): bright over light content, dark over dark — the photos show no
 *    painted edge line, it is bent content. The platter adaptation is
 *    relieved inside the band (measured: dark photo edge 0.07 sRGB < platter
 *    0.11), so raw bent content reads through at the rim.
 *  - SHADOW: a tight contact ring (penumbra ~0.035*R, slight downward bias),
 *    content-adaptive per HIG, plus a hairline dark outline. Clear carries
 *    only a faint outline — its photo shows no measurable penumbra.
 *
 * Objects "materialize in and out by gradually modulating the light bending
 * and lensing" instead of fading — `thickness` drives every material term.
 *
 * Textures are sRGB (GL_SRGB8_ALPHA8): sampling yields linear light, all
 * math below is linear, the final value is re-encoded + dithered.
 */

// --- Textures ---
uniform sampler2D TexA;      // outgoing wallpaper, sharp
uniform sampler2D TexGlassA; // outgoing, pre-blurred (probe: what is under the shadow)
uniform sampler2D TexB;      // incoming wallpaper, sharp
uniform sampler2D TexGlassB; // incoming, pre-blurred per variant (glass body)

// --- Uniforms ---
uniform float Time;              // LINEAR normalized time [0,1]
uniform vec2  Resolution;        // buffer px
uniform vec2  CenterPointPixels; // transition origin, GL coords (y up)
uniform float MaxRadiusPixels;
uniform float Variant;           // 0.0 = clear, 1.0 = regular

// --- Timeline ---
const float T_EXPAND_END  = 0.62;
const float T_FADE_START  = 0.66;
const float T_MATERIALIZE = 0.12;

// --- Lensing ---
const float LENS_WIDTH_FRAC    = 0.10;  // band as fraction of current radius
const float LENS_WIDTH_MIN     = 22.0;  // px
const float LENS_WIDTH_MAXDIAG = 0.035; // cap: fraction of output diagonal
const float LENS_BEND_CLEAR    = 0.90;  // peak displacement, fraction of band
const float LENS_BEND_REGULAR  = 0.55;
const float LENS_DISPERSION    = 0.035; // chromatic split of the bend (motion only)
const float EDGE_CLARITY       = 0.85;  // clear: bent SHARP content in the band
const float EDGE_CONCENTRATION = 0.30;  // extreme-edge light concentration gain
const float EDGE_ADAPT_RELIEF  = 0.55;  // platter adaptation released at the rim

// --- Highlights ring (all fractions of current radius) ---
const float RING_RADIUS_FRAC = 0.973;
const float RING_OFFSET_FRAC = 0.021; // ring center displaced screen-down
const float RING_W_EDGE_FRAC = 0.007; // razor where ring meets rim (bottom)
const float RING_W_TOP_FRAC  = 0.020; // soft glow where it floats inside (top)
const float RING_UP_GAIN     = 0.20;  // linear-light adds
const float RING_DOWN_GAIN   = 0.13;
const float RING_BASE_GAIN   = 0.04;

// --- Regular platter (linear light) ---
const float PLATTER_DARK_Y  = 0.012; // sRGB ~0.11 measured
const float PLATTER_LIGHT_Y = 0.78;  // sRGB ~0.91 measured
const float ADAPT_DARK      = 0.90;  // mix strength toward the platter
const float ADAPT_LIGHT     = 0.62;
const float CHROMA_DARK     = 1.89;  // platter chroma vs blurred content
const float CHROMA_LIGHT    = 0.11;

// --- Clear body ---
const float CLEAR_GAIN = 0.93;  // slight highlight compression
const float CLEAR_LIFT = 0.012; // whisper of frost
const float DIM_MAX    = 0.35;  // HIG: dark dimming layer, 35% over bright content

// --- Grounding ---
const float SHADOW_OFF_FRAC = 0.012; // downward bias of the contact ring
const float SHADOW_PEN_FRAC = 0.035; // penumbra
const float SHADOW_PEN_MIN  = 10.0;  // px
const float SHADOW_BASE     = 0.16;
const float OUTLINE_WIDTH   = 2.0;   // px, hairline dark outline
const float OUTLINE_DARK    = 0.10;

const float GLOW_INTENSITY = 0.20; // interaction illumination at the origin
const float DITHER_LSB     = 1.0 / 255.0;

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

float linToSrgb1(float c) {
    return c <= 0.0031308 ? 12.92 * c : 1.055 * pow(c, 1.0 / 2.4) - 0.055;
}
vec3 linToSrgb(vec3 c) {
    return vec3(linToSrgb1(c.r), linToSrgb1(c.g), linToSrgb1(c.b));
}

float hash12(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

// Triangular-PDF dither (sum of two independent uniforms, centered).
float triDither(vec2 p) {
    return (hash12(p) + hash12(p + 19.19)) - 1.0;
}

// Gel-like settle: slight overshoot past the target radius, then relax.
// ("gel-like flexibility... moves in tandem with your interaction")
float easeGel(float t) {
    float c1 = 0.28;
    float c3 = c1 + 1.0;
    float u  = t - 1.0;
    return 1.0 + c3 * u * u * u + c1 * u * u;
}

void main() {
    // 1. Timeline. Material thickness modulates every optical term: the
    // documented alternative to opacity fades.
    float t_expand  = easeGel(smoothstep(0.0, T_EXPAND_END, Time));
    float t_fade    = smoothstep(T_FADE_START, 1.0, Time);
    float thickness = smoothstep(0.0, T_MATERIALIZE, Time) * (1.0 - t_fade);
    float regular   = Variant;

    // 2. Geometry (SDF of the expanding circle).
    vec2  frag_px   = gl_FragCoord.xy;
    vec2  delta     = frag_px - CenterPointPixels;
    float dist_px   = length(delta);
    float radius_px = t_expand * MaxRadiusPixels;
    float sdf       = dist_px - radius_px;
    vec2  n_out     = dist_px > 0.5 ? delta / dist_px : vec2(0.0, 1.0);
    float mask      = 1.0 - smoothstep(-0.75, 0.75, sdf);

    float shadow_off = SHADOW_OFF_FRAC * radius_px;
    float shadow_pen = max(SHADOW_PEN_FRAC * radius_px, SHADOW_PEN_MIN);

    // 3. Early out beyond glass, outline, and shadow reach.
    if (sdf > shadow_off + shadow_pen + 8.0) {
        FragColor = vec4(linToSrgb(texture(TexA, v_UV).rgb), 1.0);
        return;
    }

    // 4. Grounding: tight contact ring + hairline outline (no big drop
    // shadow — the photos show a ~0.035*R penumbra hugging the rim with a
    // slight downward bias). Content-adaptive per HIG ("increases the
    // opacity of its shadow when it is over text... lowers it over a solid
    // light background"); probe = blurred OUTGOING image (under the shadow).
    float sdf_sh   = distance(frag_px - vec2(0.0, -shadow_off), CenterPointPixels) - radius_px;
    float penumbra = 1.0 - smoothstep(0.0, shadow_pen, sdf_sh);
    float bg_luma  = dot(texture(TexGlassA, v_UV).rgb, LUMA);
    float sh_adapt = mix(1.25, 0.60, smoothstep(0.03, 0.35, bg_luma));
    float outline  = smoothstep(-0.5, 0.5, sdf)
                   * (1.0 - smoothstep(OUTLINE_WIDTH * 0.5, OUTLINE_WIDTH * 1.75, sdf));
    float sh_alpha = penumbra * SHADOW_BASE * sh_adapt * mix(0.45, 1.0, regular)
                   + outline * OUTLINE_DARK * mix(0.80, 1.0, regular);
    sh_alpha *= (1.0 - mask) * thickness;

    // 5. Lensing: displacement concentrated in the edge band, zero in the
    // body. Peaks at the rim so the interior stays optically calm.
    float diag   = length(Resolution);
    float lens_w = clamp(radius_px * LENS_WIDTH_FRAC, LENS_WIDTH_MIN, LENS_WIDTH_MAXDIAG * diag);
    float band   = clamp(-sdf / lens_w, 0.0, 1.0); // 0 at rim -> 1 deep inside
    float p_lens = (1.0 - band) * step(sdf, 0.0);  // 1 at rim -> 0 inward
    float bend   = p_lens * p_lens * lens_w * mix(LENS_BEND_CLEAR, LENS_BEND_REGULAR, regular)
                 * thickness;

    // v_UV.y is flipped relative to gl_FragCoord.y: negate y for UV offsets.
    vec2  px2uv = 1.0 / Resolution;
    vec2  uv    = v_UV;
    vec2  duv   = vec2(n_out.x, -n_out.y) * bend * px2uv;
    float disp  = LENS_DISPERSION * mix(1.0, 0.45, regular);

    // The extreme edge concentrates the light it bends: bright content makes
    // a bright edge, dark content a dark one — never a painted line.
    float conc = 1.0 + EDGE_CONCENTRATION * p_lens * p_lens * p_lens * p_lens * thickness;

    // 6. Body per variant (uniform control flow: Variant is a uniform).
    vec3 glass_col;
    if (regular > 0.5) {
        // The blurred body itself bends at the edge...
        vec3 body;
        body.r = texture(TexGlassB, uv + duv * (1.0 + disp)).r;
        body.g = texture(TexGlassB, uv + duv).g;
        body.b = texture(TexGlassB, uv + duv * (1.0 - disp)).b;
        body *= conc;

        // ...then luminosity is tone-mapped onto the platter. Bimodal in
        // background luminance; the band is relieved of adaptation so bent
        // content reads through. The platter's hue wash comes from widely
        // spaced taps: Apple amplifies the DC chroma of the content behind
        // the element while flattening its variation (measured over dark:
        // chroma mean 2x background, chroma std 1/3 background) — per-pixel
        // chroma keep would let structure bleed through the platter.
        vec2 e1 = vec2(0.30, 0.0);
        vec2 e2 = vec2(0.0, 0.30);
        vec2 e3 = vec2(0.21, 0.21);
        vec2 e4 = vec2(0.21, -0.21);
        vec3 wash = (body + texture(TexGlassB, uv + e1).rgb + texture(TexGlassB, uv - e1).rgb
                     + texture(TexGlassB, uv + e2).rgb + texture(TexGlassB, uv - e2).rgb
                     + texture(TexGlassB, uv + e3).rgb + texture(TexGlassB, uv - e3).rgb
                     + texture(TexGlassB, uv + e4).rgb + texture(TexGlassB, uv - e4).rgb)
                  / 9.0;
        float Yw        = dot(wash, LUMA);
        float lightness = smoothstep(0.02, 0.30, Yw);
        float adapt     = mix(ADAPT_DARK, ADAPT_LIGHT, lightness) * thickness
                        * (1.0 - EDGE_ADAPT_RELIEF * p_lens);
        float ck      = mix(CHROMA_DARK, CHROMA_LIGHT, lightness);
        vec3  platter = vec3(mix(PLATTER_DARK_Y, PLATTER_LIGHT_Y, lightness))
                      + (wash - vec3(Yw)) * ck;
        glass_col = mix(body, platter, adapt);
    } else {
        // Clear: content passes through nearly untouched; the band shows
        // bent SHARP content ("ensuring visually rich background elements
        // remain prominent" while the edge visibly refracts).
        vec3 refracted;
        refracted.r = texture(TexB, uv + duv * (1.0 + disp)).r;
        refracted.g = texture(TexB, uv + duv).g;
        refracted.b = texture(TexB, uv + duv * (1.0 - disp)).b;
        refracted *= conc;

        vec3  body    = texture(TexGlassB, uv).rgb * CLEAR_GAIN + vec3(CLEAR_LIFT);
        float clarity = EDGE_CLARITY * p_lens * sqrt(p_lens) * thickness;
        glass_col     = mix(body, refracted, clarity);

        // HIG dimming layer: "if the underlying content is bright, consider
        // adding a dark dimming layer of 35% opacity; if sufficiently dark,
        // you don't need one." (Bricks photo Ylin 0.087 -> none. Verified.)
        float Yb  = dot(texture(TexGlassB, uv).rgb, LUMA);
        float dim = DIM_MAX * smoothstep(0.10, 0.42, Yb) * thickness;
        glass_col *= 1.0 - dim;
    }

    // 7. Highlights: ONE specular ring, radius 0.973*R, center displaced
    // 0.021*R screen-down (GL -y). Where it meets the rim it is a razor
    // line; where it floats inside (top) it reads as the soft inner glow.
    // Both measured variants share it.
    vec2  ring_c   = CenterPointPixels - vec2(0.0, RING_OFFSET_FRAC * radius_px);
    vec2  rd       = frag_px - ring_c;
    float rd_len   = length(rd);
    vec2  n_ring   = rd_len > 0.5 ? rd / rd_len : vec2(0.0, 1.0);
    float ring_sdf = rd_len - RING_RADIUS_FRAC * radius_px;
    float w_edge   = clamp(RING_W_EDGE_FRAC * radius_px, 1.25, 5.0);
    float w_top    = clamp(RING_W_TOP_FRAC * radius_px, 3.0, 24.0);
    float ring_w   = mix(w_edge, w_top, smoothstep(0.0, 1.0, max(n_ring.y, 0.0)));
    float ring_pr  = exp(-(ring_sdf * ring_sdf) / (ring_w * ring_w));

    // Light axis travels while the material exists ("light to travel around
    // the material"): 125 deg -> 95 deg, spanning the measured static axis.
    float ang   = radians(125.0) - radians(30.0) * Time;
    vec2  L     = vec2(cos(ang), sin(ang));
    float n_dot = dot(n_ring, L);
    float lobes = RING_BASE_GAIN + RING_UP_GAIN * pow(max(n_dot, 0.0), 1.6)
                + RING_DOWN_GAIN * pow(max(-n_dot, 0.0), 2.0);
    vec3 ring_light = vec3(ring_pr * lobes * thickness * mask);

    // 8. Interaction illumination: "illuminates from within... starting
    // right under your fingertips" — the origin — receding as it expands.
    float glow_t = thickness * (1.0 - smoothstep(0.18, 0.55, Time));
    float glow   = exp(-(dist_px * dist_px) / max(radius_px * radius_px * 0.45, 1.0));
    glass_col += vec3(glow * glow_t * GLOW_INTENSITY * mix(0.5, 1.0, regular));

    // 9. Composite (linear). The material thins into the sharp incoming
    // image; at Time=1 the frame equals TexB bit-exactly.
    vec3 incoming    = texture(TexB, v_UV).rgb;
    vec3 inside_col  = mix(glass_col + ring_light, incoming, t_fade);
    vec3 background  = texture(TexA, v_UV).rgb;
    vec3 bg_shadowed = background * (1.0 - sh_alpha);
    vec3 result      = mix(bg_shadowed, inside_col, mask);

    // 10. Encode + triangular dither below the visibility floor (defeats
    // banding in the blurred body); scales out with the material.
    vec3 encoded = linToSrgb(clamp(result, 0.0, 1.0));
    encoded += triDither(frag_px + vec2(Time * 61.0)) * DITHER_LSB * (1.0 - t_fade);

    FragColor = vec4(encoded, 1.0);
}
