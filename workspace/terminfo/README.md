# Ghostty terminfo provenance

The vendored `ghostty.terminfo` file is generated from Ghostty's authoritative
`src/terminfo/ghostty.zig` implementation at commit
`55a3e33ab26a23d75b274b23c7f76d837db00578`.

Upstream source:

- https://github.com/ghostty-org/ghostty/blob/55a3e33ab26a23d75b274b23c7f76d837db00578/src/terminfo/ghostty.zig
- https://github.com/ghostty-org/ghostty/blob/55a3e33ab26a23d75b274b23c7f76d837db00578/src/terminfo/Source.zig

Generation uses Ghostty's `Source.encode` implementation and preserves the
upstream capability order. The workspace image compiles this checked-in source
with `tic -x`; Docker builds do not fetch Ghostty or mutable network content.

Ghostty is MIT licensed. The preserved upstream license is in
`LICENSE.ghostty`.
