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

**Difficulty**: Medium. New parsing code, seek logic, window merging, fallback handling.

---

### 7. GPU acceleration

Enable GPU backends (Metal on Apple Silicon, CUDA on NVIDIA).

**Difficulty**: Unknown.

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

### 12. Multiple audio track censoring

Allow censoring more than one audio track in a single run.

```
--audio 1 --audio 3
```

All specified tracks are decoded, transcribed, censored, and re-encoded.

**Difficulty**: Medium.

---

### 13. Concurrent clip transcription

When subtitle-guided optimization is enabled (feature 5), transcribe multiple non-overlapping windows in parallel using a thread pool.

**Dependencies**: Requires feature 5 (subtitle optimization).

**Difficulty**: Medium. Thread safety required.
