from __future__ import annotations

import argparse
import email.parser
import hashlib
import re
import subprocess
import sys
import sysconfig
import tarfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROJECT_CANONICAL = "opencv-python"
VALIDATION_PROJECT = "opencv-python-headless"


def _run(
    *args: str,
    cwd: Path = ROOT,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _git(*args: str, check: bool = True) -> str:
    return _run("git", *args, check=check).stdout.strip()


def _normalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _gitlink(ref: str) -> str:
    value = _git("rev-parse", f"{ref}:opencv")
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise SystemExit(f"Unexpected OpenCV gitlink for {ref}: {value!r}")
    return value


def _ensure_opencv_commit(sha: str) -> None:
    opencv = ROOT / "opencv"
    if not (opencv / ".git").exists():
        raise SystemExit(
            "OpenCV submodule is not initialized; run "
            "`git submodule update --init --depth 1 opencv`"
        )

    present = _run(
        "git",
        "cat-file",
        "-e",
        f"{sha}^{{commit}}",
        cwd=opencv,
        check=False,
    )
    if present.returncode == 0:
        return

    fetch = _run(
        "git",
        "fetch",
        "--no-tags",
        "--depth",
        "1",
        "origin",
        sha,
        cwd=opencv,
        check=False,
    )
    if fetch.returncode != 0:
        raise SystemExit(
            "Could not fetch required OpenCV submodule commit "
            f"{sha}: {fetch.stderr.strip()}"
        )


def _opencv_source(ref: str) -> tuple[str, str, str]:
    sha = _gitlink(ref)
    _ensure_opencv_commit(sha)
    opencv = ROOT / "opencv"
    header = _run(
        "git",
        "show",
        f"{sha}:modules/core/include/opencv2/core/version.hpp",
        cwd=opencv,
    ).stdout

    def macro(name: str) -> str:
        match = re.search(
            rf"(?m)^#define\s+{re.escape(name)}\s+(.+?)\s*$",
            header,
        )
        if not match:
            raise SystemExit(f"Missing {name} in OpenCV version.hpp at {sha}")
        return match.group(1).strip()

    major = int(macro("CV_VERSION_MAJOR"))
    minor = int(macro("CV_VERSION_MINOR"))
    revision = int(macro("CV_VERSION_REVISION"))
    status = macro("CV_VERSION_STATUS").strip('"')
    return f"{major}.{minor}.{revision}", status, sha


def canonical_version(ref: str) -> str:
    base, status, sha = _opencv_source(ref)

    exact_tags = [
        tag
        for tag in _git("tag", "--points-at", ref).splitlines()
        if re.fullmatch(r"[0-9]+", tag)
    ]
    if exact_tags:
        tag = str(max(int(item) for item in exact_tags))
        return f"{base}.{tag}"

    suffix = re.sub(r"[^a-z0-9]+", ".", status.lower()).strip(".")
    if suffix in {"", "dev"}:
        suffix = "dev0"
    elif not re.fullmatch(r"dev[0-9]+", suffix):
        suffix = f"dev0.{suffix}"

    # Development version identity is tied to the actual OpenCV product source.
    # CI-only changes in this wrapper repository therefore stay validation-only.
    return f"{base}.{suffix}+opencv.{sha[:12]}"


def verify_source(ref: str) -> None:
    gitmodules = (ROOT / ".gitmodules").read_text(encoding="utf-8")
    setup = (ROOT / "setup.py").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    required_gitmodules = (
        "url = https://github.com/StableKite/opencv.git",
        "branch = 5.x",
    )
    for marker in required_gitmodules:
        if marker not in gitmodules:
            raise SystemExit(f"Source policy failure: missing {marker!r}")

    required_setup = (
        '"-DPYTHON3_LIMITED_API=%s"',
        'sysconfig.get_config_var("Py_GIL_DISABLED")',
        '"OFF" if sysconfig.get_config_var("Py_GIL_DISABLED") else "ON"',
    )
    for marker in required_setup:
        if marker not in setup:
            raise SystemExit(
                "Source policy failure: free-threaded limited-API guard "
                f"is missing marker {marker!r}"
            )

    if "python_version=='3.14'" not in pyproject:
        raise SystemExit(
            "Source policy failure: pyproject.toml has no explicit Python 3.14 "
            "NumPy build dependency"
        )

    base, status, gitlink = _opencv_source(ref)
    checked_out = _run(
        "git",
        "rev-parse",
        "HEAD",
        cwd=ROOT / "opencv",
    ).stdout.strip()
    if checked_out != gitlink:
        raise SystemExit(
            "Source policy failure: OpenCV checkout does not match gitlink: "
            f"{checked_out} != {gitlink}"
        )

    print(f"opencv_source_version={base}{status}")
    print(f"opencv_gitlink={gitlink}")
    print(f"canonical_version={canonical_version(ref)}")
    print("OpenCV Python source policy: PASS")


def source_manifest(output: Path | None) -> str:
    raw = subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    records: list[str] = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        meta_raw, path_raw = entry.split(b"\t", 1)
        mode, blob, stage = meta_raw.decode("ascii").split()
        if stage != "0":
            raise SystemExit(
                f"Unexpected staged index entry for {path_raw!r}"
            )
        path = path_raw.decode("utf-8", errors="surrogateescape")
        records.append(f"{mode} {blob} {path}")

    records.sort()
    payload = ("\n".join(records) + "\n").encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()

    if output is not None:
        output.write_text(
            "\n".join(records) + f"\nsource_tree_sha256={digest}\n",
            encoding="utf-8",
        )

    print(f"canonical_files={len(records)}")
    print(f"source_tree_sha256={digest}")
    return digest


def _single(directory: Path, pattern: str, label: str) -> Path:
    files = sorted(directory.glob(pattern))
    if len(files) != 1:
        raise SystemExit(
            f"Expected exactly one {label} in {directory}; "
            f"found {[item.name for item in files]}"
        )
    return files[0]


def _read_metadata_bytes(data: bytes) -> tuple[str, str]:
    message = email.parser.Parser().parsestr(data.decode("utf-8", errors="strict"))
    name = message.get("Name", "")
    version = message.get("Version", "")
    if not name or not version:
        raise SystemExit("Package metadata is missing Name or Version")
    return name, version


def _read_sdist_metadata(path: Path) -> tuple[str, str]:
    with tarfile.open(path, "r:gz") as archive:
        members = []
        for member in archive.getmembers():
            if not member.isfile():
                continue
            parts = member.name.split("/")
            if len(parts) == 2 and parts[1] == "PKG-INFO":
                members.append(member)
        if len(members) != 1:
            raise SystemExit(
                f"Expected exactly one top-level PKG-INFO in {path.name}; "
                f"found {len(members)}"
            )
        handle = archive.extractfile(members[0])
        if handle is None:
            raise SystemExit(f"Could not read PKG-INFO from {path.name}")
        return _read_metadata_bytes(handle.read())


def verify_sdist(directory: Path) -> str:
    path = _single(directory, "*.tar.gz", "sdist")
    name, version = _read_sdist_metadata(path)
    if _normalize_name(name) != PROJECT_CANONICAL:
        raise SystemExit(
            f"Unexpected sdist project name {name!r}; "
            f"expected {PROJECT_CANONICAL!r}"
        )
    print(f"sdist_file={path.name}")
    print(f"sdist_name={name}")
    print(f"sdist_version={version}")
    print("OpenCV Python sdist: PASS")
    return version


def verify_wheel(directory: Path, expected_platform: str) -> str:
    path = _single(directory, "*.whl", "wheel")
    parts = path.name[:-4].rsplit("-", 3)
    if len(parts) != 4:
        raise SystemExit(f"Could not parse wheel filename: {path.name}")
    _, python_tag, abi_tag, platform_tag = parts

    if expected_platform not in platform_tag.split("."):
        raise SystemExit(
            f"Wheel platform mismatch: expected {expected_platform!r}, "
            f"got {platform_tag!r}"
        )
    if "t" not in abi_tag or abi_tag == "abi3":
        raise SystemExit(
            f"Wheel ABI is not free-threaded/version-specific: {abi_tag!r}"
        )

    with zipfile.ZipFile(path) as archive:
        metadata_names = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise SystemExit(
                f"Expected one METADATA in {path.name}; "
                f"found {metadata_names}"
            )
        name, version = _read_metadata_bytes(
            archive.read(metadata_names[0])
        )

    if _normalize_name(name) != VALIDATION_PROJECT:
        raise SystemExit(
            f"Unexpected validation wheel project name {name!r}; "
            f"expected {VALIDATION_PROJECT!r}"
        )

    print(f"wheel_file={path.name}")
    print(f"wheel_name={name}")
    print(f"wheel_version={version}")
    print(f"wheel_python_tag={python_tag}")
    print(f"wheel_abi_tag={abi_tag}")
    print(f"wheel_platform_tag={platform_tag}")
    print("OpenCV Python free-threaded wheel: PASS")
    return version


def smoke() -> None:
    import cv2  # type: ignore

    gil_probe = getattr(sys, "_is_gil_enabled", None)
    gil_enabled = bool(gil_probe()) if callable(gil_probe) else None
    free_threaded = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))

    jit = getattr(sys, "_jit", None)
    jit_available = (
        bool(jit.is_available())
        if jit is not None and callable(getattr(jit, "is_available", None))
        else False
    )
    jit_enabled = (
        bool(jit.is_enabled())
        if jit is not None and callable(getattr(jit, "is_enabled", None))
        else False
    )

    print(f"cv2_version={cv2.__version__}")
    print(
        f"post_import_free_threaded={free_threaded} "
        f"post_import_gil_enabled={gil_enabled} "
        f"post_import_jit_available={jit_available} "
        f"post_import_jit_enabled={jit_enabled}"
    )

    if not free_threaded:
        raise SystemExit("Installed OpenCV smoke failure: not a free-threaded CPython")
    if gil_enabled is True:
        raise SystemExit(
            "Installed OpenCV smoke failure: importing cv2 enabled the GIL"
        )
    if jit_available and not jit_enabled:
        raise SystemExit(
            "Installed OpenCV smoke failure: JIT is available but not enabled"
        )

    matrix = cv2.Mat if hasattr(cv2, "Mat") else None
    _ = matrix
    if not hasattr(cv2, "getBuildInformation"):
        raise SystemExit("Installed OpenCV smoke failure: cv2 API is incomplete")
    build_info = cv2.getBuildInformation()
    if not isinstance(build_info, str) or "OpenCV" not in build_info:
        raise SystemExit("Installed OpenCV smoke failure: invalid build information")

    print("OpenCV Python installed-package smoke: PASS")


def verify_publish_set(directory: Path, expected_key: str) -> str:
    version = verify_sdist(directory)

    release = re.fullmatch(
        r"([0-9]+\.[0-9]+\.[0-9]+)\.([0-9]+)",
        expected_key,
    )
    development = re.fullmatch(
        r"([0-9]+\.[0-9]+\.[0-9]+)\.dev0(?:\.[a-z0-9.]+)?"
        r"\+opencv\.[0-9a-f]+",
        expected_key,
    )

    if release:
        if version != expected_key:
            raise SystemExit(
                f"Release sdist version {version} does not match "
                f"canonical version {expected_key}"
            )
    elif development:
        base = development.group(1)
        if not version.startswith(base + "+"):
            raise SystemExit(
                f"Development sdist version {version} does not match "
                f"OpenCV base version {base}"
            )
    else:
        raise SystemExit(f"Unsupported canonical version format: {expected_key}")

    print("OpenCV Python exact publish set: PASS")
    return version


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify-source")
    verify.add_argument("--ref", default="HEAD")

    canonical = sub.add_parser("canonical-version")
    canonical.add_argument("--ref", default="HEAD")

    manifest = sub.add_parser("source-manifest")
    manifest.add_argument("--output", type=Path)

    sdist = sub.add_parser("verify-sdist")
    sdist.add_argument("--dir", type=Path, required=True)

    wheel = sub.add_parser("verify-wheel")
    wheel.add_argument("--dir", type=Path, required=True)
    wheel.add_argument("--expected-platform", required=True)

    smoke_parser = sub.add_parser("smoke")

    publish_set = sub.add_parser("verify-publish-set")
    publish_set.add_argument("--dir", type=Path, required=True)
    publish_set.add_argument("--expected-key", required=True)

    publish_version = sub.add_parser("publish-version")
    publish_version.add_argument("--dir", type=Path, required=True)

    args = parser.parse_args()

    if args.command == "verify-source":
        verify_source(args.ref)
    elif args.command == "canonical-version":
        print(canonical_version(args.ref))
    elif args.command == "source-manifest":
        source_manifest(args.output)
    elif args.command == "verify-sdist":
        verify_sdist(args.dir)
    elif args.command == "verify-wheel":
        verify_wheel(args.dir, args.expected_platform)
    elif args.command == "smoke":
        smoke()
    elif args.command == "verify-publish-set":
        verify_publish_set(args.dir, args.expected_key)
    elif args.command == "publish-version":
        path = _single(args.dir, "*.tar.gz", "sdist")
        _, version = _read_sdist_metadata(path)
        print(version)
    else:
        raise AssertionError(args.command)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
