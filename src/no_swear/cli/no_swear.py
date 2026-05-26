import argparse
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import ffmpeg
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
                     {"elapsed_sec": f"{elapsed:.3f}", "stderr": stderr_text[:500]},
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


def assemble_output(input_path: Path, output_path: Path, logger: Logger):
    t0 = time.perf_counter()
    try:
        in_file = ffmpeg.input(str(input_path))
        out_file = ffmpeg.output(in_file, str(output_path), **{"c": "copy", "map": "0"})
        out, err = ffmpeg.run(out_file, overwrite_output=True, capture_stdout=True, capture_stderr=True)
    except ffmpeg.Error as e:
        elapsed = time.perf_counter() - t0
        stderr_text = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        print(f"ffmpeg error:\n{stderr_text}", file=sys.stderr)
        logger.error("assemble_output_failed",
                     {"elapsed_sec": f"{elapsed:.3f}", "stderr": stderr_text[:500]},
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

    # TODO: Placeholder for censoring words from audio

    assemble_output(input_path, output_path, logger)

    logger.info("pipeline_complete", {"bleeps_found": str(len(bleeps))})
    logger.close()
