import ctypes
from pathlib import Path
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext

from pynput import keyboard

import agent
import db
import voice

# ---------- HIGH-DPI AWARENESS (FIXES BLURRY TEXT ON WINDOWS 11) ----------
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

HOTKEY = "<ctrl>+<alt>+k"
VOICE_HOTKEY = "<ctrl>+<alt>+v"
events = queue.Queue()

# High-Contrast Sleek Glassy Palette
BG_GLASS = "#090d16"       # Deep dark background
SURFACE_GLASS = "#131b2e"  # Subtle glass surface
BORDER_COLOR = "#1f293d"   # Crisp border
TEXT_MAIN = "#f8fafc"      # Crisp white
TEXT_MUTED = "#94a3b8"     # Crisp slate
TEXT_FOOTER = "#64748b"    # Footer slate
ACCENT_BLUE = "#2563eb"    # Royal blue
ACCENT_BLUE_HOVER = "#1d4ed8"
USER_COLOR = "#60a5fa"     # Blue 400
AGENT_COLOR = "#34d399"    # Emerald 400
TABLE_COLOR = "#38bdf8"    # Sky blue for tabular view
HEADER_ACCENT = "#fbbf24"  # Warm amber for section headers

FONT_TITLE = ("Segoe UI", 12, "bold")
FONT_MSG = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_HEADER = ("Segoe UI", 11, "bold")
FONT_TABLE = ("Consolas", 9, "bold")
FONT_FOOTER = ("Segoe UI", 8)


class ChatWindow:
    def __init__(self, root):
        self.win = tk.Toplevel(root)
        self.win.title("Your Assistant")
        self.win.geometry("600x680")
        self.win.minsize(520, 580)
        self.win.configure(bg=BG_GLASS)
        self.win.protocol("WM_DELETE_WINDOW", self.hide)

        # High-Contrast Glassy Effect (0.98 ensures zero background bleed-through)
        try:
            self.win.attributes("-alpha", 0.98)
        except Exception:
            pass

        self.history = []
        self.busy = False
        self.voice_recording = False
        self.voice_inst = voice.VoiceInput.get_instance()
        self.thinking_index = None

        # ---------- TOP HEADER BAR ----------
        header = tk.Frame(self.win, bg=SURFACE_GLASS, height=48, padx=16, pady=10)
        header.pack(fill="x", side="top")

        app_title = tk.Label(
            header,
            text="Your Assistant",
            font=FONT_TITLE,
            fg=TEXT_MAIN,
            bg=SURFACE_GLASS,
        )
        app_title.pack(side="left")

        # ---------- CHAT DISPLAY ----------
        chat_frame = tk.Frame(self.win, bg=BG_GLASS, padx=12, pady=6)
        chat_frame.pack(fill="both", expand=True)

        self.chat = scrolledtext.ScrolledText(
            chat_frame,
            state="disabled",
            wrap="word",
            font=FONT_MSG,
            bg=BG_GLASS,
            fg=TEXT_MAIN,
            insertbackground=TEXT_MAIN,
            relief="flat",
            highlightthickness=0,
            padx=10,
            pady=10,
        )
        self.chat.pack(fill="both", expand=True)

        # Message Tags Styling (Sharp DPI-crisp rendering)
        self.chat.tag_config("user_label", foreground=USER_COLOR, font=FONT_BOLD)
        self.chat.tag_config("user_text", foreground="#e2e8f0", font=FONT_MSG)
        self.chat.tag_config("agent_label", foreground=AGENT_COLOR, font=FONT_BOLD)
        self.chat.tag_config("agent_text", foreground=TEXT_MAIN, font=FONT_MSG)
        self.chat.tag_config("bold_text", foreground="#f8fafc", font=FONT_BOLD)
        self.chat.tag_config("header_tag", foreground=HEADER_ACCENT, font=FONT_HEADER)
        self.chat.tag_config("table", foreground=TABLE_COLOR, font=FONT_TABLE)
        self.chat.tag_config("note", foreground=TEXT_MUTED, font=("Segoe UI", 9, "italic"))

        # ---------- BOTTOM INPUT BAR ----------
        bottom = tk.Frame(self.win, bg=SURFACE_GLASS, padx=12, pady=10)
        bottom.pack(fill="x", side="bottom")

        # Action Buttons on right (Packed first for guaranteed visibility)
        btn_frame = tk.Frame(bottom, bg=SURFACE_GLASS)
        btn_frame.pack(side="right", padx=(10, 0), fill="y")

        top_btns = tk.Frame(btn_frame, bg=SURFACE_GLASS)
        top_btns.pack(side="top", fill="x", pady=(0, 4))

        self.send_btn = tk.Button(
            top_btns,
            text="Send ➔",
            bg=ACCENT_BLUE,
            fg="#ffffff",
            activebackground=ACCENT_BLUE_HOVER,
            activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=8,
            pady=4,
            cursor="hand2",
            command=self.send,
        )
        self.send_btn.pack(side="left", padx=(0, 4))

        self.voice_btn = tk.Button(
            top_btns,
            text="🎙️ Voice",
            bg="#7c3aed",
            fg="#ffffff",
            activebackground="#6d28d9",
            activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=8,
            pady=4,
            cursor="hand2",
            command=self.toggle_voice,
        )
        self.voice_btn.pack(side="left", padx=(0, 4))

        self.upload_btn = tk.Button(
            top_btns,
            text="📎 Attach",
            bg="#0f766e",
            fg="#ffffff",
            activebackground="#115e59",
            activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=8,
            pady=4,
            cursor="hand2",
            command=self.upload_document,
        )
        self.upload_btn.pack(side="left")

        self.checkin_btn = tk.Button(
            btn_frame,
            text="📋 Day Check-in",
            bg=BORDER_COLOR,
            fg="#e2e8f0",
            activebackground="#334155",
            activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=8,
            pady=4,
            cursor="hand2",
            command=self.on_checkin_click,
        )
        self.checkin_btn.pack(side="bottom", fill="x")

        # Input Text Area
        self.entry = tk.Text(
            bottom,
            height=3,
            wrap="word",
            font=FONT_MSG,
            bg=BG_GLASS,
            fg=TEXT_MAIN,
            insertbackground="#38bdf8",
            relief="flat",
            padx=10,
            pady=8,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor="#38bdf8",
        )
        self.entry.pack(side="left", fill="both", expand=True)
        self.entry.bind("<Return>", self.on_enter)
        self.win.bind("<F4>", lambda e: self.toggle_voice())

        # ---------- FOOTER STATUS HINT ----------
        footer = tk.Frame(self.win, bg=BG_GLASS, padx=16, pady=4)
        footer.pack(fill="x", side="bottom")
        footer_label = tk.Label(
            footer,
            text="Enter: Send  •  Shift+Enter: New Line  •  Ctrl+Alt+K: Toggle  •  🎙️ Voice (F4 / Ctrl+Alt+V)  •  📎 Attach Docs",
            font=FONT_FOOTER,
            fg=TEXT_FOOTER,
            bg=BG_GLASS,
        )
        footer_label.pack(side="left")

        # Startup greeting
        startup_msg = agent.get_startup_message()
        self.add_message("Assistant", startup_msg, is_user=False)
        self.history.append({"role": "assistant", "content": startup_msg})
        self.show()

    # ---------- chat display with clean markdown renderer ----------
    def add_message(self, label, text, is_user=False):
        self.chat.config(state="normal")
        if is_user:
            self.chat.insert("end", "You\n", "user_label")
            self.chat.insert("end", f"{text}\n\n", "user_text")
        else:
            self.chat.insert("end", f"{label}\n", "agent_label")
            self.render_clean_text(text, default_tag="agent_text")
            self.chat.insert("end", "\n")
        self.chat.see("end")
        self.chat.config(state="disabled")

    def render_clean_text(self, text, default_tag="agent_text"):
        """Render text with clean typography, stripping raw asterisks and formatting bold/headers."""
        for line in text.split("\n"):
            # Table lines
            if line.startswith("+--") or line.startswith("| "):
                self.chat.insert("end", f"{line}\n", "table")
                continue

            clean = line.strip()

            # Section Headers (e.g. **QUICK SUMMARY** or QUICK SUMMARY)
            is_header = False
            for h in ("QUICK SUMMARY", "PRACTICE QUESTIONS", "SUMMARY", "QUESTIONS"):
                if h in clean.upper() and len(clean) < 35:
                    is_header = True
                    break

            if is_header:
                title_clean = clean.replace("*", "").replace("#", "").strip()
                icon = "📌" if "SUMMARY" in title_clean.upper() else "💡"
                self.chat.insert("end", f"\n{icon} {title_clean}\n", "header_tag")
                continue

            # Strip blockquote '>' and clean up backticks
            render_line = line.replace("`", "")
            if render_line.strip().startswith(">"):
                render_line = "   " + render_line.strip()[1:].strip()

            # Inline bold parser for **...**
            parts = re.split(r"(\*\*.*?\*\*)", render_line)
            for p in parts:
                if p.startswith("**") and p.endswith("**"):
                    self.chat.insert("end", p[2:-2], "bold_text")
                else:
                    self.chat.insert("end", p, default_tag)
            self.chat.insert("end", "\n")

    def show_thinking(self, msg="Thinking..."):
        self.chat.config(state="normal")
        self.thinking_index = self.chat.index("end-1c")
        self.chat.insert("end", f"{msg}\n", "note")
        self.chat.see("end")
        self.chat.config(state="disabled")

    def clear_thinking(self):
        if self.thinking_index:
            self.chat.config(state="normal")
            self.chat.delete(self.thinking_index, "end")
            self.chat.config(state="disabled")
            self.thinking_index = None

    # ---------- sending ----------
    def on_enter(self, event):
        if event.state & 0x1:  # Shift+Enter makes a new line
            return None
        self.send()
        return "break"

    def send(self):
        text = self.entry.get("1.0", "end").strip()
        if not text or self.busy:
            return
        self.entry.delete("1.0", "end")
        self.add_message("You", text, is_user=True)
        self.busy = True
        self.send_btn.config(state="disabled")
        self.voice_btn.config(state="disabled")
        self.upload_btn.config(state="disabled")
        self.show_thinking("Thinking...")
        threading.Thread(target=self.work, args=(text,), daemon=True).start()

    def on_checkin_click(self):
        reply = agent.do_checkin("en")
        self.add_message("Assistant", reply, is_user=False)
        self.history.append({"role": "assistant", "content": reply})
        self.entry.focus_set()

    # ---------- upload document (universal attachment handler) ----------
    def upload_document(self):
        if self.busy:
            return
        file_path = filedialog.askopenfilename(
            title="Attach Study Document (PDF, Word, PPT, Excel, Images, Code)",
            filetypes=[
                (
                    "All Supported Files",
                    "*.pdf;*.docx;*.pptx;*.xlsx;*.csv;*.txt;*.md;*.py;*.png;*.jpg;*.jpeg;*.webp",
                ),
                ("PDF Documents (*.pdf)", "*.pdf"),
                ("Word Documents (*.docx)", "*.docx"),
                ("PowerPoint (*.pptx)", "*.pptx"),
                ("Excel & CSV (*.xlsx, *.csv)", "*.xlsx;*.csv"),
                ("Images (*.png, *.jpg, *.jpeg)", "*.png;*.jpg;*.jpeg;*.webp"),
                ("Text & Code Files (*.txt, *.py)", "*.txt;*.md;*.py"),
                ("All files (*.*)", "*.*"),
            ],
        )
        if not file_path:
            return
        file_name = Path(file_path).name
        self.add_message("You", f"[Attached: {file_name}]", is_user=True)
        self.busy = True
        self.send_btn.config(state="disabled")
        self.voice_btn.config(state="disabled")
        self.upload_btn.config(state="disabled")
        self.show_thinking(f"Analyzing {file_name} offline...")
        threading.Thread(target=self.work_document, args=(file_path,), daemon=True).start()

    def work_document(self, file_path):
        try:
            reply = agent.analyze_study_document(file_path)
        except Exception as e:
            reply = f"Error reading document: {e}"
        events.put(("reply", reply))

    def work(self, text):
        try:
            reply, _intent = agent.handle(text, self.history)
        except Exception as e:
            reply = f"Something went wrong: {e}"
        events.put(("reply", reply))

    def on_reply(self, reply):
        self.clear_thinking()
        self.add_message("Assistant", reply, is_user=False)
        self.busy = False
        self.send_btn.config(state="normal")
        self.upload_btn.config(state="normal")
        self.voice_btn.config(state="normal")
        self.entry.focus_set()

    # ---------- offline voice input ----------
    def toggle_voice(self):
        if self.busy and not self.voice_recording:
            return

        if not self.voice_recording:
            # Start Recording
            try:
                self.voice_inst.start_recording()
                self.voice_recording = True
                self.voice_btn.config(
                    text="⏹️ Stop",
                    bg="#dc2626",
                    activebackground="#b91c1c",
                )
                self.show_thinking("🎙️ Listening... Speak now (click Stop or press F4 when done)")
            except Exception as e:
                self.add_message("Assistant", f"Microphone error: {e}", is_user=False)
        else:
            # Stop Recording & Transcribe
            self.voice_recording = False
            self.voice_btn.config(
                text="⏳...",
                bg="#475569",
                state="disabled",
            )
            self.show_thinking("Transcribing your speech offline...")

            def _transcribe():
                try:
                    text = self.voice_inst.stop_and_transcribe()
                except Exception as e:
                    text = ""
                    print(f"Voice error: {e}")
                events.put(("voice_text", text))

            threading.Thread(target=_transcribe, daemon=True).start()

    def on_voice_text(self, text):
        self.clear_thinking()
        self.voice_btn.config(
            text="🎙️ Voice",
            bg="#7c3aed",
            activebackground="#6d28d9",
            state="normal",
        )
        if text:
            current = self.entry.get("1.0", "end-1c").strip()
            if current:
                self.entry.insert("end", f" {text}")
            else:
                self.entry.insert("end", text)
            self.entry.focus_set()
            self.entry.mark_set("insert", "end")
        else:
            self.show_thinking("No speech detected. Try speaking closer to the mic.")
            self.win.after(2500, self.clear_thinking)

    # ---------- show / hide ----------
    def show(self):
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        self.win.after(300, lambda: self.win.attributes("-topmost", False))
        self.win.focus_force()
        self.entry.focus_set()

    def hide(self):
        self.win.withdraw()


def main():
    db.init_db()
    # Preload Whisper voice model in background for instantaneous transcription
    voice.VoiceInput.get_instance().preload_model()

    root = tk.Tk()
    root.withdraw()
    window = ChatWindow(root)

    keyboard.GlobalHotKeys({
        HOTKEY: lambda: events.put(("show", None)),
        VOICE_HOTKEY: lambda: events.put(("voice_toggle", None)),
    }).start()

    def poll():
        while not events.empty():
            kind, payload = events.get()
            if kind == "show":
                window.show()
            elif kind == "reply":
                window.on_reply(payload)
            elif kind == "voice_text":
                window.on_voice_text(payload)
            elif kind == "voice_toggle":
                window.show()
                window.toggle_voice()
        root.after(100, poll)

    poll()
    print("Assistant is running. Shortcuts: Ctrl+Alt+K (Toggle), Ctrl+Alt+V (Voice).")
    root.mainloop()


if __name__ == "__main__":
    main()
