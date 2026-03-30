import tkinter as tk
from tkinter import ttk, messagebox
import threading
import easyocr
import cv2
import numpy as np
import pyautogui
import requests
import time
import io
import re
import os
import json
import webbrowser


EGG_KEYWORDS = {
    "dreaming": "Dreamer egg (Sky Festival)",
    "protocol": "Egg v2.0 (Y.O.L.K.E.G.G)",
    "friend": "Egg v2.0 (Y.O.L.K.E.G.G)",
    "cannon": "The Egg of the Sky (Eggis)",
    "hunt": "Forest Egg (Eostre)",
    "water": "Blooming Egg (Eggore)",
    "holy": "Angelic Egg (REVIVE)",
    "right": "Andromeda egg (Eggsistance)",
    "special": "Hatch Egg (Hatchwarden)"
}

EGG_COOLDOWN_SECONDS = 1200
DISCORD_LINK = "https://discord.gg/oppression"

LOCAL_APP_DATA = os.getenv('LOCALAPPDATA')
CONFIG_DIR = os.path.join(LOCAL_APP_DATA, "ManasEggDetector")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

def clean_text(text):
    return re.sub(r'[^a-zA-Z0-9\s]', '', text).lower()

def preprocess_image_for_ocr(img_bgr):
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)

    lower_white = np.array([0, 0, 180])
    upper_white = np.array([180, 50, 255])
    lower_colors = np.array([0, 50, 150])
    upper_colors = np.array([180, 255, 255])
    lower_dark_blue = np.array([100, 40, 40])
    upper_dark_blue = np.array([140, 255, 255])
    mask_white = cv2.inRange(hsv, lower_white, upper_white)
    mask_colors = cv2.inRange(hsv, lower_colors, upper_colors)
    mask_dark_blue = cv2.inRange(hsv, lower_dark_blue, upper_dark_blue)
    combined_mask = cv2.bitwise_or(mask_white, mask_colors)
    combined_mask = cv2.bitwise_or(combined_mask, mask_dark_blue)
    isolated_text = cv2.bitwise_and(img_bgr, img_bgr, mask=combined_mask)
    gray = cv2.cvtColor(isolated_text, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 20, 255, cv2.THRESH_BINARY)
    scaled = cv2.resize(thresh, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    return scaled

def determine_special_egg_type(image, bbox):
    (tl, tr, br, bl) = bbox
    x_min = max(0, int(min(tl[0], bl[0])))
    y_min = max(0, int(min(tl[1], tr[1])))
    x_max = int(max(tr[0], br[0]))
    y_max = int(max(bl[1], br[1]))
    crop = image[y_min:y_max, x_min:x_max]
    if crop.size == 0:
        return "Unknown Special Egg"

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    lower_red1 = np.array([0, 100, 100])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([160, 100, 100])
    upper_red2 = np.array([180, 255, 255])
    mask_red1 = cv2.inRange(hsv, lower_red1,     upper_red1)
    mask_red2 = cv2.inRange(hsv, lower_red2, upper_red2)
    mask_red = mask_red1 + mask_red2
    lower_green = np.array([40, 50, 50])
    upper_green = np.array([90, 255, 255])
    mask_green = cv2.inRange(hsv, lower_green, upper_green)
    red_pixels = cv2.countNonZero(mask_red)
    green_pixels = cv2.countNonZero(mask_green)

    if red_pixels > green_pixels:
        return "Royal egg (Emperor)"
    else:
        return "Hatch Egg (Hatchwarden)"

class EggMonitorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Manas's Egg Detector")
        self.root.geometry("610x665")
        self.root.configure(bg="#1e1e1e")

        self.style = ttk.Style()
        self.style.theme_use('clam')
        self.style.configure("TLabel", background="#1e1e1e", foreground="#ffffff", font=("Segoe UI", 10))
        self.style.configure("TButton", font=("Segoe UI", 10, "bold"), padding=5)
        self.style.configure("TEntry", fieldbackground="#2d2d2d", foreground="#ffffff")
        self.style.configure("TFrame", background="#1e1e1e")

        self.chat_region = None
        self.is_running = False
        self.reader = None
        self.egg_cooldowns = {}

        self.setup_ui()
        self.load_config()
        
        self.log_message("Initializing EasyOCR... Please wait.")
        threading.Thread(target=self.init_ocr, daemon=True).start()

    def init_ocr(self):
        self.reader = easyocr.Reader(['en'])
        self.log_message("EasyOCR Initialized successfully.")

    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text="Discord Webhook URL:").pack(anchor=tk.W, pady=(0, 5))
        self.webhook_entry = ttk.Entry(main_frame, width=60)
        self.webhook_entry.pack(fill=tk.X, pady=(0, 15))

        ttk.Label(main_frame, text="Discord User ID to Ping (Optional):").pack(anchor=tk.W, pady=(0, 5))
        self.userid_entry = ttk.Entry(main_frame, width=60)
        self.userid_entry.pack(fill=tk.X, pady=(0, 15))

        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=(0, 15))

        self.region_label = ttk.Label(status_frame, text="Chat Region: Not Calibrated", foreground="#ff6b6b")
        self.region_label.pack(side=tk.LEFT)

        self.btn_calibrate = ttk.Button(status_frame, text="Calibrate Region", command=self.start_calibration)
        self.btn_calibrate.pack(side=tk.RIGHT)

        ttk.Label(main_frame, text="Logs:").pack(anchor=tk.W, pady=(0, 5))
        self.log_text = tk.Text(main_frame, height=10, bg="#2d2d2d", fg="#00ff00", font=("Consolas", 9), state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(0, 15))

        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X)

        self.btn_start = ttk.Button(control_frame, text="Start Macro", command=self.start_scanner)
        self.btn_start.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        self.btn_stop = ttk.Button(control_frame, text="Stop Macro", command=self.stop_scanner, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))

        self.discord_link_label = tk.Label(
            main_frame, 
            text="Join Manas Biome Hunt", 
            fg="#5865F2",
            bg="#1e1e1e",
            cursor="hand2",
            font=("Segoe UI", 9, "underline")
        )
        self.discord_link_label.pack(pady=(15, 0))
        self.discord_link_label.bind("<Button-1>", lambda e: self.join_discord())

    def join_discord(self):
        webbrowser.open(DISCORD_LINK)

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
                if "chat_region" in data and data["chat_region"]:
                    self.chat_region = tuple(data["chat_region"])
                    x, y, w, h = self.chat_region
                    self.region_label.config(text=f"Chat Region: {w}x{h} at ({x},{y})", foreground="#4cd137")
                    self.log_message("Loaded saved configuration.")
            except Exception as e:
                self.log_message(f"Failed to load config: {e}")

    def save_config(self):
        if not os.path.exists(CONFIG_DIR):
            os.makedirs(CONFIG_DIR)
            
        config_data = {
            "webhook_url": self.webhook_entry.get().strip(),
            "user_id": self.userid_entry.get().strip(),
            "chat_region": self.chat_region
        }
        
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config_data, f)
            self.log_message("Configuration saved.")
        except Exception as e:
            self.log_message(f"Failed to save config: {e}")

    def start_calibration(self):
        self.log_message("Draw a box around the chat.")
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

    def on_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        self.rect = self.canvas.create_rectangle(self.start_x, self.start_y, self.start_x, self.start_y, outline='red', width=3)

    def on_drag(self, event):
        cur_x, cur_y = (event.x, event.y)
        self.canvas.coords(self.rect, self.start_x, self.start_y, cur_x, cur_y)

    def on_release(self, event):
        end_x, end_y = (event.x, event.y)
        x = min(self.start_x, end_x)
        y = min(self.start_y, end_y)
        w = abs(self.start_x - end_x)
        h = abs(self.start_y - end_y)

        self.chat_region = (x, y, w, h)
        self.region_label.config(text=f"Chat Region: {w}x{h} at ({x},{y})", foreground="#4cd137")
        self.log_message(f"Region calibrated: {self.chat_region}")
        
        self.save_config()
        self.calib_window.destroy()

    def start_scanner(self):
        if not self.chat_region:
            messagebox.showwarning("Calibration Required", "Please calibrate the chat region first.")
            return
        if not self.webhook_entry.get().strip():
            messagebox.showwarning("Webhook Required", "Please enter a Discord webhook URL.")
            return
        if self.reader is None:
            messagebox.showwarning("OCR Loading", "EasyOCR is still initializing. Please wait a moment.")
            return

        self.save_config()

        self.is_running = True
        self.btn_start.config(state=tk.DISABLED)
        self.btn_calibrate.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.log_message("Scanner started.")
        
        self.send_status_webhook("started")
        
        threading.Thread(target=self.scan_loop, daemon=True).start()

    def stop_scanner(self):
        self.is_running = False
        self.btn_start.config(state=tk.NORMAL)
        self.btn_calibrate.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.log_message("Scanner stopped.")
        
        self.send_status_webhook("stopped")

    def scan_loop(self):
        while self.is_running:
            start_time = time.time()
            self.process_screen()
            
            elapsed = time.time() - start_time
            sleep_time = max(0, 1.0 - elapsed)
            time.sleep(sleep_time)

    def process_screen(self):
        try:
            screenshot = pyautogui.screenshot(region=self.chat_region)
            img_np = np.array(screenshot)
            img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)

            processed_img = preprocess_image_for_ocr(img_bgr)
            
            results = self.reader.readtext(processed_img, detail=1)
            detected_egg = None
            saw_egg_spawned = False

            for (bbox, text, prob) in results:
                cleaned_text = clean_text(text)

                if "spawned" in cleaned_text:
                    saw_egg_spawned = True

                for keyword, egg_name in EGG_KEYWORDS.items():
                    if clean_text(keyword) in cleaned_text:
                        if keyword == "special":
                            scaled_down_bbox = [(point[0] / 2.0, point[1] / 2.0) for point in bbox]
                            detected_egg = determine_special_egg_type(img_bgr, scaled_down_bbox)
                        else:
                            detected_egg = egg_name
                        break

                if detected_egg:
                    break
            
            if not detected_egg and saw_egg_spawned:
                detected_egg = "Unknown Egg Spawned"

            current_time = time.time()
            if detected_egg:
                last_seen_time = self.egg_cooldowns.get(detected_egg, 0)
                
                if (current_time - last_seen_time) > EGG_COOLDOWN_SECONDS:
                    self.log_message(f"Detected: {detected_egg}")
                    
                    full_screenshot = pyautogui.screenshot()
                    full_img_np = np.array(full_screenshot)
                    full_img_bgr = cv2.cvtColor(full_img_np, cv2.COLOR_RGB2BGR)
                    
                    self.send_discord_webhook(detected_egg, full_img_bgr)
                    self.egg_cooldowns[detected_egg] = current_time

        except Exception as e:
            self.log_message(f"Error during scan: {e}")

    def send_discord_webhook(self, egg_name, image_bgr):
        webhook_url = self.webhook_entry.get().strip()
        user_id = self.userid_entry.get().strip()
        
        is_success, buffer = cv2.imencode(".png", image_bgr)
        io_buf = io.BytesIO(buffer)

        payload = {
            "embeds": [
                {
                    "title": "Egg Spawned!",
                    "url": DISCORD_LINK,
                    "description": f"**{egg_name}** has appeared!\n\n[Join Manas Biome Hunt!]({DISCORD_LINK})",
                    "color": 16766720,
                    "image": {"url": "attachment://chat_capture.png"},
                    "footer": {"text": "Manas's Egg Detector"},
                    "timestamp": time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
                }
            ]
        }

        if user_id:
            payload["content"] = f"<@{user_id}>"

        files = {"file": ("chat_capture.png", io_buf, "image/png")}

        try:
            response = requests.post(webhook_url, data={"payload_json": requests.compat.json.dumps(payload)}, files=files)
            if response.status_code in [200, 204]:
                self.log_message(f"Webhook sent for {egg_name}")
            else:
                self.log_message(f"Failed to send webhook. Code: {response.status_code}")
        except Exception as e:
            self.log_message(f"Webhook error: {e}")
            
    def send_status_webhook(self, status):
        webhook_url = self.webhook_entry.get().strip()
        user_id = self.userid_entry.get().strip()
        
        if not webhook_url:
            return

        if status == "started":
            embed_title = "🟢 Macro Started"
            embed_color = 5763719 
        else:
            embed_title = "🔴 Macro Stopped"
            embed_color = 15548997 

        payload = {
            "embeds": [
                {
                    "title": embed_title,
                    "url": DISCORD_LINK,
                    "description": f"[Join Manas Biome Hunt!]({DISCORD_LINK})",
                    "color": embed_color,
                    "footer": {"text": "Manas's Egg Detector"},
                    "timestamp": time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
                }
            ]
        }

        def post_webhook():
            try:
                requests.post(webhook_url, json=payload)
            except Exception as e:
                self.log_message(f"Status Webhook error: {e}")
                
        threading.Thread(target=post_webhook, daemon=True).start()

if __name__ == "__main__":
    root = tk.Tk()
    app = EggMonitorGUI(root)
    root.mainloop()
