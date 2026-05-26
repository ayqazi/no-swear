# No-Swear-NG — MVP Specification

## Overview

A CLI tool that censors profanity in any media file (video or audio) by replacing matching words with brown noise. Uses ffmpeg for all media operations and a speech-to-text engine for transcription.

## CLI Interface

```
no-swear input.mkv output.mkv --audio 1
```

### Positional arguments

| Position | Name | Description |
|----------|------|-------------|
| 1 | `input` | Path to input media file (any format ffmpeg can demux) |
| 2 | `output` | Path to output media file |

### Flags

| Flag | Required | Description |
|------|----------|-------------|
| `--audio <N>` | Yes | Audio stream index to censor (0-based). User is trusted to pick an English track. |
| `--model <NAME>` | No | Speech-to-text model to use (default: `tiny.en`). The application manages download and caching transparently. |
| `--precision <TYPE>` | No | Model precision at load time (default: `int8`). Supported values depend on the speech-to-text engine. |
| `--words <LIST>` | Yes | Comma-separated list of words to censor. Example: `--words cat,dog,fish` |

### Error conditions

- `input` does not exist → error with message
- `--audio` stream index does not exist → error
- `--audio` stream exists but is not audio (e.g., video) → error
- Output file cannot be written → error
- Speech-to-text model cannot be loaded or downloaded → error with message
- Output cannot be encoded (no suitable audio encoder available) → error with message

## Behaviour

### 1. Argument parsing

Parse two positional args (`input`, `output`) and flags (`--audio`, `--model`, `--precision`, `--words`). Validate all error conditions before proceeding.

### 2. Audio extraction

Extract the selected audio stream to a separate file.

### 3. Model loading

Load the configured speech-to-text model. The application manages download and caching transparently. If the model cannot be acquired, error with a message.

### 4. Transcription

Transcribe the extracted audio with the speech-to-text engine, requesting word-level timestamps.

Collect all word-level timestamp segments where the transcribed text partially matches any word in the word list provided by `--words`. Example: `--words cat,dog,fish`.

"Partial match" means the transcribed word text contains the target word as a substring (case-insensitive). For example, "woman" matches "man", "we'll" matches "we".

Each matched segment yields a `BleepPosition` with:
- `start_time` (seconds, from speech-to-text timestamp)
- `end_time` (seconds, from speech-to-text timestamp)

### 5. Noise generation

Create a replacement audio file that is identical to the original selected audio stream except that the time ranges matching each `BleepPosition` are overwritten with brown noise.

**Brown noise application**: For each `BleepPosition`, replace the audio samples in that time range with brown noise (random walk / integrated white noise) at an amplitude that is barely audible under normal speech. Samples outside bleep ranges pass through unmodified. Brown noise is generated per-channel (independent noise for each channel) if the audio has multiple channels.

### 6. Output assembly

Assemble the output file using the processed audio and all original non-selected streams (video, subtitles, attachments, other audio tracks). All non-selected streams pass through without re-encoding. The output container format matches the input. If the output cannot be assembled because no suitable audio encoder is available, error with a message.
