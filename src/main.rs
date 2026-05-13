use clap::Parser;
use ffmpeg_next as ffmpeg;
use ffmpeg_next::Rational;
use std::path::PathBuf;

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
        default_value = "ggml-tiny.en.bin",
        help = "Whisper model filename to use from the Hugging Face repo"
    )]
    model_name: String,

    #[arg(
        long,
        default_value = "ggerganov/whisper.cpp",
        help = "Hugging Face repo to download the model from"
    )]
    model_repo: String,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    ffmpeg::init()?;

    let args = Args::parse();

    let model_path = ensure_model(&args.model_name, &args.model_repo)?;
    println!("Using model: {}", model_path.display());

    passthrough(&args.input, &args.output)?;

    Ok(())
}

fn model_cache_dir() -> PathBuf {
    let home = std::env::var("HOME").expect("HOME environment variable not set");
    PathBuf::from(home).join(".cache").join("whisper")
}

fn ensure_model(name: &str, repo: &str) -> Result<PathBuf, Box<dyn std::error::Error>> {
    let cache_dir = model_cache_dir();
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

    let response = reqwest::blocking::get(&url).map_err(|e| {
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

fn passthrough(input_path: &str, output_path: &str) -> Result<(), Box<dyn std::error::Error>> {
    let mut ictx = ffmpeg::format::input(&input_path)?;
    let mut octx = ffmpeg::format::output(&output_path)?;

    let nb_streams = ictx.nb_streams() as usize;
    let mut ist_time_bases = vec![Rational(0, 1); nb_streams];

    for (ist_index, ist) in ictx.streams().enumerate() {
        ist_time_bases[ist_index] = ist.time_base();
        let mut ost = octx.add_stream(ffmpeg::encoder::find(ffmpeg::codec::Id::None))?;
        ost.set_parameters(ist.parameters());
        unsafe {
            (*ost.parameters().as_mut_ptr()).codec_tag = 0;
        }
    }

    octx.set_metadata(ictx.metadata().to_owned());
    octx.write_header()?;

    for (stream, mut packet) in ictx.packets() {
        let ist_index = stream.index();
        let ost = octx.stream(ist_index).ok_or("output stream not found")?;
        packet.rescale_ts(ist_time_bases[ist_index], ost.time_base());
        packet.set_position(-1);
        packet.set_stream(ist_index);
        packet.write_interleaved(&mut octx)?;
    }

    octx.write_trailer()?;

    println!("Copied all streams from {} to {}", input_path, output_path);

    Ok(())
}
