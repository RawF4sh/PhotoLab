# Quality evaluation — September 29, 2026

**Implementation and evaluation complete.** All frozen primary, component and native-export evaluations are complete. The current production default is retained because no candidate clears every proposal gate. Numerical results do not establish aesthetic superiority.

## Protocol and evidence

The [evidence index](../out-quality-2026-09-29/INDEX.md) links baseline, tuning, holdout, ablation, native, performance and gallery artifacts.

The locked experiment is `out-quality-2026-09-29/locked-experiment.json`, hash `b90891d12a1397c318a63e28d015cb6633ac970c71ad7a5b79699463a1b31c3e`. The original control package/configuration snapshot is retained under `baseline/`. Proposal benchmarks evaluate raw candidate outputs; the autonomous delivery wrapper separately records source blends, identity fallbacks and exported-pixel acceptance. A safe fallback does not turn a failed proposal into evidence that its editing architecture succeeded.

Tuning comprises 106 Dolomites captures in 95 groups. Holdout comprises 532 Switzerland captures in 211 groups: 345 RAW and 187 JPEG-only. Metadata/content-only rediscovery after the capture-selection changes preserved every capture ID, identity, source, group, companion and camera reference. See `out-quality-2026-09-29/snow-evidence/capture-rediscovery.json`. The reserved Desktop 204-photo collection was not used. Available collections use one camera and provide limited event diversity; confirmed intentional low-key examples remain unavailable.

Primary A is the **frozen current pipeline with its default stub backend**, B is unsegmented global-first development/correction, and C is unsegmented bounded optimization using B's renderer. Real SAM is evaluated in separately named arms; the default primary control must not be described as a broad comparison of SAM against no SAM.

## Baseline and detector calibration

The pre-change baseline recorded **322 tests passed, zero failed** in [baseline.json](../out-quality-2026-09-29/baseline.json); this is the recorded planning baseline, not the final integration-test result. The earlier 110-file tone report had zero dark-to-darker and zero luminance-highlight regressions under its legacy success criteria, but **three files exceeded +0.5 percentage points of net RGB-highlight clipping**. All 65 historical code/configuration hashes checked at discovery matched. See [prior-tone-stub.json](../out-quality-2026-09-29/prior-tone-stub.json). That historical success must not be presented as passing today's stricter RGB, shadow, texture and boundary checks.

The final detector calibration passed **8/8 clean fixtures** and detected **11/11 deliberately damaged fixtures**, with exact cached/uncached assessment parity on all 19. The probes include identity, low-key identity, clean RGB8/JPEG ramps, correlated texture, highlight plateaus with and without noise, posterization, per-channel clipping, mask-boundary damage, and invalid pixels. Calibration found a quantization-phase false positive on a clean brightened ramp; the measurement was corrected before photographic holdout evaluation without changing thresholds. This designed-fixture result is not a photographic sensitivity or specificity estimate. See [calibration report](../out-quality-2026-09-29/detector-calibration/README.md).

## Completed tuning proposal comparison

Source: `out-quality-2026-09-29/tuning-primary-1024px/report.json`. All 318 scheduled measurements completed, with no execution, source-integrity or frozen-code errors. These are **1024-pixel JPEG proposal measurements**, not native acceptance or visual preference results. Safe counts use the final shared detector and include encoded-pixel checks.

| Candidate | Safe proposals / 106 | Foreground median change, median | Dark-coverage change, median | Highlight retention, median | Render time, median |
|---|---:|---:|---:|---:|---:|
| A current, frozen stub | 85 | +0.0474 | −0.00934 | 0.416 | 2.226 s |
| B global first | 95 | +0.0589 | −0.01948 | 0.730 | 4.745 s |
| C optimized | 90 | +0.0592 | −0.02012 | 0.803 | 8.112 s |

A had 7 new RGB-shadow clipping flags, 8 RGB-highlight clipping flags, 9 texture flags and 1 boundary-artifact flag. B had 10 RGB-highlight, 1 luminance-highlight and 1 texture flag. C had 16 RGB-highlight, 2 luminance-highlight and 1 texture flag. Several flags can belong to one photograph, so their totals are not failed-photo counts. No candidate met zero proposal failures on tuning.

Two A outputs had a floating-render texture flag that was absent after JPEG encoding (`0057` and `0086`). The table counts final JPEG assessments; it does not claim that all floating renders passed or that JPEG encoding restored lost detail.

Highlight retention is the median across images of each image's **minimum eligible sampled highlight-patch retention** (`minimum_retention`), not a whole-frame texture estimate. It has 73 available observations and 33 unavailable per candidate; unavailable detail is not zero retention. Foreground and dark-coverage changes are available for all 106. The source-neutral-pixel WB-change vector is available for all 106 in each candidate, with separate R/G/B components; it is a heuristic diagnostic, not calibrated ground-truth neutrality or skin/material color accuracy. The [compact results](../out-quality-2026-09-29/compact-results.json) retain metric-specific denominators.

The proposal harness's camera-comparison metrics are unavailable for JPEG-only sources because its camera-preview branch handles RAWs. This missingness must remain explicit in the holdout's format comparisons. The separate camera gallery uses original source JPEGs as references for those captures.

The time figures come from concurrent workers with unequal instrumentation. A's `timing_ms` excludes the historical render, which was precomputed during shared mask setup. Its `candidate_render_ms` includes frozen-worker IPC and NPY/NPZ transfer overhead; B/C render directly in-process. Neither total-time nor render-time ratios establish an isolated cross-arm speedup. Peak RSS is a cumulative process/worker high-water mark, not a per-candidate memory allocation.

`out-quality-2026-09-29/tone-tuning-final.json` records 110 source-file renders with zero dark-to-darker, highlight, shadow or other hard failures under the safe in-memory path. This legacy folder-oriented report counts files rather than 106 deduplicated captures and is proxy evidence; it does not replace exported native acceptance.

## Completed native tuning delivery

The current/stub safety wrapper completed **106/106 deduplicated tuning captures**, with **zero failed outputs and 29 remediations**. These are accepted deliveries, not 106 successful unmodified proposals. Every exported file was independently verified as **16-bit RGB TIFF** at native dimensions, and output/sidecar/mask provenance and complete unique capture coverage passed. All **216 distinct source, companion and camera-reference paths** in this tuning manifest retained their expected hashes. See the [native run manifest](../out-quality-2026-09-29/tuning-native-current-tiff/run.json) and [independent integrity audit](../out-quality-2026-09-29/native-integrity.json).

The strict report read the actual 106 exported outputs: **zero dark-to-darker, zero highlight-clipping regressions, zero shadow-clipping regressions, zero hard failures, and zero execution/completeness errors**. See [native tone results](../out-quality-2026-09-29/tone-tuning-native-final.json) and [report provenance](../out-quality-2026-09-29/tone-tuning-native-final-provenance.json). The manifest-completeness correction is report-only and retained separately from the frozen renderer. Ten outputs have a lower whole-image mean than their source; that fact is distinct from the foreground-based dark-source regression criterion.

The TIFF protocol was selected because lossy JPEG encoding introduced RGB-shadow clipping even for an identity RAW decode on tuning capture 0020. The verified TIFF16 identity probe passed the same frozen assessment; photographic settings and safety thresholds were unchanged. [native-export-protocol.json](../out-quality-2026-09-29/native-export-protocol.json) records this delivery-format decision. Native holdout rendering, integrity and independent exported-pixel tone results are reported below.

## Completed primary holdout comparison

All **1,596/1,596** scheduled proposal measurements completed: three frozen candidates on each of 532 Switzerland captures. The merge reported no processing, source-integrity or frozen-code errors. These remain 1024-pixel JPEG proposal results, with native acceptance reported separately.

| Candidate | Safe / 532 | RAW safe / 345 | JPEG-only safe / 187 | Foreground median change | Minimum sampled highlight retention, median | Render time, median |
|---|---:|---:|---:|---:|---:|---:|
| A current, frozen stub | 459 | 304 | 155 | +0.0775 | 0.445 | 1.691 s |
| B global first | 464 | 322 | 142 | +0.0609 | 0.714 | 3.336 s |
| C optimized | 335 | 223 | 112 | +0.0668 | 0.608 | 13.267 s |

B passes five more photos overall than A, but the direction differs by source format. This is not evidence of general dominance or proof that a format-specific policy caused the difference. C has substantially more rejected proposals. **None passes every hard gate; none qualifies for architectural promotion.** Greater foreground recovery or retention in sampled highlight patches does not establish an aesthetic winner.

Overlapping failure flags are: A — 58 texture, 10 RGB-highlight, 5 RGB-shadow, 3 luminance-highlight and 8 boundary; B — 22 texture, 7 RGB-highlight, 39 RGB-shadow and 2 luminance-highlight; C — 56 texture, 164 RGB-highlight, 15 RGB-shadow, 82 luminance-highlight and 6 boundary. These are counts of flags, not distinct failed images. All 532 C searches completed without a recorded watchdog fallback; optimizer observations are not applicable to A/B.

The final encoded gate is not interchangeable with the floating-pixel gate. Float-pass to encoded-fail transitions occurred in 13 A, 48 B and 139 C proposals; float-fail to encoded-pass transitions occurred in eight A, one B and zero C proposals. A passing encoded count therefore does not establish that all earlier floating measurements passed or that encoding recovered lost detail. C returned empty edit settings on 202 captures, which preserves its developed candidate pixels rather than establishing identity with the common RAW reference. Its searches used 14–98 evaluations. See the [independent primary holdout audit](../out-quality-2026-09-29/primary-holdout-audit.md).

Encoding accounts for many C failures even without nonzero edits: all 108 JPEG-source empty-settings outputs passed the floating check, but only 55 passed JPEG re-encoding. Across all 202 empty-settings cases, 197 passed floating assessment and 138 passed encoded assessment. These counts measure the complete delivery representation; they cannot all be attributed to harmful optimizer adjustments. The separate [empty-settings audit](../out-quality-2026-09-29/primary-C-empty-settings-audit.json) retains the RAW/JPEG breakdown.

Foreground and dark-coverage diagnostics are available for all 532 images. Minimum eligible sampled highlight-patch retention is available for 417 and unavailable for 115 per candidate. Heuristic source-neutral WB-change vectors are available for 530 and unavailable for two; they do not establish calibrated neutral-color accuracy. The [compact results](../out-quality-2026-09-29/compact-results.json) retain channel distributions and denominators. Median dark-coverage changes are −0.0751 for A, −0.0410 for B and −0.0610 for C.

Use the timing qualifications below: A's per-row total excludes its precomputed frozen render, and its render timer includes worker communication. The table therefore reports observed renderer costs, not a controlled end-to-end speed comparison.

## Native holdout delivery

The guarded current/stub workflow completed **532/532 native holdout exports, zero failed**, with **203 remediations**. The accepted proposal strengths were 1.0 on 329 captures, 0.75 on 141, 0.5 on 55, and 0.25 on seven. No identity fallback was needed, and TIFF encoding required no additional blend. Remediation occurred on 79/345 RAW and 124/187 JPEG-source captures. Counts are deliveries after safeguards, not successful raw proposals.

The proposed native outputs had overlapping flags on 148 boundary-artifact, 93 texture, ten RGB-highlight, four luminance-highlight and four RGB-shadow measurements. Native and proxy detector outcomes differ, which reinforces the need to assess the delivered resolution. The independent integrity audit verified **532 RGB16 TIFF headers**, all output/sidecar/mask hashes, exact unique capture coverage, and **800 unchanged source, companion and camera-reference paths**. See the [native manifest](../out-quality-2026-09-29/holdout-native-current-tiff/run.json) and [integrity audit](../out-quality-2026-09-29/native-integrity.json). Independent rereading of all **532 exported outputs passed**, with zero dark-to-darker, highlight-clipping, shadow-clipping or other hard failures and zero execution/completeness errors. All 532 recomputed assessments exactly match their accepted-export assessments; four worker exit codes and report/CSV hashes passed independent review. See [native tone JSON](../out-quality-2026-09-29/tone-holdout-native-final.json), [CSV](../out-quality-2026-09-29/tone-holdout-native-final.csv), and [delivery audit](../out-quality-2026-09-29/native-holdout-delivery-audit.md). Thirty outputs have a lower whole-image mean, 94 have bottom-band median below 0.20, and four have dark coverage above 0.20; these descriptors are distinct from source-relative hard regressions.

The before/after integrity claim covers the 216 declared tuning paths and 800 declared holdout paths. Four unused tuning JPEG aliases lacked pre-run hashes and are explicitly excluded from that claim; they were not inputs to these evaluations. See [selection audit](../out-quality-2026-09-29/final-selection-audit.json) and [unused-alias provenance](../out-quality-2026-09-29/unused-tuning-jpeg-alias-provenance.json).

## Native known-snow evidence

The bounded four-case probe used the known tuning capture `0080_snow__3D6A3592.cr2` at 5796 × 3870. Original bytes remained unchanged. The original 700 × 552 crop was located and the current rerender's encoded crop reproduced the earlier native export exactly.

| Case | Snow encoded correlated amplitude | Snow luminance P95–P05, RGB8 codes | Final safety |
|---|---:|---:|---|
| Source | 1.000 | 16.74 | Reference |
| Current proposal | 0.257 | 5.57 | Failed clipping and boundary checks |
| Current, accepted 0.5 source blend | 0.567 | 11.14 | Passed |
| B global first | 0.564 | 11.44 | Passed |
| B bounded regions | 0.565 | 11.45 | Passed |

The current saved masks do not cover the crop. Its pixels remain unchanged through regional/compositional stages; loss appears at global finishing. Historical mask arrays were unavailable, so the causal claim is limited to the reproduced current trace despite exact prior-export crop agreement. B retains more detail than the raw current proposal, but is similar to the current safety blend and retains less than the source. This does not prove a regional snow fix or an aesthetic preference.

B's regional bound did execute elsewhere in the actual mountain mask, reducing a requested +0.2713 EV to zero before editing. Its empty coverage over the measured snow prevents attributing that crop's improvement to the regional bound. See the [independent snow report](../out-quality-2026-09-29/snow-evidence/README.md) and [native crop comparison including the accepted safeguard](../out-quality-2026-09-29/snow-evidence/native-crop-comparison-with-guard.png).

## Completed component comparisons

Both locked **24-capture, 29-arm** matrices completed. Tuning contains 696 measured rows; holdout contains 668 measured rows and 28 explicitly unavailable paired-JPEG slots. The independent audit verified all **1,364 outputs and 1,364 sidecars**, yielding 648 tuning and 620 holdout measured component/control pairs. Tuning representatives are all RAW; holdout representatives comprise 12 RAW and 12 JPEG sources. These samples are not additional independent captures beyond the primary corpora. See the [component audit](../out-quality-2026-09-29/component-audit.md) for every arm, format stratum, metric denominator and exact capture mapping.

| Component | Observed result | Decision implication |
|---|---|---|
| RAW preview lift and headroom cap | Uncapped preview introduced 13 tuning and six holdout RAW safety regressions versus B's zero-lift linear-sRGB control; capped preview introduced zero and one. LibRaw auto introduced ten and six, resolving one tuning failure. | Brightness matching is not a safe default. Retain explicit decode controls and RGB headroom limits. |
| Contrast and tone | Fixed contrast introduced two holdout failures; the current curve introduced three; removing tone mapping introduced two. Reinhard changed all exports but had no holdout safety-status gains and lowered median sampled minimum highlight retention by 0.119. | No universal tone-map winner. Preserve the measured alternatives. |
| SAM analysis and regional refinement | Analysis changed all candidate masks and 15 tuning/seven holdout scene labels, but no exported pixels. Regional refinement changed 11/24 tuning and 14/24 holdout exports; it resolved one holdout common-mask failure and introduced none. | Local refinement has a measured benefit in one sampled case. This does not establish a requirement for SAM on every photo or an aesthetic advantage. |
| Stub regions and order | Fixed stub regions changed eight tuning/12 holdout exports. Regional-first changed 14 per split versus global-first. Neither comparison changed safety status. Removing all current-pipeline concepts resolved four holdout failures but introduced one; tuning resolved two and introduced two. | Regional processing has mixed effects. No order replacement or universal segmentation benefit is established. |
| Classification, statistics and format policy | Scene-before/none changed no B global exports. Gamma-target statistics changed four tuning/seven holdout exports without safety transitions. Shared format policy changed two selected holdout JPEG exports, with no safety transitions; the same paired JPEGs were identical under both policies on 24 tuning/ten holdout pairs. | B's unused scene labels cannot prove classifier value. These tests do not justify a new default format policy. RAW/JPEG decode remains intentionally distinct. |
| Vignette, gradient and palette | Vignette changed all 24 exports per split without safety transitions. Sky gradient resolved two tuning failures but introduced one holdout failure. Palette introduced one failure per split. | Appearance changes require blind judgment; decorations are not automatic improvements. |
| Consistency | Three tuning and 17 holdout exports changed. One tuning failure was resolved and one previously-safe holdout image became unsafe. Holdout gained three dark-to-darker flags and one RGB-shadow flag, including changes on already-unsafe images. Other burst members supplied source medians only. | Per-image bias effects were measured; improved within-burst output consistency remains unproven. |

These are detector transitions under common fixed evaluation masks. Separate candidate-mask checks had no additional mask-only safety transitions in this experiment; their measurements were available for 23/24 tuning and 24/24 holdout SAM pairs. RAW-versus-camera-JPEG comparisons change both source bytes and the reference baseline; differing dimensions make their direct spatial pixel differences unavailable. Legacy composition coverage/confidence placeholders were not used to infer activation; independently decoded output pixels established it.

Three tuning and 11 holdout consistency rows contain a later capture's bias in `resolved_audit.options` because of mutable-dictionary aliasing. Their hash-verified per-image sidecars, dedicated consistency fields, settings and pixels retain the actual decisions. The discrepancy artifacts preserve the affected records; the post-freeze snapshot fix is covered by regressions and does not rewrite historical evidence.

## Delivery verification

- Complete matched tuning and holdout ablations, including actual SAM and paired-JPEG arms: **complete**; results above.
- Complete primary holdout proposal matrix: **complete, 1,596 measurements**; results above.
- Native holdout delivery, strict exported-pixel verification and output bit depths: **complete, 532 accepted**, zero hard failures. Tuning: **106 accepted**, zero hard failures.
- Final live suite: **438 passed, zero failed**; pytest collects all 438. Standard and quality stub smoke each produced two images, two sidecars and a run manifest. Verified resume preserved accepted artifacts. Exported smoke tone gate: two assessed, zero hard failures. See [final verification](../out-quality-2026-09-29/final-verification/summary.json).
- Separate blinded global-baseline and camera-reference galleries: **complete, 48 pairs each**. No human votes collected.
- Architecture decision: **retain the current default and experimental alternatives**; no candidate clears every proposal gate. Aesthetic superiority remains unproven without independent blind judgments.

## Interpretation and implementation limits

1. **C's watchdog is cooperative.** It checks elapsed time between evaluations, after source preparation, and does not preempt a running NumPy operation. The 30-second value bounds the intended search window, with possible overrun by a final evaluation; full decode, native rendering, encoding and assessment are outside it. Iteration/evaluation/proxy ceilings are fixed. Partial search is discarded on timeout, but a completed search can return edits on an idle machine and identity under contention, so outcome reproducibility is conditional on the same watchdog outcome. Here “identity” means empty global edit settings applied to C's developed input; for RAWs its `linear_srgb` development can still differ from the frozen `none` reference. Separately enabled regional, composition and palette stages still execute; an empty global setting dictionary is not a whole-workflow no-op. The registered primary C had those optional stages disabled. Count watchdogs and zero-setting results separately from unchanged-reference outputs. Do not describe this as a strict whole-image deadline or unconditional timing-independent determinism.
2. **Proxy feasibility is not native/export feasibility.** C constrains its 512-pixel trial images. Final native/export gates can still reject or blend the selected settings. C's Pareto settings/objective vectors are retained as hypotheses, not an architectural winner score.
3. **SAM comparisons need explicit controls.** Analysis-only B segmentation after global correction cannot alter the global settings already chosen. Its pixel equality to B global does not establish that segmentation is never useful. Regional correction must be compared to its matched analysis-only condition; mask resegmentation is a distinct factor from fixed-source masks. The real SAM subset and primary stub control cover different questions.
4. **Scene-order arms have limited reach.** B's global decision never consumes the classifier result. The registered scene-before/no-scene arms therefore cannot establish classifier benefit for a scene-driven editor. These are label/timing comparisons, with no output-causality claim for B global. The no-scene path also still computes post-edit classification before suppressing its sidecar label; it does not measure all classifier overhead removal. Palette behavior is the only optional rendering path that can use an early scene label.
5. **Intermediate checkpoints are diagnostic.** Shared source-to-stage assessments expose later loss for review, but no separate automatic gate compares final foreground recovery to an earlier recovered peak. Source-to-final darkness regression is enforced. A partially erased improvement that still exceeds the source is not a dedicated failure condition. The post-freeze diagnostic adds a continuous peak-to-final recovery-loss measurement and an explicit test of that case, retaining this separation from the hard gate.
6. **Statistics-space ablation is specific.** `stats_space` changes the global exposure-target median/ratio between linear and gamma representations. Other established percentiles, foreground thresholds and contrast-pivot statistics remain display-referred. Results must not be described as replacing every statistic with a linear-light counterpart.
7. **Artifact protection is sampled.** Clipping and finite values cover all pixels; global color/banding/foreground and boundary checks use deterministic samples, and texture examines bounded patches. Small unmasked detail losses and skin/color defects can escape these measures. A pass is a mechanical safety result, not a visual-quality certificate.
8. **Promotion remains conservative.** Benchmark summaries deliberately keep `promotion_eligible=false`; no automatic rule selects a winner from camera similarity or foreground brightness. Native source-blended delivery must be reported separately from raw proposal quality, with fallback counts and retained alternatives.
9. **Low-key and model scope are explicit gaps.** Prompt-driven preservation and synthetic low-key tests exist; a labeled real low-key corpus does not. Learned image-to-slider and cloud LLM paths were not established as controlled competitors. The cloud path remains disabled in unattended processing.

## Post-freeze audit corrections

Seven prepared patches were applied after all frozen render jobs finished; the neutral-title/tie gallery patch was applied while the separate native tone audit was running, and the other six only after that audit completed. At seven-patch integration, all 141 source/config/test hashes exactly matched the isolated rehearsal. Five representative current/B/C fixtures retained exact pixels and settings, and valid quality configurations retained assessment parity. The rehearsal passed 432 tests. See [integration provenance](../out-quality-2026-09-29/final-integration-provenance.json) and [rehearsal manifest](../out-quality-2026-09-29/deferred-options-snapshot/integration-manifest.json).

The corrections enforce complete tone-report selections; record the backend actually invoked; include TIFF/torchvision/OpenCV distributions in runtime identity; add a diagnostic for lost intermediate recovery; honor explicit experimental `--raw-mode none`; preserve fixed-mask safety separately; expose the existing shadow budget in configuration; reject malformed/nonfinite quality configuration; retain reasons for encoding-only remediation; support honest photo ties in galleries; and snapshot per-image workflow options to prevent later consistency biases overwriting earlier audit records. They do not change the frozen defaults, thresholds or evaluated render settings. Historical reports and snapshots remain unchanged. The explicit RAW option fix affects a previously ignored caller option, absent from the registered frozen experiments.

Final review additionally found that a setup failure during resume could replace accepted manifest records before revalidation. An eighth patch now preserves prior accepted records through setup failures, interrupted checkpoints, final source-audit failures and temporary verification errors. It always rechecks original hashes and refuses changed artifacts. Six new regressions brought the full suite to **438 passed**, both in the isolated actual no-pytest runner and in the final live tree; pytest also collected all 438. The render/decode/encode/assessment block is byte-identical. All 141 final source/config/test hashes match the tested eight-patch tree. See [resume-fix evidence](../out-quality-2026-09-29/deferred-resume-provenance/README.md), [final integration hashes](../out-quality-2026-09-29/final-eight-patch-integration.json), and [live verification](../out-quality-2026-09-29/final-verification/summary.json). A first export interrupted before its initial accepted provenance record still fails closed and needs a new destination; the correction does not invent missing provenance.

## Architecture decision matrix

| Candidate | Holdout measurements | Native safety and fallback frequency | Observed renderer median | Dependencies | Explainability / complexity | Decision |
|---|---|---|---|---|---|---|
| A frozen current/stub | 459/532 proposals pass; sampled retention 0.445 | Guarded current: 532 accepted, 203 remediations; independent tone audit passed | 1.691 s, worker/IPC scope | Core plus RAW decoder; TIFF16 optional | Existing rules and mask sidecars; safety wrapper adds explicit blending | Retain current default; raw proposals still have failures |
| B global first | 464/532 pass; RAW improves versus A while JPEG declines; sampled retention 0.714 | 48/48 native gallery-subset exports accepted, one remediation; full native promotion untested | 3.336 s, direct renderer | Core plus RAW decoder; SAM optional | Explicit bounded settings/stages; simpler global route but a separate renderer | Preserve experimental alternative |
| C optimized | 335/532 pass; many failures arise during JPEG re-encoding; sampled retention 0.608 | Full native promotion untested; zero watchdog fallbacks in 532 proxy searches | 13.267 s, direct renderer including search | B dependencies, no optimization library | Bounds/history/Pareto vectors; adds search and timing contingency | Preserve experimental alternative |

No candidate passes every hard gate or clearly dominates across format, safety, measured detail and cost. The five-photo aggregate advantage of B does not offset its JPEG stratum decline or establish native acceptance. Timing scopes differ, so these observations cannot prove an architectural speedup. Complexity is not a reason to override unequal measured outcomes. No aesthetic superiority is claimed.

The existing production default remains available. Any future default change needs completed independent holdout evidence, native exported acceptance, a compatibility/migration decision and explicit revision of the repository's single-pipeline-order invariant. Rollback selects `current` with legacy RAW mode; the frozen snapshot remains the historical pixel control. No evidence here authorizes an unreported default replacement.


## Reproduce delivery without overwriting evidence

Run from the repository root. The following commands use the existing export interpreter and TIFF configuration, preserve the exact source manifests, and deliberately write to **new** output directories. The workflow refuses a nonempty destination unless an explicitly requested resume verifies its configuration and artifacts. Neither command uses a resize or subset limit. These examples run the retained current/stub control with the safety wrapper; they do not promote B or C.

```sh
cd "$(git rev-parse --show-toplevel)"
PYTHONPATH=src out-quality-2026-09-29/export-runtime/bin/python \
  scripts/quality_workflow.py curated-dolomites-summer-2026/selected_raw \
  out-quality-2026-09-29/reproduction-new/tuning-current-tiff \
  --workflow current --backend stub --raw-mode none \
  --config out-quality-2026-09-29/native-tiff-config.json \
  --manifest out-quality-2026-09-29/tuning-sources.json --split tuning
PYTHONPATH=src out-quality-2026-09-29/export-runtime/bin/python \
  scripts/quality_workflow.py curated-switzerland-summer-2026/selected \
  out-quality-2026-09-29/reproduction-new/holdout-current-tiff \
  --workflow current --backend stub --raw-mode none \
  --config out-quality-2026-09-29/native-tiff-config.json \
  --manifest out-quality-2026-09-29/holdout-sources.json --split holdout
```

These invoke the installed working tree, including any later documented audit fixes. For an exact historical implementation, use the matching script under `out-quality-2026-09-29/frozen-final/scripts/` with `PYTHONPATH=out-quality-2026-09-29/frozen-final/src`. Preserve the native export interpreter: its `tifffile` installation is intentional and differs from the frozen proposal-benchmark runtime. The export package's TIFF8 fallback is not an acceptable substitute for verified TIFF16.

## Historical frozen benchmark verification

The [frozen-final snapshot](../out-quality-2026-09-29/frozen-final/PROVENANCE.json) retains the exact renderer, assessment, configuration and harness used for the locked experiments. The separate `baseline/` snapshot retains the pre-change A renderer. The proposal benchmark uses `runtime/bin/python`, **not** `export-runtime/bin/python`; dependency identity is part of its lock. Later working-tree changes must not be used to resume this historical run.

Once the respective four primary shards are complete, these commands verify their identities, source and artifact hashes and reuse measured rows through the frozen script's resume path. They refresh report metadata; do not run them against active workers. If a shard is incomplete, resume performs its remaining scheduled work rather than merely auditing it. Never refreeze this existing experiment or replace its lock.

```sh
cd "$(git rev-parse --show-toplevel)"
for quality_shard in 0 1 2 3; do
  PYTHONPATH=out-quality-2026-09-29/frozen-final/src \
    out-quality-2026-09-29/runtime/bin/python \
    out-quality-2026-09-29/frozen-final/scripts/quality_benchmark.py \
    tune out-quality-2026-09-29 --suite primary --max-px 1024 \
    --shard "$quality_shard/4" --resume
done
# Run the holdout verification only after all holdout primary shards finish.
for quality_shard in 0 1 2 3; do
  PYTHONPATH=out-quality-2026-09-29/frozen-final/src \
    out-quality-2026-09-29/runtime/bin/python \
    out-quality-2026-09-29/frozen-final/scripts/quality_benchmark.py \
    evaluate out-quality-2026-09-29 --suite primary --max-px 1024 \
    --shard "$quality_shard/4" --resume
done
```

The analogous ablation command uses `--suite ablations --shard 0/1`. Ablation resume may repeat source-median preparation for consistency controls; this is not a zero-cost file check. A fresh `merge` requires all shards and refuses to overwrite an existing merged report. For a new experiment or changed dependencies/configuration, follow [the workflow guide](QUALITY_WORKFLOW.md#frozen-experiments) with a new run directory and a new independent holdout when retuning.

## Blinded review delivery

The predetermined gallery set contains **48 unique holdout captures from 48 distinct burst groups**, with 36 RAW and 12 JPEG sources. Camera routes are 25 paired JPEGs, 12 source JPEGs and 11 validated embedded previews; all 11 preview extractions passed source-hash, finite-pixel, orientation and aspect checks. These checks do not establish pixel registration or aesthetic correctness. Selection is fixed before votes, with no substitutions based on outcomes. See [gallery preparation](../out-quality-2026-09-29/blind-gallery-preparation.md) and [selection audit](../out-quality-2026-09-29/blind-selection-audit.json).

Both galleries are complete: **48/48 pairs each, zero failed or excluded**. Native B supplied 48 accepted TIFF16 exports with one remediation. The helper verified source/output/sidecar hashes and native bit depths. The command below is the recorded preparation command; its existing destination is protected against overwrites. For another gallery, pass `--out` with a new empty directory.

```sh
cd "$(git rev-parse --show-toplevel)"
PYTHONPATH=src out-quality-2026-09-29/export-runtime/bin/python \
  out-quality-2026-09-29/prepare_blind_galleries.py build --publish
```

The resulting pages are `out-quality-2026-09-29/blind-galleries/current-vs-B/index.html` and `out-quality-2026-09-29/blind-galleries/current-vs-camera/index.html`. Each compares images with a matched 1600-pixel maximum long edge and common JPEG-quality-95 encoding (without upscaling) with concealed method labels and independently keyed side assignments. Current/B pairs have identical dimensions. Camera pairs differ by at most one pixel on the short edge on 36/48 pairs because of aspect rounding; no crop or registration was invented. All 192 JPEGs passed hash/format/metadata checks, and both pages were inspected in the browser without entering votes. See [gallery QA](../out-quality-2026-09-29/blind-galleries/qa.json). Open only the HTML when judging; the unlinked comparison/provenance manifests reveal identities. Download each gallery's human-produced `results.csv` into its own directory, then score separately:

```sh
PYTHONPATH=src out-quality-2026-09-29/runtime/bin/python scripts/blindtest.py \
  --score out-quality-2026-09-29/blind-galleries/current-vs-B/results.csv
PYTHONPATH=src out-quality-2026-09-29/runtime/bin/python scripts/blindtest.py \
  --score out-quality-2026-09-29/blind-galleries/current-vs-camera/results.csv
```

**No human votes or preference statistics exist yet.** The completed galleries are review artifacts, not evidence of aesthetic superiority. “No preference” records a tie, excluded from the exact binomial denominator and reported separately. Two-sided exact-test validation against 5,150 SciPy reference cases agreed to a maximum absolute difference of 1.34e−15. Camera appearance is a comparison condition, not a ground-truth quality target. Both galleries compare accepted deliveries, including any reported safeguards, rather than only successful raw proposals.

## Performance and reproducibility artifacts

The [performance supplement](../out-quality-2026-09-29/performance.md), [JSON](../out-quality-2026-09-29/performance.json) and [CSV](../out-quality-2026-09-29/performance.csv) group complete reports by split, suite, candidate and format, deduplicate merged reports against their shards, and expose missing observations. They include decode cache hit/miss timings, observed first candidate use per worker versus later use, encode/assessment/total medians and p95, process RSS high-water marks and search/watchdog counts. The artifact helper can refresh these summaries as more reports complete; an in-progress shard does not contribute to its completed population.

First use is an observational **process-cold, shared OS-cache, concurrent-load** measurement, not isolated model startup. Resume boundaries are unrecorded; engines can share a segmenter. B's apply interval includes deferred analysis, C's decision interval includes trials, and A's audit overhead differs. Do not sum internal stages or equate native safe-wrapper rendering (including remediation) with raw proposal rendering.

A's per-candidate total timer starts after its historical render has already supplied the shared masks, so that total omits the render cost. Its recorded renderer interval includes frozen-worker IPC and array-file transfers, while B/C's interval calls the engine directly. These asymmetric scopes prevent total-time or renderer-ratio claims of architectural speedup.

Hardware is an Apple M1 Max, 10 CPU cores and 64 GiB memory, documented in [device.json](../out-quality-2026-09-29/device.json). The [SAM cache inventory](../out-quality-2026-09-29/sam-cache-inventory.json) records offline `facebook/sam3` revision `3c879f39826c281e95690f02c7821c4de09afae7` and verified cached weights; no weights are committed. Supplemental [evaluation](../out-quality-2026-09-29/supplemental-runtime-evaluation-runtime.json) and [export](../out-quality-2026-09-29/supplemental-runtime-export-runtime.json) runtime inventories add previously omitted optional distribution versions without changing the historical lock. These are time-stamped observations, not proof of unrecorded earlier installation state.

## Migration, rollback and changed files

Adoption begins with the opt-in quality command and retained `current` workflow. Existing CLI/UI defaults and `Pipeline` order are preserved. Explicit `--workflow current --raw-mode none` restores the legacy candidate's decode/decision/rendering path while retaining the new export-safety wrapper; use `baseline/` for the untouched historical control. Preserve original sources and previous output directories. No production-default replacement or AGENTS.md edit was made. Proposed governance changes are in [Migration and rollback](QUALITY_WORKFLOW.md#migration-and-rollback); changing the production order requires an explicit corresponding revision of AGENTS.md invariant 1.

The review inventory below describes this task's implementation, not every pre-existing working-tree change. Post-freeze corrections above are separately identified from historical rendering evidence.

| Area | Files to review |
|---|---|
| Capture identity, RAW development and pixel safety | `src/autoedit/captures.py`, `quality.py`, `imageio.py`, `contract.py`, `tonemetrics.py`, `edit/gates.py`, `cli.py`; `configs/quality.toml`, `defaults.toml`, `rules.toml` |
| Candidate workflows, guarded delivery and experiments | `src/autoedit/workflows.py`, `quality_runner.py`, `experiments.py`, `pipeline.py`, `edit/apply.py`; `configs/workflows.toml` |
| Commands and blind evaluation | `scripts/quality_workflow.py`, `quality_benchmark.py`, `make_smoke_corpus.py`, `tone_report.py`, `blindtest.py` |
| Focused regression tests | `tests/test_captures.py`, `test_quality_pixels.py`, `test_quality_config_validation.py`, `test_raw_develop.py`, `test_workflows.py`, `test_quality_runner.py`, `test_experiments.py`, `test_tone_report.py`, `test_blindtest.py` |
| Documentation | `README.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, `PROJECT_ASSESSMENT.md`, `docs/QUALITY_WORKFLOW.md`, this report |

[git-commands.sh](../out-quality-2026-09-29/git-commands.sh) contains an exact 36-file staging list, one integrated feature commit and an optional publication command. **Those commands have not been executed.** They exclude the pre-existing `.claude/settings.json`, handoff material and unrelated image/evaluation directories. Generated photographs, caches, weights and runtime environments remain artifacts rather than source commits. The primary and native holdout results and the decision above come from completed, independently audited artifacts. Final live tests and both smoke paths passed as recorded above. [changed-files.json](../out-quality-2026-09-29/changed-files.json) lists the exact 36-file scope. Nothing was staged, committed, pushed or merged. The pre-existing settings change and untracked user files remain outside the change set.
