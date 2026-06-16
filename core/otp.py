"""
FSC — OTP Layer
Vrstva 7: One-Time Pad (Vernamova šifra)

Shannonova podmienka dokonalej utajnosti (1949):
  1. Pad musí byť aspoň rovnako dlhý ako správa.
  2. Pad musí byť skutočne náhodný (CSPRNG nestačí pre IT-bezpečnosť;
     tu používame secrets.token_bytes, čo je kryptograficky bezpečné
     ale nie "true random" v zmysle fyzikálneho zdroja entropie).
  3. Pad musí byť použitý iba raz (One-Time).
  4. Pad musí byť uchovavaný v tajnosti.

Splnenie všetkých štyroch podmienok zaručuje, že ciphertext neobsahuje
žiadnu informatívnu informáciu o plaintexte — ani s neobmedzeným výpočtovým
výkonom nie je možné rozlíšiť dva rôzne plaintexty produkujúce rovnaký
ciphertext.

FSC kombinuje dve bezpečnostné záruky:
  - Vrstvy 1–6:  výpočtová bezpečnosť (2²⁵⁶ kľúčový priestor + Lorenz chaos)
  - Vrstva 7:    informačno-teoretická bezpečnosť (Shannon perfect secrecy)
"""

import secrets
import numpy as np


def pad_size(n_chars: int, canvas_size: int) -> int:
    """Minimálna veľkosť padu pre n_chars znakov na canvas_size × canvas_size plátne."""
    return n_chars * canvas_size * canvas_size


def generate_pad(size: int) -> bytes:
    """Vygeneruje kryptograficky náhodný pad danej veľkosti."""
    return secrets.token_bytes(size)


def encrypt(ciphertext: np.ndarray, pad: bytes) -> np.ndarray:
    """
    XOR ciphertext s OTP padom.

    Vstup:  uint8 array ľubovolného tvaru, pad ≥ ciphertext.size bajtov
    Výstup: uint8 array rovnakého tvaru

    XOR je samoinverzná — decrypt == encrypt.
    """
    flat    = ciphertext.ravel().astype(np.uint8)
    pad_arr = np.frombuffer(pad[:flat.size], dtype=np.uint8)
    return np.bitwise_xor(flat, pad_arr).reshape(ciphertext.shape)


# XOR is self-inverse: decrypt is identical to encrypt
decrypt = encrypt
