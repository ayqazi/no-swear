# Fixing the Agent's Transcribe Implementation

## Overview

The agent implemented a `transcribe()` function per spec but introduced three correctness bugs — two of which were silent data corruption — plus several readability issues. This log documents what was wrong, what was found during investigation, and how each was fixed.

---

## Issue 1: Resampler flush crash (blocking, would always fail on >=6ch audio)

### The bug

The agent's code flushed the decoder by sending an empty packet, then called `flush_resampler()` which looped calling `resampler.flush(&mut dst)` with `dst = Audio::empty()` (0-sample capacity). The underlying FFI call `swr_convert_frame(ctx, output, NULL)` with `output->nb_samples == 0` produced an error, because swr has no output buffer to write into.

**Why the agent didn't notice:** The agent apparently never ran the code on multi-channel audio. With mono source audio the internal resampler delay might be 0, so `flush()` never actually calls `swr_convert_frame` with data — but with 5.1 E-AC3 (the test file) there are always residual samples in the resampler's polyphase filter bank.

### Discovery path

1. Added `eprintln!("DEBUG: flushing resampler...")` just before the flush loop
2. Output showed `DEBUG: flushing resampler...` followed immediately by `ffmpeg::Error(1668179714: Output changed)`
3. Read ffmpeg-next 8.1.0 source for `resampler::Context::flush()` and `run()` side-by-side:
   - `run()` calls `if output.is_empty() { output.alloc(format, input.samples(), layout); }` before calling `swr_convert_frame`
   - `flush()` just sets `sample_rate` then calls `swr_convert_frame` — no allocation

### Fix

Allocate the output frame before passing it to `flush()`:
```rust
let mut dst = ffmpeg::frame::Audio::empty();
unsafe { dst.alloc(dst_format, 4096, dst_channel_layout); }
let delay = resampler.flush(&mut dst)?;
```

This gives swr a 4096-sample output buffer, plenty for any internal delay (typically <200 samples for 48→16 kHz resampling).

---

## Issue 2: Exhausted packet iterator (blocking, always fails)

### The bug

`transcribe()` takes `&mut ictx` and iterates `ictx.packets()` to completion (reading every demuxed packet). Then `passthrough(ictx, &args.output)` takes ownership of `ictx` and calls `ictx.packets()` again — but the AVFormatContext's internal IO position is at EOF. FFmpeg's `av_read_frame()` returns `AVERROR_EOF`, the muxer never receives any packets, and the trailer fails with "Output changed" because no data was written for the streams declared in the header.

### Discovery path

1. Initial run with `--verbose` showed `Using model: ...` then `Error: ffmpeg::Error(...)` — no "CENSORED" output
2. Added `eprintln!("transcribe OK, seeking...")` after `transcribe()` call — never printed
3. Added DEBUG eprintln inside `transcribe()` — they printed, meaning the error came from transcribe itself
4. Added DEBUG output inside the packet loop — all 2344 packets decoded fine
5. The error only appeared on `resampler.flush()` — wait, that was Issue 1

> Actually, both issues were present simultaneously. Fixing the flush revealed the second bug: after transcribe succeeded, passthrough failed with the same error code.

### Fix

Seek back to the beginning of the file before the second pass:
```rust
ictx.seek(0, ..0)?;
passthrough(ictx, &args.output)?;
```

This calls `av_seek_frame(ictx, -1, 0, AVSEEK_FLAG_BACKWARD)`, rewinding the format context so `packets()` starts fresh.

---

## Issue 3: `Greedy { best_of: 0 }` silently degrades accuracy

### The bug

The agent used `SamplingStrategy::Greedy { best_of: 0 }`. whisper.cpp's `best_of` parameter controls how many candidate tokens are evaluated at each step. The docs confirm:

> *"Defaults to 5 in whisper.cpp. Will be clamped to at least 1."*

Setting `best_of: 0` forces the clamp to 1, which is standard greedy (no candidate search). This reduces transcription accuracy for no benefit.

### Fix

```rust
SamplingStrategy::Greedy { best_of: 5 }
```

---

## Issue 4: Code organization and readability

### Problems in the original agent code

1. **Monolithic `transcribe()`** — Mixed audio extraction (decoder + resampler), whisper inference, and swear-word matching in one 90-line function. These are three distinct stages with different error domains.
2. **`let mut params = params;` shadow** — `FullParams::new()` returns an owned value. The agent created `let params = ...` then immediately shadowed with `let mut params = params;`. Just use `let mut params = ...` directly.
3. **Repeated inline resample+extract pattern** — The same 7-line slice-reinterpretation block appeared in the main packet loop, the decoder drain loop, and (in the agent's original) was absent for the flush loop entirely.

### Fix

- **Split into `extract_audio()` and `transcribe()`** — The former returns `Vec<f32>`, the latter takes `&[f32]` and returns `Vec<CensoringPosition>`. Clean separation of concerns.
- **`let mut params` directly** — Removed the unnecessary shadow.
- **Inline the resample pattern** — The 7-line pattern is too short to factor into a helper (per the project's "NEVER factor out short, simple code" rule). Just write it inline three times.

---

## Issue 5: Diagnostic logging was absent / all-or-nothing

### Problem

The agent added `--verbose` but only used it for the final "CENSORED" lines. When things went wrong, there was zero visibility into where.

### Fix

All internal stages have permanent `if verbose { eprintln!(...) }` guards:

- Audio format detection (`format={:?} rate={} layout={:?}`)
- Resampler creation (`N channels -> mono, M Hz -> 16 kHz`)
- Packet count and decoded sample count
- Whisper transcription start/completion with segment count
- Seek-back step before passthrough

---

## Verification

```
$ cargo run -- data/swearing-clip.mkv out.mkv --audio 1 --verbose
...
Transcription complete, 39 segments
Found 3 swear word occurrences to censor
CENSORED bitch 15720:17320
CENSORED fuck 64640:65720
CENSORED shit 65720:67240
Seeking input back to start for passthrough
Copied all streams from ...
```

Both `--verbose` and non-verbose modes produce correct output. The output file is a valid Matroska with all original streams preserved.
