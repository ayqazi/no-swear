use clap::Parser;
use ffmpeg_next as ffmpeg;
use ffmpeg_next::Rational;

#[derive(Parser, Debug)]
struct Args {
    #[arg(help = "Path to input media file")]
    input: String,

    #[arg(help = "Path to output media file")]
    output: String,

    #[arg(long, short, help = "Audio stream index to censor (0-based)")]
    audio: usize,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    ffmpeg::init()?;

    let args = Args::parse();

    passthrough(&args.input, &args.output)?;

    Ok(())
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