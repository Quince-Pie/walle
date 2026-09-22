#language slang 2026

/// Apple Liquid Glass, ported from the Metal shaders in QuartzCore 1195.17 (macOS 26.6.1).
/// Every function mirrors a QuartzCore AIR function; see README.md for the evidence trail.
module liquidglass;

__include "lg_precision.slang";
__include "lg_sdf.slang";
__include "lg_glass_background.slang";
__include "lg_glass_foreground.slang";
__include "lg_sdf_effects.slang";
__include "lg_blur.slang";
