"""
FSC — Compton Scatter (material sub-layer)
Klein-Nishina angular scattering, made cryptographically DETERMINISTIC.

Physically, Compton scattering is a stochastic Monte-Carlo process: a photon
scatters off an electron at a random angle θ drawn from the Klein-Nishina
differential cross-section, losing energy E → E'. That randomness makes real
Compton irreversible.

For a cipher we need reversibility, so we sample θ DETERMINISTICALLY from the
key seed via inverse-CDF sampling of the Klein-Nishina distribution:

    same seed  →  same θ  →  same scatter factor  →  exactly reversible.

Honest framing: this is *physically-inspired, cryptographically deterministic*.
It borrows the Klein-Nishina shape for angular/energy entropy but replaces the
physical dice roll with a key-derived one so decryption can undo it exactly.

Constants (SI / keV):
    m_e c² = 511.0 keV      (electron rest energy)
    r_e    = 2.818e-15 m    (classical electron radius)
"""

import numpy as np

m_e_c2 = 511.0        # electron rest energy [keV]
r_e    = 2.818e-15    # classical electron radius [m]

# Angular bin count for the inverse-CDF sampling grid over θ ∈ [0, π].
N_THETA_BINS = 180

# Reference atomic number used to normalise the Z-blend (lead, Z = 82).
Z_REF = 82.0


def compton_energy(E_keV, theta):
    """
    Scattered photon energy after Compton scatter through angle θ.

        E' = E / (1 + (E / 511)(1 − cos θ))

    Vectorised over theta. At θ = 0 → E' = E (no energy loss).
    """
    E = np.asarray(E_keV, dtype=np.float64)
    ct = np.cos(theta)
    return E / (1.0 + (E / m_e_c2) * (1.0 - ct))


def klein_nishina_weight(E_keV, theta):
    """
    Klein-Nishina differential cross-section (unnormalised angular weight):

        ratio = E' / E
        dσ/dΩ = 0.5 · r_e² · ratio² · (ratio + 1/ratio − sin²θ)

    Used purely as a relative weight to build the sampling CDF; the absolute
    scale (r_e²) cancels on normalisation. Vectorised over theta.
    """
    E = np.asarray(E_keV, dtype=np.float64)
    Ep = compton_energy(E, theta)
    ratio = Ep / E
    st2 = np.sin(theta) ** 2
    return 0.5 * r_e**2 * ratio**2 * (ratio + 1.0 / ratio - st2)


def scatter_factor(Z, E_keV, theta):
    """
    Final intensity scale factor for one scatter event, blended by atomic number.

        raw      = E'(θ) / E                     ∈ (0, 1]   (energy retained)
        z_weight = min(Z / 82, 1)                ∈ (0, 1]   (higher Z → more Compton)
        factor   = 1 − z_weight · (1 − raw)      ∈ (0, 1]

    Higher-Z materials scatter more strongly (factor pulled toward `raw`);
    low-Z materials stay near 1. At θ = 0, raw = 1 → factor = 1 (identity) for
    every Z, so Compton at θ = 0 is a perfect no-op.
    """
    raw = compton_energy(E_keV, theta) / E_keV
    z_weight = min(float(Z) / Z_REF, 1.0)
    return 1.0 - z_weight * (1.0 - raw)


def derive_scatter_angle(seed, E_keV):
    """
    Deterministically sample a scatter angle θ ∈ [0, π] from `seed`.

    Builds the Klein-Nishina CDF over N_THETA_BINS angular bins for photon
    energy E_keV, draws a single uniform u from `seed`, and returns the
    inverse-CDF angle. Deterministic: same seed (and E) → same θ.

    Returns θ in radians.
    """
    thetas = np.linspace(0.0, np.pi, N_THETA_BINS)
    weights = klein_nishina_weight(E_keV, thetas)
    cdf = np.cumsum(weights)
    cdf /= cdf[-1]
    u = np.random.default_rng(int(seed)).random()
    idx = int(np.searchsorted(cdf, u))
    idx = min(idx, N_THETA_BINS - 1)
    return float(thetas[idx])


def apply_compton(intensity_array, Z, E_keV, seed):
    """
    Apply one deterministic Compton scatter to an intensity array.

    Returns (scattered_array, theta, factor) where
        theta  = derive_scatter_angle(seed, E_keV)
        factor = scatter_factor(Z, E_keV, theta)   ∈ (0, 1]
    and scattered_array = intensity_array · factor.

    The returned `factor` is exactly what reverse_compton needs to invert this.
    """
    theta = derive_scatter_angle(seed, E_keV)
    factor = scatter_factor(Z, E_keV, theta)
    return intensity_array * factor, theta, factor


def reverse_compton(scattered_array, scatter_factor_value):
    """
    Exact inverse of apply_compton: divide out the scatter factor.

        intensity = scattered / factor
    """
    if scatter_factor_value <= 0.0:
        raise ValueError(f"scatter_factor must be > 0 for reversal, got {scatter_factor_value}")
    return scattered_array / scatter_factor_value
