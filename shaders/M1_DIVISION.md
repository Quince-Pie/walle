# Extracted native half division

**M1 reference and exact-divider control.** The full2^32 enumeration below establishes the original M1 result and the exact control. The current accepted `hw_circle` runtime uses protected promoted-Float32 half division and does not inherit universal bit-exact equality to that control. [PLATFORM_OPERATIONS.md](PLATFORM_OPERATIONS.md) records the active divider; historical selected/accepted wording below refers to the exact implementation.

Reference: Apple M1 Max, macOS26.6.1, QuartzCore1195.17. The original half division schedule is opcode1863 (half denominator to Float32 reciprocal), then opcode1798 with destination modifier0x1000. The modifier's undocumented internal meaning is not assumed.

Every one of the2^32 raw binary16 numerator/denominator pairs was executed on that machine and compared with an independent integer rational oracle. All4,294,967,296 results equal correctly rounded, ties-to-even binary16 division. This includes every sign, subnormal, signedzero, infinity and NaN payload. NaN-generating cases produce positive canonical0x7e00. Each of128chunks checks its33,554,432 executed pairs and independently calculated operand sum. Four deliberate expected-value corruptions each produce exactly one mismatch. Archived machine code retains the original1863/1798 schedule beside the independent integer arithmetic.

The corresponding unflagged mixed product is materially different: Float32-rounded multiplication followed by half rounding differs on8,536 subnormal midpoint cases. A4,194,304-pair preliminary control missed those cases. The complete sweep rejected that implementation before promotion. The complete65,536-entry native reciprocal extraction remains research evidence; the accepted divider does not need a reciprocal table.

The portable helper reconstructs integer significands ma,mb in1..2047 and exponents ea,eb in−24..5. A leading-bit difference and one comparison compute the exact quotient exponent. It divides shifted integers at the final binary16 spacing, then uses quotient parity and the exact remainder to round. Finite branches never divide by zero or shift by32: normal shifts are0..21; after the result-exponent guards, subnormal right shifts are at most5. All shifted integers are below2^22. The remainder n−q*d is exact and avoids a separate modulo operation. Rounded carry naturally reaches the next exponent, minimum normal or infinity. Division by±1 has an exact raw-word shortcut after special-class handling.

No Int16, Int64, Float64 or additional device admission requirement is introduced. The helper is selected only for native-half emulation. Public float entrypoints retain identical SPIR-V. Only directly established half-division boundaries in GB and SDF coverage use it. VCM and highlight compound schedules remain unchanged.

Evidence is retained under `/tmp/walle-work/fidelity-completion/resume_1400/arithmetic/`:

- `exhaustive_rational`: full native domain, source, per-chunk counters, archives and decoded instructions.
- `rational_negative`: four deliberate-corruption controls.
- `exhaustive_full` and `exhaustive_differences`: rejected ordinary mixed-product substitution and every distinguishing pair.
- `division_direct_final_gpu`:4,194,304 original pairs plus all8,536 distinguishing pairs on discrete AMD, integrated AMD and llvmpipe; zero errors for the exact helper. Ordinary backend division is retained as a negative control.
- `candidate/float_controls` and `candidate/vcm_preservation`: all five float entrypoints and both VCM entrypoints in both emulation states compile byte-identically to the prior code.
- `candidate/shader_build.json`: all27 runtime shader entries compile and validate. `candidate/scenes_v1`:21 actual-scene runs pass validation and teardown. These still contain native raster/sampling differences and do not establish whole-frame equality.

A checked Float32 quotient estimate is a separate exact challenger: an estimate is accepted only if integer inequalities prove the floor; otherwise the helper uses integer division. Its performance selection is not established by correctness checks. Whole-renderer performance qualification remains separate.
