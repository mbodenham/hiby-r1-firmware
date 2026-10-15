# hiby-r1-firmware

Custom firmware for the **HiBy R1** portable music player. The repo carries
the extracted stock firmware source directly: clone it, edit `src/`, run
`./build.sh`, flash the resulting `.upt`. The device is end-of-life upstream;
this repo is the living source.

Base: stock **v1.6** (build 2025-12-29), extracted from the `.upt` shipped in
[`hiby-r1-sdk`](../hiby-r1-sdk) (image md5 `494e7bfbd46d623ceb56938c042f576e`).

## Starting point

This repo starts at stock. The **first commit is pristine stock v1.6**:
`src/xImage` + `src/squashfs-root/` exactly as extracted from the stock
`.upt`, nothing added or changed. Every later commit is custom work on top,
so the full firmware delta from stock is:

```
git diff $(git rev-list --max-parents=0 HEAD) -- src/
```

## Layout

```
src/xImage              kernel image (from stock, kept as-is)
src/squashfs-root/      root filesystem source tree — edit here
tools/unpack.sh         .upt -> xImage + squashfs-root/ (md5-verified)
tools/repack.sh         xImage + squashfs-root/ -> .upt (hash-chained OTA image)
build.sh                packs src/ -> out/, then round-trip verifies
config.sh               firmware name/version
out/                    build artifacts (gitignored)
```

## Build

Requires: `7z`, `unsquashfs`/`mksquashfs` (squashfs-tools), `xorriso`.

```
./build.sh
```

Output: `out/r1-custom-<version>.upt`. Every build is verified by re-unpacking
it with `tools/unpack.sh` (hash chain + image md5s).

## Making changes

Edit files under `src/squashfs-root/` directly and commit (see *Starting
point*: the root commit is the untouched stock tree). Use `tools/unpack.sh`
on any `.upt` if you ever need to re-derive or inspect a stock image.

## Testing / flashing

Test in QEMU via the [`hiby-r1-sdk`](../hiby-r1-sdk) sandbox before touching
hardware (see SDK `docs/`). Install on a real device via the R1's TF-card
firmware upgrade path; do not flash a build that has not first booted in
emulation.
