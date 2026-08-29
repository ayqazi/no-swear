from pathlib import Path

from . import commands
from .cli.logging import Logger
from .remote import RemoteHost, RemoteRunner, remote_temp_path


def recombine(
    input_path: str,
    encoded_audio_path: Path,
    output_path: str,
    audio_idx: int,
    probe_result: dict,
    remote_host: RemoteHost,
    logger: Logger,
):
    runner = RemoteRunner(remote_host, logger)
    remote_audio_path = remote_temp_path(output_path, ".m4a")
    try:
        runner.upload(str(encoded_audio_path), remote_audio_path)
        logger.info(
            "remote_audio_upload_complete",
            {"local_path": str(encoded_audio_path), "remote_path": remote_audio_path},
        )
        commands.assemble(
            input_path,
            remote_audio_path,
            output_path,
            audio_idx,
            probe_result,
            runner,
            logger,
        )
    finally:
        try:
            commands.run(
                ["rm", "-f", remote_audio_path],
                runner,
                logger,
                "remote_recombine_cleanup",
            )
        except Exception as error:
            logger.warn(
                "remote_recombine_cleanup_failed",
                {"remote_path": remote_audio_path, "error": str(error)},
                "Remote replacement temporary file may remain",
            )
