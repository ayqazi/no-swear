import time
from pathlib import Path

import ffmpeg

from .cli.logging import Logger


def encode_to_aac(wav_path: Path, m4a_path: Path, orig_bitrate: str | None, logger: Logger):
    """
    WHY: Decoupling AAC encode from final assembly eliminates the root cause of
    muxing queue overflows.  When ffmpeg stream-copies video it supplies packets
    at demux speed (near-instant), while the built-in AAC encoder runs at
    roughly realtime.  The muxer buffers the torrent of video packets while it
    waits for audio, eventually overflowing its queue (~1024 packets default).
    Encoding audio to its final container *first*, alone, with zero video
    contention, means the subsequent mux step has no encode-speed mismatch.

    WHY m4a: Using a pure-audio container (.m4a / MP4 with only AAC) avoids
    ffmpeg's own Matroska muxer, which has accumulated many
    unknown-unknowns over the years.  The AAC encoder in a simple MP4 shell
    has a well-tested code path in ffmpeg.
    """
    t0 = time.perf_counter()
    try:
        in_file = ffmpeg.input(str(wav_path))
        opts = {"c:a": "aac"}
        if orig_bitrate:
            opts["b:a"] = orig_bitrate
        out = ffmpeg.output(in_file, str(m4a_path), **opts)
        out, err = ffmpeg.run(out, overwrite_output=True, capture_stdout=True, capture_stderr=True)
    except ffmpeg.Error as e:
        elapsed = time.perf_counter() - t0
        stderr_text = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        logger.error("encode_audio_failed",
                     {"elapsed_sec": f"{elapsed:.3f}"},
                     f"AAC encode failed: {stderr_text}")
        raise
    elapsed = time.perf_counter() - t0
    logger.info("encode_audio_complete",
                {"elapsed_sec": f"{elapsed:.3f}", "m4a_path": str(m4a_path)})
    sub_log = logger.workdir / "logs" / "encode_audio.log"
    with open(sub_log, "w") as f:
        if out:
            f.write(out.decode("utf-8", errors="replace"))
        if err:
            f.write(err.decode("utf-8", errors="replace"))