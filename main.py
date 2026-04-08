# 阿翰牛肉麵 - 完整整合版（開始畫面設定＋滑鼠拖曳音量＋防黑屏＋穩定視窗切換＋比例回填）
import asyncio
import pygame, sys, os, random
import json
IS_WEB = (sys.platform == 'emscripten')
if not IS_WEB:
    import sqlite3
else:
    import platform
from pathlib import Path
from datetime import datetime


# ---------------- 基本工具 ----------------
def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS  # pyinstaller
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


def get_save_root():
    return (
        os.path.dirname(sys.executable)
        if getattr(sys, "frozen", False)
        else os.path.abspath(".")
    )


def _safe_get_desktop_size():
    import sys
    if sys.platform == "emscripten":
        return 1280, 720
    try:
        info = pygame.display.Info()
        return max(640, info.current_w), max(480, info.current_h)
    except Exception:
        return 1920, 1080


# ---------------- 初始化音訊/視窗 ----------------
pygame.mixer.pre_init(44100, -16, 2, 512)
pygame.init()
pygame.mixer.init()
pygame.mixer.set_num_channels(16)

# 初始以全螢幕開啟（不使用 SCALED 以避免 renderer 失敗）
import sys
if sys.platform == "emscripten":
    DW, DH = 1280, 720
    screen = pygame.display.set_mode((DW, DH))
else:
    DW, DH = _safe_get_desktop_size()
    screen = pygame.display.set_mode((DW, DH), pygame.FULLSCREEN | pygame.DOUBLEBUF)
screen_width, screen_height = screen.get_size()
pygame.display.set_caption("阿翰牛肉麵")

# 狀態
is_fullscreen = True
WINDOWED_SIZE = None
WINDOWED_POS = None

# 顏色 & 字體
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
BUTTON_COLOR = (255, 0, 0)
ACCENT = (0, 120, 255)
YELLOW = (255, 255, 0)
font_name = resource_path("font.ttf")


def get_font(size, mono=False):
    size = int(size)
    try:
        if not mono:
            return pygame.font.Font(font_name, size)
        mono_path = pygame.font.match_font(
            "consolas,dejavusansmono,menlo,couriernew,monospace"
        )
        return (
            pygame.font.Font(mono_path, size)
            if mono_path
            else pygame.font.SysFont("monospace", size)
        )
    except Exception:
        return pygame.font.SysFont(None, size)


# ---------------- 音量/靜音控制（新增 Master 音量＋滑桿支持） ----------------
MASTER_VOLUME = 1.00  # 主音量（會影響 BGM 與 SFX）
MUSIC_VOLUME = 0.85  # BGM 個別音量
SFX_VOLUME = 1.00  # 音效 個別音量
MUTED = False
ALL_SFX = []

# 滑桿狀態（開始畫面設定用）
SLIDERS = {}  # name -> {"track":Rect, "knob":Rect}
MUTE_BTN_RECT = None


def _clamp01(x):
    return max(0.0, min(1.0, float(x)))


def _effective_music():
    return 0.0 if MUTED else _clamp01(MASTER_VOLUME * MUSIC_VOLUME)


def _effective_sfx():
    return 0.0 if MUTED else _clamp01(MASTER_VOLUME * SFX_VOLUME)


def apply_volumes(show_toast=False):
    mv = _effective_music()
    sv = _effective_sfx()

    # 1) BGM 立即套用
    try:
        pygame.mixer.music.set_volume(mv)
    except Exception:
        pass

    # 2) 預設音效音量（未來播放）
    for s in ALL_SFX:
        try:
            if s:
                s.set_volume(sv)
        except Exception:
            pass

    # 3) 正在播放中的聲道同步調整
    try:
        num = pygame.mixer.get_num_channels()
        for i in range(num):
            ch = pygame.mixer.Channel(i)
            ch.set_volume(sv)
    except Exception:
        pass

    if show_toast:
        percent = int((_effective_music()) * 100)
        _toast(f"音量：{0 if MUTED else percent}%" + ("（靜音）" if MUTED else ""), 1.2)


def set_master_abs(v):
    global MASTER_VOLUME
    MASTER_VOLUME = _clamp01(v)
    apply_volumes(False)
    save_volume_to_db()


def set_music_abs(v):
    global MUSIC_VOLUME
    MUSIC_VOLUME = _clamp01(v)
    apply_volumes(False)
    save_volume_to_db()


def set_sfx_abs(v):
    global SFX_VOLUME
    SFX_VOLUME = _clamp01(v)
    apply_volumes(False)
    save_volume_to_db()


def set_volume_delta(delta):
    """主音量熱鍵（- / =）會同時縮放 BGM/SFX 的總體輸出"""
    global MASTER_VOLUME
    MASTER_VOLUME = _clamp01(MASTER_VOLUME + delta)
    apply_volumes(show_toast=True)
    save_volume_to_db()


def toggle_mute():
    global MUTED
    MUTED = not MUTED
    apply_volumes(show_toast=True)
    save_volume_to_db()


# ---------------- 音效/BGM 載入 ----------------
def load_sfx_base(name_no_ext):
    for ext in (".wav", ".ogg", ".mp3"):
        p = resource_path(name_no_ext + ext)
        if os.path.exists(p):
            try:
                snd = pygame.mixer.Sound(p)
                snd.set_volume(_effective_sfx())
                return snd
            except Exception:
                pass
    return None


current_bgm_key = None


def resolve_music_path(name_no_ext):
    for ext in (".wav", ".ogg", ".mp3"):
        p = resource_path(name_no_ext + ext)
        if os.path.exists(p):
            return p
    return None


def play_bgm(name_no_ext, loop=-1, volume=None, fade_ms=500):
    global current_bgm_key, MUSIC_VOLUME
    # 如果正在播放相同的音樂，就不重複載入，避免轉場卡頓
    if current_bgm_key == name_no_ext and pygame.mixer.music.get_busy():
        return

    path = resolve_music_path(name_no_ext)
    if not path:
        print(f"[BGM] Not found: {name_no_ext}")
        return
    try:
        if pygame.mixer.music.get_busy():
            if IS_WEB:
                pygame.mixer.music.stop() # 網頁版 stop 比 fadeout 穩定
            else:
                pygame.mixer.music.fadeout(fade_ms)
        
        pygame.mixer.music.load(path)
        if volume is not None:
            MUSIC_VOLUME = _clamp01(volume)
        pygame.mixer.music.set_volume(_effective_music())
        
        try:
            # 網頁版同樣建議避免 fade_ms 參數在 play 時產生的不穩定
            if IS_WEB:
                pygame.mixer.music.play(loop)
            else:
                pygame.mixer.music.play(loop, fade_ms=fade_ms)
        except TypeError:
            pygame.mixer.music.play(loop)
            
        current_bgm_key = name_no_ext
    except Exception as e:
        print(f"[BGM] Failed: {e}")


def safe_play(snd):
    try:
        if snd:
            snd.set_volume(_effective_sfx())
            snd.play()
    except Exception:
        pass


# ---------------- DB ----------------
APP_DIR = Path(os.path.expanduser("~")) / "AhanNoodles"
DB_FILE = str(APP_DIR / "game_stats.db")

WEB_DATA = {
    "stats": {"highscore": 0, "total_play_seconds": 0.0, "master_volume": 1.0, "music_volume": 0.85, "sfx_volume": 1.0},
    "sessions": []
}
WEB_KEY = "ahan_noodles_save"

def _load_web_data():
    if not IS_WEB: return
    try:
        val = platform.window.localStorage.getItem(WEB_KEY)
        if val:
            global WEB_DATA
            sys_data = json.loads(val)
            WEB_DATA["stats"].update(sys_data.get("stats", {}))
            WEB_DATA["sessions"] = sys_data.get("sessions", [])
    except Exception:
        pass

def _save_web_data():
    if not IS_WEB: return
    try:
        val = json.dumps(WEB_DATA)
        platform.window.localStorage.setItem(WEB_KEY, val)
    except Exception:
        pass


def init_db():
    if IS_WEB:
        _load_web_data()
        return
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS stats (
                        id INTEGER PRIMARY KEY CHECK (id=1),
                        highscore INTEGER NOT NULL DEFAULT 0,
                        total_play_seconds REAL NOT NULL DEFAULT 0.0,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        score INTEGER NOT NULL,
                        play_seconds REAL NOT NULL,
                        ended_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

        c.execute("PRAGMA table_info(stats)")
        cols = [row[1] for row in c.fetchall()]

        if "master_volume" not in cols:
            c.execute("ALTER TABLE stats ADD COLUMN master_volume REAL NOT NULL DEFAULT 1.0")
        if "music_volume" not in cols:
            c.execute("ALTER TABLE stats ADD COLUMN music_volume REAL NOT NULL DEFAULT 0.85")
        if "sfx_volume" not in cols:
            c.execute("ALTER TABLE stats ADD COLUMN sfx_volume REAL NOT NULL DEFAULT 1.0")

        c.execute("SELECT id FROM stats WHERE id=1")
        if not c.fetchone():
            c.execute('''INSERT INTO stats (id, highscore, total_play_seconds, master_volume, music_volume, sfx_volume) VALUES (1, 0, 0.0, 1.0, 0.85, 1.0)''')


def load_stats():
    if IS_WEB:
        return (WEB_DATA["stats"]["highscore"], WEB_DATA["stats"]["total_play_seconds"])
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT highscore,total_play_seconds FROM stats WHERE id=1")
        row = c.fetchone()
    return (int(row[0]), float(row[1])) if row else (0, 0.0)


def update_highscore_in_db(score):
    if IS_WEB:
        if score > WEB_DATA["stats"]["highscore"]:
            WEB_DATA["stats"]["highscore"] = score
            _save_web_data()
        return
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT highscore FROM stats WHERE id=1")
        cur = int(c.fetchone()[0])
        if score > cur:
            c.execute("UPDATE stats SET highscore=?,updated_at=CURRENT_TIMESTAMP WHERE id=1", (score,))


def add_play_seconds_to_db(sec):
    if sec <= 0:
        return
    if IS_WEB:
        WEB_DATA["stats"]["total_play_seconds"] += float(sec)
        _save_web_data()
        return
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("UPDATE stats SET total_play_seconds=total_play_seconds+?,updated_at=CURRENT_TIMESTAMP WHERE id=1", (float(sec),))


def add_session(score, play_seconds):
    if IS_WEB:
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        WEB_DATA["sessions"].append({"score": int(score), "play_seconds": float(play_seconds), "ended_at": now_str})
        _save_web_data()
        return
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO sessions (score,play_seconds) VALUES (?,?)", (int(score), float(play_seconds)))


def get_top_scores(limit=10):
    if IS_WEB:
        sorted_sess = sorted(WEB_DATA["sessions"], key=lambda x: (-x["score"], x["ended_at"]))[:limit]
        return [(s["score"], s["play_seconds"], s["ended_at"].split()[0]) for s in sorted_sess]
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute('''SELECT score,play_seconds,date(ended_at) FROM sessions
                     ORDER BY score DESC, ended_at DESC LIMIT ?''', (limit,))
        return c.fetchall()


def get_recent_sessions(limit=10):
    if IS_WEB:
        sorted_sess = sorted(WEB_DATA["sessions"], key=lambda x: x["ended_at"], reverse=True)[:limit]
        return [(s["score"], s["play_seconds"], s["ended_at"].split()[0]) for s in sorted_sess]
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute('''SELECT score,play_seconds,date(ended_at) FROM sessions
                     ORDER BY ended_at DESC LIMIT ?''', (limit,))
        return c.fetchall()


def load_volume_from_db():
    global MASTER_VOLUME, MUSIC_VOLUME, SFX_VOLUME
    if IS_WEB:
        MASTER_VOLUME = _clamp01(WEB_DATA["stats"].get("master_volume", 1.0))
        MUSIC_VOLUME = _clamp01(WEB_DATA["stats"].get("music_volume", 0.85))
        SFX_VOLUME = _clamp01(WEB_DATA["stats"].get("sfx_volume", 1.0))
        return
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        try:
            c.execute("SELECT master_volume, music_volume, sfx_volume FROM stats WHERE id=1")
            row = c.fetchone()
            if row:
                MASTER_VOLUME = _clamp01(row[0])
                MUSIC_VOLUME = _clamp01(row[1])
                SFX_VOLUME = _clamp01(row[2])
        except sqlite3.OperationalError:
            pass


def save_volume_to_db():
    if IS_WEB:
        WEB_DATA["stats"]["master_volume"] = float(MASTER_VOLUME)
        WEB_DATA["stats"]["music_volume"] = float(MUSIC_VOLUME)
        WEB_DATA["stats"]["sfx_volume"] = float(SFX_VOLUME)
        _save_web_data()
        return
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        try:
            c.execute('''UPDATE stats
                SET master_volume = ?,
                    music_volume  = ?,
                    sfx_volume    = ?,
                    updated_at    = CURRENT_TIMESTAMP
                WHERE id = 1
                ''', (float(MASTER_VOLUME), float(MUSIC_VOLUME), float(SFX_VOLUME)))
        except sqlite3.OperationalError:
            pass

def format_seconds(sec):
    sec = int(sec)
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"


init_db()
highscore, total_play_seconds = load_stats()
load_volume_from_db()     # 🔔 這行是新的：啟動時讀回上次音量

# ---------------- 截圖/右下角提示 ----------------
SCREENSHOT_DIR = os.path.join(get_save_root(), "Screenshot")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)
screenshot_toast_time = 0.0
screenshot_toast_text = ""


def _toast(text, secs=1.2):
    global screenshot_toast_time, screenshot_toast_text
    screenshot_toast_text = text
    screenshot_toast_time = secs


def take_screenshot(surface):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"screenshot_{ts}.png"
    fpath = os.path.join(SCREENSHOT_DIR, fname)
    try:
        pygame.image.save(surface, fpath)
        _toast(f"已截圖：{fname}", 1.2)
    except Exception as e:
        _toast(f"截圖失敗：{e}", 1.8)


def draw_info_with_bg(
    surf, text, size, x, y, bg_color=(0, 0, 0), text_color=(255, 255, 255), padding=10
):
    font = get_font(size)
    s = font.render(text, True, text_color)
    r = s.get_rect(center=(x, y))
    box = pygame.Rect(
        r.left - padding, r.top - padding, r.width + 2 * padding, r.height + 2 * padding
    )
    pygame.draw.rect(surf, bg_color, box, border_radius=8)
    surf.blit(s, r)


def draw_screenshot_toast(dt):
    global screenshot_toast_time
    if screenshot_toast_time > 0:
        screenshot_toast_time -= dt
        draw_info_with_bg(
            screen,
            screenshot_toast_text,
            max(16, screen_width // 48),
            screen_width - 260,
            screen_height - 40,
            (0, 0, 0),
            (255, 255, 255),
            10,
        )


# ---------------- 載圖/重建版面 ----------------
def load_image(name, size=None):
    for ext in (".png", ".jpg"):
        p = resource_path("images/" + (name if name.endswith(ext) else name + ext))
        if os.path.exists(p):
            img = pygame.image.load(p).convert_alpha()
            return pygame.transform.smoothscale(img, size) if size else img
    raise FileNotFoundError(f"Images not found: {name}.png/.jpg")


# 這些全域由 build_static_layout() 建立/刷新
background_img = logo_img = leaderboard_bg = None
tutorial_images = []
gameplay_bg = None
ready_bg = None
trash_img = None
trash_rect = None
empty_bowl_img = None
hammer_img = None
hammer_rect = None
redbox_rect = None
BIG_BOWL_SIZE = None
categories = {}
material_size_map = {}
material_data = {}
extra_toppings = []
extra_topping_data = {}
extra_topping_positions = {}
customer_positions = []
interfering_positions = []


def build_static_layout():
    """依目前 screen_size 載入靜態素材與定位"""
    global screen_width, screen_height
    global background_img, logo_img, leaderboard_bg, tutorial_images
    global gameplay_bg, ready_bg, trash_img, trash_rect
    global empty_bowl_img, hammer_img, hammer_rect
    global redbox_rect, BIG_BOWL_SIZE
    global categories, material_size_map, material_data
    global extra_toppings, extra_topping_data, extra_topping_positions
    global customer_positions, interfering_positions
    screen_width, screen_height = pygame.display.get_surface().get_size()
    base_font_size = max(16, screen_width // 48)

    # 背景/Logo/教學
    background_img = load_image("background", (screen_width, screen_height))
    logo_img = load_image("logo", (int(screen_width * 0.25), int(screen_height * 0.45)))
    gameplay_bg = load_image("背景", (screen_width, screen_height))
    ready_bg = load_image("準備", (screen_width, screen_height))
    try:
        leaderboard_bg = load_image("leaderboard_bg", (screen_width, screen_height))
    except FileNotFoundError:
        leaderboard_bg = background_img
    tutorial_names = [
        "tutorial1",
        "tutorial2",
        "tutorial3",
        "tutorial4",
        "tutorial5",
        "tutorial6",
    ]
    tutorial_images.clear()
    for n in tutorial_names:
        tutorial_images.append(load_image(n, (screen_width, screen_height)))

    # 工具/垃圾桶
    trash_img = load_image(
        "trash", (int(screen_width * 0.22), int(screen_height * 0.20))
    )
    trash_rect = trash_img.get_rect()
    trash_rect.bottomright = (int(screen_width * 0.965), int(screen_height * 1.10))
    hammer_img = load_image(
        "hammer", (int(screen_width * 0.10), int(screen_width * 0.10))
    )
    hammer_rect = hammer_img.get_rect(
        topleft=(screen_width * 0.5, screen_height * 0.88)
    )

    # 紅框與大碗尺寸
    redbox_rect = pygame.Rect(
        int(screen_width * 0.60),
        int(screen_height * 0.52),
        int(screen_width * 0.20),
        int(screen_height * 0.33),
    )
    BIG_BOWL_SIZE = (redbox_rect.width, redbox_rect.height)
    empty_bowl_img = load_image(
        "empty_bowl", (int(screen_width * 0.08), int(screen_width * 0.08))
    )

    # 物料與尺寸
    categories = {
        "noodle": ["thin_noodles", "knife_cut_noodles"],
        "beef": ["shin_beef", "beef_tripe", "brisket_beef"],
        "soup": ["braised_soup", "clear_broth", "spicy_soup"],
    }
    material_size_map = {
        "noodle": (int(screen_width * 0.10), int(screen_height * 0.12)),
        "beef": (int(screen_width * 0.10), int(screen_height * 0.12)),
        "soup": (int(screen_width * 0.18), int(screen_height * 0.14)),
    }
    material_data.clear()
    for cat, items in categories.items():
        for i, name in enumerate(items):
            size = material_size_map[cat]
            img = load_image(name, size)
            if cat == "noodle":
                x = screen_width * 0.09
                y = screen_height * 0.55 + i * size[1] * 0.9
            elif cat == "soup":
                x = screen_width * 0.20
                y = screen_height * 0.54 + i * size[1] * 0.75
            else:  # beef
                x = screen_width * 0.46
                y = screen_height * 0.55 + i * size[1] * 0.9
            rect = img.get_rect(topleft=(x, y))
            material_data[name] = {
                "image": img,
                "rect": rect,
                "category": cat,
                "init_pos": (x, y),
            }

    # 加料
    extra_toppings = ["green_onion", "sour_veggies"]
    extra_topping_positions = {
        "green_onion": (screen_width * 0.82, screen_height * 0.55),
        "sour_veggies": (screen_width * 0.82, screen_height * 0.70),
    }
    extra_topping_data.clear()
    for t in extra_toppings:
        img = load_image(t, (int(screen_width * 0.08), int(screen_width * 0.08)))
        x, y = extra_topping_positions[t]
        extra_topping_data[t] = {"image": img, "rect": img.get_rect(topleft=(x, y))}

    # 站位
    customer_positions = [(0.31, 0.63), (0.51, 0.63), (0.72, 0.63)]
    interfering_positions = [(0.15, 0.50), (0.85, 0.50)]


def reload_scaled_assets():
    build_static_layout()


# ---------------- 訂單權重 ----------------
NOODLE_POP = ["thin_noodles", "knife_cut_noodles"]
NOODLE_W = [0.5, 0.5]
SOUP_POP = ["clear_broth", "braised_soup", "spicy_soup"]
SOUP_W = [0.4, 0.4, 0.2]
BEEF_POP = ["shin_beef", "beef_tripe", "brisket_beef"]
BEEF_W = [1, 1, 1]
TOPPING_POP = ["sour_veggies", "green_onion"]
TOPPING_P = 0.5

# 先建版面
build_static_layout()


# ---------------- 類別 ----------------
def draw_text(surf, text, size, x, y, color=BLACK):
    font = get_font(size)
    s = font.render(text, True, color)
    surf.blit(s, s.get_rect(center=(x, y)))


class Customer:
    def __init__(self, pos_ratio, practice=False):
        self.type = random.randint(1, 3)
        self.position_ratio = pos_ratio
        self.practice = practice
        self.timer = 20
        self.waiting = True
        self.angry_image_loaded = False
        self.bowls_needed = 1
        # 隨機訂單
        self.noodle = random.choices(NOODLE_POP, weights=NOODLE_W, k=1)[0]
        self.soup = random.choices(SOUP_POP, weights=SOUP_W, k=1)[0]
        self.beef = random.choices(BEEF_POP, weights=BEEF_W, k=1)[0]
        self.extra_toppings = [t for t in TOPPING_POP if random.random() < TOPPING_P]
        self.rescale_on_resize()

    def rescale_on_resize(self):
        FIX = int(screen_height * 0.36)
        original = load_image(f"customer{self.type}")
        ow, oh = original.get_size()
        scale = FIX / oh
        self.image = pygame.transform.smoothscale(original, (int(ow * scale), FIX))
        self.img_width = self.image.get_width()
        self.img_height = FIX
        suffix = (
            "_" + "_".join(sorted(self.extra_toppings)) if self.extra_toppings else ""
        )
        img_name = f"{self.noodle}_{self.beef}_{self.soup}{suffix}"
        self.order_image = load_image(
            img_name, (int(screen_height * 0.30), int(screen_height * 0.30))
        )
        self.pos = (
            int(screen_width * self.position_ratio[0]),
            int(screen_height * self.position_ratio[1] - self.img_height),
        )
        if self.angry_image_loaded:
            try:
                angry = load_image(f"customer{self.type}_angry")
                ow, oh = angry.get_size()
                sc = self.img_height / oh
                self.image = pygame.transform.smoothscale(
                    angry, (int(ow * sc), self.img_height)
                )
            except FileNotFoundError:
                pass

    def update(self, dt):
        if self.practice:
            return
        if self.waiting:
            self.timer -= dt
            if not self.angry_image_loaded and self.timer <= 10:
                try:
                    angry = load_image(f"customer{self.type}_angry")
                    ow, oh = angry.get_size()
                    sc = self.img_height / oh
                    self.image = pygame.transform.smoothscale(
                        angry, (int(ow * sc), self.img_height)
                    )
                    self.angry_image_loaded = True
                except FileNotFoundError:
                    pass
            if self.timer <= 0:
                self.timer = 0
                self.waiting = False

    def draw(self, surf):
        if not self.waiting:
            return
        img_rect = self.image.get_rect(topleft=self.pos)
        half_h = self.image.get_height() * 2 // 3
        crop = pygame.Rect(0, 0, self.image.get_width(), half_h)
        surf.blit(self.image, img_rect.topleft, crop)
        cx = self.pos[0] + self.img_width // 2
        order_top_y = self.pos[1] - int(screen_height * 0.05)
        surf.blit(
            self.order_image,
            self.order_image.get_rect(center=(cx, order_top_y)).topleft,
        )
        info_top_y = self.pos[1] - int(screen_height * 0.02)
        w = int(screen_width * 0.05)
        h = int(screen_height * 0.035)
        gap = int(screen_height * 0.008)
        b1 = pygame.Rect(cx - w // 2, info_top_y, w, h)
        b2 = pygame.Rect(cx - w // 2, info_top_y + h + gap, w, h)
        pygame.draw.rect(surf, BLACK, b1, border_radius=6)
        pygame.draw.rect(surf, BLACK, b2, border_radius=6)
        fs = max(screen_width // 96, 14)
        draw_text(
            surf,
            "不限時" if self.practice else f"{int(max(0,self.timer))} 秒",
            fs,
            b1.centerx,
            b1.centery,
            WHITE,
        )
        draw_text(surf, f"{self.bowls_needed} 碗", fs, b2.centerx, b2.centery, WHITE)


class InterferingCustomer:
    def __init__(self, pos_ratio):
        self.position_ratio = pos_ratio
        self.active = True
        self.hit = False
        self.hit_timer = 0
        self.rescale_on_resize()

    def rescale_on_resize(self):
        FIX = int(screen_height * 0.36)
        normal = load_image("interfering_customer")
        hit = load_image("interfering_customer_hit")
        ow, oh = normal.get_size()
        sc = FIX / oh
        self.normal_image = pygame.transform.smoothscale(normal, (int(ow * sc), FIX))
        self.hit_image = pygame.transform.smoothscale(hit, (int(ow * sc), FIX))
        self.img_width = self.normal_image.get_width()
        self.img_height = FIX
        self.pos = (
            int(screen_width * self.position_ratio[0]),
            int(screen_height * self.position_ratio[1] - self.img_height),
        )

    def update(self, dt):
        if self.hit:
            self.hit_timer -= dt
            if self.hit_timer <= 0:
                self.active = False

    def draw(self, surf):
        if self.active:
            surf.blit(self.hit_image if self.hit else self.normal_image, self.pos)

    def get_hit(self):
        self.hit = True
        self.hit_timer = 0.5


class CompletedBowl:
    def __init__(self, pos, slot_index=None, big=False):
        self.slot_index = slot_index
        self.selected_noodle = None
        self.selected_beef = None
        self.selected_soup = None
        self.extra_toppings = []
        self.big = big
        self.completed = False
        self.dragging = False
        self.offset = (0, 0)
        self.image = empty_bowl_img
        self.rect = self.image.get_rect(topleft=pos)
        self._cx_ratio = None
        self._cy_ratio = None

    def add_material(self, name, category):
        if category == "noodle" and not self.selected_noodle:
            self.selected_noodle = name
        elif category == "beef" and not self.selected_beef:
            self.selected_beef = name
        elif category == "soup" and not self.selected_soup:
            self.selected_soup = name
        if self.selected_noodle and self.selected_beef and self.selected_soup:
            self.completed = True
            suffix = (
                "_" + "_".join(sorted(self.extra_toppings))
                if self.extra_toppings
                else ""
            )
            img_name = f"{self.selected_noodle}_{self.selected_beef}_{self.selected_soup}{suffix}"
            target = (
                BIG_BOWL_SIZE
                if self.big
                else (int(screen_width * 0.08), int(screen_width * 0.08))
            )
            self.image = load_image(img_name, target)
            self.rect.size = target

    def rescale_on_resize(self):
        if self.big:
            target = BIG_BOWL_SIZE
            self.rect = pygame.Rect(redbox_rect.topleft, target)
            if self.completed:
                suffix = (
                    "_" + "_".join(sorted(self.extra_toppings))
                    if self.extra_toppings
                    else ""
                )
                img_name = f"{self.selected_noodle}_{self.selected_beef}_{self.selected_soup}{suffix}"
                self.image = load_image(img_name, target)
            else:
                self.image = load_image("empty_bowl", target)
        else:
            target = (int(screen_width * 0.08), int(screen_width * 0.08))
            if self.completed:
                suffix = (
                    "_" + "_".join(sorted(self.extra_toppings))
                    if self.extra_toppings
                    else ""
                )
                img_name = f"{self.selected_noodle}_{self.selected_beef}_{self.selected_soup}{suffix}"
                self.image = load_image(img_name, target)
            else:
                self.image = load_image("empty_bowl", target)
            if self._cx_ratio is not None and self._cy_ratio is not None:
                cx = int(screen_width * self._cx_ratio)
                cy = int(screen_height * self._cy_ratio)
                self.rect = self.image.get_rect(center=(cx, cy))
            else:
                self.rect.size = target

    def draw(self, surf):
        surf.blit(self.image, self.rect)
        if not self.completed:
            small = (int(self.rect.width * 0.45), int(self.rect.height * 0.45))
            pts = [
                (
                    self.rect.x + self.rect.width * 0.25,
                    self.rect.y + self.rect.height * 0.15,
                ),
                (
                    self.rect.x + self.rect.width * 0.55,
                    self.rect.y + self.rect.height * 0.25,
                ),
                (
                    self.rect.x + self.rect.width * 0.35,
                    self.rect.y + self.rect.height * 0.55,
                ),
            ]
            k = 0
            if self.selected_noodle:
                surf.blit(load_image(self.selected_noodle, small), pts[k])
                k += 1
            if self.selected_beef:
                surf.blit(load_image(self.selected_beef, small), pts[k])
                k += 1
            if self.selected_soup:
                surf.blit(load_image(self.selected_soup, small), pts[k])
                k += 1


# ---------------- 遊戲狀態 ----------------
hammer_selected = False
tutorial_index = 0
score = 0
game_time = 120
countdown_start = 3
game_paused = False
game_ready = True
session_play_seconds = 0.0
show_leaderboard = False
show_settings_menu = False  # ★ 新增：開始畫面「設定」頁
game_started = False
show_rules = False
show_countdown = False
timer = 0
ready_to_start_game = False
completed_bowls = []
customers = []
interfering_customers = []
practice_customer = None
customer_spawn_timer = 0
interfering_spawn_timer = 0

# 音效
score_sound = load_sfx_base("score")
end_sound = load_sfx_base("end")
end_good_sound = load_sfx_base("end_good")  # 分數 > 25 用（檔名 end_good.xxx）
click_sound = load_sfx_base("click")
hammer_hit_sound = load_sfx_base("hammer_hit")
interferer_spawn_sound = load_sfx_base("interfere_spawn")
error_sound = load_sfx_base("error")        # ⬅️ 新增：上錯麵的錯誤音效
ALL_SFX = [
    score_sound,
    end_sound,
    end_good_sound,
    click_sound,
    hammer_hit_sound,
    interferer_spawn_sound,
    error_sound,
]
# apply_volumes 和 play_bgm 移至 main() 內執行以避免網頁版 Autoplay 錯誤


# ---------------- UI 畫面：開始畫面＋設定頁 ----------------
def draw_start_screen():
    base_font_size = max(16, screen_width // 48)
    screen.blit(background_img, (0, 0))
    screen.blit(
        logo_img, logo_img.get_rect(midbottom=(screen_width / 2, screen_height / 2.3))
    )

    title = "阿翰牛肉麵"
    title_f = get_font(int(base_font_size * 2))
    s = title_f.render(title, True, BLACK)
    r = s.get_rect(center=(screen_width / 2, screen_height / 3))
    screen.blit(s, r)

    rec_f = get_font(base_font_size)
    s2 = rec_f.render(f"歷史最高紀錄: {highscore}", True, BLACK)
    r2 = s2.get_rect(center=(screen_width / 2, screen_height / 3 + base_font_size * 2))
    screen.blit(s2, r2)

    s3 = rec_f.render(
        f"累計遊玩時間: {format_seconds(total_play_seconds)}", True, BLACK
    )
    r3 = s3.get_rect(center=(screen_width / 2, r2.bottom + base_font_size * 1.4))
    screen.blit(s3, r3)

    # 半透明底
    pad_x, pad_y = 60, 25
    top = r.top - pad_y
    bot = r3.bottom + pad_y
    w = max(r.width, r2.width, r3.width) + 2 * pad_x
    bg = pygame.Surface((w, bot - top), pygame.SRCALPHA)
    pygame.draw.rect(bg, (255, 255, 255, 100), bg.get_rect(), border_radius=25)
    screen.blit(bg, ((screen_width - w) // 2, top))
    screen.blit(s, r)
    screen.blit(s2, r2)
    screen.blit(s3, r3)

    # 三顆大按鈕 + 設定按鈕
    start_button = pygame.Rect(
        (screen_width - base_font_size * 10) // 2,
        int(screen_height * 0.75),
        base_font_size * 10,
        base_font_size * 2,
    )
    lb_button = pygame.Rect(
        start_button.left,
        start_button.top - base_font_size * 3,
        base_font_size * 10,
        base_font_size * 2,
    )
    exit_button = pygame.Rect(
        start_button.left,
        start_button.bottom + base_font_size,
        base_font_size * 10,
        base_font_size * 2,
    )
    # 新增：設定按鈕（與排行榜同列，靠左）
    settings_button = pygame.Rect(
        start_button.left - base_font_size * 12,
        start_button.top,
        base_font_size * 10,
        base_font_size * 2,
    )

    pygame.draw.rect(screen, BUTTON_COLOR, start_button, border_radius=10)
    pygame.draw.rect(screen, ACCENT, lb_button, border_radius=10)
    pygame.draw.rect(screen, (60, 60, 60), exit_button, border_radius=10)
    pygame.draw.rect(screen, (0, 160, 120), settings_button, border_radius=10)

    draw_text(
        screen,
        "開始遊戲",
        base_font_size,
        start_button.centerx,
        start_button.centery,
        WHITE,
    )
    draw_text(
        screen,
        "排行榜 / 歷史",
        base_font_size,
        lb_button.centerx,
        lb_button.centery,
        WHITE,
    )
    draw_text(
        screen,
        "退出遊戲",
        base_font_size,
        exit_button.centerx,
        exit_button.centery,
        WHITE,
    )
    draw_text(
        screen,
        "設定",
        base_font_size,
        settings_button.centerx,
        settings_button.centery,
        WHITE,
    )

    pygame.display.flip()
    return start_button, lb_button, exit_button, settings_button


# ---- 設定頁滑桿繪製與互動 ----
def _draw_slider(name, label, value, mid_y):
    """畫出一條 0..1 的滑桿，回傳並記錄 track/knob 幾何"""
    global SLIDERS
    base_font_size = max(16, screen_width // 48)

    track_w = int(screen_width * 0.45)
    track_h = max(6, base_font_size // 6)
    track_left = (screen_width - track_w) // 2
    track_top = mid_y - track_h // 2
    track = pygame.Rect(track_left, track_top, track_w, track_h)

    # 背景與刻度
    pygame.draw.rect(screen, (230, 230, 230), track, border_radius=track_h // 2)
    # 已填充區
    fill_w = int(track_w * _clamp01(value))
    if fill_w > 0:
        pygame.draw.rect(
            screen,
            (0, 160, 120),
            (track_left, track_top, fill_w, track_h),
            border_radius=track_h // 2,
        )

    # knob
    knob_r = max(10, track_h * 2)
    knob_x = track_left + fill_w
    knob_y = mid_y
    knob = pygame.Rect(0, 0, knob_r, knob_r)
    knob.center = (knob_x, knob_y)
    pygame.draw.circle(screen, (30, 30, 30), knob.center, knob_r // 2)

    # 文字：label 與數值%
    draw_text(
        screen,
        f"{label}",
        base_font_size,
        track_left - base_font_size * 3,
        mid_y,
        BLACK,
    )
    draw_text(
        screen,
        f"{int(_clamp01(value)*100)}%",
        base_font_size,
        track_left + track_w + base_font_size * 2,
        mid_y,
        BLACK,
    )

    SLIDERS[name] = {"track": track, "knob": knob}


def _slider_x_to_value(track_rect, x):
    """把滑鼠 x 座標轉換成 0..1 值"""
    if x <= track_rect.left:
        return 0.0
    if x >= track_rect.right:
        return 1.0
    return (x - track_rect.left) / track_rect.width


def draw_settings_screen_start():
    """開始畫面的設定頁：三條可拖曳音量滑桿 + 靜音 + 返回"""
    global MUTE_BTN_RECT
    base_font_size = max(16, screen_width // 48)

    screen.blit(background_img, (0, 0))
    draw_text(screen, "設定", base_font_size * 2, screen_width / 2, screen_height / 6)

    draw_info_with_bg(
        screen,
        "拖曳滑桿調整音量｜熱鍵：- / = 主音量 ｜ [ / ] SFX ｜ ; / ' BGM ｜ M 靜音",
        base_font_size,
        screen_width / 2,
        screen_height / 6 + base_font_size * 2,
    )

    block_top = int(screen_height * 0.35)
    row_gap = int(screen_height * 0.11)
    _draw_slider("master", "主音量 (Master)", MASTER_VOLUME, block_top)
    _draw_slider("music", "BGM 音量", MUSIC_VOLUME, block_top + row_gap)
    _draw_slider("sfx", "SFX 音量", SFX_VOLUME, block_top + row_gap * 2)

    # 靜音按鈕
    mute_w, mute_h = base_font_size * 7, base_font_size * 2
    MUTE_BTN_RECT = pygame.Rect(
        (screen_width - mute_w) // 2,
        block_top + row_gap * 3 + base_font_size,
        mute_w,
        mute_h,
    )
    pygame.draw.rect(
        screen,
        (80, 160, 80) if MUTED else (200, 50, 50),
        MUTE_BTN_RECT,
        border_radius=10,
    )
    draw_text(
        screen,
        "靜音：開" if MUTED else "靜音：關",
        base_font_size,
        MUTE_BTN_RECT.centerx,
        MUTE_BTN_RECT.centery,
        WHITE,
    )

    # 返回按鈕
    back_btn = pygame.Rect(
        30, screen_height - base_font_size * 3, base_font_size * 10, base_font_size * 2
    )
    pygame.draw.rect(screen, (255, 0, 0), back_btn, border_radius=10)
    draw_text(
        screen,
        "返回開始頁面",
        base_font_size,
        back_btn.centerx,
        back_btn.centery,
        WHITE,
    )

    pygame.display.flip()
    return back_btn


def draw_leaderboard_screen():
    base_font_size = max(16, screen_width // 48)
    screen.blit(leaderboard_bg, (0, 0))
    tops = get_top_scores(10)
    recents = get_recent_sessions(10)
    col_w = int(screen_width * 0.45)
    left_x = int(screen_width * 0.05)
    right_x = int(screen_width * 0.55)
    top_y = int(base_font_size * 4)
    hdr_h = int(base_font_size * 1.6)
    row_h = int(base_font_size * 1.4)
    pad_y = int(base_font_size * 0.5)
    header_font = get_font(base_font_size)
    cell_font = get_font(int(base_font_size * 1.0))

    def build_cols(x, w):
        wr = int(w * 0.14)
        ws = int(w * 0.20)
        wt = int(w * 0.22)
        wd = w - (wr + ws + wt)
        L = {"rank": x, "score": x + wr, "time": x + wr + ws, "date": x + wr + ws + wt}
        W = {"rank": wr, "score": ws, "time": wt, "date": wd}
        A = {"rank": "right", "score": "right", "time": "right", "date": "left"}
        P = {"rank": 12, "score": 12, "time": 12, "date": 100}
        O = {"rank": (0, 0), "score": (0, 0), "time": (0, 0), "date": (0, 0)}
        C = {
            "rank": (180, 0, 0),
            "score": (180, 0, 0),
            "time": (180, 0, 0),
            "date": (180, 0, 0),
        }
        return L, W, A, P, O, C

    def blit_cell(surf, font, text, key, y, L, W, A, P, O, color):
        dx, dy = O[key]
        pad = P[key]
        left = L[key]
        width = W[key]
        align = A[key]
        s = font.render(str(text), True, color)
        r = s.get_rect()
        r.top = y + dy
        if align == "left":
            r.left = left + pad + dx
        elif align == "right":
            r.right = left + width - pad + dx
        else:
            r.centerx = left + width // 2 + dx
        surf.blit(s, r)

    def head(x, y):
        L, W, A, P, O, C = build_cols(x, col_w)
        cy = y + (hdr_h - header_font.get_height()) // 2
        for k, t in zip(
            ["rank", "score", "time", "date"], ["排名", "分數", "時長", "日期"]
        ):
            blit_cell(screen, header_font, t, k, cy, L, W, A, P, O, (0, 0, 0))

    def row(x, y, idx, score_v, sec_v, date_v):
        from datetime import datetime as dt

        L, W, A, P, O, C = build_cols(x, col_w)
        by = y + (row_h - cell_font.get_height()) // 2
        try:
            d = dt.strptime(date_v, "%Y-%m-%d").strftime("%m/%d")
        except:
            d = date_v
        blit_cell(screen, cell_font, idx, "rank", by, L, W, A, P, O, C["rank"])
        blit_cell(screen, cell_font, score_v, "score", by, L, W, A, P, O, C["score"])
        blit_cell(
            screen,
            cell_font,
            format_seconds(sec_v),
            "time",
            by,
            L,
            W,
            A,
            P,
            O,
            C["time"],
        )
        blit_cell(screen, cell_font, d, "date", by, L, W, A, P, O, C["date"])

    hy = top_y + pad_y
    head(left_x, hy)
    head(right_x, hy)
    y = hy + hdr_h + 26
    for i, (sv, sec, at) in enumerate(tops, 1):
        row(left_x, y, i, sv, sec, at)
        y += row_h + 6
    y = hy + hdr_h + 26
    for i, (sv, sec, at) in enumerate(recents, 1):
        row(right_x, y, i, sv, sec, at)
        y += row_h + 6
    back_btn = pygame.Rect(
        30, screen_height - base_font_size * 3, base_font_size * 10, base_font_size * 2
    )
    pygame.draw.rect(screen, (255, 0, 0), back_btn, border_radius=10)
    draw_text(
        screen,
        "返回開始頁面",
        base_font_size,
        back_btn.centerx,
        back_btn.centery,
        WHITE,
    )
    pygame.display.flip()
    return back_btn


def draw_pause_menu():
    base_font_size = max(16, screen_width // 48)
    screen.blit(background_img, (0, 0))
    draw_text(
        screen, "遊戲暫停", base_font_size * 2, screen_width / 2, screen_height / 4
    )
    resume_button = pygame.Rect(
        (screen_width - base_font_size * 10) // 2,
        screen_height // 2,
        base_font_size * 10,
        base_font_size * 2,
    )
    exit_button = pygame.Rect(
        resume_button.left,
        resume_button.bottom + 10,
        base_font_size * 10,
        base_font_size * 2,
    )
    pygame.draw.rect(screen, BUTTON_COLOR, resume_button, border_radius=10)
    pygame.draw.rect(screen, BUTTON_COLOR, exit_button, border_radius=10)
    draw_text(
        screen,
        "繼續遊戲",
        base_font_size,
        resume_button.centerx,
        resume_button.centery,
        WHITE,
    )
    draw_text(
        screen,
        "退出遊戲",
        base_font_size,
        exit_button.centerx,
        exit_button.centery,
        WHITE,
    )
    pygame.display.flip()
    return resume_button, exit_button


def draw_end_screen(score):
    base_font_size = max(16, screen_width // 48)
    end_bg = (
        load_image("End1", (screen_width, screen_height))
        if score <= 25
        else (
            load_image("End2", (screen_width, screen_height))
            if score < 50
            else load_image("End3", (screen_width, screen_height))
        )
    )
    screen.blit(end_bg, (0, 0))
    draw_info_with_bg(
        screen,
        f"分數: {score}",
        base_font_size * 2,
        screen_width / 2,
        screen_height / 6,
    )
    draw_info_with_bg(
        screen,
        f"本局遊玩時間: {format_seconds(session_play_seconds)}",
        base_font_size,
        screen_width / 2,
        screen_height / 6 + base_font_size * 2.2,
    )
    restart_button = pygame.Rect(
        30,
        screen_height - base_font_size * 4 - 30,
        base_font_size * 10,
        base_font_size * 2,
    )
    exit_button = pygame.Rect(
        30,
        screen_height - base_font_size * 2 - 10,
        base_font_size * 10,
        base_font_size * 2,
    )
    pygame.draw.rect(screen, BUTTON_COLOR, restart_button, border_radius=10)
    pygame.draw.rect(screen, BUTTON_COLOR, exit_button, border_radius=10)
    draw_text(
        screen,
        "返回開始頁面",
        base_font_size,
        restart_button.centerx,
        restart_button.centery,
        WHITE,
    )
    draw_text(
        screen,
        "退出遊戲",
        base_font_size,
        exit_button.centerx,
        exit_button.centery,
        WHITE,
    )
    pygame.display.flip()
    return restart_button, exit_button


# ---------------- 切換/縮放安全處理 ----------------
def _force_redraw_once():
    try:
        if not game_started and not show_rules:
            if show_settings_menu:
                draw_settings_screen_start()
            else:
                screen.blit(
                    leaderboard_bg if show_leaderboard else background_img, (0, 0)
                )
        elif show_rules and not game_started:
            screen.blit(tutorial_images[tutorial_index], (0, 0))
        else:
            screen.blit(ready_bg if game_ready else gameplay_bg, (0, 0))
        pygame.display.update()
    except Exception:
        screen.fill((0, 0, 0))
        pygame.display.update()


def snapshot_dynamic_layout_before_resize():
    # 小碗存中心比；大碗錨在紅框不用存
    for b in completed_bowls:
        if not b.big:
            b._cx_ratio = b.rect.centerx / max(1, screen_width)
            b._cy_ratio = b.rect.centery / max(1, screen_height)


def reflow_dynamic_layout_after_resize():
    for c in customers:
        c.rescale_on_resize()
    for ic in interfering_customers:
        ic.rescale_on_resize()
    for b in completed_bowls:
        b.rescale_on_resize()


def toggle_fullscreen():
    global is_fullscreen, screen, WINDOWED_SIZE, WINDOWED_POS
    import sys
    if sys.platform == "emscripten":
        return
    try:
        snapshot_dynamic_layout_before_resize()
        if is_fullscreen:
            dw, dh = _safe_get_desktop_size()
            if WINDOWED_SIZE is None:
                win_w = max(960, int(dw * 0.80))
                win_h = max(540, int(dh * 0.80))
                WINDOWED_SIZE = (win_w, win_h)
            screen = pygame.display.set_mode(
                WINDOWED_SIZE, pygame.RESIZABLE | pygame.DOUBLEBUF
            )
            if hasattr(pygame.display, "set_window_bordered"):
                pygame.display.set_window_bordered(True)
            if hasattr(pygame.display, "set_window_resizable"):
                pygame.display.set_window_resizable(True)
            if hasattr(pygame.display, "set_window_position"):
                x = (dw - WINDOWED_SIZE[0]) // 2
                y = (dh - WINDOWED_SIZE[1]) // 2
                try:
                    # 有些 pygame 版本：set_window_position(x, y)
                    pygame.display.set_window_position(x, y)
                except TypeError:
                    # pygame-ce 2.5.6 可能是吃一個 tuple
                    pygame.display.set_window_position((x, y))
                WINDOWED_POS = (x, y)
            is_fullscreen = False
        else:
            dw, dh = _safe_get_desktop_size()
            screen = pygame.display.set_mode(
                (dw, dh), pygame.FULLSCREEN | pygame.DOUBLEBUF
            )
            is_fullscreen = True
        pygame.event.pump()
        reload_scaled_assets()
        reflow_dynamic_layout_after_resize()
        _force_redraw_once()
        _toast("全螢幕：開" if is_fullscreen else "視窗化：開（可調整）", 1.2)
    except Exception as e:
        print(f"[Display] 切換失敗：{e}")
        try:
            screen = pygame.display.set_mode(
                (1280, 720), pygame.RESIZABLE | pygame.DOUBLEBUF
            )
            is_fullscreen = False
            reload_scaled_assets()
            reflow_dynamic_layout_after_resize()
            _force_redraw_once()
        except Exception as e2:
            print(f"[Display] 回退失敗：{e2}")


def handle_hotkeys(event):
    # 視窗切換
    if event.key == pygame.K_F11 or (
        event.key == pygame.K_RETURN and (pygame.key.get_mods() & pygame.KMOD_ALT)
    ):
        toggle_fullscreen()
    # 主音量
    elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
        set_volume_delta(-0.05)
    elif event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
        set_volume_delta(+0.05)
    # SFX 個別
    elif event.key == pygame.K_LEFTBRACKET:
        set_sfx_abs(SFX_VOLUME - 0.05)
    elif event.key == pygame.K_RIGHTBRACKET:
        set_sfx_abs(SFX_VOLUME + 0.05)
    # BGM 個別
    elif event.key == pygame.K_SEMICOLON:
        set_music_abs(MUSIC_VOLUME - 0.05)
    elif event.key == pygame.K_QUOTE:
        set_music_abs(MUSIC_VOLUME + 0.05)
    # 靜音 & 截圖
    elif event.key == pygame.K_m:
        toggle_mute()
    elif event.key == pygame.K_F12:
        take_screenshot(screen)


# ---------------- 主迴圈 ----------------
clock = pygame.time.Clock()
running = True


def start_game_from_tutorial():
    global score, session_play_seconds, ready_to_start_game, show_rules, tutorial_index
    score = 0
    session_play_seconds = 0.0
    for name, d in material_data.items():
        d["rect"].topleft = d["init_pos"]
    completed_bowls.clear()
    customers.clear()
    interfering_customers.clear()
    ready_to_start_game = True
    show_rules = False
    tutorial_index = 0


def draw_gameplay_layer():
    screen.blit(ready_bg if game_ready else gameplay_bg, (0, 0))
    pygame.draw.rect(screen, (255, 0, 0), redbox_rect, 4)
    for ic in interfering_customers:
        ic.draw(screen)
    for c in customers:
        c.draw(screen)
    for b in completed_bowls:
        if not b.dragging:
            b.draw(screen)
    screen.blit(trash_img, trash_rect)
    for b in completed_bowls:
        if b.dragging:
            b.draw(screen)
    draw_info_with_bg(
        screen, f"Score: {score}", max(16, screen_width // 48), screen_width - 100, 90
    )
    if not hammer_selected:
        screen.blit(hammer_img, hammer_rect)
    else:
        mx, my = pygame.mouse.get_pos()
        hx = mx - hammer_img.get_width() // 2
        hy = my - hammer_img.get_height() // 2
        screen.blit(hammer_img, (hx, hy))
    for cat, items in categories.items():
        for name in items:
            d = material_data[name]
            screen.blit(d["image"], d["rect"])
    for t, d in extra_topping_data.items():
        screen.blit(d["image"], d["rect"])


async def main():
    global running, score, session_play_seconds, game_ready, show_countdown, timer, ready_to_start_game, customer_respawn_cooldown, practice_customer, highscore, total_play_seconds, game_paused, show_leaderboard, show_settings_menu, show_rules, game_started, tutorial_index, hammer_selected, customer_spawn_timer, interfering_spawn_timer, customers, interfering_customers, completed_bowls, SLIDERS, MUTE_BTN_RECT, MUTED, MASTER_VOLUME, MUSIC_VOLUME, SFX_VOLUME, background_img, logo_img, leaderboard_bg, tutorial_images, gameplay_bg, ready_bg, trash_img, trash_rect, empty_bowl_img, hammer_img, hammer_rect, redbox_rect, BIG_BOWL_SIZE, categories, material_size_map, material_data, extra_toppings, extra_topping_data, extra_topping_positions, customer_positions, interfering_positions, screenshot_toast_time, screenshot_toast_text
    
    print("阿翰牛肉麵 - 遊戲正式啟動！")
    
    # 網頁版需在 main 內播放音效，才能正確觸發「點擊解鎖音效」機制
    apply_volumes(False)
    play_bgm("bgm", loop=-1, volume=None, fade_ms=0)

    while running:
        dt = clock.tick(60) / 1000
        await asyncio.sleep(0)

        # ---------- 起始/排行榜/設定 ----------
        if not game_started and not show_rules:
            highscore, total_play_seconds = load_stats()

            # (A) 首頁
            if not show_leaderboard and not show_settings_menu:
                waiting = True
                while waiting:
                    start_button, lb_button, exit_button, settings_button = draw_start_screen()
                    for event in pygame.event.get():
                        if event.type == pygame.QUIT:
                            running = False
                            waiting = False
                        elif event.type == pygame.KEYDOWN:
                            if event.key == pygame.K_q:
                                running = False
                                waiting = False
                            handle_hotkeys(event)
                        elif event.type == pygame.VIDEORESIZE:
                            snapshot_dynamic_layout_before_resize()
                            reload_scaled_assets()
                            reflow_dynamic_layout_after_resize()
                            start_button, lb_button, exit_button, settings_button = (
                                draw_start_screen()
                            )
                        elif event.type == pygame.MOUSEBUTTONDOWN:
                            if start_button.collidepoint(event.pos):
                                safe_play(click_sound)
                                show_rules = True
                                waiting = False
                            elif lb_button.collidepoint(event.pos):
                                safe_play(click_sound)
                                show_leaderboard = True
                                waiting = False
                            elif settings_button.collidepoint(event.pos):
                                safe_play(click_sound)
                                show_settings_menu = True
                                waiting = False
                            elif exit_button.collidepoint(event.pos):
                                safe_play(click_sound)
                                running = False
                                waiting = False
                    draw_screenshot_toast(dt)
                    pygame.display.flip()
                    pygame.time.delay(10)
                    await asyncio.sleep(0)

            # (B) 排行榜
            elif show_leaderboard and not show_settings_menu:
                waiting = True
                while waiting:
                    back_btn = draw_leaderboard_screen()
                    for event in pygame.event.get():
                        if event.type == pygame.QUIT:
                            running = False
                            waiting = False
                        elif event.type == pygame.KEYDOWN:
                            if event.key == pygame.K_q:
                                running = False
                                waiting = False
                            handle_hotkeys(event)
                        elif event.type == pygame.VIDEORESIZE:
                            snapshot_dynamic_layout_before_resize()
                            reload_scaled_assets()
                            reflow_dynamic_layout_after_resize()
                            back_btn = draw_leaderboard_screen()
                        elif event.type == pygame.MOUSEBUTTONDOWN:
                            if back_btn.collidepoint(event.pos):
                                safe_play(click_sound)
                                show_leaderboard = False
                                waiting = False
                    draw_screenshot_toast(dt)
                    pygame.display.flip()
                    pygame.time.delay(10)
                    await asyncio.sleep(0)

            # (C) 設定頁（可拖曳滑桿）
            elif show_settings_menu:
                waiting = True
                local_drag = None  # 暫存拖曳的滑桿名
                back_btn = draw_settings_screen_start()
                while waiting:
                    for event in pygame.event.get():
                        if event.type == pygame.QUIT:
                            running = False
                            waiting = False
                        elif event.type == pygame.KEYDOWN:
                            if event.key == pygame.K_q:
                                running = False
                                waiting = False
                            elif event.key == pygame.K_ESCAPE:
                                show_settings_menu = False
                                waiting = False
                            handle_hotkeys(event)

                        elif event.type == pygame.VIDEORESIZE:
                            snapshot_dynamic_layout_before_resize()
                            reload_scaled_assets()
                            reflow_dynamic_layout_after_resize()
                            back_btn = draw_settings_screen_start()

                        elif event.type == pygame.MOUSEBUTTONDOWN:
                            # 檢查三條滑桿
                            for name, geo in SLIDERS.items():
                                if geo["knob"].collidepoint(event.pos) or geo[
                                    "track"
                                ].collidepoint(event.pos):
                                    local_drag = name
                                    v = _slider_x_to_value(geo["track"], event.pos[0])
                                    if name == "master":
                                        set_master_abs(v)
                                    elif name == "music":
                                        set_music_abs(v)
                                    elif name == "sfx":
                                        set_sfx_abs(v)
                                    break
                            else:
                                # 靜音或返回
                                if MUTE_BTN_RECT and MUTE_BTN_RECT.collidepoint(event.pos):
                                    toggle_mute()
                                elif back_btn.collidepoint(event.pos):
                                    safe_play(click_sound)
                                    show_settings_menu = False
                                    waiting = False

                        elif event.type == pygame.MOUSEMOTION:
                            if local_drag:
                                geo = SLIDERS.get(local_drag)
                                if geo:
                                    v = _slider_x_to_value(geo["track"], event.pos[0])
                                    if local_drag == "master":
                                        set_master_abs(v)
                                    elif local_drag == "music":
                                        set_music_abs(v)
                                    elif local_drag == "sfx":
                                        set_sfx_abs(v)

                        elif event.type == pygame.MOUSEBUTTONUP:
                            local_drag = None

                    # 每幀重畫設定頁（滑桿即時更新）
                    back_btn = draw_settings_screen_start()
                    draw_screenshot_toast(dt)
                    pygame.display.flip()
                    pygame.time.delay(10)
                    await asyncio.sleep(0)

        # ---------- 教學 ----------
        if show_rules and not game_started and running:
            waiting = True
            while waiting:
                # 教學頁面
                base_font_size = max(16, screen_width // 48)
                screen.blit(tutorial_images[tutorial_index], (0, 0))
                btns = {}
                if tutorial_index < len(tutorial_images) - 1:
                    next_btn = pygame.Rect(
                        (screen_width - base_font_size * 10) // 2,
                        screen_height - base_font_size * 4,
                        base_font_size * 10,
                        base_font_size * 2,
                    )
                    pygame.draw.rect(screen, BUTTON_COLOR, next_btn, border_radius=10)
                    draw_text(
                        screen,
                        "下一步",
                        base_font_size,
                        next_btn.centerx,
                        next_btn.centery,
                        WHITE,
                    )
                    btns["next"] = next_btn
                else:
                    start_btn = pygame.Rect(
                        (screen_width - base_font_size * 10) // 2,
                        screen_height - base_font_size * 4,
                        base_font_size * 10,
                        base_font_size * 2,
                    )
                    restart_btn = pygame.Rect(
                        (screen_width - base_font_size * 10) // 2,
                        screen_height - base_font_size * 7,
                        base_font_size * 10,
                        base_font_size * 2,
                    )
                    pygame.draw.rect(screen, BUTTON_COLOR, start_btn, border_radius=10)
                    pygame.draw.rect(screen, BUTTON_COLOR, restart_btn, border_radius=10)
                    draw_text(
                        screen,
                        "開始遊戲",
                        base_font_size,
                        start_btn.centerx,
                        start_btn.centery,
                        WHITE,
                    )
                    draw_text(
                        screen,
                        "回到第一步",
                        base_font_size,
                        restart_btn.centerx,
                        restart_btn.centery,
                        WHITE,
                    )
                    btns["start"] = start_btn
                    btns["restart"] = restart_btn

                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                        waiting = False
                    elif event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_q:
                            running = False
                            waiting = False
                        handle_hotkeys(event)
                    elif event.type == pygame.VIDEORESIZE:
                        snapshot_dynamic_layout_before_resize()
                        reload_scaled_assets()
                        reflow_dynamic_layout_after_resize()
                    elif event.type == pygame.MOUSEBUTTONDOWN:
                        if "next" in btns and btns["next"].collidepoint(event.pos):
                            safe_play(click_sound)
                            tutorial_index = min(
                                tutorial_index + 1, len(tutorial_images) - 1
                            )
                        elif "restart" in btns and btns["restart"].collidepoint(event.pos):
                            safe_play(click_sound)
                            tutorial_index = 0
                        elif "start" in btns and btns["start"].collidepoint(event.pos):
                            safe_play(click_sound)
                            start_game_from_tutorial()
                            waiting = False
                draw_screenshot_toast(dt)
                pygame.display.flip()
                pygame.time.delay(10)
                await asyncio.sleep(0)

        # ---------- 進入正式 ----------
        if running:
            if ready_to_start_game:
                game_started = True
                game_ready = True
                show_countdown = False
                timer = game_time
                ready_to_start_game = False
                customer_respawn_cooldown = 0.0
                practice_customer = Customer((0.51, 0.63), practice=True)
                customers.append(practice_customer)

            if game_started:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                    elif event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            game_paused = not game_paused
                            pygame.mouse.set_visible(True if game_paused else not hammer_selected)
                            pygame.mouse.set_visible(True if game_paused else not hammer_selected)
                        elif event.key == pygame.K_q:
                            running = False
                        handle_hotkeys(event)
                    elif event.type == pygame.VIDEORESIZE:
                        snapshot_dynamic_layout_before_resize()
                        reload_scaled_assets()
                        reflow_dynamic_layout_after_resize()
                    elif event.type == pygame.MOUSEBUTTONDOWN:
                        if hammer_rect.collidepoint(event.pos):
                            safe_play(click_sound)
                            hammer_selected = not hammer_selected
                            pygame.mouse.set_visible(not hammer_selected)
                        elif hammer_selected:
                            for ic in interfering_customers:
                                if ic.active and pygame.Rect(
                                    ic.pos, ic.normal_image.get_size()
                                ).collidepoint(event.pos):
                                    ic.get_hit()
                                    safe_play(hammer_hit_sound)
                                    hammer_selected = False
                                    pygame.mouse.set_visible(True)
                                    break
                        if game_paused:
                            resume_button, exit_btn = draw_pause_menu()
                            if resume_button.collidepoint(event.pos):
                                safe_play(click_sound)
                                game_paused = False
                            elif exit_btn.collidepoint(event.pos):
                                safe_play(click_sound)
                                running = False
                        else:
                            dragging = False
                            for b in completed_bowls:
                                if b.completed and b.rect.collidepoint(event.pos):
                                    b.dragging = True
                                    b.offset = (
                                        b.rect.x - event.pos[0],
                                        b.rect.y - event.pos[1],
                                    )
                                    dragging = True
                                    break
                            if not dragging:
                                if redbox_rect.collidepoint(event.pos):
                                    if not completed_bowls:
                                        nb = CompletedBowl(
                                            redbox_rect.topleft, slot_index=0, big=True
                                        )
                                        nb.rect = pygame.Rect(
                                            redbox_rect.topleft, BIG_BOWL_SIZE
                                        )
                                        nb.image = load_image("empty_bowl", BIG_BOWL_SIZE)
                                        completed_bowls.append(nb)
                                else:
                                    for name, d in material_data.items():
                                        if d["rect"].collidepoint(event.pos):
                                            cat = d["category"]
                                            for b in completed_bowls:
                                                if not b.completed:
                                                    b.add_material(name, cat)
                                                    break
                                for b in completed_bowls:
                                    if b.completed and b.rect.collidepoint(event.pos):
                                        b.dragging = True
                                        b.offset = (
                                            b.rect.x - event.pos[0],
                                            b.rect.y - event.pos[1],
                                        )
                                for t, d in extra_topping_data.items():
                                    if d["rect"].collidepoint(event.pos):
                                        for b in reversed(completed_bowls):
                                            if (
                                                b.completed
                                                and t not in b.extra_toppings
                                                and len(b.extra_toppings) < 2
                                            ):
                                                b.extra_toppings.append(t)
                                                suffix = "_" + "_".join(
                                                    sorted(b.extra_toppings)
                                                )
                                                img = f"{b.selected_noodle}_{b.selected_beef}_{b.selected_soup}{suffix}"
                                                target = (
                                                    BIG_BOWL_SIZE
                                                    if b.big
                                                    else (
                                                        int(screen_width * 0.08),
                                                        int(screen_width * 0.08),
                                                    )
                                                )
                                                try:
                                                    b.image = load_image(img, target)
                                                    b.rect.size = target
                                                except FileNotFoundError:
                                                    pass
                                                break
                    elif event.type == pygame.MOUSEMOTION:
                        for b in completed_bowls:
                            if b.dragging:
                                x, y = event.pos
                                dx, dy = b.offset
                                b.rect.topleft = (x + dx, y + dy)
                    elif event.type == pygame.MOUSEBUTTONUP:
                        for b in completed_bowls[:]:
                            if b.dragging:
                                b.dragging = False
                                if trash_rect.colliderect(b.rect):
                                    completed_bowls.remove(b)
                                else:
                                    for c in customers:
                                        if c.waiting:
                                            catch = pygame.Rect(
                                                c.pos[0] + int(c.img_width * 0.15),
                                                c.pos[1] + int(c.img_height * 0.2),
                                                int(c.img_width * 0.7),
                                                int(c.img_height * 0.4),
                                            )
                                            if catch.colliderect(b.rect):
                                                basic = (
                                                    b.selected_noodle == c.noodle
                                                    and b.selected_beef == c.beef
                                                    and b.selected_soup == c.soup
                                                )
                                                topping_ok = all(
                                                    t in b.extra_toppings
                                                    for t in c.extra_toppings
                                                )
                                                if basic and topping_ok:
                                                    c.bowls_needed -= 1
                                                    completed_bowls.remove(b)
                                                    if not getattr(c, "practice", False):
                                                        safe_play(score_sound)
                                                        score += 5
                                                    else:
                                                        safe_play(click_sound)
                                                    if c.bowls_needed <= 0 and (
                                                        c in customers
                                                    ):
                                                        customers.remove(c)
                                                        if getattr(c, "practice", False):
                                                            show_countdown = True
                                                            game_ready = False
                                                            timer = countdown_start
                                                        else:
                                                            customer_respawn_cooldown = 4.0

                                                else:
                                                    # ❌ 麵交上去但不對：播放錯誤音效
                                                    safe_play(error_sound)


                # --- 畫面 ---
                draw_gameplay_layer()
                draw_screenshot_toast(dt)

                # --- 計時/生成 ---
                if game_paused:
                    draw_pause_menu()
                else:
                    if show_countdown:
                        timer -= dt
                        draw_info_with_bg(
                            screen,
                            f"倒數: {int(timer)+1}",
                            max(16, screen_width // 24),
                            screen_width / 2,
                            screen_height / 2,
                        )
                        if timer <= 0:
                            show_countdown = False
                            timer = game_time
                            await asyncio.sleep(0) # 轉場瞬間保留呼吸空間
                            play_bgm("bgm_fast", loop=-1, volume=None)
                    else:
                        if not game_ready:
                            session_play_seconds += dt
                            active_interferers = any(
                                ic.active for ic in interfering_customers
                            )
                            timer -= dt * (2 if active_interferers else 1)
                            draw_info_with_bg(
                                screen,
                                f"剩餘時間: {int(timer)}",
                                max(16, screen_width // 48),
                                screen_width - 130,
                                30,
                            )
                            if active_interferers:
                                draw_info_with_bg(
                                    screen,
                                    "干擾中！時間加倍流失",
                                    max(16, screen_width // 48),
                                    screen_width / 2,
                                    50,
                                    (200, 0, 0),
                                )
                            # 生成顧客
                            customer_spawn_timer += dt
                            try:
                                customer_respawn_cooldown = max(
                                    0.0, customer_respawn_cooldown - dt
                                )
                            except NameError:
                                customer_respawn_cooldown = 0.0
                            if (
                                (len(customers) < 3)
                                and (customer_respawn_cooldown <= 0.0)
                                and (customer_spawn_timer >= 0.5)
                            ):
                                avail = [
                                    p
                                    for p in customer_positions
                                    if all(c.position_ratio != p for c in customers)
                                ]
                                if avail:
                                    customers.append(Customer(random.choice(avail)))
                                    customer_spawn_timer = 0.0
                            # 更新顧客
                            for c in customers[:]:
                                c.update(dt)
                                if (not c.waiting) or (c.bowls_needed <= 0):
                                    customers.remove(c)
                                    customer_respawn_cooldown = 4.0
                                    customer_spawn_timer = 0.0
                            # 干擾客
                            interfering_spawn_timer += dt
                            for ic in interfering_customers:
                                ic.update(dt)
                            interfering_customers = [
                                ic for ic in interfering_customers if ic.active
                            ]
                            if (interfering_spawn_timer >= 8.0) and len(
                                interfering_customers
                            ) < 1:
                                FIXH = int(screen_height * 0.36)
                                FIXW = int(screen_width * 0.18)
                                avail = []
                                for p in interfering_positions:
                                    newr = pygame.Rect(
                                        int(screen_width * p[0]),
                                        int(screen_height * p[1] - FIXH),
                                        FIXW,
                                        FIXH,
                                    )
                                    overlap = False
                                    for c in customers:
                                        cr = pygame.Rect(
                                            int(screen_width * c.position_ratio[0]),
                                            int(screen_height * c.position_ratio[1] - FIXH),
                                            FIXW,
                                            FIXH,
                                        )
                                        if newr.colliderect(cr):
                                            overlap = True
                                            break
                                    if not overlap:
                                        for ic in interfering_customers:
                                            ir = pygame.Rect(
                                                int(screen_width * ic.position_ratio[0]),
                                                int(
                                                    screen_height * ic.position_ratio[1]
                                                    - FIXH
                                                ),
                                                FIXW,
                                                FIXH,
                                            )
                                            if newr.colliderect(ir):
                                                overlap = True
                                                break
                                    if not overlap:
                                        avail.append(p)
                                if avail:
                                    interfering_customers.append(
                                        InterferingCustomer(random.choice(avail))
                                    )
                                    interfering_spawn_timer = 0.0
                                    safe_play(interferer_spawn_sound)
                        else:
                            draw_info_with_bg(
                                screen,
                                "先為前方練習客人上餐，遊戲才會開始！",
                                max(16, screen_width // 48),
                                screen_width / 2,
                                50,
                            )

                # --- 結束 ---
                if not show_countdown and not game_ready and timer <= 0:
                    try:
                        # 分數 > 25 用另一種結算音效
                        if score > 25 and end_good_sound:
                            safe_play(end_good_sound)
                        elif end_sound:
                            safe_play(end_sound)
                        pygame.mixer.music.stop()
                    except Exception:
                        pass

                    update_highscore_in_db(score)
                    add_play_seconds_to_db(session_play_seconds)
                    add_session(score, session_play_seconds)
                    pygame.mouse.set_visible(True)
                    waiting = True
                    while waiting:
                        restart_btn, exit_btn = draw_end_screen(score)
                        for event in pygame.event.get():
                            if event.type == pygame.QUIT:
                                running = False
                                waiting = False
                            elif event.type == pygame.KEYDOWN:
                                if event.key == pygame.K_q:
                                    running = False
                                    waiting = False
                                handle_hotkeys(event)
                            elif event.type == pygame.VIDEORESIZE:
                                snapshot_dynamic_layout_before_resize()
                                reload_scaled_assets()
                                reflow_dynamic_layout_after_resize()
                                restart_btn, exit_btn = draw_end_screen(score)
                            elif event.type == pygame.MOUSEBUTTONDOWN:
                                if restart_btn.collidepoint(event.pos):
                                    safe_play(click_sound)
                                    game_started = False
                                    play_bgm("bgm", loop=-1, volume=0.85)
                                    show_countdown = False
                                    timer = 0
                                    score = 0
                                    waiting = False
                                elif exit_btn.collidepoint(event.pos):
                                    safe_play(click_sound)
                                    running = False
                                    waiting = False
                        draw_screenshot_toast(dt)
                        pygame.display.flip()
                        pygame.time.delay(10)
                        await asyncio.sleep(0)

                if score > highscore:
                    highscore = score
                    update_highscore_in_db(highscore)

                pygame.display.flip()

    # 當 main 迴圈結束後才關閉
    pygame.quit()
    sys.exit()

if __name__ == '__main__':
    asyncio.run(main())
