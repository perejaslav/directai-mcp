"""Pre-commit / pre-push block-list check (public repo hygiene).

Reads %USERPROFILE%\\.directai\\sanitize-blocklist.txt (kept OUTSIDE the repo).
Numeric entries (digits only) match as whole numbers: (?<!\\d)PAT(?!\\d).
Name entries (see WHOLE_WORDS) match as whole words, case-insensitive.
Other text entries match as substrings, case-insensitive.
Plus FORBIDDEN_ID_DIGESTS: sha256 of forbidden long numeric IDs (the IDs
themselves are never stored here) — any run of 15+ digits hashing to a
listed digest blocks the commit.
uv.lock is excluded (generated file: public package metadata with hex hashes).
Prints only file + match count, never matched values.
Exit 1 on any hit (commit blocked), 0 when clean.
"""
import os
import re
import hashlib
import subprocess
import sys

HOME = os.environ.get("USERPROFILE", "")
BLOCKLIST = os.path.join(HOME, ".directai", "sanitize-blocklist.txt")
WHOLE_WORDS = {"ив" + "ан"}
SKIP_FILES = {"uv.lock"}

# Запрещённый реальный ID объявления (утёк в tests 01.10.2026, история
# переписана): храним ТОЛЬКО sha256, не само число — иначе защита станет
# новой утечкой. Срабатывает на серию цифр длиной 15+ с совпадающим хешем.
FORBIDDEN_ID_DIGESTS = frozenset({
    "dc835a6b3cd24e22692e8411585b019828e3f19db4264ceedc9300efec7c0afa",
})
_LONG_DIGITS = re.compile(r"\d{15,}")


def load_patterns():
    numeric = []
    words = []
    text = []
    with open(BLOCKLIST, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s:
                continue
            if re.fullmatch(r"\d+", s):
                numeric.append(re.compile(r"(?<!\d)" + s + r"(?!\d)"))
            elif s.lower() in WHOLE_WORDS:
                words.append(re.compile(r"(?<![\w])" + re.escape(s) + r"(?![\w])",
                                        re.IGNORECASE))
            else:
                text.append(s.lower())
    return numeric, words, text


def staged_files():
    out = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACM"],
        text=False,
    )
    return [p.decode("utf-8", "replace") for p in out.split(b"\x00") if p]


def check_file(path, numeric, words, text):
    try:
        with open(path, encoding="utf-8", errors="strict") as fh:
            content = fh.read()
    except Exception:
        return 0
    hits = 0
    for rx in numeric:
        hits += len(rx.findall(content))
    for rx in words:
        hits += len(rx.findall(content))
    low = content.lower()
    for s in text:
        start = 0
        while True:
            i = low.find(s, start)
            if i < 0:
                break
            hits += 1
            start = i + 1
    for match in _LONG_DIGITS.finditer(content):
        digest = hashlib.sha256(match.group(0).encode("ascii")).hexdigest()
        if digest in FORBIDDEN_ID_DIGESTS:
            hits += 1
    return hits


def main(paths):
    if not os.path.exists(BLOCKLIST):
        print("check_blocklist: block-list not found: " + BLOCKLIST)
        return 1
    numeric, words, text = load_patterns()
    if not paths:
        paths = staged_files()
    bad = 0
    for p in paths:
        if not os.path.isfile(p):
            continue
        if os.path.basename(p) in SKIP_FILES:
            continue
        try:
            if os.path.getsize(p) > 5 * 1024 * 1024:
                continue
        except OSError:
            continue
        n = check_file(p, numeric, words, text)
        if n:
            print(f"BLOCKED {p}: {n}")
            bad += 1
    if bad:
        print(f"check_blocklist: {bad} file(s) blocked, commit aborted")
        return 1
    print(f"check_blocklist: clean ({len(paths)} file(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
