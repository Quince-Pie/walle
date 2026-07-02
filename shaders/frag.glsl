#version 300 es
precision highp float;

in vec2 v_UV;
out vec4 FragColor;

/*
 * Liquid Glass transition.
 *
 * Optical model per Apple's primary sources (HIG "Materials", WWDC25 219
 * "Meet Liquid Glass", Technology Overviews "Adopting Liquid Glass"):
 *
 *  - LENSING is the defining trait: the material "dynamically bends, shapes,
 *    and concentrates light in real time" — refraction concentrated at the
 *    edge, not uniform scattering. Objects "materialize in and out by
 *    gradually modulating the light bending and lensing" instead of fading.
 *  - A layered composite: a highlights layer whose specular "responds to
 *    geometry" with lights that "move in space, causing light to travel
 *    around the material"; content-aware shadows ("increases the opacity of
 *    its shadow when it is over text... lowers it over a solid light
 *    background"); an interaction illumination layer ("illuminates from
 *    within... starting right under your fingertips").
 *  - The regular variant "blurs and adjusts the luminosity of background
 *    content"; the material "has no inherent color, and instead takes on
 *    colors from the content directly behind it".
 *  - Size scales the material: large elements get "deeper, richer shadows,
 *    more pronounced lensing and refraction effects, and a softer scattering
 *    of light".
 *
 * Textures are sRGB (GL_SRGB8_ALPHA8), so sampling yields linear light; all
 * blending below happens in linear space and the final value is re-encoded.
 */

// --- Textures ---
uniform sampler2D TexA;      // outgoing wallpaper, sharp
uniform sampler2D TexGlassA; // outgoing wallpaper, pre-blurred (content probe for shadow adaptivity)
uniform sampler2D TexB;      // incoming wallpaper, sharp (refracted through the lens edge)
uniform sampler2D TexGlassB; // incoming wallpaper, pre-blurred + vibrancy (glass body)

// --- Uniforms ---
uniform float Time;              // LINEAR normalized time [0,1]; all shaping is done here
uniform vec2  Resolution;        // buffer px
uniform vec2  CenterPointPixels; // transition origin ("fingertip"), GL coords
uniform float MaxRadiusPixels;   // distance from origin to farthest corner

// --- Timeline ---
const float T_EXPAND_END   = 0.62; // circle covers the screen here
const float T_FADE_START   = 0.66; // glass starts thinning into the sharp image
const float T_MATERIALIZE  = 0.12; // lens strength ramp-in ("materialize by modulating lensing")

// --- Material constants ---
const float LENS_WIDTH_FRAC   = 0.16;  // edge band as a fraction of current radius
const float LENS_WIDTH_MIN    = 28.0;  // px
const float LENS_WIDTH_MAX    = 110.0; // px
const float LENS_BEND         = 0.85;  // peak refraction displacement as fraction of band width
const float LENS_DISPERSION   = 0.035; // per-channel displacement spread (subtle chromatic split)
const float EDGE_CLARITY      = 0.55;  // how much the lens band shows concentrated (sharp) light
const float ADAPT_STRENGTH    = 0.42;  // luminosity/dynamic-range adaptation of the glass body
const float ADAPT_LIFT        = 0.115; // linear-light lift toward legibility (colorless)
const float SPEC_POWER        = 9.0;   // specular lobe tightness on the rim
const float SPEC_INTENSITY    = 0.85;  // primary highlight energy
const float SPEC_COUNTER      = 0.30;  // opposite-rim counter highlight (glass catches light twice)
const float RIM_WIDTH         = 1.6;   // px, the razor edge
const float BEVEL_WIDTH_FRAC  = 0.45;  // inner volume shading band, fraction of lens band
const float BEVEL_STRENGTH    = 0.16;
const float GLOW_INTENSITY    = 0.22;  // interaction illumination at the origin
const float SHADOW_OFFSET_PX  = 14.0;  // screen-down offset of the cast shadow
const float SHADOW_BASE       = 0.34;  // shadow opacity before content adaptation
const float SHADOW_WIDTH_FRAC = 0.11;  // penumbra as a fraction of radius ("deeper, richer" when large)
const float SHADOW_WIDTH_MIN  = 26.0;  // px
const float DITHER_LSB        = 1.0 / 255.0;

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
    // 1. Timeline phases.
    float t_expand    = easeGel(smoothstep(0.0, T_EXPAND_END, Time));
    float t_fade      = smoothstep(T_FADE_START, 1.0, Time);
    // Material thickness: lensing/highlights materialize in, then modulate
    // out — the Apple-documented alternative to opacity fades.
    float thickness   = smoothstep(0.0, T_MATERIALIZE, Time) * (1.0 - t_fade);

    // 2. Geometry (SDF of the expanding circle).
    vec2  frag_px   = gl_FragCoord.xy;
    vec2  delta     = frag_px - CenterPointPixels;
    float dist_px   = length(delta);
    float radius_px = t_expand * MaxRadiusPixels;
    float sdf       = dist_px - radius_px;
    vec2  n_out     = dist_px > 0.5 ? delta / dist_px : vec2(0.0, 1.0);

    float lens_w   = clamp(radius_px * LENS_WIDTH_FRAC, LENS_WIDTH_MIN, LENS_WIDTH_MAX);
    float shadow_w = max(radius_px * SHADOW_WIDTH_FRAC, SHADOW_WIDTH_MIN);

    float mask = 1.0 - smoothstep(-0.75, 0.75, sdf);

    // 3. Early out for pixels beyond glass, rim, and full shadow penumbra.
    if (sdf > SHADOW_OFFSET_PX + shadow_w + 8.0) {
        FragColor = vec4(linToSrgb(texture(TexA, v_UV).rgb), 1.0);
        return;
    }

    // 4. Content-aware drop shadow (grounding layer, outside the glass).
    // Adaptivity probe: the pre-blurred outgoing image approximates "what is
    // behind the shadow"; light solid backgrounds get a fainter shadow.
    float shadow_dist  = distance(frag_px - vec2(0.0, -SHADOW_OFFSET_PX), CenterPointPixels);
    float shadow_mask  = 1.0 - smoothstep(0.0, shadow_w, shadow_dist - radius_px);
    float bg_luma      = dot(texture(TexGlassA, v_UV).rgb, LUMA);
    float shadow_adapt = mix(1.20, 0.55, smoothstep(0.18, 0.62, bg_luma));
    float shadow_alpha = shadow_mask * SHADOW_BASE * shadow_adapt * (1.0 - mask) * thickness;

    // 5. Lensing: refraction displacement concentrated at the edge band.
    // Profile peaks at the rim and decays toward the body, so the interior
    // stays optically calm while the edge visibly bends the incoming image.
    float band   = clamp(-sdf / lens_w, 0.0, 1.0); // 0 at rim -> 1 deep inside
    float p_lens = (1.0 - band) * step(sdf, 0.0);  // 1 at rim -> 0 inward, only inside
    float bend   = p_lens * p_lens * lens_w * LENS_BEND * thickness;

    // Per-channel displacement ("bends, shapes, and concentrates light"):
    // subtle spectral split at the strongest bend.
    vec2 uv      = v_UV;
    vec2 px2uv   = 1.0 / Resolution;
    // v_UV.y is flipped relative to gl_FragCoord.y, so a screen-space
    // direction needs its y negated before use as a UV offset.
    vec2 duv     = vec2(n_out.x, -n_out.y) * bend * px2uv;
    vec3 refracted;
    refracted.r = texture(TexB, uv + duv * (1.0 + LENS_DISPERSION)).r;
    refracted.g = texture(TexB, uv + duv).g;
    refracted.b = texture(TexB, uv + duv * (1.0 - LENS_DISPERSION)).b;

    // 6. Glass body: blurred incoming image, thinning to sharp as the
    // material dissolves. The edge band concentrates light: refracted sharp
    // content shines through where the bend is strongest.
    vec3  body      = mix(texture(TexGlassB, uv).rgb, texture(TexB, uv).rgb, t_fade);
    float clarity   = max(t_fade, p_lens * EDGE_CLARITY * thickness);
    vec3  glass_col = mix(body, refracted, clarity * (1.0 - t_fade));

    // 7. Luminosity adaptation (regular variant): colorless dynamic-range
    // compression + lift so the material stays legible over any content
    // while taking its color only from that content.
    float adapt = ADAPT_STRENGTH * thickness;
    glass_col = mix(glass_col, glass_col * (1.0 - ADAPT_LIFT) + vec3(ADAPT_LIFT), adapt);

    // 8. Highlights layer. The light direction travels during the transition
    // ("these lights move in space, causing light to travel around the
    // material"), sweeping from upper-left toward overhead.
    float ang   = radians(125.0) - radians(30.0) * Time;
    vec2  L     = vec2(cos(ang), sin(ang));
    float n_dot = dot(n_out, L);

    // Razor rim on the cut edge, specular on the lit arc + faint counter-arc.
    float rim_band = (1.0 - smoothstep(-0.75, 0.75, sdf))
                   - (1.0 - smoothstep(-0.75, 0.75, sdf + RIM_WIDTH));
    float spec = pow(max(n_dot, 0.0), SPEC_POWER) * SPEC_INTENSITY
               + pow(max(-n_dot, 0.0), SPEC_POWER * 2.0) * SPEC_COUNTER;
    vec3 rim_col = vec3(rim_band * spec * thickness);

    // Inner bevel: volume shading that follows the circle's geometry (lit
    // side lifts, shadow side dips) — the "softer scattering" of thick glass.
    float bevel_band = p_lens * smoothstep(0.0, BEVEL_WIDTH_FRAC, 1.0 - band);
    glass_col *= 1.0 + n_dot * bevel_band * BEVEL_STRENGTH * thickness;

    // 9. Interaction illumination: the material "illuminates from within...
    // starting right under your fingertips" — the transition origin — and
    // recedes as the expansion completes.
    float glow_t = thickness * (1.0 - smoothstep(0.18, 0.55, Time));
    float glow   = exp(-(dist_px * dist_px) / max(radius_px * radius_px * 0.45, 1.0));
    glass_col += vec3(glow * glow_t * GLOW_INTENSITY);

    // 10. Composite (linear light).
    vec3 background  = texture(TexA, v_UV).rgb;
    vec3 bg_shadowed = background * (1.0 - shadow_alpha);
    vec3 result      = mix(bg_shadowed, glass_col + rim_col, mask);

    // 11. Encode + triangular dither below the visibility floor to defeat
    // banding in the blurred body. Both scale with the material so the final
    // frame equals the incoming image bit-exactly.
    vec3 encoded = linToSrgb(clamp(result, 0.0, 1.0));
    encoded += triDither(frag_px + vec2(Time * 61.0)) * DITHER_LSB * (1.0 - t_fade);

    FragColor = vec4(encoded, 1.0);
}
