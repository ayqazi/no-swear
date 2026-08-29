import json
import shlex
import subprocess
import sys
import time
from pathlib import PurePosixPath
from typing import Protocol

from .cli.logging import Logger


class CommandRunner(Protocol):
    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]: ...


class LocalRunner:
    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, capture_output=True, text=True, check=True)


def _write_log(
    logger: Logger, log_name: str, command: list[str], stdout: str, stderr: str
):
    log_path = logger.workdir / "logs" / f"{log_name}.log"
    with open(log_path, "w", encoding="utf-8") as log_file:
        log_file.write(f"command: {shlex.join(command)}\n")
        log_file.write("stdout:\n")
        log_file.write(stdout)
        if stdout and not stdout.endswith("\n"):
            log_file.write("\n")
        log_file.write("stderr:\n")
        log_file.write(stderr)
        if stderr and not stderr.endswith("\n"):
            log_file.write("\n")


def run(command: list[str], runner: CommandRunner, logger: Logger, log_name: str):
    t0 = time.perf_counter()
    command_text = shlex.join(command)
    try:
        result = runner.run(command)
    except subprocess.CalledProcessError as error:
        elapsed = time.perf_counter() - t0
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        _write_log(logger, log_name, command, stdout, stderr)
        logger.error(
            f"{log_name}_failed",
            {
                "elapsed_sec": f"{elapsed:.3f}",
                "returncode": str(error.returncode),
                "command": command_text,
            },
            stderr or str(error),
        )
        print(f"{log_name} failed:\n{stderr}", file=sys.stderr)
        raise
    except OSError as error:
        elapsed = time.perf_counter() - t0
        _write_log(logger, log_name, command, "", str(error))
        logger.error(
            f"{log_name}_failed",
            {"elapsed_sec": f"{elapsed:.3f}", "command": command_text},
            str(error),
        )
        print(f"{log_name} failed: {error}", file=sys.stderr)
        raise

    elapsed = time.perf_counter() - t0
    _write_log(logger, log_name, command, result.stdout or "", result.stderr or "")
    logger.info(
        f"{log_name}_complete",
        {"elapsed_sec": f"{elapsed:.3f}", "command": command_text},
    )
    return result


def probe(path: str, runner: CommandRunner, logger: Logger) -> dict:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        path,
    ]
    result = run(command, runner, logger, "probe")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        logger.error(
            "probe_parse_failed", {"error": str(error)}, "Could not parse ffprobe JSON"
        )
        raise


def validate_audio_stream(probe_result: dict, audio_idx: int):
    if audio_idx < 0:
        raise SystemExit("invalid audio index")
    streams = probe_result.get("streams", [])
    if audio_idx >= len(streams) or streams[audio_idx].get("codec_type") != "audio":
        raise SystemExit("audio stream not audio")


def extract_audio(
    audio_idx: int,
    input_path: str,
    audio_path: str,
    runner: CommandRunner,
    logger: Logger,
):
    command = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-map",
        f"0:{audio_idx}",
        "-c",
        "copy",
        audio_path,
    ]
    run(command, runner, logger, "extract_audio")


def decode_to_pcm(
    audio_path: str, wav_path: str, runner: CommandRunner, logger: Logger
):
    command = [
        "ffmpeg",
        "-y",
        "-i",
        audio_path,
        "-c:a",
        "pcm_s16le",
        "-ac",
        "2",
        wav_path,
    ]
    run(command, runner, logger, "decode_audio")


def encode_to_aac(
    wav_path: str,
    m4a_path: str,
    orig_bitrate: str | None,
    runner: CommandRunner,
    logger: Logger,
):
    command = ["ffmpeg", "-y", "-i", wav_path, "-c:a", "aac"]
    if orig_bitrate:
        command.extend(["-b:a", orig_bitrate])
    command.append(m4a_path)
    run(command, runner, logger, "encode_audio")


def _build_ffmpeg_assembly(
    input_path: str,
    encoded_audio_path: str,
    output_path: str,
    audio_idx: int,
    probe_result: dict,
) -> list[str]:
    probe_streams = probe_result.get("streams", [])
    command = ["ffmpeg", "-y", "-i", input_path, "-i", encoded_audio_path]
    video_count = subtitle_count = audio_count = 0

    for stream_idx, stream in enumerate(probe_streams):
        if stream_idx == audio_idx:
            command.extend(["-map", "1:a:0"])
            command.extend([f"-c:a:{audio_count}", "copy"])
            audio_count += 1
        elif stream.get("codec_type") == "video":
            command.extend(["-map", f"0:v:{video_count}"])
            command.extend([f"-c:v:{video_count}", "copy"])
            video_count += 1
        elif stream.get("codec_type") == "subtitle":
            command.extend(["-map", f"0:s:{subtitle_count}"])
            command.extend([f"-c:s:{subtitle_count}", "copy"])
            subtitle_count += 1

    source_audio_type_idx = sum(
        1 for stream in probe_streams[:audio_idx] if stream.get("codec_type") == "audio"
    )
    command.extend(
        [
            "-max_muxing_queue_size",
            "4096",
            "-muxing_queue_data_threshold",
            "8388608",
            "-map_metadata",
            "0",
            "-map_metadata:s:a:0",
            f"0:s:a:{source_audio_type_idx}",
        ]
    )
    if video_count:
        command.extend(["-map_metadata:s:v:0", "0:s:v:0"])
    for subtitle_idx in range(subtitle_count):
        command.extend([f"-map_metadata:s:s:{subtitle_idx}", f"0:s:s:{subtitle_idx}"])
    command.append(output_path)
    return command


def _build_mkvmerge_assembly(
    input_path: str,
    encoded_audio_path: str,
    output_path: str,
    audio_idx: int,
    probe_result: dict,
) -> list[str]:
    original_audio = probe_result["streams"][audio_idx]
    tags = original_audio.get("tags", {})
    disposition = original_audio.get("disposition", {})
    language = tags.get("language", "und")

    command = ["mkvmerge", "-o", output_path, "--no-audio", input_path]
    command.extend(["--language", f"0:{language}"])
    if title := tags.get("title"):
        command.extend(["--track-name", f"0:{title}"])
    command.extend(["--default-track", f"0:{disposition.get('default', 0)}"])
    command.extend(["--forced-track", f"0:{disposition.get('forced', 0)}"])
    command.extend(
        [
            "--no-video",
            "--no-subtitles",
            "--no-chapters",
            "--no-attachments",
            "--no-global-tags",
            encoded_audio_path,
        ]
    )
    return command


def assemble(
    input_path: str,
    encoded_audio_path: str,
    output_path: str,
    audio_idx: int,
    probe_result: dict,
    runner: CommandRunner,
    logger: Logger,
):
    if PurePosixPath(output_path).suffix.lower() == ".mkv":
        command = _build_mkvmerge_assembly(
            input_path, encoded_audio_path, output_path, audio_idx, probe_result
        )
        run(command, runner, logger, "mkvmerge")
        return

    command = _build_ffmpeg_assembly(
        input_path, encoded_audio_path, output_path, audio_idx, probe_result
    )
    run(command, runner, logger, "assemble_output")
