# Liquid Glass evidence audit through run 30529096266

This report separates accepted measurements from hypotheses. It does not
authorize a shader change by visual intuition. Production changes remain
blocked on an automated Apple-reference comparison that can prove no protected
quality metric regresses.

## Bit-exact live filter parameters

The dedicated macOS runtime workflow at `lg-test` commit
`266278b11f41f3a1289eae1daa6890424801280d` completed as GitHub run
`30487878788` in 39 seconds. Its 26,209-byte artifact is
`artifacts/liquid-glass-introspection-30487878788.zip` (SHA-256
`b6c79909ecdc94f00fd69d6c1ae8059b88831e753e33717606033765f1926414`).
All members pass ZIP CRC. The 243,365-byte `runtime.json` has SHA-256
`7496cd16d2fa8ca1558d31109cac4a634fec1639c906153c81cf3c36fb0fcd75`.
It was produced by a real `.glassEffect(.clear, in: .circle)` instance on
macOS 26.4 build 25E246 with Xcode 26.5.

The normalized machine-readable report is
`artifacts/liquid-glass-runtime-evidence-30487878788.json` (SHA-256
`994585cfba5dbd43b30e78e2e39096c61cdfb745ba476d4fe902857e20ba8b65`).
Its analyzer is `analysis/liquid_glass_runtime_probe.py` (SHA-256
`b4725f3a7c3b6b069c912eff0a45aa5f844a40ff7dd7278eb897817e398edb93`).
The model and presentation trees contain two identical copies of every filter
input below.

The live `CABackdropLayer` is window-server aware, allows filtered luma, and
uses `scale = 0.5`. Its `glassBackground` filter exposes 55 inputs. The active
clear-material values include:

| Input | Exact observed value |
| --- | ---: |
| `inputBlurRadius` | `1` |
| `inputFaceOpacity` | `1` |
| `inputFaceColorMatrixWhite` | float32 `1.149999976158142` |
| `inputFaceColorMatrixBlack` | float32 `0.07500000298023224` |
| `inputFaceColorMatrixSaturation` | float32 `1.059999942779541` |
| `inputSDRHoldingToneEnabled` | `true` |
| `inputSDRHoldingToneWhite` | float32 `0.9700000286102295` |
| `inputSDRShadowOpacity` | float32 `0.23999999463558197` |
| `inputClamp` | float32 `1.3758244514465332` |
| `inputInnerRefractionAmount` | `-60` |
| `inputOuterRefractionAmount` | `160` |
| `inputRefractionOpacity` | `0` |
| `inputBleedOpacity` | `0` |
| `inputShadowOpacity` | `0` |

The downstream `vibrantColorMatrix` value is an 80-byte
`{CAColorMatrix=ffffffffffffffffffff}`. The probe copies the `NSValue` bytes
before formatting, so these are the actual 20 float32 constants rather than a
fit:

```text
[ 1.2023999691, -1.0013999939, -0.1009999961, 0, 0.8999999762 ]
[-0.2976000011,  0.4986999929, -0.1010999978, 0, 0.8999999762 ]
[-0.2976999879, -1.0011999607,  1.3988999128, 0, 0.8999999762 ]
[ 0,              0,              0,            1, 0            ]
```

The complete raw value is preserved as
`3ee8993fe02d80bf16d9cebd000000006666663f075f98be9b55ff3e840dcfbd000000006666663f226c98be522780bf270fb33f000000006666663f0000000000000000000000000000803f00000000`.

The SDF stack is also no longer inferred. `CASDFLayer` reports smoothness
`8`, Gaussian radius `0`, effect offset `0`, and no element merge.
`CASDFOutputEffect` maps `[-10000, 1]`. The
`CASDFKeyFillHighlightEffect` uses white key and fill colors, global `false`,
curvature `0.7`, key and fill amounts `0.5`, heights `1`, scale terms `1`,
offset terms `0`, spread `2.792526803190927` radians, key angle
`-0.7853981633974483`, and fill angle `2.356194490192345`.

Run `30512375456`, from `lg-test` commit
`d370b072a233d69564740b1bc4ccbf1906b2356a`, adds bounded reflection of the
real SDF objects. Its 26,050,889-byte archive is
`artifacts/liquid-glass-introspection-30512375456.zip` (SHA-256
`1f72c5c7b541c12893fd26c350b62cfa490d9b4a0be0f9ebd11d40ab87d0b88a`);
all 108 members pass CRC. Its 719,831-byte `runtime.json` has SHA-256
`5a21f0c60871d0351689cb1ececbe8a5e8ff701b41502c1cc362e33daa423884`.
The independently normalized report is
`artifacts/liquid-glass-runtime-evidence-30512375456.json` (SHA-256
`c1715ee823536af5b20b993e4f3c0d60ad4cf7e5eb5684bff58d761fdbfa4b10`).
The live `SwiftUI.SDFLayer`, not a reconstructed substitute, reports:

- `distanceRange = -400...8`;
- `shapeBounds = (0, 0, 800, 800)` apart from the recorded
  `2.842170943040401e-14` layout residual;
- `ovalization = 0`;
- a `CASDFElementLayer` in `bounds` mode, union operation, with
  `contentsZeroValueDistance = 0`, `contentsOneValueDistance = 1`, and
  `gradientOvalization = 0`;
- a circular element with `cornerRadius = 400`.

Run `30514594562`, from `lg-test` commit
`09e11ba108d18b90b9f44a24303d85c439d2d5dd`, tests whether the private SDF
field can be recovered without inference by rendering every live SDF layer
directly. Its 26,081,527-byte archive is
`artifacts/liquid-glass-introspection-30514594562.zip` (SHA-256
`d1d03a974e019ec1f9e6c5d3387253fe44cee7e0886d0c54688a80cec2b3c1ca`);
all 128 members pass CRC. The 728,676-byte `runtime.json` has SHA-256
`572619c5997f62d48518133d0ae883b1f04dc848849da23c760a6c6f934e6bf6`.
The independently verified report is
`artifacts/liquid-glass-runtime-evidence-30514594562.json` (SHA-256
`3d130f33873f024529043d24eda35ccca4434de9ba5e11d9aa6e8580f6238932`);
its analyzer SHA-256 is
`8ebcc8c1a53ea0c5fe7070feffdadfccad1abbdd8b1b3c1b0cad569038b970ab`.

Both the model and presentation trees contain five bounded SDF layers that
accept the render request and one zero-sized portal that is explicitly
skipped. All ten 800x800 RGBA8 raw buffers are byte-for-byte zero: 25,600,000
checked bytes, zero nonzero channel values, and the same independently
reproduced FNV-1a checksum `00c79d81ebc76325`. This does not mean the live SDF
is empty—the WindowServer capture visibly uses it. It proves that
`CALayer.render(in:)` does not expose these server-side/private raster
resources, so direct local layer rasterization is closed as an extraction
route.

The disassembled Apple AIR now fixes the downstream arithmetic as well. The
complete private distance-field path is a three-stage jump-flood pipeline:

1. `brim_init_lph` samples the half-precision coverage alpha at the current
   pixel and its four axial neighbours. It emits the floored integer pixel
   coordinate only when the center is above the host half-precision alpha
   threshold and at least one neighbour is at or below it; all other pixels
   emit the `(0, 0)` sentinel.
2. Each `brim_jump_lph` pass searches the 3x3 neighbourhood at the
   host-supplied integer jump distance. For every nonzero candidate coordinate
   it evaluates
   `fast_sqrt(dot(pixel - candidate, pixel - candidate))`, adds either the
   candidate coverage alpha or `1 - alpha` according to the current pixel's
   side of the threshold, rounds that cost to binary16, and retains the
   strictly smaller candidate. An infinite winner becomes the zero sentinel.
3. `sdf_gen_field_lph` reads the winning unsigned integer coordinate, computes
   the same float32 fast distance, signs it from
   `hostFloatThreshold < centerHalfAlpha`, then evaluates
   `(signedDistance + 0.5 - boundaryHalfAlpha) * hostFloatScale +
   hostFloatBias` with fast float32 operations and converts exactly once to
   binary16.

`sdf_gen_gradients_lph` then central-differences the half field, using
`right - left` and `up - down`, normalizes it with float32 `fast_rsqrt`, and
applies the host scale/bias before half conversion. This proves that the
production field is not an analytic circle SDF: exact general-shape parity
requires reproducing Apple's coverage raster, jump schedule, tie behavior,
fast-math results, and host uniforms.

In `glass_background_lph`, each distance interval is evaluated by a float32
FMA and saturation before conversion to binary16. The opacity differences are
multiplied and accumulated in ordered binary16 operations; the resulting
scale multiplies the requested blur radius in float32 and is then rounded to
binary16. The mip LOD is:

```text
effectiveRadius < 2
    ? half(log2(half(1 + 0.5 * effectiveRadius)))
    : half(log2(effectiveRadius))
```

These observations replace fitted SDF and blur-profile equations with live
constants and Apple code. The still-unobserved host scale/bias used to create
the SDF texture and the radius-dependent mip-generation uniforms remain
protected unknowns, so no production shader change is authorized.

## Exact fixed-radius scale isolation

GitHub run `30512173612` at `lg-test` commit
`851d96065678d67540077d8d024bcd20c823e673` produced
`artifacts/liquid-glass-fixed-resource-lod-sweep-30512173612.zip`
(3,258,056 bytes, SHA-256
`b4408af67eb72443c26d67e7f370ca38115bfdf23c868451aca074c37d6b6da1`).
The 264,539,520-byte native RGB stream and both source controls validate
exactly. The independent report is
`artifacts/liquid-glass-fixed-resource-lod-30512173612.json` (SHA-256
`4af5fecdecae33819fe46e502664bf7497e0f8ef264ea24dd2b01f844b71439d`).

All 104,976 default-profile spatial signatures at requested radius one and
all 104,976 at requested radius four match a same-radius constant-opacity
catalog state exactly. A signature contains all 15 native RGB8 values from
five amplitudes; equality is lossless byte equality, not a hash, fit, or
tolerance. This proves that the protected interior spatial variation lies on
a one-dimensional constant-profile response curve. It does **not** prove
that a catalog index is the internal SDF scale, because changing all five
opacity endpoints also changes Apple's upstream source path. The captures do
prove that the live refraction amounts add no separate result at scale one
when `inputRefractionOpacity = 0`: the complex and zero-refraction paths are
1,574,640/1,574,640 values exact at both requested radii.

The sweep also falsifies two shortcuts:

- radius-one grid state 37 and exact scale one differ in 30,464 of
  1,574,640 values even though both LOD expressions fall in the previously
  identified 37/64 sampler bucket;
- holding a constant radius-product does not collapse the full radius-four
  path to the cross-radius flat catalog: states 74 and 85 through 127 differ,
  with 14,019,361 changed values and maximum error 26 codes.

The exhaustive binary16 scale sweep introduced at `lg-test` commit
`abb9797bb94ef651b67d59343d236945b9e7805a` is therefore required to recover
the complete response-equivalence curve without treating a coarse sampler
bucket or cross-radius resource as interchangeable.

The independent fixed-endpoint falsification report is
`artifacts/liquid-glass-resource-confound-30512173612.json` (SHA-256
`171d8dd51589ba19c74997cc79c8bfeb5a2e13a9c76eb28f995f3f5b2e858592`).
For a measured radius-one amplitude-127 spatial response, it exhaustively
tests every nonnegative binary16 endpoint pair through one under the measured
half-texture sampler and every possible RGBA8 endpoint pair on the exact
1/16- and 1/64-code spatial grids under the measured fused UNORM sampler.
Both candidate counts are zero. The nominal fixed-resource curve therefore
has a state-dependent source path; it cannot be explained by one fixed pair
of mip samples with only LOD changing.

GitHub run `30512850382` at `lg-test` commit
`abb9797bb94ef651b67d59343d236945b9e7805a` completed in 24m14s.
Its 14,949,451-byte artifact is
`artifacts/liquid-glass-sdf-scale-sweep-30512850382.zip` (SHA-256
`5ca38b819762a3610512ac93f5beb05acd06a65e5589687f08b2969923ade8b8`);
all members pass CRC, including the 647,177,040-byte native RGB stream.
The exact report is
`artifacts/liquid-glass-sdf-scale-30512850382.json` (SHA-256
`94937f049fa9892f5cc0905420b6ccee77040590f9165c3a9d9b07e907f57d6c`)
and its candidate maps are
`artifacts/liquid-glass-sdf-scale-30512850382.npz` (SHA-256
`531662ff26fd4d48371523243ea8b9ce7e01362eacdf7f80dc4838686c52b4d6`).

Both controls are exact. Every one of the 104,976 default radius-four
15-byte spatial signatures matches the exhaustive response catalog, with
zero unmatched signatures and contiguous candidate sets. No signature is
unique: candidate counts range from 19 to 411 states. This is complete
response coverage but not an invertible internal-scale measurement. The
continuous normalized-circle AIR model selects an exact response for
104,795/104,976 signatures (99.8276%) at the best of the three preregistered
pixel offsets, but no coordinate offset or single affine radial scale fits
all candidate intervals. The remaining disagreement cannot be assigned to
SDF geometry while the measured upstream-resource confound remains active.

GitHub run `30514097218` at `lg-test` commit
`56fc754b98359a47fa3b2e12aaad2f58034feb92` completed in 24m53s. Its
14,928,032-byte artifact is
`artifacts/liquid-glass-pinned-sdf-scale-sweep-30514097218.zip` (SHA-256
`88241cb22861cffa15ada9242f16614561ce400dbe054cb85d0b5c50453a459c`);
all members pass CRC. The exact report is
`artifacts/liquid-glass-pinned-sdf-scale-30514097218.json` (SHA-256
`a8c06b61cd23d24df5f8a861570c4b87904546d3a4ed4c5a8292b472936957cc`).
Its candidate maps are byte-for-byte identical to the all-opacity run at
SHA-256
`531662ff26fd4d48371523243ea8b9ce7e01362eacdf7f80dc4838686c52b4d6`.

This intervention varies only blur opacities zero and one, holds opacities
two through four at one, and fixes the live distance profile to
`[-400, -1, 0, 0, 0]`. Both controls match exactly. More importantly, all
104,976 protected response curves match the all-opacity corpus at all 411
binary16 states. The upstream-resource confound is therefore absent over
this tested interval.

Applying the 800-pixel introspection layer's `[-400, 8]` distance range to
the separate 4000-pixel protected circle produced a normalized-circle
prediction with 181 of 104,976 samples outside their accepted catalog
interval. That was a preregistered hypothesis, not a measured Apple
residual. The distance interventions below falsify the cross-size transfer:
the 4000-pixel protected grid is entirely deeper than the live `-400`
breakpoint. The 181 samples therefore are not remaining Apple-versus-model
errors and must not be optimized.

GitHub run `30516089186`, from `lg-test` commit
`e2287dfdbe60c43ec85bba9cb1fe21898afbc846`, executed the preregistered
adjacent-half threshold sweep. Its 3,989,595-byte artifact is
`artifacts/liquid-glass-sdf-threshold-sweep-30516089186.zip` (SHA-256
`f6c81a2b2399354d15c85c715661f7c2be65dca9caa51d525f7b9f96c863d993`).
The 162,187,920-byte native stream has SHA-256
`98d6424f1af07c14cb120c2295bf4b42ddb2455c4b7f1e31375ebc02d7259844`.
The independent report is
`artifacts/liquid-glass-sdf-threshold-30516089186.json` (SHA-256
`f261905a0f1fb9263a25a4fda2f8de8234cb03545bafea5aa725e27b7086249d`).

All 515 thresholds from `-400.25` through `-271.75` produce one
byte-identical response at all 104,976 spatial samples. The threshold
endpoints also coincide exactly. Thus there are zero endpoint-discriminating
samples and no recoverable half words in that range. This is a successful
falsification of the assumed range, not evidence that the distance uniforms
are dead.

Run `30516708707`, from commit
`6e4e7cc3f3007abc89d0f4770823bb1dc4f495ba`, broadened the calibration
ranges. Its 2,223,732-byte artifact is
`artifacts/liquid-glass-sdf-distance-calibration-30516708707.zip` (SHA-256
`33b0b6703b6a2e1c57ad150049f8332dd8d3f72103214d11b8cd3dd3d154663c`).
It proved that pinned radius-zero and radius-four controls differ at every
sample, but its `-10000`/`-9999` sentinel interval is degenerate because
both inputs round to the same binary16 value. Its range conclusion is
therefore superseded by the adjacent-half run.

Run `30516866870`, from commit
`7b6f26cbdd0f4ea727386d891f93d31099f164cb`, uses the adjacent representable
binary16 values `-10008`, `-10000`, and `-9992`. Its 2,685,762-byte artifact
is `artifacts/liquid-glass-sdf-distance-calibration-30516866870.zip`
(SHA-256
`94cc3177afb2f23f7b3fb06ba37ab5b38954e02106a932c0d6baa38215d89f0e`).
The independent report is
`artifacts/liquid-glass-sdf-distance-calibration-30516866870.json` (SHA-256
`9453c0142df731a653c3274d7edddc073e9954495233a027c2aa77bc9fb9446b`);
the native stream SHA-256 is
`d26e24a6c55005f4b23d1c14e9cf9adfe30b78436d3f39c83e9df66fa30a02f2`.

Holding the complete opacity profile at `[0, 1, 1, 1, 1]` yields exactly two
lossless response classes:

- collapsed sentinel, far-positive, live, both raw, and both normalized
  ranges are byte-identical to one another and to the pinned zero-radius
  response;
- the two adjacent sentinel brackets are byte-identical to one another.

The classes differ at every one of 104,976 pixels and in 308,700 of 314,928
RGB values, with maximum difference 72 codes. Distance inputs are therefore
renderer-live. Both adjacent sentinel brackets selecting the same class
falsifies an exact `-10000` field. Under the disassembled saturation
arithmetic, the two classes observationally bracket the protected field
between the extreme sentinel regime and `-400.25`; they do not recover its
exact binary16 words.

Most importantly for the production capture, the actual live distance
profile `[-400, -1, 0, 0, 0]` is byte-for-byte identical to the pinned
zero-radius endpoint over the entire protected grid. Apple therefore selects
opacity zero exactly in this 4000-pixel deep interior. Exact numeric SDF
recovery remains necessary for boundary and general-shape parity, but it is
not a dependency of the protected interior source-filter measurement.

The remaining interior unknown is now the production radius-one source
pyramid. AIR proves that its mip-generation fragment uses seven ordered
binary16 samples—center plus three symmetric offset pairs—with host-supplied
offsets and weights. Those uniforms and the resulting production replay must
be recovered on randomized train and protected holdout sources with zero
unequal native RGB values before any production shader change is authorized.

GitHub run `30517618605`, from `lg-test` commit
`58e80df6ed70fb009f3cbb4a24bd97bd100e5638`, tests whether active blur
opacity can scan that pyramid without rebuilding it. Its 8,638,867-byte
artifact is `artifacts/liquid-glass-production-kernel-30517618605.zip`
(SHA-256
`49f299ab46cc9490db2245d407168ecbb3f680fb03423f24aff428ec7a75cbc7`).
The 75,582,720-byte native identity stream has SHA-256
`375eadc539363edcfad55f72ddb561e26f9b8da9399fd3c7d457d3fa3823a84a`.
The independent report is
`artifacts/liquid-glass-production-kernel-30517618605.json` (SHA-256
`2a0fa48728377019c212a2ad179178d0d391e4b825bcc082a1202c6893622bbd`).

All 1,889,568 deterministic source-control values are exact, both production
captures are byte-identical, the constant input is exact through all 40
states, and all 1,889,568 sampled channel curves are monotonic. The
preregistered resource-invariance gate nevertheless rejects the intervention:
the grid-37 active opacity and exact opacity one share the same measured
37/64 LOD bucket but differ in 390,968 native values, with maximum error two
codes. Active opacity therefore conditions the upstream source resource; its
otherwise clean curves cannot be interpreted as samples from one frozen
pyramid.

GitHub run `30518053052`, from `lg-test` commit
`4be7a123cd2c3a58c3f4960ffce87b1f8be64e2d`, leaves requested radius and
all five production opacities unchanged while varying only distance inputs.
Its 13,773,684-byte artifact is
`artifacts/liquid-glass-production-distance-30518053052.zip` (SHA-256
`16af2d8fd5ab8242e4fae1dd21c6d5c6582482a8651197c92f5dbfe7399d4c6d`).
The 134,159,328-byte native stream has SHA-256
`11a325d1c0ae73a8f3169a02c0b2a244e9d1a46cad43b49a99bb41f954e7071a`.
The independent report is
`artifacts/liquid-glass-production-distance-30518053052.json` (SHA-256
`bfb6a7a3ee89eef3b972ab5dc1c6ed25c376165e8650c86b07065d986a70bdcd`).

Both alternate opacity-one distance profiles match production in
1,889,568/1,889,568 native values, and both saturated opacity-one-half
profiles match one another in 1,889,568/1,889,568 values. The endpoints
differ in 1,442,840 values, providing dense discrimination. Across all 65
coarse thresholds there are zero intermediate values, zero reverse
transitions, zero source/channel class conflicts, and zero uncovered spatial
samples. Every one of 104,976 spatial curves transitions exactly once.
Distance inputs therefore provide an accepted control axis over one unchanged
production source resource.

The occupied field lies in nine coarse brackets spanning approximately
`-2013` through `-1342`. Each individual broadband pattern discriminates at
least 104,848/104,976 samples; every pair covers all 104,976. The exact
follow-up at commit `2b3ba8d58de44b58e8b0cb71facee2642e889098`
therefore uses two preregistered training patterns and enumerates all 672
adjacent binary16 thresholds from `0xe7dd` through `0xe53e`. The first
opacity-one response at each spatial sample is its exact SDF half word. No
production change is authorized unless all controls, endpoint identities,
and single-transition requirements pass.

GitHub run `30518467617`, from `lg-test` commit
`2b3ba8d58de44b58e8b0cb71facee2642e889098`, completed that exhaustive
scan in 16m22s. GitHub reports a 45,252,954-byte artifact with SHA-256
`7a9c302468b54ec4fa9ee5a71e0598eebacb2b7cce0f5f4d9e5980fea705ceea`;
the downloaded
`artifacts/liquid-glass-production-sdf-exact-30518467617.zip` matches both
exactly and all members pass CRC. Its 426,412,512-byte native identity stream
has SHA-256
`2a9a7b7dd2ae47d8fcf6ed1308fc081179490a8507355c9e80c6cc94e0959e45`.
The independent report is
`artifacts/liquid-glass-production-sdf-exact-30518467617.json` (SHA-256
`428478068ca763cbbf6de6828bde1ecd5d7b1bd0f52cda585fce99bf2f4d89a3`);
its compact exact field map is
`artifacts/liquid-glass-production-sdf-exact-30518467617.npz` (SHA-256
`7ba27b21d32d3b4cded96e1abf990307ad8b981d34287aaf71a7e8fa132a2e1b`).

Every gate passes:

- the two deterministic source controls are exact in 629,856/629,856 native
  values;
- leading and trailing production renders, the two opacity-one profiles, and
  the two opacity-one-half profiles are byte-identical within their classes;
- the endpoint classes differ in 577,985 native values, giving dense
  discrimination;
- all 104,976 spatial samples transition exactly once, with zero intermediate
  values, zero reverse transitions, zero uncovered samples, and zero
  source/channel conflicts;
- the exact field spans binary16 words `0xe7be` through `0xe563`, or
  `-1982` through `-1379`.

The runner advanced from macOS 26.4 (25E246) to 26.5.2 (25F84) between the
coarse and exact scans. This does not introduce an unmeasured cross-version
assumption: the shared production, opacity-one, and opacity-one-half endpoint
captures for both retained source patterns are exact across all
629,856/629,856 native values in each comparison.

A continuous 4000-point circle evaluated at the best tested `+0.5` pixel
convention reproduces 69,026/104,976 half words (65.7541%); every remaining
word is exactly one half step away. The AIR disassembly explains this bounded
pattern: Apple's field is generated from antialiased half-coverage, seeded
boundary pixels, subpixel alpha costs, and repeated jump-flood passes, not
from an analytic circle formula. The correct next gate is therefore an exact
replay of that decoded raster/JFA path on general-shape holdouts, followed by
recovery of the fixed production source-pyramid uniforms. Fitting the
remaining one-step differences with an ad hoc radial correction is forbidden.

## V2.19 fixed-block and real runtime evidence

| Property | Result |
| --- | --- |
| Archive | `liquid-glass-captures-30477841671-static.zip` |
| Bytes | 8,234,820,345 |
| SHA-256 | `54c6beaacd1898869cda3aac5e171657b8e4a03677c1dcf7ba3a43ff93ba515b` |
| ZIP integrity | All 3,255 members pass CRC |
| Capture source | `lg-test` commit `269a86ad0831112af396d1e721c06a2a31041929` |
| Capture host | macOS 26.4 build 25E246, arm64 `VirtualMac2,1` |
| Toolchain | Xcode 26.5 build 17F42 |
| Static evidence | 617 references and 2,633/2,633 stable captures |
| Validation result | Valid with zero errors and zero warnings |

Independent strict validation is
`artifacts/liquid-glass-validation-30477841671-local.json` (SHA-256
`30c765ac7e872a6f66a545f34a867fae999c9a11009b49112a234378ba3d3281`).
The independent measurement replay is
`artifacts/liquid-glass-measurements-30477841671-local.json` (SHA-256
`633cc593a171e3481ef960b204d483ebfdb29826168a15627c23972410c565a5`).
It has zero non-floating differences from the CI report. Differences caused
by the newer local NumPy/SciPy/Pillow stack are confined to 3,631 floating
reductions, with maximum absolute delta `0.0000004032553416`.

The v2.19 cross-run report is
`artifacts/liquid-glass-crossrun-30470041517-30477841671.json` (SHA-256
`f820a2ef052e719c750757ca7faf1d37d83b9db899425007480570c97e590bd5`).
All 617 source references are bit-exact. Of 2,633 shared outputs, 2,626 are
bit-exact. The seven differences are all large-rectangle, dark-appearance
RGB-noise cases. Their maximum channel delta is one code and their changed
pixel fractions range from `0.00016609375` to `0.00051484375`. Fixed
impulses, fixed blocks, source controls, and every other shared output are
exact. The same noise-case family varied in the preceding run while remaining
stable within each run, so it is an observed Apple cross-process low-bit
envelope, not a tolerance available to Walle.

The real SwiftUI runtime tree is recorded in
`introspection/runtime.json` (SHA-256
`fcf7ddda3ac3201be07019c4cbc0a3ecfec4f1e5ccc9d184b014e209bcd743db`).
It is evidence from the actual Apple material instance, not an inferred
implementation:

- `CABackdropLayer` carries the `glassBackground` `CAFilter`, reports
  `scale = 0.5`, and is window-server aware.
- Its shape stack is a `CASDFLayer` containing a `CASDFElementLayer`; the
  circle is represented by `cornerRadius = 400`.
- A sibling SDF portal and a second `CASDFLayer` carry
  `CASDFKeyFillHighlightEffect` and the downstream
  `vibrantColorMatrix` filter.
- Both `CASDFLayer` instances report `smoothness = 8`.
- The hosted Apple-paravirtual device exposes no `gpuTraceDocument`
  destination, so no `.gputrace` was claimed.

The runtime `scale = 0.5` independently matches the pixel-domain proof of a
half-resolution clear path. The filter ordering independently matches the
measured separation between a spatial stage and a cross-channel point stage.
Framework presence alone is not treated as proof that a framework implements
either stage.

The reproducible compact spatial report is
`artifacts/liquid-glass-clear-compact-fit-30477841671.json` (analyzer
SHA-256
`cac9b52266f384bc4be913bb399d3a25166d83da43db7274b2512baae05ba3c8`).
Its internal partition fits amplitudes 2/16/64 and block sizes 2/8/32,
withholds block sizes 4/16/64, and withholds amplitude 127. It does not open
the protected historical output holdouts. A half-grid Gaussian plus
state-linear sharp mix reaches:

| Partition | Overall exact | Active exact | Maximum error |
| --- | ---: | ---: | ---: |
| Fit amplitudes and sizes | 97.7991% | 86.7698% | 2 codes |
| Unseen block sizes | 97.1932% | 86.8825% | 2 codes |
| Unseen amplitude 127 | 91.6577% | 70.1282% | 3 codes |
| All measured cases | 96.0226% | 80.6580% | 3 codes |

Continuous signed distance, a within-state slope, and a free 13-state lookup
do not improve both holdouts. The within-band error correlation is only
`0.01323` on all cases. The 13 optical states therefore remain discrete.
This compact model is rejected for production.

The point-stage interval proof is
`artifacts/liquid-glass-clear-point-stage-30477841671.json` (analyzer
SHA-256
`15cfca823a26213344e82e358ebf4f19b8cce942185741f2e84a52470809b273`).
At the four central pixels of 64-pixel blocks, 12,160 observations collapse
to 494 distinct source RGB codes with zero conflicting outputs. A single
arbitrary real affine map followed by a floor or nearest-code quantizer is
mathematically infeasible: the minimum required extra half-width is
`0.07483917`, `0.00998750`, and `0.06021246` codes for red, green, and blue.
Even a full cubic code-space polynomial remains infeasible in red and blue.

A compact denominator-2048 integer matrix is 98.3806% exact over the 1,482
channel values; 24 input colors miss by one code. A free categorical additive
diagnostic is interval-feasible, and the RGB contribution of each input
channel is nearly rank one: `0.99999217`, `0.99997036`, and `0.99999751`
energy fractions. This supports per-channel nonlinear or discrete processing
before one fixed cross-channel matrix, consistent with the observed
`glassBackground` then `vibrantColorMatrix` stack. The categorical fit is not
a deployable LUT and does not authorize a shader change.

All 130 root analysis and renderer tests pass. No production optical code was
changed. The shader remains byte-frozen at SHA-256
`11f3dd2ab07bf41230f9b53fc4db7a9b788bd5300695a9d8a62b0ef741c9a2f3`.

## V2.13 geometry-selector identification artifact

| Property | Result |
| --- | --- |
| Archive | `liquid-glass-captures-30420761535-static.zip` |
| Bytes | 2,354,059,562 |
| SHA-256 | `7498867d67df9b8405c746d911e773ec1d24a221d44d41b5cffb84e3780080b8` |
| ZIP integrity | Every entry passes CRC |
| Capture source | `lg-test` commit `ae322f0d3e7019a1cf8722b5ec8f4545628926e4` |
| Capture host | macOS 26.4 build 25E246, arm64 `VirtualMac2,1` |
| Toolchain | Xcode 26.5 build 17F42 |
| Static evidence | 146 references and 1,884/1,884 stable captures |
| Validation result | Valid with zero errors and zero warnings |

Independent strict validation is
`artifacts/liquid-glass-validation-30420761535-local.json` (SHA-256
`06c13fa5a24f01abeec806c8c172c25fa0cecea16f7b4045a118fe5174d2525f`).
The schema-v10 measurement replay is
`artifacts/liquid-glass-measurements-30420761535-local.json` (SHA-256
`f6343362639d1f2c7d39ee119cf6703ce796bc233edf355b76167d58cb3d111c`).

The v2.12/v2.13 comparison is
`artifacts/liquid-glass-crossrun-30416047818-vs-30420761535.json` (SHA-256
`85a216acd715709582fed2de045d12264a58fc5fd31b42cb6ad4656b7a5c75e9`).
All 140 inherited references are pixel-exact. Of 1,788 inherited captures,
1,787 are exact. The sole difference is one pixel with maximum channel delta
one among 6.4 million pixels in
`checker-0064|circle-4000-center|clear|dark`; the maximum and mean absolute
channel deltas are 1 and `0.00000015625`.

The reproducible geometry analysis is
`artifacts/liquid-glass-clear-geometry-fit-30420761535.json` (SHA-256
`37ec772f21738d1ef1938f767ea9c2d2481a240a7d4cbab969f17eb0e7358286`);
its analyzer SHA-256 is
`0147ec864d793227e0edf92f00c120e6c7304aac54cbabbdbc83c1364d053c85`.
It uses only the four designated training fields to infer state boundaries.

The three oversized shapes differ over most central pixels, but their contrast
responses collapse under one coordinate:

`q = 1 + signedDistance(point, boundary) / inradius`

Cross-shape collapse RMS is `0.02753165` output code for this normalized
signed-distance coordinate. Competing width radius, height radius, bounding
ellipse, and box-maximum coordinates score `0.12355319`, `0.12476704`,
`0.12573759`, and `0.14421621` codes. The response is a 13-state staircase,
with training-derived boundaries:

```text
0.0800000, 0.1577545, 0.2289485, 0.3037185, 0.3753005, 0.4434995,
0.5183790, 0.5866120, 0.6550380, 0.7233850, 0.7911125, 0.8595865
```

For pixels that two different shapes assign to the same state, the joint
four-training-seed RGB output is exactly equal with probability `0.987628` to
`0.997281`. For different states the corresponding joint equality probability
is only `0.007028` to `0.010542`. This identifies a shape-local discrete
filter selector. Geometry-dependent gain/filter terms explain 72% to 81% of
the independent-seed geometry delta; adding coordinate-warp terms gains
nothing.

This does not yet identify Apple's exact source-to-state filter. The two v2.13
kernel holdouts were opened during this geometry investigation. They confirm
the selector, but are now development-exposed and cannot serve as a fresh
final gate. No production shader change is authorized.

`lg-test` v2.14 supplies the missing quantization-aware evidence. It appends
source amplitudes 17, 31, and 47 for the four training seeds, plus complete
17/31/47/64 ladders for two new protected seeds. Every ladder is captured
under four oversized geometries, including a transposed 4000x6000 rectangle
that creates orthogonal signed-distance bands. The existing six amplitude-64
kernel fields and a uniform control are also captured under that rectangle.
This is 20 new references and 107 new captures; all 1,884 v2.13 captures remain
an unchanged prefix. The fitting report inventories but does not decode the
fresh holdout outputs. They stay sealed until a source-to-state model is frozen.

No production optical code was changed. The shader remains byte-frozen at
SHA-256
`11f3dd2ab07bf41230f9b53fc4db7a9b788bd5300695a9d8a62b0ef741c9a2f3`.

## V2.12 boundary-free clear identification artifact

| Property | Result |
| --- | --- |
| Archive | `liquid-glass-captures-30416047818-static.zip` |
| Bytes | 1,427,344,802 |
| SHA-256 | `530b3057a68e40ec2d91d151ba99401e506d757bc28df9bbf75c28b812dd2b0c` |
| ZIP integrity | Every entry passes CRC |
| Capture source | `lg-test` commit `4bbfe2eebc675572c296ae0ec45641d4ed848bb4` |
| Capture host | macOS 26.4 build 25E246, arm64 `VirtualMac2,1` |
| Toolchain | Xcode 26.5 |
| Static evidence | 140 references and 1,788/1,788 stable captures |
| Validation result | Valid with zero errors and zero warnings |

The local archive size and SHA-256 exactly match GitHub's finalized upload.
Independent strict validation produces
`artifacts/liquid-glass-validation-30416047818-local.json` (SHA-256
`de721d8fb631dde152d3347405c51243035a4de66bd44c5a4c2c1bf29fbe2921`).
After normalizing only the artifact path, it equals the embedded report.

The independent schema-v9 measurement replay is
`artifacts/liquid-glass-measurements-30416047818-local.json` (SHA-256
`2759c91d28675d2f71db50ecfe75ce1a0f874bd92643a6f88cf4fbada9a7968c`).
All hashes, integers, arrays, and other non-floating structure are exact.
The 3,133 floating reductions differ only by the expected arm64/x86 reduction
order, with maximum absolute delta `0.0000002773804579`.

The v2.11/v2.12 comparison is
`artifacts/liquid-glass-crossrun-30410677531-vs-30416047818.json` (SHA-256
`7063167125007f27cda90769702511de5fd265ab55023ad8be5ed459db686f3a`).
All 140 references and all 1,704 inherited captures are pixel-exact. The
right-only set is exactly the intended 84 clear captures. There is no shifted
Apple state in the inherited matrix.

Clear light/dark appearance identity now holds on all 106 comparable
giant-circle cases. Paired amplitude-16 and amplitude-64 fields obey linear
scaling to quantization: continuous mean error is about 0.263 code, p95 is
0.5, and the maximum is one code. Adjacent output deltas on the held-out RGB
field have phase magnitudes `4.4078, 8.7537` horizontally and
`4.4039, 8.7491` vertically. That repeated 1:2 signature identifies a
two-pixel output grid.

An unconstrained projection onto a half-resolution, half-pixel bilinear
subspace reaches 0.216-code continuous mean error and reconstructs 87.73% of
held-out pixels exactly; a quarter-resolution projection fails at 7.188-code
mean error. The source references and no-glass controls for the tested noise
and edge probes are pixel-exact, so this is an Apple material response rather
than display round-trip error.

The empirical low-grid impulse response contains a sharp center tap near
0.567 plus a smooth fourfold-symmetric lobe through low-grid radius six. Its
sum is about 1.033, fourfold asymmetry is 0.133%, and a rank-one separable
approximation leaves 8% relative error. The clear response is therefore not a
single Gaussian.

The reproducible structural search is
`artifacts/liquid-glass-clear-grid-fit-30416047818.json` (SHA-256
`aa65e7cf2095cfd7fd067bcf78e590f6bc294a705bf2eafe3e913b6cac33ccf1`);
its analyzer SHA-256 is
`e988abd8dfb2640f4932349c6e7dd9e653d8b34637efe820d7947f53f94bb1d4`.
The best frozen structure uses a 13x13 half-grid kernel, quarter/eighth-scale
paths, and degree-two radial terms. Across all holdouts it reaches
0.273-code continuous mean error and 74.99% exact rounded pixels. On the
previously unopened block it reaches 0.266-code mean error, 0.559-code p95,
1.043-code continuous maximum, and 78.19% exact rounded pixels. Every rounded
miss is one code.

This is not a parity pass. A uniform gray-128 field is exactly code 152
everywhere, yet stochastic contrast response increases with radius even while
the glass boundary remains offscreen. The current artifact cannot distinguish
a circle-local optical-depth field from a geometry-independent kernel with an
unidentified coordinate mapping.

`lg-test` v2.13 resolves that ambiguity without consuming the existing
holdouts. It appends four independently seeded RGB training fields, two new
RGB holdouts, and an oversized rectangle. The six fields are captured under
clear material through the centered 4000-point circle, translated 6000-point
circle, and 6000x4000-point rectangle; the two historical amplitude-64 RGB
fields are replayed through the two new geometries. The validator regenerates
all six sources independently. Across their 18 full-size RGB bit planes,
one-bit fractions are 0.499792344 to 0.500502812 and maximum absolute pairwise
correlation is 0.001058737. Analysis schema v10 reports exact regional hashes
and bitwise appearance/geometry differences before any model is fit.
The expected v2.13 static artifact contains 146 references and 1,884 captures.

No production optical code was changed. The shader remains byte-frozen at
SHA-256
`11f3dd2ab07bf41230f9b53fc4db7a9b788bd5300695a9d8a62b0ef741c9a2f3`.

## V2.11 multiscale/local-mean identification artifact

| Property | Result |
| --- | --- |
| Archive | `liquid-glass-captures-30410677531-static.zip` |
| Bytes | 1,231,040,564 |
| SHA-256 | `4cfda680d6716e773f47aa5c501d7974dd7394d945161326e4170b5ebbc755ae` |
| ZIP integrity | All 1,847 entries pass CRC; no duplicate paths |
| Capture source | `lg-test` commit `d2d46f3939a0fb2b3edd42f7b5fc5daa88332756` |
| Capture host | macOS 26.4 build 25E246, arm64 `VirtualMac2,1` |
| Toolchain | Xcode 26.5 build 17F42 |
| Static evidence | 140 references and 1,704/1,704 stable captures |
| Validation result | Valid with zero errors and zero warnings |

The local byte count and digest exactly match GitHub's finalized upload.
Independent strict validation decodes every PNG and produces
`artifacts/liquid-glass-validation-30410677531-local.json` (SHA-256
`b5479cece4986e4b00c7a771b9aaed3ab031b49c5f3fd61300845ae391fef635`).
It equals CI's report after normalizing only the absolute artifact path.

The independent schema-v8 measurement replay is
`artifacts/liquid-glass-measurements-30410677531-local.json` (SHA-256
`55f5839c56c49a0fcd3f77ebd3b8755625a12e70aaf81e317adc8bf3e5399c44`).
Across 288,768 comparable leaves, 286,009 are bit-exact and 2,759
floating-point values agree within `2e-6` relative / `2e-7` absolute
tolerance. The maximum cross-platform difference is
`0.0000002773804579`; there are zero failures.

The v2.10/v2.11 cross-run report is
`artifacts/liquid-glass-crossrun-30395758967-vs-30410677531.json` (SHA-256
`09ca1325c85a78c8747b12919d93a42cb1fb5fe85361d34d9e2567f0f2bbfb6c`).
All 119 shared references are pixel-exact. Of 1,494 shared captures, 1,493
are exact. The sole difference,
`sine-x-p0256-ph0|circle-0500-upper-left|regular|light`, affects 13,677 of
6.4 million pixels, has mean channel delta `0.00405849`, and maximum delta
10. Its v2.11 hash exactly matches the stable state observed in v2.7 and v2.8;
v2.9/v2.10 contain the other previously observed state. It is Apple
bistability, not a new capture or shader regression.

Clear material is pixel-exact between light and dark appearance on all 21
adaptive probes. The known `(37, 53)` source translation is not
translation-equivariant after glass: after alignment, 95.37% of clear pixels
differ by 3.99 codes on average and regular differs on about 96.5% by 1.40
codes. The residual is strongest at fixed block-boundary phases, identifying
a renderer-grid sampling stage rather than a stationary Gaussian.

The frozen training-only multiscale fit is
`artifacts/liquid-glass-v211-fit-30410677531.json` (SHA-256
`e3a986e43e5e1a4369d1b6d98e7fab6244a3da26ee7a1568249cece688ccbdde`).
Its independent holdouts reject that model family:

| Variant | Held-out MAE | p95 | Maximum | Exact rounded pixels |
| --- | ---: | ---: | ---: | ---: |
| clear | 1.446 codes | 4.303 | 15.370 | 8.00% |
| dark regular | 0.679 codes | 2.090 | 11.087 | 27.05% |
| light regular | 0.776 codes | 2.470 | 19.926 | 24.93% |

The diagnostic polyphase report is
`artifacts/liquid-glass-polyphase-probe-30410677531.json` (SHA-256
`778b717140b74b84e8d36fa40479c72d6f5bd860f5f0910acccb99ded58fb1fc`);
its analyzer SHA-256 is
`75c482bf294fd2f1455ee23107a36107a2dd78222c229b094341307527b50287`.
On the independent pixel-noise seed, a phase-blind 17x17 complete RGB kernel
has 5.217-code MAE.
Conditioning the same kernel on `(x mod 2, y mod 2)` lowers MAE to 0.756.
A 4x4 phase split reaches 0.746, while 8x8 worsens to 0.976, so the dominant
grid is two pixels and the larger split overfits. Direct output deltas are
4.77 codes within a two-pixel cell and 9.49 across the next cell, the exact
1:2 signature of half-pixel bilinear reconstruction.

The same held-out 2x model scores 0.613 codes in the central 100-pixel disk
but worsens to 0.756 by radius 200. This distance dependence proves the
500-point clear probe mixes the fixed sampling grid with circle refraction.
`lg-test` v2.12 therefore appends 84 boundary-free 4000-point clear captures:
the existing eight pixel-scale stochastic probes, edge/line/noise/checker
probes, and the missing p32/p128/p512 four-phase MTF. It adds no reference,
seed, threshold, or historical-case reorder. The independent one-pixel fields
already excite every 2D grid phase, so a brute-force translation matrix is
unnecessary.

Regular material independently identifies a four-pixel grid. Its held-out
adjacent deltas by absolute phase are `0.366, 0.189, 0.366, 0.365` codes in
dark appearance and `0.379, 0.199, 0.379, 0.380` in light. The
`1 : 1/2 : 1 : 1` pattern is exactly 4x half-pixel linear reconstruction. A
17x17 diagnostic improves from 0.617/0.632 phase-blind MAE to 0.434/0.442 at
phase four; phase eight worsens to 0.491/0.500 on the independent seed.

The structural regular fit is
`artifacts/liquid-glass-quarter-grid-fit-30410677531.json` (SHA-256
`57346b0273194a02f9a02f4a91f47503d30e1d7347d634c6601459979390d288`);
its analyzer SHA-256 is
`9edf40514841af699185365a9156b8b66c32b9bc5055170ef46044cd3e7326ef`.
Across 1,920 training-only candidates, both appearances independently select:

- encoded-sRGB processing;
- exact 4x area reduction and half-pixel linear reconstruction;
- quarter-grid Gaussian features equivalent to 0, 2, 4, 8, and 16
  full-resolution pixels;
- no full-resolution source bypass; and
- degree-one color mixing. Degrees two and three do not generalize.

On the four independent stochastic seeds this model reaches 0.244-code dark
and 0.256-code light MAE, 2.094/1.956-code maxima, and 79.36%/79.10% exact
pixels. The older phase-blind Gaussian model reached 0.406/0.415 codes and
32.72%/30.11% exact pixels on the comparable RGB +/-64 holdout.

An unconstrained least-squares projection of Apple's regular output onto the
4x bilinear subspace establishes the remaining quantization floor. On the
independent RGB +/-64 seed it reconstructs 88.42% of dark pixels and 86.12% of
light pixels exactly, with continuous MAE 0.2332/0.2376 code and maximum
1.021/0.978 code. The measured output is therefore almost entirely inside the
quarter-resolution bilinear subspace; the remaining source-to-output gap is
the exact low-grid kernel and quantization order, not missing high-order color
terms.

No production optical code was changed. The shader remains byte-frozen at
SHA-256
`11f3dd2ab07bf41230f9b53fc4db7a9b788bd5300695a9d8a62b0ef741c9a2f3`.

## V2.10 adaptive-response identification artifact

| Property | Result |
| --- | --- |
| Archive | `liquid-glass-captures-30395758967-static.zip` |
| Bytes | 763,016,359 |
| SHA-256 | `7efbd9785cbe0848cf6a0ca2be2ba7b29656aeecefe6478e82c0c3bb56e92213` |
| ZIP integrity | All 1,616 entries pass CRC |
| Capture source | `lg-test` commit `66d806f1c4dd1b70eae07ac3da62009a43e2a251` |
| Capture host | macOS 26.4 build 25E246, arm64 `VirtualMac2,1` |
| Toolchain | Xcode 26.5 build 17F42 |
| Static evidence | 119 references and 1,494/1,494 stable captures |
| Validation result | Valid with zero errors and zero warnings |

The local archive byte count and SHA-256 exactly match GitHub's finalized
upload. Independent strict validation reopens every PNG and is byte-identical
to the earlier local result (`SHA-256
b8fe95a413d0eabfc261832e54f4c7717627208be22b186885c220096d8a6a18`).
The v2.11-compatible analyzer also reproduces the normalized v2.10 schema-v7
report exactly; it does not expose the new section to an older artifact.

An independent schema-v7 replay compares 286,141 leaves with the CI report:
284,326 are exact and the remaining 1,815 floating-point values are within
`2e-6` relative / `2e-7` absolute tolerance. The maximum difference is
0.0000002774 and there are zero failures. The replay report has SHA-256
`fb1fa551067fe27324fcee2495da6831d42ad0dc4b5a9d816b77484b552ca28b`.

Cross-run reproducibility against v2.9 is exact:

- all 103 shared references are pixel-exact;
- all 1,350 shared captures are pixel-exact; and
- the right-only set is exactly the intended 16 references and 144 captures.

No Apple-side repeatability envelope is needed for this static pair. The
cross-run report has SHA-256
`181ae3b44019ffe9ae8ede210784706bb9d0ed1d0872002d4e642906507d2169`.

V2.10 closes the requested capture matrix but does not identify a passing
regular-material model. The untouched holdouts report:

| Frozen model / holdout | Dark | Light |
| --- | ---: | ---: |
| Ten-scale chart model, on-grid shuffle MAE | 2.937 codes | 3.245 codes |
| Ten-scale chart model, on-grid shuffle maximum | 39.911 codes | 59.466 codes |
| Raw-inclusive bank, RGB ±64 MAE | 0.406 codes | 0.415 codes |
| Raw-inclusive bank, RGB ±64 maximum | 2.774 codes | 2.597 codes |
| Raw-inclusive bank, RGB ±64 exact pixels | 32.72% | 30.11% |

The raw-inclusive audit spans the unfiltered source and 20 Gaussian scales
through 256 pixels. Degree and ridge penalty are selected only by
leave-one-training-probe-out validation; both appearances select the 63-term
linear model. Higher polynomial degrees do not generalize. Its report has
SHA-256
`41b1b5847c7d1c6333acb65e643f2e094b1f757e396a4dfe3a8ac35fbce1d41e`
and embeds analyzer SHA-256
`f423576334c4f412f60922e100c13cdc581af22e178dbb96c3014d8705c24833`.

The evidence therefore rejects both a pointwise color transform and the
remaining stationary multiscale-polynomial approximation. It does not justify
tuning the production shader against the final holdouts. `lg-test` v2.11 adds
21 narrowly targeted backgrounds:

- calibrated full-color block fields at 4, 16, 64, and 256 pixels, using
  on-grid training codes and independently seeded 507-color source-safe
  midpoint holdouts; the historical chart retains all 512 midpoint colors;
- paired gray/RGB block fields at means 64, 128, and 192 with independent
  train/holdout seeds; and
- a known `(37, 53)`-pixel periodic translation check.

The full-color seeds were selected before observing Apple output from 500,000
deterministic candidates, using only b256 source-field marginal balance,
channel means, and cross-channel correlation. Their worst normalized design
scores are 0.05981 for training and 0.05043 for holdout, down from accidental
source correlations near 0.17 in the initial draft.

Every new field is captured under both `.regular` and `.clear` in the
edge-free giant-circle scene. The v2.11 validator independently regenerates
all 21 source images and requires pixel-exact archive equality. The historical
shuffled charts and v2.10 stochastic holdouts remain untouched. A v2.11 static
artifact is required before a quality-safe shader fit can continue.

The production shader remains byte-frozen at SHA-256
`11f3dd2ab07bf41230f9b53fc4db7a9b788bd5300695a9d8a62b0ef741c9a2f3`.

## V2.9 focused-static spatial-identification artifact

| Property | Result |
| --- | --- |
| Archive | `liquid-glass-captures-30387500844-static.zip` |
| Bytes | 389,206,247 |
| SHA-256 | `8cf97da6b36a4b2d4ed4421d7ef3846b6589c96c4de42f533e2536775b2c77bf` |
| ZIP integrity | All 1,456 entries pass CRC |
| Capture source | `lg-test` commit `61674058b8efa4de1b998492823eb0f8c2bd7ebe` |
| Capture host | macOS 26.4 build 25E246, arm64 `VirtualMac2,1` |
| Toolchain | Xcode 26.5 build 17F42 |
| Static evidence | 103 references and 1,350/1,350 stable captures |
| Validation result | Valid with zero errors and zero warnings |

The local archive size and digest exactly match GitHub's finalized upload.
Independent strict validation reopens every PNG and produces the same report
as CI after removing only its absolute artifact path. The environment records
a 1920x1080 60 Hz Apple Virtual display, 3200x2000 sRGB capture window, 1x
backing scale, active/key window, and all three accessibility overrides
disabled.

Cross-run reproducibility against v2.8 is nearly exact: 101/101 shared
references and 1,261/1,262 shared static captures have identical canonical
pixel hashes. The sole difference is
`sine-x-p0256-ph0|circle-0500-upper-left|regular|light`: 13,677 of 6.4 million
pixels differ (0.2137%), with mean channel delta 0.00406 and maximum 10. It is
an isolated Apple-side repeatability event; the edge-free giant-circle phase
fit does not use that quadrant frame.

The independent shuffled layouts confirm that regular material is spatial:

| Context comparison | Dark regular MAE / p95 / max | Light regular MAE / p95 / max |
| --- | ---: | ---: |
| Ordered vs affine permutation | 11.540 / 33.7 / 60 | 10.697 / 36 / 124 |
| Ordered vs independent shuffle | 11.165 / 33 / 87 | 9.805 / 33 / 88 |
| Ordered vs shuffled midpoint cube | 9.758 / 28 / 62 | 8.520 / 29 / 73 |

All three clear-material comparisons are exactly zero.

The complete giant-circle regular MTF is isotropic but multi-scale. The
measured period-32 through period-1024 fundamental ratios are
0.0522/0.1196/0.1492/0.1572/0.1841/0.2531 for dark x and
0.0806/0.1851/0.2309/0.2433/0.2542/0.2675 for light x; y agrees closely.
A model fitted only on x and selected by the three-pixel line uses:

- dark: 53.3% at sigma 7.588 px plus 46.7% at sigma 163.769 px;
- light: 84.1% at sigma 7.508 px, 3.84% at sigma 16.560 px, and 12.1% at
  sigma 152.217 px.

On the untouched y-axis frequency ladder, its worst modulation error is 0.353
output code for dark and 0.874 for light. That is strong identification, not a
parity pass: the orthogonal edge still reaches 6.48 codes of error.

The full-spectrum `noise-gray` capture is a second independent rejection. On
1,298,944 central dark pixels the model has 0.367-code mean/3-code maximum
error; on 1,549,924 light pixels it has 0.584 mean/5 maximum. Predicted
standard deviations are only 0.393 and 0.699 codes versus measured 0.716 and
1.105, with centered correlations 0.592 and 0.687. The response is
contrast-dependent or otherwise adaptive; a single full-amplitude MTF is not
enough.

Finally, fitting the color transform on the ordered and affine layouts and
then freezing it before the Fisher-Yates holdout yields 3.193-code mean/42.305
maximum error in dark appearance and 5.285 mean/72.254 maximum in light.
A flexible multiscale context regression selected within the affine layout
also fails the independent random layout. The v2.9 artifact therefore
completes the *holdout*, but not a sufficiently diverse context-training
distribution.

V2.10 adds four randomized on-grid training layouts, four randomized midpoint
training layouts, and independent fit/holdout binary gray and RGB noise at
two amplitudes. The original v2.9 shuffles remain untouched final holdouts.
An exact 6.4-million-pixel audit of all eight deterministic bit fields found
one-bit fractions from 0.499606 to 0.500251 and no pairwise absolute
correlation above 0.000718. The ±16 and ±64 probes deliberately reuse each
field so amplitude dependence is paired rather than confounded by a new
random realization.

The schema-v7 analyzer was also replayed directly against the v2.9 ZIP. After
excluding only the schema/implementation metadata and three intentionally new
unavailable sections, it preserves all 166,344 legacy report leaves:
166,180 are bit-exact and the remaining 164 optimizer results are within
`2e-6` relative / `2e-7` absolute tolerance, with zero failures and maximum
absolute difference 0.000000506.

The production shader is still byte-frozen at SHA-256
`11f3dd2ab07bf41230f9b53fc4db7a9b788bd5300695a9d8a62b0ef741c9a2f3`.

## V2.8 color-context artifact

| Property | Result |
| --- | --- |
| Archive | `liquid-glass-captures-30379034310-all.zip` |
| Bytes | 7,796,682,869 |
| SHA-256 | `59ae420876cc3aeb57f6b10657d666a669b20ff7f4ef0ddc6a003a314b9e3531` |
| ZIP integrity | All 3,326 entries pass CRC |
| Capture host | macOS 26.4, build 25E246, arm64 `VirtualMac2,1` |
| Toolchain | Xcode 26.5, build 17F42 |
| Runner image | `macos-26-arm64` 20260720.0258.1 |
| Static evidence | 103 references and 1,262/1,262 stable captures |
| Exact-state evidence | 24 sweeps and 1,224/1,224 stable frames |
| Live evidence | 32 sequences, 703 real frames, and 32 delayed endpoints |
| Validation result | Rejected for two source-calibration and four temporal-coverage errors |

The downloaded byte count and SHA-256 exactly match GitHub's finalized upload.
Independent extraction and validation reopen every image and reproduce the
embedded six errors and 47 warnings. The archive is not globally valid.

The two source-calibration errors are completely localized. Five of the 512
midpoint chart tiles traverse the virtual display as red code 15 instead of
the requested code 16:

- `(16, 240, 144)`;
- `(16, 240, 176)`;
- `(16, 240, 208)`;
- `(16, 208, 240)`; and
- `(16, 240, 240)`.

Every affected pixel differs in one channel by exactly one code. That is
62,500 pixels, 0.9765625% of the image, with mean absolute channel delta
0.0032552083. Light and dark controls are pixel-exact with each other. This is
a deterministic display-color conversion, but the v2.8 0.5% / 0.002 source
bound correctly rejects it. It must be represented as source calibration, not
silently spent as shader error.

The live errors are sampling holes:

| Sequence | Failure |
| --- | ---: |
| `wallpaper-transition__regular__light` | 230.719 ms actual and 0.229062 presented-progress gap |
| `wallpaper-transition-reverse__regular__light` | 227.005 ms actual gap |
| `wallpaper-transition-reverse__clear__light` | 248.640 ms actual gap |

The unchanged ceiling is 200 ms. These sequences remain rejected. They do not
invalidate the static color charts.

Static reproducibility is exact. The runner, OS, Xcode, display, backing scale,
and accessibility state match accepted v2.7 run `30326591212`; all 101 shared
references and all 1,242 shared static captures have identical canonical pixel
hashes. The only new pixels are the 20 intended v2.8 chart cases.

The new context experiment decisively rejects a pointwise regular-material
model:

| Appearance/material | Same-color context MAE / p95 / maximum |
| --- | ---: |
| dark / clear | 0 / 0 / 0 codes |
| light / clear | 0 / 0 / 0 codes |
| dark / regular | 11.540 / 33.700 / 60 codes |
| light / regular | 10.697 / 36.000 / 124 codes |

Clear is pixel-exact for all 729 colors after spatial permutation. Regular is
not. The 512 off-grid midpoint colors independently reject direct trilinear
interpolation: maximum errors are 2.661 codes for clear, 29.000 for dark
regular, and 37.331 for light regular. A tone-plus-residual representation
reduces clear's maximum to 1.210 codes but does not fix regular.

Existing frequency probes explain the regular discrepancy. At the center of
the 500-pixel circle, the p32/p64/p128/p256 fundamental amplitudes are
0.0559/0.1212/0.1496/0.1585 in dark appearance and
0.0861/0.1880/0.2322/0.2449 in light appearance. That is a compact local
response. Separately, a three-pixel white line has only about 19 to 25 pixels
of one-code reach, while a half-field step changes the nominally black side
even 100 pixels from the source edge. Giant-circle p1024 and the permuted
charts therefore expose an additional broad, low-frequency adaptation
component. The v2.8 giant-circle ladder samples that component only at p256
and p1024.

V2.9 was the next evidence-driven experiment:

- a seeded Fisher-Yates context of all 729 fitting colors, independent of the
  affine permutation;
- the same independent context repeat for all 512 midpoint colors;
- missing p32, p128, and p512 giant-circle regular-material phases; and
- edge, line, checker, and deterministic-noise giant-circle regular probes.

It also reports captured no-glass chart codes alongside nominal source codes.
The source-calibration prevalence bound becomes 1.0% / 0.0033 while preserving
the hard one-code maximum. This narrowly includes the five measured tiles and
does not weaken any Walle pixel gate.

The updated transfer audit correctly reports
`colorTransferCertificationReady=false`,
`pointwiseColorLutRejectedByMaximumCodes=124`, and
`spatialCaptureCoverageComplete=false` for v2.8. The production shader remains
unchanged at SHA-256
`11f3dd2ab07bf41230f9b53fc4db7a9b788bd5300695a9d8a62b0ef741c9a2f3`.
The analysis-schema-v6 measurement report has SHA-256
`58c95a012afc87f5bf71409f4805521be63767fb73c108e535d6728f5ecfeb13`;
the fit-schema-v2 audit has SHA-256
`749196099b9cc582e23495fe694e7ee424c0b33aa50d31620489aebe01d8061f`.

## Second v2.7 all-suite artifact: deterministic optics, incomplete timing

| Property | Result |
| --- | --- |
| Archive | `liquid-glass-captures-30365533488-all.zip` |
| Bytes | 7,931,617,677 |
| SHA-256 | `def52e047656519400606a300a83c1d905b4068b5b85b705118a923180ca7242` |
| ZIP integrity | All 3,344 entries pass CRC; 3,341 are PNGs |
| Capture host | macOS 26.4, build 25E246, arm64 `VirtualMac2,1` |
| Toolchain | Xcode 26.5, build 17F42 |
| Runner image | `macos-26-arm64` 20260720.0258.1 |
| Static evidence | 101 references and 1,242/1,242 stable captures |
| Exact-state evidence | 24 sweeps and 1,224/1,224 stable frames |
| Live evidence | 32 sequences, 742 real frames, and 32 stable delayed endpoints |
| Capture result | Zero app-level or transient sample failures |
| Validation result | Rejected for five temporal-coverage errors in four sequences |

The downloaded byte count and SHA-256 exactly match GitHub's finalized upload.
Independent strict validation reopens every image and reproduces the CI report
exactly after removing only the expected artifact-path field. Independent
optical recomputation has the same 24,811 measurement leaves as CI. All 24,693
numeric leaves agree within `2e-6` relative / `2e-7` absolute tolerance, with
23,362 bit-exact and a maximum numerical difference of 0.000000277 pixels.

The artifact is not the second wholly valid live run. The rejected sequences
are:

| Sequence | Actual-time gap | Presented-progress gap |
| --- | ---: | ---: |
| `wallpaper-transition__regular__light` | 222.320 ms | 0.186562 |
| `wallpaper-transition-reverse__regular__light` | 208.006 ms | 0.135937 |
| `wallpaper-transition__clear__dark` | 444.923 ms | 0.249687 |
| `wallpaper-transition-reverse__clear__dark` | 253.549 ms | 0.166562 |

The unchanged hard limit is 200 ms in either domain. All 857 screenshot
attempts decoded and no image is corrupt. Relative to the accepted run's 1,059
attempts, acquisition throughput fell by 19.1%; the failures are missing
WindowServer samples in the full-frame two-wallpaper traversals, not an
optical mismatch or presentation-clock failure. The manifest records capture
cost only for frames retained by the target-bin selector, so it cannot
attribute discarded sampling holes to an individual slow acquisition. This is
a capture-runner diagnostic limitation, not permission to weaken the gate.

The valid optical subsets are exceptionally repeatable against run
`30326591212`:

- 101/101 references are pixel-exact;
- 1,242/1,242 static captures are pixel-exact;
- all 64 live initial and delayed endpoints are pixel-exact;
- 1,164/1,224 settled sweep frames are pixel-exact. The 60 differences affect
  2 to 54,602 pixels (median 119.5), at most 1.5001% of a frame and ten code
  values.

The sweep differences remain measured Apple-side cold-repeat and warm-history
effects. They are retained as an envelope and are not a parity tolerance that
Walle may spend. This artifact therefore completes the second independent
run for references, static probes, exact-state sweeps, and endpoint controls.
Its 28 temporally valid sequences also count individually. A focused
one-second dynamic run of the two wallpaper-transition modes can replace the
four rejected traversals while preserving artifact provenance; repeating the
multi-gigabyte static and sweep matrices again is unnecessary.

Replaying the production analytical gate against this artifact passes all 56
protected metrics with zero regressions. The rendered-pixel gate remains
deliberately false, so no production shader change is authorized. The shader
remains frozen at SHA-256
`11f3dd2ab07bf41230f9b53fc4db7a9b788bd5300695a9d8a62b0ef741c9a2f3`.

## First wholly valid v2.7 artifact

| Property | Result |
| --- | --- |
| Archive | `liquid-glass-captures-30326591212-all.zip` |
| Bytes | 8,335,182,555 |
| SHA-256 | `d0fb036b613e7db50e11f38a2550e8baad417688b98e8641aa8e18f288a24fa4` |
| ZIP integrity | All 3,467 entries pass CRC; 3,464 are PNGs |
| Capture host | macOS 26.4, build 25E246, arm64 `VirtualMac2,1` |
| Toolchain | Xcode 26.5, build 17F42 |
| Runner image | `macos-26-arm64` 20260720.0258.1 |
| Static evidence | 101 references and 1,242/1,242 stable captures |
| Exact-state evidence | 24 sweeps and 1,224/1,224 stable frames |
| Live evidence | 32 sequences, 865 real frames, and 32 stable delayed endpoints |
| Capture result | Zero app-level or transient sample failures |
| Validation result | Valid with zero errors |

The downloaded size and digest exactly match GitHub's finalized upload.
Independent strict validation reopens every image and reproduces the CI report
exactly after removing only the expected artifact-path field. Independent
optical recomputation preserves all 23,988 shared measurement leaves, with
zero missing or out-of-tolerance values and a maximum cross-platform numerical
difference of 0.000000277 pixels.

All 32 live sequences pass. They retain 17 to 39 unique frames each. The worst
actual acquisition gap is 177.789 ms and the worst presented-progress gap is
0.1925, both below the unchanged 200 ms limits. All 1,059 capture attempts
decode successfully. The run therefore supplies the first wholly valid v2.7
live traversal set and the first half of the independent optical-repeatability
corpus.

All 24 initial/post-settle source controls and all 16 shape-aligned gray
position comparisons are pixel-exact. The quadrant phase captures initially
appeared less consistent because a 33-pixel local estimate sampled only part
of a nonlinear p256 sine cycle. Analysis schema v4 now fits one complete
source-normalized cycle. Across the center and four quadrants, the largest
measured displacement range is 0.0451 physical pixels and the largest
amplitude-ratio range is 0.000237. All 23,987 legacy report leaves remain
bit-exact after adding the corrected estimator.

Within the run, 22/408 fresh-cold states and 155/408 warm-reverse states differ.
The largest within-run difference affects 54,602 pixels, or 1.50% of one
frame, with a seven-code maximum. Comparing v2.7 against v2.6 gives:

- 101/101 references pixel-exact;
- 1,113/1,114 shared static captures pixel-exact; the sole difference is the
  qualitative HIG controls scene and affects 1,002/6,400,000 pixels at no more
  than two codes;
- 64/64 live initial and delayed endpoints pixel-exact;
- 1,030/1,224 settled sweep frames pixel-exact. The 194 differences affect
  1 to 54,602 pixels (median 85.5, p95 29,587), at most 1.50% of a frame and
  eight codes.

`Analysis/compare_runs.py` now computes this envelope directly from two
directories or ZIPs. These Apple-to-Apple differences remain evidence of
renderer repeatability and history, not a tolerance that Walle may spend.

## First v2.6 artifact: useful but not wholly accepted

| Property | Result |
| --- | --- |
| Archive | `liquid-glass-captures-30321772562-all.zip` |
| Bytes | 7,836,739,979 |
| SHA-256 | `d2ca866cbca173e3dbaf8ddac3e3836baad7ca6f5f8e3613d2b7c50451af78ae` |
| ZIP integrity | All 3,201 entries pass CRC; 3,198 are PNGs |
| Capture host | macOS 26.4, build 25E246, arm64 `VirtualMac2,1` |
| Toolchain | Xcode 26.5, build 17F42 |
| Runner image | `macos-26-arm64` 20260720.0258.1 |
| Static evidence | 101 references and 1,114/1,114 stable captures |
| Exact-state evidence | 24 sweeps and 1,224/1,224 stable frames |
| Live evidence | 32 sequences, 727 real frames, and 32 stable delayed endpoints |
| Capture result | Zero app-level capture failures |
| Validation result | Rejected: one live sequence violates two temporal-coverage gates |

The downloaded SHA-256 exactly matches GitHub's upload digest. Independent
validation reproduces the CI report byte-for-byte after removing the expected
artifact-path field. Independent optical recomputation has the same structure
across 25,755 report nodes. Of 17,714 floating-point values, 16,974 are
bit-exact; the other 740 are all within `2e-6` relative / `2e-7` absolute
tolerance. The maximum absolute platform difference is 0.000000277 pixels.

The rejected sequence is
`wallpaper-transition__regular__light`. Its last interior sample is at
0.784472 seconds and presented progress 0.738125; the endpoint is at 1.123076
seconds and progress 1.0. This creates a real 338.604 ms acquisition hole and
a 0.261875 presentation hole, both above the unchanged 200 ms hard limit. No
image is corrupt and there were no transient decoder failures. The other 31
live sequences pass.

The non-temporal evidence is strong:

- all 100 retained references and all 1,066 retained static captures are
  pixel-exact with accepted v2.5;
- all 24 initial/post-settle source controls are pixel-exact after excluding
  only the declared four clock rows;
- shape-aligned 700x700 gray-128 crops are pixel-exact at the center and all
  four quadrants for both materials and appearances;
- 382/408 fresh-cold repeat states are pixel-exact. The 26 differences affect
  20 to 33,134 pixels (median 142), at most 0.918% of a frame and eight code
  values;
- all eight new two-wallpaper fresh-cold repeats are pixel-exact;
- 258/408 warm-reverse states are pixel-exact. The 150 differences affect 2
  to 34,436 pixels (median 239), at most 0.954% and nine code values;
- two-wallpaper warm-reverse differences are material-dependent: regular
  affects at most 30,698 pixels / 0.480% / nine codes, while clear affects at
  most 269 pixels / 0.00421% / three codes.

These direction-dependent states are retained as real Apple hysteresis. They
are not a tolerance that Walle may spend arbitrarily. The artifact is accepted
for its complete static, endpoint, and exact-state subsets, but not as one of
the two required wholly valid v2.7 runs.

The v2.7 analyzer replay is
`artifacts/liquid-glass-measurements-30321772562-local-v3.json`. Its embedded
implementation hash matches the analyzed `measure.py`. All 20,436 legacy
measurement leaves remain present; all 20,324 shared numeric leaves agree with
the CI analyzer within `2e-6` relative / `2e-7` absolute tolerance, with zero
failures. The new report adds physical pixel-difference magnitudes for every
non-identical sweep state, source-endpoint diffs, and aligned spatial
comparisons without changing any prior measurement outside its declared
numerical tolerance.

## Accepted v2.5 baseline

| Property | Result |
| --- | --- |
| Archive | `liquid-glass-captures-30317142057-all.zip` |
| Bytes | 2,674,611,273 |
| SHA-256 | `15862a17607ad473fa9c7c31b39c6672bf8657605d02218b82a34749c475ee58` |
| ZIP CRC | All 1,896 entries pass |
| Capture host | macOS 26.4, build 25E246, arm64 `VirtualMac2,1` |
| Toolchain | Xcode 26.5, build 17F42 |
| Runner image | `macos-26-arm64` 20260720.0258.1 |
| Clock preflight | Settled 0.25/0.75; live 0.499375/1.0 |
| Visual state | Active key window; all three accessibility overrides false |
| Static evidence | 100 references and 1,066/1,066 stable captures pass |
| Exact-state evidence | 16 sweeps and 272/272 stable, unique frames pass within-run validation |
| Live evidence | All 20 sequences and 455 real frames pass temporal-fit gates |

The embedded validator is valid with no errors and six requested-bin timing
warnings. Independent decoding rechecked all 1,893 PNGs: every file hash,
canonical pixel hash, size, opaque-alpha condition, and sRGB declaration
passes. The local optical recomputation is
`artifacts/liquid-glass-measurements-30317142057-local.json`; it agrees with
the CI report across 24,020 report nodes and 16,704 floating-point values
within `2e-6` relative / `2e-7` absolute tolerance. The maximum absolute
platform difference remains 0.000000277 pixels.

## Prior v2.3 artifact

| Property | Result |
| --- | --- |
| Archive | `liquid-glass-captures-30311970063-all.zip` |
| Bytes | 2,610,630,421 |
| SHA-256 | `14dce9346b119f7ccfa5b344ec37f663a73f10cbe8109ee3e7b0768865d95978` |
| ZIP CRC | All 1,864 entries pass |
| Capture host | macOS 26.4, build 25E246, arm64 `VirtualMac2,1` |
| Toolchain | Xcode 26.5, build 17F42 |
| Runner image | `macos-26-arm64` 20260720.0258.1 |
| Visual state | Active key window; Reduce Transparency, Increase Contrast, and Reduce Motion all false |
| Static evidence | 1,066/1,066 stable; complete matrix; all file/pixel hashes, sRGB metadata, alpha, controls, and source-round-trip bounds pass |
| Exact-state evidence | 16/16 sweeps, 272/272 stable frames, and 272 unique states pass |
| Live evidence | 16/20 sequences pass temporal-fit gates |
| Rejected live evidence | Four materialize sequences have only their two observable clock bins |

The archive is complete and useful, but the artifact as a whole is correctly
invalid. The continuous worker successfully decoded 28–32 screenshots for
each materialize sequence with zero transient failures, but SwiftUI held the
marker at zero until the endpoint. Binning therefore correctly retained only
indices 0 and 60. All 16 geometry sequences now pass with 21–31 unique real
frames, maximum actual gaps below 89 ms, and maximum presented-progress gaps
below 0.084.

All nine unit tests pass on the macOS runner, confirming the earlier
`/var`-to-`/private/var` test-fixture problem is fixed.

Run 30315225943 is a separate, intentionally fail-fast v2.4 diagnostic
artifact:

| Property | Result |
| --- | --- |
| Archive | `liquid-glass-captures-30315225943-all.zip` |
| Bytes | 9,806 |
| SHA-256 | `4885dbd900a2785c2e7a2559757ca9de1467955847bda11ac87f1c654dcf8c6a` |
| ZIP CRC | Both entries pass |
| Contents | `manifest.json` and `validation.json`; no images |
| Failure | Core Animation clock decoded midpoint 0.0 and endpoint 0.0 |

This contains no new optical evidence. It proves the v2.4 clock implementation
was not observable through the capture path, while correctly preventing a
multi-gigabyte invalid matrix from running. The two zeroes do not distinguish
overlay occlusion from omission of the server-side transform.

## Cross-run determinism

All 100 references and all 1,066 static captures in v2.5 are pixel-exact with
both v2.2 and v2.3. Consequently every static optical measurement is also
numerically identical to v2.3.

The settled sweeps expose a narrower state-dependence that the within-run
stability gate cannot detect. Across v2.2, v2.3, and v2.5, 245/272 frames are
pixel-exact in all three runs; another 14 v2.5 frames exactly match one of the
two prior runs. Thirteen v2.5 frames differ from the mutually identical prior
pair. Eight change only 1–28 pixels. Five light/regular frames are larger:
late morph states change 68,441 and 69,987 of 3.64 million pixels, while the
first three wallpaper-wipe states change 9,870–25,894 of 6.4 million pixels.
The maximum channel delta is eight codes. Difference masks follow the glass
shapes and do not intersect the presentation-clock rows. These five frames
are withheld from a strict cross-run fit pending an identical dynamic-only
rerun.

## Measured static transfer

Measurements use the median of a 65x65-pixel region deep inside the centered
500-point circle. Values and fits are in encoded sRGB because that is the
canonical captured representation.

| Appearance/material | Output codes for input 0, 128, 255 | Fitted transfer | MAE / maximum |
| --- | ---: | --- | ---: |
| light/clear | 19, 152, 255 | `clamp(1.043080357*x + 0.073017429)` | 0.212 / 0.444 codes |
| dark/clear | 19, 152, 255 | same as light/clear | 0.212 / 0.444 codes |
| light/regular | 179, 219, 250 | `-0.066204377*x² + 0.346139605*x + 0.701371127` | 0.267 / 0.686 codes |
| dark/regular | 15, 60, 94 | `-0.084399849*x² + 0.388762421*x + 0.061829279` | 0.273 / 0.766 codes |

The clear body is appearance-independent to the measured precision. Regular
is explicitly appearance-dependent even for the same background. Therefore a
shader that chooses a light/dark pole from local background luminance cannot
match both real materials.

The new giant-circle ramps recover all 256 tone inputs for each material and
appearance. Every curve is monotonic. Independent horizontal/vertical ramp
decodes disagree by only 0.024 mean codes for clear, 0.142 for light regular,
and 0.523 for dark regular (worst case three codes), quantifying the remaining
spatial/refraction contamination instead of hiding it.

The 9x9x9 cube supplies 729 measured RGB outputs per material/appearance. On
the sparse holdout probes, an affine encoded-sRGB diagnostic fits clear with
0.204-code mean absolute error, 0.479-code p95, and 0.895-code maximum error.
Regular is nonlinear: light regular reaches 1.787-code MAE and 17.652-code
maximum error; dark regular reaches 1.500-code MAE and 4.341-code maximum
error. Production fitting must use the dense measured transfer (or a
cross-validated compact approximation), not the affine diagnostic.

## Measured blur

The table fits an effective Gaussian edge-spread width to checker-0128
transitions well inside each circle. It is an output-domain diagnostic, not an
assumption that Apple's full operator is exactly Gaussian.

| Circle diameter | light regular | dark regular | clear, both appearances |
| ---: | ---: | ---: | ---: |
| 256 px | 6.265 px | 4.452 px | 1.109 px |
| 500 px | 5.927 px | 5.576 px | 0.985 px |
| 1000 px | 5.730 px | 5.284 px | 0.978 px |
| 1600 px | 5.549 px | 5.110 px | 0.962 px |
| 4000 px | 5.837 px | 5.595 px | 0.945 px |

Except for the smallest regular case, the kernel is effectively fixed in
screen pixels as element diameter changes by 16x. The current Walle
preprocessor instead uses output-diagonal fractions: on the measured
5120x2880 output those constants request roughly 223 px regular sigma and
76 px clear sigma. Its 1/8-resolution glass texture also cannot represent the
measured approximately one-pixel clear response. This is a model mismatch, not
a tuning discrepancy.

The four-phase local MTF is not perfectly Gaussian. At the center of the
500-point circle, the p64/p256 fundamental-amplitude ratio is approximately
0.772 for light regular and 0.767 for dark regular, corresponding to an
effective two-frequency sigma near 7.5 px. Clear retains essentially the full
p64 modulation. The edge-spread and phase methods agree on the important
conclusion: fixed, modest regular blur and nearly sharp clear transmission.

## Measured rim, shadow, and refraction

On uniform gray-128:

- clear has a one-pixel inner rim and no greater-than-one-code outer shadow at
  any tested size;
- regular light's outer reach is approximately 11.75 px up, 18.75 px right,
  26.75 px down, and 19.75 px left;
- regular dark's outer reach is approximately 16.75 px up, 23.75 px right,
  31.75 px down, and 24.75 px left;
- those reaches are unchanged from 256 through 1600 px diameter.

The current radius-proportional shadow, rim, and lens widths therefore diverge
as the wipe grows.

Four-phase p256 measurements at the right edge of the 500-point circle show a
narrow, strong outward displacement:

| Depth inside edge | clear | regular light | regular dark |
| ---: | ---: | ---: | ---: |
| 2 px | 36.006 px | 38.711 px | 50.121 px |
| 5 px | 22.070 px | 23.527 px | 35.676 px |
| 10 px | 9.475 px | 11.253 px | 22.043 px |
| 15 px | 2.937 px | 4.071 px | 14.040 px |
| 20 px | 0.414 px | 1.934 px | 10.642 px |

The p64 phase wraps at the strongest edge displacement, which is why the
report preserves per-period wrapped values instead of pretending one
frequency can unambiguously recover the lens field. The current corpus
captures three frequencies at three element scales for proper phase unwrapping
and scale testing.

## Dynamic evidence

The v2.5 continuous sampler accepts all 20 sequences with 16–30 saved frames
per sequence. Maximum actual and presentation gaps are 115.707 ms and
0.130625, respectively, inside both 200 ms hard limits. All four materialize
sequences now contain 16–21 frames and 16–20 unique pixel states, with exact
zero and final endpoints. The staged AppKit-clock preflight measured settled
0.25/0.75 widths, a live midpoint of 0.499375, and endpoint 1.0.

The independent raster clock trails acquisition time by a 28.944 ms median
and 69.415 ms maximum on interior materialize samples. The established SwiftUI
clock has a 19.726 ms median interior offset on this run. This is measured
compositor latency, so temporal fitting must retain both `actualSeconds` and
the clock decoded from the same presented screenshot; target-grid indices are
not timestamps.

Materialize is accepted as the first real temporal measurement, not yet as a
repeatability envelope. The regular/dark sequence reaches its final pixels in
the penultimate saved frame while the clock is still at 0.978125, which is
consistent with the measured clock lag and is why timestamps and pixels—not
nominal frame numbers—must drive the fit.

## Production analytical baseline

`analysis/liquid_glass_compare.py` now reads the Apple report and the actual
constants in `shaders/frag.glsl` and `walle.c`. Its immutable baseline is
`artifacts/walle-vs-apple-30317142057-baseline.json`. Against the accepted
static evidence, the current shader has:

| Protected model metric | Current error |
| --- | ---: |
| Clear tone MAE / maximum | 36.033 / 71.649 codes |
| Light regular tone MAE / maximum | 71.114 / 161.061 codes |
| Dark regular tone MAE / maximum | 88.121 / 145.054 codes |
| Clear preblur sigma | 49.057 px vs approximately 0.95–1.11 px |
| Regular preblur sigma | 143.397 px vs approximately 4.45–6.27 px |
| Clear refraction MAE / maximum | 3.666 / 16.962 px |
| Light regular refraction MAE / maximum | 6.658 / 27.073 px |
| Dark regular refraction MAE / maximum | 14.633 / 38.483 px |

The comparator exposes 40 protected analytical metrics and rejects any
candidate that increases one relative to a supplied baseline. It deliberately
keeps its rendered-pixel gate false: matching these summaries is necessary but
not sufficient to authorize a shader change. Replaying it against v2.6 produced
`artifacts/walle-vs-apple-30321772562-audit.json`: all 40 analytical metrics
match the accepted baseline with zero regressions. V2.7 adds 16 quadrant blur
metrics. They are pinned in the expanded
`artifacts/walle-vs-apple-30326591212-baseline.json`; the corresponding audit
passes all 56 analytical metrics with zero regressions. The rendered-pixel
gate in that analytical report correctly remains false.

## Production rendered-pixel baseline

`analysis/walle_shader_renderer.py` now reproduces the production texture
preprocessing and compiles the byte-locked production vertex and fragment
shaders in a headless EGL context. `analysis/liquid_glass_pixel_gate.py`
renders at each captured presentation-clock progress, excludes only the
manifest-declared four clock rows, and compares those pixels directly with
Apple's screenshots.

The immutable rendered baseline is
`artifacts/walle-rendered-pixel-baseline-30326591212.json`. It covers every
captured frame in the two Walle-relevant live transition directions: 76
forward frames for fitting and 80 reverse-direction frames held out from
fitting. Each frame protects 16 full-frame, active-region, edge-weighted, SSIM,
and CIEDE2000 error metrics. The independent replay report
`artifacts/walle-rendered-pixel-replay-30326591212.json` reproduces all 156
case records and aggregate values exactly and passes all 2,496 per-frame
protected comparisons with zero regressions. Baseline compatibility also pins
the metric and renderer source hashes, evidence and frame selection, Python
and scientific-library versions, libvips, Mesa, and the GPU. A candidate may
change the fragment shader; it may not silently change how error is measured.

This is a measurement baseline, not a quality endorsement. Current
active-region mean absolute code errors are:

| Appearance / material | Forward fitting frames | Reverse holdout frames |
| --- | ---: | ---: |
| dark / clear | 26.240 | 24.109 |
| dark / regular | 99.634 | 91.155 |
| light / clear | 25.001 | 22.913 |
| light / regular | 21.904 | 22.662 |

The dark/regular maximum frame error reaches 152.493 codes on fitting data
and 147.620 on holdout data. This is direct evidence that appearance cannot be
inferred by the current shared regular path: an explicit appearance input and
separate measured transfer are structural requirements.

## Historical transfer-model audit and v2.8 requirement

`analysis/liquid_glass_transfer_fit.py` cross-validates representations instead
of assuming the dense charts are pointwise lookup tables. Its report is
`artifacts/liquid-glass-transfer-fit-30326591212.json`.

The full 256-code tone curves are retained. A 17-knot approximation adds up to
2.438 codes of error, which is forbidden by the no-quality-regression policy
and saves an irrelevant amount of memory.

The color result is more consequential. Interpolating a 5x5x5 subset onto the
withheld 9x9x9 knots misses by up to 15.468 codes for clear, 29.000 for dark
regular, and 33.492 for light regular. More importantly, the 23 inputs shared
by the full-field and tiled probes are pixel-exact for clear but disagree by
up to 49 codes for dark regular and 67 codes for light regular. The regular
material is therefore geometry-, neighborhood-, and/or
screen-position-dependent; the captured 9x9x9 observations must not be
deployed as a pointwise 3D LUT until those effects are separated.

The v2.7 sparse set cannot resolve that dependency. Its only eight off-grid
inputs are gray, leaving zero off-grid cross-channel holdouts. The capture rig
is now v2.8 and adds:

- all 729 fitting colors in a second, bijectively permuted spatial context;
- all 512 RGB midpoint combinations, none of which occurs in the fitting cube;
- raw sparse input/output values in the measurement report.

Those additions require a new v2.8 macOS artifact. They add only two
backgrounds, two references, 12 base captures, and eight giant-circle
captures; the dynamic and exact-sweep suites are unchanged.

## Exact private SDF reconstruction

Runs `30523670870` through `30529096266` turn the private
`CASDFGenerator` path from an AIR interpretation into an independently
executed, bit-gated reconstruction. The accepted run/commit chain is:

| Runtime schema | GitHub run | `lg-test` commit | Artifact SHA-256 |
| ---: | ---: | --- | --- |
| 20 | `30523670870` | `a7daaa0` | `2b228b5adb10dd3773fcb7e80b00a1cc9d4236f80adc21e5f3ee915abc71b390` |
| 21 | `30524366340` | `fa9f7ac` | `6b4fd9adbc5bdf4412db69d235117328567112303b76cb29852d63553b0acd08` |
| 22 | `30525035905` | `7cdb8ac` | `c37d7e7fb6719c72316bc3378d4a88572d845e77dccc21721510ecebe0bd09a8` |
| 23 | `30525347064` | `651f190` | `c9999d253f3b6944cce70a554e1c99a15a4ac088e384df96458a4af376dd61ea` |
| 24 | `30525864984` | `c58f6bf` | `dda04d6269d0edf733bdfb2b4ad8d0c67027c91949f7a88293f1c76ea3fe040f` |
| 26 | `30526862882` | `2e78b44` | `c5e42c42fdf3ab45f3cf18baf0aeeda5ff1966601eac5c4c3de06b4a58f88d6c` |
| 27 | `30527347311` | `3ef6a50` | `8280a3d887860fbe6147d1671880be3f0ebc554d00e73934195e03483f27017a` |
| 28 | `30527760453` | `e754dfa` | `11a102f854e6ce229f6139076dc1e90cbe522519eb883cca7544f9f42a265199` |
| 29 | `30528135196` | `7d242d6` | `5b49b4520011241e80f5a00afbf05605c81f6c39bb0138968f50dd13b5fa4864` |
| 30 | `30528498931` | `08ad867` | `2e43669d204e4e2204b0439a5a1269794ace5b9266f7a2dbbbd4a265d5aebf94` |
| 31 | `30529096266` | `47aa304` | `5ef45bcf3ef95d238ce2259d05b37ebfa729c205a671c38dd7eb78667377f2a3` |

Schema 25/run `30526502795` is deliberately excluded from the accepted
chain. Calling the captured private function directly hung that isolated
command buffer. The run remained useful as a negative result, but no metric
depends on it.

The native smoothing-three uniforms are no longer unknown. The 88-byte
`DownsampleBlurUniforms` record contains eight `float2` offsets starting at
byte zero, two `half4` weight vectors starting at byte 64, and the final
half-precision divide term. The five active binary16 weight words are
`[0x322d, 0x31fd, 0x2d9f, 0x26d8, 0x1d67]`. The first five normalized
horizontal offset x words are
`[0x3adf4da4, 0x3bcf71fa, 0x3c3ac66b, 0x3c86f955, 0x3cb0a3dc]`; the vertical
pass uses the corresponding y values
`[0x3abf671f, 0x3bb1cf69, 0x3c2017ca, 0x3c676249, 0x3c9767e2]`.

Coordinate traces rule out an offset or sampler explanation for the prior
blur mismatch: all 1,632,160 position-derived half samples match the compute
trace bit-for-bit, and a captured sampler replay is byte-identical to a
publicly constructed sampler. The remaining difference was arithmetic.
Apple rounds each symmetric sample pair to binary16, initializes the
accumulator with pair zero, then performs binary16 fused multiply-adds in
pair order `[0, 3, 2, 1, 4]`. Replaying exactly that order produces:

- 802,816/802,816 exact components for the independent 448x448 horizontal
  surface;
- 652,864/652,864 exact components for the active vertical surface;
- 589,824/589,824 exact components for the final 384x384 crop;
- 294,912/294,912 exact final gradient components.

The end-to-end replay uses no native intermediate texture. Both unused
448x448 exteriors are independently verified as zero.

Schema 31 closes the jump-flood winner ambiguity. The depth-two/half path
stores each accepted best cost in binary16 before the next strict-less-than
comparison. With the AIR loop order (x outer, y inner), the jump schedule
`[64, 32, 16, 8, 4, 2, 1]`, and the x-zero invalid sentinel, the local replay
matches all 294,912 RG16Uint coordinate components in Apple's final winner
texture. The corresponding raw half field matches all 147,456 native R
values. A separate Metal `fast::sqrt` trace also matches those 147,456
values, proving that standard float32 square root happens to round identically
for every captured integer distance.

The depth-zero/R8 generator selects the full-precision variant instead:
float32 best-cost storage plus one half conversion before UNORM8 reproduces
all 147,456 R codes and all 147,456 alpha codes. Substituting the half-cost
winner map into this path changes 234 R codes, so the two precision variants
are explicitly separate gates rather than a convenient merged model.

The normalized report is
`artifacts/liquid-glass-introspection-30529096266-direct-sdf.json` (SHA-256
`3d9f15aaf0dc68e3ff366942070868ef08de719934db56b3db8af428419f2f22`).
It is generated by `analysis/liquid_glass_direct_sdf.py`; its protected SDF
generator gate passes only when every field, winner, gradient, horizontal,
vertical, and crop metric above is exact. All 248 local analysis tests pass
inside `nix develop`.

This closes the private SDF generator for the captured bounded input. It does
not yet close the complete Liquid Glass renderer. The protected unknown is
now outside this generator: the live backdrop source/downsample pyramid and
the final `glass_background` color/compositing integration.

## Consequence for Walle

The current shader is preserved at SHA-256
`11f3dd2ab07bf41230f9b53fc4db7a9b788bd5300695a9d8a62b0ef741c9a2f3`.
No production optical constant was changed from the user's pre-optimization
worktree during this audit.

The accepted corpus is strong enough to reject several current model
assumptions and now reproduces the bounded private SDF generator exactly.
That result is necessary but still insufficient to replace the production
shader under a zero-regression policy: the backdrop pyramid and final
compositor have not yet passed the same bit gate.

The rendered baseline completes the original per-pixel gate requirement. The
remaining production pass needs:

1. captured intermediate textures and uniforms for the live backdrop
   source/downsample pyramid;
2. an independently executed `glass_background` compositor replay with exact
   half/FMA order, sampler state, color matrices, and output encoding;
3. a real appearance input rather than background-luma inference;
4. validation against the exact Apple stage gates, the 56-metric analytical
   gate, and the rendered training/holdout gate;
5. only then, protected production integration and fresh VRAM, throughput,
   latency, and pixel measurements.

Only after that gate passes should the shader and its glass-texture layout
change. The previously measured VRAM and latency gains are independent of
these optical changes and remain valid because they preserve the exact
pre-optimization shader and texture bytes.
