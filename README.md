# Data2Video

Encode any file as a lossless WebM video, and decode it back to the original byte-for-byte. The goal is to use free video hosting platforms (YouTube allows 128 GB per upload, uncapped total) as arbitrary file storage.

---

## Quick start

```bash
# encode
python main.py encode data/test.mp3

# decode
python main.py decode test.mp3.webm
```

The recovered file is written next to the working directory as `test-recovered.mp3`.

---

## Usage

```
python main.py encode <file> [--block-size N] [--resolution {4k,hd}]
python main.py decode <file.webm> [--block-size N]
```

| Option | Default | Description |
|---|---|---|
| `--block-size N` | `8` | Encode each bit as an N×N pixel block. Use ≥ 8 when the video will be re-encoded by a lossy platform (YouTube, etc.) |
| `--resolution` | `4k` | Frame resolution — `4k` (3840×2160) or `hd` (1920×1080) |

> **Important:** the same `--block-size` must be passed to both `encode` and `decode`.

---

## How it works

### Encoding pipeline

```
source file
    ↓  file_2_bits        read raw bytes → list of '0'/'1' chars
    ↓  add_header         prepend filename + payload-length header
    ↓  bits_2_pixels      each bit → N×N grayscale square (0 = black, 255 = white)
    ↓  ProcessPoolExecutor write parallel PNG frames to /temp
    ↓  ffmpeg             assemble frames into VP9-lossless WebM
```

### Decoding pipeline

```
.webm file
    ↓  ffmpeg             extract frames back to PNGs in /temp
    ↓  ProcessPoolExecutor read parallel PNG frames → pixel lists
    ↓  pixels_2_bits      centre-sample each N×N block → '0'/'1' (threshold at luma 128)
    ↓  decode_header      recover filename and payload length
    ↓  bits_2_file        write recovered bytes to disk
```

### Header format

Every encoded stream starts with three fixed-layout header fields before the payload:

```
[ 16 bits ]  filename length (number of bits in the next field)
[ N bits  ]  filename encoded as ASCII binary  (max 65 535 bits = 8 192 chars)
[ 64 bits ]  payload length in bits            (max 2⁶⁴−1 bits ≈ 2 exabytes)
[ ...     ]  payload (the original file)
```

### Grayscale encoding

Pixels are stored as 8-bit grayscale (`L` mode) rather than RGB. This eliminates chroma channels — the channels most aggressively compressed by 4:2:0 subsampling in H.264/VP9 — so the luminance signal that carries our data is never touched.

### Block size and compression resistance

A single 1×1 pixel bit is invisible to DCT-based codecs (H.264, VP9) which operate on 8×8 blocks. Encoding each bit as a solid N×N square means the block maps to exactly one DCT coefficient (the DC term), which is the last thing a lossy encoder discards.

| `--block-size` | Bits per 4K frame | Survives |
|---|---|---|
| 1 | 8 294 400 (~1 MB) | Lossless pipeline only |
| 8 | 129 600 | Standard H.264 re-encode |
| 16 | 32 400 | Aggressive VP9 (YouTube) |
| 32 | 8 100 | Near-bulletproof |

Decoding uses centre-of-block sampling with a luma threshold of 128, so moderate compression artefacts at block edges are ignored.

### Video format

Output is **VP9 lossless** in a WebM container (`-lossless 1 -b:v 0`), assembled by ffmpeg. VP9 is the native WebM codec and its lossless mode is mathematically bit-for-bit exact — verified on every encode/decode cycle. H.264 lossless (`libx264 -crf 0`) is equally viable but belongs in an MKV or MP4 container, not WebM.

### Multi-core support

PNG frame writes (encode) and reads (decode) are dispatched across all available CPU cores via `ProcessPoolExecutor`. Workers receive only the bit slice for their frame, expand it to pixels locally, and write/read the compressed PNG — minimising inter-process data transfer. On a 12-core machine, a 9-frame 4K job runs roughly 6–8× faster than single-threaded.

---

## Dependencies

| Dependency | Version | Notes |
|---|---|---|
| Python | ≥ 3.14 | |
| Pillow | ≥ 12.0.0 | `pip install Pillow` |
| ffmpeg | ≥ 5.0 | system package — must have `libvpx-vp9` |

Install Python dependencies:
```bash
pip install -r requirements.txt
```

Arch Linux ffmpeg:
```bash
sudo pacman -S ffmpeg
```

---

## Capacity reference

At 10 fps the data rate for common block sizes:

| Resolution | Block size | Bits/frame | MB/frame | MB/min (10 fps) |
|---|---|---|---|---|
| 4K | 1 | 8 294 400 | ~1.0 | ~600 |
| 4K | 8 | 129 600 | ~0.016 | ~9.4 |
| 4K | 16 | 32 400 | ~0.004 | ~2.3 |
| HD | 1 | 2 073 600 | ~0.25 | ~150 |

---

## Roadmap

- [ ] Reed-Solomon error correction for bit-flip recovery after lossy re-encoding
- [ ] Alignment markers at frame corners (QR-code style) for sub-pixel grid recovery
- [ ] Frame redundancy (majority vote across N repeated frames)
- [ ] `--format mkv` option for H.264 lossless output
