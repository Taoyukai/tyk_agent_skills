# Third-Party Notices

This repository bundles third-party components. They remain under their
original licenses; this project's MIT license applies only to the original
code (the `dscope-la-osc` plugin sources themselves).

## `dscope-la-osc/skills/dscope-la-osc/assets/hil-tools/decoders/`

Protocol decoder scripts (149 decoders) copied from
[DSView](https://www.dreamsourcelab.com/) by DreamSourceLab, which
distributes them under **GPL-3.0** (fork of the sigrok project's
libsigrokdecode). Source: DSView installation (`decoders/` directory).
These scripts are plain Python sources distributed in their original form,
satisfying GPL source requirements. License: https://www.gnu.org/licenses/gpl-3.0.html

## `dscope-la-osc/skills/dscope-la-osc/assets/hil-tools/bin/` (DLLs)

- **libglib-2.0-0.dll** — GLib, LGPL-2.1 (https://developer.gnome.org/glib/)
- **libusb-1.0.dll** — libusb, LGPL-2.1 (https://libusb.info/)
- **libintl-8.dll / libiconv-2.dll** — gettext/libiconv, LGPL/GPL (GNU)
- **libpcre2-8-0.dll** — PCRE2, BSD-style
- **libwinpthread-1.dll** — mingw-w64 winpthreads, MIT/BSF-style
- **libz-1.dll** — zlib, zlib license
- `ds-capture.exe` — built by this project's author on top of
  DreamSourceLab's open DSLogic/DSCope driver stack; check with the author
  for its exact upstream provenance.

LGPL components are dynamically linked (plain DLL imports) and may be
replaced/reverted by the user; their sources are available from the
upstream projects listed above.
