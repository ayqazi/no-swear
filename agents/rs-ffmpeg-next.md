# ffmpeg-next v8.1.0 — Compressed API Reference

Safe FFmpeg wrapper (FFmpeg 3.4–8.0). Fork of abandoned `ffmpeg` crate. Wraps `ffmpeg-sys-next`.
**Init**: `ffmpeg_next::init().unwrap()` | **Error**: `ffmpeg_next::Error` | **FFI**: `ffmpeg_next::ffi`

## Top-Level Re-exports
`ChannelLayout` (util::channel_layout), `Dictionary`/`Mut`/`Ref` (util::dictionary), `Error` (util::error), `Frame` (util::frame), `Rational` (util::rational), `Packet` (codec::packet::packet), `Stream`/`StreamMut` (format::stream), `Codec` (codec::codec), `Format` (format::format), `Filter` (filter::filter), `Discard` (codec::discard).

## Module: `format`
**Top fns**: `format::input<P: AsRef<Path>>(p)`, `input_with_dictionary`, `input_with_interrupt`, `output`, `output_with`, `open`, `open_with`, `configuration()`, `license()`, `version()`.

**`format::context::Context`** (enum: `Input`/`Output` via Deref):
- **Input**: `.streams()`, `.streams().best(media::Type)`, `.packets()` iter `(Stream,Packet)`, `.seek(ts,range)`, `.metadata()`, `.nb_streams()`, `.duration()` (i64, AV_TIME_BASE), `.format()`, `.probe_score()`, `.pause()`/`.play()`, `input::dump(&ictx,0,Some(&fname))`.
- **Output**: `.add_stream(encoder) -> StreamMut`, `.add_stream_with(&Context)`, `.add_chapter(id,tb,start,end,title)`, `.write_header()`/`_with(dict)`, `.write_trailer()`, `.set_metadata(dict)`, `.stream(index)`, `output::dump(&octx,0,Some(&fname))`.

**`format::stream::Stream<'a>`**: `.index()`, `.id()`, `.parameters()`, `.time_base()`, `.start_time()`, `.duration()`, `.frames()`, `.avg_frame_rate()`, `.rate()`, `.disposition()`, `.discard()`, `.side_data()`.
**`StreamMut`**: same + `.set_parameters(&enc)`, `.set_time_base(R)`.
**`format::format::Input/Output`**: `.codec(path,type)`, `.flags()`/`.contains(Flags::GLOBAL_HEADER)`.

## Module: `codec`
**`codec::context::Context`**: from `from_parameters(stream.parameters())?` or `new_with_codec(codec)?`. Methods: `.decoder()`, `.encoder()`, `.medium()`, `.id()`, `.codec()`, `.set_flags/set_time_base/set_frame_rate/set_parameters`, `.time_base()`, `.frame_rate()`, `.threading()`, `.as_ptr()`/`.as_mut_ptr()`.

**`codec::Parameters`**: `.medium()`, `.id()`.
**`codec::Id`**: `None`, `H264`, `AAC`, `MP3`, `PCM_S16LE`, etc.

**`codec::decoder::Decoder`** (newtype Context): `.video()`, `.audio()`, `.subtitle()`, `.open()`/`_as(codec)`/`_as_with(codec,dict)`. Opened (→Context via Deref): `.send_packet(&pkt)`, `.send_eof()`, `.receive_frame(&mut Frame)`. Video: `.width/height/format(Pixel)/aspect_ratio/has_b_frames/color_space/color_range`. Audio: `.rate/channels/format(Sample)/channel_layout/frame_size/align`. Common: `.bit_rate/max_bit_rate/delay`.

**`codec::encoder::Encoder`**: `.video()`, `.audio()`, `.open_as(codec)`/`_with(dict)`. Opened (→Context via Deref): `.send_frame(&f)`, `.send_eof()`, `.receive_packet(&mut Packet)`. Setters: Video — `.set_width/height/format(Pixel)/aspect_ratio/frame_rate`. Audio — `.set_rate(i32)/channel_layout/format(Sample)/bit_rate/max_bit_rate`. `.frame_size()`.

**`codec::packet::Packet`**: `empty()`, `new(sz)`, `copy(&[u8])`. Methods: `.pts/set_pts`, `.dts/set_dts`, `.stream/set_stream`, `.size`, `.duration/set_duration`, `.position/set_position`, `.flags/set_flags`, `.is_key/is_corrupt`, `.time_base/set_time_base`, `.rescale_ts(src,dst)`, `.data/data_mut`, `.side_data()`, `.write_interleaved(&mut Output)`, `.write(&mut Output)`, `.read(&mut Input)`.

## Module: `util`
**`util::frame::Frame`**: `empty()`, `frame::Video::empty()`, `frame::Audio::empty()`. `.pts/set_pts`, `.timestamp()`, `.is_key/is_corrupt`, `.quality/flags`, `.metadata/set_metadata`, `.side_data(kind)/new_side_data/remove_side_data`, `.packet()`. `From<Frame> for Video/Audio`. Video: `.width/height/format/data(plane)/stride/linesize`. Audio: `.data/stride/samples/sample_rate/channels/channel_layout/format`.

**`util::rational::Rational(i32,i32)`**: `From<(i32,i32)>`, `Into<(i32,i32)>`, arithmetic `+ - * /`, `* f64`, `From<f64>`. `rescale::TIME_BASE = (1,1_000_000)`.

**`util::dictionary`**: `Dictionary::new()`, `.set(k,v)`, `.get(k)`, `.iter()` — `(k,v)`. `DictionaryRef`, `DictionaryMut`. Macro: `dict!{k=>v}`.

**`util::channel_layout`**: `STEREO`, `MONO`, `.channels()`, `.bits()`, `.best(n)`.
**`util::format::pixel::Pixel`**: `RGB24`, `YUV420P`, `NV12`, `.name()`.
**`util::format::sample::Sample`**: `S16`, `FLT`, `FLTP`, `.name()`.
**`util::error::Error`**: `StreamNotFound`, `InvalidData`, impl `std::error::Error`.
**`util::media::Type`**: `Video`, `Audio`, `Subtitle`, `Data`.
**`util::picture::Type`**: `None`, `I`, `P`, `B`.
**`util::frame::flag::Flags`**: frame flags.
**`util::log`**: `log::set_level(Level::Warning)`.
**`util::mathematics::rescale::Rescale`**: `.rescale((n,d),tb)`, `rescale::TIME_BASE=(1,AV_TIME_BASE)`, `Rounding::{Pass,Inf,Down,Up,NearInf,PassMinMax,Near}`.
**Other**: `util::option`, `color`, `chroma`, `interrupt`, `time`, `range`.

## Module: `filter`
**`filter::Graph`**: `new()`, `.add(&filter,name,args)`, `.get(name) -> Context`, `.output(n,p)?.input(n,p)?.parse(spec)`, `.validate()`, `.dump()`.
**`filter::context::Context`**: `.source() -> Source` (`.add(&frame)`, `.flush()`), `.sink() -> Sink` (`.frame(&mut Frame)`). Setters: `.set_sample_format`, `.set_channel_layout`, `.set_sample_rate`, `.set_frame_size`.
**`filter::find(name)`**: `"abuffer"/"abuffersink"` (audio), `"buffer"/"buffersink"` (video).
```rust
let mut g = filter::Graph::new();
g.add(&filter::find("abuffer")?, "in",
    &format!("time_base={}:sample_rate={}:sample_fmt={}:channel_layout=0x{:x}",tb,rate,fmt.name(),ch.bits()))?;
g.add(&filter::find("abuffersink")?, "out", "")?;
g.output("in",0)?.input("out",0)?.parse(filter_spec)?;
g.validate()?;
```

## Module: `software`
**`resampling::Context`**: audio rate/format/layout conversion. `Context::get(fmt_in,fmt_out,ch_in,ch_out,rate_in,rate_out)?`, `.run(&in,&mut out)?`.
**`scaling::Context`**: video pixel/resolution conversion. `Context::get(fmt_in,w_in,h_in,fmt_out,w_out,h_out,Flags::BILINEAR)?`, `.run(&in,&mut out)?`.
**Convenience**: `software::converter`, `resampler`, `scaler`.

## Module: `device`
Device I/O (rarely needed). `device::register_all()`, `device::input::Input`, `output::Output`, `Info`.
