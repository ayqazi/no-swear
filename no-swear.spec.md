# No-Swear — MVP Specification

## Overview

A CLI tool that censors profanity in any media file (video or audio) by replacing matching words with white noise. Uses libav (via `ffmpeg-next`) for all media operations and whisper.cpp (via `whisper-rs`) for speech-to-text.

## CLI Interface

```
no-swear input.mkv output.mkv --audio 1
```

### Positional arguments

| Position | Name | Description |
|----------|------|-------------|
| 1 | `input` | Path to input media file (any format libav can demux) |
| 2 | `output` | Path to output media file |

### Flags

| Flag | Required | Description |
|------|----------|-------------|
| `--audio <N>` | Yes | Audio stream index to censor (0-based). Passed directly to libav stream selection. User is trusted to pick an English track. |
| `--model-name <NAME>` | No | Model filename to use from the Hugging Face repo (default: `ggml-tiny.en-q5_1.bin`) |
| `--model-repo <REPO>` | No | Hugging Face repo to download the model from (default: `ggerganov/whisper.cpp`) |
| `--verbose` | No | Increase output verbosity |

### Error conditions

- `input` does not exist → error with message
- `--audio` stream index does not exist → error listing available streams
- `--audio` stream exists but is not audio (e.g., video) → error
- Output file cannot be written → error

## Dependencies (Cargo.toml)

| Crate | Version | Purpose |
|-------|---------|---------|
| `clap` | latest | CLI argument parsing (positional + flags) |
| `ffmpeg-next` | latest | libav bindings: demux, decode, encode, resample, mux, stream copy |
| `whisper-rs` | latest | whisper.cpp bindings: load GGML model, transcribe PCM audio |
| `rand` | latest | Generate white noise samples |
| `reqwest` | latest | HTTP client for model download (blocking) |

## Behaviour

### 1. Argument parsing

Parse two positional args (`input`, `output`) and one flag (`--audio`). Validate all error conditions before proceeding.

### 2. Open input

Open `input` via `ffmpeg-next`. Identify all streams:
- The selected audio stream (index from `--audio`)
- All other streams (video, subtitles, other audio tracks)

### 3. Model loading

Load `whisper-rs` with a GGML format model. The model name and Hugging Face repo are configurable via `--model-name` and `--model-repo` flags (defaults listed above).

**Model acquisition**: The application must download the model file on first use if not already present. Use `reqwest` to download from the configured Hugging Face repo:
```
https://huggingface.co/{repo}/resolve/main/{model_name}
```

**Cache location**: Store the downloaded model at the standard whisper.cpp cache path. Do NOT require the user to manage model files. The application handles download + caching transparently.
- Linux/WSL/macOS: `~/.cache/whisper/{model_name}`
- Windows NOT supported

If download fails, error with a message including the URL. Partial downloads must not be left in the cache directory — use a `.part` suffix during download and atomically rename on completion.

### 4. Transcription pass

Decode the selected audio stream in its entirety to raw PCM samples. Resample to 16kHz mono (whisper.cpp requirement). Feed the full PCM buffer to `whisper-rs` with word-level timestamps enabled.

Collect all word-level timestamp segments where the transcribed text partially matches any word in the hardcoded default list:

```
fuck, shit, damn, bitch, dick, cunt, bastard, asshole
```

"Partial match" means the transcribed word text contains the target word as a substring (case-insensitive). For example, "fucking" matches "fuck", "dammit" matches "damn".

Each matched segment yields a `BleepPosition` with:
- `start_time` (milliseconds, from whisper timestamp)
- `end_time` (milliseconds, from whisper timestamp)

### 5. Encoding + muxing pass

Create the output file with the same container format as the input.

For each stream in the input:

| Stream type | Handling |
|-------------|----------|
| Video | Stream copy (passthrough, no re-encode) |
| Subtitles | Stream copy (passthrough, no re-encode) |
| Attachments / data | Stream copy (passthrough, no re-encode) |
| Audio (not selected) | Stream copy (passthrough, no re-encode) |
| Audio (selected) | Decode → apply white noise → encode as AAC |

**White noise application**: For each `BleepPosition`, replace the audio samples in that time range with white noise at 80% of full scale. Samples outside bleep ranges pass through unmodified. White noise is generated per-channel (independent noise for each channel).

**AAC encoding**: Use libav's AAC encoder (`AV_CODEC_ID_AAC`). Bitrate: 640 kbps (Blu-ray quality). If the AAC encoder is not available in the libav build, error with a message.

**Muxing**: Interleave all output streams (copied + re-encoded audio) into the output container. Use the same format context as the input.

### 6. Cleanup

Close all libav contexts. The model file remains cached for future runs.

## Directory layout

```
no-swear/
├── Cargo.toml
├── src/
│   └── main.rs          # Single source file — all logic here
├── README.md            # Build + run instructions
└── .gitignore
```
