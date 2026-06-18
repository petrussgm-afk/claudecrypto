"""
art/lorenz_portrait.py — Lorenz attractor portrait.

Two entry points:

  lorenz_portrait(key, ...)             — from a full FSCKey
  lorenz_portrait_from_master(mk, ...)  — from raw master_key bytes
                                          (Key Forge calls this before any text
                                          is bound to the key)

Both render the x–z projection of a 100 000-step RK4 Lorenz trajectory as a
hexbin density map coloured by mean |dx/dt| with the plasma colormap.

Usage (standalone):
    py art/lorenz_portrait.py [text]
"""

import hashlib
import os
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.blackhole import (
    _rk4_step, _derive_lorenz_init, BlackholeParams,
    SIGMA, RHO, BETA, DT, N_WARMUP,
)


# ── master_key → lorenz_init derivation (matches keys/keygen.py) ──────────────

def derive_lorenz_init(master_key: bytes) -> list:
    """SHAKE256-derived [x0, y0, z0] for a 32-byte master_key."""
    raw = hashlib.shake_256(master_key + b'lorenz').digest(24)
    x = int.from_bytes(raw[0:8],  'big') / 2**64 * 40.0 - 20.0   # [-20, 20]
    y = int.from_bytes(raw[8:16], 'big') / 2**64 * 40.0 - 20.0   # [-20, 20]
    z = int.from_bytes(raw[16:24],'big') / 2**64 * 50.0           # [0, 50]
    return [x, y, z]


# ── core renderer ─────────────────────────────────────────────────────────────

def _render_portrait(
    lorenz_init: list,
    key_tag: str,
    save_path: str,
    n_steps: int = 100_000,
) -> str:
    """Integrate Lorenz from lorenz_init (zero-nonce) and save a portrait PNG."""
    # ── derive effective initial condition (fixed zero nonce → deterministic) ──
    params = BlackholeParams(lorenz_init=lorenz_init, nonce=b'\x00' * 16)
    x, y, z = _derive_lorenz_init(params)

    # ── warm-up ───────────────────────────────────────────────────────────
    for _ in range(N_WARMUP):
        x, y, z = _rk4_step(x, y, z, DT)

    # ── collect x, z, |dx/dt| ─────────────────────────────────────────────
    xs     = np.empty(n_steps, dtype=np.float64)
    zs     = np.empty(n_steps, dtype=np.float64)
    speeds = np.empty(n_steps, dtype=np.float64)
    for i in range(n_steps):
        x, y, z   = _rk4_step(x, y, z, DT)
        xs[i]     = x
        zs[i]     = z
        speeds[i] = abs(SIGMA * (y - x))   # |dx/dt| = σ|y − x|

    # ── figure ────────────────────────────────────────────────────────────
    BG = "#050508"
    fig, ax = plt.subplots(figsize=(11, 7.5), facecolor=BG)
    ax.set_facecolor(BG)
    fig.subplots_adjust(left=0.08, right=0.88, top=0.88, bottom=0.09)

    hb = ax.hexbin(
        xs, zs, C=speeds, reduce_C_function=np.mean,
        gridsize=220, cmap="plasma",
        linewidths=0.0, mincnt=1,
    )

    for spine in ax.spines.values():
        spine.set_edgecolor("#1e1e2e")
        spine.set_linewidth(0.5)

    ax.tick_params(colors="#44445a", labelsize=7, length=3)
    ax.set_xlabel("x", fontsize=9, color="#6666aa", fontfamily="monospace", labelpad=6)
    ax.set_ylabel("z", fontsize=9, color="#6666aa", fontfamily="monospace",
                  rotation=0, labelpad=10)

    cax = fig.add_axes([0.905, 0.12, 0.018, 0.70])
    cb  = fig.colorbar(hb, cax=cax)
    cb.set_label("mean |dx/dt|  (σ|y − x|)", fontsize=7.5, color="#888899",
                 fontfamily="monospace", labelpad=8)
    cb.ax.tick_params(labelsize=6, colors="#888899")
    cb.outline.set_edgecolor("#1e1e2e")

    init_str = "[" + ", ".join(f"{v:.4f}" for v in lorenz_init) + "]"
    ax.set_title(
        f"FSC — Lorenz Attractor Portrait\n"
        f"lorenz_init = {init_str}   key = {key_tag}…",
        fontsize=9.5, color="#c0b8f0", fontfamily="monospace",
        pad=10, loc="left",
    )

    ax.text(
        0.01, 0.017,
        f"σ={SIGMA:.0f}  ρ={RHO:.0f}  β=8/3  dt={DT}  "
        f"steps={n_steps:,}  warmup={N_WARMUP}  projection: x–z",
        transform=ax.transAxes,
        fontsize=6.5, color="#333355", fontfamily="monospace",
    )

    fig.savefig(save_path, dpi=180, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return save_path


# ── public entry points ───────────────────────────────────────────────────────

def lorenz_portrait_from_master(
    master_key: bytes,
    save_path: str = None,
    n_steps: int = 100_000,
) -> str:
    """
    Render a Lorenz portrait directly from raw master_key bytes.
    No FSCKey / text binding required — useful for Key Forge previews.
    """
    if save_path is None:
        art_dir   = os.path.dirname(os.path.abspath(__file__))
        save_path = os.path.join(art_dir, f"portrait_{master_key[:4].hex()}.png")
    return _render_portrait(
        lorenz_init=derive_lorenz_init(master_key),
        key_tag=master_key[:4].hex(),
        save_path=save_path,
        n_steps=n_steps,
    )


def lorenz_portrait(key, save_path: str = None, n_steps: int = 100_000) -> str:
    """Render a Lorenz portrait from a full FSCKey."""
    if save_path is None:
        art_dir   = os.path.dirname(os.path.abspath(__file__))
        save_path = os.path.join(art_dir, f"portrait_{key.master_key[:4].hex()}.png")
    return _render_portrait(
        lorenz_init=key.lorenz_init,
        key_tag=key.master_key[:4].hex(),
        save_path=save_path,
        n_steps=n_steps,
    )


# ── standalone entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    from keys.keygen import generate

    text = sys.argv[1] if len(sys.argv) > 1 else "FSC"
    key  = generate(text)
    out  = lorenz_portrait(key)
    print(f"Saved: {out}")
    print(f"Key:   {key.key_hex}")
    print(f"Init:  {[round(v, 6) for v in key.lorenz_init]}")
