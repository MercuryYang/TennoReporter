"""
TennoReporter — Warframe 世界状态监控
支持两种运行模式：
  - GUI 模式（本地）：python tenno_reporter.py
  - 无头云端模式（Railway）：python tenno_reporter.py --headless
"""

import threading
import requests
import json
import time
import sys
from datetime import datetime, timezone
from pathlib import Path

# ══════════════════════════════════════════════
#  配置（可通过环境变量覆盖，适配 Railway）
# ══════════════════════════════════════════════
import os

WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/yourdiscordbotabcdefg"
)
CHECK_EVERY  = int(os.environ.get("CHECK_INTERVAL", "60"))
STATE_FILE   = os.environ.get("STATE_FILE", "state.json")

RARE_KEYWORDS = ["OrokinCatalyst", "OrokinReactor", "Forma",
                 "AuraForma", "Riven", "AladCoordinate", "SentinelWeaponBP"]
TIER_NAME     = {"VoidT1":"Lith","VoidT2":"Meso","VoidT3":"Neo",
                 "VoidT4":"Axi","VoidT5":"Requiem","VoidT6":"Omnia"}
FACTION_NAME  = {"FC_CORPUS":"星团","FC_GRINEER":"基尼尔","FC_INFESTATION":"感染体"}

# ══════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════
def now_ms():
    return int(time.time() * 1000)

def to_dt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%M UTC")

def remaining(ms):
    diff = (ms - now_ms()) / 1000
    if diff <= 0:
        return "已过期"
    h, m = int(diff // 3600), int((diff % 3600) // 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"

def load_state() -> dict:
    if Path(STATE_FILE).exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def purge_old(state: dict):
    """清理 3 天前的状态记录，防止文件无限增长"""
    cutoff = time.time() - 3 * 86400
    stale = [k for k, v in state.items() if v.get("ts", 0) < cutoff]
    for k in stale:
        del state[k]


# ══════════════════════════════════════════════
#  Discord 推送
# ══════════════════════════════════════════════
def post_discord(embed: dict, log_fn=None):
    try:
        r = requests.post(
            WEBHOOK_URL,
            json={"embeds": [embed]},
            timeout=10,
            headers={"Content-Type": "application/json"},
        )
        if r.status_code in (200, 204):
            if log_fn:
                log_fn(f"Discord 推送成功：{embed.get('title', '')}", "ok")
        elif r.status_code == 429:
            retry_after = r.json().get("retry_after", 5)
            if log_fn:
                log_fn(f"Discord 限流，等待 {retry_after}s", "warn")
            time.sleep(float(retry_after))
            post_discord(embed, log_fn)  # 重试一次
        else:
            if log_fn:
                log_fn(f"Discord 推送失败 HTTP {r.status_code}：{r.text[:120]}", "err")
    except requests.exceptions.ConnectionError as e:
        if log_fn:
            log_fn(f"网络连接失败（Discord）：{e}", "err")
    except requests.exceptions.Timeout:
        if log_fn:
            log_fn("Discord 请求超时", "err")
    except Exception as e:
        if log_fn:
            log_fn(f"推送异常：{e}", "err")
    time.sleep(0.6)   # 避免触发 Discord rate limit (50 req/s global)

# ══════════════════════════════════════════════
#  API 子端点请求（warframestat.us 解析版）
# ══════════════════════════════════════════════
BASE = "https://api.warframestat.us/pc"

def _get(path: str, log_fn=None) -> any:
    """GET BASE/path，失败返回 None"""
    url = f"{BASE}/{path}"
    try:
        r = requests.get(url, timeout=15,
                         headers={"Accept-Language": "zh-hans"})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        if log_fn:
            log_fn(f"请求失败 {path}: {e}", "err")
        return None


def _parse_iso_ms(s: str) -> int:
    """ISO 时间字符串 → 毫秒时间戳"""
    if not s:
        return 0
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


# ── 稀有入侵判断（解析版字段名）──
RARE_REWARD_TYPES = ["OrokinCatalyst", "OrokinReactor", "Forma",
                     "AuraForma", "Riven", "AladCoordinate", "SentinelWeaponBP"]

def _reward_is_rare(reward_obj) -> bool:
    """
    warframestat.us invasion reward 结构:
    {"asString": "...", "items": [...], "credits": 0, "thumbnail": "...", "color": 0}
    items 列表中的元素: {"uniqueName": "...", "count": 1, "type": "..."}
    """
    if not isinstance(reward_obj, dict):
        return False
    items = reward_obj.get("items", [])
    if not items:
        # 也有部分 API 直接用 "countedItems"
        items = reward_obj.get("countedItems", [])
    for it in items:
        name = it.get("uniqueName", "") or it.get("type", "")
        if any(kw in name for kw in RARE_REWARD_TYPES):
            return True
    return False

def _fmt_reward_parsed(reward_obj) -> str:
    if not isinstance(reward_obj, dict):
        return "无"
    # 优先用 asString（已格式化）
    s = reward_obj.get("asString", "").strip()
    if s:
        return s
    items = reward_obj.get("items", reward_obj.get("countedItems", []))
    if not items:
        return "无"
    return "  ".join(
        f"{it.get('type', it.get('uniqueName','?')).split('/')[-1]} x{it.get('count', 1)}"
        for it in items
    )


# ══════════════════════════════════════════════
#  数据处理（各子端点，GUI/Cloud 共用）
# ══════════════════════════════════════════════
def fetch_traders(log_fn=None) -> list:
    """
    GET /pc/voidTraders → list
    字段: id, character, location, active, activation(ISO), expiry(ISO),
          startString, endString, inventory(list)
    """
    data = _get("voidTraders", log_fn)
    if not isinstance(data, list):
        # 部分版本返回单对象
        data = [data] if isinstance(data, dict) else []

    traders = []
    cur = now_ms()
    for t in data:
        if not t:
            continue
        exp_ms = _parse_iso_ms(t.get("expiry", ""))
        act_ms = _parse_iso_ms(t.get("activation", ""))
        if exp_ms and cur > exp_ms:
            continue
        traders.append({
            "_active":       t.get("active", False),
            "name":          t.get("character", "Baro Ki'Teer"),
            "node":          t.get("location", "未知"),
            "remain":        t.get("endString") or remaining(exp_ms),
            "arrive_remain": t.get("startString") or remaining(act_ms),
            "arrive_str":    to_dt(act_ms) if act_ms else "—",
            "expiry_str":    to_dt(exp_ms) if exp_ms else "—",
            "_oid":          t.get("id", ""),
            "_act_ms":       act_ms,
            "_exp_ms":       exp_ms,
        })
    return traders


def fetch_invasions(log_fn=None) -> list:
    """
    GET /pc/invasions → list
    字段: id, node, desc, attackingFaction, defendingFaction,
          attacker{reward{...}}, defender{reward{...}},
          completed(bool), count(int), goal(int), eta
    """
    data = _get("invasions", log_fn)
    if not isinstance(data, list):
        return []

    invasions = []
    for inv in data:
        if inv.get("completed", False):
            continue

        atk_reward = inv.get("attacker", {}).get("reward", {})
        def_reward = inv.get("defender", {}).get("reward", {})

        if not _reward_is_rare(atk_reward) and not _reward_is_rare(def_reward):
            continue

        count    = abs(inv.get("count", 0))
        goal     = max(inv.get("goal", 1), 1)
        atk_fac  = inv.get("attackingFaction", "")
        def_fac  = inv.get("defendingFaction", "")

        invasions.append({
            "node":     inv.get("node", "未知"),
            "atk":      FACTION_NAME.get(atk_fac, atk_fac),
            "def_":     FACTION_NAME.get(def_fac, def_fac),
            "atk_r":    _fmt_reward_parsed(atk_reward),
            "def_r":    _fmt_reward_parsed(def_reward),
            "progress": count / goal * 100,
            "_oid":     inv.get("id", ""),
        })
    return invasions


def fetch_fissures(log_fn=None) -> list:
    """
    GET /pc/fissures → list，只返回关注节点的钢铁裂缝。

    warframestat.us 解析版中 node 字段为本地化字符串，例如：
      "Mot (Void)"、"Ani (Void)"、"Olympus (Mars)"
    使用关键词匹配（节点名部分）来过滤，兼容中英文 API 返回。
    """
    data = _get("fissures", log_fn)
    if not isinstance(data, list):
        return []

    # ── 关注节点：节点名关键词 → 显示标签 ──
    # key 为节点名中的唯一关键词（忽略大小写），value 为显示名
    WATCHED_NODES = {
        "mot":       "Mot (虚空)",
        "ani":       "Ani (虚空)",
        "olympus":   "Olympus (火星)",
        "stephano":  "Stephano (天王星)",
        "kappa":     "Kappa (冥神星)",
    }

    fissures = []
    cur = now_ms()
    for m in data:
        if not m.get("isHard", False):
            continue
        if not m.get("active", True):
            continue

        node_raw = m.get("node", "")
        node_lower = node_raw.lower()

        # 只保留关注节点
        matched_label = None
        for keyword, label in WATCHED_NODES.items():
            if keyword in node_lower:
                matched_label = label
                break
        if matched_label is None:
            continue

        exp_ms = _parse_iso_ms(m.get("expiry", ""))
        if exp_ms and cur > exp_ms:
            continue

        fissures.append({
            "node_label": matched_label,
            "tier":       m.get("tier", m.get("tierNum", "")),
            "mtype":      m.get("missionType", ""),
            "remain":     m.get("eta") or remaining(exp_ms),
            "expiry":     to_dt(exp_ms) if exp_ms else "—",
            "_oid":       m.get("id", ""),
        })
    return fissures


def process_data(log_fn=None):
    """并行拉取三个子端点，返回 (traders, invasions, fissures)"""
    results = {}

    def _fetch(key, fn):
        results[key] = fn(log_fn)

    threads = [
        threading.Thread(target=_fetch, args=("traders",  lambda l: fetch_traders(l))),
        threading.Thread(target=_fetch, args=("invasions", lambda l: fetch_invasions(l))),
        threading.Thread(target=_fetch, args=("fissures",  lambda l: fetch_fissures(l))),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    return (
        results.get("traders", []),
        results.get("invasions", []),
        results.get("fissures", []),
    )


def fetch_weather() -> list:
    """从 warframestat.us 各子端点获取天气（独立函数，不依赖任何类）"""
    weather = []
    try:
        ws = _get("", None)   # GET /pc/ 返回完整 worldstate
        if not isinstance(ws, dict):
            return weather
    except Exception as e:
        print(f"[WARN] 天气数据获取失败: {e}")
        return weather

    def _tl(obj):
        tl = obj.get("timeLeft", "")
        if tl:
            return tl
        exp = obj.get("expiry", "")
        if exp:
            try:
                dt   = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                diff = int((dt - datetime.now(timezone.utc)).total_seconds())
                if diff <= 0:
                    return "已过期"
                h, m = diff // 3600, (diff % 3600) // 60
                return f"{h}h {m:02d}m" if h else f"{m}m"
            except Exception:
                pass
        return "—"

    def _exp(obj):
        exp = obj.get("expiry", "—")
        return exp[:16].replace("T", " ") if len(exp) > 10 else exp

    mappings = [
        ("earthCycle",   "地球",     lambda d: "白天 ☀" if d.get("isDay", True) else "夜晚 🌙",
                                     lambda d: "夜晚 🌙" if d.get("isDay", True) else "白天 ☀"),
        ("cetusCycle",   "地球平原", lambda d: "白天 ☀" if d.get("isDay", True) else "夜晚 🌙",
                                     lambda d: "夜晚 🌙" if d.get("isDay", True) else "白天 ☀"),
        ("vallisCycle",  "金星",     lambda d: "温暖 ☀" if d.get("isWarm", True) else "寒冷 ❄",
                                     lambda d: "寒冷 ❄" if d.get("isWarm", True) else "温暖 ☀"),
    ]
    for key, planet, state_fn, next_fn in mappings:
        obj = ws.get(key, {})
        if obj:
            weather.append({
                "planet":     planet,
                "state":      state_fn(obj),
                "next_state": next_fn(obj),
                "remain":     _tl(obj),
                "expiry":     _exp(obj),
            })

    cambion = ws.get("cambionCycle", {})
    if cambion:
        s  = cambion.get("state", "fass")
        sm = {"fass": "Fass 白昼 🔥", "vome": "Vome 夜晚 ❄"}
        nm = {"fass": "Vome 夜晚 ❄", "vome": "Fass 白昼 🔥"}
        weather.append({
            "planet":     "火星",
            "state":      sm.get(s, s),
            "next_state": nm.get(s, ""),
            "remain":     _tl(cambion),
            "expiry":     _exp(cambion),
        })

    return weather


# ══════════════════════════════════════════════
#  Discord 推送逻辑（纯函数，无 self 依赖）
# ══════════════════════════════════════════════
def do_discord_notifications(traders, invasions, fissures, state: dict, log_fn=print):
    """
    执行所有 Discord 推送并更新 state（in-place）。
    state 必须在调用前 load_state()，调用后 save_state()。
    """
    cur = now_ms()

    # ─── 虚空商人 ───
    for t in traders:
        oid       = t["_oid"]
        act       = t["_act_ms"]
        pre_key   = f"vt_pre_{oid}"
        arr_key   = f"vt_arrive_{oid}"

        # 提前 3 天预告
        if 0 < (act - cur) / 1000 <= 259200 and pre_key not in state:
            post_discord({
                "title":       "🛸 虚空商人提前预告！",
                "description": f"**{t['name']}** 将在 3 天内抵达 **{t['node']}**",
                "color":       0xFFA500,
                "fields": [
                    {"name": "到达",   "value": t["arrive_str"],    "inline": True},
                    {"name": "倒计时", "value": t["arrive_remain"], "inline": True},
                    {"name": "离开",   "value": t["expiry_str"],    "inline": True},
                ],
                "footer":    {"text": "TennoReporter"},
                "timestamp": datetime.utcnow().isoformat(),
            }, log_fn)
            state[pre_key] = {"ts": time.time()}

        # 到达通知
        if cur >= act and arr_key not in state:
            post_discord({
                "title":       "🛸 虚空商人已到达！",
                "description": f"**{t['name']}** 已抵达 **{t['node']}**",
                "color":       0xFFD700,
                "fields": [
                    {"name": "剩余", "value": t["remain"],      "inline": True},
                    {"name": "离开", "value": t["expiry_str"],  "inline": True},
                ],
                "footer":    {"text": "TennoReporter"},
                "timestamp": datetime.utcnow().isoformat(),
            }, log_fn)
            state[arr_key] = {"ts": time.time()}

    # ─── 稀有入侵 ───
    for inv in invasions:
        oid = inv["_oid"]
        if oid and oid not in state:
            post_discord({
                "title":       "⚠️ 稀有入侵任务！",
                "description": f"**{inv['node']}** — {inv['atk']} ▶ {inv['def_']}",
                "color":       0xE74C3C,
                "fields": [
                    {"name": "进攻奖励", "value": inv["atk_r"],            "inline": True},
                    {"name": "防守奖励", "value": inv["def_r"],            "inline": True},
                    {"name": "进度",     "value": f"{inv['progress']:.1f}%", "inline": False},
                ],
                "footer":    {"text": "TennoReporter"},
                "timestamp": datetime.utcnow().isoformat(),
            }, log_fn)
            state[oid] = {"ts": time.time()}

    # ─── 钢铁裂缝 ───
    # 有新裂缝 → 推送全部当前裂缝；无新裂缝 → 不重复推送
    new_fissures = [fs for fs in fissures if fs["_oid"] and fs["_oid"] not in state]
    if new_fissures:
        for fs in fissures:
            post_discord({
                "title":       "🌀 钢铁模式虚空裂缝（更新）",
                "description": f"**{fs['node_label']}** — {fs['tier']} 裂缝",
                "color":       0x8E44AD,
                "fields": [
                    {"name": "🎯 任务", "value": fs["mtype"],   "inline": True},
                    {"name": "⌛ 剩余", "value": fs["remain"],  "inline": True},
                    {"name": "📅 到期", "value": fs["expiry"],  "inline": True},
                ],
                "footer":    {"text": "TennoReporter"},
                "timestamp": datetime.utcnow().isoformat(),
            }, log_fn)
            if fs["_oid"]:
                state[fs["_oid"]] = {"ts": time.time()}
        log_fn(f"裂缝更新推送，共 {len(fissures)} 条", "ok")

    # ─── 天气（仅地球昼/夜）───
    try:
        earth_weather = [w for w in fetch_weather() if w["planet"] == "地球"]
    except Exception as e:
        log_fn(f"天气获取失败: {e}", "err")
        earth_weather = []

    for w in earth_weather:
        key = f"weather_{w['planet']}_{w['state']}_{w['expiry']}"
        if key in state:
            continue
        embed = {
            "title":  "🌦 地球天气更新",
            "color":  0x3A86FF,
            "fields": [
                {"name": "当前状态", "value": w["state"],  "inline": True},
                {"name": "剩余时间", "value": w["remain"], "inline": True},
                {"name": "切换时间", "value": w["expiry"], "inline": False},
            ],
            "footer":    {"text": "TennoReporter · 天气推送"},
            "timestamp": datetime.utcnow().isoformat(),
        }
        if w.get("next_state"):
            embed["fields"].append(
                {"name": "下一状态", "value": w["next_state"], "inline": True}
            )
        post_discord(embed, log_fn)
        state[key] = {"ts": time.time()}


# ══════════════════════════════════════════════
#  无头云端运行器（Railway 部署使用）
# ══════════════════════════════════════════════
class HeadlessReporter:
    def __init__(self):
        self.state = load_state()

    def log(self, msg, tag="info"):
        level = tag.upper().ljust(4)
        print(f"[{datetime.now().strftime('%H:%M:%S')}][{level}] {msg}", flush=True)

    def run_once(self):
        self.log("轮询 API...")
        traders, invasions, fissures = process_data(self.log)
        self.log(
            f"刷新成功 — 商人:{len(traders)}  稀有入侵:{len(invasions)}  "
            f"钢铁裂缝:{len(fissures)}",
            "ok"
        )

        do_discord_notifications(traders, invasions, fissures, self.state, self.log)
        purge_old(self.state)
        save_state(self.state)

    def loop_forever(self):
        self.log(f"TennoReporter 云端模式启动 (CHECK_EVERY={CHECK_EVERY}s)", "ok")
        self.log(f"WEBHOOK: {'已配置' if 'discordapp' not in WEBHOOK_URL and 'yourdiscord' not in WEBHOOK_URL else '⚠ 未配置，请设置环境变量 DISCORD_WEBHOOK_URL'}", "ok")
        while True:
            try:
                self.run_once()
            except Exception as e:
                self.log(f"未捕获异常: {e}", "err")
            self.log(f"等待 {CHECK_EVERY}s 后再次检查...")
            time.sleep(CHECK_EVERY)


# ══════════════════════════════════════════════
#  GUI 模式（仅本地使用，Railway 不需要）
# ══════════════════════════════════════════════
def run_gui():
    import tkinter as tk
    from tkinter import ttk

    C = {
        "bg":       "#0a0c10", "panel":   "#0f1318", "border":  "#1e2d3d",
        "border2":  "#0d7377", "accent":  "#14ffec", "accent2": "#0d7377",
        "gold":     "#c8a84b", "red":     "#e74c3c", "purple":  "#9b59b6",
        "green":    "#2ecc71", "text":    "#cdd6e0", "subtext": "#5a7a8a",
        "trader":   "#c8a84b", "invasion":"#e74c3c", "fissure": "#9b59b6",
        "log_info": "#5a7a8a", "log_ok":  "#2ecc71", "log_warn":"#e67e22",
        "log_err":  "#e74c3c",
    }
    FONT_TITLE  = ("Courier New", 11, "bold")
    FONT_MONO   = ("Courier New", 9)
    FONT_MONO_S = ("Courier New", 8)
    FONT_LABEL  = ("Courier New", 9)
    FONT_HEADER = ("Courier New", 10, "bold")

    class TennoReporterGUI(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("TennoReporter")
            self.geometry("1200x680")
            self.minsize(960, 580)
            self.configure(bg=C["bg"])
            self.resizable(True, True)
            self.state_data = load_state()
            self.running    = False
            self.worker     = None
            self.next_check = 0
            self.last_traders  = []
            self.last_invasions = []
            self.last_fissures  = []
            self._build_ui()
            self._start_clock()

        def _build_ui(self):
            self._build_titlebar()
            main = tk.Frame(self, bg=C["bg"])
            main.pack(fill="both", expand=True, padx=10, pady=(0, 10))
            for i in range(4):
                main.columnconfigure(i, weight=1)
            main.rowconfigure(0, weight=3)
            main.rowconfigure(1, weight=2)
            self._build_panel_trader(main,   row=0, col=0)
            self._build_panel_invasion(main, row=0, col=1)
            self._build_panel_fissure(main,  row=0, col=2)
            self._build_panel_weather(main,  row=0, col=3)
            self._build_log_panel(main,      row=1, col=0, colspan=4)
            self._build_statusbar()

        def _build_titlebar(self):
            bar = tk.Frame(self, bg=C["panel"], height=52)
            bar.pack(fill="x", padx=10, pady=(10, 6))
            bar.pack_propagate(False)
            left = tk.Frame(bar, bg=C["panel"])
            left.pack(side="left", padx=14, fill="y")
            tk.Label(left, text="TENNO",    font=("Courier New", 16, "bold"), bg=C["panel"], fg=C["accent"]).pack(side="left")
            tk.Label(left, text="REPORTER", font=("Courier New", 16, "bold"), bg=C["panel"], fg=C["gold"]).pack(side="left", padx=(2, 0))
            tk.Label(left, text=" v3.1",    font=FONT_MONO_S,                 bg=C["panel"], fg=C["subtext"]).pack(side="left", anchor="s", pady=3)
            mid = tk.Frame(bar, bg=C["panel"])
            mid.pack(side="left", expand=True, fill="both")
            self.lbl_clock = tk.Label(mid, text="", font=("Courier New", 11), bg=C["panel"], fg=C["subtext"])
            self.lbl_clock.pack(expand=True)
            right = tk.Frame(bar, bg=C["panel"])
            right.pack(side="right", padx=14, fill="y")
            self.btn_push    = self._btn(right, "📤 推送 Discord", self._force_push,    C["gold"],    side="right", padx=4)
            self.btn_refresh = self._btn(right, "⟳ 立即刷新",     self._manual_refresh, C["accent2"], side="right", padx=4)
            self.btn_toggle  = self._btn(right, "▶ 启动监控",     self._toggle,         C["accent"],  side="right", padx=4)

        def _btn(self, parent, text, cmd, color, side="left", padx=6):
            b = tk.Button(parent, text=text, command=cmd, font=FONT_LABEL,
                          bg=C["panel"], fg=color, activebackground=color,
                          activeforeground=C["bg"], relief="flat", bd=0,
                          cursor="hand2", highlightbackground=color,
                          highlightthickness=1, padx=8, pady=3)
            b.pack(side=side, padx=padx, pady=10)
            return b

        def _panel_frame(self, parent, title, color, row, col):
            outer = tk.Frame(parent, bg=color, bd=0)
            outer.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")
            inner = tk.Frame(outer, bg=C["panel"])
            inner.pack(fill="both", expand=True, padx=1, pady=1)
            hdr = tk.Frame(inner, bg=C["panel"])
            hdr.pack(fill="x", padx=8, pady=(8, 4))
            tk.Label(hdr, text="▸ " + title, font=FONT_HEADER, bg=C["panel"], fg=color).pack(side="left")
            count_var = tk.StringVar(value="0")
            tk.Label(hdr, textvariable=count_var, font=FONT_MONO_S, bg=C["panel"], fg=color).pack(side="right")
            tk.Frame(inner, bg=color, height=1).pack(fill="x", padx=8, pady=(0, 6))
            scroll_frame = tk.Frame(inner, bg=C["panel"])
            scroll_frame.pack(fill="both", expand=True, padx=4, pady=(0, 6))
            canvas = tk.Canvas(scroll_frame, bg=C["panel"], bd=0, highlightthickness=0, relief="flat")
            vsb = tk.Scrollbar(scroll_frame, orient="vertical", command=canvas.yview, bg=C["panel"])
            canvas.configure(yscrollcommand=vsb.set)
            vsb.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            content = tk.Frame(canvas, bg=C["panel"])
            cwin = canvas.create_window((0, 0), window=content, anchor="nw")
            canvas.bind("<Configure>", lambda e: canvas.itemconfig(cwin, width=e.width))
            content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
            return inner, content, count_var

        def _build_panel_trader(self, p, row, col):
            _, self.trader_content, self.trader_count = self._panel_frame(p, "虚空商人", C["trader"], row, col)

        def _build_panel_invasion(self, p, row, col):
            _, self.invasion_content, self.invasion_count = self._panel_frame(p, "稀有入侵", C["invasion"], row, col)

        def _build_panel_fissure(self, p, row, col):
            _, self.fissure_content, self.fissure_count = self._panel_frame(p, "钢铁裂缝", C["fissure"], row, col)

        def _build_panel_weather(self, p, row, col):
            _, self.weather_content, self.weather_count = self._panel_frame(p, "星球天气", "#3a86ff", row, col)

        def _build_log_panel(self, parent, row, col, colspan):
            outer = tk.Frame(parent, bg=C["border"], bd=0)
            outer.grid(row=row, column=col, columnspan=colspan, padx=4, pady=4, sticky="nsew")
            inner = tk.Frame(outer, bg=C["panel"])
            inner.pack(fill="both", expand=True, padx=1, pady=1)
            hdr = tk.Frame(inner, bg=C["panel"])
            hdr.pack(fill="x", padx=8, pady=(6, 2))
            tk.Label(hdr, text="▸ 运行日志", font=FONT_HEADER, bg=C["panel"], fg=C["subtext"]).pack(side="left")
            self._btn(hdr, "清空", self._clear_log, C["subtext"], side="right", padx=0)
            self.log_box = tk.Text(inner, bg=C["bg"], fg=C["text"], font=FONT_MONO_S,
                                   relief="flat", bd=0, wrap="word", state="disabled",
                                   insertbackground=C["accent"], selectbackground=C["accent2"], height=8)
            self.log_box.pack(fill="both", expand=True, padx=8, pady=(0, 6))
            for tag, fg in [("info", C["log_info"]), ("ok", C["log_ok"]),
                            ("warn", C["log_warn"]), ("err", C["log_err"]),
                            ("accent", C["accent"])]:
                self.log_box.tag_config(tag, foreground=fg)

        def _build_statusbar(self):
            bar = tk.Frame(self, bg=C["panel"], height=22)
            bar.pack(fill="x", side="bottom")
            bar.pack_propagate(False)
            self.lbl_status = tk.Label(bar, text="● 待机", font=FONT_MONO_S, bg=C["panel"], fg=C["subtext"])
            self.lbl_status.pack(side="left", padx=10)
            self.lbl_next = tk.Label(bar, text="", font=FONT_MONO_S, bg=C["panel"], fg=C["subtext"])
            self.lbl_next.pack(side="right", padx=10)

        def _log(self, msg, tag="info"):
            ts   = datetime.now().strftime("%H:%M:%S")
            line = f"[{ts}] {msg}\n"
            self.after(0, self._log_write, line, tag)

        def _log_write(self, line, tag):
            self.log_box.configure(state="normal")
            self.log_box.insert("end", line, tag)
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

        def _clear_log(self):
            self.log_box.configure(state="normal")
            self.log_box.delete("1.0", "end")
            self.log_box.configure(state="disabled")

        def _clear_frame(self, frame):
            for w in frame.winfo_children():
                w.destroy()

        def _card(self, parent, color):
            f = tk.Frame(parent, bg=C["bg"], bd=0)
            f.pack(fill="x", padx=4, pady=3)
            tk.Frame(f, bg=color, width=3).pack(side="left", fill="y")
            body = tk.Frame(f, bg=C["bg"])
            body.pack(side="left", fill="both", expand=True, padx=8, pady=6)
            return body

        def _row(self, parent, label, value, lc=None, vc=None):
            r = tk.Frame(parent, bg=C["bg"])
            r.pack(fill="x", pady=1)
            tk.Label(r, text=label, font=FONT_MONO_S, bg=C["bg"], fg=lc or C["subtext"], width=10, anchor="w").pack(side="left")
            tk.Label(r, text=value, font=FONT_MONO_S, bg=C["bg"], fg=vc or C["text"], anchor="w").pack(side="left")

        def render_trader(self, traders):
            self._clear_frame(self.trader_content)
            self.trader_count.set(f"{len(traders)} 条")
            if not traders:
                tk.Label(self.trader_content, text="暂无数据", font=FONT_MONO_S, bg=C["panel"], fg=C["subtext"]).pack(pady=20)
                return
            for t in [x for x in traders if x.get("_active")]:
                b = self._card(self.trader_content, C["gold"])
                tk.Label(b, text=f"🛸 {t['name']} — 已抵达", font=FONT_TITLE, bg=C["bg"], fg=C["gold"]).pack(anchor="w")
                self._row(b, "驿站", t["node"],      vc=C["text"])
                self._row(b, "剩余", t["remain"],    vc=C["green"])
                self._row(b, "离开", t["expiry_str"],vc=C["subtext"])
            for t in [x for x in traders if not x.get("_active")]:
                b = self._card(self.trader_content, C["accent2"])
                tk.Label(b, text=f"🛸 {t['name']} — 即将到来", font=FONT_TITLE, bg=C["bg"], fg=C["accent"]).pack(anchor="w")
                self._row(b, "驿站",       t["node"])
                self._row(b, "抵达倒计时", t["arrive_remain"], vc=C["accent"])
                self._row(b, "到达",       t["arrive_str"])
                self._row(b, "离开",       t["expiry_str"])

        def render_invasions(self, invasions):
            self._clear_frame(self.invasion_content)
            self.invasion_count.set(f"{len(invasions)} 条")
            if not invasions:
                tk.Label(self.invasion_content, text="暂无稀有入侵", font=FONT_MONO_S, bg=C["panel"], fg=C["subtext"]).pack(pady=20)
                return
            for inv in invasions:
                b = self._card(self.invasion_content, C["red"])
                tk.Label(b, text=f"⚠ {inv['node']}", font=FONT_TITLE, bg=C["bg"], fg=C["invasion"]).pack(anchor="w")
                self._row(b, "阵营",     f"{inv['atk']} ► {inv['def_']}", vc=C["text"])
                self._row(b, "进攻奖励", inv["atk_r"], vc=C["gold"])
                self._row(b, "防守奖励", inv["def_r"], vc=C["gold"])
                pct   = inv["progress"]
                bar_w = 160
                bf = tk.Frame(b, bg=C["bg"])
                bf.pack(anchor="w", pady=(3, 0))
                tk.Label(bf, text="进度 ", font=FONT_MONO_S, bg=C["bg"], fg=C["subtext"]).pack(side="left")
                track = tk.Frame(bf, bg=C["border"], width=bar_w, height=6)
                track.pack(side="left")
                track.pack_propagate(False)
                tk.Frame(track, bg=C["red"], width=int(bar_w * min(pct / 100, 1)), height=6).place(x=0, y=0)
                tk.Label(bf, text=f" {pct:.1f}%", font=FONT_MONO_S, bg=C["bg"], fg=C["text"]).pack(side="left")

        def render_fissures(self, fissures):
            self._clear_frame(self.fissure_content)
            self.fissure_count.set(f"{len(fissures)} 条")
            if not fissures:
                tk.Label(self.fissure_content, text="暂无钢铁裂缝", font=FONT_MONO_S, bg=C["panel"], fg=C["subtext"]).pack(pady=20)
                return
            for fs in fissures:
                b = self._card(self.fissure_content, C["purple"])
                tk.Label(b, text=f"🌀 {fs['node_label']}", font=FONT_TITLE, bg=C["bg"], fg=C["fissure"]).pack(anchor="w")
                self._row(b, "等级", fs["tier"],   vc=C["accent"])
                self._row(b, "任务", fs["mtype"],  vc=C["text"])
                self._row(b, "剩余", fs["remain"], vc=C["green"])
                self._row(b, "到期", fs["expiry"], vc=C["subtext"])

        def render_weather(self, weather_list):
            self._clear_frame(self.weather_content)
            self.weather_count.set(f"{len(weather_list)} 条")
            if not weather_list:
                tk.Label(self.weather_content, text="暂无天气数据", font=FONT_MONO_S, bg=C["panel"], fg=C["subtext"]).pack(pady=20)
                return
            PLANET_COLOR = {"地球": "#2ecc71", "火星": "#e74c3c", "金星": "#f39c12"}
            for w in weather_list:
                color = PLANET_COLOR.get(w["planet"], "#3a86ff")
                b = self._card(self.weather_content, color)
                tk.Label(b, text=w["planet"], font=FONT_TITLE, bg=C["bg"], fg=color).pack(anchor="w")
                self._row(b, "当前状态", w["state"],      vc=C["text"])
                self._row(b, "剩余时间", w["remain"],     vc=C["green"])
                self._row(b, "切换时间", w["expiry"],     vc=C["subtext"])
                if w.get("next_state"):
                    self._row(b, "下一状态", w["next_state"], vc=C["accent"])

        def _fetch_and_update(self):
            self._set_status("● 正在请求...", C["accent"])
            self._log("轮询 API...", "info")
            traders, invasions, fissures = process_data(self._log)
            self.last_traders  = traders
            self.last_invasions = invasions
            self.last_fissures  = fissures
            weather = fetch_weather()
            self.after(0, lambda t=traders:   self.render_trader(t))
            self.after(0, lambda i=invasions: self.render_invasions(i))
            self.after(0, lambda f=fissures:  self.render_fissures(f))
            self.after(0, lambda w=weather:   self.render_weather(w))
            self._log(
                f"刷新完成 — 商人:{len(traders)}  入侵:{len(invasions)}  "
                f"裂缝:{len(fissures)}  天气:{len(weather)}", "ok"
            )
            self._set_status("● 运行中", C["green"])
            do_discord_notifications(traders, invasions, fissures, self.state_data, self._log)
            purge_old(self.state_data)
            save_state(self.state_data)

        def _worker_loop(self):
            while self.running:
                self.next_check = time.time() + CHECK_EVERY
                self._fetch_and_update()
                while self.running and time.time() < self.next_check:
                    time.sleep(1)

        def _toggle(self):
            if not self.running:
                self.running = True
                self.btn_toggle.configure(text="■ 停止监控", fg=C["log_err"])
                self._set_status("● 运行中", C["green"])
                self._log("监控已启动", "ok")
                self.worker = threading.Thread(target=self._worker_loop, daemon=True)
                self.worker.start()
            else:
                self.running = False
                self.btn_toggle.configure(text="▶ 启动监控", fg=C["accent"])
                self._set_status("● 已停止", C["subtext"])
                self._log("监控已停止", "warn")

        def _manual_refresh(self):
            if not self.running:
                threading.Thread(target=self._fetch_and_update, daemon=True).start()
            else:
                self.next_check = 0

        def _force_push(self):
            if not hasattr(self, 'last_traders'):
                self._log("尚无数据，请先刷新", "warn")
                return
            self.btn_push.configure(state="disabled", text="推送中...")
            self._log("── 强制推送开始 ──", "accent")

            def _do():
                traders  = self.last_traders
                invasions = self.last_invasions
                fissures  = self.last_fissures
                sent = 0
                for t in traders:
                    embed = (
                        {"title": "🛸 虚空商人已到达！",
                         "description": f"**{t['name']}** 现在在 **{t['node']}**！",
                         "color": 0xFFD700,
                         "fields": [{"name": "⌛ 剩余", "value": t["remain"],      "inline": True},
                                    {"name": "📅 离开", "value": t["expiry_str"], "inline": True}],
                         "footer": {"text": "TennoReporter · 手动推送"},
                         "timestamp": datetime.utcnow().isoformat()}
                        if t.get("_active") else
                        {"title": "🛸 虚空商人即将到来",
                         "description": f"**{t['name']}** 将抵达 **{t['node']}**",
                         "color": 0xFFA500,
                         "fields": [{"name": "📅 到达",   "value": t["arrive_str"],    "inline": True},
                                    {"name": "⌛ 倒计时", "value": t["arrive_remain"], "inline": True},
                                    {"name": "📅 离开",   "value": t["expiry_str"],    "inline": True}],
                         "footer": {"text": "TennoReporter · 手动推送"},
                         "timestamp": datetime.utcnow().isoformat()}
                    )
                    post_discord(embed, self._log)
                    sent += 1
                for inv in invasions:
                    post_discord({
                        "title":       "⚠️ 稀有入侵任务！",
                        "description": f"**{inv['node']}** — {inv['atk']} 进攻 {inv['def_']}",
                        "color":       0xE74C3C,
                        "fields": [{"name": "⚔️ 进攻奖励", "value": inv["atk_r"],              "inline": True},
                                   {"name": "🛡️ 防守奖励", "value": inv["def_r"],              "inline": True},
                                   {"name": "📊 进度",      "value": f"{inv['progress']:.1f}%", "inline": False}],
                        "footer":    {"text": "TennoReporter · 手动推送"},
                        "timestamp": datetime.utcnow().isoformat()
                    }, self._log)
                    sent += 1
                for fs in fissures:
                    post_discord({
                        "title":       "🌀 钢铁模式虚空裂缝",
                        "description": f"**{fs['node_label']}** — {fs['tier']} 裂缝",
                        "color":       0x8E44AD,
                        "fields": [{"name": "🎯 任务", "value": fs["mtype"],  "inline": True},
                                   {"name": "⌛ 剩余", "value": fs["remain"], "inline": True},
                                   {"name": "📅 到期", "value": fs["expiry"], "inline": True}],
                        "footer":    {"text": "TennoReporter · 手动推送"},
                        "timestamp": datetime.utcnow().isoformat()
                    }, self._log)
                    sent += 1
                msg = f"── 强制推送完成，共 {sent} 条 ──" if sent else "当前无可推送内容"
                self._log(msg, "ok" if sent else "warn")
                self.after(0, self.btn_push.configure, {"state": "normal", "text": "📤 推送 Discord"})

            threading.Thread(target=_do, daemon=True).start()

        def _set_status(self, text, color):
            self.after(0, self.lbl_status.configure, {"text": text, "fg": color})

        def _start_clock(self):
            self._tick()

        def _tick(self):
            self.lbl_clock.configure(text=datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))
            if self.running and self.next_check > 0:
                secs = max(0, int(self.next_check - time.time()))
                self.lbl_next.configure(text=f"下次刷新  {secs // 60:02d}:{secs % 60:02d}", fg=C["subtext"])
            elif not self.running:
                self.lbl_next.configure(text="")
            self.after(1000, self._tick)

    app = TennoReporterGUI()
    app.mainloop()


# ══════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════
if __name__ == "__main__":
    if "--headless" in sys.argv:
        bot = HeadlessReporter()
        bot.loop_forever()
    else:
        run_gui()