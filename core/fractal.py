"""
FSC — Fractal Layer
Vrstva 4: IFS fraktálna dekompozícia s φ-rezmi (zlatý rez)

Affínne transformácie definujú geometrickú orbitu každého pixelu:
    T_k(x, y) = (a·x + b·y + e,  c·x + d·y + f)

Každý pixel v mriežke (H×W) dostane φ-váhované IFS skóre → triedenie
skóre definuje bijektívnu permutáciu pixelov (žiadna interpolácia,
presné round-trip).

Šifrovanie:  pixely preusporiadaj podľa permutácie P
Dešifrovanie: použij inverznú permutáciu P⁻¹
"""

import numpy as np
from dataclasses import dataclass, field
from typing import List

PHI = 1.6180339887498948  # zlatý rez


@dataclass
class AffineTransform:
    A: np.ndarray  # 2×2 lineárna časť [a, b; c, d]
    b: np.ndarray  # 2D posun  [e, f]


@dataclass
class FractalParams:
    fractal_seed: int
    phi_angle: float          # φ-modulovaný bazový uhol [rad], uložený v kľúči
    n_transforms: int
    transforms: List[AffineTransform] = field(repr=False)


def _rotation_matrix(theta: float) -> np.ndarray:
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[c, -s], [s, c]])


def _generate_single_transform(
    rng: np.random.Generator, phi_angle: float, canvas_size: int
) -> AffineTransform:
    """
    Generuje jednu invertibilnú affinnú transformáciu.
    Konštrukcia: A = D @ R(θ) @ Shear
    - θ modulovaný φ-uhlom, skosia ohraničená 1/φ²
    """
    theta = phi_angle * float(rng.uniform(-1.0, 1.0))
    sx = float(rng.uniform(0.85, 1.10))
    sy = float(rng.uniform(0.85, 1.10))
    shear = float(rng.uniform(-1.0 / PHI**2, 1.0 / PHI**2))

    R = _rotation_matrix(theta)
    S = np.array([[1.0, shear], [0.0, 1.0]])
    D = np.diag([sx, sy])
    A = D @ R @ S

    if abs(np.linalg.det(A)) < 1e-6:
        A = np.eye(2)

    limit = canvas_size * 0.15
    b = rng.uniform(-limit, limit, size=2)
    return AffineTransform(A=A, b=b)


def generate_fractal_params(seed: int, canvas_size: int = 128) -> FractalParams:
    """
    Vygeneruje IFS parametre pre jeden znak na základe seed hodnoty z kľúča.
    phi_angle je uložený v kľúči ako ľudsky čitateľný fraktálny odtlačok.
    """
    rng = np.random.default_rng(seed)
    phi_angle = float(rng.uniform(0.0, 2.0 * np.pi / PHI))
    n_transforms = int(rng.integers(3, 8))
    transforms = [
        _generate_single_transform(rng, phi_angle, canvas_size)
        for _ in range(n_transforms)
    ]
    return FractalParams(
        fractal_seed=seed,
        phi_angle=phi_angle,
        n_transforms=n_transforms,
        transforms=transforms,
    )


def _ifs_score(row: np.ndarray, col: np.ndarray, params: FractalParams) -> np.ndarray:
    """
    Pre každý pixel (row[i], col[i]) vypočíta IFS skóre akumuláciou
    transformácií váhovaných φ.

    Skóre = suma_k [ φ^k · (||T_k(x)|| + phi_angle·angle(T_k(x))) ]

    Toto skóre je deterministické a unikátne pre každý pixel →
    triedenie podľa skóre definuje bijekciu bez kolízií (pre generické parametre).
    """
    pts = np.stack([row.astype(np.float64), col.astype(np.float64)], axis=0)  # (2, N)
    score = np.zeros(pts.shape[1])
    phi_weight = 1.0

    for t in params.transforms:
        # T_k(x) = A @ x + b  (batch: A @ pts je (2,N))
        pts_t = t.A @ pts + t.b[:, np.newaxis]       # (2, N)
        r = np.sqrt(pts_t[0] ** 2 + pts_t[1] ** 2)  # polomer
        angle = np.arctan2(pts_t[1], pts_t[0])       # uhol
        score += phi_weight * (r + params.phi_angle * angle)
        phi_weight *= PHI
        pts = pts_t  # iteruj ďalej (IFS orbita)

    return score


def _build_permutation(params: FractalParams, H: int, W: int) -> np.ndarray:
    """
    Vracia permutáciu pixelových indexov odvodenú od IFS orbít.
    perm[i] = odkiaľ zoberieme pixel pre výstupnú pozíciu i.
    """
    total = H * W
    rows, cols = np.unravel_index(np.arange(total), (H, W))
    score = _ifs_score(rows.astype(float), cols.astype(float), params)

    # stable argsort: rovnaké skóre zachová pôvodné poradie (bez náhodnosti)
    perm = np.argsort(score, stable=True)
    return perm.astype(np.int64)


def apply_ifs(image: np.ndarray, params: FractalParams) -> np.ndarray:
    """
    Šifruje maticu znaku permutáciou pixelov odvodenou z IFS geometrie.
    Výstup má rovnaký tvar a rozsah hodnôt ako vstup — bez interpolácie.
    """
    H, W = image.shape
    flat = image.ravel()
    perm = _build_permutation(params, H, W)
    return flat[perm].reshape(H, W)


def reverse_ifs(image: np.ndarray, params: FractalParams) -> np.ndarray:
    """
    Dešifruje maticu znaku inverznou permutáciou — presný round-trip.
    """
    H, W = image.shape
    flat = image.ravel()
    perm = _build_permutation(params, H, W)
    inv_perm = np.empty_like(perm)
    inv_perm[perm] = np.arange(len(perm))
    return flat[inv_perm].reshape(H, W)


def encrypt(geometry_stack: np.ndarray, fractal_params: List[FractalParams]) -> dict:
    """
    Vstup:  geometry_stack (n_chars, H, W), fractal_params pre každý znak
    Výstup: dict so šifrovanou geometriou po IFS permutácii
    """
    transformed = np.stack([
        apply_ifs(geometry_stack[i], fractal_params[i])
        for i in range(len(fractal_params))
    ])
    return {
        "transformed": transformed,
        "params": fractal_params,
    }


def decrypt(fractal_output: dict) -> np.ndarray:
    """
    Reverzia fraktálovej vrstvy.
    Vyžaduje fractal_params z kľúča — bez nich je reverzia nemožná.
    """
    params = fractal_output["params"]
    transformed = fractal_output["transformed"]
    return np.stack([
        reverse_ifs(transformed[i], params[i])
        for i in range(len(params))
    ])
