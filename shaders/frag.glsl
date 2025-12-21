#version 300 es
precision highp float;

in vec2 v_UV;
out vec4 FragColor;

// --- Textures (Pre-processed by C23 VIPS pipeline) ---
uniform sampler2D TexA;        // Standard (Sharp)
uniform sampler2D TexGlassA;   // Blurred & Saturated
uniform sampler2D TexB;
uniform sampler2D TexGlassB;

// --- Uniforms ---
uniform float Time;             // Eased [0.0 - 1.0]
uniform vec2  Resolution;       // Viewport px
uniform vec2  CenterPointPixels;// Center px
uniform float MaxRadiusPixels;  // Expansion radius px

// --- Visual Constants (Reference Match) ---
const float RIM_THICKNESS     = 1.2;   // Pixels (Razor sharp)
const float RIM_INTENSITY     = 0.65;  // Crisp white edge
const float BEVEL_OPACITY     = 0.30;  // Volume hint
const float SHADOW_OPACITY    = 0.25;  // Grounding
const float SHADOW_OFFSET_Y   = 13.0;  // Pixels down
const float NOISE_INTENSITY   = 0.025; // "Frosted" grain

// --- Timeline ---
const float T_EXPAND_END = 0.5;
const float T_FADE_START = 0.5;

// --- Utilities ---

// Triangular Dither (Luminance preserving high-frequency noise)
float dither(vec2 uv) {
    return fract(sin(dot(uv, vec2(12.9898, 78.233))) * 43758.5453) - 0.5;
}

void main() {
    // 1. Time Phases
    float t_expand = smoothstep(0.0, T_EXPAND_END, Time);
    float t_fade   = smoothstep(T_FADE_START, 1.0, Time);

    // 2. Geometry (SDF Calculation)
    vec2  frag_px   = gl_FragCoord.xy;
    float dist_px   = distance(frag_px, CenterPointPixels);
    float radius_px = t_expand * MaxRadiusPixels;
    
    // Signed Distance Field (Negative = Inside)
    float sdf = dist_px - radius_px;

    // Hard Mask (1.0 inside, 0.0 outside) with 1px Anti-Aliasing
    float mask = 1.0 - smoothstep(-0.5, 0.5, sdf);
    
    // Optimization: Skip heavy processing for pure background pixels
    if (mask <= 0.001 && sdf > SHADOW_OFFSET_Y + 20.0) {
        vec3 bg = mix(texture(TexA, v_UV).rgb, texture(TexB, v_UV).rgb, t_fade);
        FragColor = vec4(bg, 1.0);
        return;
    }

    // 3. Render Drop Shadow (Grounding Layer)
    // Calculated *behind* the glass to detach it from background
    float shadow_dist = distance(frag_px - vec2(0.0, -SHADOW_OFFSET_Y), CenterPointPixels);
    float shadow_sdf = shadow_dist - radius_px;
    float shadow_mask = 1.0 - smoothstep(0.0, 43.0, shadow_sdf); // Soft blur 43px
    // Fade in shadow as circle expands to avoid shadow appearing before circle
    float shadow_fade_in = smoothstep(0.0, 0.15, t_expand);
    float shadow_alpha = shadow_mask * SHADOW_OPACITY * (1.0 - mask) * (1.0 - t_fade) * shadow_fade_in;

    // 4. Glass Material (Inside the Sphere)

    // Sample Blurred New Image (Direct reveal - no crossfade)
    vec3 glass_color = texture(TexGlassB, v_UV).rgb;

    // Apply Dithering (Grain)
    float noise = dither(frag_px + vec2(Time * 10.0));
    float noise_val = noise * NOISE_INTENSITY;
    glass_color += noise_val;

    // 5. Lighting Effects (Screen Space)
    // Normalized Y (0.0 bottom -> 1.0 top)
    float norm_y = frag_px.y / Resolution.y;

    // A. Inner Bevel (Simulates Volume)
    // Highlight Top, Shadow Bottom
    float highlight = pow(smoothstep(0.6, 1.0, norm_y), 3.0) * BEVEL_OPACITY;
    float shade     = pow(smoothstep(0.4, 0.0, norm_y), 3.0) * BEVEL_OPACITY;
    
    glass_color += vec3(highlight);
    glass_color *= (1.0 - shade);

    // B. Micro-Rim (The Cut Edge)
    // Logic: 1.2px stroke inside the shape
    float rim_edge_outer = 1.0 - smoothstep(-0.5, 0.5, sdf);
    float rim_edge_inner = 1.0 - smoothstep(-0.5, 0.5, sdf + RIM_THICKNESS);
    float rim_mask_val   = rim_edge_outer - rim_edge_inner;

    // Rim Bias: Stronger at Top-Left (Overhead Light)
    float rim_bias = smoothstep(0.0, 1.0, norm_y) * 0.5 + 0.5;
    vec3  rim_col  = vec3(rim_mask_val * RIM_INTENSITY * rim_bias);

    // 6. Final Compositing

    // A. Background Layer (old image, only visible outside the glass circle)
    vec3 background = texture(TexA, v_UV).rgb;

    // Apply Shadow to Background
    vec3 bg_shadowed = mix(background, vec3(0.0), shadow_alpha);

    // B. Add Rim to Glass
    vec3 final_glass = glass_color + rim_col;

    // C. Composite: blend old background with glass based on mask
    vec3 with_glass = mix(bg_shadowed, final_glass, mask);

    // D. Fade glass effect to sharp new image (TexB)
    // The rim fades out, glass blur transitions to sharp
    vec3 result = mix(with_glass, texture(TexB, v_UV).rgb, t_fade);

    FragColor = vec4(clamp(result, 0.0, 1.0), 1.0);
}
