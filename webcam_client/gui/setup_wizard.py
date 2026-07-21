# sdprs/webcam_client/gui/setup_wizard.py
import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional

import httpx

from ..camera_manager import scan_cameras
from .preview import make_thumbnail, grab_preview_frame

logger = logging.getLogger("webcam_client.gui.wizard")


def run_setup_wizard(existing_config: Optional[dict] = None) -> Optional[dict]:
    result = {"config": None}
    root = tk.Tk()
    root.title("SDPRS Webcam 設定")
    root.geometry("500x450")
    root.resizable(False, False)

    config = existing_config or {}
    cameras_found = []

    # --- Frame: Server connection ---
    frame_conn = ttk.LabelFrame(root, text="伺服器連線", padding=10)
    frame_conn.pack(fill="x", padx=10, pady=5)

    ttk.Label(frame_conn, text="Server URL:").grid(row=0, column=0, sticky="w")
    url_var = tk.StringVar(value=config.get("server_url", ""))
    url_entry = ttk.Entry(frame_conn, textvariable=url_var, width=40)
    url_entry.grid(row=0, column=1, padx=5)

    ttk.Label(frame_conn, text="API Key:").grid(row=1, column=0, sticky="w", pady=5)
    key_var = tk.StringVar(value=config.get("api_key", ""))
    key_entry = ttk.Entry(frame_conn, textvariable=key_var, width=40, show="*")
    key_entry.grid(row=1, column=1, padx=5, pady=5)

    status_var = tk.StringVar(value="")
    ttk.Label(frame_conn, textvariable=status_var, foreground="gray").grid(row=2, column=0, columnspan=2)

    # --- Frame: Camera selection ---
    frame_cam = ttk.LabelFrame(root, text="攝影機", padding=10)
    frame_cam.pack(fill="both", expand=True, padx=10, pady=5)

    cam_vars = []
    cam_frame_inner = ttk.Frame(frame_cam)
    cam_frame_inner.pack(fill="both", expand=True)

    def do_scan():
        status_var.set("掃描中...")
        root.update()
        cams = scan_cameras()
        cameras_found.clear()
        cameras_found.extend(cams)
        for w in cam_frame_inner.winfo_children():
            w.destroy()
        cam_vars.clear()
        if not cams:
            ttk.Label(cam_frame_inner, text="未偵測到攝影機").pack()
        for cam in cams:
            var = tk.BooleanVar(value=True)
            name_var = tk.StringVar(value=f"Webcam {cam['device_index']}")
            cam_vars.append((cam, var, name_var))
            row = ttk.Frame(cam_frame_inner)
            row.pack(fill="x", anchor="w", pady=2)
            ttk.Checkbutton(row,
                text=f"Camera {cam['device_index']} ({cam['width']}x{cam['height']})",
                variable=var).pack(side="left")
            ttk.Label(row, text="名稱:").pack(side="left", padx=(8, 2))
            ttk.Entry(row, textvariable=name_var, width=16).pack(side="left")
            # Spec §173 item 6: live preview thumbnail per camera. Best-effort — a
            # frame grab may fail (camera busy); make_thumbnail(None) yields None so
            # the wizard just omits the image and never blocks on a bad device.
            thumb = make_thumbnail(grab_preview_frame(cam["device_index"]))
            if thumb is not None:
                lbl = ttk.Label(row, image=thumb)
                lbl.image = thumb  # keep a ref so Tk doesn't GC the PhotoImage
                lbl.pack(side="right")
        status_var.set(f"找到 {len(cams)} 支攝影機")

    ttk.Button(frame_cam, text="掃描攝影機", command=do_scan).pack(anchor="e", pady=5)

    # --- Buttons ---
    frame_btn = ttk.Frame(root, padding=10)
    frame_btn.pack(fill="x")

    def on_start():
        server_url = url_var.get().strip()
        api_key = key_var.get().strip()
        if not server_url or not api_key:
            messagebox.showerror("錯誤", "請填入 Server URL 和 API Key")
            return
        selected = [{"device_index": c["device_index"],
                     "name": nv.get().strip() or f"Webcam {c['device_index']}",
                     "resolution": [640, 480], "jpeg_quality": 40, "target_fps": 8}
                    for c, v, nv in cam_vars if v.get()]
        if not selected:
            messagebox.showerror("錯誤", "請至少選擇一支攝影機")
            return
        # Validate connection
        try:
            resp = httpx.post(f"{server_url}/api/webcam/cameras",
                json={"cameras": selected},
                headers={"X-API-Key": api_key}, timeout=10.0)
            if resp.status_code == 401:
                messagebox.showerror("錯誤", "API Key 無效")
                return
            if resp.status_code != 201:
                messagebox.showerror("錯誤", f"伺服器回應: {resp.status_code}")
                return
            registered = resp.json()
            for i, cam in enumerate(selected):
                if i < len(registered):
                    cam["node_id"] = registered[i]["node_id"]
        except httpx.ConnectError:
            messagebox.showerror("錯誤", "無法連線到伺服器")
            return

        result["config"] = {
            "server_url": server_url,
            "api_key": api_key,
            "cameras": selected,
            "motion_threshold": 25,
            "heartbeat_interval": 30,
        }
        root.destroy()

    ttk.Button(frame_btn, text="開始", command=on_start).pack(side="right")
    ttk.Button(frame_btn, text="取消", command=root.destroy).pack(side="right", padx=5)

    # Auto-scan on open
    root.after(100, do_scan)
    root.mainloop()
    return result["config"]
