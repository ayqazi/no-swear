import argparse
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import ffmpeg
import numpy as np
import soundfile as sf
from faster_whisper import WhisperModel

from .logging import Logger


@dataclass
class BleepPosition:
    word: str
    start_sec: float
    end_sec: float


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="no-swear", description="Censor profanity from audio or video files")
    p.add_argument("input", help="Path to input media file")
    p.add_argument("output", help="Path to output media file")
    p.add_argument("--audio", type=int, required=True, help="Audio stream index (0-based)")
    p.add_argument("--model", default="tiny.en", help="Speech-to-text model name")
    p.add_argument("--precision", default="int8", help="Model precision (int8, float16, etc.)")
    p.add_argument("--words", required=True, help="Comma-separated list of words to censor")
    return p.parse_args(argv)


def validate_args(args: argparse.Namespace):
    input_path = Path(args.input)
    if not input_path.is_file():
        sys.exit("input not found")

    probe = ffmpeg.probe(str(input_path))
    streams = probe.get("streams", [])
    if args.audio >= len(streams) or streams[args.audio]["codec_type"] != "audio":
        sys.exit("audio stream not audio")

    output_path = Path(args.output)
    o_dir = output_path.parent
    if str(o_dir) and not o_dir.is_dir():
        sys.exit("output dir invalid")
    if args.audio < 0:
        sys.exit("invalid audio index")

    wordlist = []
    for part in args.words.split(","):
        w = part.strip()
        if w:
            wordlist.append(w)

    if not wordlist:
        sys.exit("empty word list")


def extract_audio(audio_idx: int, input_path: Path, audio_path: Path, logger: Logger):
    t0 = time.perf_counter()
    try:
        in_file = ffmpeg.input(str(input_path))
        audio = in_file[str(audio_idx)]
        out_file = ffmpeg.output(audio, str(audio_path), **{"c": "copy"})
        out, err = ffmpeg.run(out_file, overwrite_output=True, capture_stdout=True, capture_stderr=True)
    except ffmpeg.Error as e:
        elapsed = time.perf_counter() - t0
        stderr_text = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        logger.error("extract_audio_failed",
                     {"elapsed_sec": f"{elapsed:.3f}"},
                     f"Audio extraction failed, ffmpeg error: {stderr_text}")
        raise
    elapsed = time.perf_counter() - t0
    logger.info("extract_audio_complete", {"elapsed_sec": f"{elapsed:.3f}", "audio_path": str(audio_path)},
                "Audio extracted for transcription")
    sub_log = logger.workdir / "logs" / "extract_audio.log"
    with open(sub_log, "w") as f:
        if out:
            f.write(out.decode("utf-8", errors="replace"))
        if err:
            f.write(err.decode("utf-8", errors="replace"))


def load_model(model: str, precision: str, logger: Logger) -> WhisperModel:
    t0 = time.perf_counter()
    try:
        whisper = WhisperModel(model, compute_type=precision)
    except Exception as e:
        elapsed = time.perf_counter() - t0
        logger.error("whisper_error", {"model": model, "precision": precision}, str(e))
        raise
    elapsed = time.perf_counter() - t0
    logger.info("whisper_model_loaded", {"model": model, "precision": precision, "elapsed_sec": f"{elapsed:.3f}"})
    return whisper


def transcribe(whisper: WhisperModel, audio_path: Path, logger: Logger) -> list:
    t0 = time.perf_counter()
    whisper_log_path = logger.workdir / "logs" / "whisper.log"
    try:
        segments, info = whisper.transcribe(str(audio_path), word_timestamps=True)
        seg_list = list(segments)
    except Exception as e:
        elapsed = time.perf_counter() - t0
        logger.error("transcribe_failed", {"elapsed_sec": f"{elapsed:.3f}", "error": str(e)},
                     "Transcription failed")
        raise
    with open(whisper_log_path, "w") as f:
        f.write(f"Language: {info.language}\n")
        f.write(f"Duration: {info.duration:.3f}s\n")
        f.write("Segments:\n")
        for seg in seg_list:
            f.write(f"  [{seg.start:.3f}s -> {seg.end:.3f}s] {seg.text}\n")
            if seg.words:
                for w in seg.words:
                    f.write(f"    word: {w.word} [{w.start:.3f}s -> {w.end:.3f}s]\n")
    elapsed = time.perf_counter() - t0
    logger.info("transcribe_complete", {"elapsed_sec": f"{elapsed:.3f}"})
    return seg_list


def match_censored_words(segments, wordlist: frozenset, logger: Logger) -> list[BleepPosition]:
    bleeps: list[BleepPosition] = []
    for seg in segments:
        if not seg.words:
            continue
        for w in seg.words:
            normalized_censored_words = w.word.strip().lower()
            for censored in wordlist:
                if censored in normalized_censored_words:
                    bleeps.append(BleepPosition(word=w.word.strip(), start_sec=w.start, end_sec=w.end))
                    logger.info("bleep_match",
                                {"word": w.word.strip(), "start": f"{w.start:.3f}", "end": f"{w.end:.3f}"})
                    break
    logger.info("bleep_summary", {"total": str(len(bleeps))})
    return bleeps


def generate_noise(bleeps: list[BleepPosition], audio_path: Path, processed_path: Path, logger: Logger):
    t0 = time.perf_counter()
    wav_path = audio_path.with_suffix(".wav")
    try:
        in_file = ffmpeg.input(str(audio_path))
        decode = ffmpeg.output(in_file, str(wav_path), **{"c": "pcm_s16le"})
        ffmpeg.run(decode, overwrite_output=True, capture_stdout=True, capture_stderr=True)
    except ffmpeg.Error as e:
        elapsed = time.perf_counter() - t0
        stderr_text = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        logger.error("decode_audio_failed",
                     {"elapsed_sec": f"{elapsed:.3f}"},
                     f"Audio decode failed: {stderr_text}")
        raise

    samples, sr = sf.read(str(wav_path))
    if samples.ndim == 1:
        samples = samples.reshape(-1, 1)

    max_amp = 0.8 / 35.0
    step_size = max_amp * 0.125

    for bp in bleeps:
        i0 = max(0, int(bp.start_sec * sr))
        i1 = min(len(samples), int(bp.end_sec * sr))
        length = i1 - i0
        if length <= 0:
            continue
        n_channels = samples.shape[1]
        steps = np.random.uniform(-step_size, step_size, (length, n_channels))
        noise = np.clip(np.cumsum(steps, axis=0), -max_amp, max_amp)
        samples[i0:i1] = noise.astype(samples.dtype)
        logger.info("noise_applied",
                    {"word": bp.word, "start_sec": f"{bp.start_sec:.3f}", "end_sec": f"{bp.end_sec:.3f}",
                     "i0": str(i0), "i1": str(i1), "samples": str(length)})

    if samples.shape[1] == 1:
        samples = samples.ravel()

    sf.write(str(processed_path), samples, sr)
    elapsed = time.perf_counter() - t0
    logger.info("generate_noise_complete",
                {"elapsed_sec": f"{elapsed:.3f}", "bleeps_processed": str(len(bleeps))})


def assemble_output(input_path: Path, processed_audio_path: Path, output_path: Path, audio_idx: int, logger: Logger):
    t0 = time.perf_counter()
    try:
        probe = ffmpeg.probe(str(input_path))
        probe_streams = probe.get("streams", [])

        orig = ffmpeg.input(str(input_path))
        proc = ffmpeg.input(str(processed_audio_path))

        stream_objects = []
        codec_opts = {}
        v_count = a_count = s_count = 0

        for i, s in enumerate(probe_streams):
            if i == audio_idx:
                stream_objects.append(proc["a:0"])
                codec_opts[f"c:a:{a_count}"] = "aac"
                orig_bitrate = probe_streams[audio_idx].get("bit_rate")
                if orig_bitrate:
                    codec_opts[f"b:a:{a_count}"] = str(orig_bitrate)
                a_count += 1
            else:
                st = s["codec_type"]
                if st == "video":
                    stream_objects.append(orig[f"v:{v_count}"])
                    codec_opts[f"c:v:{v_count}"] = "copy"
                    v_count += 1
                elif st == "audio":
                    stream_objects.append(orig[f"a:{a_count}"])
                    codec_opts[f"c:a:{a_count}"] = "copy"
                    a_count += 1
                elif st == "subtitle":
                    stream_objects.append(orig[f"s:{s_count}"])
                    codec_opts[f"c:s:{s_count}"] = "copy"
                    s_count += 1

        out_file = ffmpeg.output(*stream_objects, str(output_path), **codec_opts)
        out, err = ffmpeg.run(out_file, overwrite_output=True, capture_stdout=True, capture_stderr=True)
    except ffmpeg.Error as e:
        elapsed = time.perf_counter() - t0
        stderr_text = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        print(f"ffmpeg error:\n{stderr_text}", file=sys.stderr)
        logger.error("assemble_output_failed",
                     {"elapsed_sec": f"{elapsed:.3f}", "stderr": stderr_text},
                     "Output assembly failed")
        raise
    elapsed = time.perf_counter() - t0
    logger.info("assemble_output_complete", {"elapsed_sec": f"{elapsed:.3f}", "output": str(output_path)})
    sub_log = logger.workdir / "logs" / "assemble_output.log"
    with open(sub_log, "w") as f:
        if out:
            f.write(out.decode("utf-8", errors="replace"))
        if err:
            f.write(err.decode("utf-8", errors="replace"))

def main(argv: list[str] | None = None):
    args = parse_args(argv)
    validate_args(args)

    workdir = Path(tempfile.mkdtemp(prefix="no_swear_"))
    print(workdir, file=sys.stderr)
    logger = Logger(workdir)

    wordlist = frozenset(w.strip().lower() for w in args.words.split(",") if w.strip())

    logger.info("pipeline_start",
                {"input": args.input, "output": args.output, "audio": str(args.audio),
                 "model": args.model, "precision": args.precision, "words": args.words})

    input_path = Path(args.input)
    output_path = Path(args.output)
    audio_path = workdir / "audio.mka"

    extract_audio(args.audio, input_path, audio_path, logger)
    whisper = load_model(args.model, args.precision, logger)
    segments = transcribe(whisper, audio_path, logger)
    bleeps = match_censored_words(segments, wordlist, logger)

    processed_audio = workdir / "processed.wav"
    generate_noise(bleeps, audio_path, processed_audio, logger)

    assemble_output(input_path, processed_audio, output_path, args.audio, logger)

    logger.info("pipeline_complete", {"bleeps_found": str(len(bleeps))})
    logger.close()
