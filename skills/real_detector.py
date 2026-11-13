"""Real detector: front-camera frame + YOLO, run only inside the vision service (skills/docs/robot.md).

All third-party imports (unitree_sdk2py, cv2, numpy, ultralytics) are inside methods, so importing
this module needs only the standard library. It reads the camera and never creates a SportClient.
"""

from __future__ import annotations

import os

from contract.env import YOLO_WEIGHTS
from skills import real, vision
from skills.backend import BadFrame, CameraUnavailable, DetectResult, WeightsMissing


class RealDetector:
    """YOLO and the VideoClient are created once and kept across requests; a part that failed is
    retried on the next detect()."""

    CAMERA_TIMEOUT_S = 3.0
    IMGSZ = 640
    CONF = 0.4

    def __init__(self) -> None:
        self._model = None
        self._video = None

    def _load_model(self):
        if self._model is None:
            weights = os.environ.get(YOLO_WEIGHTS, "")
            if not weights or not os.path.isfile(weights):
                # never let ultralytics download weights automatically
                raise WeightsMissing(f"YOLO weights file not found: {weights or '(unset)'}")
            from ultralytics import YOLO

            self._model = YOLO(weights)
        return self._model

    def _video_client(self):
        if self._video is None:
            real.init_dds()
            from unitree_sdk2py.go2.video.video_client import VideoClient

            client = VideoClient()
            client.SetTimeout(self.CAMERA_TIMEOUT_S)
            client.Init()
            self._video = client
        return self._video

    def warm_up(self) -> None:
        """Load YOLO, run one predict on a blank image (the first predict is slow), open the camera client."""
        import numpy as np

        self._load_model().predict(
            np.zeros((self.IMGSZ, self.IMGSZ, 3), dtype=np.uint8), imgsz=self.IMGSZ, verbose=False
        )
        self._video_client()

    def _frame(self):
        code, data = self._video_client().GetImageSample()
        if code != 0:
            raise CameraUnavailable(f"GetImageSample returned {code}")
        if not data:
            raise CameraUnavailable("GetImageSample returned no data")
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(bytes(data), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise BadFrame("camera frame could not be decoded")
        return image

    def detect(self, target: str) -> DetectResult:
        model = self._load_model()
        image = self._frame()
        height, width = image.shape[:2]
        best = None  # (confidence, x1, y1, x2, y2)
        for res in model.predict(image, imgsz=self.IMGSZ, conf=self.CONF, verbose=False):
            names = res.names  # pyright: ignore[reportAttributeAccessIssue]  # ultralytics types results as Results | Tensor
            for box in res.boxes:  # pyright: ignore[reportAttributeAccessIssue, reportOptionalIterable]  # always set for a detect model
                if names[int(box.cls[0])] != target:
                    continue
                conf = float(box.conf[0])
                if best is None or conf > best[0]:
                    best = (conf, *[float(v) for v in box.xyxy[0].tolist()])
        if best is None:
            return DetectResult(found=False)
        conf, x1, y1, x2, y2 = best
        return vision.classify_box((x1 + x2) / 2.0 / width, (y2 - y1) / height, conf)
