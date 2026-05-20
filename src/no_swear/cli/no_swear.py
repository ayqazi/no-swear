import argparse
import sys
import tempfile
from pathlib import Path

from no_swear.cli.logging import Logger


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="no-swear", description="Censor profanity from audio or video files")
    p.add_argument("input", help="Path to input media file")
    p.add_argument("output", help="Path to output media file")
    p.add_argument("--audio", type=int, required=True, help="Audio stream index (0-based)")
    p.add_argument("--model", default="tiny.en", help="Speech-to-text model name")
    p.add_argument("--precision", default="int8", help="Model precision (int8, float16, etc.)")
    return p.parse_args(argv)


def validate_args(args: argparse.Namespace) -> list[str]:
    failures: list[str] = []
    input_path = Path(args.input)
    if not input_path.is_file():
        failures.append("input_not_found")
    output_path = Path(args.output)
    o_dir = output_path.parent
    if str(o_dir) and not o_dir.is_dir():
        failures.append("output_dir_invalid")
    if args.audio < 0:
        failures.append("invalid_audio_index")
    return failures


def phase_parse_and_validate(argv: list[str] | None, logger: Logger) -> argparse.Namespace | None:
    args = parse_args(argv)
    errors = validate_args(args)
    if errors:
        logger.error("validation_failed", {"errors": ",".join(errors)}, "Argument validation failed, pipeline aborted")
        return None
    logger.info("args_parsed",
                {"input": args.input, "output": args.output, "audio": args.audio, "model": args.model, "precision": args.precision},
                "Arguments parsed and validated")
    return args


def phase_extract_stt(audio_idx: int, out_wav: Path):
    pass


def phase_load_model(model: str, precision: str):
    pass


def phase_transcribe(wav_path: Path):
    pass


def phase_match_swear_words(segments, wordlist: frozenset):
    return []


def phase_extract_fullres(audio_idx: int, out_wav: Path):
    pass


def phase_load_pcm(wav_path: Path):
    return None


def phase_generate_noise(samples, bleeps, sample_rate: int, channels: int):
    return None


def phase_write_processed(samples, sample_rate: int, channels: int, out_wav: Path):
    pass


def phase_remux(container_path: Path, audio_idx: int, processed_wav: Path, output_path: Path):
    pass


def main(argv: list[str] | None = None):
    workdir = Path(tempfile.mkdtemp(prefix="no_swear_"))
    print(workdir, file=sys.stderr)
    logger = Logger(workdir)

    args = phase_parse_and_validate(argv, logger)
    if args is None:
        sys.exit(1)

    logger.info("pipeline_placeholder",
                {"input": args.input, "output": args.output, "audio": args.audio, "model": args.model, "precision": args.precision},
                "Pipeline invoked")

    logger.close()