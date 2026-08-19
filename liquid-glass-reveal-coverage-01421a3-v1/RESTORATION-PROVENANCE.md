# Corpus restoration provenance

This directory is a bit-exact regeneration of the original
liquid-glass-reveal-coverage-01421a3-v1 corpus, which had been lost from
every reachable machine (only empty placeholder directories remained).

- Regenerated 2026-08-19 on the authorized M1 (MacBookPro18,2, macOS
  26.6.1 build 25G76) from the frozen, hash-pinned procedure at lg-test
  commit 01421a3 (`Analysis/run_walle_reveal_coverage_corpus_local_macos_26_6_1.sh`,
  run label `restore2`), launched inside the GUI session via
  `sudo launchctl asuser 501 sudo -u quince`.
- The preregistered validator passed (VALIDATION_STATUS=0); the full run
  directory including capture-context, preflight, and validation.json is
  preserved on the M1 at ~/lg-test-coverage-01421a3/local-walle-reveal-coverage-restore2-v1.
- All 65 sweep frame PNGs are byte-identical to the retained per-state
  referenceSha256 values recorded in
  analysis/reveal_best_known_gles_corpus_gate_result.json: 65/65 file-hash
  matches. The capture pipeline is therefore deterministic across capture
  sessions on this OS build, and this directory carries the same authority
  as the original.
