# Archive branch: liquid-glass-reveal-coverage-01421a3-v1

The Apple ground-truth corpus for walle's reveal gate: 65 states of the
macOS 26.6.1 (25G76) wallpaper-reveal at 2048x2048, captured on the
authorized M1 by the frozen, hash-pinned procedure at lg-test commit
01421a3, with the preregistered validator's output and full provenance.

This branch exists so the corpus bytes survive machine loss: the working
copies live outside git (walle/artifacts/ on the Linux workstation, the
capture run directory on the M1, plus ~/walle-archives). Every sweep
frame here is byte-identical to the referenceSha256 pins retained in
analysis/reveal_best_known_gles_corpus_gate_result.json - see
RESTORATION-PROVENANCE.md inside for the regeneration story.

To install for scoring: copy or symlink the directory to
`artifacts/liquid-glass-reveal-coverage-01421a3-v1` in a walle checkout.
