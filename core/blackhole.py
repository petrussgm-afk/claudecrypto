"""
FSC — Blackhole Layer
Vrstva 6: Lorenzov chaotický prúdový šifier (stream XOR)

Lorenzov atraktor:
    dx/dt = sigma*(y - x)
    dy/dt = x*(rho - z) - y
    dz/dt = x*y - beta*z
    sigma=10, rho=28, beta=8/3

Keystream: trajektória ODE integrovaná RK4 → x-zložka → bajty.
Šifrovanie: ciphertext = data XOR keystream(lorenz_init, nonce)
Dešifrovanie: data = ciphertext XOR keystream(lorenz_init, nonce)   [XOR je samoinverzná]

Bezpečnostná vlastnosť: nonce zmiešaný s lorenz_init cez SHAKE256 zaručuje,
že rovnaký lorenz_init s odlišným nonce produkuje úplne odlišný keystream.
"""

import hashlib
import secrets
import struct
import numpy as np
from dataclasses import dataclass, field

SIGMA = 10.0
RHO   = 28.0
BETA  = 8.0 / 3.0
DT    = 0.01          # časový krok RK4
N_WARMUP = 1000       # kroky pred odberom — usadenie na attraktore


@dataclass
class BlackholeParams:
    lorenz_init: list  # [x0, y0, z0]
    dt: float = DT
    nonce: bytes = field(default_factory=lambda: secrets.token_bytes(16))


def _derive_lorenz_init(params: BlackholeParams) -> tuple:
    """Mix lorenz_init with nonce via SHAKE256 → unique effective initial condition."""
    lorenz_bytes = struct.pack('<3d', *params.lorenz_init)
    h = hashlib.shake_256(lorenz_bytes + params.nonce)
    raw = h.digest(24)
    # Integer scaling avoids subnormal floats (which can land on ODE fixed points)
    x = int.from_bytes(raw[0:8],  'big') / 2**64 * 40.0 - 20.0   # [-20, 20]
    y = int.from_bytes(raw[8:16], 'big') / 2**64 * 40.0 - 20.0   # [-20, 20]
    z = int.from_bytes(raw[16:24],'big') / 2**64 * 50.0           # [0, 50]
    return (x, y, z)


def _rk4_step(x: float, y: float, z: float, dt: float):
    """Jeden krok RK4 pre Lorenzov systém. Vracia (x, y, z) v čase t+dt."""
    k1x = SIGMA * (y - x)
    k1y = x * (RHO - z) - y
    k1z = x * y - BETA * z

    mx = x + 0.5*dt*k1x;  my = y + 0.5*dt*k1y;  mz = z + 0.5*dt*k1z
    k2x = SIGMA * (my - mx)
    k2y = mx * (RHO - mz) - my
    k2z = mx * my - BETA * mz

    mx = x + 0.5*dt*k2x;  my = y + 0.5*dt*k2y;  mz = z + 0.5*dt*k2z
    k3x = SIGMA * (my - mx)
    k3y = mx * (RHO - mz) - my
    k3z = mx * my - BETA * mz

    mx = x + dt*k3x;  my = y + dt*k3y;  mz = z + dt*k3z
    k4x = SIGMA * (my - mx)
    k4y = mx * (RHO - mz) - my
    k4z = mx * my - BETA * mz

    x += dt/6 * (k1x + 2*k2x + 2*k3x + k4x)
    y += dt/6 * (k1y + 2*k2y + 2*k3y + k4y)
    z += dt/6 * (k1z + 2*k2z + 2*k3z + k4z)
    return x, y, z


def _generate_keystream(params: BlackholeParams, n: int) -> np.ndarray:
    """
    Generuje n bajtov keystreamu z Lorenzovej trajektórie.

    Efektívny počiatočný stav = SHAKE256(lorenz_init_bytes || nonce),
    čím sa zaručuje unikátnosť keystreamu pre každú správu.
    """
    x, y, z = _derive_lorenz_init(params)

    # warm-up — presunutie na atraktor
    for _ in range(N_WARMUP):
        x, y, z = _rk4_step(x, y, z, params.dt)

    xs = np.empty(n, dtype=np.float64)
    for i in range(n):
        x, y, z = _rk4_step(x, y, z, params.dt)
        xs[i] = x

    # konverzia na bajty: IEEE-754 mantisa (bajty 3–5) XOR-ovaná → 1 bajt na pixel
    raw = xs.view(np.uint8).reshape(n, 8)
    keystream = raw[:, 3] ^ raw[:, 4] ^ raw[:, 5]
    return keystream  # dtype=uint8, shape=(n,)


def encrypt(quantized: np.ndarray, params: BlackholeParams) -> dict:
    """
    Vstup:  quantized — uint16 array (n_chars, H, W), hodnoty 0–255
    Výstup: dict s XOR-šifrovaným uint8 array, params (vrátane nonce) a pôvodným tvarom
    """
    flat   = quantized.ravel().astype(np.uint8)
    stream = _generate_keystream(params, len(flat))
    cipher = (flat ^ stream).astype(np.uint8)
    return {
        "cipher":         cipher.reshape(quantized.shape),
        "params":         params,
        "nonce":          params.nonce,
        "original_shape": quantized.shape,
    }


def decrypt(blackhole_output: dict) -> np.ndarray:
    """
    Reverzia Lorenzovej vrstvy — XOR so zhodným keystreamom (vrátane nonce).
    Vracia uint16 array kompatibilný so vstupom quantizer.decrypt().
    """
    params = blackhole_output["params"]
    cipher = blackhole_output["cipher"].ravel()
    stream = _generate_keystream(params, len(cipher))
    plain  = (cipher ^ stream).astype(np.uint16)
    return plain.reshape(blackhole_output["original_shape"])
