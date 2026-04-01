import tkinter as tk
from tkinter import ttk, messagebox
import threading
import cv2
import numpy as np
import requests
import time
import io
import re
import os
import json
import webbrowser
import ctypes
from ctypes import wintypes
from mousekey import MouseKey
import keyboard
import sys
from thefuzz import fuzz
import difflib
import mss
from PIL import Image, ImageOps
import multiprocessing
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

POOL_ENGINE = None

def _init_pool_reader():
    global POOL_ENGINE
    if POOL_ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            try:
                POOL_ENGINE = RapidOCR(
                    engine_kwargs={"providers": ["CUDAExecutionProvider", "CPUExecutionProvider"]}
                )
            except TypeError:
                POOL_ENGINE = RapidOCR()
        except Exception as e:
            print(f"Failed to load OCR: {e}")

def _pool_ocr_task(img_bgr):
    global POOL_ENGINE
    if POOL_ENGINE is None:
        _init_pool_reader()

    if POOL_ENGINE is None:
        return {"result": None, "error": "RapidOCR Engine failed to initialize."}

    try:
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        
        gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
        resized = cv2.resize(gray, (0, 0), fx=2.0, fy=2.0, interpolation=cv2.INTER_LANCZOS4)
        inverted = cv2.bitwise_not(resized)
        
        final_bgr = cv2.cvtColor(inverted, cv2.COLOR_GRAY2BGR)

        ocr_result, _ = POOL_ENGINE(final_bgr)
        return {"result": ocr_result, "error": None}
    except Exception as e:
        return {"result": None, "error": str(e)}

def compute_frame_hash(image: Image.Image, hash_size: int = 16) -> int:
    gray = image if image.mode == "L" else image.convert("L")
    small = gray.resize((hash_size, hash_size), Image.BILINEAR)
    arr = np.asarray(small, dtype=np.float32)
    avg = float(arr.mean()) if arr.size else 0.0
    bits = (arr > avg).astype(np.uint8).reshape(-1)
    packed = np.packbits(bits)
    return int.from_bytes(packed.tobytes(), byteorder="big", signed=False)

def frame_hash_diff_percent(hash_a: int, hash_b: int, hash_size: int = 16) -> float:
    bits = int(hash_size) * int(hash_size)
    dist = (int(hash_a) ^ int(hash_b)).bit_count()
    return (float(dist) / float(bits)) * 100.0

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

user32 = ctypes.WinDLL('user32', use_last_error=True)
winmm = ctypes.WinDLL('winmm')
mkey = MouseKey()

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

class KEYBDINPUT(ctypes.Structure):
    _fields_ = (("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)))

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = (("ki", KEYBDINPUT),
                    ("mi", ctypes.c_int * 7), 
                    ("hi", ctypes.c_int * 7)) 
    _anonymous_ = ("_input",)
    _fields_ = (("type", wintypes.DWORD),
                ("_input", _INPUT))

def send_scancode(scancode, is_pressed):
    flags = KEYEVENTF_SCANCODE
    if not is_pressed:
        flags |= KEYEVENTF_KEYUP
    x = INPUT(type=INPUT_KEYBOARD,
              ki=KEYBDINPUT(wVk=0, wScan=scancode, dwFlags=flags, time=0, dwExtraInfo=None))
    user32.SendInput(1, ctypes.byref(x), ctypes.sizeof(x))

CURRENT_VERSION = "v1.1.0"  
GITHUB_REPO = "ManasAarohi1/Manas-s-Egg-Detector"

COLOR_RANGES = {
    "purple": [(np.array([125, 50, 50]), np.array([155, 255, 255]))],
    "blue": [(np.array([100, 50, 50]), np.array([135, 255, 255]))],
    "green": [(np.array([40, 50, 50]), np.array([85, 255, 255]))],
    "cyan": [(np.array([85, 50, 50]), np.array([105, 255, 255]))],
    "pink": [(np.array([145, 50, 50]), np.array([170, 255, 255]))],
    "orange": [(np.array([10, 80, 80]), np.array([25, 255, 255]))], 
    "special_colors": [
        (np.array([0, 100, 100]), np.array([10, 255, 255])),  
        (np.array([160, 100, 100]), np.array([180, 255, 255])),
        (np.array([40, 50, 50]), np.array([90, 255, 255]))
    ]
}

EGG_KEYWORDS = {
    "dreaming": {"name": "Dreamer egg (Sky Festival)", "color": "purple"},
    "protocol": {"name": "Egg v2.0 (Y.O.L.K.E.G.G)", "color": "blue"},
    "cannon": {"name": "The Egg of the Sky (Eggis)", "color": "green"},
    "hunt": {"name": "Forest Egg (Eostre)", "color": "cyan"},
    "plant": {"name": "Blooming Egg (Eggore)", "color": "pink"},
    "holy": {"name": "Angelic Egg (REVIVE)", "color": "orange"},
    "spaaaaace": {"name": "Andromeda egg (Eggsistance)", "color": "blue"},
    "special": {"name": "SPECIAL_CHECK", "color": "special_colors"}
}

EGG_COOLDOWN_SECONDS = 1200
DISCORD_LINK = "https://discord.gg/oppression"
LOCAL_APP_DATA = os.getenv('LOCALAPPDATA')
CONFIG_DIR = os.path.join(LOCAL_APP_DATA, "ManasEggDetector")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

def clean_text(text):
    return re.sub(r'[^a-zA-Z0-9\s]', '', str(text)).lower()

def is_fuzzy_match(keyword, text, threshold=0.75):
    if keyword in text:
        return True
    if len(text) < len(keyword): 
        return False
    for i in range(len(text) - len(keyword) + 1):
        chunk = text[i:i+len(keyword)]
        if difflib.SequenceMatcher(None, keyword, chunk).ratio() >= threshold:
            return True
    return False

def determine_special_egg_type(image_bgr, bbox):
    xs = [p[0] for p in bbox]
    ys = [p[1] for p in bbox]
    
    x_min = max(0, int(min(xs) / 2.0))
    y_min = max(0, int(min(ys) / 2.0))
    x_max = int(max(xs) / 2.0)
    y_max = int(max(ys) / 2.0)
    
    crop = image_bgr[y_min:y_max, x_min:x_max]
    
    if crop.size == 0:
        return "Unknown Special Egg"

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    
    lower_red1 = np.array([0, 100, 100])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([160, 100, 100])
    upper_red2 = np.array([180, 255, 255])
    
    mask_red1 = cv2.inRange(hsv, lower_red1, upper_red1)
    mask_red2 = cv2.inRange(hsv, lower_red2, upper_red2)
    mask_red = cv2.bitwise_or(mask_red1, mask_red2)
    
    lower_green = np.array([40, 50, 50])
    upper_green = np.array([90, 255, 255])
    mask_green = cv2.inRange(hsv, lower_green, upper_green)
    
    red_pixels = cv2.countNonZero(mask_red)
    green_pixels = cv2.countNonZero(mask_green)

    if red_pixels > green_pixels and red_pixels > 5:
        return "Royal egg (Emperor)"
    elif green_pixels > red_pixels and green_pixels > 5:
        return "Hatch Egg (Hatchwarden)"
    else:
        return "Unknown Special Egg"
    
def check_text_color(image_bgr, bbox, expected_color_name):
    if expected_color_name not in COLOR_RANGES: 
        return True
    
    xs = [p[0] for p in bbox]
    ys = [p[1] for p in bbox]
    x_min = max(0, int(min(xs) / 2.0))
    y_min = max(0, int(min(ys) / 2.0))
    x_max = int(max(xs) / 2.0)
    y_max = int(max(ys) / 2.0)
    
    crop = image_bgr[y_min:y_max, x_min:x_max]
    
    if crop.size == 0:
        return False

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    
    total_matched_pixels = 0
    for lower, upper in COLOR_RANGES[expected_color_name]:
        mask = cv2.inRange(hsv, lower, upper)
        total_matched_pixels += cv2.countNonZero(mask)
        
    return total_matched_pixels > 5

class EggMonitorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f"Manas's Egg Detector - {CURRENT_VERSION}")
        self.root.geometry("650x980")
        self.root.configure(bg="#1e1e1e")

        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        BG_COLOR = "#1e1e1e"
        ACCENT_COLOR = "#5865F2"
        TEXT_COLOR = "#ffffff"

        self.style.configure("TFrame", background=BG_COLOR)
        self.style.configure("TLabel", background=BG_COLOR, foreground=TEXT_COLOR, font=("Segoe UI", 10))
        self.style.configure("Header.TLabel", background=BG_COLOR, foreground=TEXT_COLOR, font=("Segoe UI", 11, "bold"))
        self.style.configure("TLabelframe", background=BG_COLOR, borderwidth=1)
        self.style.configure("TLabelframe.Label", background=BG_COLOR, foreground=ACCENT_COLOR, font=("Segoe UI", 10, "bold"))
        self.style.configure("TButton", font=("Segoe UI", 10, "bold"), padding=8, background="#333333", foreground=TEXT_COLOR, borderwidth=0)
        self.style.map("TButton", background=[('active', ACCENT_COLOR), ('disabled', '#222222')], foreground=[('disabled', '#666666')])
        self.style.configure("TEntry", fieldbackground="#2b2b2b", foreground=TEXT_COLOR, borderwidth=0, insertcolor=TEXT_COLOR, padding=6)
        self.style.configure("TCheckbutton", background=BG_COLOR, foreground=TEXT_COLOR, font=("Segoe UI", 10))
        self.style.map("TCheckbutton", background=[('active', BG_COLOR)])

        self.chat_region = None
        self.collection_pos = None
        self.collection_close_pos = None
        self.chat_toggle_pos = None
        
        self.is_running = False
        self.egg_cooldowns = {}
        self.is_playing_path = False
        self.path_lock = threading.Lock()
        self.last_periodic_time = time.time()
        
        self.current_webhook_url = ""
        self.current_user_id = ""
        self.current_run_on_detect = False
        self.current_run_periodic = False

        self.ocr_pool = None
        self.last_frame_hash = None
        self.current_future = None
        self.exit_collection_pos = None
        self.setup_ui()
        self.load_config()
        
        keyboard.add_hotkey('f1', self.start_scanner_hotkey)
        keyboard.add_hotkey('f2', self.stop_scanner_hotkey)
        
        threading.Thread(target=self.check_for_updates, daemon=True).start()
        threading.Thread(target=self.periodic_path_loop, daemon=True).start()

    def check_for_updates(self):
        self.log_message("Checking GitHub for updates...")
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            response = requests.get(url, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                latest_version = data.get("tag_name", "")
                release_url = data.get("html_url", "")

                if latest_version and latest_version != CURRENT_VERSION:
                    self.log_message(f"Update found! Version {latest_version} is available.")
                    def show_prompt():
                        msg = f"A new version ({latest_version}) is available!\nYou are currently running {CURRENT_VERSION}.\n\nWould you like to download it now?"
                        if messagebox.askyesno("Update Available", msg):
                            webbrowser.open(release_url)
                    self.root.after(0, show_prompt)
                else:
                    self.log_message("You are running the latest version.")
            else:
                self.log_message(f"Update check skipped. GitHub returned code: {response.status_code}")
        except Exception as e:
            self.log_message(f"Could not check for updates: {e}")

    def setup_ui(self):
        main_container = ttk.Frame(self.root, padding="25 25 25 25")
        main_container.pack(fill=tk.BOTH, expand=True)

        discord_frame = ttk.LabelFrame(main_container, text=" Discord Configuration ", padding="15 15 15 15")
        discord_frame.pack(fill=tk.X, pady=(0, 15))

        ttk.Label(discord_frame, text="Webhook URL:").pack(anchor=tk.W, pady=(0, 5))
        self.webhook_entry = ttk.Entry(discord_frame, width=60)
        self.webhook_entry.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(discord_frame, text="User ID to Ping (Optional):").pack(anchor=tk.W, pady=(0, 5))
        self.userid_entry = ttk.Entry(discord_frame, width=60)
        self.userid_entry.pack(fill=tk.X)

        path_frame = ttk.LabelFrame(main_container, text=" Pathing Automation ", padding="15 15 15 15")
        path_frame.pack(fill=tk.X, pady=(0, 15))
        
        self.run_on_detect_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(path_frame, text="Run paths on Egg Detection", variable=self.run_on_detect_var).pack(anchor=tk.W, pady=(0, 10))
        
        periodic_frame = ttk.Frame(path_frame)
        periodic_frame.pack(fill=tk.X)
        
        self.run_periodic_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(periodic_frame, text="Run paths every", variable=self.run_periodic_var).pack(side=tk.LEFT)
        
        self.periodic_minutes_entry = ttk.Entry(periodic_frame, width=6, justify="center")
        self.periodic_minutes_entry.insert(0, "20")
        self.periodic_minutes_entry.pack(side=tk.LEFT, padx=10)
        ttk.Label(periodic_frame, text="minutes").pack(side=tk.LEFT)

        calib_frame = ttk.LabelFrame(main_container, text=" Calibration ", padding="15 15 15 15")
        calib_frame.pack(fill=tk.X, pady=(0, 15))

        self.btn_open_calib = ttk.Button(calib_frame, text=" Open Calibration Menu", command=self.open_calibration_menu)
        self.btn_open_calib.pack(fill=tk.X)

        log_frame = ttk.Frame(main_container)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
        
        ttk.Label(log_frame, text="Activity Logs", style="Header.TLabel").pack(anchor=tk.W, pady=(0, 5))
        self.log_text = tk.Text(log_frame, height=8, bg="#151515", fg="#4cd137", font=("Consolas", 9), state=tk.DISABLED, relief="flat", borderwidth=1, highlightthickness=1, highlightbackground="#333333")
        self.log_text.pack(fill=tk.BOTH, expand=True)

        control_frame = ttk.Frame(main_container)
        control_frame.pack(fill=tk.X)

        self.btn_start = ttk.Button(control_frame, text=" Start Macro (F1)", command=self.start_scanner)
        self.btn_start.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        self.btn_stop = ttk.Button(control_frame, text=" Stop Macro (F2)", command=self.stop_scanner, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))

        self.discord_link_label = tk.Label(
            main_container, text="Join Manas Biome Hunt", fg="#5865F2", bg="#1e1e1e", cursor="hand2", font=("Segoe UI", 10, "bold", "underline")
        )
        self.discord_link_label.pack(pady=(15, 0))
        self.discord_link_label.bind("<Button-1>", lambda e: webbrowser.open(DISCORD_LINK))
        
        self.credits_label = tk.Label(main_container, text="Credits to @eagleashu for pathing", fg="#888888", bg="#1e1e1e", font=("Segoe UI", 9, "italic"))
        self.credits_label.pack(pady=(2, 0))
        
    def log_message(self, message):
        def append():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {message}\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        self.root.after(0, append)

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                if "webhook_url" in data:
                    self.webhook_entry.insert(0, data["webhook_url"])
                if "user_id" in data:
                    self.userid_entry.insert(0, data["user_id"])
                if "run_on_detect" in data:
                    self.run_on_detect_var.set(data["run_on_detect"])
                if "run_periodic" in data:
                    self.run_periodic_var.set(data["run_periodic"])
                if "periodic_minutes" in data:
                    self.periodic_minutes_entry.delete(0, tk.END)
                    self.periodic_minutes_entry.insert(0, str(data["periodic_minutes"]))
                if "chat_region" in data and data["chat_region"]:
                    self.chat_region = tuple(data["chat_region"])
                if "collection_pos" in data and data["collection_pos"]:
                    self.collection_pos = tuple(data["collection_pos"])
                if "collection_close_pos" in data and data["collection_close_pos"]:
                    self.collection_close_pos = tuple(data["collection_close_pos"])
                if "chat_toggle_pos" in data and data["chat_toggle_pos"]:
                    self.chat_toggle_pos = tuple(data["chat_toggle_pos"])
                if "exit_collection_pos" in data and data["exit_collection_pos"]:
                    self.exit_collection_pos = tuple(data["exit_collection_pos"])
            except Exception as e:
                self.log_message(f"Failed to load config: {e}")

    def save_config(self):
        if not os.path.exists(CONFIG_DIR):
            os.makedirs(CONFIG_DIR)
        try:
            periodic_mins = float(self.periodic_minutes_entry.get().strip())
        except ValueError:
            periodic_mins = 25.0
            
        config_data = {
            "webhook_url": self.webhook_entry.get().strip(),
            "user_id": self.userid_entry.get().strip(),
            "chat_region": self.chat_region,
            "collection_pos": self.collection_pos,
            "collection_close_pos": self.collection_close_pos,
            "chat_toggle_pos": self.chat_toggle_pos,
            "run_on_detect": self.run_on_detect_var.get(),
            "run_periodic": self.run_periodic_var.get(),
            "periodic_minutes": periodic_mins,
            "exit_collection_pos": self.exit_collection_pos
        }
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config_data, f)
        except Exception as e:
            self.log_message(f"Failed to save config: {e}")

    def open_calibration_menu(self):
        if hasattr(self, 'calib_menu') and self.calib_menu.winfo_exists():
            self.calib_menu.lift()
            return

        self.calib_menu = tk.Toplevel(self.root)
        self.calib_menu.title("Calibration Menu")
        self.calib_menu.geometry("400x600")
        self.calib_menu.configure(bg="#1e1e1e")
        self.calib_menu.resizable(False, False)

        ttk.Label(self.calib_menu, text="Current Calibration Status:", font=("Segoe UI", 11, "bold")).pack(pady=(15, 5))

        self.status_chat_reg = ttk.Label(self.calib_menu, font=("Segoe UI", 10))
        self.status_chat_reg.pack(anchor=tk.W, padx=25)
        self.status_chat_tog = ttk.Label(self.calib_menu, font=("Segoe UI", 10))
        self.status_chat_tog.pack(anchor=tk.W, padx=25)
        self.status_coll_open = ttk.Label(self.calib_menu, font=("Segoe UI", 10))
        self.status_coll_open.pack(anchor=tk.W, padx=25)
        self.status_coll_close = ttk.Label(self.calib_menu, font=("Segoe UI", 10))
        self.status_coll_close.pack(anchor=tk.W, padx=25)
        self.status_exit_failsafe = ttk.Label(self.calib_menu, font=("Segoe UI", 10))
        self.status_exit_failsafe.pack(anchor=tk.W, padx=25)
        
        self.update_calibration_statuses()

        ttk.Label(self.calib_menu, text="Select a feature to calibrate:", font=("Segoe UI", 10, "bold")).pack(pady=(20, 5))

        ttk.Button(self.calib_menu, text="Calibrate Chat Region", command=self.start_calibration).pack(fill=tk.X, pady=5, padx=20)
        ttk.Button(self.calib_menu, text="Calibrate Chat Toggle Icon", command=self.start_chat_toggle_calibration).pack(fill=tk.X, pady=5, padx=20)
        ttk.Button(self.calib_menu, text="Calibrate Collection OPEN Btn", command=self.start_collection_calibration).pack(fill=tk.X, pady=5, padx=20)
        ttk.Button(self.calib_menu, text="Calibrate Collection CLOSE Btn", command=self.start_collection_close_calibration).pack(fill=tk.X, pady=5, padx=20)
        ttk.Button(self.calib_menu, text="Calibrate Exit Questboard Btn", command=self.start_exit_failsafe_calibration).pack(fill=tk.X, pady=5, padx=20)
    
    def update_calibration_statuses(self):
        if not hasattr(self, 'calib_menu') or not self.calib_menu.winfo_exists():
            return
        def format_status(pos_data): return "✅ Calibrated" if pos_data else "❌ Not Set"
        def get_color(pos_data): return "#4cd137" if pos_data else "#ff6b6b"

        self.status_chat_reg.config(text=f"Chat Region: {format_status(self.chat_region)}", foreground=get_color(self.chat_region))
        self.status_chat_tog.config(text=f"Chat Toggle: {format_status(self.chat_toggle_pos)}", foreground=get_color(self.chat_toggle_pos))
        self.status_coll_open.config(text=f"Collection Open: {format_status(self.collection_pos)}", foreground=get_color(self.collection_pos))
        self.status_coll_close.config(text=f"Collection Close: {format_status(self.collection_close_pos)}", foreground=get_color(self.collection_close_pos))
        self.status_exit_failsafe.config(text=f"Exit Questboard: {format_status(self.exit_collection_pos)}", foreground=get_color(self.exit_collection_pos))
        
    def start_calibration(self):
        self.calib_window = tk.Toplevel(self.root)
        self.calib_window.attributes('-alpha', 0.4)
        self.calib_window.attributes('-fullscreen', True)
        self.calib_window.configure(cursor="cross")
        self.canvas = tk.Canvas(self.calib_window, bg='black', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.rect = None
        self.start_x = None
        self.start_y = None
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.calib_window.bind("<Escape>", lambda e: self.calib_window.destroy())
        
    def start_exit_failsafe_calibration(self):
        self.calib_window = tk.Toplevel(self.root)
        self.calib_window.attributes('-alpha', 0.4)
        self.calib_window.attributes('-fullscreen', True)
        self.calib_window.configure(cursor="cross")
        self.calib_window.bind("<ButtonPress-1>", lambda e: self._handle_click_calib(e, 'exit_collection_pos'))
        self.calib_window.bind("<Escape>", lambda e: self.calib_window.destroy())
        
    def on_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        self.rect = self.canvas.create_rectangle(self.start_x, self.start_y, self.start_x, self.start_y, outline='red', width=3)

    def on_drag(self, event):
        self.canvas.coords(self.rect, self.start_x, self.start_y, event.x, event.y)

    def on_release(self, event):
        x = min(self.start_x, event.x)
        y = min(self.start_y, event.y)
        w = abs(self.start_x - event.x)
        h = abs(self.start_y - event.y)

        self.chat_region = (x, y, w, h)
        self.log_message(f"Region calibrated: {self.chat_region}")
        self.save_config()
        self.update_calibration_statuses()
        self.calib_window.destroy()
        
    def start_chat_toggle_calibration(self):
        self.calib_window = tk.Toplevel(self.root)
        self.calib_window.attributes('-alpha', 0.4)
        self.calib_window.attributes('-fullscreen', True)
        self.calib_window.configure(cursor="cross")
        self.calib_window.bind("<ButtonPress-1>", lambda e: self._handle_click_calib(e, 'chat_toggle_pos'))
        self.calib_window.bind("<Escape>", lambda e: self.calib_window.destroy())

    def start_collection_calibration(self):
        self.calib_window = tk.Toplevel(self.root)
        self.calib_window.attributes('-alpha', 0.4)
        self.calib_window.attributes('-fullscreen', True)
        self.calib_window.configure(cursor="cross")
        self.calib_window.bind("<ButtonPress-1>", lambda e: self._handle_click_calib(e, 'collection_pos'))
        self.calib_window.bind("<Escape>", lambda e: self.calib_window.destroy())

    def start_collection_close_calibration(self):
        self.calib_window = tk.Toplevel(self.root)
        self.calib_window.attributes('-alpha', 0.4)
        self.calib_window.attributes('-fullscreen', True)
        self.calib_window.configure(cursor="cross")
        self.calib_window.bind("<ButtonPress-1>", lambda e: self._handle_click_calib(e, 'collection_close_pos'))
        self.calib_window.bind("<Escape>", lambda e: self.calib_window.destroy())
        
    def _handle_click_calib(self, event, target_attr):
        setattr(self, target_attr, (event.x_root, event.y_root))
        self.save_config()
        self.update_calibration_statuses()
        self.calib_window.destroy()

    def get_scan_code(self, key_str):
        key_str = key_str.replace("'", "").lower()
        if key_str.startswith("key."): key_str = key_str.replace("key.", "")
        mapping = {
            'w': 0x11, 'a': 0x1E, 's': 0x1F, 'd': 0x20, 'space': 0x39, 'shift': 0x2A, 'shift_l': 0x2A, 'shift_r': 0x36,
            'ctrl': 0x1D, 'ctrl_l': 0x1D, 'ctrl_r': 0x1D, 'enter': 0x1C, 'esc': 0x01, 'tab': 0x0F,
            'q': 0x10, 'e': 0x12, 'r': 0x13, 'f': 0x21, 'z': 0x2C, 'x': 0x2D, 'c': 0x2E, 'v': 0x2F,
            '1': 0x02, '2': 0x03, '3': 0x04, '4': 0x05, '5': 0x06
        }
        return mapping.get(key_str, None)

    def reset_all_keys(self):
        for reset_key in ['w', 'a', 's', 'd', 'space', 'shift_l', 'ctrl_l']:
            code = self.get_scan_code(reset_key)
            if code: send_scancode(code, False)

    def smooth_mouse_move(self, target_x, target_y, duration=0.4):
        class POINT(ctypes.Structure): _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        start_x, start_y = pt.x, pt.y
        steps = max(1, int(duration * 60))
        sleep_time = duration / steps
        for i in range(1, steps + 1):
            t = i / steps
            ease_t = 1 - pow(1 - t, 3) 
            mkey.move_to(int(start_x + (target_x - start_x) * ease_t), int(start_y + (target_y - start_y) * ease_t))
            time.sleep(sleep_time)

    def click_mouse(self, x, y, delay=0.1):
        self.smooth_mouse_move(x, y, duration=0.4)
        time.sleep(0.1)
        ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0) 
        time.sleep(0.05) 
        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0) 
        time.sleep(delay)

    def exit_collection_failsafe(self):
        if self.exit_collection_pos:
            self.click_mouse(*self.exit_collection_pos, delay=0.2)
        
    def reset_character(self):
        for key in ['esc', 'r', 'enter']:
            code = self.get_scan_code(key)
            send_scancode(code, True); time.sleep(0.05); send_scancode(code, False)
            time.sleep(0.3)
        time.sleep(1.5)
        
    def snap_camera_up(self):
        try:
            screen_width = ctypes.windll.user32.GetSystemMetrics(0)
            screen_height = ctypes.windll.user32.GetSystemMetrics(1)
            center_x = int(screen_width / 2)
            center_y = int(screen_height / 2)
            
            self.smooth_mouse_move(center_x, center_y, duration=0.2)
            time.sleep(0.1)
            
            ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0) 
            time.sleep(0.05)
            ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.15)
            
            ctypes.windll.user32.mouse_event(0x0008, 0, 0, 0, 0) 
            time.sleep(0.05)
            ctypes.windll.user32.mouse_event(0x0001, 0, 1500, 0, 0) 
            time.sleep(0.05)
            ctypes.windll.user32.mouse_event(0x0010, 0, 0, 0, 0) 
            time.sleep(0.2)
            
        except Exception as e:
            self.log_message(f"Camera snap error: {e}")
        
    def execute_camera_alignment(self):
        
        send_scancode(0x35, True); time.sleep(0.05); send_scancode(0x35, False)
        time.sleep(0.4) 
        send_scancode(0x1C, True); time.sleep(0.05); send_scancode(0x1C, False)
        time.sleep(0.6)

        if self.chat_toggle_pos: 
            self.click_mouse(*self.chat_toggle_pos, delay=1.0)

        if self.collection_pos:
            self.click_mouse(*self.collection_pos, delay=1.5)
            
        if self.collection_close_pos:
            self.click_mouse(*self.collection_close_pos, delay=1.0)
        else:
            self.log_message("Warning: Collection Close button not calibrated!")

        if self.chat_toggle_pos: 
            self.click_mouse(*self.chat_toggle_pos, delay=1.0)

        send_scancode(0x35, True); time.sleep(0.05); send_scancode(0x35, False)
        time.sleep(0.3) 
        send_scancode(0x1C, True); time.sleep(0.05); send_scancode(0x1C, False)
        time.sleep(0.3)
        
        self.snap_camera_up()
    

    def play_path_sequence(self):
        if not self.path_lock.acquire(blocking=False):
            return  
            
        try:
            self.is_playing_path = True
            path_files = ["path1.json", "path2.json", "path3.json", "path4.json", "path5.json"]
            
            if not self.is_running: return
                
            winmm.timeBeginPeriod(1)
            
            try:
                for path_file in path_files:
                    if not self.is_running: break 
                    actual_path = resource_path(path_file)
                    if not os.path.exists(actual_path): continue
                    
                    try:
                        with open(actual_path, 'r') as f: data = json.load(f)
                        if not data: continue
                        
                        self.exit_collection_failsafe()
                        self.reset_character()
                        self.execute_camera_alignment()
                        
                        self.reset_all_keys()
                        time.sleep(0.3)

                        playback_start_time = time.perf_counter()
                        
                        for event in data:
                            if not self.is_running: break 
                            
                            if event.get('type') == 'k':
                                target_time = event['t']
                                key_str = event['k']
                                is_pressed = event['p']
                                
                                time_to_wait = target_time - (time.perf_counter() - playback_start_time)
                                if time_to_wait > 0.005: 
                                    time.sleep(time_to_wait - 0.003)
                                    
                                while time.perf_counter() - playback_start_time < target_time: 
                                    pass 
                                
                                scancode = self.get_scan_code(key_str)
                                if scancode: send_scancode(scancode, is_pressed)
                                
                    except Exception as e:
                        self.log_message(f"Playback Error: {e}")
                    finally:
                        self.reset_all_keys()
                        time.sleep(1.0)
            finally:
                winmm.timeEndPeriod(1)
        finally:
            self.is_playing_path = False
            self.last_periodic_time = time.time()
            self.path_lock.release()
        
    def periodic_path_loop(self):
        while True:
            time.sleep(1)
            if self.is_running and self.current_run_periodic and not self.is_playing_path:
                try:
                    mins = float(self.periodic_minutes_entry.get().strip())
                    if time.time() - self.last_periodic_time > (mins * 60):
                        self.play_path_sequence()
                except ValueError: pass
                except Exception: pass

    def start_scanner_hotkey(self):
        if not self.is_running: self.root.after(0, self.start_scanner)

    def stop_scanner_hotkey(self):
        if self.is_running: self.root.after(0, self.stop_scanner)

    def start_scanner(self):
        if not self.chat_region:
            self.log_message("Error: Chat Region missing. Please calibrate.")
            return

        self.save_config()
        self.is_running = True
        self.last_frame_hash = None 
        self.last_periodic_time = time.time()
        
        self.current_webhook_url = self.webhook_entry.get().strip()
        self.current_user_id = self.userid_entry.get().strip()
        self.current_run_on_detect = self.run_on_detect_var.get()
        self.current_run_periodic = self.run_periodic_var.get()
        
        if not self.current_webhook_url:
            self.log_message("Warning: No Webhook provided. Discord alerts disabled.")

        self.ocr_pool = ThreadPoolExecutor(max_workers=1)

        self.btn_start.config(state=tk.DISABLED)
        self.btn_open_calib.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.send_status_webhook("started")
                
        if self.current_run_periodic:
            threading.Thread(target=self.play_path_sequence, daemon=True).start()
        
        threading.Thread(target=self.scan_loop, daemon=True).start()

    def stop_scanner(self):
        self.is_running = False
        if self.ocr_pool:
            self.ocr_pool.shutdown(wait=False, cancel_futures=True)
            self.ocr_pool = None
            
        self.current_future = None

        self.btn_start.config(state=tk.NORMAL)
        self.btn_open_calib.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.send_status_webhook("stopped")
        
    def scan_loop(self):
        while self.is_running:
            start_time = time.time()
            
            if not self.is_playing_path:
                self.process_screen()
                
            elapsed = time.time() - start_time
            sleep_time = max(0, 0.5 - elapsed)
            time.sleep(sleep_time)

    def process_screen(self):
            try:
                with mss.mss() as sct:
                    monitor = {
                        "top": self.chat_region[1], 
                        "left": self.chat_region[0], 
                        "width": self.chat_region[2], 
                        "height": self.chat_region[3]
                    }
                    screenshot = sct.grab(monitor)
                    
                img_np = np.array(screenshot)
                img_bgr = cv2.cvtColor(img_np, cv2.COLOR_BGRA2BGR)
                
                gray_for_hash = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
                pil_img_for_hash = Image.fromarray(gray_for_hash)
                
                current_hash = compute_frame_hash(pil_img_for_hash)
                if self.last_frame_hash is not None:
                    diff_pct = frame_hash_diff_percent(self.last_frame_hash, current_hash)
                    if diff_pct < 2.0:
                        return 
                
                self.last_frame_hash = current_hash

                if self.current_future and not self.current_future.done():
                    return
                    
                if not self.is_running or self.ocr_pool is None:
                    return
                    
                self.current_future = self.ocr_pool.submit(_pool_ocr_task, img_bgr)
                threading.Thread(target=self.handle_ocr_result, args=(self.current_future, img_bgr), daemon=True).start()

            except Exception as e:
                self.log_message(f"Screen capture error: {e}")

    def handle_ocr_result(self, future, img_bgr):
        try:
            result_dict = future.result(timeout=20)
            if result_dict.get("error"):
                self.log_message(f"OCR Error: {result_dict['error']}")
                return
                
            results = result_dict.get("result")
            if not results: return

            full_chat_text = " ".join([res[1].lower() for res in results])
            
            is_spawn_msg = False
            for trigger in ["egg spawned", "spawned]:"]:
                if is_fuzzy_match(trigger, full_chat_text, threshold=0.80):
                    is_spawn_msg = True
                    break

            if not is_spawn_msg: return 

            detected_egg = None
            for (bbox, text, prob) in results:
                cleaned_line = clean_text(text)
                for keyword, egg_data in EGG_KEYWORDS.items():
                    if is_fuzzy_match(clean_text(keyword), cleaned_line, threshold=0.85):
                        
                        expected_color = egg_data["color"]
                        if not check_text_color(img_bgr, bbox, expected_color):
                            continue 

                        if keyword == "special":
                            detected_egg = determine_special_egg_type(img_bgr, bbox)
                        else:
                            detected_egg = egg_data["name"]
                        break
                if detected_egg: break

            if detected_egg:
                current_time = time.time()
                last_seen_time = self.egg_cooldowns.get(detected_egg, 0)
                
                if (current_time - last_seen_time) > EGG_COOLDOWN_SECONDS:
                    self.log_message(f"Confirmed Detection: {detected_egg}")
                    self.egg_cooldowns[detected_egg] = current_time
                    threading.Thread(target=self.finish_detection, args=(detected_egg,), daemon=True).start()

        except Exception as e:
            error_name = type(e).__name__
            if error_name == 'TimeoutError':
                self.log_message("Timeout, Running OCR in next cycle.")
            else:
                self.log_message(f"OCR error: [{error_name}] {e}")
                
    def finish_detection(self, egg_name):
        try:
            self.exit_collection_failsafe()
            
            with mss.mss() as sct:
                monitor = sct.monitors[1] 
                full_screenshot = sct.grab(monitor)
                
            full_img_np = np.array(full_screenshot)
            full_img_bgr = cv2.cvtColor(full_img_np, cv2.COLOR_BGRA2BGR)
            
            self.send_discord_webhook(egg_name, full_img_bgr)

            if self.current_run_on_detect and not self.is_playing_path:
                self.log_message(f"Triggering pathing sequence for {egg_name}...")
                self.play_path_sequence()
                
        except Exception as e:
            self.log_message(f"Detection error: {e}")
            
    def send_discord_webhook(self, egg_name, image_bgr):
        webhook_url = self.current_webhook_url
        if not webhook_url: 
            return 

        user_id = self.current_user_id
        is_success, buffer = cv2.imencode(".png", image_bgr)
        
        with io.BytesIO(buffer) as io_buf:
            payload = {
                "embeds": [{
                    "title": "Egg Spawned!",
                    "url": DISCORD_LINK,
                    "description": f"**{egg_name}** has appeared!\n\n[Join Manas Biome Hunt!]({DISCORD_LINK})",
                    "color": 16766720,
                    "image": {"url": "attachment://chat_capture.png"},
                    "footer": {"text": "Manas's Egg Detector"},
                    "timestamp": time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
                }]
            }
            if user_id: payload["content"] = f"<@{user_id}>"
            try: requests.post(webhook_url, data={"payload_json": json.dumps(payload)}, files={"file": ("chat_capture.png", io_buf, "image/png")})
            except Exception as e: self.log_message(f"Webhook error: {e}")
            
    def send_status_webhook(self, status):
        webhook_url = self.current_webhook_url
        if not webhook_url: return

        embed_title = "🟢 Macro Started" if status == "started" else "🔴 Macro Stopped"
        embed_color = 5763719 if status == "started" else 15548997 

        payload = {"embeds": [{
            "title": embed_title, "url": DISCORD_LINK, "description": f"[Join Manas Biome Hunt!]({DISCORD_LINK})",
            "color": embed_color, "footer": {"text": "Manas's Egg Detector"},
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
        }]}
        threading.Thread(target=lambda: requests.post(webhook_url, json=payload), daemon=True).start()

if __name__ == "__main__":
    multiprocessing.freeze_support()
    root = tk.Tk()
    app = EggMonitorGUI(root)
    root.mainloop()
