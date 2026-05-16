use clap::Parser;
use ffmpeg_next as ffmpeg;
use ffmpeg_next::Rational;
use rand::Rng;
use std::path::PathBuf;
use std::time::Duration;

#[derive(Parser, Debug)]
struct Args {
    #[arg(help = "Path to input media file")]
    input: String,

    #[arg(help = "Path to output media file")]
    output: String,

    #[arg(long, short, help = "Audio stream index to censor (0-based)")]
    audio: usize,

    #[arg(
        long,
        default_value = "ggml-tiny.en-q5_1.bin",
        help = "Whisper model filename to use from the repo. Recommended: ggml-medium.en-q5_0.bin"
    )]
    model_name: String,

    #[arg(
        long,
        default_value = "ggerganov/whisper.cpp",
        help = "Hugging Face repo to download the model from"
    )]
    model_repo: String,

    #[arg(long, help = "Increase output verbosity")]
    verbose: bool,
}

struct CensoringPosition {
    word: String,
    start_ms: i64,
    end_ms: i64,
}

fn extract_samples(frame: &ffmpeg::frame::Audio) -> &[f32] {
    let data = frame.data(0);
    let count = frame.samples();
    if count > 0 {
        unsafe { std::slice::from_raw_parts(data.as_ptr() as *const f32, count) }
    } else {
        &[]
    }
}

fn extract_audio(
    ictx: &mut ffmpeg::format::context::Input,
    audio_stream_index: usize,
    verbose: bool,
) -> Result<Vec<f32>, Box<dyn std::error::Error>> {
    if verbose {
        eprintln!("Opening audio decoder for stream {}", audio_stream_index);
    }
    let stream = ictx.stream(audio_stream_index).ok_or("audio stream not found")?;
    let context = ffmpeg::codec::context::Context::from_parameters(stream.parameters())?;
    let mut decoder = context.decoder().audio()?;

    let src_layout = decoder.channel_layout();
    let src_rate = decoder.rate();
    let src_format = decoder.format();

    if verbose {
        eprintln!("Source audio: format={:?} rate={} layout={:?}", src_format, src_rate, src_layout);
    }

    let dst_format = ffmpeg::util::format::Sample::F32(ffmpeg::util::format::sample::Type::Packed);
    let dst_layout = ffmpeg::channel_layout::ChannelLayout::MONO;
    let dst_rate = 16000;

    if verbose {
        eprintln!("Creating resampler ({} channels -> mono, {} Hz -> 16 kHz)", src_layout.channels(), src_rate);
    }
    let mut resampler = ffmpeg::software::resampling::context::Context::get(
        src_format,
        src_layout,
        src_rate,
        dst_format,
        dst_layout,
        dst_rate,
    )?;

    let mut pcm_buffer: Vec<f32> = Vec::new();

    for (pkt_stream, packet) in ictx.packets() {
        if pkt_stream.index() != audio_stream_index {
            continue;
        }
        decoder.send_packet(&packet)?;
        let mut frame = ffmpeg::frame::Audio::empty();
        loop {
            match decoder.receive_frame(&mut frame) {
                Ok(()) => {
                    let mut dst = ffmpeg::frame::Audio::empty();
                    resampler.run(&frame, &mut dst)?;
                    pcm_buffer.extend_from_slice(extract_samples(&dst));
                }
                Err(ffmpeg::Error::Eof) => break,
                Err(_) => break,
            }
        }
    }

    if verbose {
        eprintln!("Draining decoder (flush)");
    }
    decoder.send_packet(&ffmpeg::packet::Packet::empty())?;
    let mut frame = ffmpeg::frame::Audio::empty();
    loop {
        match decoder.receive_frame(&mut frame) {
            Ok(()) => {
                let mut dst = ffmpeg::frame::Audio::empty();
                resampler.run(&frame, &mut dst)?;
                pcm_buffer.extend_from_slice(extract_samples(&dst));
            }
            Err(ffmpeg::Error::Eof) => break,
            Err(_) => break,
        }
    }

    if verbose {
        eprintln!("Flushing resampler");
    }
    loop {
        let mut dst = ffmpeg::frame::Audio::empty();
        unsafe { dst.alloc(dst_format, 4096, dst_layout); }
        let delay = resampler.flush(&mut dst)?;
        if delay.is_none() {
            break;
        }
        pcm_buffer.extend_from_slice(extract_samples(&dst));
    }

    if pcm_buffer.is_empty() {
        return Err("No audio samples decoded - empty PCM buffer".into());
    }

    if verbose {
        eprintln!("Decoded {} PCM samples ({:.1}s at 16 kHz)", pcm_buffer.len(), pcm_buffer.len() as f64 / 16000.0);
    }

    Ok(pcm_buffer)
}

fn find_bleeps_by_segment(
    state: &whisper_rs::WhisperState,
    swear_words: &[&str],
    verbose: bool,
) -> Result<Vec<CensoringPosition>, Box<dyn std::error::Error>> {
    let n_segments = state.full_n_segments();
    let mut censored = Vec::new();
    for i in 0..n_segments {
        let segment = state.get_segment(i).ok_or("failed to get segment")?;
        let text = segment.to_str()?.to_lowercase();
        for &word in swear_words {
            if text.contains(word) {
                let t0 = segment.start_timestamp() * 10;
                let t1 = segment.end_timestamp() * 10;
                censored.push(CensoringPosition {
                    word: word.to_string(),
                    start_ms: t0,
                    end_ms: t1,
                });
            }
        }
    }
    if verbose {
        eprintln!("Segment-level matching found {} bleep positions", censored.len());
    }
    Ok(censored)
}

fn find_bleeps_by_token(
    state: &whisper_rs::WhisperState,
    swear_words: &[&str],
    verbose: bool,
) -> Result<Vec<CensoringPosition>, Box<dyn std::error::Error>> {
    let n_segments = state.full_n_segments();
    let mut censored = Vec::new();
    for i in 0..n_segments {
        let segment = state.get_segment(i).ok_or("failed to get segment")?;
        let n_tokens = segment.n_tokens();
        for j in 0..n_tokens {
            let token = segment.get_token(j).ok_or("failed to get token")?;
            let token_text = token.to_str()?.to_lowercase();
            let token_data = token.token_data();
            for &word in swear_words {
                if token_text.contains(word) {
                    censored.push(CensoringPosition {
                        word: word.to_string(),
                        start_ms: token_data.t0 * 10,
                        end_ms: token_data.t1 * 10,
                    });
                }
            }
        }
    }
    if verbose {
        eprintln!("Token-level matching found {} bleep positions", censored.len());
    }
    Ok(censored)
}

fn transcribe(
    pcm_buffer: &[f32],
    state: &mut whisper_rs::WhisperState,
    verbose: bool,
) -> Result<Vec<CensoringPosition>, Box<dyn std::error::Error>> {
    let mut params = whisper_rs::FullParams::new(whisper_rs::SamplingStrategy::Greedy { best_of: 5 });
    params.set_token_timestamps(true);
    params.set_split_on_word(true);
    params.set_n_threads(4);
    params.set_print_progress(false);
    params.set_print_realtime(false);
    params.set_print_special(false);
    params.set_language(Some("en"));

    if verbose {
        eprintln!("Running whisper transcription on {:.1}s of audio", pcm_buffer.len() as f64 / 16000.0);
    }

    state.full(params, pcm_buffer)?;

    if verbose {
        eprintln!("Transcription complete, {} segments", state.full_n_segments());
    }

    let swear_words = ["fuck", "shit", "damn", "bitch", "dick", "cunt", "bastard", "asshole"];

    let use_word_boundary_fix = std::env::var("CENSOR_WORD_BOUNDARY_FIX").is_ok();
    if verbose {
        eprintln!("CENSOR_WORD_BOUNDARY_FIX={}", if use_word_boundary_fix { "enabled (using token-level matching)" } else { "disabled (using segment-level matching)" });
    }

    if use_word_boundary_fix {
        find_bleeps_by_token(state, &swear_words, verbose)
    } else {
        find_bleeps_by_segment(state, &swear_words, verbose)
    }
}

fn apply_noise(
    frame: &mut ffmpeg::frame::Audio,
    censored: &[CensoringPosition],
    time_base: Rational,
    sample_rate: i32,
) -> Result<(), Box<dyn std::error::Error>> {
    let frame_pts = match frame.pts() {
        Some(pts) => pts,
        None => return Ok(()),
    };

    let frame_start_ms = frame_pts * time_base.0 as i64 * 1000 / time_base.1 as i64;
    let frame_duration_ms = frame.samples() as i64 * 1000 / sample_rate as i64;
    let frame_end_ms = frame_start_ms + frame_duration_ms;

    let channels = frame.channels() as usize;
    let nb_samples = frame.samples();

    for cp in censored {
        if cp.start_ms >= frame_end_ms || cp.end_ms <= frame_start_ms {
            continue;
        }

        let overlap_start = (cp.start_ms - frame_start_ms).max(0) * sample_rate as i64 / 1000;
        let overlap_end = ((cp.end_ms - frame_start_ms) * sample_rate as i64 / 1000 - 1).min(nb_samples as i64 - 1);

        if overlap_start > overlap_end {
            continue;
        }

        for ch in 0..channels {
            let data = frame.data_mut(ch);
            let samples = unsafe {
                std::slice::from_raw_parts_mut(
                    data.as_mut_ptr() as *mut f32,
                    nb_samples,
                )
            };
            let mut rng = rand::thread_rng();
            let max_amp = 0.8f32 / 35.0;
            let mut value = 0.0f32;
            for i in overlap_start..=overlap_end {
                value += (rng.gen::<f32>() - 0.5) * max_amp * 0.125;
                value = value.clamp(-max_amp, max_amp);
                samples[i as usize] = value;
            }
        }
    }

    Ok(())
}

fn drain_encoder_packets(
    encoder: &mut ffmpeg::codec::encoder::audio::Encoder,
    octx: &mut ffmpeg::format::context::Output,
    stream_index: usize,
    src_tb: Rational,
    dst_tb: Rational,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut packet = ffmpeg::Packet::empty();
    loop {
        match encoder.receive_packet(&mut packet) {
            Ok(()) => {
                packet.set_stream(stream_index);
                packet.rescale_ts(src_tb, dst_tb);
                packet.write_interleaved(octx)?;
            }
            Err(ffmpeg::Error::Eof) => break,
            Err(ffmpeg::Error::Other { errno: ffmpeg::util::error::EAGAIN }) => break,
            Err(e) => return Err(e.into()),
        }
    }
    Ok(())
}

fn flush_encoder(
    encoder: &mut ffmpeg::codec::encoder::audio::Encoder,
    octx: &mut ffmpeg::format::context::Output,
    stream_index: usize,
    src_tb: Rational,
    dst_tb: Rational,
) -> Result<(), Box<dyn std::error::Error>> {
    encoder.send_eof()?;
    let mut packet = ffmpeg::Packet::empty();
    let mut retries = 0;
    const MAX_RETRIES: i32 = 100;
    loop {
        match encoder.receive_packet(&mut packet) {
            Ok(()) => {
                retries = 0;
                packet.set_stream(stream_index);
                packet.rescale_ts(src_tb, dst_tb);
                packet.write_interleaved(octx)?;
            }
            Err(ffmpeg::Error::Eof) => break,
            Err(ffmpeg::Error::Other { errno: ffmpeg::util::error::EAGAIN }) => {
                retries += 1;
                if retries >= MAX_RETRIES {
                    return Err("encoder flush timed out after 100 retries".into());
                }
                std::thread::sleep(std::time::Duration::from_millis(10));
            }
            Err(e) => return Err(e.into()),
        }
    }
    Ok(())
}

fn encode_and_mux(
    input_path: &str,
    output_path: &str,
    audio_stream_index: usize,
    censored: &[CensoringPosition],
    verbose: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut ictx = ffmpeg::format::input(&input_path)?;
    let aac_codec = ffmpeg::encoder::find(ffmpeg::codec::Id::AAC)
        .ok_or("AAC encoder not available in this ffmpeg build")?;

    let mut octx = ffmpeg::format::output(&output_path)?;

    let nb_streams = ictx.nb_streams() as usize;
    let mut ist_time_bases = vec![Rational(0, 1); nb_streams];

    let audio_ist = ictx.stream(audio_stream_index).ok_or("audio stream not found")?;
    let audio_ctx = ffmpeg::codec::context::Context::from_parameters(audio_ist.parameters())?;
    let mut decoder = audio_ctx.decoder().audio()?;

    let src_format = decoder.format();
    let src_layout = decoder.channel_layout();
    let src_rate = decoder.rate() as i32;
    let num_channels = src_layout.channels() as usize;

    if verbose {
        eprintln!("Source audio for encoding: format={:?} rate={} layout={:?} ({} channels)", src_format, src_rate, src_layout, num_channels);
    }

    let dst_format = ffmpeg::util::format::Sample::F32(ffmpeg::util::format::sample::Type::Planar);

    for (ist_index, ist) in ictx.streams().enumerate() {
        if ist_index >= audio_stream_index {
            break;
        }
        ist_time_bases[ist_index] = ist.time_base();
        let mut ost = octx.add_stream(ffmpeg::encoder::find(ffmpeg::codec::Id::None))?;
        ost.set_parameters(ist.parameters());
        unsafe {
            (*ost.parameters().as_mut_ptr()).codec_tag = 0;
        }
    }

    let enc_time_base = Rational(1, src_rate);
    let aac_frame_size = 1024usize;
    let mut encoder = {
        let mut audio_ost = octx.add_stream(aac_codec)?;

        let enc_ctx = ffmpeg::codec::context::Context::from_parameters(audio_ost.parameters())?;
        let mut enc_builder = enc_ctx.encoder().audio()?;
        enc_builder.set_rate(src_rate);
        enc_builder.set_channel_layout(src_layout);
        enc_builder.set_format(dst_format);
        enc_builder.set_bit_rate(640_000);
        enc_builder.set_time_base(enc_time_base);

        let encoder = enc_builder.open_as(aac_codec)?;
        audio_ost.set_parameters(&encoder);
        encoder
    };

    for (ist_index, ist) in ictx.streams().enumerate() {
        if ist_index <= audio_stream_index {
            continue;
        }
        ist_time_bases[ist_index] = ist.time_base();
        let mut ost = octx.add_stream(ffmpeg::encoder::find(ffmpeg::codec::Id::None))?;
        ost.set_parameters(ist.parameters());
        unsafe {
            (*ost.parameters().as_mut_ptr()).codec_tag = 0;
        }
    }

    let mut resampler = ffmpeg::software::resampling::context::Context::get(
        src_format,
        src_layout,
        src_rate as u32,
        dst_format,
        src_layout,
        src_rate as u32,
    )?;

    octx.set_metadata(ictx.metadata().to_owned());

    if verbose {
        eprintln!("Writing output header");
    }
    octx.write_header()?;

    let audio_ost_time_base = {
        let aost = octx.stream(audio_stream_index).ok_or("audio output stream not found")?;
        aost.time_base()
    };
    if verbose {
        eprintln!("Audio output stream time_base: {}/{}", audio_ost_time_base.0, audio_ost_time_base.1);
    }

    let mut channel_bufs: Vec<Vec<f32>> = vec![Vec::with_capacity(aac_frame_size * 2); num_channels];
    let mut encoder_pts_offset: i64 = 0;

    fn flush_buffers(
        channel_bufs: &mut Vec<Vec<f32>>,
        encoder: &mut ffmpeg::codec::encoder::audio::Encoder,
        octx: &mut ffmpeg::format::context::Output,
        audio_stream_index: usize,
        enc_time_base: Rational,
        src_layout: ffmpeg::channel_layout::ChannelLayout,
        dst_format: ffmpeg::util::format::Sample,
        src_rate: i32,
        aac_frame_size: usize,
        encoder_pts_offset: &mut i64,
        censored: &[CensoringPosition],
        ost_tb: Rational,
    ) -> Result<(), Box<dyn std::error::Error>> {
        while channel_bufs[0].len() >= aac_frame_size {
            let mut enc_frame = ffmpeg::frame::Audio::new(
                dst_format,
                aac_frame_size,
                src_layout,
            );
            enc_frame.set_rate(src_rate as u32);

            for ch in 0..channel_bufs.len() {
                let drained: Vec<f32> = channel_bufs[ch].drain(..aac_frame_size).collect();
                let plane = unsafe {
                    std::slice::from_raw_parts_mut(
                        enc_frame.data_mut(ch).as_mut_ptr() as *mut f32,
                        aac_frame_size,
                    )
                };
                plane.copy_from_slice(&drained);
            }

            let frame_pts = Some(*encoder_pts_offset);
            enc_frame.set_pts(frame_pts);
            *encoder_pts_offset += aac_frame_size as i64;

            apply_noise(&mut enc_frame, censored, enc_time_base, src_rate)?;

            encoder.send_frame(&enc_frame)?;
            drain_encoder_packets(encoder, octx, audio_stream_index, enc_time_base, ost_tb)?;
        }
        Ok(())
    }

    fn drain_decode_frames(
        decoder: &mut ffmpeg::codec::decoder::audio::Audio,
        resampler: &mut ffmpeg::software::resampling::context::Context,
        channel_bufs: &mut Vec<Vec<f32>>,
        num_channels: usize,
        verbose: bool,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let mut frame = ffmpeg::frame::Audio::empty();
        loop {
            match decoder.receive_frame(&mut frame) {
                Ok(()) => {
                    let mut resampled = ffmpeg::frame::Audio::empty();
                    resampler.run(&frame, &mut resampled)?;
                    let sample_count = resampled.samples();
                    for ch in 0..num_channels {
                        let data = resampled.data(ch);
                        let samples = unsafe {
                            std::slice::from_raw_parts(data.as_ptr() as *const f32, sample_count)
                        };
                        channel_bufs[ch].extend_from_slice(samples);
                    }
                }
                Err(ffmpeg::Error::Eof) => break,
                Err(e) => {
                    if verbose {
                        eprintln!("Warning: decoder error during drain: {:?}", e);
                    }
                    break;
                }
            }
        }
        Ok(())
    }

    if verbose {
        eprintln!("Processing packets (selected audio will be re-encoded, others stream-copied)");
    }

    for (stream, mut packet) in ictx.packets() {
        let ist_index = stream.index();

        if ist_index == audio_stream_index {
            decoder.send_packet(&packet)?;
            drain_decode_frames(
                &mut decoder, &mut resampler, &mut channel_bufs, num_channels, verbose,
            )?;
            flush_buffers(
                &mut channel_bufs, &mut encoder, &mut octx, audio_stream_index, enc_time_base,
                src_layout, dst_format, src_rate, aac_frame_size,
                &mut encoder_pts_offset, censored, audio_ost_time_base,
            )?;
        } else {
            let ost = octx.stream(ist_index).ok_or("output stream not found")?;
            packet.rescale_ts(ist_time_bases[ist_index], ost.time_base());
            packet.set_position(-1);
            packet.set_stream(ist_index);
            packet.write_interleaved(&mut octx)?;
        }
    }

    if verbose {
        eprintln!("Flushing decoder");
    }
    decoder.send_packet(&ffmpeg::packet::Packet::empty())?;
    drain_decode_frames(
        &mut decoder, &mut resampler, &mut channel_bufs, num_channels, verbose,
    )?;

    if verbose {
        eprintln!("Flushing resampler");
    }
    loop {
        let mut dst = ffmpeg::frame::Audio::empty();
        unsafe { dst.alloc(dst_format, 4096, src_layout); }
        let delay = resampler.flush(&mut dst)?;
        if delay.is_none() {
            break;
        }
        for ch in 0..num_channels {
            let data = dst.data(ch);
            let sample_count = dst.samples();
            let samples = unsafe {
                std::slice::from_raw_parts(data.as_ptr() as *const f32, sample_count)
            };
            channel_bufs[ch].extend_from_slice(samples);
        }
    }

    if channel_bufs[0].len() >= aac_frame_size {
        flush_buffers(
            &mut channel_bufs, &mut encoder, &mut octx, audio_stream_index, enc_time_base,
            src_layout, dst_format, src_rate, aac_frame_size,
            &mut encoder_pts_offset, censored, audio_ost_time_base,
        )?;
    }
    if channel_bufs[0].len() > 0 {
        if verbose {
            eprintln!("Padding trailing {} samples to AAC frame size {}", channel_bufs[0].len(), aac_frame_size);
        }
        for ch in 0..num_channels {
            channel_bufs[ch].resize(aac_frame_size, 0.0f32);
        }
    }

    flush_buffers(
        &mut channel_bufs, &mut encoder, &mut octx, audio_stream_index, enc_time_base,
        src_layout, dst_format, src_rate, aac_frame_size,
        &mut encoder_pts_offset, censored, audio_ost_time_base,
    )?;

    if verbose {
        eprintln!("Flushing encoder");
    }
    flush_encoder(&mut encoder, &mut octx, audio_stream_index, enc_time_base, audio_ost_time_base)?;

    if verbose {
        eprintln!("Writing trailer");
    }
    octx.write_trailer()?;

    if verbose {
        eprintln!("Encoder wrote {} samples per channel", encoder_pts_offset);
    }

    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    ffmpeg::init()?;

    let args = Args::parse();

    let input_path = std::path::Path::new(&args.input);
    if !input_path.exists() {
        return Err(format!("Input file does not exist: {}", args.input).into());
    }

    let mut ictx = ffmpeg::format::input(&args.input)?;

    let nb_streams = ictx.nb_streams() as usize;
    if args.audio >= nb_streams {
        let available: Vec<String> = ictx
            .streams()
            .map(|s| {
                format!(
                    "  [{}] {:?}",
                    s.index(),
                    s.parameters().medium()
                )
            })
            .collect();
        return Err(format!(
            "Audio stream index {} does not exist. Available streams:\n{}",
            args.audio,
            available.join("\n")
        )
        .into());
    }

    let audio_stream = ictx.stream(args.audio).ok_or("audio stream not found")?;
    if audio_stream.parameters().medium() != ffmpeg::media::Type::Audio {
        return Err(format!(
            "Stream {} is not an audio stream (type: {:?})",
            args.audio,
            audio_stream.parameters().medium()
        )
        .into());
    }

    let output_path = std::path::Path::new(&args.output);
    if let Some(parent) = output_path.parent() {
        if !parent.as_os_str().is_empty() && !parent.exists() {
            return Err(format!("Output directory does not exist: {}", parent.display()).into());
        }
    }
    if output_path.exists() && output_path.metadata()?.permissions().readonly() {
        return Err(format!("Output file is not writable: {}", args.output).into());
    }

    let model_path = ensure_model(&args.model_name, &args.model_repo)?;
    println!("Using model: {}", model_path.display());

    let whisper_ctx = whisper_rs::WhisperContext::new_with_params(
        &model_path,
        whisper_rs::WhisperContextParameters::default(),
    )?;
    let mut whisper_state = whisper_ctx.create_state()?;

    let pcm_buffer = extract_audio(&mut ictx, args.audio, args.verbose)?;

    let censored = transcribe(&pcm_buffer, &mut whisper_state, args.verbose)?;

    if args.verbose {
        for pos in &censored {
            eprintln!("CENSORED {} {}:{}", pos.word, pos.start_ms, pos.end_ms);
        }
    }

    if args.verbose {
        eprintln!("Opening fresh input context for encoding pass");
    }
    drop(ictx);

    encode_and_mux(&args.input, &args.output, args.audio, &censored, args.verbose)?;

    println!("Processed {} -> {} (censored {} words)", args.input, args.output, censored.len());

    Ok(())
}

fn model_cache_dir() -> Result<PathBuf, Box<dyn std::error::Error>> {
    let home = dirs::home_dir().ok_or("Could not determine home directory")?;
    Ok(home.join(".cache").join("whisper"))
}

fn ensure_model(name: &str, repo: &str) -> Result<PathBuf, Box<dyn std::error::Error>> {
    let cache_dir = model_cache_dir()?;
    std::fs::create_dir_all(&cache_dir)?;

    let model_path = cache_dir.join(name);

    if model_path.exists() {
        let metadata = model_path.metadata()?;
        if metadata.len() > 0 {
            return Ok(model_path);
        }
    }

    let url = format!("https://huggingface.co/{}/resolve/main/{}", repo, name);
    let temp_path = cache_dir.join(format!("{}.part", name));

    println!("Downloading model from {} ...", url);

    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(3600))
        .build()
        .map_err(|e| format!("Failed to build HTTP client: {}", e))?;
    let response = client.get(&url).send().map_err(|e| {
        let _ = std::fs::remove_file(&temp_path);
        format!("Failed to download model from {}: {}", url, e)
    })?;

    if !response.status().is_success() {
        let _ = std::fs::remove_file(&temp_path);
        return Err(format!(
            "Failed to download model from {} (HTTP {})",
            url,
            response.status(),
        )
        .into());
    }

    let bytes = response.bytes()?;
    std::fs::write(&temp_path, &bytes)?;
    std::fs::rename(&temp_path, &model_path)?;
    println!("Model cached at {}", model_path.display());
    Ok(model_path)
}
