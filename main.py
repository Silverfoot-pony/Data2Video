from PIL import Image
import argparse
import binascii
import os
import subprocess
from shutil import rmtree
from concurrent.futures import ProcessPoolExecutor

four_k = (3840, 2160)
HD = (1920, 1080)

# encoding: 0 is black pixel (luma=0), 1 is white pixel (luma=255)


# ── Module-level helpers (must be top-level for ProcessPoolExecutor pickling) ──

def _write_frame(task):
    bits_chunk, fname, reso, block_size = task
    pixels = bits_2_pixels(bits_chunk, block_size=block_size, reso=reso)
    img = Image.new("L", reso)
    img.putdata(pixels)
    img.save(fname)
    return fname


def _read_frame(task):
    fname, block_size = task
    im = Image.open(fname).convert("L")
    pixels = list(im.get_flattened_data())
    return pixels_2_bits(pixels, block_size=block_size, reso=im.size)


# ── WebM video assembly / disassembly ────────────────────────────────────────

def frames_to_webm(temp_folder, output_path, fps=10):
    """Assemble numbered PNGs in temp_folder into a VP9-lossless WebM video."""
    pattern = os.path.join(temp_folder, "frame-%04d.png")
    # yuv420p forces VP9 Profile 0 — the standard profile supported by every
    # media player. Without it, ffmpeg picks gbrp from grayscale input, which
    # almost nothing can render.
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-framerate", str(fps),
            "-i", pattern,
            "-c:v", "libvpx-vp9",
            "-lossless", "1",
            "-b:v", "0",
            "-pix_fmt", "yuv420p",
            "-row-mt", "1",
            output_path,
        ],
        check=True,
    )
    return output_path


def webm_to_frames(src, temp_folder):
    """Extract all frames from a WebM file into numbered PNGs in temp_folder."""
    pattern = os.path.join(temp_folder, "frame-%04d.png")
    subprocess.run(
        ["ffmpeg", "-y", "-i", src, pattern],
        check=True,
    )
    frames = sorted(
        [os.path.join(temp_folder, f) for f in os.listdir(temp_folder) if f.endswith(".png")],
        key=lambda p: int(os.path.basename(p)[len("frame-") : -len(".png")]),
    )
    return frames


# ── Low-level pixel / file helpers ───────────────────────────────────────────

def pixels_2_png(pixels, fname, reso=four_k):
    img = Image.new("L", reso)
    img.putdata(pixels)
    img.save(fname)
    print("pixels_2_png: Saved %d pixels to %s" % (len(pixels), fname))


def png_2_pixels(fname):
    pixel_list = list(Image.open(fname).convert("L").get_flattened_data())
    print("png_2_pixels: Read %d pixels from %s" % (len(pixel_list), fname))
    return pixel_list


def bits_2_file(bits, fname):
    with open(fname, "wb") as f:
        idx = 0
        while idx < len(bits):
            f.write(bytes([int("".join(bits[idx : idx + 8]), 2)]))
            idx += 8
    print("bits_2_file: Wrote %d bits to %s" % (len(bits), fname))


def file_2_bits(fname):
    bits = []
    with open(fname, "rb") as f:
        byte = f.read(1)
        while byte:
            bits.extend(list(bin(byte[0])[2:].zfill(8)))
            byte = f.read(1)
    print("file_2_bits: Read %d bits from %s" % (len(bits), fname))
    return bits


def bits_2_pixels(bits, block_size=1, reso=four_k):
    """Convert a list of '0'/'1' chars to a flat grayscale pixel list.

    Each bit becomes a block_size×block_size square so the signal survives
    DCT-based compression (H.264/VP9 operate on 8×8 blocks minimum).
    """
    width, height = reso
    block_cols = width // block_size
    block_rows = height // block_size
    pixels = []
    for br in range(block_rows):
        row = []
        for bc in range(block_cols):
            bit_idx = br * block_cols + bc
            val = 255 if bit_idx < len(bits) and bits[bit_idx] == "1" else 0
            row += [val] * block_size
        for _ in range(block_size):
            pixels += row
    print("bits_2_pixels: %d bits → %d pixels (block_size=%d)" % (len(bits), len(pixels), block_size))
    return pixels


def pixels_2_bits(pixels, block_size=1, reso=four_k):
    """Recover bits from a flat grayscale pixel list using centre-of-block sampling.

    A luma threshold of 128 handles minor compression shifts without needing
    exact black/white values.
    """
    width, height = reso
    block_cols = width // block_size
    block_rows = height // block_size
    bits = []
    for br in range(block_rows):
        for bc in range(block_cols):
            cy = br * block_size + block_size // 2
            cx = bc * block_size + block_size // 2
            luma = pixels[cy * width + cx]
            if isinstance(luma, tuple):  # RGB fallback
                luma = int(0.299 * luma[0] + 0.587 * luma[1] + 0.114 * luma[2])
            bits.append("0" if luma < 128 else "1")
    print("pixels_2_bits: %d pixels → %d bits (block_size=%d)" % (len(pixels), len(bits), block_size))
    return bits


# ── Header encoding / decoding ────────────────────────────────────────────────

def add_header(bits, fname):
    fname_bitstr = bin(int(binascii.hexlify(fname.encode()), 16))
    print("add_header: fname_bitstr length %d" % len(fname_bitstr))

    fname_len_bits = "{0:b}".format(len(fname_bitstr) - 2).zfill(16)
    header = list(fname_len_bits + fname_bitstr[2:])

    payload_len_bits = "{0:b}".format(len(bits)).zfill(64)
    print("bits in payload: %d" % len(bits))

    header.extend(list(payload_len_bits))
    header.extend(bits)
    return header


def decode_header(bits):
    def decode_binary_string(s):
        return "".join(chr(int(s[i * 8 : i * 8 + 8], 2)) for i in range(len(s) // 8))

    fname_length = int("".join(bits[:16]), 2)
    print("decode_header: fname_length: %d" % fname_length)

    fname = decode_binary_string("0" + "".join(bits[16 : 16 + fname_length]))
    print("decode_header: fname: %s" % fname)

    payload_length = int("".join(bits[16 + fname_length : 16 + fname_length + 64]), 2)
    print("decode_header: payload_length: %d" % payload_length)

    return fname, bits[16 + fname_length + 64 : 16 + fname_length + 64 + payload_length]


# ── Utility ───────────────────────────────────────────────────────────────────

def test_bit_similarity(bits1, bits2):
    with open("bits.txt", "w") as f:
        f.write("".join(bits1) + "\n")
        f.write("".join(bits2) + "\n")
    if len(bits1) != len(bits2):
        print("Bit lengths are not the same!")
        return
    if any(b1 != b2 for b1, b2 in zip(bits1, bits2)):
        print("Bits are not the same!")
        return
    print("Bits are identical")


def clear_folder(path):
    try:
        rmtree(path)
    except Exception:
        print("WARNING: Could not remove %s" % path)
    for _ in range(10):
        try:
            os.mkdir(path)
            break
        except Exception:
            continue


# ── Main encode / decode ──────────────────────────────────────────────────────

def encode(src, res=four_k, block_size=1):
    bits = file_2_bits(src)
    bits = add_header(bits, src.split("/")[-1])

    bits_per_frame = (res[0] // block_size) * (res[1] // block_size)
    name_clean = src.split("/")[-1]
    clear_folder("temp")

    tasks = []
    for i in range((len(bits) + bits_per_frame - 1) // bits_per_frame):
        chunk = bits[i * bits_per_frame : (i + 1) * bits_per_frame]
        tasks.append((chunk, "temp/frame-%04d.png" % i, res, block_size))

    workers = min(len(tasks), os.cpu_count() or 1)
    print("encode: %d frames, %d workers, block_size=%d" % (len(tasks), workers, block_size))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for fname in pool.map(_write_frame, tasks):
            print("encode: wrote %s" % fname)

    out = name_clean + ".webm"
    print("encode: assembling %s …" % out)
    return frames_to_webm("temp", out)


def decode(src, block_size=1):
    clear_folder("temp")
    print("decode: extracting frames from %s …" % src)
    saved_frames = webm_to_frames(src, "temp")

    first = Image.open(saved_frames[0])
    res = first.size
    first.close()

    print("decode: %d frames at %dx%d" % (len(saved_frames), res[0], res[1]))

    read_tasks = [(f, block_size) for f in saved_frames]
    workers = min(len(saved_frames), os.cpu_count() or 1)
    print("decode: %d workers, block_size=%d" % (workers, block_size))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        bit_lists = list(pool.map(_read_frame, read_tasks))

    bits = [b for bl in bit_lists for b in bl]
    fname, bits = decode_header(bits)

    parts = fname.rsplit(".", 1)
    out = parts[0] + "-recovered." + parts[1] if len(parts) == 2 else fname + "-recovered"
    bits_2_file(bits, out)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Encode arbitrary files as lossless WebM videos and decode them back.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", metavar="command")

    enc = sub.add_parser("encode", help="encode a file into a lossless WebM video")
    enc.add_argument("file", help="source file to encode")
    enc.add_argument(
        "--block-size", type=int, default=8, metavar="N",
        help="encode each bit as an N×N pixel block (≥8 resists H.264/VP9 lossy re-encoding)",
    )
    enc.add_argument(
        "--resolution", choices=["4k", "hd"], default="4k",
        help="output frame resolution",
    )

    dec = sub.add_parser("decode", help="decode a WebM video back to the original file")
    dec.add_argument("file", help=".webm file to decode")
    dec.add_argument(
        "--block-size", type=int, default=8, metavar="N",
        help="block size used during encoding",
    )

    args = parser.parse_args()

    if args.cmd == "encode":
        encode(args.file, res=four_k if args.resolution == "4k" else HD, block_size=args.block_size)
    elif args.cmd == "decode":
        decode(args.file, block_size=args.block_size)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
