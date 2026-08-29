import argparse
import sys
import tempfile
from pathlib import Path

from .. import censor, local_prepare, local_recombine, remote_prepare, remote_recombine
from ..remote import RemoteHost, parse_remote_host
from .logging import Logger


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="no-swear", description="Censor profanity from audio or video files"
    )
    parser.add_argument("input", help="Path to input media file")
    parser.add_argument("output", help="Path to output media file")
    parser.add_argument(
        "--audio", type=int, required=True, help="Audio stream index (0-based)"
    )
    parser.add_argument("--model", default="tiny.en", help="Speech-to-text model name")
    parser.add_argument(
        "--precision", default="int8", help="Model precision (int8, float16, etc.)"
    )
    parser.add_argument(
        "--words", required=True, help="Comma-separated list of words to censor"
    )
    parser.add_argument(
        "--keep", action="store_true", help="Keep intermediate audio files"
    )
    parser.add_argument(
        "--remote-host",
        help="SSH host for remote input and output (ssh://[user@]host[:port])",
    )
    return parser.parse_args(argv)


def parse_wordlist(value: str) -> frozenset[str]:
    wordlist = frozenset(
        part.strip().lower() for part in value.split(",") if part.strip()
    )
    if not wordlist:
        raise SystemExit("empty word list")
    return wordlist


def _cleanup_intermediate_files(paths: list[Path], logger: Logger):
    files = {}
    for path in paths:
        if path.is_file():
            files[str(path)] = path.stat().st_size
    for path in paths:
        path.unlink(missing_ok=True)
    if files:
        logger.info("intermediate_files_deleted", {"files": str(files)})


def main(argv: list[str] | None = None):
    args = parse_args(argv)
    wordlist = parse_wordlist(args.words)
    remote_host: RemoteHost | None = None
    if args.remote_host is not None:
        try:
            remote_host = parse_remote_host(args.remote_host)
        except ValueError as error:
            raise SystemExit(str(error)) from error

    workdir = Path(tempfile.mkdtemp(prefix="no_swear_"))
    print(workdir, file=sys.stderr)
    logger = Logger(workdir)
    intermediate_paths: list[Path] = []

    logger.info(
        "pipeline_start",
        {
            "input": args.input,
            "output": args.output,
            "audio": str(args.audio),
            "model": args.model,
            "precision": args.precision,
            "words": args.words,
            "mode": "remote" if remote_host else "local",
        },
    )

    try:
        if remote_host:
            audio_path, probe_result = remote_prepare.prepare(
                args.input,
                args.output,
                args.audio,
                workdir,
                remote_host,
                logger,
            )
        else:
            audio_path, probe_result = local_prepare.prepare(
                args.input,
                args.output,
                args.audio,
                workdir,
                logger,
            )
        intermediate_paths.append(audio_path)

        orig_bitrate = probe_result["streams"][args.audio].get("bit_rate")
        censored_audio = censor.censor_audio(
            audio_path,
            args.model,
            args.precision,
            wordlist,
            orig_bitrate,
            logger,
        )
        intermediate_paths.extend(censored_audio.intermediate_paths)

        if remote_host:
            remote_recombine.recombine(
                args.input,
                censored_audio.encoded_path,
                args.output,
                args.audio,
                probe_result,
                remote_host,
                logger,
            )
        else:
            local_recombine.recombine(
                Path(args.input),
                censored_audio.encoded_path,
                Path(args.output),
                args.audio,
                probe_result,
                logger,
            )

        logger.info(
            "pipeline_complete", {"bleeps_found": str(len(censored_audio.bleeps))}
        )
        if not args.keep:
            _cleanup_intermediate_files(intermediate_paths, logger)
    finally:
        logger.close()
