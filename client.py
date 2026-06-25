"""
classroom_client.py  –  Run this on each STUDENT computer.
Compile to EXE with:
    pip install pyinstaller pynput
    pyinstaller --noconsole --onefile client.py

What it does automatically on startup:
  1. Creates the OPSB-ARUBA Wi-Fi profile (if not already saved)
  2. Connects to OPSB-ARUBA
  3. Connects to the master PC at MASTER_IP and awaits commands
"""

import multiprocessing  # must be first import for PyInstaller freeze support
multiprocessing.freeze_support()

import socket
import subprocess
import threading
import tkinter as tk
import time
import sys
import os
import tempfile
from pynput import keyboard

try:
    from pynput import keyboard as kb, mouse as ms
except ImportError:
    import ctypes
    ctypes.windll.user32.MessageBoxW(
        0,
        "Missing dependency.\n\nRun in Command Prompt:\n  pip install pynput",
        "Classroom Client – Error",
        0x10,
    )
    sys.exit(1)

# ── !! CONFIGURATION – edit these if needed !! ───────────────────────────────
MASTER_IP_LIST  = [
    "10.236.21.222",

]
MASTER_PORT     = 9000
WIFI_SSID       = "OPSB-ARUBA"
WIFI_PASSWORD   = "opsbaruba"
RECONNECT_DELAY = 8
# ─────────────────────────────────────────────────────────────────────────────

BUFFER = 1024

# ── Global state ──────────────────────────────────────────────────────────────
input_blocked     = False
screen_blacked    = False
enabled           = True
black_window      = None
kb_listener       = None
ms_listener       = None
root              = None
mouse_lock_thread = None


# ── Wi-Fi helper ──────────────────────────────────────────────────────────────

WIFI_PROFILE_XML = """<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{ssid}</name>
    <SSIDConfig>
        <SSID><name>{ssid}</name></SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>WPA3SAE</authentication>
                <encryption>AES</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
            <sharedKey>
                <keyType>passPhrase</keyType>
                <protected>false</protected>
                <keyMaterial>{password}</keyMaterial>
            </sharedKey>
        </security>
    </MSM>
</WLANProfile>"""


def _run(cmd: list[str], timeout=15) -> str:
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return str(e)


def ensure_wifi():
    """Add the Wi-Fi profile (if missing) and connect to OPSB-ARUBA."""
    print(f"[CLIENT] Ensuring Wi-Fi connection to '{WIFI_SSID}' ...")

    # Write profile XML to a temp file
    xml = WIFI_PROFILE_XML.format(ssid=WIFI_SSID, password=WIFI_PASSWORD)
    tmp = os.path.join(tempfile.gettempdir(), "opsb_wifi_profile.xml")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(xml)

    # Add profile (safe to run even if it already exists)
    out = _run(["netsh", "wlan", "add", "profile", f"filename={tmp}"])
    print(f"[WIFI]  add profile → {out}")

    # Connect
    out = _run(["netsh", "wlan", "connect", f"name={WIFI_SSID}"])
    print(f"[WIFI]  connect     → {out}")

    # Wait up to 20 s for an IP on that SSID
    for _ in range(20):
        cur = _run(["netsh", "wlan", "show", "interfaces"])
        if WIFI_SSID in cur and ("State" in cur):
            if "connected" in cur.lower():
                print("[WIFI]  Connected to OPSB-ARUBA ✓")
                return
        time.sleep(1)

    print("[WIFI]  Warning: could not confirm Wi-Fi connection – continuing anyway.")


# ── Input blocking ────────────────────────────────────────────────────────────

def _lock_mouse():
    controller = ms.Controller()

    while input_blocked:
        controller.position = (0, 0)
        time.sleep(0.01)

def block_input():
    global input_blocked, mouse_lock_thread

    if input_blocked:
        return

    input_blocked = True

    mouse_lock_thread = threading.Thread(
        target=_lock_mouse,
        daemon=True
    )
    mouse_lock_thread.start()

    print("[CLIENT] Input BLOCKED")

def unblock_input():
    global input_blocked, kb_listener, ms_listener
    if not input_blocked:
        return
    input_blocked = False
    if kb_listener:
        kb_listener.stop()
        kb_listener = None
    if ms_listener:
        ms_listener.stop()
        ms_listener = None
    print("[CLIENT] Input UNBLOCKED")


# ── Black screen ──────────────────────────────────────────────────────────────

def show_black_screen(text=""):
    global screen_blacked, black_window

    def _create():
        global screen_blacked, black_window

        if screen_blacked:
            return

        screen_blacked = True

        black_window = tk.Toplevel(root)
        black_window.configure(bg="black")

        # Force fullscreen and frontmost
        black_window.attributes("-fullscreen", True)
        black_window.attributes("-topmost", True)

        black_window.lift()
        black_window.focus_force()

        label = tk.Label(
            black_window,
            text=text,
            bg="black",
            fg="white",
            font=("Arial", 48, "bold")
        )
        label.place(relx=0.5, rely=0.5, anchor="center")

        black_window.update_idletasks()
        black_window.update()

        print("[CLIENT] Screen BLACKED OUT")

        if text:
            black_window.after(3000, hide_black_screen)

    # Schedule everything on the Tk thread
    root.after(0, _create)

def hide_black_screen():
    global black_window, screen_blacked

    if black_window is not None:
        black_window.destroy()
        black_window = None

    screen_blacked = False

    print("[CLIENT] Screen RESTORED")


# ── Command handler ───────────────────────────────────────────────────────────

def handle_command(cmd: str):
    cmd = cmd.strip().upper()
    print(f"[CLIENT] Command: {cmd}")
    if enabled:
        if cmd == "LOCK":
            block_input()
        elif cmd == "UNLOCK":
            unblock_input()
        elif "BLACKSCREEN" in cmd:
            if len(cmd) > 11:
                show_black_screen(cmd[11:].strip())
            else:
                show_black_screen()
        elif cmd == "RESTORESCREEN":
            hide_black_screen()
        elif cmd == "RESTART":
            appdata_path = os.environ.get("APPDATA")
            startup_path = os.path.join(appdata_path, r"Microsoft/Windows/Start Menu/Programs/Startup")
            os.startfile(startup_path+"/client.exe")
            os.exit(0)
    if "ADD_MASTER" in cmd:
        threading.Thread(
            target=connect_to_server,
            args=(cmd[11:].strip(),),
            daemon=True
        ).start()


# ── Network loop ──────────────────────────────────────────────────────────────

def connect_to_server(MASTER_IP):
    while True:
        if enabled:
            try:
                print(f"[CLIENT] Connecting to {MASTER_IP}:{MASTER_PORT} ...")
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10)
                sock.connect((MASTER_IP, MASTER_PORT))
                sock.settimeout(None)
                print("[CLIENT] Connected to master ✓")

                hostname = socket.gethostname()
                sock.sendall(f"HELLO {hostname}\n".encode())

                buf = ""
                while True:
                    data = sock.recv(BUFFER).decode(errors="ignore")
                    if not data:
                        print("[CLIENT] Master disconnected.")
                        break
                    buf += data
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        if line.strip():
                            handle_command(line.strip())

            except (ConnectionRefusedError, OSError, TimeoutError) as e:
                print(f"[CLIENT] Connection failed ({e}). Retrying in {RECONNECT_DELAY}s ...")
                # If we lost connection, re-check Wi-Fi too
                if enabled:
                    ensure_wifi()
            finally:
                try:
                    sock.close()
                except Exception:
                    pass

            time.sleep(RECONNECT_DELAY)
        else:
            time.sleep(1)

def on_activate():
    global enabled
    enabled = not enabled
    onoff = "off"
    if enabled:
        onoff = "on"
    else:
        onoff = "off"
    print("Hotkey pressed and app is " + onoff)
    show_black_screen("App is " + onoff)

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    global root

    # Step 1 – enforce Wi-Fi
    ensure_wifi()

    # Step 2 – hidden tkinter root (needed for black-screen Toplevel)
    root = tk.Tk()
    root.withdraw()

    hotkey = keyboard.HotKey(
        keyboard.HotKey.parse('<ctrl>+<shift>+<alt>+a'),
        on_activate
    )

    def for_canonical(f):
        return lambda k: f(listener.canonical(k))

    listener = keyboard.Listener(
        on_press=for_canonical(hotkey.press),
        on_release=for_canonical(hotkey.release),
    )

    for ip in MASTER_IP_LIST:
        threading.Thread(
            target=connect_to_server,
            args=(ip,),
            daemon=True
        ).start()

    root.mainloop()


if __name__ == "__main__":
    main()