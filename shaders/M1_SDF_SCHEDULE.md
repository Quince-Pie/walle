# Native SDF Float32 operation schedule

The native-half template now explicitly keeps the decoded Float32 FMA schedule instead of leaving implicit multiply/add and mix lowering to a backend. Float template and non-native compilation remain unchanged.

The original clear GB code is `fidelity-fix/gpu-precision/gb_code/clear_original_212_fragment.code.pretty`, backed by its retained code/archive manifest. The original AIR is `work/mac/qc_ll/glass_background_sdf_no_bleed_lph.ll`.

- Extent uses pairedFMA1402 at0x7c/0x8e: fma(t,b,fma(-t,a,a)).
- Circular-cell affine components useFMA1402 at0x69a/0x6a2, followed by the existing maxwithzero. The source constants retain their original Float32 values.
- Squared norms use a separately rounded x*x followed by y*y+x2 FMA (for example0x678/0x684 and0x6ba/0x6c6).
- Circle sqrt-affine usesFMA1402 at0x7ca.
- Axis mixes use pairedFMA at0x7d8/0x7e4 and0x7ec/0x7fa. Registeru7 contains circular.y (R5 store0x368), u8 contains circular.x (R4 store0x370); therefore R3 isdy and R6 isdx.
- The final pair0x808/0x810 evaluates fma(w,dy,fma(-w,dx,dx)). WeightR5 comes from0x6e2/0x6ee:0.5−s, then the saturated mixedFMA(t,s,bias). SignR13L comes from the c.y/c.x comparison0x68c. This agrees with original AIR%59..%66; there is no complemented-weight reorder.

`sdfMixF32` selects the protected two-FMA schedule only for the native-half template. Circular affine and dot operations use the existing FloatControls2-protected Float32 FMA helper. No new numeric constant, table, layout, resource, device feature or image correction is introduced. Float32 division expressions remain original AIR fdiv operations; no reciprocal approximation is invented.

Verification: all27runtime entries compile and pass spirv-val; all5publicfloat controls remain byte-identical. `sdf_complete/RESULTS.json` retains uninstrumented source comparison against the anchored original GB source: all36,864 fully observed quads are exact on bothAMD devices when given native coordinates. CPU retains16 differingpixels; the explicit schedule changes do not alter those outputs. These are not classified as raster differences.

A diagnostic that writes intermediate SDF values changes6other source pixels (`sdf_trace/INVALID_CONTEXT.json`), so its observations are only leads. At the original16pixels it points to Float32 circular-affine/dot/ratio operations, but this does not yet establish an unmodified-shader first divergence. A direct Float32FMA implementation probe is being prepared. No blanket tolerance or platform classification is asserted for the16remainingCPU pixels.
