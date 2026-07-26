"""Sum the on-disk size of everything PyInstaller put in the onefile payload,
grouped by component, so the <=250MB target stays verifiable instead of assumed.

    /c/Python314/python tools/payload_audit.py build/build/PKG-00.toc
"""
import ast
import collections
import os
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    raw = ast.literal_eval(f.read())
# A PKG TOC is an 11-tuple of build params; the (name, src, typecode) list is [2].
entries = raw[2] if isinstance(raw, tuple) else raw

groups, counts, biggest = collections.Counter(), collections.Counter(), []
for name, src, _typecode in entries:
    size = os.path.getsize(src) if src and os.path.isfile(src) else 0
    n = name.replace("\\", "/")
    if n.startswith("ffmpeg"):
        key = "ffmpeg.exe"
    elif n.startswith("cv2/"):
        key = "cv2"
    elif n.startswith("numpy"):
        key = "numpy"
    elif n.startswith("PIL"):
        key = "PIL/Pillow"
    elif n.startswith("tcl") or n.startswith("tk") or "_tkinter" in n:
        key = "tcl/tk (tkinter)"
    elif n.endswith(".pyz"):
        key = "PYZ (pure python)"
    elif n.startswith("python"):
        key = "CPython runtime"
    else:
        key = "other"
    groups[key] += size
    counts[key] += 1
    biggest.append((size, name))

total = sum(groups.values())
print(f"{'component':24s} {'MB':>9s} {'share':>7s} {'files':>7s}")
print("-" * 52)
for key, size in groups.most_common():
    print(f"{key:24s} {size/1e6:9.1f} {100*size/total:6.1f}% {counts[key]:7d}")
print("-" * 52)
print(f"{'TOTAL (uncompressed)':24s} {total/1e6:9.1f}")
print("\nTop 10 individual files:")
for size, name in sorted(biggest, reverse=True)[:10]:
    print(f"  {size/1e6:8.1f} MB  {name}")
