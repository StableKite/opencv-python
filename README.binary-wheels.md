# CPython 3.15t dependency delivery

A normal `pip install` from the default branch uses the recorded original
linux_x86_64 wheel on Linux x86_64, CPython 3.15t, glibc 2.39+ and the checked
Ubuntu 24.04 system-library profile. Its source is wrapper b304bc2ad314ce0c99352010fe63c52091dcf76e
and native OpenCV 6a18c3424987b7a2bf76377fc2464cc36421b378.

The wheel retains GTK3, Pango, video codecs and image codecs. It uses the actual
system GTK3, FFmpeg 6 and image-library ABIs listed in `binary_wheels.json`.
The backend checks those SONAMEs before selecting the artifact. This is an
original platform wheel, not a manylinux portability claim. Other profiles,
source changes and explicit compiler options use the original source build.
No consumer build flag or GIL override is required.

The GTK3 link fix removes only a direct system HarfBuzz reference when OpenCV
uses bundled HarfBuzz. Every other GTK dependency and the system-only HarfBuzz
case are retained. Native text rendering, independent threads, image/video
regressions, ELF closure and an explicit GIL-on control qualified this artifact.
The earlier init-only upstream PR #30134 is unchanged.

An experimental auditwheel closure failed during dynamic loading and is not
delivered. It is retained locally as failed evidence. These are engineering
dependency artifacts, not a StableKite product release.
