# Extracted fixed tint-ramp response

**Original ramp and archived response-table proof.** The current accepted runtime still uses the extracted ramp texture, coordinate order and special-coordinate policy, but samples the texture directly. The128KiB response table and binding9 tail below are historical control resources, not current allocations. See [PLATFORM_OPERATIONS.md](PLATFORM_OPERATIONS.md).

The regular/clear material producer supplies one invariant256×1 RGBA16F
grayscale ramp and invariant coordinate parameters. `tint_ramp_response.bin`
stores its **complete65,536-input sampled scalar function**, indexed by the
raw binary16 signed-distance word. It contains no image, frame, tint-color,
geometry or output-residual data.

The original target is M1 Max, macOS26.6.1(25G76), QuartzCore1195.17. The original
metallib SHA-256 is
`eb32770f9a595d777a040dee7454fe30d668ccacaa803f35ddb2f97646193ca7`.
The reference files are:

| File | Bytes | Meaning |
| --- | ---: | --- |
| `native_tint_ramp.rgba16f` |2048| Original ramp texture bytes |
| `native_tint_gradient.bin` |24| Original Float4 coordinate/coverage block and half4 color |
| `tint_ramp_response.bin` |131072| One little-endian half response for every half distance word |

Ramp SHA-256:
`76bb2965dedd18e9d943d67c7387bb597bdd6f144310669ed4270e68e34b1372`.
Response SHA-256:
`7f924c1b55b4593a6c0041226143bc6c132930ca79441617dcca7c1189a3f414`.
Twelve original texture snapshots agree. The gradient block is identical
across229 selected-material draws in nineteen retained traces.

## Mechanism and specialization boundary

The native coordinate parameters are `[0,-1,10,-1/11]`, represented by their
original Float32 bits. The machine program computes the separate uniform
subtraction `y-x`, adds negated half distance in Float32, then separately
multiplies by `w`. Its normalized clamp-to-edge sampler uses linear min/mag
filtering and no mip filtering. Original and calibration kernels establish
opcodes1093,1094 and1786 and the rounding boundaries; the table captures the
texture unit's result after that exact schedule.

The host enables this specialization only for a tint-gradient draw whose
entire ramp texture and coordinate fields x/y/w exactly match the original.
Coverage offset z and gradient color remain live inputs. The shader retains
derivative antialiasing, SDF alpha, gradient color multiplication and later
blending. The general sampler path remains for different ramp bytes or
coordinate parameters. No NaN guard or fitted edge threshold is added.

The response is appended to the existing shared scalar-function buffer at
binding9. Its last32,768 UInt32 words pack two half entries each. The shader
computes the tail offset from the buffer's word count, so the preceding
lossless reciprocal-square-root representation is independent. The response
adds128KiB of immutable shared data, no descriptor and no per-output allocation.
The original ramp texture remains available for the general path.

## Evidence and limits

The complete native input/coordinate/result capture, repeated dispatch and
machine-code comparison are retained in
[`tint_ramp_sampling`](/tmp/walle-work/fidelity-completion/native-scene/tint_ramp_sampling/README.md).
Literal AIR and decoded-machine coordinate programs compile to identical
186-byte kernels and agree for every input word. An independent invocation
of Apple's original compiled gradient agrees for all63,488 finite inputs,
checking1,015,808 output half words. That control changes only the separate
coverage offset to expose the sample with full coverage; it does not substitute
for checking the product's dynamic coverage.

All NaN distance words produce the high-edge sample0x02b6 after the original
coordinate arithmetic canonicalizes NaNs. Positive infinity also produces
0x02b6; negative infinity produces the low-edge sample0x3c00. Nonfinite
coverage is separate and is not asserted to be1.

This establishes the fixed sampled-ramp primitive. It does not establish
general RGBA16F texture-filter equivalence or whole-frame optical identity;
those have separate checks in the fidelity completion record.
