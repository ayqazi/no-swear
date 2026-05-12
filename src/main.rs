use clap::Parser;

#[derive(Parser, Debug)]
struct Args {
    #[arg(help = "Path to input media file")]
    input: String,

    #[arg(help = "Path to output media file")]
    output: String,

    #[arg(long, short, help = "Audio stream index to censor (0-based)")]
    audio: usize,
}

fn main() {
    let args = Args::parse();
    println!("input: {}", args.input);
    println!("output: {}", args.output);
    println!("audio stream: {}", args.audio);
}
