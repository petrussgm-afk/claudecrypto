"""
FSC — Fractal Singularity Cipher   Streamlit demo
Run: streamlit run demo/app.py
"""
import sys, os, time, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="FSC — Fractal Singularity Cipher",
    page_icon="🕳️",
    layout="wide",
    initial_sidebar_state="expanded",
)

from keys.keygen import generate
from core.pipeline import encrypt
from core.blackhole import BlackholeParams, _generate_keystream
from viz.visualizer import visualize


# ── helpers ───────────────────────────────────────────────────────────────────

def _entropy(cipher: np.ndarray) -> float:
    counts = np.bincount(cipher.ravel(), minlength=256)
    probs  = counts[counts > 0] / counts.sum()
    return float(-np.dot(probs, np.log2(probs)))


def _fmt_halflife(seconds: float) -> str:
    if seconds < 1e-3:             return f"{seconds*1e6:.2g} µs"
    if seconds < 1:                return f"{seconds*1e3:.2g} ms"
    if seconds < 60:               return f"{seconds:.2g} s"
    if seconds < 3_600:            return f"{seconds/60:.2g} min"
    if seconds < 86_400:           return f"{seconds/3600:.2g} h"
    if seconds < 365.25 * 86_400:  return f"{seconds/86400:.2g} d"
    yr = seconds / (365.25 * 86_400)
    if yr < 1e6:                   return f"{yr:.3g} yr"
    return f"{yr:.2e} yr"


def _hex_dump(data: np.ndarray, n: int = 256, cols: int = 16) -> str:
    flat = data.ravel()[:n]
    lines = []
    for i in range(0, len(flat), cols):
        chunk     = flat[i : i + cols]
        hex_part  = " ".join(f"{b:02X}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "·" for b in chunk)
        lines.append(f"{i:04X}  {hex_part:<{cols * 3}}  {ascii_part}")
    return "\n".join(lines)


def _run_encrypt(text: str, seed: int, canvas: int, planck: int) -> dict:
    key = generate(text, master_key=seed.to_bytes(32, 'big'), canvas_size=canvas, planck_resolution=planck)
    t0  = time.perf_counter()
    enc = encrypt(text, key)
    enc["_t_ms"] = (time.perf_counter() - t0) * 1000
    return enc


# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🕳️ FSC")
    st.caption("Fractal Singularity Cipher")
    st.divider()

    msg    = st.text_input("Message", value="HELLO WORLD", max_chars=30,
                           placeholder="10–30 characters")
    seed   = st.number_input("Master seed", min_value=0, max_value=2**32 - 1,
                              value=42, step=1)
    canvas = st.select_slider("Canvas size", options=[64, 128, 256], value=128)
    planck = st.select_slider("Planck resolution", options=[128, 256, 512], value=256)

    encrypt_btn = st.button("⚡ Encrypt", type="primary", use_container_width=True)

    st.divider()
    st.caption("""
**7 encryption layers**

`1` Renderer — vector geometry
`2` Material — Beer-Lambert X-ray
`3` Isotope — radioactive decay
`4` Fractal — IFS φ-permutation
`5` Quantizer — Planck discretize
`6` Blackhole — Lorenz XOR
`7` OTP — perfect secrecy
""")


# ── main title ────────────────────────────────────────────────────────────────

st.title("FSC — Fractal Singularity Cipher")
st.caption("A 7-layer cryptographic proof-of-concept combining fractal geometry, "
           "material physics, isotope decay, Lorenz chaos, and a One-Time Pad.")


# ── input validation ──────────────────────────────────────────────────────────

if encrypt_btn:
    if not msg or not msg.strip():
        st.error("Message cannot be empty.")
        st.stop()
    if len(msg) > 30:
        st.error("Message must be ≤ 30 characters.")
        st.stop()

    with st.spinner(f"Running 7-layer pipeline on {len(msg)} characters…"):
        enc = _run_encrypt(msg.strip(), int(seed), int(canvas), int(planck))
    st.session_state["enc"] = enc


if "enc" not in st.session_state:
    st.info("👈  Enter a message in the sidebar and press **⚡ Encrypt**.")
    st.stop()


enc    = st.session_state["enc"]
text   = enc["text"]
key    = enc["key"]
qp     = enc["quant_params"]
cipher = enc["otp_out"]
t_ms   = enc["_t_ms"]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Layer visualization
# ─────────────────────────────────────────────────────────────────────────────

with st.spinner("Rendering layer visualization…"):
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    visualize(enc, save_path=tmp.name)

st.image(tmp.name, use_container_width=True,
         caption=f"FSC 7-layer encryption — text={text!r}")
try:
    os.unlink(tmp.name)
except OSError:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# 2. Metrics
# ─────────────────────────────────────────────────────────────────────────────

c1, c2, c3, c4 = st.columns(4)
c1.metric("Encrypt time", f"{t_ms:.1f} ms")
c2.metric("Entropy", f"{_entropy(cipher):.3f} / 8.000 bit")
c3.metric("Ciphertext", f"{cipher.size:,} bytes")
c4.metric("OTP pad", f"{key.otp_pad_kb:.1f} kB")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Per-character table
# ─────────────────────────────────────────────────────────────────────────────

st.subheader("Per-character key parameters")

rows = []
for i, ch in enumerate(text):
    mp = enc["material_params"][i]
    ip = enc["isotope_params"][i]
    fp = enc["fractal_params"][i]
    rows.append({
        "char":         ch,
        "material":     mp.material,
        "µ [1/cm]":     round(mp.mu, 4),
        "thickness cm": round(mp.thickness, 3),
        "attenuation":  round(float(np.exp(-mp.mu * mp.thickness)), 4),
        "isotope":      ip.isotope,
        "half-life":    _fmt_halflife(ip.half_life),
        "φ-angle":      round(fp.phi_angle, 4),
        "n_transforms": fp.n_transforms,
    })

st.dataframe(
    pd.DataFrame(rows),
    use_container_width=True,
    hide_index=True,
    column_config={
        "char":         st.column_config.TextColumn("Char", width="small"),
        "material":     st.column_config.TextColumn("Material"),
        "µ [1/cm]":     st.column_config.NumberColumn("µ [1/cm]", format="%.4f"),
        "thickness cm": st.column_config.NumberColumn("Thickness (cm)", format="%.3f"),
        "attenuation":  st.column_config.ProgressColumn(
                            "Attenuation", min_value=0, max_value=1, format="%.3f"),
        "isotope":      st.column_config.TextColumn("Isotope"),
        "half-life":    st.column_config.TextColumn("Half-life"),
        "φ-angle":      st.column_config.NumberColumn("φ-angle", format="%.4f"),
        "n_transforms": st.column_config.NumberColumn("IFS transforms", width="small"),
    },
)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Butterfly effect
# ─────────────────────────────────────────────────────────────────────────────

with st.expander("🦋 Key sensitivity — butterfly effect"):
    N_STEPS   = 3000
    init_orig  = key.lorenz_init
    init_nudge = [init_orig[0] + 1e-10, init_orig[1], init_orig[2]]

    with st.spinner("Generating comparison keystreams…"):
        k1 = _generate_keystream(BlackholeParams(lorenz_init=init_orig),  N_STEPS)
        k2 = _generate_keystream(BlackholeParams(lorenz_init=init_nudge), N_STEPS)

    diff_count = int(np.sum(k1 != k2))
    diff_pct   = diff_count / N_STEPS * 100

    bc1, bc2, bc3 = st.columns(3)
    bc1.metric("Keystream divergence", f"{diff_pct:.1f}%")
    bc2.metric("Bytes differ", f"{diff_count} / {N_STEPS}")
    bc3.metric("Perturbation on x₀", "1 × 10⁻¹⁰")

    st.caption(
        f"The original `lorenz_init` is `{[round(v, 6) for v in init_orig]}`. "
        f"After shifting `x₀` by just **1×10⁻¹⁰**, the Lorenz trajectory diverges "
        f"exponentially (Lyapunov exponent ≈ 0.9). "
        f"After **{1000 + N_STEPS} RK4 steps**, {diff_pct:.1f}% of keystream bytes differ. "
        f"An attacker who does not know `lorenz_init` to 10+ decimal places "
        f"cannot reconstruct the keystream."
    )

    # visual comparison of first 64 bytes
    st.markdown("**First 64 keystream bytes — original vs. perturbed:**")
    col_a, col_b = st.columns(2)
    with col_a:
        st.caption("Original `lorenz_init`")
        st.code(" ".join(f"{b:02X}" for b in k1[:64]), language=None)
    with col_b:
        st.caption("Perturbed by 1×10⁻¹⁰")
        st.code(" ".join(f"{b:02X}" for b in k2[:64]), language=None)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Raw ciphertext hex dump
# ─────────────────────────────────────────────────────────────────────────────

with st.expander("🔢 Raw ciphertext (hex)"):
    shown = min(256, cipher.size)
    st.caption(f"First {shown} of {cipher.size:,} bytes  "
               f"— canvas {key.canvas_size}×{key.canvas_size}  "
               f"× {len(text)} chars  × 1 byte/pixel")
    st.code(_hex_dump(cipher, n=shown), language=None)
