import queue
import threading
import tkinter as tk
from tkinter import scrolledtext

from pynput import keyboard

import agent
import db

HOTKEY = "<ctrl>+<alt>+k"
events = queue.Queue()
FONT = ("Segoe UI", 10)


class ChatWindow:
    def __init__(self, root):
        self.win = tk.Toplevel(root)
        self.win.title("Study Agent")
        self.win.geometry("560x560")
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        self.history = []
        self.busy = False
        self.thinking_index = None

        self.chat = scrolledtext.ScrolledText(self.win, state="disabled", wrap="word", font=FONT)
        self.chat.pack(fill="both", expand=True, padx=10, pady=(10, 5))
        self.chat.tag_config("user_label", foreground="#1a5fb4", font=("Segoe UI", 10, "bold"))
        self.chat.tag_config("agent_label", foreground="#26a269", font=("Segoe UI", 10, "bold"))
        self.chat.tag_config("note", foreground="#777777")

        bottom = tk.Frame(self.win)
        bottom.pack(fill="x", padx=10, pady=(0, 10))
        self.entry = tk.Text(bottom, height=3, wrap="word", font=FONT)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", self.on_enter)
        self.send_btn = tk.Button(bottom, text="Send", width=8, command=self.send)
        self.send_btn.pack(side="right", padx=(8, 0), fill="y")

        self.add_message(
            "Agent",
            "Hi! Tell me your plan, ask what's planned, tell me what you finished, "
            "or ask me anything.",
            "agent_label",
        )
        self.hide()

    # ---------- chat display ----------
    def add_message(self, label, text, label_tag):
        self.chat.config(state="normal")
        self.chat.insert("end", f"{label}\n", label_tag)
        self.chat.insert("end", f"{text}\n\n")
        self.chat.see("end")
        self.chat.config(state="disabled")

    def show_thinking(self):
        self.chat.config(state="normal")
        self.thinking_index = self.chat.index("end-1c")
        self.chat.insert("end", "Thinking...\n", "note")
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
        self.add_message("You", text, "user_label")
        self.busy = True
        self.send_btn.config(state="disabled")
        self.show_thinking()
        threading.Thread(target=self.work, args=(text,), daemon=True).start()

    def work(self, text):
        try:
            reply, _intent = agent.handle(text, self.history)
        except Exception as e:
            reply = f"Something went wrong: {e}"
        events.put(("reply", reply))

    def on_reply(self, reply):
        self.clear_thinking()
        self.add_message("Agent", reply, "agent_label")
        self.busy = False
        self.send_btn.config(state="normal")
        self.entry.focus_set()

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
    root = tk.Tk()
    root.withdraw()
    window = ChatWindow(root)

    keyboard.GlobalHotKeys({HOTKEY: lambda: events.put(("show", None))}).start()

    def poll():
        while not events.empty():
            kind, payload = events.get()
            if kind == "show":
                window.show()
            elif kind == "reply":
                window.on_reply(payload)
        root.after(100, poll)

    poll()
    print("Agent is running. Shortcut: Ctrl+Alt+K. Press Ctrl+C in this terminal to stop.")
    root.mainloop()


if __name__ == "__main__":
    main()