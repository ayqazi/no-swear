import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from faster_whisper import WhisperModel

from . import commands
from .cli.logging import Logger

CENSORED_PAD_START_SEC = 0.01
CENSORED_PAD_END_SEC = 0.01


@dataclass
class BleepPosition:
    word: str
    start_sec: float
    end_sec: float


@dataclass
class CensoredAudio:
    encoded_path: Path
    intermediate_paths: list[Path]
    bleeps: list[BleepPosition]


def load_model(model: str, precision: str, logger: Logger) -> WhisperModel:
    t0 = time.perf_counter()
    try:
        whisper = WhisperModel(model, compute_type=precision)
    except Exception as error:
        elapsed = time.perf_counter() - t0
        logger.error(
            "whisper_error", {"model": model, "precision": precision}, str(error)
        )
        raise
    elapsed = time.perf_counter() - t0
    logger.info(
        "whisper_model_loaded",
        {"model": model, "precision": precision, "elapsed_sec": f"{elapsed:.3f}"},
    )
    return whisper


def transcribe(whisper: WhisperModel, audio_path: Path, logger: Logger) -> list:
    t0 = time.perf_counter()
    whisper_log_path = logger.workdir / "logs" / "whisper.log"
    try:
        segments, info = whisper.transcribe(str(audio_path), word_timestamps=True)
        segment_list = list(segments)
    except Exception as error:
        elapsed = time.perf_counter() - t0
        logger.error(
            "transcribe_failed",
            {"elapsed_sec": f"{elapsed:.3f}", "error": str(error)},
            "Transcription failed",
        )
        raise
    with open(whisper_log_path, "w", encoding="utf-8") as log_file:
        log_file.write(f"Language: {info.language}\n")
        log_file.write(f"Duration: {info.duration:.3f}s\n")
        log_file.write("Segments:\n")
        for segment in segment_list:
            log_file.write(
                f"  [{segment.start:.3f}s -> {segment.end:.3f}s] {segment.text}\n"
            )
            if segment.words:
                for word in segment.words:
                    log_file.write(
                        f"    word: {word.word} [{word.start:.3f}s -> {word.end:.3f}s]\n"
                    )
    elapsed = time.perf_counter() - t0
    logger.info("transcribe_complete", {"elapsed_sec": f"{elapsed:.3f}"})
    return segment_list


def match_censored_words(
    segments, wordlist: frozenset[str], logger: Logger
) -> list[BleepPosition]:
    bleeps: list[BleepPosition] = []
    for segment in segments:
        if not segment.words:
            continue
        for word in segment.words:
            normalized_word = word.word.strip().lower()
            for censored in wordlist:
                if censored in normalized_word:
                    bleeps.append(
                        BleepPosition(
                            word=word.word.strip(),
                            start_sec=word.start,
                            end_sec=word.end,
                        )
                    )
                    logger.info(
                        "bleep_match",
                        {
                            "word": word.word.strip(),
                            "start": f"{word.start:.3f}",
                            "end": f"{word.end:.3f}",
                        },
                    )
                    break
    logger.info("bleep_summary", {"total": str(len(bleeps))})
    return bleeps


def generate_noise(
    bleeps: list[BleepPosition], audio_path: Path, processed_path: Path, logger: Logger
) -> Path:
    t0 = time.perf_counter()
    with tempfile.NamedTemporaryFile(
        dir=logger.workdir, prefix="no_swear_", suffix=".wav", delete=False
    ) as temporary_file:
        wav_path = Path(temporary_file.name)

    commands.decode_to_pcm(
        str(audio_path), str(wav_path), commands.LocalRunner(), logger
    )
    samples, sample_rate = sf.read(str(wav_path))
    if samples.ndim == 1:
        samples = samples.reshape(-1, 1)

    max_amp = 0.8 / 35.0
    step_size = max_amp * 0.125
    leak = 0.003

    for bleep in bleeps:
        start_idx = max(
            0, int((bleep.start_sec - CENSORED_PAD_START_SEC) * sample_rate)
        )
        end_idx = min(
            len(samples), int((bleep.end_sec + CENSORED_PAD_END_SEC) * sample_rate)
        )
        length = end_idx - start_idx
        if length <= 0:
            continue
        channel_count = samples.shape[1]
        steps = np.random.uniform(-step_size, step_size, (length, channel_count))
        noise = np.empty((length, channel_count), dtype=np.float64)
        state = np.zeros((1, channel_count), dtype=np.float64)
        for sample_idx in range(length):
            state = (1.0 - leak) * state + steps[sample_idx : sample_idx + 1]
            noise[sample_idx] = state
        samples[start_idx:end_idx] = noise.astype(samples.dtype)
        logger.info(
            "noise_applied",
            {
                "word": bleep.word,
                "start_sec": f"{bleep.start_sec:.3f}",
                "end_sec": f"{bleep.end_sec:.3f}",
                "i0": str(start_idx),
                "i1": str(end_idx),
                "samples": str(length),
            },
        )

    if samples.shape[1] == 1:
        samples = samples.ravel()
    sf.write(str(processed_path), samples, sample_rate)
    elapsed = time.perf_counter() - t0
    logger.info(
        "generate_noise_complete",
        {"elapsed_sec": f"{elapsed:.3f}", "bleeps_processed": str(len(bleeps))},
    )
    return wav_path


def censor_audio(
    audio_path: Path,
    model_name: str,
    precision: str,
    wordlist: frozenset[str],
    orig_bitrate: str | None,
    logger: Logger,
) -> CensoredAudio:
    whisper = load_model(model_name, precision, logger)
    segments = transcribe(whisper, audio_path, logger)
    bleeps = match_censored_words(segments, wordlist, logger)

    with tempfile.NamedTemporaryFile(
        dir=logger.workdir, prefix="no_swear_", suffix=".wav", delete=False
    ) as temporary_file:
        processed_wav = Path(temporary_file.name)
    decoded_wav = generate_noise(bleeps, audio_path, processed_wav, logger)

    with tempfile.NamedTemporaryFile(
        dir=logger.workdir, prefix="no_swear_", suffix=".m4a", delete=False
    ) as temporary_file:
        encoded_audio = Path(temporary_file.name)
    commands.encode_to_aac(
        str(processed_wav),
        str(encoded_audio),
        orig_bitrate,
        commands.LocalRunner(),
        logger,
    )
    logger.info(
        "censor_audio_complete",
        {"encoded_audio": str(encoded_audio), "bleeps_found": str(len(bleeps))},
    )
    return CensoredAudio(
        encoded_path=encoded_audio,
        intermediate_paths=[decoded_wav, processed_wav, encoded_audio],
        bleeps=bleeps,
    )
