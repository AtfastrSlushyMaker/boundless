"""Where the face is in a portrait, so small thumbnails can zoom onto it.

Returns the face centre and height as fractions of the image. Painted faces are not
always found, so callers fall back to where a standing full-length figure's head sits.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

FULL_BODY_DEFAULT = {"x": 0.5, "y": 0.125, "size": 0.085, "detected": False}


def face_focus(data: bytes) -> dict[str, Any] | None:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    try:
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None:
            return None
        height, width = image.shape[:2]
        gray = cv2.equalizeHist(image)
        minimum = max(24, int(height * 0.025))
        faces = []
        for name in ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml", "haarcascade_profileface.xml"):
            cascade = cv2.CascadeClassifier(cv2.data.haarcascades + name)
            found = cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=5, minSize=(minimum, minimum))
            faces += [tuple(int(value) for value in face) for face in found]
            if faces:
                break
    except Exception:  # noqa: BLE001 - a missing focus only means a default crop
        logger.warning("Face detection failed", exc_info=True)
        return None
    # The subject's face is large and in the upper part of the frame; ignore faces in paintings behind them.
    candidates = [face for face in faces if face[1] + face[3] / 2 < height * 0.6]
    if not candidates:
        return None
    x, y, w, h = max(candidates, key=lambda face: face[2] * face[3])
    return {"x": round((x + w / 2) / width, 4), "y": round((y + h / 2) / height, 4),
            "size": round(h / height, 4), "detected": True}


def image_aspect(data: bytes) -> float | None:
    """Width over height, read from the PNG header, or decoded for other formats."""
    if data.startswith(b"\x89PNG") and len(data) > 24:
        width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        return round(width / height, 4) if height else None
    try:
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        return round(image.shape[1] / image.shape[0], 4) if image is not None else None
    except Exception:  # noqa: BLE001
        return None
