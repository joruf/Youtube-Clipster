# Third-party code served to the phone

Everything in this directory is somebody else's work, taken unmodified from its
release and checked in rather than fetched at runtime. The phone interface has
to work on a phone with no internet connection — a `<script src="https://…">`
would fail exactly when Clipster is most useful.

## jsqr.js

| | |
|---|---|
| Project | [jsQR](https://github.com/cozmo/jsQR) |
| Version | 1.4.0 |
| Licence | Apache-2.0 (full text in `jsqr-LICENSE.txt`) |
| Source | `https://registry.npmjs.org/jsqr/-/jsqr-1.4.0.tgz`, file `package/dist/jsQR.js` |
| SHA-1 of the tarball | `8efb8d0a7cc6863cb6d95116b9069123ce9eb2d1` |

Reads a QR code out of the pixels of a camera frame, which is what the Scan
button in Streaming needs. Decoding is done here rather than on the Python side
because that would mean `pyzbar` or OpenCV — native libraries, on Termux, on a
phone.

The file is the upstream bundle as published, unminified. That is larger than a
minified copy would be, and deliberately so: vendored code that can be read and
compared against upstream is worth more than a few saved kilobytes on a local
network.

### Updating

Download the tarball for the new version, check its SHA-1 against what npm
reports, copy `package/dist/jsQR.js` here as `jsqr.js`, copy `package/LICENSE`
here, and update the table above. Then bump `SHELL_CACHE` in
`clipster/web/sw.js`, or installed home-screen copies keep the old file.
