param(
    [string]$WheelOutputDir = 'ci-dist-windows'
)

$ErrorActionPreference = 'Stop'

function Assert-NativeSuccess {
    param([string]$Message)
    if ($LASTEXITCODE -ne 0) {
        throw $Message
    }
}

python .github/scripts/ci_python_policy.py --verify
Assert-NativeSuccess 'CI Python policy failed.'

python .github/scripts/ci_opencv_python.py verify-source
Assert-NativeSuccess 'OpenCV Python source policy failed.'

$Manifest = Join-Path $env:RUNNER_TEMP 'SOURCE_MANIFEST.txt'
python .github/scripts/ci_opencv_python.py source-manifest --output $Manifest
Assert-NativeSuccess 'Source manifest failed.'

python -m pip install --upgrade pip
Assert-NativeSuccess 'pip upgrade failed.'

python -c @'
import subprocess
import sys
import tomllib

with open("pyproject.toml", "rb") as handle:
    requirements = tomllib.load(handle)["build-system"]["requires"]

subprocess.check_call([
    sys.executable,
    "-m",
    "pip",
    "install",
    *requirements,
    "numpy>=2",
    "wheel",
    "twine",
    "cmake==4.3.2",
])
'@
Assert-NativeSuccess 'Build dependency installation failed.'

$RawDir = Join-Path $env:RUNNER_TEMP 'opencv-ft-raw-wheel'
Remove-Item -LiteralPath $RawDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $RawDir | Out-Null

$Output = Join-Path $PWD $WheelOutputDir
Remove-Item -LiteralPath $Output -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $Output | Out-Null

$env:CI_BUILD = '1'
$env:ENABLE_CONTRIB = '0'
$env:ENABLE_HEADLESS = '1'
$env:ENABLE_ROLLING = '0'
$env:OPENCV_PYTHON_SKIP_GIT_COMMANDS = '1'
$env:PYTHON_JIT = '1'
$env:PYTHON_GIL = '0'

python setup.py bdist_wheel --dist-dir $RawDir -v
Assert-NativeSuccess 'Windows free-threaded wheel build failed.'

python .github/scripts/ci_opencv_python.py verify-wheel `
    --dir $RawDir `
    --expected-platform win_amd64
Assert-NativeSuccess 'Windows wheel verification failed.'

$VerifyDir = Join-Path $env:RUNNER_TEMP 'opencv-ft-verify'
Remove-Item -LiteralPath $VerifyDir -Recurse -Force -ErrorAction SilentlyContinue
python -m venv $VerifyDir
Assert-NativeSuccess 'Fresh verification venv creation failed.'

$VerifyPython = Join-Path $VerifyDir 'Scripts\python.exe'
& $VerifyPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Verification pip upgrade failed.' }

$Wheel = Get-ChildItem -LiteralPath $RawDir -Filter '*.whl'
if ($Wheel.Count -ne 1) {
    throw "Expected exactly one Windows wheel, found $($Wheel.Count)."
}

& $VerifyPython -m pip install $Wheel[0].FullName
if ($LASTEXITCODE -ne 0) { throw 'Verification wheel install failed.' }

$env:PYTHON_JIT = '1'
$env:PYTHON_GIL = '0'
& $VerifyPython .github/scripts/ci_python_policy.py --verify
if ($LASTEXITCODE -ne 0) { throw 'Verification Python policy failed.' }

& $VerifyPython .github/scripts/ci_opencv_python.py smoke
if ($LASTEXITCODE -ne 0) { throw 'Installed OpenCV smoke failed.' }

Copy-Item -LiteralPath $Wheel[0].FullName -Destination $Output

git diff --exit-code
Assert-NativeSuccess 'Tracked source changed during Windows build.'

$status = @(git status --porcelain --untracked-files=no)
if ($status.Count -ne 0) {
    $status
    throw 'Tracked Git state changed during Windows build.'
}
