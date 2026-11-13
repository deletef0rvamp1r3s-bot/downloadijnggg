import os
import json
import subprocess

TEMP_DIR = "/tmp/watermark_bot"
os.makedirs(TEMP_DIR, exist_ok=True)

subprocess.run(["wget", "-q", "-O", f"{TEMP_DIR}/Poppins-Medium.ttf",
    "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Medium.ttf"])
subprocess.run(["wget", "-q", "-O", f"{TEMP_DIR}/Poppins-Bold.ttf",
    "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Bold.ttf"])

import asyncio
import random
import uuid
import math
import time
from pathlib import Path

import numpy as np
import cv2
import nest_asyncio

from PIL import Image, ImageDraw, ImageFont

nest_asyncio.apply()

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

if not API_ID or not API_HASH or not BOT_TOKEN:
    raise ValueError("⚠️ عبّئ المتغيرات")

ENCODE_CRF = 20
ENCODE_PRESET = "superfast"
STRONG_COUNT = 50
FLICK_COUNT = 0
GHOST_COUNT = 0
MAX_QUEUE = 6

PROGRESS_INTERVAL = 2.0

for f in Path(TEMP_DIR).glob("*.session*"):
    try:
        f.unlink()
    except Exception:
        pass


def probe_video(path):
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return json.loads(r.stdout)
    except Exception:
        pass
    return None


def read_geometry(pr):
    out = {"w0": 720, "h0": 1280, "sar_str": "1:1", "sar_val": 1.0, "rot": 0, "fps": 30.0, "total": 0, "bitrate": 0}
    if pr is None:
        return out
    vs = None
    for s in pr.get("streams", []):
        if s.get("codec_type") == "video":
            vs = s; break
    if vs is None:
        return out
    w0 = int(vs.get("width", 720)); h0 = int(vs.get("height", 1280))
    sar_str = vs.get("sample_aspect_ratio", "1:1") or "1:1"
    sar_val = 1.0
    if ":" in sar_str:
        try:
            n, d = sar_str.split(":")
            sar_val = float(n) / float(d) if float(d) else 1.0
        except Exception:
            pass
    rot = 0
    for sd in vs.get("side_data_list", []):
        if "rotation" in sd:
            try:
                rot = int(float(sd["rotation"])) % 360
            except Exception:
                pass
            break
    fps = 30.0
    rf = vs.get("r_frame_rate", "")
    if "/" in str(rf):
        try:
            n, d = rf.split("/")
            fps = float(n) / float(d) if float(d) else 30.0
        except Exception:
            pass
    total = 0
    nb = vs.get("nb_frames")
    try:
        total = int(nb) if nb and nb != "N/A" else 0
    except Exception:
        pass
    if total <= 0:
        try:
            dur = float(pr.get("format", {}).get("duration", 0))
            total = int(dur * fps) if dur > 0 else 0
        except Exception:
            pass
    br = 0
    try:
        br = int(pr.get("format", {}).get("bit_rate", 0) or 0)
    except Exception:
        pass
    out.update({"w0": w0, "h0": h0, "sar_str": sar_str, "sar_val": sar_val,
                "rot": rot, "fps": fps, "total": total, "bitrate": br})
    return out


def first_frame_sig(path):
    png = path + ".first.png"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path, "-frames:v", "1", "-y", png]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not os.path.exists(png):
        return None, None
    img = cv2.imread(png)
    try:
        os.remove(png)
    except Exception:
        pass
    if img is None:
        return None, None
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (64, 64)).astype(np.float32)
    g = (g - g.mean()) / (g.std() + 1e-6)
    return (img.shape[1], img.shape[0]), g


def sig_of(frame):
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (64, 64)).astype(np.float32)
    g = (g - g.mean()) / (g.std() + 1e-6)
    return g


def rot_frame(f, t):
    if t == 90: return cv2.rotate(f, cv2.ROTATE_90_CLOCKWISE)
    if t == 180: return cv2.rotate(f, cv2.ROTATE_180)
    if t == 270: return cv2.rotate(f, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return f


class PipeEncoder:
    def __init__(self, w, h, fps, out, audio_src, crf=20, preset="superfast", maxrate=None, bufsize=None):
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
               "-r", f"{fps:.6f}", "-i", "pipe:0"]
        if audio_src and os.path.exists(audio_src):
            cmd += ["-i", audio_src]
        cmd += ["-map", "0:v:0"]
        if audio_src and os.path.exists(audio_src):
            cmd += ["-map", "1:a:0?"]
        cmd += ["-c:v", "libx264", "-crf", str(crf), "-preset", preset,
                "-pix_fmt", "yuv420p", "-profile:v", "high", "-bf", "0", "-level", "4.0",
                "-vf", "setsar=1:1", "-video_track_timescale", "90000",
                "-g", str(max(30, int(fps * 2))),
                "-keyint_min", str(max(15, int(fps))),
                "-sc_threshold", "0",
                "-x264-params", "aq-mode=3:aq-strength=0.9:ref=2:me=dia:subme=2:trellis=0:threads=0"]
        if maxrate: cmd += ["-maxrate", maxrate]
        if bufsize: cmd += ["-bufsize", bufsize]
        if audio_src and os.path.exists(audio_src):
            cmd += ["-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "128k"]
        cmd += ["-movflags", "+faststart", "-shortest", "-y", out]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def alive(self): return self.proc.poll() is None

    def write(self, frame):
        try:
            self.proc.stdin.write(frame.tobytes()); return True
        except (BrokenPipeError, OSError):
            return False

    def close(self):
        try:
            if self.proc.stdin: self.proc.stdin.close()
            self.proc.wait(timeout=300)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


class FloatingWatermark:
    def __init__(self, video_path, output_path, audio_src=None):
        self.video_path = video_path
        self.output_path = output_path
        self.audio_src = audio_src
        g = read_geometry(probe_video(video_path))
        self.in_geo = g
        self.fps = g["fps"] if g["fps"] > 0 else 30.0
        self.src_bitrate = g["bitrate"]
        self.step = 2 if self.fps >= 50 else 1
        self.out_fps = self.fps / self.step

        self.dec = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-noautorotate",
             "-i", video_path, "-vf", "format=bgr24", "-f", "rawvideo", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.frame_bytes = g["w0"] * g["h0"] * 3
        self.coded_w, self.coded_h = g["w0"], g["h0"]
        first_buf = self._read_exact(self.frame_bytes)
        if first_buf is None:
            self._kill_dec()
            raise ValueError("❌ ما قدرت أقرأ أول فريم")
        coded0 = np.frombuffer(first_buf, dtype=np.uint8).reshape(g["h0"], g["w0"], 3)
        self._first_pending = first_buf

        in_dims, in_sig = first_frame_sig(video_path)
        self.calib_sim = 0.0
        self.rot_pick = 0
        self.sar_stretch = abs(g["sar_val"] - 1.0) > 0.001
        best = None
        for t in (0, 90, 180, 270):
            f = coded0
            if self.sar_stretch:
                f = cv2.resize(f, (int(round(g["w0"] * g["sar_val"])), g["h0"]), interpolation=cv2.INTER_LINEAR)
            f = rot_frame(f, t)
            dims = (f.shape[1], f.shape[0])
            if in_dims is not None and dims != in_dims:
                continue
            s = float((sig_of(f) * in_sig).mean()) if in_sig is not None else 1.0
            if best is None or s > best[1]:
                best = (t, s, dims)
        if best is None:
            dims = (g["h0"], g["w0"]) if g["rot"] in (90, 270) else (g["w0"], g["h0"])
            best = (g["rot"] % 360, 0.0, dims)
        self.rot_pick, self.calib_sim, dims = best[0], best[1], best[2]
        self.width, self.height = dims[0] + dims[0] % 2, dims[1] + dims[1] % 2
        self.in_png_dims = in_dims

        raw_total = g["total"] if g["total"] > 0 else 10 ** 9
        self.total_out = max(1, raw_total // self.step) if raw_total < 10 ** 9 else 0

        self.thumb_path = output_path + ".thumb.jpg"
        self._thumb_done = False
        print(f"✅ معايرة: rot={self.rot_pick} | {self.width}x{self.height} | fps {self.fps:.0f}→{self.out_fps:.0f}")

        br_m = self.src_bitrate / 1_000_000 if self.src_bitrate else 0
        cap_m = max(2.5, min(8.0, br_m * 2.2)) if br_m > 0 else 4.0
        self.maxrate = f"{cap_m:.1f}M"
        self.bufsize = f"{max(4, int(cap_m * 2.5))}M"
        self.enc = PipeEncoder(self.width, self.height, self.out_fps, output_path,
                               audio_src, 20, "superfast", self.maxrate, self.bufsize)
        self.text_variants = ["Tel:@vamp1r3s"]
        self.vip_text = "Telegram:@vamp1r3s"
        self.vip_vary = random.randint(1, 10**9)
        self.font_path = self._get_font_path()
        self.bold_font_path = self._get_bold_font_path()
        self.use_pil = (self.font_path is not None) or (self.bold_font_path is not None)
        self._font_cache = {}
        self._sprite_cache = {}
        self._halo_cache = {}
        base = min(self.width, self.height)
        old_min = max(14, int(base * 0.020))
        old_max = max(old_min + 4, int(base * 0.040))
        self.strong_min_font = max(12, int(old_min * 0.55))
        self.strong_max_font = max(self.strong_min_font + 4, int(old_max * 0.55))
        max_strong_w = int((self.width * 0.90) / max(10, len(self.text_variants[0]) * 0.60))
        self.strong_max_font = max(self.strong_min_font + 4, min(self.strong_max_font, max_strong_w))
        vip_size = max(self.strong_max_font + 6, int(base * 0.062))
        max_vip_w = int((self.width * 0.92) / max(10, len(self.vip_text) * 0.68))
        self.vip_font_size = max(self.strong_min_font + 6, min(vip_size, max_vip_w))
        if self.vip_font_size <= self.strong_max_font:
            self.vip_font_size = self.strong_max_font + 6
        self.grid_cols, self.grid_rows = 8, 9
        self._cell_queue, self._bottom_queue = [], []
        self.top_fade_margin = 80
        self._MAXH = int(max(self.vip_font_size, int(self.strong_max_font * 1.5)) * 2.6) + 80
        self._MAXW = min(self.width, 1600) + 80
        ii, jj = np.ogrid[0:self._MAXH, 0:self._MAXW]
        self._checker_base = (((ii + jj) & 1).astype(np.float32) * 2.0 - 1.0)
        b4 = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]],
                      dtype=np.float32) / 16.0 - 0.5
        self._bayer_big = np.tile(b4, (self._MAXH // 4 + 1, self._MAXW // 4 + 1))[:self._MAXH, :self._MAXW]
        self._grad_cache = {}
        self._frame_detail = self._frame_edges = self._frame_texture = self._frame_lum = None
        self._maps_frame = -10
        rng = np.random.RandomState(1337)
        bh = (self.height + 8 - 1) // 8
        bw = (self.width + 8 - 1) // 8
        blocks = rng.choice([-1.0, 1.0], size=(bh, bw)).astype(np.float32)
        self._parity1 = np.kron(blocks, np.ones((8, 8), dtype=np.float32))[:self.height, :self.width]

        seed = 0
        for ch in "Telegram:@vamp1r3s":
            seed = (seed * 31 + ord(ch)) % (2**31)
        ssrng = np.random.RandomState(seed)
        field = np.zeros((self.height, self.width), dtype=np.float32)
        for k in range(8):
            bit = (seed >> k) & 1
            coef = 1.0 if bit else -1.0
            bs_h = max(8, self.height // (20 + 6 * k))
            bs_w = max(8, self.width // (20 + 6 * k))
            basis = ssrng.normal(size=(bs_h, bs_w)).astype(np.float32)
            basis = cv2.resize(basis, (self.width, self.height), interpolation=cv2.INTER_CUBIC)
            basis = basis / (np.std(basis) + 1e-6)
            field += coef * basis
        self._ss_field = field / (np.std(field) + 1e-6)

        self.watermarks = []
        self.initialize_watermarks()
        self._init_vip()
        self.ghosts = []

    def _read_exact(self, n):
        buf = self.dec.stdout.read(n)
        return None if buf is None or len(buf) < n else buf

    def _kill_dec(self):
        try:
            if self.dec.poll() is None:
                self.dec.kill()
        except Exception:
            pass

    @property
    def strong_text(self):
        return self.text_variants[0]

    def close(self):
        self._kill_dec()
        try:
            if os.path.exists(self.thumb_path):
                os.remove(self.thumb_path)
        except Exception:
            pass

    def _to_display(self, f):
        if self.sar_stretch:
            f = cv2.resize(f, (int(round(self.coded_w * self.in_geo["sar_val"])), self.coded_h), interpolation=cv2.INTER_LINEAR)
        if self.rot_pick:
            f = rot_frame(f, self.rot_pick)
        if f.shape[1] != self.width or f.shape[0] != self.height:
            f = cv2.resize(f, (self.width, self.height), interpolation=cv2.INTER_LINEAR)
        return f

    def _get_font_path(self):
        for p in [os.path.join(TEMP_DIR, "Poppins-Medium.ttf"), os.path.join(TEMP_DIR, "Poppins-Bold.ttf"),
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
            if os.path.exists(p): return p
        return None

    def _get_bold_font_path(self):
        for p in [os.path.join(TEMP_DIR, "Poppins-Bold.ttf"),
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  os.path.join(TEMP_DIR, "Poppins-Medium.ttf")]:
            if os.path.exists(p): return p
        return None

    def _get_font(self, size):
        if not self.use_pil: return None
        path = self.bold_font_path if size >= self.vip_font_size else self.font_path
        if not path: return None
        key = (int(size), path)
        if key not in self._font_cache:
            try:
                self._font_cache[key] = ImageFont.truetype(path, int(size))
            except Exception:
                self._font_cache[key] = None
        return self._font_cache[key]

    def _get_grad_refl(self, H, W):
        key = (H, W)
        gr = self._grad_cache.get(key)
        if gr is None:
            yy = (np.arange(H, dtype=np.float32) / max(1, H))[:, None]
            xx = (np.arange(W, dtype=np.float32) / max(1, W))[None, :]
            g = (xx + yy) * 0.5
            grad25 = ((g - 0.5) * 25.0).astype(np.float32)
            refl = np.exp(-((g - 0.5) ** 2) / 0.05).astype(np.float32)
            gradv = (np.arange(H, dtype=np.float32) / max(1, H))[:, None]
            gr = (grad25, refl, gradv)
            if len(self._grad_cache) > 96:
                self._grad_cache.clear()
            self._grad_cache[key] = gr
        return gr

    def _make_sprite(self, text, font_size, bold=False, vary_seed=None):
        path = self.bold_font_path if bold else self.font_path
        if not self.use_pil or not path: return None
        key = (text, int(font_size), bold, vary_seed)
        if key in self._sprite_cache: return self._sprite_cache[key]
        font = self._get_font(font_size)
        if font is None: return None
        pad = max(6, int(font_size * 0.25))
        tmp = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        dr = ImageDraw.Draw(tmp)
        try:
            bbox = dr.textbbox((0, 0), text, font=font)
        except Exception:
            bbox = (0, 0, int(font_size * 0.6 * len(text)), int(font_size))
        tw = max(4, bbox[2] - bbox[0]); th = max(4, bbox[3] - bbox[1])
        sp = 0; stroke = 0
        if vary_seed is not None:
            rr = random.Random(vary_seed)
            sp = rr.choice([0, 1]); stroke = rr.choice([-1, 0, 1])
            tw += sp * max(1, len(text) - 1)
        img = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        if sp == 0:
            dr.text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=(0, 0, 0, 255))
        else:
            cx = pad
            for ch in text:
                dr.text((cx - bbox[0], pad - bbox[1]), ch, font=font, fill=(0, 0, 0, 255))
                cx += int(font.getlength(ch)) + sp
        if stroke != 0:
            arr0 = np.array(img)
            a0 = arr0[:, :, 3]
            k = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
            a0 = cv2.dilate(a0, k, 1) if stroke > 0 else cv2.erode(a0, k, 1)
            arr0[:, :, 3] = a0; img = Image.fromarray(arr0)
        arr = np.array(img)
        sprite = arr[:, :, [2, 1, 0, 3]].copy()
        if len(self._sprite_cache) > 500:
            self._sprite_cache.clear()
        self._sprite_cache[key] = sprite
        return sprite

    def _get_halo(self, text, font_size, bold=False, halo_color=90, halo_strength=0.20, halo_sigma=4.0, halo_dilate=4):
        key = (text, int(font_size), bool(bold), halo_color, halo_strength, halo_sigma, halo_dilate)
        if key in self._halo_cache: return self._halo_cache[key]
        sprite = self._make_sprite(text, font_size, bold)
        if sprite is None:
            self._halo_cache[key] = (None, 0); return None, 0
        m = int(3 * halo_sigma) + halo_dilate + 2
        a = sprite[:, :, 3]
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (halo_dilate * 2 + 1, halo_dilate * 2 + 1))
        a = cv2.dilate(a, k, iterations=1)
        a = cv2.GaussianBlur(a, (0, 0), halo_sigma)
        H, W = a.shape[:2]
        canvas = np.zeros((H + 2 * m, W + 2 * m), dtype=np.float32)
        canvas[m:m + H, m:m + W] = a.astype(np.float32)
        halo = np.zeros((H + 2 * m, W + 2 * m, 4), dtype=np.uint8)
        halo[:, :, :3] = halo_color
        halo[:, :, 3] = np.clip(canvas * halo_strength, 0, 255).astype(np.uint8)
        if len(self._halo_cache) > 120:
            self._halo_cache.clear()
        self._halo_cache[key] = (halo, m)
        return halo, m

    def _blend_sprite(self, frame, sprite, x, y, alpha):
        if sprite is None or alpha <= 0.0: return
        H, W = frame.shape[:2]
        sh, sw = sprite.shape[:2]
        x0, y0 = int(round(x)), int(round(y))
        sx0, sy0 = max(0, -x0), max(0, -y0)
        sx1, sy1 = max(0, min(sw, W - x0)), max(0, min(sh, H - y0))
        if sx1 <= sx0 or sy1 <= sy0: return
        dx0, dy0 = max(0, x0), max(0, y0)
        roi = frame[dy0:dy0 + (sy1 - sy0), dx0:dx0 + (sx1 - sx0)].astype(np.float32)
        spr = sprite[sy0:sy1, sx0:sx1]
        a = (spr[:, :, 3:4].astype(np.float32) / 255.0) * float(alpha)
        rgb = spr[:, :, :3].astype(np.float32)
        frame[dy0:dy0 + (sy1 - sy0), dx0:dx0 + (sx1 - sx0)] = \
            np.clip(rgb * a + roi * (1.0 - a), 0, 255).astype(np.uint8)

    def _blend_sprite_overlay(self, frame, sprite, x, y, alpha):
        if sprite is None or alpha <= 0.0: return
        H, W = frame.shape[:2]
        sh, sw = sprite.shape[:2]
        x0, y0 = int(round(x)), int(round(y))
        sx0, sy0 = max(0, -x0), max(0, -y0)
        sx1, sy1 = max(0, min(sw, W - x0)), max(0, min(sh, H - y0))
        if sx1 <= sx0 or sy1 <= sy0: return
        dx0, dy0 = max(0, x0), max(0, y0)
        roi = frame[dy0:dy0 + (sy1 - sy0), dx0:dx0 + (sx1 - sx0)].astype(np.float32) / 255.0
        spr = sprite[sy0:sy1, sx0:sx1]
        a = (spr[:, :, 3:4].astype(np.float32) / 255.0) * float(alpha)
        s = spr[:, :, :3].astype(np.float32) / 255.0
        ov = np.where(roi < 0.5, 2.0 * roi * s, 1.0 - 2.0 * (1.0 - roi) * (1.0 - s))
        outp = roi * (1.0 - a) + ov * a
        frame[dy0:dy0 + (sy1 - sy0), dx0:dx0 + (sx1 - sx0)] = \
            np.clip(outp * 255.0, 0, 255).astype(np.uint8)

    def _adaptive_bind(self, sprite, detail, edges, texture, x, y, gain, amod):
        if sprite is None or detail is None: return sprite
        H, W = detail.shape[:2]
        sh, sw = sprite.shape[:2]
        x0, y0 = int(round(x)), int(round(y))
        sx0, sy0 = max(0, -x0), max(0, -y0)
        sx1, sy1 = max(0, min(sw, W - x0)), max(0, min(sh, H - y0))
        if sx1 <= sx0 or sy1 <= sy0: return sprite
        dx0, dy0 = max(0, x0), max(0, y0)
        dx1, dy1 = min(W, x0 + sw), min(H, y0 + sh)
        dmap = np.zeros((sh, sw), dtype=np.float32)
        dmap[sy0:sy1, sx0:sx1] = detail[dy0:dy1, dx0:dx1]
        emap = np.zeros((sh, sw), dtype=np.float32)
        if edges is not None:
            emap[sy0:sy1, sx0:sx1] = edges[dy0:dy1, dx0:dx1]
        tmap = np.zeros((sh, sw), dtype=np.float32)
        if texture is not None:
            tmap[sy0:sy1, sx0:sx1] = texture[dy0:dy1, dx0:dx1]
        spr = sprite.copy()
        mask = (spr[:, :, 3] > 10).astype(np.float32)
        rgb = spr[:, :, :3].astype(np.float32)
        dn = np.clip(dmap / 50.0, -1.0, 1.0)
        en = np.clip(emap / 80.0, 0, 1.0)
        tn = np.clip(tmap / 60.0, 0, 1.0)
        complexity = (en + tn) / 2.0
        # ✅ bind كروما أقوى (5.0 بدل 4.0)
        chroma_mod = (dn * 0.6 + en * 0.3 + tn * 0.2) * gain * 0.15 * mask
        rgb[:, :, 0] += chroma_mod * 5.0
        rgb[:, :, 2] -= chroma_mod * 5.0
        spr[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
        al = spr[:, :, 3].astype(np.float32) * (1.0 + amod * dn * mask)
        al *= (1.0 - 0.3 * complexity * mask)
        spr[:, :, 3] = np.clip(al, 0, 255).astype(np.uint8)
        return spr

    def _fade_alpha(self, wm):
        base = wm.get("base_alpha", 1.0)
        age, life = wm.get("age", 0), wm.get("life", 1)
        fi, fo = wm.get("fade_in", 1), wm.get("fade_out", 1)
        if age < fi: f = age / max(1, fi)
        elif age > life - fo: f = max(0.0, (life - age) / max(1, fo))
        else: f = 1.0
        return base * f

    def _reshuffle(self):
        cells = [(c, r) for r in range(self.grid_rows) for c in range(self.grid_cols)]
        random.shuffle(cells); self._cell_queue = cells

    def _next_cell(self):
        if not self._cell_queue: self._reshuffle()
        return self._cell_queue.pop()

    def _reshuffle_bottom(self):
        first_row = self.grid_rows // 2 - 1
        cells = [(c, r) for r in range(first_row, self.grid_rows) for c in range(self.grid_cols)]
        random.shuffle(cells); self._bottom_queue = cells

    def _next_bottom_cell(self):
        if not self._bottom_queue: self._reshuffle_bottom()
        return self._bottom_queue.pop()

    def _spawn_position(self, font_size, prefer_detail=False):
        est_w = int(font_size * len(self.strong_text) * 0.62); est_h = int(font_size * 1.6)
        best = None; best_score = -1.0
        tries = 8 if prefer_detail else 1
        x = y = 2.0
        for _ in range(tries):
            col, row = self._next_bottom_cell()
            cw, ch = self.width / self.grid_cols, self.height / self.grid_rows
            x = col * cw + cw * random.uniform(0.15, 0.85) - est_w / 2
            y = row * ch + ch * random.uniform(0.15, 0.85) - est_h / 2
            x = max(2, min(x, self.width - est_w - 2)); y = max(2, min(y, self.height - est_h - 2))
            if not prefer_detail or self._frame_texture is None or self._frame_edges is None:
                return x, y
            x0, y0 = int(x), int(y)
            x1 = min(int(x + est_w), self.width); y1 = min(int(y + est_h), self.height)
            if x1 <= x0 or y1 <= y0:
                return x, y
            sc = (float(self._frame_texture[y0:y1, x0:x1].mean()) +
                  0.5 * float(self._frame_edges[y0:y1, x0:x1].mean()) + random.uniform(0, 5))
            if sc > best_score:
                best_score = sc; best = (x, y)
        return best if best is not None else (x, y)

    def _make_watermark(self, wm_type):
        anchor = (wm_type == "strong" and random.random() < 0.26)
        if anchor:
            font_size = random.randint(self.strong_max_font, max(self.strong_max_font + 2, int(self.strong_max_font * 1.5)))
        else:
            font_size = random.randint(self.strong_min_font, self.strong_max_font)
        base_alpha = 1.0
        vy_ps = random.uniform(120, 260)
        life_s = random.uniform(4.5, 8.0)
        j_amp = random.uniform(0.25, 0.45)
        bind_gain = random.uniform(5.0, 7.5)
        bind_amod = random.uniform(0.32, 0.52)
        prof = random.random()
        if anchor:
            op_mid, op_amp = random.uniform(0.55, 0.68), random.uniform(0.04, 0.08)
            bind_gain = random.uniform(7.5, 10.0)
            bind_amod = random.uniform(0.48, 0.68)
        elif prof < 0.30:
            op_mid, op_amp = random.uniform(0.38, 0.52), random.uniform(0.08, 0.14)
        elif prof < 0.60:
            op_mid, op_amp = 0.65, 0.0
            bind_gain = random.uniform(6.5, 8.5)
            bind_amod = random.uniform(0.42, 0.58)
        else:
            op_mid, op_amp = random.uniform(0.32, 0.58), random.uniform(0.08, 0.16)
            bind_gain = random.uniform(7.5, 10.0)
            bind_amod = random.uniform(0.48, 0.68)
        blend = "overlay" if random.random() < 0.60 else "alpha"
        prefer_detail = True if anchor else (random.random() < 0.55)
        life = max(30, int(life_s * self.out_fps))
        fi = max(6, int(0.7 * self.out_fps))
        fo = max(10, int(1.3 * self.out_fps))
        x, y = self._spawn_position(font_size, prefer_detail=prefer_detail)
        return {
            "type": wm_type, "blend": blend, "anchor": anchor, "text_idx": 0,
            "vary_seed": random.randint(1, 10**9),
            "x": x, "y": y, "vy_ps": vy_ps, "vx_ps": 0.0,
            "fx_tilt": math.radians(random.uniform(-3, 3)),
            "fx_grow_amp": random.uniform(0.06, 0.14),
            "font_size": font_size, "base_alpha": base_alpha,
            "life": life, "age": random.randint(0, fi), "fade_in": fi, "fade_out": fo,
            "op_mid": op_mid, "op_amp": op_amp,
            "op_period": random.uniform(3.0, 7.0),
            "op_phase": random.uniform(0, math.pi * 2),
            "pz_rot_amp": math.radians(random.uniform(0.8, 1.6)),
            "pz_rot_freq": random.uniform(0.08, 0.18),
            "pz_rot_ph": random.uniform(0, math.pi * 2),
            "pz_gam_amp": random.uniform(0.08, 0.13),
            "pz_gam_freq": random.uniform(0.10, 0.25),
            "pz_gam_ph": random.uniform(0, math.pi * 2),
            "pz_nyq_sign": random.choice([-1.0, 1.0]),
            "pz_roll": random.choice([-1, 0, 1]),
            "fx_jx_amp": j_amp, "fx_jy_amp": j_amp * random.uniform(0.7, 1.1),
            "fx_jx_freq": random.uniform(0.05, 0.12),
            "fx_jy_freq": random.uniform(0.04, 0.10),
            "fx_erode_freq": random.uniform(0.08, 0.15),
            "fx_refl": random.uniform(30.0, 42.0),
            "bind_gain": bind_gain, "bind_amod": bind_amod,
            "fx_grad_dir": random.choice([0, 1, 2]), "fx_grad_strength": random.uniform(0.25, 0.55),
            "fx_morph_amp": random.uniform(0.45, 0.75),
            "fx_morph_freq": random.uniform(0.10, 0.20),
        }

    def initialize_watermarks(self):
        self.watermarks = [self._make_watermark("strong") for _ in range(STRONG_COUNT)]

    def reset_watermark(self, idx):
        old = self.watermarks[idx].get("type", "strong")
        self.watermarks[idx] = self._make_watermark(old)

    def _apply_fast_poisons(self, spr, frame_num):
        if spr is None: return spr
        alpha = spr[:, :, 3]
        mask = alpha > 10
        if not mask.any(): return spr
        H, W = spr.shape[:2]
        rng = np.random.RandomState((frame_num * 7919) % (2**32))
        dx = rng.uniform(-0.5, 0.5)
        dy = rng.uniform(-0.5, 0.5)
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        spr = cv2.warpAffine(spr, M, (W, H), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        mask = spr[:, :, 3] > 10
        if not mask.any(): return spr
        # ✅ كروما داخل الحرف ±2.5
        cn = rng.uniform(-2.5, 2.5, size=(H, W)).astype(np.float32) * mask
        rgbf = spr[:, :, :3].astype(np.float32)
        rgbf[:, :, 0] = np.clip(rgbf[:, :, 0] + cn * 0.6, 0, 255)
        rgbf[:, :, 2] = np.clip(rgbf[:, :, 2] - cn * 0.6, 0, 255)
        spr[:, :, :3] = rgbf.astype(np.uint8)
        a8 = spr[:, :, 3]
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        er = cv2.erode(a8, k, 1).astype(np.float32)
        di = cv2.dilate(a8, k, 1).astype(np.float32)
        band = ((di - er) > 8)
        if band.any():
            # ✅ حواف كروما معاكسة (تكسر حدود الـ matting)
            ce = rng.uniform(-3.0, 3.0, size=(H, W)).astype(np.float32) * band
            rgbf = spr[:, :, :3].astype(np.float32)
            rgbf[:, :, 0] = np.clip(rgbf[:, :, 0] - ce * 0.7, 0, 255)
            rgbf[:, :, 2] = np.clip(rgbf[:, :, 2] + ce * 0.7, 0, 255)
            spr[:, :, :3] = rgbf.astype(np.uint8)
            edge_noise = rng.uniform(-10.0, 10.0, size=(H, W)).astype(np.float32)
            af = spr[:, :, 3].astype(np.float32)
            af = np.where(band, np.clip(af + edge_noise * 0.4, 0, 255), af)
            spr[:, :, 3] = af.astype(np.uint8)
        # ✅ ثقوب ميكرو 1.5% بالـ Alpha (تحت عتبة الرؤية، تدمر نظافة القناع)
        mask_a = spr[:, :, 3] > 200
        if mask_a.any():
            holes = (rng.rand(H, W) < 0.015) & mask_a
            af = spr[:, :, 3].astype(np.float32)
            af[holes] *= 0.3
            spr[:, :, 3] = af.astype(np.uint8)
        # ✅ Alpha jitter ±3
        mask2 = (spr[:, :, 3] > 10).astype(np.int16)
        jit = rng.randint(-3, 4, size=(H, W), dtype=np.int16)
        aj = spr[:, :, 3].astype(np.int16) + jit * mask2
        spr[:, :, 3] = np.clip(aj, 0, 255).astype(np.uint8)
        return spr

    def _apply_text_effects(self, sprite, wm, frame_num, orig_w, orig_h, orig_x, orig_y):
        if sprite is None: return sprite, orig_x, orig_y
        H, W = sprite.shape[:2]
        t = frame_num / self.out_fps
        ph, ph2 = wm.get("fx_phase", 0.0), wm.get("fx_phase2", 0.0)
        life = wm.get("life", 0)
        p = min(1.0, max(0.0, wm.get("age", 0) / float(life))) if life else ((t % 6.0) / 6.0)
        grow = 1.0 + wm.get("fx_grow_amp", 0.10) * math.sin(math.pi * p)
        scale = (1.0 + 0.03 * math.sin(t * 0.8 + ph)) * grow
        nH, nW = max(4, int(H * scale)), max(4, int(W * scale))
        spr = cv2.resize(sprite, (nW, nH), interpolation=cv2.INTER_LINEAR)
        cx, cy = orig_x + orig_w / 2.0, orig_y + orig_h / 2.0
        nx, ny = cx - nW / 2.0, cy - nH / 2.0
        rot = wm.get("fx_tilt", 0.0) + wm.get("pz_rot_amp", 0.02) * math.sin(
            2 * math.pi * wm.get("pz_rot_freq", 0.12) * t + wm.get("pz_rot_ph", 0.0))
        sc = 1.0 + 0.004 * math.sin(2 * math.pi * 0.15 * t + ph2)
        a = sc * math.cos(rot); b = -sc * math.sin(rot)
        c = sc * math.sin(rot); d = sc * math.cos(rot)
        HW, HH = nW / 2.0, nH / 2.0
        M = np.float32([[a, b, HW - a * HW - b * HH],
                        [c, d, HH - c * HW - d * HH]])
        spr = cv2.warpAffine(spr, M, (nW, nH), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        H_spr, W_spr = spr.shape[:2]
        alpha = spr[:, :, 3].astype(np.float32)
        rgb = spr[:, :, :3].astype(np.float32)
        mask = (alpha > 10).astype(np.float32)
        a8 = alpha.astype(np.uint8)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        er = cv2.erode(a8, k, iterations=1)
        band = ((a8.astype(np.int16) - er.astype(np.int16)) > 30) & (a8 > 12)
        we = 0.5 + 0.5 * math.sin(t * wm.get("fx_erode_freq", 0.10) + ph2)
        alpha = alpha * (1.0 - 0.5 * we) + er.astype(np.float32) * (0.5 * we)
        gj = 1.0 + wm.get("pz_gam_amp", 0.10) * math.sin(
            2 * math.pi * wm.get("pz_gam_freq", 0.15) * t + wm.get("pz_gam_ph", 0.0))
        alpha = 255.0 * np.power(np.clip(alpha / 255.0, 0.0, 1.0), gj)

        use_big = (H_spr <= self._MAXH and W_spr <= self._MAXW)
        if use_big:
            chk = self._checker_base[:H_spr, :W_spr]
        else:
            ii, jj = np.ogrid[0:H_spr, 0:W_spr]
            chk = (((ii + jj) & 1).astype(np.float32) * 2.0 - 1.0)
        nyq_f = 12.0 / 255.0
        alpha = alpha * (1.0 + chk * nyq_f * wm.get("pz_nyq_sign", 1.0) * mask)

        g25, rfl, gradv = self._get_grad_refl(H_spr, W_spr)
        alpha_mod = (g25 / 25.0 + rfl / 40.0) * 0.12 * mask
        alpha = alpha * (1.0 + alpha_mod)

        if band.any():
            bay_f = 14.0 / 255.0
            if use_big:
                bay = self._bayer_big[:H_spr, :W_spr] * bay_f
            else:
                bb = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]],
                              dtype=np.float32) / 16.0 - 0.5
                bay = np.tile(bb, (H_spr // 4 + 1, W_spr // 4 + 1))[:H_spr, :W_spr] * bay_f
            alpha = np.where(band, alpha * (1.0 + bay), alpha)

        morph_cycle = wm.get("fx_morph_amp", 0.4) * math.sin(2 * math.pi * wm.get("fx_morph_freq", 0.12) * t + ph2)
        if abs(morph_cycle) > 0.1:
            k_morph = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            alpha_u8 = alpha.astype(np.uint8)
            if morph_cycle > 0:
                alpha_morphed = cv2.dilate(alpha_u8, k_morph, iterations=1).astype(np.float32)
            else:
                alpha_morphed = cv2.erode(alpha_u8, k_morph, iterations=1).astype(np.float32)
            alpha = alpha * 0.7 + alpha_morphed * 0.3

        gs = wm.get("fx_grad_strength", 0.4)
        gd = wm.get("fx_grad_dir", 0)
        if gs > 0.01:
            if gd == 0: alpha *= (1.0 - gs * gradv)
            elif gd == 1: alpha *= (1.0 - gs * (1.0 - gradv))
            else: alpha *= (1.0 - gs * (g25 / 25.0 + 0.5))

        spr = np.dstack([np.clip(rgb, 0, 255).astype(np.uint8),
                         np.clip(alpha, 0, 255).astype(np.uint8)])
        spr = self._apply_fast_poisons(spr, frame_num)
        return spr, nx, ny

    def _init_vip(self):
        self.vip = {"age": 0, "t": 0.0,
                    "px": random.uniform(0, math.pi * 2), "py": random.uniform(0, math.pi * 2),
                    "fx": random.uniform(0.3, 0.5), "fy": random.uniform(0.45, 0.75)}
        self.vip_fx = {
            "fx_tilt": math.radians(random.uniform(-2, 2)),
            "fx_grow_amp": random.uniform(0.03, 0.06),
            "fx_phase": random.uniform(0, math.pi * 2), "fx_phase2": random.uniform(0, math.pi * 2),
            "fx_erode_freq": random.uniform(0.08, 0.15),
            "fx_refl": random.uniform(30.0, 40.0),
            "bind_gain": 4.0, "bind_amod": 0.28,
            "fx_grad_dir": random.choice([0, 1, 2]), "fx_grad_strength": random.uniform(0.20, 0.40),
            "fx_refl_strength": random.uniform(0.35, 0.50), "fx_refl_scale": random.uniform(0.5, 0.7),
            "fx_refl_gap": random.uniform(2, 6),
            "fx_refl_wave_amp": random.uniform(1.2, 2.2),
            "fx_refl_wave_freq": random.uniform(0.10, 0.20),
            "fx_refl_wave_speed": random.uniform(1.5, 2.5),
            "pz_rot_amp": math.radians(random.uniform(0.6, 1.2)),
            "pz_rot_freq": random.uniform(0.08, 0.15),
            "pz_rot_ph": random.uniform(0, math.pi * 2),
            "pz_gam_amp": random.uniform(0.06, 0.10),
            "pz_gam_freq": random.uniform(0.10, 0.20),
            "pz_gam_ph": random.uniform(0, math.pi * 2),
            "pz_nyq_sign": random.choice([-1.0, 1.0]),
            "pz_roll": random.choice([-1, 0, 1]),
            "fx_morph_amp": random.uniform(0.3, 0.5),
            "fx_morph_freq": random.uniform(0.08, 0.15),
        }

    def _process_vip(self, frame, dt, frame_num, rects):
        sprite = self._make_sprite(self.vip_text, self.vip_font_size, bold=True, vary_seed=self.vip_vary)
        if sprite is None: return
        sh, sw = sprite.shape[:2]
        if sw > self.width - 16:
            nw = max(40, self.width - 16)
            nh = max(20, int(sh * nw / max(1, sw)))
            sprite = cv2.resize(sprite, (nw, nh), interpolation=cv2.INTER_AREA)
            sh, sw = sprite.shape[:2]
        self.vip["age"] += 1; self.vip["t"] += dt
        t = self.vip["t"]
        alpha = 0.88 + 0.01 * math.sin(t * 0.4)
        fade_f = max(10, int(0.8 * self.out_fps))
        if self.vip["age"] < fade_f: alpha *= self.vip["age"] / fade_f
        mx, my = sw / 2 + 20, sh / 2 + 20
        ax = max(0.0, self.width / 2 - mx)
        ay = max(0.0, self.height / 2 - my)
        cx = self.width / 2 + ax * math.sin(t * self.vip["fx"] + self.vip["px"])
        cy = self.height / 2 + ay * math.sin(t * self.vip["fy"] + self.vip["py"])
        x = max(5, min(cx - sw / 2, self.width - sw - 5))
        y = max(5, min(cy - sh / 2, self.height - sh - 5))
        sprite, x, y = self._apply_text_effects(sprite, self.vip_fx, frame_num, sw, sh, x, y)
        sh, sw = sprite.shape[:2]
        x = max(5, min(x, self.width - sw - 5))
        y = max(5, min(y, self.height - sh - 5))
        sprite = self._adaptive_bind(sprite, self._frame_detail, self._frame_edges,
                                     self._frame_texture, x, y,
                                     self.vip_fx["bind_gain"], self.vip_fx["bind_amod"])
        halo, hm = self._get_halo(self.vip_text, self.vip_font_size, bold=True,
                                  halo_color=20, halo_strength=0.30, halo_sigma=3.0, halo_dilate=2)
        if halo is not None:
            self._blend_sprite(frame, halo, x - hm + 1, y - hm + 2, alpha)
        self._blend_sprite(frame, sprite, x, y, alpha)
        rects.append((int(x), int(y), sw, sh))
        rst = self.vip_fx.get("fx_refl_strength", 0.42)
        rsc = self.vip_fx.get("fx_refl_scale", 0.6)
        gap = int(self.vip_fx.get("fx_refl_gap", 4))
        r = np.flipud(sprite)
        rh, rw = r.shape[:2]
        nh2 = max(4, int(rh * rsc))
        r = cv2.resize(r, (rw, nh2), interpolation=cv2.INTER_AREA)
        rh2, rw2 = r.shape[:2]
        wa = self.vip_fx.get("fx_refl_wave_amp", 1.6)
        wf = self.vip_fx.get("fx_refl_wave_freq", 0.15)
        ws = self.vip_fx.get("fx_refl_wave_speed", 2.0)
        rows = np.arange(rh2, dtype=np.float32)
        dxw = (wa * np.sin(rows * wf + t * ws)).astype(np.float32)
        map_x = np.tile(np.arange(rw2, dtype=np.float32)[None, :], (rh2, 1)) + dxw[:, None]
        map_y = np.tile(rows[:, None], (1, rw2))
        r = cv2.remap(r, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        fade = (np.linspace(1.0, 0.0, rh2, dtype=np.float32) ** 1.5)[:, None]
        r[:, :, 3] = np.clip(r[:, :, 3].astype(np.float32) * fade * rst, 0, 255).astype(np.uint8)
        self._blend_sprite(frame, r, x, y + sh + gap, alpha)

    def _apply_region_poisons(self, frame, rects, frame_num):
        if not rects: return frame
        H, W = frame.shape[:2]
        rng = np.random.RandomState((frame_num * 104729) % (2**32))
        for i, (rx, ry, rw, rh) in enumerate(rects):
            x0, y0 = max(0, rx - 6), max(0, ry - 6)
            x1, y1 = min(W, rx + rw + 6), min(H, ry + rh + 6)
            if x1 <= x0 or y1 <= y0: continue
            sgn = 1.0 if (i % 2 == 0) else -1.0
            sub = self._parity1[y0:y1, x0:x1]
            region = frame[y0:y1, x0:x1].astype(np.float32)
            # ✅ كروما-Parity أقوى: 5.5
            chroma = sub * 5.5 * sgn
            region[:, :, 0] = np.clip(region[:, :, 0] + chroma * 0.7, 0, 255)
            region[:, :, 2] = np.clip(region[:, :, 2] - chroma * 0.7, 0, 255)
            # ✅ وميض كروما زمني لكل منطقة (يكسر الاتساق الزمني للإزالة)
            co = rng.uniform(-1.5, 1.5)
            region[:, :, 0] = np.clip(region[:, :, 0] + co, 0, 255)
            region[:, :, 2] = np.clip(region[:, :, 2] - co, 0, 255)
            frame[y0:y1, x0:x1] = np.clip(region, 0, 255).astype(np.uint8)
        return frame

    def process_frame(self, coded_frame, frame_num):
        frame = self._to_display(coded_frame)
        out = frame
        blur_s = cv2.blur(out, (3, 3))
        out = np.clip(out.astype(np.float32) * 1.08 - blur_s.astype(np.float32) * 0.08,
                      0, 255).astype(np.uint8)

        # ✅ Spread-Spectrum كروما متحرك زمنياً (سعة 0.6 بموجة بطيئة)
        t_ss = frame_num / self.out_fps
        ss_amp = 0.6 * (0.6 + 0.4 * math.sin(2 * math.pi * 0.07 * t_ss))
        outf = out.astype(np.float32)
        outf[:, :, 0] = np.clip(outf[:, :, 0] + self._ss_field * ss_amp, 0, 255)
        outf[:, :, 2] = np.clip(outf[:, :, 2] - self._ss_field * ss_amp, 0, 255)
        out = outf.astype(np.uint8)

        t = frame_num / self.out_fps
        if self._frame_detail is None or (frame_num - self._maps_frame) >= 4:
            gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
            gf = gray.astype(np.float32)
            self._frame_detail = gf - cv2.blur(gf, (5, 5)).astype(np.float32)
            self._frame_edges = cv2.Canny(gray, 50, 150).astype(np.float32)
            blur_small = cv2.GaussianBlur(gf, (3, 3), 0)
            blur_large = cv2.GaussianBlur(gf, (15, 15), 0)
            self._frame_texture = np.abs(blur_small - blur_large)
            self._frame_lum = cv2.blur(gf, (9, 9))
            self._maps_frame = frame_num

        rng_af = np.random.RandomState((frame_num * 65537) % (2**32))
        rects = []
        for idx, wm in enumerate(self.watermarks):
            wm["y"] -= wm["vy_ps"] / self.out_fps
            wm["age"] += 1
            txt = self.text_variants[wm.get("text_idx", 0)]
            sprite = self._make_sprite(txt, wm["font_size"], bold=False, vary_seed=wm.get("vary_seed"))
            if sprite is None: continue
            osh, osw = sprite.shape[:2]
            fade = self._fade_alpha(wm) / max(1e-6, wm.get("base_alpha", 1.0))
            wave = math.sin(2 * math.pi * t / wm["op_period"] + wm["op_phase"])
            alpha = fade * (wm["op_mid"] + wm["op_amp"] * wave)
            alpha = max(0.28, min(0.68, alpha))
            alpha *= 1.0 + rng_af.uniform(-0.06, 0.06)
            if wm["y"] < self.top_fade_margin:
                fade_top = max(0.0, wm["y"] / self.top_fade_margin)
                alpha *= fade_top
            if wm["y"] <= 0 or wm["age"] > wm["life"]:
                self.reset_watermark(idx); continue
            if alpha <= 0.01: continue
            x = wm["x"] + math.sin(t * wm.get("fx_jx_freq", 0.08) + wm.get("fx_phase", 0)) * wm.get("fx_jx_amp", 0.6)
            y = wm["y"] + math.cos(t * wm.get("fx_jy_freq", 0.06) + wm.get("fx_phase2", 0)) * wm.get("fx_jy_amp", 0.5)
            sprite, x, y = self._apply_text_effects(sprite, wm, frame_num, osw, osh, x, y)
            sh, sw = sprite.shape[:2]
            x = max(2, min(x, self.width - sw - 2))
            y = max(2, min(y, self.height - sh - 2))
            sprite = self._adaptive_bind(sprite, self._frame_detail, self._frame_edges,
                                         self._frame_texture, x, y, wm["bind_gain"], wm["bind_amod"])
            if wm.get("anchor"):
                halo, hm = self._get_halo(txt, wm["font_size"], bold=False,
                                          halo_color=30, halo_strength=0.22, halo_sigma=3.0, halo_dilate=2)
                if halo is not None:
                    self._blend_sprite(out, halo, x - hm + 1, y - hm + 2, alpha)
            if wm.get("blend") == "overlay":
                self._blend_sprite_overlay(out, sprite, x, y, alpha)
            else:
                self._blend_sprite(out, sprite, x, y, alpha)
            rects.append((int(x), int(y), sw, sh))
        self._process_vip(out, 1.0 / self.out_fps, frame_num, rects)
        out = self._apply_region_poisons(out, rects, frame_num)
        return out

    def run(self, progress_cb=None, job=None):
        n = 0; idx = 0; t0 = time.time()
        pending = getattr(self, "_first_pending", None)
        while True:
            if pending is not None:
                buf = pending; pending = None
            else:
                buf = self._read_exact(self.frame_bytes)
                if buf is None: break
            idx += 1
            if self.step > 1 and (idx % self.step) != 1: continue
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(self.coded_h, self.coded_w, 3)
            n += 1
            done = self.process_frame(frame, n)
            if not self._thumb_done and n >= 1:
                try:
                    th = done
                    h0, w0 = th.shape[:2]
                    sc = 640.0 / max(h0, w0)
                    if sc < 1.0: th = cv2.resize(th, (int(w0 * sc), int(h0 * sc)))
                    cv2.imwrite(self.thumb_path, th, [int(cv2.IMWRITE_JPEG_QUALITY), 87])
                except Exception: pass
                self._thumb_done = True
            if not self.enc.alive():
                raise ValueError("❌ المرمّز توقف")
            if not self.enc.write(done):
                raise ValueError("❌ فشل إرسال الفريم")
            if progress_cb is not None and n % 20 == 0:
                progress_cb(n, max(n, self.total_out if self.total_out else n))
        if progress_cb is not None:
            progress_cb(max(n, 1), max(n, 1))
        self.enc.close(); self._kill_dec()
        return time.time() - t0


from pyrogram import Client, filters
from pyrogram.types import Message

app = Client("watermark_bot", api_id=API_ID, api_hash=API_HASH,
             bot_token=BOT_TOKEN, workdir=TEMP_DIR, in_memory=True)
MAX_SIZE = 500 * 1024 * 1024
JOB_QUEUE = asyncio.Queue()


def bar(pct, length=12):
    pct = max(0.0, min(1.0, pct))
    f = int(round(pct * length))
    return "█" * f + "░" * (length - f)


def fmt_time(s):
    s = max(0, int(s))
    return f"{s // 60:02d}:{s % 60:02d}"


async def progress_updater(job):
    msg = job["status_msg"]
    while not job["done"]:
        await asyncio.sleep(PROGRESS_INTERVAL)
        if job["done"]: break
        stage = job.get("stage", "watermark")
        if stage == "download":
            text = "⬇️ تحميل...\n📊 [░░░░░░░░░░░░]"
        elif stage == "watermark":
            n = job.get("frames_done", 0)
            total = max(1, job.get("frames_total", 1))
            pct = min(1.0, n / total)
            el = time.time() - job.get("t0", time.time())
            eta = (el / max(pct, 0.02)) * (1 - pct) if pct > 0.02 else 0
            text = (f"🎬 معالجة {job.get('dims', '')}...\n📊 [{bar(pct)}] {pct*100:.0f}%\n"
                    f"🎞 فريم: {n}/{total}\n⏱ مضى: {fmt_time(el)} | المتبقي: ~{fmt_time(eta)}\n"
                    f"📚 منتظرين: {JOB_QUEUE.qsize()}")
        elif stage == "finish":
            text = "📦 إنهاء الترميز...\n📊 [████████████] 99%"
        elif stage == "upload":
            text = "📤 إرسال...\n📊 [████████████] 100%"
        else:
            text = "⏳ بالطابور..."
        try: await msg.edit_text(text)
        except Exception: pass


async def process_job(job):
    message, msg, uid = job["message"], job["status_msg"], job["uid"]
    inp = os.path.join(TEMP_DIR, f"in_{uid}.mp4")
    out = os.path.join(TEMP_DIR, f"out_{uid}.mp4")
    w = None
    try:
        job["stage"] = "download"
        await message.download(file_name=inp)
        if not os.path.exists(inp) or os.path.getsize(inp) == 0:
            await msg.edit_text("❌ فشل تحميل الفيديو"); return
        job["stage"] = "watermark"; job["frames_done"] = 0; job["t0"] = time.time()
        w = FloatingWatermark(inp, out, audio_src=inp)
        job["frames_total"] = w.total_out if w.total_out else 1
        job["dims"] = f"{w.width}x{w.height}"
        def cb(n, total):
            job["frames_done"], job["frames_total"] = n, total
        updater = asyncio.create_task(progress_updater(job))
        elapsed = await asyncio.to_thread(w.run, cb, job)
        job["stage"] = "finish"; job["done"] = True
        try: await asyncio.wait_for(updater, timeout=PROGRESS_INTERVAL + 2)
        except Exception: updater.cancel()
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            await msg.edit_text("❌ فشل إنشاء الفيديو"); return
        sz = os.path.getsize(out) / (1024 ** 2)
        if sz > 500:
            await msg.edit_text(f"❌ الحجم {sz:.1f}MB أكبر من 500MB"); return
        job["stage"] = "upload"
        dur_sec = int(round(w.total_out / w.out_fps)) if (w.out_fps > 0 and w.total_out) else 0
        thumb_arg = w.thumb_path if os.path.exists(w.thumb_path) else None
        await message.reply_video(
            out, thumb=thumb_arg,
            caption=f"✓ تمت المعالجة\n⏱ {fmt_time(elapsed)}",
            duration=dur_sec, width=w.width, height=w.height,
            supports_streaming=True,
        )
        await msg.edit_text(f"✅ انتهى!\n⏱ {fmt_time(elapsed)}\n💾 {sz:.1f}MB\n📚 بالانتظار: {JOB_QUEUE.qsize()}")
    except Exception as e:
        print(f"❌ خطأ: {e}")
        try: await msg.edit_text(f"❌ خطأ: {e}")
        except Exception: pass
    finally:
        if w is not None:
            try: w.close()
            except Exception: pass
        for p in (inp, out):
            try:
                if os.path.exists(p): os.remove(p)
            except Exception: pass


async def worker():
    while True:
        job = await JOB_QUEUE.get()
        try: await job["status_msg"].edit_text(f"🚀 بدأنا فيديوك!\n📚 متبقين: {JOB_QUEUE.qsize()}")
        except Exception: pass
        try: await process_job(job)
        except Exception as e: print(f"❌ worker: {e}")
        finally: JOB_QUEUE.task_done()


@app.on_message(filters.command("start"))
async def start(c, m):
    await m.reply("👋 هلا! 🎬\n\nأرسل فيديو وأضيف الحقوق:\n\n"
                  "🖋 50 نص + مراسي 26% (نظيف تماماً للمشاهد)\n"
                  "🖼 مصغرة = أول فريم\n"
                  "🛡️ 12 سلاح خفي: كروما/Alpha/شكل/زمن فقط\n"
                  "🖤 Telegram:@vamp1r3s أسود واضح\n"
                  "💾 CRF 20 مضبوط | 📺 عرض سليم | 🚀 سريع")


@app.on_message(filters.video)
async def on_video(c, m):
    if m.video and m.video.file_size and m.video.file_size > MAX_SIZE:
        await m.reply("❌ أكبر من 500MB"); return
    if JOB_QUEUE.qsize() >= MAX_QUEUE:
        await m.reply(f"😅 الطابور ممتلئ ({JOB_QUEUE.qsize()}/{MAX_QUEUE})"); return
    pos = JOB_QUEUE.qsize() + 1
    msg = await m.reply(f"✅ استلمت فيديوك!\n📚 ترتيبك: #{pos}\n⏳ سأبدأ فور انتهاء من قبلك")
    await JOB_QUEUE.put({"message": m, "status_msg": msg, "uid": uuid.uuid4().hex[:8],
                         "stage": "queued", "done": False,
                         "frames_done": 0, "frames_total": 1, "t0": None, "dims": ""})


@app.on_message(filters.command("status"))
async def status(c, m):
    await m.reply(f"📚 بالطابور: {JOB_QUEUE.qsize()}\n🎞 CRF 20\n🏭 شغال")


async def main():
    print("🤖 تشغيل البوت...")
    await app.start()
    print("✓ البوت شغال | تعزيز القنوات العمياء: كروما+Alpha+شكل+زمن")
    asyncio.create_task(worker())
    try:
        from pyrogram import idle
        await idle()
    except ImportError:
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
