import sys
import types
import unittest
from unittest.mock import patch

fake_numpy = types.ModuleType("numpy")
fake_numpy.ndarray = object
sys.modules.setdefault("numpy", fake_numpy)

fake_cv2 = types.ModuleType("cv2")
fake_cv2.FONT_HERSHEY_SIMPLEX = 0
sys.modules.setdefault("cv2", fake_cv2)

from stream import H264Streamer


class DummyPipe:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class DummyProc:
    def __init__(self):
        self.stdin = None
        self.stdout = DummyPipe()

    def kill(self):
        pass

    def wait(self, timeout=None):
        pass

    def poll(self):
        return None


def make_streamer() -> H264Streamer:
    streamer = H264Streamer()
    streamer.device_id = "device-1"
    streamer.phone_w = 1080
    streamer.phone_h = 2400
    return streamer


class StartStreamTests(unittest.TestCase):
    @patch("stream.subprocess.Popen")
    def test_device_resize_uses_screenrecord_size_and_low_latency_ffmpeg(self, popen):
        popen.side_effect = [DummyProc(), DummyProc()]
        streamer = make_streamer()

        self.assertTrue(streamer.start_stream(device_resize=True))

        adb_cmd = popen.call_args_list[0][0][0]
        ff_cmd = popen.call_args_list[1][0][0]

        self.assertIn("--size", adb_cmd)
        self.assertIn("486x1080", adb_cmd)
        self.assertNotIn("-vf", ff_cmd)
        self.assertIn("-fflags", ff_cmd)
        self.assertIn("nobuffer", ff_cmd)
        self.assertIn("-flags", ff_cmd)
        self.assertIn("low_delay", ff_cmd)
        self.assertEqual(streamer.frame_size, 486 * 1080 * 3)
        self.assertTrue(streamer.use_device_resize)

    @patch("stream.subprocess.Popen")
    def test_ffmpeg_resize_omits_screenrecord_size_and_scales_in_ffmpeg(self, popen):
        popen.side_effect = [DummyProc(), DummyProc()]
        streamer = make_streamer()

        self.assertTrue(streamer.start_stream(device_resize=False))

        adb_cmd = popen.call_args_list[0][0][0]
        ff_cmd = popen.call_args_list[1][0][0]

        self.assertNotIn("--size", adb_cmd)
        self.assertIn("-vf", ff_cmd)
        self.assertIn("scale=486:1080", ff_cmd)
        self.assertNotIn("-fflags", ff_cmd)
        self.assertEqual(streamer.frame_size, 486 * 1080 * 3)
        self.assertFalse(streamer.use_device_resize)

    def test_fallback_switches_to_ffmpeg_resize_once(self):
        streamer = make_streamer()
        streamer.use_device_resize = True

        with patch("builtins.print"), patch.object(
            streamer, "start_stream", return_value=True
        ) as start_stream:
            self.assertTrue(streamer.fallback_to_ffmpeg_resize())

        start_stream.assert_called_once_with(device_resize=False)

    def test_fallback_is_noop_after_ffmpeg_resize_is_active(self):
        streamer = make_streamer()
        streamer.use_device_resize = False

        with patch.object(streamer, "start_stream") as start_stream:
            self.assertFalse(streamer.fallback_to_ffmpeg_resize())

        start_stream.assert_not_called()


if __name__ == "__main__":
    unittest.main()
