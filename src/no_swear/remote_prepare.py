import shlex
import tempfile
from pathlib import Path, PurePosixPath

from . import commands
from .cli.logging import Logger
from .remote import RemoteHost, RemoteRunner, remote_temp_path


def build_toolchain_check() -> list[str]:
    return [
        "sh",
        "-c",
        "command -v ffmpeg && command -v ffprobe && command -v mkvmerge",
    ]


def build_remote_path_validation(input_path: str, output_path: str) -> list[str]:
    output_parent = PurePosixPath(output_path).parent.as_posix()
    shell_command = (
        f"test -f {shlex.quote(input_path)} && test -d {shlex.quote(output_parent)}"
    )
    return ["sh", "-c", shell_command]


def prepare(
    input_path: str,
    output_path: str,
    audio_idx: int,
    workdir: Path,
    remote_host: RemoteHost,
    logger: Logger,
) -> tuple[Path, dict]:
    runner = RemoteRunner(remote_host, logger)
    commands.run(build_toolchain_check(), runner, logger, "remote_preflight")
    commands.run(
        build_remote_path_validation(input_path, output_path),
        runner,
        logger,
        "remote_validate_paths",
    )
    probe_result = commands.probe(input_path, runner, logger)
    commands.validate_audio_stream(probe_result, audio_idx)

    with tempfile.NamedTemporaryFile(
        dir=workdir, prefix="no_swear_", suffix=".mka", delete=False
    ) as temporary_file:
        local_audio_path = Path(temporary_file.name)
    remote_audio_path = remote_temp_path(input_path, ".mka")

    try:
        commands.extract_audio(audio_idx, input_path, remote_audio_path, runner, logger)
        runner.download(remote_audio_path, str(local_audio_path))
        logger.info(
            "remote_audio_download_complete",
            {"remote_path": remote_audio_path, "local_path": str(local_audio_path)},
        )
    finally:
        try:
            commands.run(
                ["rm", "-f", remote_audio_path],
                runner,
                logger,
                "remote_extract_cleanup",
            )
        except Exception as error:
            logger.warn(
                "remote_extract_cleanup_failed",
                {"remote_path": remote_audio_path, "error": str(error)},
                "Remote extraction temporary file may remain",
            )

    return local_audio_path, probe_result
