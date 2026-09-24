# Why the idle face draw is omitted

The selected wallpaper graph has no pointer/press interaction. Apple's idle
foreground is achromatic black in light appearance or slightly extended white
in dark appearance, with alpha exactly zero. This is independent of regular/
clear style, activity, material dimensions and the separate byte tint.

Original AppKit `GlassInteractionFill.resolve`185095c14 initializes opacity0
at185095d04; the idle branch reaches185095d8c unchanged. Press/rollover select
different inputs and are outside this wallpaper graph. The DesignLibrary
adaptiveColor tag0x25 matrix path24092dc34..dcc4 preserves the YCC alpha row
(0,0,0,1,0), multiplies its alpha coefficient by foreground alpha at24092dcdc,
and stores it at24092e338. Its complete resulting alpha row is(0,0,0,0,0).
The material's face-opacity property remains1; the RGB matrix is not identity.
Native clamp is0. The extracted packet retains all of those values unchanged.

The VCM operator computes `a=saturate(lin.a)`, then
`c=(lin.rgb*a,a)*coverage`, then `(1-c.a)*destination+c`. For an opaque UNORM8
destination, its inputs are finite and bounded by1. More generally, the source
alpha guard0x068e bounds unpremultiplication of any UNORM8 destination by10000.
The two idle half matrices have maximum absolute RGB row sums below2.764, so
their evaluation remains below27640, far from half overflow even with rounding.
The zero alpha row gives a=0 and c=signed-zero for
every finite coverage. The resulting destination and stored bytes are unchanged.

The native coverage is finite over the admitted wallpaper geometry:

- A projected corner radius below1 takes the original solid `fill_rect` path
  at18a8a28c8..28e4. Its AA endpoint fractions/products give finite coverage
  in[0,1]; there is no division by the disappearing circle radius.
- Otherwise the circle has finite affine geometry, with homogeneous w=1.
  `emit_nine_part_rect`'s AA normalization at18a89fb84 adds one target pixel
  and adjusts UV by the same finite factor. Normalization uses the outer-to-
  corner-center distance, never a tiny middle-cell width; a collapsed distance
  skips adjustment. Radius at least1 bounds normalized
  coordinates at the expanded edge. The Float dot/sqrt remains finite, the
  derivative denominator is floored at.0001, and saturation yields[0,1].
- Zero progress/scale uses the separate exact endpoint path. The public motion
  constructor keeps finite centers/radii within bounds derived from the admitted
  output and normalized motion inputs; it does not accept arbitrary projective
  face geometry.

No `0*NaN==0` assumption is used. This is a covering argument for this idle
opaque-SDR graph, not permission to omit interactive/custom foreground effects.
The native face's sixteen-vertex grid is therefore not replaced by another
tessellation. The controller elides the unobservable pass entirely. Native
material packets and the generic face shader API remain available.

Primary retained listings and source mappings are under
`/tmp/extract-liquidglass/work/finish/material/evidence/` and
`/tmp/extract-liquidglass/liquidglass/host/lg_material/`.
[Alpha-row derivation](/tmp/walle-work/fidelity-fix/face-identity-evidence/SOURCE_ALPHA_PROOF.md)
records the exact dispatch, matrix bounds and original source locations.
[Native face controls](/tmp/walle-work/fidelity-fix/native-scene/face_identity_0130/README.md)
independently check53 traces/110 draws, including the plain-fill fallback:
all five alpha components and clamp are
zero, and vertices are finite. Those finite controls support the source argument;
the omission is not selected by tuning a visual comparison.
