"""Codec smoke test with a diagnostic sine, never a musical checkpoint."""
import math
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
import unittest
import wave

from musicstudio.common import ffmpeg_bin

private = ffmpeg_bin()
FFMPEG = str(private / "ffmpeg.exe") if private else shutil.which("ffmpeg")


class AudioExportTests(unittest.TestCase):
    @unittest.skipUnless(FFMPEG, "No hay FFmpeg disponible; no se instala.")
    def test_stereo_pcm_to_mp3_and_back_preserves_duration_and_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, mp3, decoded = root / "sine.wav", root / "sine.mp3", root / "decoded.wav"
            samples = [int(3000 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(4800)]
            with wave.open(str(source), "wb") as audio:
                audio.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
                audio.writeframes(b"".join(struct.pack("<hh", sample, sample) for sample in samples))
            encode = subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-y", "-i", str(source),
                "-codec:a", "libmp3lame", "-b:a", "192k", str(mp3)], capture_output=True, timeout=30)
            self.assertEqual(encode.returncode, 0, encode.stderr.decode("utf-8", "replace"))
            self.assertGreater(mp3.stat().st_size, 128)
            decode = subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-i", str(mp3),
                "-codec:a", "pcm_s16le", str(decoded)], capture_output=True, timeout=30)
            self.assertEqual(decode.returncode, 0, decode.stderr.decode("utf-8", "replace"))
            with wave.open(str(decoded), "rb") as audio:
                self.assertEqual(audio.getnchannels(), 2)
                self.assertEqual(audio.getframerate(), 48000)
                self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), .1, delta=.01)
                self.assertTrue(any(audio.readframes(4800)))


if __name__ == "__main__":
    unittest.main()
