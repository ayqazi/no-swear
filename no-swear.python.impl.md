# No-Swear-NG — Python Implementation Specification

## Rationale

The Rust prototype (`no-swear`, using `ffmpeg-next` + `whisper-rs`) proved that direct libav bindings and raw BPE-token-level whisper access force reimplementation of mature, stable functionality already available at the CLI level and in Python libraries.

**Key problems with the Rust approach:**
- ffmpeg-next (libav bindings) requires manual demuxing, decoding, resampling, encoding, and muxing — thousands of lines of battle-tested C code that ffmpeg's own CLI already exposes perfectly.
- whisper-rs exposes raw BPE tokens, not decoded words. Recombining subword tokens into whole words for swear-word matching requires duplicating whisper.cpp's own detokenization logic.
- Word-level timestamps — a built-in feature of the Python `faster-whisper` library — require manual DTW alignment reimplementation in Rust.

**Python approach:**
- Use `ffmpeg` CLI via `subprocess` for all media operations. No C bindings, no library integration bugs.
- Use `faster-whisper` for transcription. It provides word-level timestamps (`word_timestamps=True`) out of the box, including BPE token recombination and CTranslate2-accelerated inference. 2–4x faster than openai-whisper.
- The tradeoff: intermediate audio is stored as uncompressed WAV files (~10 MB/minute for mono 16 kHz 16-bit). This is acceptable for typical media files (<2 hours). Lossless compression (e.g., FLAC) can be used to reduce disk usage if needed — decompress to raw PCM in memory before processing.

## Dependencies

| Dependency | Version | Purpose |
|------------|---------|---------|
| `faster-whisper` | latest | Speech-to-text with word-level timestamps and CTranslate2 acceleration. Handles BPE token recombination and model download/caching transparently. |
| `ctranslate2` | latest | Inference engine for Transformer models (faster-whisper dependency, included automatically). |
| `ffmpeg` (CLI) | system | Audio extraction, resampling, remuxing. Invoked via `subprocess`. |
| `numpy` | latest | PCM sample manipulation, noise generation. |
| (standard library) | — | `subprocess`, `argparse`, `tempfile`, `os`, `json` |

## Architecture

The pipeline has three phases with two intermediate WAV files:

```
input.mkv
   │
   ▼
Phase 1: Extract ───► 16 kHz mono WAV  ──► faster_whisper.transcribe(word_timestamps=True)
                        (for STT)                  │
                                                   ▼
                                           List of BleepPosition
                                           (word, start_sec, end_sec)
                                                   │
                                                   ▼
Phase 2: Generate ───► Processed WAV   ◄── numpy: copy input WAV samples,
                        (noise applied)     overwrite bleep ranges with brown noise
                                                   │
                                                   ▼
Phase 3: Remux ───► output.mkv
               ffmpeg -map 0 -map -0:a -map 1:a -c copy
```

## Detailed Behaviour

### Phase 1: Audio extraction and transcription

#### 1a. Extract audio for transcription

Run ffmpeg to extract the selected audio stream to a 16 kHz mono 16-bit PCM WAV file:

```
ffmpeg -y -i <input> -map 0:a:<audio_idx> -ac 1 -ar 16000 -sample_fmt s16 <stt_wav>
```

- If the stream index does not exist, ffmpeg will error. Catch this and show the available streams.
- If the stream exists but is not audio, ffmpeg will error (it cannot map a non-audio stream with `-map 0:a:N`). Show a clear error.

After extraction, load the WAV into memory via numpy/`soundfile` for noise generation. The WAV is transcribed via faster-whisper which reads files from disk.

#### 1b. Transcribe

```python
from faster_whisper import WhisperModel

model = WhisperModel(model_name, compute_type=precision)  # e.g. "tiny.en", "int8"
segments, info = model.transcribe(stt_wav, word_timestamps=True)
```

`segments` is a generator yielding segment objects, each containing `words`:

```python
for segment in segments:
    for word in segment.words:
        # word.word      — "fucking"
        # word.start     — 1.2 (seconds)
        # word.end       — 1.5 (seconds)
        # word.probability — 0.98
```

Each word is already fully decoded — no BPE token handling needed.

#### 1c. Match swear words

Iterate over all words across all segments. For each word:

1. Strip leading/trailing whitespace, normalize to lowercase.
2. Check if the word contains any swear word as a substring.
3. If matched, record a `BleepPosition` with the word's `start` and `end` timestamps in seconds.

The swear word list (hardcoded):

```
fuck, shit, damn, bitch, dick, cunt, bastard, asshole
```

"Partial match" means `normalized_word.contains(swear_word)`. Examples:
- "fucking" → "fuck" → match
- "dammit" → "damn" → match  
- "bitch" → "bitch" → match
- "bullshit" → "shit" → match
- "asshole" → "asshole" → match (but also matches "ass" and "hole" individually if those were in the list — acceptable)

### Phase 2: Brown noise generation

#### 2a. Load the original audio PCM

Read the original selected audio stream at its native sample rate and channel count. This is needed because the output audio must have the same sample rate and channel layout as the input.

Extract the full-resolution audio (not the 16 kHz mono version used for STT):

```
ffmpeg -y -i <input> -map 0:a:<audio_idx> -acodec pcm_s16le -f wav <full_audio_wav>
```

Load the WAV file into a numpy array. If the audio is multi-channel, load as a 2D array (samples × channels).

#### 2b. Generate brown noise replacement

For each `BleepPosition` (start_sec, end_sec):

1. Convert to sample indices: `i0 = int(start_sec * sample_rate)`, `i1 = int(end_sec * sample_rate)`.
2. Generate brown noise for the sample range [i0, i1) for each channel.

**Brown noise algorithm** (per channel):

```python
max_amp = 0.8 / 35.0  # ~0.02286
value = 0.0
for i in range(i0, i1):
    value += (random.random() - 0.5) * max_amp * 0.125
    value = max(-max_amp, min(max_amp, value))
    samples[i, ch] = value
```

This is a random walk (integrated white noise) clamped to `±max_amp`. The step size `max_amp * 0.125` controls the frequency roll-off — smaller steps produce more low-frequency content.

Each channel gets an independent noise stream.

#### 2c. Write processed WAV

Write the modified samples back to a WAV file at the original sample rate and channel count.

### Phase 3: Remuxing

Replace the selected audio stream in the original container with the processed WAV, copying all other streams verbatim:

```
ffmpeg -y \
  -i <input> \
  -i <processed_wav> \
  -map 0 -map -0:a:<audio_idx> -map 1:a \
  -c copy \
  -shortest \
  <output>
```

- `-map 0` — select all streams from the original file.
- `-map -0:a:<audio_idx>` — remove (subtract) the selected audio stream from the original.
- `-map 1:a` — add the processed audio from the second input (the WAV).
- `-c copy` — copy all remaining streams without re-encoding (video, subtitles, attachments, other audio tracks).
- `-shortest` — stop when the shortest stream ends (avoids padding if the WAV is slightly shorter than the original).

The output container format is inferred from the output filename extension, matching the input format. If the output format does not support the input's video codec, `-c copy` will fail — in that case, re-encode video with `libx264` as a fallback, or error with a message.

### Temporary files

All intermediate WAV files are created in a temporary directory (e.g., `tempfile.mkdtemp()`) and cleaned up on completion. The only persistent artifact is the cached model.

### Model management

`faster-whisper` manages model download and caching automatically via Hugging Face Hub. Models are cached in the standard Hugging Face Hub cache at `~/.cache/huggingface/hub/` in CTranslate2 format. This cache is shared by all HF tools.

The `--model` flag accepts any model name resolvable by faster-whisper (e.g., `"tiny.en"`, `"base"`, `"medium"`, `"large-v3"`, `"Systran/faster-whisper-large-v3"`). The `--precision` flag maps directly to the `compute_type` parameter: `"int8"`, `"int8_float16"`, `"float16"`, or `"float32"`.

Default model: `"tiny.en"` (~75 MB). Default precision: `"int8"`. With int8 precision, the model loads ~75 MB into RAM and transcribes at roughly 4–5x real-time on a modern CPU.

If model download fails, show an error with a descriptive message.

### Gotchas and edge cases

| Issue | Handling |
|-------|----------|
| Empty audio (no speech) | If no bleep positions are found, the processed WAV is identical to the original. Remux proceeds normally — output is a clean copy. |
| Bleep range at start/end of audio | Clamp sample indices to valid range `[0, len(samples))`. |
| Whisper word timestamp drift | Word timestamps from faster-whisper are post-hoc alignments and may be off by 100-300ms. This is acceptable for a bleeping tool — slight over- or under-bleeping is less noticeable than uncensored swears. |
| Hallucinated speech | Whisper may hallucinate words in silence. Use `vad_filter=True` or adjust `log_prob_threshold` / `no_speech_threshold` in `transcribe()` to suppress. |
| Multiple audio streams | Only the selected audio stream is replaced. Other audio streams pass through untouched. |
| Very long files (>2 hours) | The WAV for a 2-hour 48 kHz stereo 16-bit file is ~2.7 GB. This is manageable on modern systems. For longer files, consider processing in chunks. |
| Lossless compression | The intermediate WAV can be stored as FLAC to reduce disk usage: `ffmpeg -i <input> -map 0:a:<idx> -acodec flac <temp.flac>`, then decompress in memory with `soundfile.read()` or `ffmpeg -f flac -i <temp.flac> -f s16le -`. This is optional — uncompressed WAV is the default for simplicity. |
| Windows | Not supported. macOS and Linux only. |

## Invocation

```
uvx --from git+https://github.com/ayqazi/no-swear no-swear
```
