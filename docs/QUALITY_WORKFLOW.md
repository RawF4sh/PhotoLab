# Autonomous quality workflow

The quality workflow adds source selection, measured safety, export verification and
reproducible experiments around the existing editor. It runs locally and never asks
for an aesthetic decision during processing. The existing `Pipeline` remains the
production control; experimental global-first and optimization paths are isolated.

## Process photographs

```sh
PYTHONPATH=src python3 scripts/quality_workflow.py /path/to/photos /path/to/exports \
  --backend sam3 --device mps --workflow current
```

Use `--backend stub` for the lightweight installation. The workflow recursively
discovers supported RAW and raster formats (including JPEG, PNG and TIFF), selects
one source per capture, prefers RAW, and ignores XMP. Local curation manifests
recover capture identities when copied RAW/JPEG names
have different rank prefixes. Original paths in those manifests are identifiers;
the workflow reads the local copies, not the original Desktop photographs. Exact
duplicates within an event directory coalesce. Renamed RAW/JPEG pairs without
curation require unambiguous precise capture metadata; ambiguous whole-second
timestamps cannot merge burst frames.

Outputs must be outside the input tree. A nonempty output directory is refused
unless `--resume` validates the same selection/configuration and existing artifact
hashes. Accepted records survive resume setup failures, interruptions and temporary verification errors; retry rechecks their original hashes. Corrupt inputs fail individually. Missing RAW decoding support is an image
failure; missing SAM support is an explicitly recorded no-mask fallback. SAM is
loaded from the local cache in this command. The LLM path is not permitted.

An interruption after writing an image but before its sidecar or accepted manifest
record can leave a partial export. Resume refuses that unverified file; it does not
silently delete or overwrite it. Keep the partial directory for inspection and run
the same selection into a new, empty output destination. An image without complete
accepted provenance is not an accepted delivery.

Each accepted capture has a JPEG or TIFF, sidecar, and optional mask artifact. The
run also contains `run.json`, `quality.json` and `quality.csv`. Source bytes are
hashed before and after processing. Sidecars record proposed settings, accepted
source-blend factors, decode provenance, pipeline timings, and exported-pixel
assessment. Rejected proposals can appear under `alternatives/`; they are diagnostic
artifacts, not accepted edits. A source-preserving fallback is a reported outcome,
not evidence that the proposed editor succeeded.

Lossy JPEG encoding can exceed the strict clipping budget even for an unchanged
RAW decode. Such an export fails explicitly. For preservation-oriented output,
install the optional `tifffile` package and pass a configuration containing
`{"run_config": {"out_format": "tiff"}}` to request 16-bit TIFF. The legacy I/O
fallback is 8-bit when `tifffile` is unavailable; verify actual bit depth when a
16-bit deliverable is required. The implementation's native acceptance protocol
uses verified 16-bit TIFF, while JPEG proposal reports retain encoding failures.

Exit 0 means every selected output was accepted and source integrity passed. Exit 1
means per-image or final integrity failures. Exit 2 means invalid configuration or
run setup. `--limit` and `--max-px` are diagnostic options and cannot establish full
native-resolution corpus acceptance.

## Measurements and their limits

`configs/quality.toml` is the shared threshold source. Reports include global and
regional luminance, foreground/band percentiles, headroom, dark coverage, newly
clipped and net clipped RGB/luminance pixels, source-correlated encoded highlight
detail, saturation/color change, banding, and mask-boundary artifacts. Invalid
pixels and clipping are scanned across all pixels in bounded-memory chunks.
Global statistics use a deterministic spatial sample; texture uses native patches
including masked and high-detail highlight areas. Artifact checks are not an
exhaustive visual-quality certificate. Unavailable regions or color references
remain explicitly unavailable.

Configuration validation rejects NaN/Inf, nonnumeric safety
thresholds, invalid counts/ranges, and unknown keys before assessment. It covers
both TOML overrides and direct API overrides while preserving valid defaults and
the explicit `preserve_exposure` boolean. The frozen evaluated defaults are finite and unchanged; this post-freeze correction prevents malformed custom configuration from disabling checks. Its isolated validation and parity evidence is retained under `out-quality-2026-09-29/deferred-quality-config-validation/`.

The texture detector uses the shortest pixel lag with usable source contrast in
RGB8 luminance. This avoids false losses caused by a harmless quantization-phase
shift. Reported lag and availability matter: coarse retained amplitude does not
establish preservation of every finer spatial frequency. Calibration covers clean
and deliberately damaged synthetic probes, not every real scene.

```sh
PYTHONPATH=src python3 scripts/tone_report.py /path/to/photos --out /path/to/exports \
  --fail-on-regression --csv /path/to/tone.csv --json /path/to/tone.json
```

The report checks recorded hashes, output/sidecar completeness and actual exported
pixels. Legacy sidecars without source hashes cannot prove original integrity and
fail strict verification. `--render` uses the safe wrapper in memory; use
`--unsafe-control` to diagnose the original unremediated pipeline. In-memory results
are proxy evidence, not exported/native evidence. The report's stricter RGB and
shadow failures intentionally replace the older luminance-only success definition.

## Competing workflows

| Workflow | Behavior |
| --- | --- |
| `current` | Existing region-first pipeline, followed by explicit safety acceptance. |
| `global-first` | Experimental development, bounded global corrections, optional regional refinement. |
| `optimized` | Experimental bounded local search using the same photographic renderer and safety constraints. |

The default remains `current`; adding alternatives is not a claim that they are
better photographs. `configs/workflows.toml` defines candidate parameters,
operation variants and resource limits. A JSON/TOML `--config` may provide
`run_config` and `options` blocks. `apply_ops({})` remains exact identity. Empty global settings do not disable separately configured regional, composition or palette stages.

RAW development retains `none`, `libraw_auto`, `preview_ev`,
`preview_ev_headroom`, and `tone_target`, adding `linear_srgb` as the zero-lift
transfer-function control. JPEG/TIFF decoding is unchanged. Preview modes default
to zero lift without a compatible preview; there is no Canon-derived universal EV
fallback. Preview similarity is diagnostic, not a quality objective or ground truth.

The optimizer uses at most a 512-pixel proxy, six passes and 144 evaluations. Its
time watchdog discards a partial solution and returns predetermined empty global settings, with the event recorded. Those settings operate on the candidate-developed input; enabled regional, composition and palette stages still run, followed by the common safety wrapper. Parameter bounds and objective weights are
experimental, configurable, and part of each run's provenance. Different numerical
tradeoffs remain available for later blind review.

The watchdog checks between evaluations; it does not interrupt a NumPy operation
or impose a deadline on decoding and native export. A slow final evaluation can
overrun the search window. The search order is deterministic, but machine load can
change whether it finishes or returns the recorded empty-settings fallback.

## Frozen experiments

The implementation run is `out-quality-2026-09-29/`. It contains the original
source/config snapshot, baseline evidence, source manifests and evaluation protocol.
Dolomites supplies 106 unique tuning captures; Switzerland supplies 532 holdout
captures (345 CR2, 187 JPEG-only). All paired members and bursts stay together.
Both collections use one camera. Sparse warm scenes, missing confirmed low-key
labels and limited event diversity constrain generalization. The final Desktop
204-photo set is excluded.

```sh
QUALITY_RUN=/path/to/new-run
PYTHONPATH=src python3 scripts/quality_benchmark.py prepare "$QUALITY_RUN" \
  --tuning /path/to/tuning --holdout /path/to/new-independent-holdout
PYTHONPATH=src python3 scripts/quality_benchmark.py freeze "$QUALITY_RUN"
PYTHONPATH=src python3 scripts/quality_benchmark.py tune "$QUALITY_RUN"
PYTHONPATH=src python3 scripts/quality_benchmark.py tune "$QUALITY_RUN" --suite ablations
PYTHONPATH=src python3 scripts/quality_benchmark.py evaluate "$QUALITY_RUN"
PYTHONPATH=src python3 scripts/quality_benchmark.py evaluate "$QUALITY_RUN" --suite ablations
```

Freeze is one-time. Evaluation refuses changed source partitions or frozen code and
configurations. A completed experiment can contain unsafe proposals: these are
measured failures, not production exports. Do not promote a candidate from proxy
metrics, camera similarity, or a single aggregate brightness score. Native export
safety and separate scene/format results are required. Holdout-driven retuning
requires a new holdout; it cannot reuse the same data as independent evidence.

For parallel evaluation, pass `--shard 0/4` through `--shard 3/4`, then run
`quality_benchmark.py merge RUN --split tuning --shard-count 4` (also pass
`--suite ablations` when appropriate). Merging verifies the entire expected capture
and candidate grid, source integrity, and output hashes. Use `--resume` only with
the exact same protocol. A new experiment snapshots the currently installed
control; the implementation run preserves the original pre-change control.

Component comparisons include decode modes, transfer functions, statistics space,
scene timing, format policies, rules/search, segmentation, regional order, contrast,
tone mapping, composition, palette and consistency. No masks and stub masks are
different conditions. Model or runtime failures remain visible and cannot count as
successful SAM evidence. A learned model without independent training provenance
is explicitly untested. Cloud LLM evaluation is out of scope.

Timing fields describe observed implementation costs, including instrumentation.
Some stage intervals overlap: B's application interval includes analysis deferred
until after global correction, and C's decision interval includes trial renders and
safety assessment. Do not add these intervals to estimate elapsed time. A's row total excludes its historical render, already computed during shared-mask preparation, and its render timer includes worker IPC. Whole-row ratios therefore do not compare end-to-end throughput. First use is observed under shared OS caches and concurrent load; it does not isolate model startup. Decode/cache, encoding, assessment and process high-water RSS are reported separately where available. Internal audit costs also differ, so renderer timing alone does not isolate algorithmic complexity.

## Later blind review

Prepare two separate two-arm galleries from the comparison manifest:

```sh
PYTHONPATH=src python3 scripts/blindtest.py --comparison-manifest comparisons.json \
  gallery-global --methods baseline,autoedit --split holdout
PYTHONPATH=src python3 scripts/blindtest.py --comparison-manifest comparisons.json \
  gallery-camera --methods camera,autoedit --split holdout
PYTHONPATH=src python3 scripts/blindtest.py --score gallery-global/results.csv
PYTHONPATH=src python3 scripts/blindtest.py --score gallery-camera/results.csv
```

The comparison manifest maps opaque capture IDs to source, scene, burst, split,
category and `methods` paths. Method identities and filenames stay out of the
presentation. Separate RNG domains randomize slots in each comparison. Missing
camera renders are exclusions, never substituted original pixels. Scoring validates
gallery IDs and rejects duplicate/invalid votes; exact binomial results, ties and
sample-size warnings accompany raw win rates. The “No preference” button (key 0) records a photo tie; ties are counted and excluded from the exact two-sided binomial denominator. Group summaries retain tied groups and group by split plus capture group, independently of scene labels. Burst independence remains an
assumption, and results from two events do not prove cross-event generalization.

## Migration and rollback

Existing CLI/UI defaults and pipeline order remain available. Use the new command
to opt into acceptance and provenance, then explicitly select an experimental
workflow for comparisons. Switching `--workflow current --raw-mode none` restores
the original decision/rendering candidate with the new safety wrapper; the frozen
baseline snapshot reproduces historical control pixels for experiments.

Promoting a different orchestration requires a documented holdout decision,
native-resolution acceptance and a compatibility review. Before replacing the
production pipeline, revise AGENTS.md invariant 1 to name the selected order and
preserve separate stages. No instruction change is required merely to retain these
isolated experiments. Originals and previous output directories remain untouched.

Proposed additions to AGENTS.md, if this workflow is adopted:

> Accepted quality-workflow exports must pass the shared assessment against a common
> reference decode and decoded export pixels, with source hashes and complete artifacts.
> Keep experimental orchestration separate from the production pipeline. Promotion
> requires frozen holdout evidence, native exports, and explicit treatment of fallback
> frequency and unresolved visual tradeoffs.

These are proposed additions only; this implementation does not edit AGENTS.md or
replace its pipeline-order invariant.

## Technical references

- [rawpy postprocessing parameters](https://letmaik.github.io/rawpy/api/rawpy.Params.html)
  identify the legacy default `(2.222, 4.5)` transfer curve as BT.709. The
  `linear_srgb` control separates transfer encoding from exposure lift.
- [Photographic Tone Reproduction for Digital Images](https://www-old.cs.utah.edu/docs/techreports/2002/pdf/UUCS-02-001.pdf)
  supplies the global white-point mapping used by the experimental Reinhard arm;
  this implementation does not claim the paper's local adaptation operator.
- [SciPy's exact binomial test](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html)
  is the independent reference for the two-sided blind-vote test. SciPy is not a
  runtime dependency of the gallery scorer.
