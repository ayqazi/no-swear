use clap::Parser;
use ffmpeg_next as ffmpeg;
use ffmpeg_next::Rational;
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

    let mut censored = Vec::new();
    let swear_words = ["fuck", "shit", "damn", "bitch", "dick", "cunt", "bastard", "asshole"];
    let n_segments = state.full_n_segments();
    for i in 0..n_segments {
        let segment = state.get_segment(i).ok_or("failed to get segment")?;
        let text = segment.to_str()?.to_lowercase();
        for &word in &swear_words {
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
        eprintln!("Found {} swear word occurrences to censor", censored.len());
    }

    Ok(censored)
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
        eprintln!("Seeking input back to start for passthrough");
    }
    ictx.seek(0, ..0)?;

    passthrough(ictx, &args.output)?;

    println!("Copied all streams from {} to {}", args.input, args.output);

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

fn passthrough(mut ictx: ffmpeg::format::context::Input, output_path: &str) -> Result<(), Box<dyn std::error::Error>> {
    let mut octx = ffmpeg::format::output(&output_path)?;

    let nb_streams = ictx.nb_streams() as usize;
    let mut ist_time_bases = vec![Rational(0, 1); nb_streams];

    for (ist_index, ist) in ictx.streams().enumerate() {
        ist_time_bases[ist_index] = ist.time_base();
        let mut ost = octx.add_stream(ffmpeg::encoder::find(ffmpeg::codec::Id::None))?;
        ost.set_parameters(ist.parameters());
        // Zero codec tag so libav auto-selects the correct tag for the output container
        unsafe {
            (*ost.parameters().as_mut_ptr()).codec_tag = 0;
        }
    }

    octx.set_metadata(ictx.metadata().to_owned());
    octx.write_header()?;

    let result = (|| -> Result<(), Box<dyn std::error::Error>> {
        for (stream, mut packet) in ictx.packets() {
            let ist_index = stream.index();
            let ost = octx.stream(ist_index).ok_or("output stream not found")?;
            packet.rescale_ts(ist_time_bases[ist_index], ost.time_base());
            packet.set_position(-1);
            packet.set_stream(ist_index);
            packet.write_interleaved(&mut octx)?;
        }
        Ok(())
    })();

    octx.write_trailer()?;
    result
}
