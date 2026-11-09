"""Read explicit ANLZ files into a standalone waveform-data preview.

This diagnostic opens no audio, library state, device, Qt window or native deck.
It is deliberately NOT a screenshot/emulation of Rekordbox or SoundBox's Metal
renderer. Linear connections between decoded rows expose the stored geometry;
the second RGB lane isolates SoundBox's current packed-height transfer curve.
Only the explicitly supplied output directory is written.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from soundbox.rekordbox_analysis import _read_pwv3, _read_pwv5, _read_pwv7


def decode(blue: bytes, rgb: bytes, bands: bytes) -> dict:
    if not blue or not rgb or len(rgb) % 2 or not bands or len(bands) % 3:
        raise ValueError("Three complete packed waveform streams are required")
    blue_rows = np.frombuffer(blue, dtype=np.uint8)
    rgb_rows = np.frombuffer(rgb, dtype=">u2")
    band_rows = np.frombuffer(bands, dtype=np.uint8).reshape(-1, 3)
    return {
        "blue": ((blue_rows & 31) / 31).tolist(),
        "rgb": (((rgb_rows & 0x7c) >> 2) / 31).tolist(),
        "colors": np.stack((
            ((rgb_rows >> 13) & 7) / 7,
            ((rgb_rows >> 10) & 7) / 8,
            ((rgb_rows >> 7) & 7) / 8.75,
        ), axis=-1).tolist(),
        "bands": ((band_rows & 127) / 127).tolist(),
    }


def current_height_curve(values):
    values = np.asarray(values, dtype=float)
    companded = values ** 1.0
    return 0.84 * (companded + (1 / 64) * (1 - companded)
                   * (1 - np.exp(-values / (4 / 65535))))


def plot(data: dict, title: str, center: float, span: float, target: Path):
    """Generate a data plot from scratch, never alter an app screenshot."""
    width, left, right = 1500, 40, 1460
    image = Image.new("RGB", (width, 1040), (19, 21, 28))
    draw = ImageDraw.Draw(image)
    font_path = "/System/Library/Fonts/Supplemental/Arial.ttf"
    large = ImageFont.truetype(font_path, 30)
    medium = ImageFont.truetype(font_path, 21)
    small = ImageFont.truetype(font_path, 17)
    draw.text((left, 25), title, font=large, fill="#f1f3fa")
    draw.text((left, 69), "Real rekordbox data • diagnostic plot, not an app screenshot",
              font=medium, fill="#a9b4c7")
    start = center - span / 2
    xs = np.linspace(start, start + span, right - left)
    positions = xs * 150
    colors = np.asarray(data["colors"])
    sampled_colors = colors[np.clip(positions.astype(int), 0, len(colors) - 1)]
    rows = (
        ("BLUE · saved heights, linear connections", "blue", False),
        ("RGB · saved heights, linear connections", "rgb", False),
        ("RGB · same data with SoundBox’s current height curve", "rgb", True),
        ("3BAND · three saved envelopes (normalized 0–127)", "bands", False),
    )
    for i, (label, mode, transform) in enumerate(rows):
        top = 150 + i * 208
        draw.text((left, top - 34), label, font=medium, fill="#dbe1ef")
        values = np.asarray(data[mode])
        if transform:
            values = current_height_curve(values)
        if mode == "bands":
            heights = np.stack([
                np.interp(positions, np.arange(len(values)), values[:, j])
                for j in range(3)], axis=-1)
        else:
            heights = np.interp(positions, np.arange(len(values)), values)[:, None]
        yy = np.abs(np.arange(152)[:, None] - 75.5) / 74
        pixels = np.empty((152, right - left, 3), dtype=np.uint8)
        pixels[:] = (13, 14, 18)
        if mode == "bands":
            # Independent layers; membership colors are a diagnostic palette,
            # not a claim about Rekordbox's compositor or vertical calibration.
            bits = sum((yy <= heights[:, j][None, :]) * (1 << j) for j in range(3))
            palette = np.asarray([(13, 14, 18), (0, 85, 225), (255, 166, 0),
                                  (180, 105, 10), (245, 235, 215), (210, 220, 250),
                                  (245, 235, 215), (255, 255, 255)], dtype=np.uint8)
            pixels = palette[bits]
        else:
            mask = yy <= heights[:, 0][None, :]
            lane_colors = (np.tile([0.1, 0.58, 1.0], (right - left, 1))
                           if mode == "blue" else sampled_colors)
            full_colors = np.broadcast_to((lane_colors * 255).astype(np.uint8), pixels.shape)
            pixels[mask] = full_colors[mask]
        image.paste(Image.fromarray(pixels), (left, top))
        draw.line((width // 2, top, width // 2, top + 151), fill="#ff557b", width=2)
        for x, seconds in ((left, start), (width // 2 - 35, center), (right - 75, start + span)):
            draw.text((x, top + 159), f"{seconds:.3f}s", font=small, fill="#8392ad")
    draw.text((left, 996), "150 saved rows/sec • no audio decoding • no cache changes • equal time window in every lane",
              font=small, fill="#a9b4c7")
    image.save(target)


PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Waveform data preview</title>
<style>body{background:#13151c;color:#edf1fc;font:16px system-ui;margin:32px auto;max-width:1400px;padding:0 24px}
h1{font-size:28px}p{color:#a9b4c7;line-height:1.6}label{display:inline-block;margin:12px 24px 12px 0}
select,input{background:#232939;color:#fff;border:1px solid #424c63;border-radius:6px;padding:8px}
canvas{width:100%;height:240px;background:#0d0e12}h2{font-size:17px;margin:25px 0 8px}</style>
<h1>__TITLE__</h1><p>Real saved rekordbox waveform data. This is a diagnostic plot, <strong>not an app screenshot</strong>.
No audio is decoded. Shapes connect the saved 150-Hz rows linearly; colors are a preview palette.
The second lane isolates SoundBox’s height curve, not its complete current rendering.</p>
<label>Mode <select id="mode"><option>RGB</option><option>BLUE</option><option>3BAND</option></select></label>
<label>Visible seconds <select id="span"><option>0.5</option><option>1</option><option selected>2</option><option>4</option><option>8</option></select></label>
<label>Center time (seconds) <input id="center" type="number" min="0" step="0.05" value="__CENTER__"></label>
<h2>Saved heights · linear connections</h2><canvas id="raw"></canvas>
<h2 id="curveLabel">Same data · SoundBox’s current height curve</h2><canvas id="curve"></canvas>
<p id="window"></p><p>3BAND shows three independent envelopes normalized to their 7-bit range. Its duplicate second lane
does not apply the BLUE/RGB height curve. This preview does not establish visual parity with rekordbox.</p>
<script>
const data=__DATA__;
const curve=v=>.84*(v**1.0+(1/64)*(1-v**1.0)*(1-Math.exp(-v/(4/65535))));
function render(){const mode=document.querySelector('#mode').value, span=+document.querySelector('#span').value;
const max=Math.min(data.blue.length,data.rgb.length,data.bands.length)/150;
let center=Math.max(span/2,Math.min(max-span/2,+document.querySelector('#center').value||0));
document.querySelector('#center').value=center.toFixed(3);
document.querySelector('#curveLabel').textContent=mode==='3BAND'?'Same saved 3BAND data · no BLUE/RGB curve':'Same data · SoundBox’s current height curve';
document.querySelector('#window').textContent=`${(center-span/2).toFixed(3)}s – ${(center+span/2).toFixed(3)}s · ${Math.round(span*150)} saved rows`;
for(const [id,shaped] of [['raw',false],['curve',true]]){const canvas=document.querySelector('#'+id);
const w=Math.max(1,Math.round(canvas.clientWidth*devicePixelRatio)), h=Math.round(240*devicePixelRatio);canvas.width=w;canvas.height=h;
const ctx=canvas.getContext('2d'), img=ctx.createImageData(w,h);
for(let x=0;x<w;x++){const p=(center-span/2+x/(w-1)*span)*150, i=Math.max(0,Math.min(Math.floor(p),data.rgb.length-2)), f=p-i;
const key=mode==='BLUE'?'blue':'rgb';
let height=(1-f)*data[key][i]+f*data[key][i+1];if(shaped&&mode!=='3BAND')height=(1-f)*curve(data[key][i])+f*curve(data[key][i+1]);
const color=mode==='BLUE'?[.1,.58,1]:data.colors[i];const bands=[0,1,2].map(j=>(1-f)*data.bands[i][j]+f*data.bands[i+1][j]);
const palette=[[13,14,18],[0,85,225],[255,166,0],[180,105,10],[245,235,215],[210,220,250],[245,235,215],[255,255,255]];
for(let y=0;y<h;y++){const dy=Math.abs(y-h/2)/(h*.46);let c=[13,14,18];
if(mode==='3BAND'){let mask=0;for(let j=0;j<3;j++)if(dy<=bands[j])mask|=1<<j;c=palette[mask];}
else if(dy<=height)c=color.map(v=>Math.round(v*255));const k=(y*w+x)*4;img.data.set([...c,255],k);}}
ctx.putImageData(img,0,0);ctx.fillStyle='#ff557b';ctx.fillRect(w/2,0,2*devicePixelRatio,h);}}
document.querySelectorAll('select,input').forEach(el=>el.addEventListener('input',render));window.addEventListener('resize',render);render();
</script></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--center", type=float, required=True)
    parser.add_argument("--span", type=float, default=2.0)
    args = parser.parse_args()
    source_parent = args.analysis.resolve().parent
    output = args.out.resolve()
    if output == source_parent or source_parent in output.parents:
        parser.error("output must be outside the read-only analysis directory")
    packed = [_read_pwv3(args.analysis), _read_pwv5(args.analysis), _read_pwv7(args.analysis)]
    data = decode(*packed)
    if args.span <= 0 or args.center < args.span / 2 or args.center + args.span / 2 > min(
            len(data[key]) for key in ("blue", "rgb", "bands")) / 150:
        parser.error("requested window must be within all three streams")
    args.out.mkdir(parents=True, exist_ok=True)
    plot(data, args.title, args.center, args.span, args.out / "waveforms.png")
    # Standalone offline page: no server, imports, telemetry or remote assets.
    page = PAGE.replace("__TITLE__", html.escape(args.title)).replace("__CENTER__", str(args.center))
    page = page.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    (args.out / "index.html").write_text(page, encoding="utf-8")
    (args.out / "proof.json").write_text(json.dumps({
        "diagnostic_only": True, "audio_opened": False, "center_seconds": args.center,
        "span_seconds": args.span, "rows_per_second": 150,
        "streams": {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                    for name, raw in zip(("PWV3", "PWV5", "PWV7"), packed)},
    }, indent=2) + "\n")
    print(args.out / "index.html")


if __name__ == "__main__":
    main()
