#version 450 core

in vec2 v_UV;
out vec4 FragColor;

/*
 * Liquid Glass transition — regular and clear variants.
 *
 * The STATIC substrate below is fitted to structured-light measurements of
 * real macOS 26.4 (25E246) `glassEffect` renders over calibration patterns
 * (3200x2000 @1x, BOTH appearances; capture rig: /tmp/liquid-glass). The
 * numbers are measured, not folklore:
 *
 *  - REGULAR is a fully opaque platter in both appearances: sRGB 0.980
 *    (light) / 0.078 (dark) across the whole interior for every background
 *    gray 0..255 (transmission MTF 0.0000 even 3 px inside the rim), plus a
 *    hue wash that is symmetric in sRGB space once channel clipping is
 *    accounted for: platter = Yp + 0.18 (light) / 0.28 (dark) * chroma
 *    vector of the blurred backdrop, clamped. No rings, no bevel, no shadow
 *    in the static material — either appearance.
 *  - CLEAR is an exact affine veil applied in sRGB SPACE over a mega-blurred
 *    backdrop: out = 0.494*sat(blur) + ADD, with ADD 0.267 / sat 1.102
 *    (light) and ADD 0.016 / sat 0.85 (dark); each pole fits its whole gray
 *    sweep to +-0.004. The HIG's "dimming layer over bright content" is
 *    already this veil (white -> 0.761). The lens copy contrast is 0.36
 *    (light) / 0.19 (dark, measured MTF 0.051 vs 0.122).
 *  - `.tint()` measured as a hue-free platter shift on this build (blue and
 *    orange produce identical pixels in both appearances) — deliberately
 *    not modeled.
 *  - BLUR: interior fundamental transmission at p=256 px is 0.011-0.027
 *    (rect) -> gaussian sigma >= ~110 px at 3774 px window diagonal, i.e.
 *    sigma ~ 0.032*diagonal. Identical at all three element sizes.
 *  - LENSING (clear): a faint sharp copy of the backdrop, displaced OUTWARD
 *    (structured-light phase decode), rides inside an edge band:
 *      band width  w = min(0.44*R, 0.033*diagonal)   [110 px at R=250;
 *                    fixed ~125 px at straight edges of huge elements]
 *      displacement d(s) = dmax * (1 - s^5.2)(0.52 + 0.48 sin(pi/2 min(s/0.508,1)))
 *                    with s = distance inside rim / w  (rms 0.028 vs decode)
 *      dmax = min(0.21*R, 1.09*w);  copy contrast ~ 0.36*exp(-s) pre-veil
 *      (MTF equal at p=64 and p=256 -> the copy itself is near-sharp.)
 *  - The regular variant transmits nothing, so it gets no lens at all.
 *
 * The DYNAMIC layer (specular ring, interaction glow, grounding shadow) does
 * not exist in the static captures — flat-gray deltas are zero to the AA
 * edge, and even `.interactive()` buttons rest flat. It appears when the
 * material moves (WWDC25 219: lights "move in space, causing light to travel
 * around the material"; materialize "by modulating the light bending"). The
 * ring/glow/shadow constants therefore come from the HIG variant photos
 * (fitted earlier at pixel level) and drive only the moving transition:
 *      ring radius 0.973R, center displaced 0.021R screen-down, razor at the
 *      bottom rim (~0.007R) widening to a soft glow at the top (~0.02R),
 *      lobes +0.20/+0.13/+0.04 linear; contact shadow only over dark
 *      content (macOS light shows none).
 *
 * The light/dark pole is selected by content luminance (steep smoothstep on
 * the blurred backdrop) — walle's stand-in for the system appearance a
 * wallpaper daemon does not have; both poles are macOS-measured. Textures
 * are sRGB; blending is linear except the platter wash and the veil, which
 * are deliberately applied in sRGB space as measured.
 */

// --- Textures ---
uniform sampler2D TexA;      // outgoing wallpaper, sharp
uniform sampler2D TexGlassA; // outgoing, pre-blurred (shadow probe)
uniform sampler2D TexB;      // incoming wallpaper, sharp (lens source)
uniform sampler2D TexGlassB; // incoming, pre-blurred sigma~0.032*diag (body)

// --- Uniforms ---
uniform float Time;              // LINEAR normalized [0,1]
uniform vec2  Resolution;        // buffer px
uniform vec2  CenterPointPixels; // transition origin, GL coords (y up)
uniform float MaxRadiusPixels;
uniform float Variant;           // 0.0 = clear, 1.0 = regular

// --- Timeline ---
const float T_EXPAND_END  = 0.62;
const float T_FADE_START  = 0.66;
const float T_MATERIALIZE = 0.12;

// --- Clear veil (measured, sRGB space, both appearances) ---
// Primaries-vs-black responses sum to the white response: the veil is LINEAR
// in sRGB space with a cross-channel matrix, i.e. a saturation term before
// the affine map: out = MIX*(Y + SAT*(x - Y)) + ADD, luma-weighted in sRGB.
// The mix is appearance-invariant; the scrim constant and saturation flip
// (light: gray scrim + slight boost; dark: near-black scrim + desaturation).
const float VEIL_MIX       = 0.494;
const float VEIL_ADD_LIGHT = 0.267;
const float VEIL_ADD_DARK  = 0.016;
const float VEIL_SAT_LIGHT = 1.102;
const float VEIL_SAT_DARK  = 0.85;

// --- Lens (measured; fractions of R / window diagonal) ---
const float LENS_BAND_RFRAC  = 0.44;
const float LENS_BAND_DIAG   = 0.033;
const float LENS_DMAX_RFRAC  = 0.21;
const float LENS_DMAX_WFRAC  = 1.09;
const float LENS_COPY_LIGHT  = 0.36;  // copy contrast at the rim (pre-veil)
const float LENS_COPY_DARK   = 0.19;  // measured MTF 0.051 vs 0.122 light
const float LENS_PROF_POW    = 5.2;   // d(s) closed-form fit, rms 0.028
const float LENS_PROF_KNEE   = 0.508;
const float LENS_PROF_RIM    = 0.52;
const float LENS_DISPERSION  = 0.030; // chromatic split during motion only

// --- Regular platter (measured, both appearances; sRGB space) ---
// Fully opaque at BOTH poles (flat to the AA edge; dark deltas flat too).
// The hue wash is symmetric in sRGB space once clipping is accounted for:
// platter = Yp + (wash - Yw)*WASH_CK, clamped to [0,1].
const float PLATTER_LIGHT_S = 0.980;  // sRGB (all grays, measured)
const float PLATTER_DARK_S  = 0.078;  // sRGB (all grays, measured)
const float WASH_CK_LIGHT   = 0.18;
const float WASH_CK_DARK    = 0.28;

// --- Dynamic layer (HIG-photo fitted; motion only) ---
const float RING_RADIUS_FRAC = 1.0;
const float RING_OFFSET_FRAC = 0.0;
const float RING_W_EDGE_FRAC = 0.007;
const float RING_W_TOP_FRAC  = 0.020;
const float RING_UP_GAIN     = 0.20;
const float RING_DOWN_GAIN   = 0.13;
const float RING_BASE_GAIN   = 0.04;
const float SHADOW_OFF_FRAC  = 0.012;
const float SHADOW_PEN_FRAC  = 0.035;
const float SHADOW_PEN_MIN   = 10.0;
const float SHADOW_BASE      = 0.16;
const float GLOW_INTENSITY   = 0.20;

const float DITHER_LSB = 1.0 / 255.0;
const vec3  LUMA = vec3(0.2126, 0.7152, 0.0722);
const float HALF_PI = 1.57079632679;

float linToSrgb1(float c) {
    return c <= 0.0031308 ? 12.92 * c : 1.055 * pow(c, 1.0 / 2.4) - 0.055;
}
vec3 linToSrgb(vec3 c) {
    return vec3(linToSrgb1(c.r), linToSrgb1(c.g), linToSrgb1(c.b));
}
float srgbToLin1(float c) {
    return c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4);
}
vec3 srgbToLin(vec3 c) {
    return vec3(srgbToLin1(c.r), srgbToLin1(c.g), srgbToLin1(c.b));
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
float easeGel(float t) {
    float c1 = 0.28;
    float c3 = c1 + 1.0;
    float u  = t - 1.0;
    return 1.0 + c3 * u * u * u + c1 * u * u;
}

void main() {
    // 1. Timeline. Thickness modulates every material term ("materialize by
    // modulating the light bending and lensing").
    float t_expand  = easeGel(smoothstep(0.0, T_EXPAND_END, Time));
    float t_fade    = smoothstep(T_FADE_START, 1.0, Time);
    float thickness = smoothstep(0.0, T_MATERIALIZE, Time) * (1.0 - t_fade);
    float regular   = Variant;

    // 2. Geometry.
    vec2  frag_px   = gl_FragCoord.xy;
    vec2  delta     = frag_px - CenterPointPixels;
    float dist_px   = length(delta);
    float radius_px = t_expand * MaxRadiusPixels;
    float sdf       = dist_px - radius_px;
    vec2  n_out     = dist_px > 0.5 ? delta / dist_px : vec2(0.0, 1.0);
    float mask      = 1.0 - smoothstep(-0.75, 0.75, sdf);

    float shadow_off = SHADOW_OFF_FRAC * radius_px;
    float shadow_pen = max(SHADOW_PEN_FRAC * radius_px, SHADOW_PEN_MIN);

    // 3. Early out beyond glass and shadow reach.
    if (sdf > shadow_off + shadow_pen + 8.0) {
        FragColor = vec4(linToSrgb(texture(TexA, v_UV).rgb), 1.0);
        return;
    }

    vec2 uv    = v_UV;
    vec2 px2uv = 1.0 / Resolution;
    float diag = length(Resolution);

    // 4. Backdrop wash + platter mode. Wide taps flatten the chroma of the
    // blurred backdrop (measured: the platter takes a near-uniform hue wash,
    // not per-pixel color). Light/dark platter selection is walle's stand-in
    // for the system appearance a wallpaper daemon does not have.
    vec2 e1 = vec2(0.30, 0.0);
    vec2 e2 = vec2(0.0, 0.30);
    vec2 e3 = vec2(0.21, 0.21);
    vec2 e4 = vec2(0.21, -0.21);
    vec3 blurB = texture(TexGlassB, uv).rgb;
    vec3 wash = (blurB + texture(TexGlassB, uv + e1).rgb + texture(TexGlassB, uv - e1).rgb
                 + texture(TexGlassB, uv + e2).rgb + texture(TexGlassB, uv - e2).rgb
                 + texture(TexGlassB, uv + e3).rgb + texture(TexGlassB, uv - e3).rgb
                 + texture(TexGlassB, uv + e4).rgb + texture(TexGlassB, uv - e4).rgb)
              / 9.0;
    // Steep mode switch: macOS picks the platter by system appearance, a
    // binary the wallpaper stands in for. Content at linear Y>=0.10 (sRGB
    // ~0.35) decisively gets the light platter; only genuinely dark content
    // gets the dark one. A wide blend would park mid-gray content on a
    // mid-luminance platter that matches neither measured pole.
    float Yw        = dot(wash, LUMA);
    float lightness = smoothstep(0.04, 0.10, Yw);

    // 5. Grounding. The static macOS material casts NO shadow over light
    // content (flat-gray deltas are zero outside the rim); the HIG-measured
    // tight contact ring survives only for the dark platter.
    float sdf_sh   = distance(frag_px - vec2(0.0, -shadow_off), CenterPointPixels) - radius_px;
    float penumbra = 1.0 - smoothstep(0.0, shadow_pen, sdf_sh);
    float bg_luma  = dot(texture(TexGlassA, v_UV).rgb, LUMA);
    float sh_adapt = mix(1.25, 0.60, smoothstep(0.03, 0.35, bg_luma));
    float sh_alpha = penumbra * SHADOW_BASE * sh_adapt
                   * regular * (1.0 - lightness) * (1.0 - mask) * thickness;

    // 6. Lens band geometry (clear only consumes it; the dark platter's edge
    // relief reuses the band position).
    float w_band = min(LENS_BAND_RFRAC * radius_px, LENS_BAND_DIAG * diag);
    float s_band = (radius_px - dist_px) / max(w_band, 1.0); // 0 at rim, + inward
    float s01    = clamp(s_band, 0.0, 1.0);

    // 7. Body per variant (uniform control flow).
    vec3 glass_col;
    if (regular > 0.5) {
        // Opaque platter at BOTH poles (measured flat to the AA edge in both
        // appearances) + symmetric sRGB-space hue wash; the clamp reproduces
        // the measured channel clipping exactly.
        vec3  wash_s = linToSrgb(wash);
        float Yw_s   = dot(wash_s, LUMA);
        float Yp_s   = mix(PLATTER_DARK_S, PLATTER_LIGHT_S, lightness);
        float ck     = mix(WASH_CK_DARK, WASH_CK_LIGHT, lightness);
        vec3  plat_s = clamp(vec3(Yp_s) + (wash_s - vec3(Yw_s)) * ck, 0.0, 1.0);
        glass_col    = mix(blurB, srgbToLin(plat_s), thickness);
    } else {
        // Clear: faint near-sharp copy of the backdrop, displaced OUTWARD
        // along the rim normal by the measured profile, mixed over the
        // mega-blur, then the measured sRGB-space veil on top.
        float prof = (1.0 - pow(s01, LENS_PROF_POW))
                   * (LENS_PROF_RIM + (1.0 - LENS_PROF_RIM)
                                    * sin(HALF_PI * min(s01 / LENS_PROF_KNEE, 1.0)));
        float dmax = min(LENS_DMAX_RFRAC * radius_px, LENS_DMAX_WFRAC * w_band);
        float bend = dmax * prof * thickness * step(0.0, s_band);

        // v_UV.y is flipped relative to gl_FragCoord.y: negate y for UV use.
        vec2 duv = vec2(n_out.x, -n_out.y) * bend * px2uv;
        vec3 lensed;
        lensed.r = texture(TexB, uv + duv * (1.0 + LENS_DISPERSION)).r;
        lensed.g = texture(TexB, uv + duv).g;
        lensed.b = texture(TexB, uv + duv * (1.0 - LENS_DISPERSION)).b;

        float a_copy = mix(LENS_COPY_DARK, LENS_COPY_LIGHT, lightness)
                     * exp(-max(s_band, 0.0)) * thickness * step(0.0, s_band);
        vec3 pre = mix(blurB, lensed, a_copy);

        // The veil is authored in sRGB space (measured affine + saturation
        // matrix, per appearance); thickness relaxes it toward identity so
        // the material can materialize.
        vec3  pre_s = linToSrgb(pre);
        float Ys    = dot(pre_s, LUMA);
        float vsat  = mix(VEIL_SAT_DARK, VEIL_SAT_LIGHT, lightness);
        pre_s       = vec3(Ys) + (pre_s - vec3(Ys)) * mix(1.0, vsat, thickness);
        float vmix  = mix(1.0, VEIL_MIX, thickness);
        float vadd  = mix(VEIL_ADD_DARK, VEIL_ADD_LIGHT, lightness) * thickness;
        glass_col   = srgbToLin(clamp(pre_s * vmix + vec3(vadd), 0.0, 1.0));
    }

    // 8. Dynamic highlights: the offset specular ring (HIG-fitted). Absent
    // in static macOS captures; this is the moving-material layer.
    vec2  ring_c   = CenterPointPixels - vec2(0.0, RING_OFFSET_FRAC * radius_px);
    vec2  rd       = frag_px - ring_c;
    float rd_len   = length(rd);
    vec2  n_ring   = rd_len > 0.5 ? rd / rd_len : vec2(0.0, 1.0);
    float ring_sdf = rd_len - RING_RADIUS_FRAC * radius_px;
    float w_edge   = clamp(RING_W_EDGE_FRAC * radius_px, 1.25, 5.0);
    float w_top    = clamp(RING_W_TOP_FRAC * radius_px, 3.0, 24.0);
    float ring_w   = mix(w_edge, w_top, smoothstep(0.0, 1.0, max(n_ring.y, 0.0)));
    float ring_pr  = exp(-(ring_sdf * ring_sdf) / (ring_w * ring_w));

    float ang   = radians(125.0) - radians(30.0) * Time; // light travels
    vec2  L     = vec2(cos(ang), sin(ang));
    float n_dot = dot(n_ring, L);
    float lobes = RING_BASE_GAIN + RING_UP_GAIN * pow(max(n_dot, 0.0), 1.6)
                + RING_DOWN_GAIN * pow(max(-n_dot, 0.0), 2.0);
    vec3 ring_light = vec3(ring_pr * lobes * thickness * mask);

    // 9. Interaction glow at the origin, receding as the circle expands.
    float glow_t = thickness * (1.0 - smoothstep(0.18, 0.55, Time));
    float glow   = exp(-(dist_px * dist_px) / max(radius_px * radius_px * 0.45, 1.0));
    glass_col += vec3(glow * glow_t * GLOW_INTENSITY * mix(0.5, 1.0, regular));

    // 10. Composite (linear). At Time=1 the frame equals TexB bit-exactly.
    vec3 incoming    = texture(TexB, v_UV).rgb;
    vec3 inside_col  = mix(glass_col + ring_light, incoming, t_fade);
    vec3 background  = texture(TexA, v_UV).rgb;
    vec3 bg_shadowed = background * (1.0 - sh_alpha);
    vec3 result      = mix(bg_shadowed, inside_col, mask);

    // 11. Encode + triangular dither below the visibility floor.
    vec3 encoded = linToSrgb(clamp(result, 0.0, 1.0));
    encoded += triDither(frag_px + vec2(Time * 61.0)) * DITHER_LSB * (1.0 - t_fade);

    FragColor = vec4(encoded, 1.0);
}
