"""Real GTK3 window lifecycle and system-plugin interoperability, natural GIL OFF."""
from __future__ import annotations

import argparse
import ctypes
import faulthandler
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
from typing import Callable, cast


def natural_gil_off() -> None:
    assert getattr(sys.flags, 'gil') is None
    assert not cast(Callable[[], bool], getattr(sys, '_is_gil_enabled'))()
    assert not os.environ.get('PYTHON_GIL')


def child(order: str) -> None:
    natural_gil_off()
    faulthandler.enable()
    faulthandler.dump_traceback_later(8)
    for name in ('GIO_MODULE_DIR', 'GIO_EXTRA_MODULES', 'GSETTINGS_BACKEND',
                 'OPENCV_UI_BACKEND', 'LD_PRELOAD', 'LD_LIBRARY_PATH'):
        assert not os.environ.get(name), name
    if order == 'system-gtk-first':
        gtk = ctypes.CDLL('libgtk-3.so.0')
        gtk.gtk_widget_get_type.restype = ctypes.c_size_t
        assert gtk.gtk_widget_get_type() != 0
    # Run the existing contracts before OpenCV's own bootstrap adjusts its
    # companion-library path. Their original inherited-environment assertions
    # remain intact; the subsequent real GTK lifecycle shares this process.
    scripts = Path(__file__).resolve().parent
    runpy.run_path(str(scripts/'cp315t_video_contract.py'), run_name='__main__')
    runpy.run_path(str(scripts/'cp315t_text_contract.py'), run_name='__main__')
    import cv2
    import numpy as np

    assert cv2.currentUIFramework() == 'GTK3'
    title = 'OpenCV actual GTK3 lifecycle'
    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    try:
        cv2.resizeWindow(title, 240, 180)
        for value in (32, 128, 224):
            cv2.imshow(title, np.full((120, 160, 3), value, dtype=np.uint8))
            cv2.waitKey(20)
        # The exact GTK backend implements AUTOSIZE, not WND_PROP_VISIBLE.
        assert cv2.getWindowProperty(title, cv2.WND_PROP_AUTOSIZE) == 0
        rectangle = cv2.getWindowImageRect(title)
        assert rectangle[2] > 0 and rectangle[3] > 0
    finally:
        cv2.destroyAllWindows()
        cv2.waitKey(1)
    natural_gil_off()
    paths = sorted({line.split()[-1] for line in Path('/proc/self/maps').read_text().splitlines()
                    if any(part in line for part in ('libgio-', 'libgtk-', 'libgdk-',
                                                    'libgdk_pixbuf-', 'libglib-', 'libgobject-'))})
    for prefix in ('libgio-2', 'libgtk-3', 'libgdk-3', 'libgdk_pixbuf-2'):
        assert len([path for path in paths if Path(path).name.startswith(prefix)]) == 1, paths
    faulthandler.cancel_dump_traceback_later()
    print(json.dumps({'status': 'PASSED', 'order': order, 'module': cv2.__file__,
        'framework': cv2.currentUIFramework(), 'frames': 3, 'libraries': paths,
        'same_process_codec_and_font_contracts': True,
        'GIL': False, 'GIL_flag': None}), flush=True)


def main(output: Path) -> None:
    assert sys.platform == 'linux' and os.environ.get('DISPLAY'), 'Run with a real X server or xvfb-run.'
    natural_gil_off()
    assert not output.exists()
    cases: list[dict[str, object]] = []
    for order in ('cv2-first', 'system-gtk-first'):
        argv = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--child', order]
        with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              start_new_session=True, text=True) as process:
            timed_out = False
            try:
                stdout, stderr = process.communicate(timeout=12)
            except subprocess.TimeoutExpired:
                timed_out = True
                getpgid = cast(Callable[[int], int], getattr(os, 'getpgid'))
                killpg = cast(Callable[[int, int], None], getattr(os, 'killpg'))
                assert process.poll() is None and getpgid(process.pid) == process.pid
                killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate(timeout=3)
        row: dict[str, object] = {'order': order, 'argv': argv, 'exit_code': process.returncode,
            'timed_out': timed_out, 'stdout': stdout, 'stderr': stderr}
        cases.append(row)
        output.write_text(json.dumps({'scope': 'Actual GTK3 GUI/software display; no physical camera/GPU coverage.',
                                      'cases': cases}, indent=2)+'\n')
        assert process.returncode == 0 and not timed_out, row
        assert not any(token in stdout+stderr for token in ('cannot register existing type',
            'GLib-GObject-CRITICAL', 'GLib-CRITICAL', 'Segmentation fault'))
    print(json.dumps({'status': 'PASSED', 'cases': len(cases), 'output': str(output)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child', choices=('cv2-first', 'system-gtk-first'))
    parser.add_argument('--output', type=Path, default=Path('gtk-contract.json'))
    args = parser.parse_args()
    if args.child:
        child(args.child)
    else:
        main(args.output)
