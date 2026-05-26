# No-Swear-NG — Python Implementation Specification

## Summary

- Use `ffmpeg-python` for all media operations. No C bindings, no library integration bugs.
- Use `faster-whisper` for transcription. It provides word-level timestamps (`word_timestamps=True`) out of the box, including BPE token recombination and CTranslate2-accelerated inference. 2–4x faster than openai-whisper.

## Dependencies

| Dependency | Version |
|------------|---------|
| `faster-whisper` | latest |
| `ffmpeg-python` | latest |
| `numpy` | latest |
| `soundfile` | latest |

## Architecture

The pipeline has three phases with one intermediate audio file:

Phase 1: Extract selected audio → faster-whisper transcribe → list of BleepPositions (word, start_sec, end_sec).
Phase 2: Load extracted audio, overwrite bleep ranges with brown noise → processed audio.
Phase 3: Remux original container replacing the selected audio stream with processed audio, copy all other streams.

## Detailed Behaviour

### Phase 1: Audio extraction and transcription

#### 1a. Extract audio

Extract the selected audio stream to a separate file with `c='copy'` preserving original format.

#### 1c. Match swear words

Iterate over all words across all segments. For each word:

1. Strip leading/trailing whitespace, normalize to lowercase.
2. Check if the word contains any swear word as a substring.
3. If matched, record a `BleepPosition` with the word's `start` and `end` timestamps in seconds.

The swear word list is provided by the `--words` flag as a comma-separated string. All whitespace is stripped from each word before use.

"Partial match" means `normalized_word.contains(swear_word)`. Examples:
- "we'll" → "we" → match
- "woman" → "man" → match
- "right" → "right" → match

### Phase 2: Brown noise generation

#### 2a. Load audio into memory

Load the extracted audio file with `soundfile` into a numpy array. If the audio is multi-channel, load as a 2D array (samples × channels).

#### 2b. Generate brown noise replacement

For each `BleepPosition` (start_sec, end_sec):

1. Convert to sample indices: `i0 = int(start_sec * sample_rate)`, `i1 = int(end_sec * sample_rate)`.
2. Generate brown noise for the sample range [i0, i1) for each channel.

**Brown noise algorithm** (per channel): Use a random walk with step size `max_amp * 0.125` where `max_amp = 0.8 / 35.0` (~0.02286). Clamp values to `±max_amp`. Each channel gets an independent noise stream.

#### 2c. Write processed audio

Write the modified samples back to an audio file at the original sample rate and channel.

### Phase 3: Remuxing

Replace the selected audio stream in the original container with the processed audio, copying all other streams verbatim with settings.

The output container format is inferred by ffmpeg from the output file extension.

### Working directory

All intermediate files are created in a working directory created using `tempfile.mkdtemp()`. The working directory path is output to STDERR. Working directory is created and output after all error checking and immediately before work is ready to begin.

### Logs

Logs are stored in the `logs/` subdirectory of the working directory. The main process and each subprocess have separate log files.

Main log contains forensic analysis info containing:
- Full command-line parameters and configuration
- Timing information, and relative log filename, for each pipeline stage (extraction, transcription, noise generation, remuxing)
- List of all censored words found with start/end timestamps
- Final results summary

Each sub-process that is spawned must have its own log file.

All whisper output MUST go to a separate log file.

### Model management

`faster-whisper` manages model download and caching automatically via Hugging Face Hub. Models are cached in the standard Hugging Face Hub cache at `~/.cache/huggingface/hub/` in CTranslate2 format. This cache is shared by all HF tools.

The `--model` flag accepts any model name resolvable by faster-whisper (e.g., `"tiny.en"`, `"base"`, `"medium"`, `"large-v3"`, `"Systran/faster-whisper-large-v3"`). The `--precision` flag maps directly to the `compute_type` parameter: `"int8"`, `"int8_float16"`, `"float16"`, or `"float32"`.

Default model: `"tiny.en"` (~75 MB). Default precision: `"int8"`. With int8 precision, the model loads ~75 MB into RAM and transcribes at roughly 4–5x real-time on a modern CPU.

If model download fails, show an error with a descriptive message.

### Gotchas and edge cases

| Issue | Handling |
|-------|----------|
| Empty audio (no speech) | If no bleep positions are found, the processed audio is identical to the original. Remux proceeds normally — output is a clean copy. |
| Bleep range at start/end of audio | Clamp sample indices to valid range `[0, len(samples))`. |
| Hallucinated speech | Whisper may hallucinate words in silence. Use `vad_filter=True` or adjust `log_prob_threshold` / `no_speech_threshold` in `transcribe()` to suppress. |
| Multiple audio streams | Only the selected audio stream is replaced. Other audio streams pass through untouched. |

## Invocation

```
uv run no-swear input.mkv output.mkv --words cat,dog --audio 1
```
