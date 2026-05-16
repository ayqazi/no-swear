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
| `--verbose` | No | Increase output verbosity |

### Error conditions

- `input` does not exist → error with message
- `--audio` stream index does not exist → error listing available streams
- `--audio` stream exists but is not audio (e.g., video) → error
- Output file cannot be written → error
- Speech-to-text model cannot be loaded or downloaded → error with message
- Output cannot be encoded (no suitable audio encoder available) → error with message

## Behaviour

### 1. Argument parsing

Parse two positional args (`input`, `output`) and flags (`--audio`, `--model`, `--precision`). Validate all error conditions before proceeding.

### 2. Audio extraction

Extract the selected audio stream resampled to the format expected by the speech-to-text engine (mono, 16 kHz).

### 3. Model loading

Load the configured speech-to-text model. The application manages download and caching transparently. If the model cannot be acquired, error with a message.

### 4. Transcription

Transcribe the extracted audio with the speech-to-text engine, requesting word-level timestamps.

Collect all word-level timestamp segments where the transcribed text partially matches any word in the hardcoded default list:

```
fuck, shit, damn, bitch, dick, cunt, bastard, asshole
```

"Partial match" means the transcribed word text contains the target word as a substring (case-insensitive). For example, "fucking" matches "fuck", "dammit" matches "damn".

Each matched segment yields a `BleepPosition` with:
- `start_time` (seconds, from speech-to-text timestamp)
- `end_time` (seconds, from speech-to-text timestamp)

### 5. Noise generation

Create a replacement audio file that is identical to the original selected audio stream except that the time ranges matching each `BleepPosition` are overwritten with brown noise.

**Brown noise application**: For each `BleepPosition`, replace the audio samples in that time range with brown noise (random walk / integrated white noise) at approximately 1/35th the amplitude of the surrounding dialog (~0.023 of full scale). Samples outside bleep ranges pass through unmodified. Brown noise is generated per-channel (independent noise for each channel) if the audio has multiple channels.

### 6. Output assembly

Assemble the output file using the processed audio and all original non-selected streams (video, subtitles, attachments, other audio tracks). All non-selected streams pass through without re-encoding. The output container format matches the input. If the output cannot be assembled because no suitable audio encoder is available, error with a message.

### 7. Cleanup

All temporary artifacts are cleaned up.

## Directory layout

```
no-swear/
├── no-swear          # Executable
├── README.md         # Run instructions
└── .gitignore
```
