"""
classroom_master.py  –  Run this on the TEACHER computer.
Compile to EXE with:
    pip install pyinstaller
    pyinstaller --noconsole --onefile master.py

On first run it will offer to set a static IP of 192.168.1.100 on the
Wi-Fi adapter connected to OPSB-ARUBA so clients always find you.
"""

import multiprocessing
multiprocessing.freeze_support()

import socket
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import time

# ── Configuration ─────────────────────────────────────────────────────────────
STATIC_IP   = "10.236.10.113"   # Master's static IP  ← must match client.py
SUBNET_MASK = "255.255.255.0"
GATEWAY     = "192.168.1.1"     # Your router's IP (change if different)
DNS1        = "8.8.8.8"
DNS2        = "8.8.4.4"
WIFI_SSID   = "OPSB-ARUBA"

LISTEN_PORT = 9000
BUFFER      = 1024
# ─────────────────────────────────────────────────────────────────────────────


# ── Static-IP helper ──────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout=15) -> str:
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return str(e)


def get_wifi_adapter_name() -> str | None:
    """Return the name of the Wi-Fi adapter currently on OPSB-ARUBA, or None."""
    out = _run(["netsh", "wlan", "show", "interfaces"])
    adapter = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Name") and ":" in line:
            adapter = line.split(":", 1)[1].strip()
        if line.startswith("SSID") and not line.startswith("BSSID"):
            ssid = line.split(":", 1)[1].strip()
            if ssid == WIFI_SSID and adapter:
                return adapter
    return None


def current_ip_on_adapter(adapter: str) -> str:
    """Return the IPv4 address currently assigned to the adapter."""
    out = _run(["netsh", "interface", "ip", "show", "address", adapter])
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("IP Address"):
            return line.split(":", 1)[1].strip()
    return ""


def set_static_ip(adapter: str):
    """Apply static IP settings to the given adapter via netsh."""
    _run(["netsh", "interface", "ip", "set", "address",
          f"name={adapter}", "static",
          STATIC_IP, SUBNET_MASK, GATEWAY])
    _run(["netsh", "interface", "ip", "set", "dns",
          f"name={adapter}", "static", DNS1])
    _run(["netsh", "interface", "ip", "add", "dns",
          f"name={adapter}", DNS2, "index=2"])


def ensure_static_ip():
    """Check if master already has STATIC_IP; if not, offer to set it."""
    adapter = get_wifi_adapter_name()
    if not adapter:
        messagebox.showwarning(
            "Wi-Fi Not Connected",
            f"Not connected to '{WIFI_SSID}'.\n\n"
            "Please connect this computer to OPSB-ARUBA first, then restart.",
        )
        return

    current = current_ip_on_adapter(adapter)
    if current == STATIC_IP:
        print(f"[MASTER] Static IP already set: {STATIC_IP} ✓")
        return

    answer = messagebox.askyesno(
        "Set Static IP",
        f"Your current IP on adapter '{adapter}' is:  {current or '(unknown)'}\n\n"
        f"This computer needs a static IP of  {STATIC_IP}  so students can always find you.\n\n"
        "Set it now? (Requires administrator privileges)",
    )
    if answer:
        set_static_ip(adapter)
        messagebox.showinfo(
            "Done",
            f"Static IP {STATIC_IP} has been set on '{adapter}'.\n\n"
            "If you lose connectivity, run this app again as Administrator.",
        )


# ── Client registry ───────────────────────────────────────────────────────────

class ClientConnection:
    def __init__(self, sock: socket.socket, addr):
        self.sock    = sock
        self.addr    = addr
        self.name    = str(addr[0])
        self.locked  = False
        self.blacked = False

    def send(self, cmd: str):
        try:
            self.sock.sendall((cmd + "\n").encode())
        except OSError:
            pass

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


clients: dict[str, ClientConnection] = {}
clients_lock = threading.Lock()


# ── Server ────────────────────────────────────────────────────────────────────

def accept_loop(server_sock: socket.socket, gui_callback):
    while True:
        try:
            conn, addr = server_sock.accept()
            key = f"{addr[0]}:{addr[1]}"
            client = ClientConnection(conn, addr)
            with clients_lock:
                clients[key] = client
            print(f"[MASTER] Connected: {key}")
            t = threading.Thread(
                target=client_reader,
                args=(client, key, gui_callback),
                daemon=True,
            )
            t.start()
        except OSError:
            break


def client_reader(client: ClientConnection, key: str, gui_callback):
    buf = ""
    try:
        while True:
            data = client.sock.recv(BUFFER).decode(errors="ignore")
            if not data:
                break
            buf += data
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if line.startswith("HELLO "):
                    client.name = line[6:].strip()
                    gui_callback("refresh")
    except OSError:
        pass
    finally:
        with clients_lock:
            clients.pop(key, None)
        client.close()
        print(f"[MASTER] Disconnected: {key}")
        gui_callback("refresh")


# ── GUI ───────────────────────────────────────────────────────────────────────

class MasterApp(tk.Tk):
    # colours
    BG      = "#1e1e2e"
    PANEL   = "#2a2a3e"
    ACCENT  = "#7c3aed"
    DANGER  = "#dc2626"
    SUCCESS = "#16a34a"
    FG      = "#e2e8f0"
    SUBTLE  = "#94a3b8"
    DARK    = "#374151"

    def __init__(self):
        super().__init__()
        self.title("Classroom Control – Master")
        self.geometry("720x540")
        self.configure(bg=self.BG)
        self.resizable(True, True)
        self._build_ui()
        self._start_server()
        self._refresh_loop()
        # Check static IP after window is visible
        self.after(500, ensure_static_ip)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=self.BG, pady=12)
        hdr.pack(fill="x", padx=20)
        tk.Label(hdr, text="🖥  Classroom Control",
                 bg=self.BG, fg=self.FG,
                 font=("Segoe UI", 18, "bold")).pack(side="left")
        self.ip_lbl = tk.Label(hdr, text="", bg=self.BG, fg=self.SUBTLE,
                               font=("Segoe UI", 10))
        self.ip_lbl.pack(side="right")
        self._update_ip_label()

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=20)

        # Global controls
        ctrl = tk.Frame(self, bg=self.BG, pady=10)
        ctrl.pack(fill="x", padx=20)
        tk.Label(ctrl, text="ALL STUDENTS", bg=self.BG, fg=self.SUBTLE,
                 font=("Segoe UI", 9, "bold")).grid(
                 row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))

        bcfg = dict(font=("Segoe UI", 10, "bold"), bd=0,
                    padx=16, pady=8, cursor="hand2", relief="flat")
        tk.Button(ctrl, text="🔒  Lock Input",     bg=self.DANGER,  fg="white",
                  command=self.lock_all,    **bcfg).grid(row=1, column=0, padx=4)
        tk.Button(ctrl, text="🔓  Unlock Input",   bg=self.SUCCESS, fg="white",
                  command=self.unlock_all,  **bcfg).grid(row=1, column=1, padx=4)
        tk.Button(ctrl, text="⬛  Black Screen",   bg=self.DARK,    fg="white",
                  command=self.black_all,   **bcfg).grid(row=1, column=2, padx=4)
        tk.Button(ctrl, text="💡  Restore Screen", bg=self.ACCENT,  fg="white",
                  command=self.restore_all, **bcfg).grid(row=1, column=3, padx=4)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=20, pady=8)

        # Student list with scrollbar
        tk.Label(self, text="CONNECTED STUDENTS", bg=self.BG, fg=self.SUBTLE,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=22)

        outer = tk.Frame(self, bg=self.BG)
        outer.pack(fill="both", expand=True, padx=20, pady=(4, 16))

        self.canvas = tk.Canvas(outer, bg=self.BG, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=self.BG)
        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(
                scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _update_ip_label(self):
        self.ip_lbl.config(
            text=f"Master IP: {STATIC_IP}  |  Port: {LISTEN_PORT}  |  SSID: {WIFI_SSID}"
        )

    # ── Server ────────────────────────────────────────────────────────────────

    def _start_server(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(("", LISTEN_PORT))
        self.server_sock.listen(50)
        print(f"[MASTER] Listening on port {LISTEN_PORT}")
        threading.Thread(
            target=accept_loop,
            args=(self.server_sock, lambda e: self.after(0, self._refresh_list)),
            daemon=True,
        ).start()

    # ── Refresh ───────────────────────────────────────────────────────────────

    def _refresh_loop(self):
        self._refresh_list()
        self.after(3000, self._refresh_loop)

    def _refresh_list(self):
        for w in self.inner.winfo_children():
            w.destroy()

        with clients_lock:
            snap = list(clients.items())

        if not snap:
            tk.Label(
                self.inner,
                text=(
                    "No students connected yet.\n\n"
                    f"Students must be on Wi-Fi:  {WIFI_SSID}\n"
                    f"client.exe connects automatically to  {STATIC_IP}"
                ),
                bg=self.BG, fg=self.SUBTLE,
                font=("Segoe UI", 11), justify="center", pady=40,
            ).pack(expand=True)
            return

        for key, client in snap:
            self._build_row(key, client)

    def _build_row(self, key: str, client: ClientConnection):
        row = tk.Frame(self.inner, bg=self.PANEL, pady=8, padx=12,
                       highlightbackground="#3a3a5e", highlightthickness=1)
        row.pack(fill="x", pady=4, padx=2)

        # Info
        info = tk.Frame(row, bg=self.PANEL)
        info.pack(side="left")
        tk.Label(info, text=f"💻  {client.name}",
                 bg=self.PANEL, fg=self.FG,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w")
        tk.Label(info, text=client.addr[0],
                 bg=self.PANEL, fg=self.SUBTLE,
                 font=("Segoe UI", 9)).pack(anchor="w")

        # Status badges
        badges = tk.Frame(row, bg=self.PANEL)
        badges.pack(side="left", padx=20)
        lk_col  = self.DANGER  if client.locked  else self.SUCCESS
        sc_col  = self.DARK    if client.blacked  else self.ACCENT
        lk_txt  = "🔒 Locked"  if client.locked  else "🔓 Unlocked"
        sc_txt  = "⬛ Blacked" if client.blacked  else "💡 Screen On"
        tk.Label(badges, text=lk_txt, bg=lk_col, fg="white",
                 font=("Segoe UI", 9, "bold"), padx=6, pady=2).pack(side="left", padx=2)
        tk.Label(badges, text=sc_txt, bg=sc_col, fg="white",
                 font=("Segoe UI", 9, "bold"), padx=6, pady=2).pack(side="left", padx=2)

        # Per-client buttons
        btns = tk.Frame(row, bg=self.PANEL)
        btns.pack(side="right")
        bc = dict(font=("Segoe UI", 9, "bold"), bd=0,
                  padx=10, pady=5, cursor="hand2", relief="flat")
        tk.Button(btns, text="🔒", bg=self.DANGER,  fg="white",
                  command=lambda k=key: self._send(k, "LOCK"),         **bc).pack(side="left", padx=2)
        tk.Button(btns, text="🔓", bg=self.SUCCESS, fg="white",
                  command=lambda k=key: self._send(k, "UNLOCK"),       **bc).pack(side="left", padx=2)
        tk.Button(btns, text="⬛", bg=self.DARK,    fg="white",
                  command=lambda k=key: self._send(k, "BLACKSCREEN"),  **bc).pack(side="left", padx=2)
        tk.Button(btns, text="💡", bg=self.ACCENT,  fg="white",
                  command=lambda k=key: self._send(k, "RESTORESCREEN"),**bc).pack(side="left", padx=2)

    # ── Commands ──────────────────────────────────────────────────────────────

    def _send(self, key: str, cmd: str):
        with clients_lock:
            client = clients.get(key)
        if not client:
            return
        client.send(cmd)
        self._update_state(client, cmd)
        self._refresh_list()

    def _broadcast(self, cmd: str):
        with clients_lock:
            snap = list(clients.values())
        for c in snap:
            c.send(cmd)
            self._update_state(c, cmd)
        self._refresh_list()

    @staticmethod
    def _update_state(client: ClientConnection, cmd: str):
        if cmd == "LOCK":            client.locked  = True
        elif cmd == "UNLOCK":        client.locked  = False
        elif cmd == "BLACKSCREEN":   client.blacked = True
        elif cmd == "RESTORESCREEN": client.blacked = False

    def lock_all(self):    self._broadcast("LOCK")
    def unlock_all(self):  self._broadcast("UNLOCK")
    def black_all(self):   self._broadcast("BLACKSCREEN")
    def restore_all(self): self._broadcast("RESTORESCREEN")

    def on_close(self):
        self._broadcast("UNLOCK+RESTORESCREEN")
        self.server_sock.close()
        self.destroy()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = MasterApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()