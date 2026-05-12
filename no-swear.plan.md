# No-Swear — Feature Roadmap

Features listed from easiest to hardest. Each feature is independent unless a dependency is noted.

---

### 1. `--words` flag

Allow users to specify a custom comma-separated word list instead of the hardcoded default.

```
--words fuck,shit,dammit
```

**Difficulty**: Trivial. Replace hardcoded constant with a `clap` flag, split on comma, trim whitespace.

---

### 2. `--bleep-volume` flag

Control white noise volume as a percentage (0–100). Default: 80.

```
--bleep-volume 50
```

**Difficulty**: Trivial. Multiply noise samples by `volume / 100.0`.

---

### 3. `--original-volume` flag

Control how much of the original audio bleeds through during bleeps (0.0–1.0). Default: 0.0.

```
--original-volume 0.15
```

When > 0.0, mix original audio samples with white noise: `output = noise * bleep_volume + original * original_volume`.

**Difficulty**: Trivial. Linear mix of two sample streams.

---

### 4. Progress bar

Show a progress indicator during the long transcription pass.

**Approach**: Add `indicatif` crate. Show a spinner during model download, a progress bar during Whisper transcription (based on audio duration processed), and a progress bar during the encode/mux pass (based on packet count).

**Difficulty**: Easy. UI-only change, no logic modification.

---

### 5. Subtitle-guided optimization

Use subtitle streams to narrow Whisper's transcription window, reducing processing time dramatically.

**Behaviour**:
1. Scan for SRT subtitle streams in the input
2. Parse entries, partial-match text against word list
3. Collect time windows `[start - 5s, end + 5s]` for matching entries
4. Merge overlapping windows
5. Only run Whisper on those windows (seek + decode small clips)
6. Offset returned timestamps by window start to get absolute positions
7. If no subtitle stream exists, fall back to full-audio Whisper pass

**Dependencies**: Add `nom` or manual SRT parser. New module `subtitle.rs`.

**Difficulty**: Medium. New parsing code, seek logic, window merging, fallback handling.

---

### 6. Model selection (`--model` flag)

Allow choosing any whisper.cpp GGML model.

```
--model base.en
--model large-v3
--model /path/to/custom-model.bin
```

**Behaviour**:
- If the value is a known model name (`tiny.en`, `base.en`, `small.en`, `medium.en`, `large-v3`), download from Hugging Face if not cached
- If the value is a path to an existing file, use it directly
- Cache path: `~/.cache/no-swear/<name>.bin`

**Difficulty**: Medium. URL mapping, download logic already exists for tiny.en — generalize it.

---

### 7. GPU acceleration

Enable whisper.cpp GPU backends (Metal on Apple Silicon, CUDA on NVIDIA).

**Behaviour**: Pass `GpuEnable::Auto` to whisper-rs context creation. whisper.cpp auto-detects the best available backend.

**Difficulty**: Easy. Single parameter change in whisper-rs initialization. No new dependencies.

---

### 8. `--copy-all-audio` flag

By default, only the censored audio track is included in the output. With this flag, all audio tracks are copied through (censored track re-encoded, others stream-copied).

```
--copy-all-audio
```

**Difficulty**: Easy. Conditional branch in muxing loop.

---

### 9. `--buffer` flag

Add extra silence/padding around each bleep position.

```
--buffer 0.5
```

Default: 0. Extends each `BleepPosition.start` backward and `BleepPosition.end` forward by the given seconds, clamped to `[0, duration]`.

**Difficulty**: Trivial. Arithmetic on timestamp values.

---

### 10. Match mode flags

Add `--exact` and `--fuzzy` match modes alongside the default partial match.

- `--exact`: Transcribed word must exactly equal the target word
- `--fuzzy`: Levenshtein distance ≤ N (add `--fuzzy-distance <N>`, default 1)
- Default (no flag): partial substring match

**Difficulty**: Easy. String comparison logic, Levenshtein implementation.

---

### 11. Audio-only file support

Currently assumes video container. Support raw audio files (MP3, WAV, FLAC, etc.).

**Behaviour**: Detect container type on open. If no video streams, skip video-related logic. Output is audio-only in the same format as input (or WAV if passthrough not possible).

**Difficulty**: Medium. Conditional branching on container type, different muxing setup.

---

### 12. Multiple audio track censoring

Allow censoring more than one audio track in a single run.

```
--audio 1 --audio 3
```

All specified tracks are decoded, transcribed, censored, and re-encoded. Non-specified audio tracks are dropped (or kept with `--copy-all-audio`).

**Difficulty**: Medium. `--audio` becomes a multi-value flag. Transcription + bleep logic runs per-track. Muxing handles N re-encoded audio streams.

---

### 13. Concurrent clip transcription

When subtitle-guided optimization is enabled (feature 5), transcribe multiple non-overlapping windows in parallel using a thread pool.

**Behaviour**: Use `rayon` or `std::thread`. Each window is independent. Collect results, sort by time, merge.

**Dependencies**: Requires feature 5 (subtitle optimization). Add `rayon` crate.

**Difficulty**: Medium. Thread safety with whisper-rs (each thread needs its own whisper context, or use a mutex around a shared context).

---

### 14. Configuration file

Support a `no-swear.toml` or `~/.config/no-swear/config.toml` for persistent defaults.

```toml
words = ["fuck", "shit", "damn"]
bleep_volume = 70
original_volume = 0.1
model = "base.en"
```

CLI flags override config file values.

**Difficulty**: Easy. Add `serde` + `toml` crates, load config, merge with CLI args.

---

### 15. Batch processing

Accept multiple input files and process them sequentially.

```
no-swear episode1.mkv episode1-clean.mkv --audio 1
no-swear episode2.mkv episode2-clean.mkv --audio 1
```

Or with a glob pattern:

```
no-swear "season1/*.mkv" ./output/ --audio 1
```

**Difficulty**: Medium. Loop over inputs, handle output path generation for directories.