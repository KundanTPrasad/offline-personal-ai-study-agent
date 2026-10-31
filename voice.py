"""
Offline Voice Recognition Module using faster-whisper and sounddevice.
100% offline, zero internet or cloud API dependencies.
Optimized for English, Hindi, and Hinglish (code-mixed student speech).
"""
from pathlib import Path
import re
import threading
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

SAMPLE_RATE = 16000

# Rich prompt priming Whisper to transcribe code-mixed Hinglish naturally in Latin script
HINGLISH_PROMPT = (
    "Aise Hinglish mein baat karo: aaj ka plan batao, kal 3 DSA questions karne hain, "
    "lecture ho gaya, sab kaam ho gaya, checkin kardo, revision pending hai, "
    "practice questions banao, cancel plan, college jaana hai, kal math revision karna hai, "
    "1 lecture dekhna hai, streak kitni hai, notes analyze karo."
)

# ----------------- Devanagari to Hinglish Transliteration Engine -----------------
VOWELS = {
    'अ': 'a', 'आ': 'aa', 'इ': 'i', 'ई': 'ee', 'उ': 'u', 'ऊ': 'oo',
    'ऋ': 'ri', 'ए': 'e', 'ऐ': 'ai', 'ओ': 'o', 'औ': 'au', 'अं': 'an', 'अः': 'ah'
}

MATRAS = {
    'ा': 'a', 'ि': 'i', 'ी': 'ee', 'ु': 'u', 'ू': 'oo',
    'ृ': 'ri', 'े': 'e', 'ै': 'ai', 'ो': 'o', 'ौ': 'au',
    'ं': 'n', 'ँ': 'n', 'ः': 'h', '्': ''
}

CONSONANTS = {
    'क': 'k', 'ख': 'kh', 'ग': 'g', 'घ': 'gh', 'ङ': 'ng',
    'च': 'ch', 'छ': 'chh', 'ज': 'j', 'झ': 'jh', 'ञ': 'ny',
    'ट': 't', 'ठ': 'th', 'ड': 'd', 'ढ': 'dh', 'ण': 'n',
    'त': 't', 'थ': 'th', 'द': 'd', 'ध': 'dh', 'न': 'n',
    'प': 'p', 'फ': 'ph', 'ब': 'b', 'भ': 'bh', 'म': 'm',
    'य': 'y', 'र': 'r', 'ल': 'l', 'व': 'v',
    'श': 'sh', 'ष': 'sh', 'स': 's', 'ह': 'h',
    'क़': 'q', 'ख़': 'kh', 'ग़': 'gh', 'ज़': 'z', 'ड़': 'd', 'ढ़': 'dh', 'फ़': 'f'
}

NUMERALS = {
    '०': '0', '१': '1', '२': '2', '३': '3', '४': '4',
    '५': '5', '६': '6', '७': '7', '८': '8', '९': '9'
}

# Common academic & conversational Hindi -> Hinglish / English vocabulary map
DICTIONARY = {
    'प्लान': 'plan', 'क्वेश्चन': 'question', 'क्वेश्चन्स': 'questions',
    'लेक्चर': 'lecture', 'लेक्चर्स': 'lectures', 'डीएसए': 'DSA',
    'डी': 'D', 'एस': 'S', 'ए': 'A', 'कैंसिल': 'cancel',
    'रद्द': 'cancel', 'अपडेट': 'update', 'टास्क': 'task', 'टास्क्स': 'tasks',
    'चेकइन': 'checkin', 'चेक': 'check', 'इन': 'in', 'रिवीजन': 'revision',
    'नोट्स': 'notes', 'समरी': 'summary', 'प्रैक्टिस': 'practice',
    'कॉलेज': 'college', 'पार्टी': 'party', 'टुडे': 'today',
    'टुमॉरो': 'tomorrow', 'फ्यूचर': 'future', 'काम': 'kaam',
    'आज': 'aaj', 'कल': 'kal', 'हो': 'ho', 'गया': 'gaya', 'गए': 'gaye',
    'गई': 'gayi', 'करना': 'karna', 'करने': 'karne', 'करनी': 'karni',
    'है': 'hai', 'हैं': 'hain', 'बताओ': 'batao', 'दिखाओ': 'dikhao',
    'नहीं': 'nahi', 'हुआ': 'hua', 'हुए': 'hue', 'हुई': 'hui',
    'कर': 'kar', 'दो': 'do', 'दी': 'di', 'दिया': 'diya',
    'बाकी': 'baaki', 'पेंडिंग': 'pending', 'खत्म': 'khatam',
    'पूरा': 'poora', 'पूरे': 'poore', 'सब': 'sab', 'सारे': 'saare',
    'कितना': 'kitna', 'कितनी': 'kitni', 'स्टडी': 'study'
}


def transliterate_devanagari_word(word):
    if word in DICTIONARY:
        return DICTIONARY[word]

    chars = list(word)
    n = len(chars)
    res = []
    i = 0
    while i < n:
        c = chars[i]
        # Check two-char nukta
        if i + 1 < n and (c + chars[i + 1]) in CONSONANTS:
            c = c + chars[i + 1]
            i += 1

        if c in CONSONANTS:
            base = CONSONANTS[c]
            # Next is halant or matra
            if i + 1 < n and chars[i + 1] in MATRAS:
                matra = chars[i + 1]
                i += 1
                res.append(base + MATRAS[matra])
            else:
                # Schwa deletion: add 'a' only if not word-final
                if i + 1 < n and chars[i + 1] in CONSONANTS:
                    res.append(base + 'a')
                else:
                    res.append(base)
        elif c in VOWELS:
            res.append(VOWELS[c])
        elif c in MATRAS:
            res.append(MATRAS[c])
        else:
            res.append(c)
        i += 1

    out = ''.join(res)
    out = re.sub(r'ee\b', 'i', out)
    out = re.sub(r'aa\b', 'a', out)
    return out


def devanagari_to_hinglish(text):
    """Converts Devanagari script to natural Roman Hinglish."""
    if not re.search(r'[\u0900-\u097F]', text):
        return text

    # Pre-replace known vocabulary (longest first)
    for hi_w, en_w in sorted(DICTIONARY.items(), key=lambda x: -len(x[0])):
        text = text.replace(hi_w, en_w)
    for hi_d, en_d in NUMERALS.items():
        text = text.replace(hi_d, en_d)

    # Tokenize word-by-word while preserving whitespace and punctuation
    tokens = re.split(r'(\s+|[^\w\u0900-\u097F]+)', text)
    result = []
    for tok in tokens:
        if re.search(r'[\u0900-\u097F]', tok):
            result.append(transliterate_devanagari_word(tok))
        else:
            result.append(tok)
    return ''.join(result)


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
        """Stops recording and returns natural Hinglish or English transcribed text."""
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
            # Transcribe with Hinglish prompt and beam search for high accuracy
            segments, info = self.model.transcribe(
                audio,
                beam_size=5,
                initial_prompt=HINGLISH_PROMPT,
                vad_filter=True,
            )
            raw_text = " ".join(s.text.strip() for s in segments).strip()

            # Convert any Devanagari words to clean Roman Hinglish
            clean_text = devanagari_to_hinglish(raw_text)
            return clean_text
        except Exception as e:
            print(f"[Voice] Transcription error: {e}")
            return ""
