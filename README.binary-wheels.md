# CPython 3.15t engineering wheel delivery

Normal `pip install "opencv-python @ git+https://github.com/StableKite/opencv-python.git"`
uses retained, SHA256-verified native wheels on Windows x86_64 CPython3.15t and
Linux x86_64 CPython3.15t with glibc2.39 or newer. No GIL override, loader variable, system codec SDK, or pip
feature flag is required. This is an engineering prerelease with explicit source
and test provenance; it is not an upstream release or a full hardware acceptance.

The native OpenCV source remains 6a18c3424987b7a2bf76377fc2464cc36421b378. Packaging
repairs the previously retained original source wheel (wrapper b304bc2) and uses
a clean current shared FFmpeg6.1.6/OpenH2642.7.0 LGPL SDK. No GPL FFmpeg, x264,
x265 or postproc payload is included. The existing Intel IPP notice is retained;
IPP is a separately licensed binary component and its source is not claimed.
The final native `.text` and all19 public exports match the original raw wheel.

Every actual bundled DSO has full input/output hashes and original provider
provenance in the wheel's STABLEKITE_NATIVE_PROVENANCE.json. Package-specific
license texts, complete selected corresponding source archives, exact native
configure/build/test commands, source patches and public recipe Git archives
are retained in the matching source release asset. The source bundle also gives
instructions for rebuilding/replacing compatible LGPL shared components.
See `.github/recipes/cp315t-portable-linux.json` for pinned sources, hashes,
qualification scope and the original native test limitations.

The platform tag remains cp315-cp315t. The distribution local version increments
to 5.1.0+b304bc2.stablekite.1 for this SDK packaging revision. No Python ABI tag
was relabeled. Linux's measured minimum symbol policy is glibc2.38; automatic
delivery conservatively starts at tested Ubuntu24.04's glibc2.39.

Windows retains the entire original source-produced wheel 5.1.0+b304bc2, SHA
9db81328f8ad9b70fa1ddd77f7a3080bfe05a99c6433fda5c0aabac1b4d16b0d.
Its original FFmpeg7.1 plugin matches the immutable upstream provider Git blob.
Its separate vendor-declared source/license bundle and exact native build/probe
receipt are linked in `.github/recipes/cp315t-original-windows.json`. The original
Windows source wheel has no SDK packaging mutation, so its original local source
version stays b304bc2; Linux increments stablekite.1 for its changed shared SDK.
No Cisco codec implementation DLL is bundled; optional Windows H264 encoding
with a separately supplied Cisco DLL is not part of the scoped three-codec gate.

All unsupported target/options retain the original source backend.
Custom compiler flags, CMAKE_ARGS, headless/contrib/rolling/Java/package-name
options, config settings, and changed native sources use that backend. Delivery
source hashes and normalized pyproject configuration bind the retained wheel to
the reviewed native source. A missing mandatory integrity check does not silently
accept a changed wheel. In particular an eligible download failure is an error;
it does not silently build a feature-reduced Linux wheel.

CI installs the public default by ordinary pip, checks expected PEP610 commit,
downloads the retained original wheel independently, verifies ZIP CRC and every
RECORD hash against installed bytes, probes actual LGPL SDK/83DSO closure, tests
real video/text/threads and requires natural GIL OFF and normal process exit.
Physical camera/GPU/interactive desktop coverage is not claimed by these gates.
