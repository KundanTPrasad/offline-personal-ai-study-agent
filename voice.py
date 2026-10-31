"""
Offline Voice Recognition Module using faster-whisper and sounddevice.
100% offline, zero internet or cloud API dependencies.
"""
from pathlib import Path
import threading
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

SAMPLE_RATE = 16000


class VoiceInput:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.model = None
        self.model_loading = False
        self.recording = False
        self.frames = []
        self.stream = None

    @classmethod
    def get_instance(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def preload_model(self):
        """Loads model in a background thread if not already loaded."""
        if self.model is None and not self.model_loading:
            def _load():
                self.model_loading = True
                try:
                    self.model = WhisperModel(
                        "base",
                        device="cpu",
                        compute_type="int8",
                        local_files_only=True,
                    )
                except Exception as e:
                    print(f"[Voice] Error loading model: {e}")
                finally:
                    self.model_loading = False

            t = threading.Thread(target=_load, daemon=True)
            t.start()

    def is_recording(self):
        return self.recording

    def start_recording(self):
        """Starts recording audio from default microphone."""
        if self.recording:
            return False
        self.frames = []
        self.recording = True

        def callback(indata, frames, time_info, status):
            if self.recording:
                self.frames.append(indata.copy())

        try:
            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                callback=callback,
            )
            self.stream.start()
            return True
        except Exception as e:
            self.recording = False
            raise RuntimeError(f"Microphone access error: {e}")

    def stop_and_transcribe(self):
        """Stops recording and returns the transcribed text."""
        self.recording = False
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        if not self.frames:
            return ""

        audio = np.concatenate(self.frames, axis=0).flatten()
        # If user pressed and released immediately (less than 0.3s)
        if len(audio) < int(SAMPLE_RATE * 0.3):
            return ""

        # Ensure model is ready
        if self.model is None:
            self.model = WhisperModel(
                "base",
                device="cpu",
                compute_type="int8",
                local_files_only=True,
            )

        try:
            segments, _info = self.model.transcribe(
                audio,
                beam_size=3,
                vad_filter=True,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            return text
        except Exception as e:
            print(f"[Voice] Transcription error: {e}")
            return ""
