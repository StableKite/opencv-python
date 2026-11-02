"""Real tiny AVI codec/FFmpeg and concurrent I/O checks at natural GIL off."""
from concurrent.futures import ThreadPoolExecutor
import importlib.metadata as md
import json,os
from pathlib import Path
import sys,tempfile,threading
from typing import Callable, TypedDict, cast

# CPython's actual runtime diagnostic has no public replacement.
is_gil_enabled = cast(Callable[[], bool], getattr(sys, '_is_gil_enabled'))
assert getattr(sys.flags, 'gil') is None and not is_gil_enabled()
assert not any(os.environ.get(k) for k in ['PYTHON_GIL','LD_PRELOAD','LD_LIBRARY_PATH'])
import cv2
import numpy as np
import numpy.typing as npt
assert not is_gil_enabled()
distributions=[d.metadata['Name'].lower().replace('_','-') for d in md.distributions() if d.metadata['Name'].lower().replace('_','-').startswith('opencv-')]
assert distributions==['opencv-python'],distributions
assert cv2.videoio_registry.hasBackend(cv2.CAP_FFMPEG)

class VideoResult(TypedDict):
    codec: str
    number: int
    frames: int
    bytes: int
    maximum_pixel_error: int
    signal_correlation: float
    maximum_frame_MSE: float
    native_thread: int

def roundtrip(codec: str, number: int, folder: Path) -> VideoResult:
    suffix = 'mp4' if codec == 'avc1' else 'avi'
    path=folder/f'{codec}-{number}.{suffix}'
    writer=cv2.VideoWriter(str(path),cv2.CAP_FFMPEG,cv2.VideoWriter.fourcc(*codec),10,(32,24))
    assert writer.isOpened(),codec
    assert writer.getBackendName()=='FFMPEG'
    expected: list[npt.NDArray[np.uint8]]=[]
    try:
        for i in range(12):
            frame=np.full((24,32,3),(i*13+number*7)%200,dtype=np.uint8)
            writer.write(frame);expected.append(frame)
    finally:
        writer.release()
    assert path.stat().st_size>0
    capture=cv2.VideoCapture(str(path),cv2.CAP_FFMPEG)
    assert capture.isOpened()
    assert capture.getBackendName()=='FFMPEG'
    frames=0;maximum_error=0
    decoded_means: list[float] = []
    frame_MSE: list[float] = []
    try:
        while True:
            ok,frame=capture.read()
            if not ok:break
            assert frames<len(expected) and frame.shape==(24,32,3)
            error=int(np.abs(frame.astype(np.int16)-expected[frames].astype(np.int16)).max())
            if codec != 'avc1':
                assert error<=(0 if codec=='FFV1' else 8),(codec,frames,error)
            decoded_means.append(float(frame.mean()))
            frame_MSE.append(float(np.mean((frame.astype(np.float64)-expected[frames])**2)))
            maximum_error=max(error,maximum_error);frames+=1
    finally:
        capture.release()
    assert frames==12,(codec,frames)
    expected_means = [float(frame.mean()) for frame in expected]
    correlation = float(np.corrcoef(expected_means, decoded_means)[0, 1])
    # H.264 is lossy. Check the known ascending signal and record its actual
    # peak/MSE; a universal single-pixel limit is not a lossless codec contract.
    # The same unmodified SDK's CLI reference is retained in qualification data.
    assert correlation >= 0.99, (codec, correlation)
    assert all(b > a for a, b in zip(decoded_means, decoded_means[1:])), codec
    return {'codec':codec,'number':number,'frames':frames,'bytes':path.stat().st_size,'maximum_pixel_error':maximum_error,'signal_correlation':correlation,'maximum_frame_MSE':max(frame_MSE),'native_thread':threading.get_native_id()}

with tempfile.TemporaryDirectory(prefix='sk-opencv-ffmpeg-') as temporary:
    folder=Path(temporary)
    codecs = ('XVID','MJPG','FFV1','avc1') if sys.platform == 'linux' else ('XVID','MJPG','FFV1')
    serial=[roundtrip(codec,0,folder) for codec in codecs]
    barrier=threading.Barrier(8)
    def worker(n: int) -> list[VideoResult]:
        barrier.wait(timeout=30)
        return [roundtrip(codec,n+1,folder) for codec in codecs]
    with ThreadPoolExecutor(max_workers=8) as pool:parallel=list(pool.map(worker,range(8)))
    assert len({group[0]['native_thread'] for group in parallel})==8
assert not is_gil_enabled()
print(json.dumps({'status':'PASS','python':sys.version,'GIL':False,'gil_flag':getattr(sys.flags, 'gil'),'version':md.version('opencv-python'),'FFmpeg':True,'serial':serial,'parallel':parallel,'total_frames':9*len(codecs)*12}),flush=True)
