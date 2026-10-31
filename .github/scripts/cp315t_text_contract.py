"""Ordinary drawing/text API regression for the HarfBuzz GTK link correction."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import sys

expected_gil = os.environ.get('PYTHON_GIL') == '1'
assert sys._is_gil_enabled() is expected_gil
import cv2
import numpy as np
assert sys._is_gil_enabled() is expected_gil

fonts = [cv2.FONT_HERSHEY_SIMPLEX, cv2.FONT_HERSHEY_PLAIN, cv2.FONT_HERSHEY_DUPLEX,
         cv2.FONT_HERSHEY_COMPLEX, cv2.FONT_HERSHEY_TRIPLEX, cv2.FONT_HERSHEY_COMPLEX_SMALL,
         cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, cv2.FONT_HERSHEY_SCRIPT_COMPLEX]
texts = ['StableKite', '0123456789', 'Привет', 'مرحبا']
cases = [(font, text, line_type) for font in fonts for text in texts for line_type in (cv2.LINE_8, cv2.LINE_AA)]

def draw(case):
    font, text, line_type = case
    image = np.zeros((180, 420), dtype=np.uint8)
    dimensions, baseline = cv2.getTextSize(text, font, 1.0, 2)
    assert dimensions[0] > 0 and dimensions[1] > 0 and baseline >= 0
    result = cv2.putText(image, text, (15, 90), font, 1.0, 255, 2, line_type)
    assert np.shares_memory(result, image) and np.count_nonzero(image) > 0
    return hashlib.sha256(image.tobytes()).hexdigest()

reference = [draw(case) for case in cases]
def worker(seed):
    for offset in range(64):
        number = (seed * 17 + offset) % len(cases)
        assert draw(cases[number]) == reference[number]
    return 64
with ThreadPoolExecutor(max_workers=8) as pool:
    assert sum(pool.map(worker, range(8))) == 512
assert sys._is_gil_enabled() is expected_gil
print(json.dumps({'status':'PASS', 'python':sys.version, 'GIL':expected_gil, 'cv2':cv2.__version__, 'native_file':cv2.__file__, 'serial_font_text_cases':len(cases), 'concurrent_text_renders':512, 'threads':8, 'render_digests':reference}))
