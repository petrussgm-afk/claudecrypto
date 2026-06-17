"""
art/lorenz_portrait.py — Lorenz attractor portrait for a given FSCKey.

Integrates the Lorenz system from the key's lorenz_init (mixed with a fixed
zero nonce for reproducibility), collects 100 000 trajectory points in the
x–z plane, and renders them with the plasma colormap coloured by |dx/dt|.

Usage (standalone):
    python art/lorenz_portrait.py [text]
    # generates a random FSCKey for `text` (default "FSC") and saves the portrait

Importable:
    from art.lorenz_portrait import lorenz_portrait
    path = lorenz_portrait(key)          # → "art/portrait_<key_hex4>.png"
    path = lorenz_portrait(key, n_steps=200_000, save_path="custom.png")
"""

import os
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.blackhole import _rk4_step, _derive_lorenz_init, BlackholeParams, SIGMA, RHO, BETA, DT, N_WARMUP
from keys.keygen import FSCKey


def lorenz_portrait(
    key: FSCKey,
    save_path: str = None,
    n_steps: int = 100_000,
) -> str:
    """
    Generate and save a Lorenz attractor portrait from an FSCKey.

    Parameters
    ----------
    key       : FSCKey — provides lorenz_init and master_key for labelling.
    save_path : output file path; defaults to art/portrait_<key4hex>.png.
    n_steps   : number of trajectory points after warm-up (default 100 000).

    Returns
    -------
    str — absolute path of the saved PNG.
    """
    # ── save path ─────────────────────────────────────────────────────────
    art_dir = os.path.dirname(os.path.abspath(__file__))
    if save_path is None:
        key_tag   = key.master_key[:4].hex()
        save_path = os.path.join(art_dir, f"portrait_{key_tag}.png")

    # ── derive effective initial condition ─────────────────────────────────
    # Fixed nonce (all zeros) → deterministic portrait for the same key.
    params = BlackholeParams(lorenz_init=key.lorenz_init, nonce=b'\x00' * 16)
    x, y, z = _derive_lorenz_init(params)

    # ── warm-up: settle onto attractor ────────────────────────────────────
    for _ in range(N_WARMUP):
        x, y, z = _rk4_step(x, y, z, DT)

    # ── integrate n_steps, collect x, z, and |dx/dt| ──────────────────────
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

    # hexbin aggregates 100k points into density bins coloured by mean |dx/dt|.
    # gridsize=220 gives fine attractor structure without empty bins.
    hb = ax.hexbin(
        xs, zs,
        C=speeds,
        reduce_C_function=np.mean,
        gridsize=220,
        cmap="plasma",
        linewidths=0.0,
        mincnt=1,         # hide empty bins (shows as background)
    )

    # ── axes styling ──────────────────────────────────────────────────────
    for spine in ax.spines.values():
        spine.set_edgecolor("#1e1e2e")
        spine.set_linewidth(0.5)

    ax.tick_params(colors="#44445a", labelsize=7, length=3)
    ax.set_xlabel("x", fontsize=9, color="#6666aa", fontfamily="monospace", labelpad=6)
    ax.set_ylabel("z", fontsize=9, color="#6666aa", fontfamily="monospace",
                  rotation=0, labelpad=10)

    # ── colorbar ──────────────────────────────────────────────────────────
    cax = fig.add_axes([0.905, 0.12, 0.018, 0.70])
    cb  = fig.colorbar(hb, cax=cax)
    cb.set_label("mean |dx/dt|  (σ|y − x|)", fontsize=7.5, color="#888899",
                 fontfamily="monospace", labelpad=8)
    cb.ax.tick_params(labelsize=6, colors="#888899")
    cb.outline.set_edgecolor("#1e1e2e")

    # ── title ─────────────────────────────────────────────────────────────
    init_str = "[" + ", ".join(f"{v:.4f}" for v in key.lorenz_init) + "]"
    ax.set_title(
        f"FSC — Lorenz Attractor Portrait\n"
        f"lorenz_init = {init_str}   key = {key.master_key[:4].hex()}…",
        fontsize=9.5, color="#c0b8f0", fontfamily="monospace",
        pad=10, loc="left",
    )

    # ── parameter annotation (bottom-left) ────────────────────────────────
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


# ── standalone entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    from keys.keygen import generate

    text = sys.argv[1] if len(sys.argv) > 1 else "FSC"
    key  = generate(text)
    out  = lorenz_portrait(key)
    print(f"Saved: {out}")
    print(f"Key:   {key.key_hex}")
    print(f"Init:  {[round(v, 6) for v in key.lorenz_init]}")
