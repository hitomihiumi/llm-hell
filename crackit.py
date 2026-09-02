"""crackit.py - offline password / passphrase cracker for authorized lab & audit use.

Attacks an encoded password hash (or a file of them) with four passes, in
order: straight dictionary, mangling rules, word+word combinations, and
short brute force. Runs threaded so memory-hard hashes like Argon2 can use
all cores. This is the same crowd as `ransom_sim.py`: lab-only, run it only
against hashes you own / are authorised to test.

Two verifiers ship with it, picked automatically from the hash:

  * argon2id  - the hash LLM-Hell stores for web logins and API keys
                (memory-hard; ~64 MiB per worker just for the verifier).
  * hashlib   - any fast digest, e.g.  --algo sha256 --hash <hex> --salt demo
                (handy for speed-testing the engine without waiting on Argon2).

Rule grammar (inspired by John the Ripper, applied left to right):
  ^STR    prepend STR            (may only be the LAST op)
  $STR    append  STR            (may only be the LAST op)
  sAB     replace every A with B
  r       reverse                d   double (word + word)
  f       reflect (word + reverse(w))
  t       toggle case            c   Capitalize (lower the rest)
  C       capitalize first, rest untouched
  u       UPPERCASE              l   lowercase
  anything else is skipped (so "#" comments and spaces are harmless).
Example: "clean" + rule c$2026!  =>  Clean2026!

Built-in rule packs (--rules NAME, repeatable): caps leet digits punct double
common (the bread-and-butter combo).

Usage:
  python crackit.py --hash '$argon2id$...' -w words.txt --mangle          # argon2id
  python crackit.py --hash-file hashes.txt -w words.txt --mangle
  python crackit.py --algo sha256 --hash <hex> -w words.txt --salt demo --brute
  python crackit.py '$argon2id$...' '$argon2id$...' -w words.txt --rules caps --rules digits
  python crackit.py --self-test

Run the Argon2 path from the backend venv:  backend\\.venv\\Scripts\\python.exe crackit.py

Exit codes: 0 = at least one candidate cracked; 1 = none; 2 = bad usage.
"""
import argparse
import hashlib
import itertools
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

__version__ = "1.0.0"


# --------------------------------------------------------------------------
# Rule engine
# --------------------------------------------------------------------------
_SINGLE = "rdfctCul"

def compile_rule(text: str):
    """Compile a rule string into word -> word. See the module docstring."""
    ops = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "s" and i + 2 < n:
            ops.append(("rep", text[i + 1], text[i + 2]))
            i += 3
            continue
        if ch in _SINGLE:
            ops.append((ch,))
            i += 1
            continue
        if ch in "^$":
            ops.append((ch, text[i + 1:]))
            break
        i += 1

    def apply(word: str) -> str:
        for name, *a in ops:
            if name == "^":
                word = a[0] + word
            elif name == "$":
                word = word + a[0]
            elif name == "rep":
                word = word.replace(a[0], a[1])
            elif name == "r":
                word = word[::-1]
            elif name == "d":
                word = word + word
            elif name == "f":
                word = word + word[::-1]
            elif name == "t":
                word = word.swapcase()
            elif name == "c":
                word = word[:1].upper() + word[1:].lower()
            elif name == "C":
                word = word[:1].upper() + word[1:]
            elif name == "u":
                word = word.upper()
            elif name == "l":
                word = word.lower()
        return word

    return apply


RULES = {
    "caps": ["c", "C", "u", "l", "lu", "lc", "lC"],
    "leet": [
        "lsa4", "lsa@", "lse3", "lsi1", "lso0", "lss5", "lst7",
        "lsa4c", "lsa@c", "lse3c", "lsi1c", "lso0c", "lss5c", "lst7c",
        "lsa4u", "lse3u", "lsi1u", "lso0u", "lss5u", "lst7u",
    ],
    "digits": [
        "$1", "$12", "$123", "$1234", "$12345", "$123456", "$7", "$77",
        "$2020", "$2021", "$2022", "$2023", "$2024", "$2025", "$2026",
    ],
    "punct": ["$!", "$!!", "$@", "$#", "$*", "$$", "$?", "^!", "c$!", "u$!"],
    "double": ["d", "d$1", "d$12", "d$123", "d$2026", "f", "f$123", "r", "r$1", "r$123"],
    "common": [
        "c", "C", "u", "",
        "c$1", "c$12", "c$123", "c$1234", "c$12345", "c$123456",
        "c$2024", "c$2025", "c$2026", "c$!", "c$!!", "c$@", "u$!", "u$123", "u$2026",
        "$123", "$1234", "$2026", "$!", "$!!",
    ],
}


# --------------------------------------------------------------------------
# Candidate sources
# --------------------------------------------------------------------------
def iter_words(args):
    for path in args.wordlist:
        with open(path, encoding=args.encoding, errors="replace") as fh:
            for line in fh:
                w = line.strip().lstrip("\ufeff")
                if w and not w.startswith("#"):
                    yield w


def load_rule_fns(args):
    fns = []
    for name in args.rules:
        pack = RULES.get(name)
        if pack is None:
            raise SystemExit(f"unknown rule pack {name!r} (have: {', '.join(sorted(RULES))})")
        fns.extend(compile_rule(r) for r in pack)
    for path in args.rules_file:
        with open(path, encoding=args.encoding) as fh:
            for line in fh:
                r = line.strip()
                if r and not r.startswith("#"):
                    fns.append(compile_rule(r))
    return fns


def brute_candidates(alphabet, lo, hi):
    for ln in range(lo, hi + 1):
        for tup in itertools.product(alphabet, repeat=ln):
            yield "".join(tup)


# --------------------------------------------------------------------------
# Verifiers
# --------------------------------------------------------------------------
class Target:
    __slots__ = ("label", "hash", "fn")
    def __init__(self, label, hash, fn):
        self.label, self.hash, self.fn = label, hash, fn


class MultiVerifier:
    """Holds one or more target hashes; a candidate that verifies wins them all."""

    def __init__(self, targets):
        self._targets = list(targets)
        self._active = list(targets)
        self.found = []          # [(label, candidate), ...]
        self.checked = 0

    @property
    def any_argon2(self):
        return any(t.label in ("argon2id",) for t in self._targets)

    def check(self, cand) -> bool:
        self.checked += 1
        matched = []
        for t in list(self._active):
            try:
                ok = t.fn(cand)
            except Exception:
                ok = False
            if ok:
                matched.append(t)
                self.found.append((t.label, cand))
        if matched:
            for t in matched:
                try:
                    self._active.remove(t)
                except ValueError:
                    pass
        return bool(matched)


def digest_check(algo, hexdigest, salt: bytes):
    dig = bytes.fromhex(hexdigest)
    def fn(cand):
        h = hashlib.new(algo)
        if salt:
            h.update(salt)
        h.update(cand.encode("utf-8"))
        return h.digest() == dig
    return fn


def argon2_check(encoded: str):
    import argon2
    from argon2.exceptions import VerificationError

    ph = argon2.PasswordHasher()

    def fn(cand):
        try:
            return ph.verify(encoded, cand)
        except VerificationError:
            return False
        except Exception:
            return False
    return fn


def build_targets(args):
    hashes = list(args.hashes) + list(args.hash_opts)
    for path in args.hash_file:
        with open(path, encoding=args.encoding) as fh:
            for line in fh:
                h = line.strip()
                if h and not h.startswith("#"):
                    hashes.append(h)
    if not hashes:
        raise SystemExit("nothing to crack: pass hashes or --hash-file")

    targets = []
    for h in hashes:
        if h.startswith("$argon2"):
            targets.append(Target("argon2id", h, argon2_check(h)))
        else:
            if not args.algo or args.algo == "argon2":
                raise SystemExit(
                    f"hash {h[:32]!r}... is not argon2id; pass --algo <hashlib name>")
            targets.append(Target(args.algo, h, digest_check(args.algo, h, args.salt_bytes)))
    return MultiVerifier(targets)


# --------------------------------------------------------------------------
# Crack driver (threaded, chunked)
# --------------------------------------------------------------------------
def _check_batch(verifier, cands):
    hits = []
    for c in cands:
        if verifier.check(c):
            hits.append(c)
    return hits or None


def run_stage(verifier, cand_iter, args, hit):
    start = time.monotonic()
    done = len(verifier._targets)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        while True:
            if len(verifier.found) >= done:
                hit.set()
                break
            if hit.is_set():
                break
            if args.timeout and time.monotonic() - args._t0 > args.timeout:
                break
            if args.max_tests and verifier.checked >= args.max_tests:
                break
            buf = list(itertools.islice(cand_iter, args.chunk))
            if not buf:
                break
            nw = min(args.workers, len(buf))
            slices = [buf[i::nw] for i in range(nw)]
            futs = [ex.submit(_check_batch, verifier, s) for s in slices if s]
            for f in futs:
                if f.result():
                    hit.set()
    return time.monotonic() - start


def start_reporter(verifier, args):
    stop = threading.Event()
    t0 = time.monotonic()
    def loop():
        while not stop.wait(args.report):      # wait(interval) -> True if set
            el = time.monotonic() - t0
            rate = verifier.checked / el if el else 0.0
            print(f"    tested={verifier.checked:,}  rate={rate:,.0f}/s  elapsed={el:.1f}s",
                  file=sys.stderr)
    threading.Thread(target=loop, daemon=True).start()
    return stop


# --------------------------------------------------------------------------
# Stages
# --------------------------------------------------------------------------
def build_stages(args, rule_fns):
    stages = []

    def words():
        yield from iter_words(args)

    if args.straight and args.wordlist:
        stages.append(("dictionary", words()))

    if rule_fns and args.wordlist:
        def rules():
            for w in iter_words(args):
                for fn in rule_fns:
                    yield fn(w)
        stages.append(("rules", rules()))

    if args.combo:
        lst = list(itertools.islice(iter_words(args), args.combo_limit))
        if len(lst) < 2:
            print(f"    [warn] --combo needs >=2 words, got {len(lst)}", file=sys.stderr)
            lst = []
        elif len(lst) > args.combo_limit:
            print(f"    [warn] --combo capped at the first {args.combo_limit} wordlist words",
                  file=sys.stderr)
        if lst:
            def combo():
                for a in lst:
                    for b in lst:
                        yield a + b
            stages.append((f"combo ({len(lst)} words, {len(lst) ** 2:,} candidates)", combo()))

    if args.brute:
        stages.append(
            (f"brute [{args.alphabet}] len{args.min_len}-{args.max_len}",
             brute_candidates(args.alphabet, args.min_len, args.max_len)))

    return stages


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------
def self_test(_args):
    c = compile_rule
    assert c("c$2026")("clean") == "Clean2026"
    assert c("^!")("welcome") == "!welcome"
    assert c("sa4")("banana") == "b4n4n4"
    assert c("d")("ab") == "abab"
    assert c("f")("ab") == "abba"
    assert c("r")("abc") == "cba"
    assert c("t")("aB1") == "Ab1"
    assert c("u")("hi") == "HI"
    assert c("l")("HI") == "hi"
    assert c("")("word") == "word"
    assert c("lsa4c")("banana") == "B4n4n4"

    dig = hashlib.sha256(b"demo").hexdigest()
    v = MultiVerifier([Target("sha256", dig, digest_check("sha256", dig, b""))])
    for cand in brute_candidates("odem", 1, 4):
        if v.check(cand):
            break
    assert v.found and v.found[0][1] == "demo", "brute missed 'demo'"

    print("self-test OK")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="crackit.py",
        description="Offline password / passphrase cracker (authorized lab use).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        allow_abbrev=False,
    )
    p.add_argument("hashes", nargs="*", help="encoded hash(es) to crack")
    p.add_argument("--hash", action="append", default=[], dest="hash_opts",
                   metavar="HASH", help="encoded hash to crack (repeatable)")
    p.add_argument("--hash-file", action="append", default=[], metavar="FILE",
                   help="file of hashes, one per line (# comments ignored)")
    p.add_argument("--algo", default=None, metavar="NAME",
                   help="hashlib digest for non-argon2 hashes (sha256, md5, sha1, ...)")
    p.add_argument("--salt", default=None, help="salt prepended for the digest verifier")
    p.add_argument("--salt-hex", default=None, help="hex-encoded salt for the digest verifier")

    g = p.add_argument_group("dictionary / rules")
    g.add_argument("-w", "--wordlist", action="append", default=[], metavar="FILE",
                   help="wordlist (repeatable); '-' reads stdin")
    g.add_argument("--rules", action="append", default=[], metavar="NAME",
                   help=f"built-in rule pack (repeatable): {', '.join(sorted(RULES))}")
    g.add_argument("--rules-file", action="append", default=[], metavar="FILE",
                   help="custom rule file, one rule per line (repeatable)")
    g.add_argument("--mangle", action="store_true",
                   help="shorthand: apply the caps + digits + punct rule packs")
    g.add_argument("--no-straight", action="store_false", dest="straight", default=True,
                   help="skip the raw-dictionary pass")
    g.add_argument("--combo", action="store_true", help="word+word combination pass")
    g.add_argument("--combo-limit", type=int, default=2000,
                   help="cap words loaded for the combo pass")

    g = p.add_argument_group("brute force")
    g.add_argument("-b", "--brute", action="store_true", help="enable exhaustive brute force")
    g.add_argument("--alphabet", default="abcdefghijklmnopqrstuvwxyz0123456789")
    g.add_argument("--min-len", type=int, default=1)
    g.add_argument("--max-len", type=int, default=5)

    g = p.add_argument_group("engine")
    g.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    g.add_argument("--chunk", type=int, default=500, help="candidates per batch")
    g.add_argument("--timeout", type=float, default=None, help="seconds per run")
    g.add_argument("--max-tests", type=int, default=None, help="cap total candidates checked")
    g.add_argument("--report", type=float, default=2.0, help="progress line interval (s)")
    g.add_argument("--encoding", default="utf-8")
    g.add_argument("--quiet", action="store_true")
    g.add_argument("--self-test", action="store_true")
    p.add_argument("--version", action="version", version=__version__)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.self_test:
        return self_test(args)
    if not args.wordlist and not args.brute and not args.combo:
        raise SystemExit("nothing to generate: give -w/--wordlist and/or --brute/--combo")

    if args.salt_hex and args.salt:
        raise SystemExit("pick one of --salt / --salt-hex, not both")
    args.salt_bytes = (bytes.fromhex(args.salt_hex) if args.salt_hex
                       else args.salt.encode("utf-8") if args.salt else b"")
    args._t0 = time.monotonic()

    try:
        verifier = build_targets(args)
    except ImportError:
        raise SystemExit("argon2 not installed here; run from the backend venv:\n"
                         "  backend\\.venv\\Scripts\\python.exe crackit.py ...")
    except ValueError as e:
        raise SystemExit(f"bad input: {e}")

    if args.mangle:
        args.rules += ["caps", "digits", "punct"]

    rule_fns = load_rule_fns(args)
    if verifier.any_argon2 and args.workers > 1:
        print(f"    [note] argon2id ~{args.workers * 64} MiB across {args.workers} workers",
              file=sys.stderr)
    if verifier.any_argon2 and args.brute and args.max_len >= 5:
        total = (len(args.alphabet)) ** args.max_len
        print(f"    [warn] argon2id brute at max_len {args.max_len}: up to {total:,} candidates",
              file=sys.stderr)

    stages = build_stages(args, rule_fns)
    print(f"[*] targets={len(verifier._targets)} workers={args.workers} "
          f"stages={', '.join(s[0] for s in stages)}")

    hit = threading.Event()
    rep = None if args.quiet else start_reporter(verifier, args)
    try:
        for label, gen in stages:
            if hit.is_set():
                break
            print(f"[*] stage: {label}")
            el = run_stage(verifier, gen, args, hit)
            print(f"    {label}: done in {el:.1f}s, tested={verifier.checked:,}", file=sys.stderr)
            if hit.is_set():
                break
    except KeyboardInterrupt:
        print("\n[!] interrupted - partial results below", file=sys.stderr)
    finally:
        if rep:
            rep.set()

    if verifier.found:
        for label, cand in verifier.found[:50]:
            print(f"[+] CRACKED ({label}): {cand!r}")
        extra = len(verifier.found) - 50
        if extra > 0:
            print(f"    ... and {extra} more")
        print(f"[+] done in {time.monotonic() - args._t0:.1f}s after {verifier.checked:,} checks")
        return 0
    print(f"[-] not cracked after {verifier.checked:,} checks "
          f"({time.monotonic() - args._t0:.1f}s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
