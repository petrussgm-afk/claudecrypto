"""
FSC — Layer Visualizer

Renders each encryption layer as a row in a single matplotlib figure.
Accepts enc_state dict from core.pipeline.encrypt().

Row layout (one column per character):
  1  Renderer    — original rendered character (float32)
  2  Material    — after Beer-Lambert attenuation (darkening visible)
  3  Isotope     — after radioactive decay (further darkening)
  4  Fractal     — after IFS pixel permutation (scrambled)
  5  Quantizer   — Planck-discretized uint16 → displayed as grayscale
  6  Blackhole   — Lorenz XOR ciphertext (should look like noise)

Rows 1–4 share the same vmax (renderer max) so progressive darkening
across layers is visually apparent. Rows 5–6 use [0, 255].
"""

import numpy as np
import matplotlib
matplotlib.use("Agg")          # non-interactive backend — safe for scripts
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable


def _row_label(ax, text: str, fontsize: int = 9):
    """Write a vertical row label on the left of an axes."""
    ax.set_ylabel(text, fontsize=fontsize, rotation=0,
                  labelpad=6, ha="right", va="center",
                  fontfamily="monospace")
    ax.yaxis.set_label_coords(-0.08, 0.5)


def visualize(enc_state: dict, save_path: str = "fsc_layers.png") -> str:
    """
    Build and save the 6-row layer figure.

    Parameters
    ----------
    enc_state : dict
        Output of core.pipeline.encrypt().
    save_path : str
        Destination file.  Returns the path on success.
    """
    text   = enc_state["text"]
    n      = len(text)
    key    = enc_state["key"]

    # ── collect per-layer arrays ──────────────────────────────────────────
    # float layers (rows 1–4): shape (n, H, W)
    arr_renderer  = enc_state["geometry"]
    arr_material  = enc_state["material_out"]["attenuated"]
    arr_isotope   = enc_state["isotope_out"]["decayed"]
    arr_fractal   = enc_state["fractal_out"]["transformed"]

    # integer layers (rows 5–6)
    arr_quantizer = enc_state["quant_out"]["quantized"].astype(np.float32)   # uint16 → float
    arr_blackhole = enc_state["bh_out"]["cipher"].astype(np.float32)         # uint8  → float

    # ── per-column vmax for float rows ───────────────────────────────────
    # Each column (character) is normalised to its own renderer max so the
    # glyph is always visible regardless of the randomly assigned colour.
    # The ratio between rows (row2 < row1 etc.) still shows attenuation.
    float_vmaxes = [float(arr_renderer[c].max()) or 1.0 for c in range(n)]
    float_vmin   = 0.0

    # ── per-layer metadata strings ────────────────────────────────────────
    mp_list = enc_state["material_params"]
    ip_list = enc_state["isotope_params"]
    fp_list = enc_state["fractal_params"]
    qp      = enc_state["quant_params"]

    def _mat_label(i):
        mp = mp_list[i]
        atten = np.exp(-mp.mu * mp.thickness)
        return f"{mp.material}\nμ={mp.mu:.3f}  atten={atten:.2f}"

    def _iso_label(i):
        ip = ip_list[i]
        return f"{ip.isotope}\nn₀={ip.n0:.2f}"

    def _frac_label(i):
        fp = fp_list[i]
        return f"φ={fp.phi_angle:.2f}  T={fp.n_transforms}"

    # ── figure layout ─────────────────────────────────────────────────────
    N_ROWS   = 6
    FIG_W    = max(8.0, n * 2.4 + 2.5)
    FIG_H    = N_ROWS * 2.2 + 0.8
    CMAP_F   = "gray"           # float layers
    CMAP_I   = "inferno"        # integer / noise layers — distinct look

    fig = plt.figure(figsize=(FIG_W, FIG_H), facecolor="#0e0e12")

    # left margin for row labels, right margin for colorbars, top for col headers
    gs = gridspec.GridSpec(
        N_ROWS, n,
        figure=fig,
        left=0.18, right=0.88,
        top=0.93,  bottom=0.04,
        hspace=0.08, wspace=0.06,
    )

    # ── colorbar axes (one per row, on the right) ─────────────────────────
    cb_axes = []
    for row in range(N_ROWS):
        cax = fig.add_axes([0.895, 0.04 + row * (0.89 / N_ROWS) + 0.01,
                            0.012, 0.89 / N_ROWS - 0.02])
        cb_axes.append(cax)
    cb_axes.reverse()   # top row → cb_axes[0]

    # ── helper: draw one image cell ───────────────────────────────────────
    def _draw(row, col, arr, vmin, vmax, cmap, col_label=None, sub_label=None):
        ax = fig.add_subplot(gs[row, col])
        ax.imshow(arr, cmap=cmap, vmin=vmin, vmax=vmax,
                  interpolation="nearest", aspect="equal")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_edgecolor("#444455")
            spine.set_linewidth(0.6)

        # column header (top row only)
        if col_label:
            ax.set_title(col_label, fontsize=11, fontfamily="monospace",
                         color="#e0e0f0", pad=4, fontweight="bold")

        # sub-annotation below image
        if sub_label:
            ax.text(0.5, -0.04, sub_label, transform=ax.transAxes,
                    fontsize=6.5, color="#aaaacc", ha="center", va="top",
                    fontfamily="monospace")
        return ax

    # ── row definitions ───────────────────────────────────────────────────
    # vmax=None → use per-column float_vmaxes; a float → fixed scale
    rows = [
        # (arrays, vmax_or_None, cmap, row_title, sub_label_fn)
        (arr_renderer,  None,  CMAP_F,
         "1 · Renderer\n    (original)",
         lambda i: f"char='{text[i]}'"),

        (arr_material,  None,  CMAP_F,
         "2 · Material\n    (X-ray attn.)",
         _mat_label),

        (arr_isotope,   None,  CMAP_F,
         "3 · Isotope\n    (decay)",
         _iso_label),

        (arr_fractal,   None,  CMAP_F,
         "4 · Fractal\n    (IFS permut.)",
         _frac_label),

        (arr_quantizer, 255.0, CMAP_F,
         f"5 · Quantizer\n    ({qp.n_levels} levels)",
         lambda i: f"step={qp.step:.3e}"),

        (arr_blackhole, 255.0, CMAP_I,
         "6 · Blackhole\n    (Lorenz XOR)",
         lambda i: f"entropy≈8 bit"),
    ]

    # ── draw all cells ────────────────────────────────────────────────────
    for r, (arr, fixed_vmax, cmap, row_title, sub_fn) in enumerate(rows):
        # colorbar uses the first column's scale for per-column rows
        cb_vmax = fixed_vmax if fixed_vmax is not None else float_vmaxes[0]

        for c in range(n):
            vmax = fixed_vmax if fixed_vmax is not None else float_vmaxes[c]
            col_lbl = f"'{text[c]}'" if r == 0 else None
            ax = _draw(r, c, arr[c], float_vmin, vmax, cmap,
                       col_label=col_lbl, sub_label=sub_fn(c))
            # row label on first column only
            if c == 0:
                ax.set_ylabel(row_title, fontsize=8.5, rotation=0,
                              labelpad=8, ha="right", va="center",
                              color="#ccccee", fontfamily="monospace",
                              fontweight="bold")
                ax.yaxis.set_label_coords(-0.12, 0.5)

        # colorbar
        norm = Normalize(vmin=float_vmin, vmax=cb_vmax)
        sm   = ScalarMappable(cmap=cmap, norm=norm)
        sm.set_array([])
        cb = fig.colorbar(sm, cax=cb_axes[r])
        cb.ax.tick_params(labelsize=6, colors="#aaaacc")
        cb.outline.set_edgecolor("#444455")

    # ── figure title ──────────────────────────────────────────────────────
    fig.suptitle(
        f"FSC — Fractal Singularity Cipher  |  text={text!r}  seed={key.master_seed}",
        fontsize=10, color="#e8e8ff", fontfamily="monospace",
        y=0.975,
    )
    fig.patch.set_facecolor("#0e0e12")

    fig.savefig(save_path, dpi=160, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return save_path


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from keys.keygen import generate
    from core.pipeline import encrypt

    text = sys.argv[1] if len(sys.argv) > 1 else "FSC"
    key  = generate(text, master_seed=42)
    enc  = encrypt(text, key)
    out  = visualize(enc, "fsc_layers.png")
    print(f"Saved: {out}")
