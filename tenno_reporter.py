"""
TennoReporter — Warframe 世界状态监控 GUI
深色科幻风格，tkinter 实现，无需额外安装
"""

import tkinter as tk
from tkinter import ttk
import threading
import requests
import json
import time
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# ══════════════════════════════════════════════
#  配置
# ══════════════════════════════════════════════
WEBHOOK_URL  = "https://discord.com/api/webhooks/yourdiscordbotabcdefg"
API_URL = "https://api.warframestat.us/pc/worldstate"
WEATHER_API  = "https://api.warframestat.us/pc"   # 天气用已解析 API，字段名有保证
CHECK_EVERY  = 10
STATE_FILE   = "state.json"

FISSURE_NODES = {"SolNode36", "SolNode38"}
NODE_LABEL    = {"SolNode36": "Mot (虚空)", "SolNode38": "Ani (虚空)"}
RARE_KEYWORDS = ["OrokinCatalyst", "OrokinReactor", "Forma",
                 "AuraForma", "Riven", "AladCoordinate", "SentinelWeaponBP"]
TIER_NAME     = {"VoidT1":"Lith","VoidT2":"Meso","VoidT3":"Neo",
                 "VoidT4":"Axi","VoidT5":"Requiem","VoidT6":"Omnia"}
FACTION_NAME  = {"FC_CORPUS":"星团","FC_GRINEER":"基尼尔","FC_INFESTATION":"感染体"}

# ══════════════════════════════════════════════
#  配色 / 字体
# ══════════════════════════════════════════════
C = {
    "bg":        "#0a0c10",
    "panel":     "#0f1318",
    "border":    "#1e2d3d",
    "border2":   "#0d7377",
    "accent":    "#14ffec",
    "accent2":   "#0d7377",
    "gold":      "#c8a84b",
    "red":       "#e74c3c",
    "purple":    "#9b59b6",
    "green":     "#2ecc71",
    "text":      "#cdd6e0",
    "subtext":   "#5a7a8a",
    "header":    "#14ffec",
    "trader":    "#c8a84b",
    "invasion":  "#e74c3c",
    "fissure":   "#9b59b6",
    "log_info":  "#5a7a8a",
    "log_ok":    "#2ecc71",
    "log_warn":  "#e67e22",
    "log_err":   "#e74c3c",
}
FONT_TITLE  = ("Courier New", 11, "bold")
FONT_MONO   = ("Courier New", 9)
FONT_MONO_S = ("Courier New", 8)
FONT_LABEL  = ("Courier New", 9)
FONT_HEADER = ("Courier New", 10, "bold")
FONT_BIG    = ("Courier New", 14, "bold")

# ══════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════
def now_ms(): return int(time.time() * 1000)

def to_dt(ms):
    return datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime("%m-%d %H:%M UTC")

def remaining(ms):
    diff = (ms - now_ms()) / 1000
    if diff <= 0: return "已过期"
    h, m = int(diff//3600), int((diff%3600)//60)
    return f"{h}h {m:02d}m" if h else f"{m}m"

def expiry_ms(obj):
    try: return int(obj["Expiry"]["$date"]["$numberLong"])
    except: return 0

def activation_ms(obj):
    try: return int(obj["Activation"]["$date"]["$numberLong"])
    except: return 0

def load_state():
    if Path(STATE_FILE).exists():
        with open(STATE_FILE, encoding="utf-8") as f: return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def purge_old(state):
    cutoff = time.time() - 3*86400
    for k in [k for k,v in state.items() if v.get("ts",0)<cutoff]: del state[k]

def _get_items(reward):
    if isinstance(reward, dict): return reward.get("countedItems", [])
    return []

def fmt_reward(reward):
    items = _get_items(reward)
    if not items: return "无"
    return "  ".join(f"{it['ItemType'].split('/')[-1]} x{it.get('ItemCount',1)}" for it in items)

def is_rare(reward):
    return any(any(kw in it.get("ItemType","") for kw in RARE_KEYWORDS) for it in _get_items(reward))


# ══════════════════════════════════════════════
#  Discord 推送
# ══════════════════════════════════════════════
def post_discord(embed, log_fn=None):
    try:
        r = requests.post(
            WEBHOOK_URL,
            json={"embeds": [embed]},
            timeout=10,
            headers={"Content-Type": "application/json"},
        )
        if r.status_code in (200, 204):
            if log_fn: log_fn(f"Discord 推送成功：{embed.get('title','')}", "ok")
        else:
            if log_fn: log_fn(f"Discord 推送失败 HTTP {r.status_code}：{r.text[:120]}", "err")
    except requests.exceptions.ConnectionError as e:
        if log_fn: log_fn(f"网络连接失败（Discord）：{e}", "err")
    except requests.exceptions.Timeout:
        if log_fn: log_fn("Discord 请求超时", "err")
    except Exception as e:
        if log_fn: log_fn(f"推送异常：{e}", "err")
    time.sleep(0.5)


# ══════════════════════════════════════════════
#  主 GUI 类
# ══════════════════════════════════════════════
class TennoReporter(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TennoReporter")
        self.geometry("1200x680")
        self.minsize(960, 580)
        self.configure(bg=C["bg"])
        self.resizable(True, True)

        self.state      = load_state()
        self.running    = False
        self.worker     = None
        self.next_check = 0
        self.last_data  = None

        self._build_ui()
        self._start_clock()

    # ────────────────────────────────────────────
    #  UI 构建
    # ────────────────────────────────────────────
    def _build_ui(self):
        self._build_titlebar()
        main = tk.Frame(self, bg=C["bg"])
        main.pack(fill="both", expand=True, padx=10, pady=(0,10))
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)
        main.columnconfigure(2, weight=1)
        main.columnconfigure(3, weight=1)
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
        bar.pack(fill="x", padx=10, pady=(10,6))
        bar.pack_propagate(False)

        left = tk.Frame(bar, bg=C["panel"])
        left.pack(side="left", padx=14, pady=0, fill="y")
        tk.Label(left, text="TENNO", font=("Courier New",16,"bold"),
                 bg=C["panel"], fg=C["accent"]).pack(side="left")
        tk.Label(left, text="REPORTER", font=("Courier New",16,"bold"),
                 bg=C["panel"], fg=C["gold"]).pack(side="left", padx=(2,0))
        tk.Label(left, text=" v3.0", font=FONT_MONO_S,
                 bg=C["panel"], fg=C["subtext"]).pack(side="left", anchor="s", pady=3)

        mid = tk.Frame(bar, bg=C["panel"])
        mid.pack(side="left", expand=True, fill="both")
        self.lbl_clock = tk.Label(mid, text="", font=("Courier New",11),
                                   bg=C["panel"], fg=C["subtext"])
        self.lbl_clock.pack(expand=True)

        right = tk.Frame(bar, bg=C["panel"])
        right.pack(side="right", padx=14, fill="y")

        self.btn_push    = self._btn(right, "📤 推送 Discord", self._force_push,
                                     C["gold"],    side="right", padx=4)
        self.btn_refresh = self._btn(right, "⟳ 立即刷新", self._manual_refresh,
                                     C["accent2"], side="right", padx=4)
        self.btn_toggle  = self._btn(right, "▶ 启动监控", self._toggle,
                                     C["accent"],  side="right", padx=4)

    def _btn(self, parent, text, cmd, color, side="left", padx=6):
        b = tk.Button(parent, text=text, command=cmd,
                      font=FONT_LABEL, bg=C["panel"], fg=color,
                      activebackground=color, activeforeground=C["bg"],
                      relief="flat", bd=0, cursor="hand2",
                      highlightbackground=color, highlightthickness=1, padx=8, pady=3)
        b.pack(side=side, padx=padx, pady=10)
        return b

    def _panel_frame(self, parent, title, color, row, col):
        outer = tk.Frame(parent, bg=color, bd=0)
        outer.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")
        inner = tk.Frame(outer, bg=C["panel"])
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        hdr = tk.Frame(inner, bg=C["panel"])
        hdr.pack(fill="x", padx=8, pady=(8,4))
        tk.Label(hdr, text="▸ "+title, font=FONT_HEADER,
                 bg=C["panel"], fg=color).pack(side="left")
        count_var = tk.StringVar(value="0")
        tk.Label(hdr, textvariable=count_var, font=FONT_MONO_S,
                 bg=C["panel"], fg=color).pack(side="right")

        sep = tk.Frame(inner, bg=color, height=1)
        sep.pack(fill="x", padx=8, pady=(0,6))

        scroll_frame = tk.Frame(inner, bg=C["panel"])
        scroll_frame.pack(fill="both", expand=True, padx=4, pady=(0,6))

        canvas = tk.Canvas(scroll_frame, bg=C["panel"], bd=0,
                           highlightthickness=0, relief="flat")
        vsb = tk.Scrollbar(scroll_frame, orient="vertical",
                           command=canvas.yview, bg=C["panel"])
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        content = tk.Frame(canvas, bg=C["panel"])
        cwin = canvas.create_window((0,0), window=content, anchor="nw")

        def on_resize(e): canvas.itemconfig(cwin, width=e.width)
        def on_frame(e):  canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.bind("<Configure>", on_resize)
        content.bind("<Configure>", on_frame)
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        return inner, content, count_var

    def _build_panel_trader(self, parent, row, col):
        _, self.trader_content, self.trader_count = self._panel_frame(
            parent, "虚空商人", C["trader"], row, col)

    def _build_panel_invasion(self, parent, row, col):
        _, self.invasion_content, self.invasion_count = self._panel_frame(
            parent, "稀有入侵", C["invasion"], row, col)

    def _build_panel_fissure(self, parent, row, col):
        _, self.fissure_content, self.fissure_count = self._panel_frame(
            parent, "钢铁裂缝", C["fissure"], row, col)

    def _build_panel_weather(self, parent, row, col):
        _, self.weather_content, self.weather_count = self._panel_frame(
            parent, "星球天气", "#3a86ff", row, col)

    def _build_log_panel(self, parent, row, col, colspan):
        outer = tk.Frame(parent, bg=C["border"], bd=0)
        outer.grid(row=row, column=col, columnspan=colspan,
                   padx=4, pady=4, sticky="nsew")
        inner = tk.Frame(outer, bg=C["panel"])
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        hdr = tk.Frame(inner, bg=C["panel"])
        hdr.pack(fill="x", padx=8, pady=(6,2))
        tk.Label(hdr, text="▸ 运行日志", font=FONT_HEADER,
                 bg=C["panel"], fg=C["subtext"]).pack(side="left")
        self._btn(hdr, "清空", self._clear_log, C["subtext"], side="right", padx=0)

        self.log_box = tk.Text(inner, bg=C["bg"], fg=C["text"],
                               font=FONT_MONO_S, relief="flat",
                               bd=0, wrap="word", state="disabled",
                               insertbackground=C["accent"],
                               selectbackground=C["accent2"],
                               height=8)
        self.log_box.pack(fill="both", expand=True, padx=8, pady=(0,6))
        self.log_box.tag_config("info",   foreground=C["log_info"])
        self.log_box.tag_config("ok",     foreground=C["log_ok"])
        self.log_box.tag_config("warn",   foreground=C["log_warn"])
        self.log_box.tag_config("err",    foreground=C["log_err"])
        self.log_box.tag_config("accent", foreground=C["accent"])

    def _build_statusbar(self):
        bar = tk.Frame(self, bg=C["panel"], height=22)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self.lbl_status = tk.Label(bar, text="● 待机", font=FONT_MONO_S,
                                    bg=C["panel"], fg=C["subtext"])
        self.lbl_status.pack(side="left", padx=10)
        self.lbl_next = tk.Label(bar, text="", font=FONT_MONO_S,
                                  bg=C["panel"], fg=C["subtext"])
        self.lbl_next.pack(side="right", padx=10)

    # ────────────────────────────────────────────
    #  日志（线程安全）
    # ────────────────────────────────────────────
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
        self.log_box.delete("1.0","end")
        self.log_box.configure(state="disabled")

    # ────────────────────────────────────────────
    #  面板渲染
    # ────────────────────────────────────────────
    def _clear_frame(self, frame):
        for w in frame.winfo_children(): w.destroy()

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
        tk.Label(r, text=label, font=FONT_MONO_S, bg=C["bg"],
                 fg=lc or C["subtext"], width=10, anchor="w").pack(side="left")
        tk.Label(r, text=value, font=FONT_MONO_S, bg=C["bg"],
                 fg=vc or C["text"], anchor="w").pack(side="left")

    def render_trader(self, traders):
        self._clear_frame(self.trader_content)
        self.trader_count.set(f"{len(traders)} 条")
        if not traders:
            tk.Label(self.trader_content, text="暂无数据",
                     font=FONT_MONO_S, bg=C["panel"], fg=C["subtext"]).pack(pady=20)
            return
        for t in [x for x in traders if x.get("_active")]:
            b = self._card(self.trader_content, C["gold"])
            tk.Label(b, text=f"🛸 {t['name']} — 已抵达",
                     font=FONT_TITLE, bg=C["bg"], fg=C["gold"]).pack(anchor="w")
            self._row(b, "驿站", t["node"],       vc=C["text"])
            self._row(b, "剩余", t["remain"],      vc=C["green"])
            self._row(b, "离开", t["expiry_str"],  vc=C["subtext"])
        for t in [x for x in traders if not x.get("_active")]:
            b = self._card(self.trader_content, C["accent2"])
            tk.Label(b, text=f"🛸 {t['name']} — 即将到来",
                     font=FONT_TITLE, bg=C["bg"], fg=C["accent"]).pack(anchor="w")
            self._row(b, "驿站",     t["node"])
            self._row(b, "抵达倒计时", t["arrive_remain"], vc=C["accent"])
            self._row(b, "到达",     t["arrive_str"])
            self._row(b, "离开",     t["expiry_str"])

    def render_invasions(self, invasions):
        self._clear_frame(self.invasion_content)
        self.invasion_count.set(f"{len(invasions)} 条")
        if not invasions:
            tk.Label(self.invasion_content, text="暂无稀有入侵",
                     font=FONT_MONO_S, bg=C["panel"], fg=C["subtext"]).pack(pady=20)
            return
        for inv in invasions:
            b = self._card(self.invasion_content, C["red"])
            tk.Label(b, text=f"⚠ {inv['node']}",
                     font=FONT_TITLE, bg=C["bg"], fg=C["invasion"]).pack(anchor="w")
            self._row(b, "阵营",   f"{inv['atk']} ► {inv['def_']}", vc=C["text"])
            self._row(b, "进攻奖励", inv["atk_r"], vc=C["gold"])
            self._row(b, "防守奖励", inv["def_r"], vc=C["gold"])
            pct      = inv["progress"]
            bar_w    = 160
            bar_fill = int(bar_w * min(pct/100, 1))
            bf = tk.Frame(b, bg=C["bg"])
            bf.pack(anchor="w", pady=(3,0))
            tk.Label(bf, text="进度 ", font=FONT_MONO_S,
                     bg=C["bg"], fg=C["subtext"]).pack(side="left")
            track = tk.Frame(bf, bg=C["border"], width=bar_w, height=6)
            track.pack(side="left")
            track.pack_propagate(False)
            tk.Frame(track, bg=C["red"], width=bar_fill, height=6).place(x=0, y=0)
            tk.Label(bf, text=f" {pct:.1f}%", font=FONT_MONO_S,
                     bg=C["bg"], fg=C["text"]).pack(side="left")

    def render_fissures(self, fissures):
        self._clear_frame(self.fissure_content)
        self.fissure_count.set(f"{len(fissures)} 条")
        if not fissures:
            tk.Label(self.fissure_content, text="暂无钢铁裂缝",
                     font=FONT_MONO_S, bg=C["panel"], fg=C["subtext"]).pack(pady=20)
            return
        for fs in fissures:
            b = self._card(self.fissure_content, C["purple"])
            tk.Label(b, text=f"🌀 {fs['node_label']}",
                     font=FONT_TITLE, bg=C["bg"], fg=C["fissure"]).pack(anchor="w")
            self._row(b, "等级", fs["tier"],   vc=C["accent"])
            self._row(b, "任务", fs["mtype"],  vc=C["text"])
            self._row(b, "剩余", fs["remain"], vc=C["green"])
            self._row(b, "到期", fs["expiry"], vc=C["subtext"])

    def render_weather(self, weather_list):
        self._clear_frame(self.weather_content)
        self.weather_count.set(f"{len(weather_list)} 条")
        if not weather_list:
            tk.Label(self.weather_content, text="暂无天气数据",
                     font=FONT_MONO_S, bg=C["panel"], fg=C["subtext"]).pack(pady=20)
            return
        PLANET_COLOR = {"地球": "#2ecc71", "火星": "#e74c3c", "金星": "#f39c12"}
        for w in weather_list:
            planet = w["planet"]
            color  = PLANET_COLOR.get(planet, "#3a86ff")
            b = self._card(self.weather_content, color)
            tk.Label(b, text=planet, font=FONT_TITLE,
                     bg=C["bg"], fg=color).pack(anchor="w")
            self._row(b, "当前状态", w["state"],      vc=C["text"])
            self._row(b, "剩余时间", w["remain"],     vc=C["green"])
            self._row(b, "切换时间", w["expiry"],     vc=C["subtext"])
            if w.get("next_state"):
                self._row(b, "下一状态", w["next_state"], vc=C["accent"])

    # ────────────────────────────────────────────
    #  数据处理
    # ────────────────────────────────────────────
    def _process_data(self, data):
        cur = now_ms()

        # ── 虚空商人 ──
        traders = []
        for t in data.get("VoidTraders", []):
            exp = expiry_ms(t)
            act = activation_ms(t)
            if exp and cur > exp: continue
            traders.append({
                "_active":       cur >= act,
                "name":          t.get("Character", "Baro'Ki Teel"),
                "node":          t.get("Node", "未知"),
                "remain":        remaining(exp),
                "arrive_remain": remaining(act),
                "arrive_str":    to_dt(act),
                "expiry_str":    to_dt(exp),
                "_oid":          t.get("_id",{}).get("$oid",""),
                "_act_ms":       act,
                "_exp_ms":       exp,
            })

        # ── 稀有入侵 ──
        invasions = []
        for inv in data.get("Invasions", []):
            if inv.get("Completed", False): continue
            atk_r = inv.get("AttackerReward", {})
            def_r = inv.get("DefenderReward", {})
            if not is_rare(atk_r) and not is_rare(def_r): continue
            count = abs(inv.get("Count", 0))
            goal  = max(inv.get("Goal", 1), 1)
            invasions.append({
                "node":     inv.get("Node","未知"),
                "atk":      FACTION_NAME.get(inv.get("Faction",""),         inv.get("Faction","")),
                "def_":     FACTION_NAME.get(inv.get("DefenderFaction",""), inv.get("DefenderFaction","")),
                "atk_r":    fmt_reward(atk_r),
                "def_r":    fmt_reward(def_r),
                "progress": count / goal * 100,
                "_oid":     inv.get("_id",{}).get("$oid",""),
            })

        # ── 钢铁裂缝 ──
        # 正确节点编号（经 wiki 核实）：
        # Mot(虚空)=SolNode409, Ani(虚空)=SolNode405
        # Olympus(火星)=SolNode30, Stephano(天王星)=SolNode122, Kappa(冥神星)=SolNode177
        STEEL_NODES = {"SolNode409", "SolNode405", "SolNode30", "SolNode122", "SolNode177"}
        STEEL_LABEL = {
            "SolNode409": "Mot (虚空)",
            "SolNode405": "Ani (虚空)",
            "SolNode30":  "Olympus (火星)",
            "SolNode122": "Stephano (天王星)",
            "SolNode177": "Kappa (冥神星)",
        }
        fissures = []
        for m in data.get("ActiveMissions", []):
            if not m.get("Hard", False): continue
            node  = m.get("Node", "")
            is_h2 = "H-2" in node
            if node not in STEEL_NODES and not is_h2: continue
            exp = expiry_ms(m)
            if exp and cur > exp: continue
            fissures.append({
                "node_label": STEEL_LABEL.get(node, "H-2 星云" if is_h2 else node),
                "tier":       TIER_NAME.get(m.get("Modifier",""), m.get("Modifier","")),
                "mtype":      m.get("MissionType","").replace("MT_",""),
                "remain":     remaining(exp),
                "expiry":     to_dt(exp),
                "_oid":       m.get("_id",{}).get("$oid",""),
            })

        return traders, invasions, fissures

    def _fetch_weather(self):
        """从 warframestat.us 获取天气（字段名有文档保证）"""
        weather = []
        try:
            r = requests.get(WEATHER_API, timeout=15)
            r.raise_for_status()
            ws = r.json()
        except Exception as e:
            self._log(f"⚠ 天气数据获取失败: {e}", "warn")
            return weather

        def _tl(obj):
            tl = obj.get("timeLeft", "")
            if tl: return tl
            exp = obj.get("expiry", "")
            if exp:
                try:
                    dt = datetime.fromisoformat(exp.replace("Z","+00:00"))
                    diff = int((dt - datetime.now(timezone.utc)).total_seconds())
                    if diff <= 0: return "已过期"
                    h, m = diff//3600, (diff%3600)//60
                    return f"{h}h {m:02d}m" if h else f"{m}m"
                except: pass
            return "—"

        def _exp(obj):
            exp = obj.get("expiry", "—")
            return exp[:16].replace("T"," ") if len(exp) > 10 else exp

        ec = ws.get("earthCycle", {})
        if ec:
            is_day = ec.get("isDay", True)
            weather.append({"planet":"地球","state":"白天 ☀" if is_day else "夜晚 🌙",
                "next_state":"夜晚 🌙" if is_day else "白天 ☀","remain":_tl(ec),"expiry":_exp(ec)})

        cetus = ws.get("cetusCycle", {})
        if cetus:
            is_day = cetus.get("isDay", True)
            weather.append({"planet":"地球平原","state":"白天 ☀" if is_day else "夜晚 🌙",
                "next_state":"夜晚 🌙" if is_day else "白天 ☀","remain":_tl(cetus),"expiry":_exp(cetus)})

        vallis = ws.get("vallisCycle", {})
        if vallis:
            is_warm = vallis.get("isWarm", True)
            weather.append({"planet":"金星","state":"温暖 ☀" if is_warm else "寒冷 ❄",
                "next_state":"寒冷 ❄" if is_warm else "温暖 ☀","remain":_tl(vallis),"expiry":_exp(vallis)})

        cambion = ws.get("cambionCycle", {})
        if cambion:
            state = cambion.get("state", "fass")
            sm = {"fass":"Fass 白昼 🔥","vome":"Vome 夜晚 ❄"}
            nm = {"fass":"Vome 夜晚 ❄","vome":"Fass 白昼 🔥"}
            weather.append({"planet":"火星","state":sm.get(state,state),
                "next_state":nm.get(state,""),"remain":_tl(cambion),"expiry":_exp(cambion)})

        return weather

    def _do_discord_notifications(self, traders, invasions, fissures):
        cur = now_ms()
        for t in traders:
            oid = t["_oid"]
            act = t["_act_ms"]
            exp = t["_exp_ms"]
            pre_key    = f"vt_pre_{oid}"
            arrive_key = f"vt_arrive_{oid}"
            # 提前 3 天（259200 秒）预告
            if 0 < (act - cur)/1000 <= 259200 and pre_key not in self.state:
                post_discord({
                    "title": "🛸 虚空商人提前预告！",
                    "description": f"**{t['name']}** 将在约 3 天内抵达 **{t['node']}**",
                    "color": 0xFFA500,
                    "fields": [
                        {"name":"📅 到达","value":t["arrive_str"],"inline":True},
                        {"name":"⌛ 倒计时","value":t["arrive_remain"],"inline":True},
                        {"name":"📅 离开","value":t["expiry_str"],"inline":True},
                    ],
                    "footer":{"text":"TennoReporter"},
                    "timestamp": datetime.utcnow().isoformat(),
                }, self._log)
                self.state[pre_key] = {"ts": time.time()}
            if cur >= act and arrive_key not in self.state:
                post_discord({
                    "title": "🛸 虚空商人已到达！",
                    "description": f"**{t['name']}** 现在在 **{t['node']}**！",
                    "color": 0xFFD700,
                    "fields": [
                        {"name":"⌛ 剩余","value":t["remain"],"inline":True},
                        {"name":"📅 离开","value":t["expiry_str"],"inline":True},
                    ],
                    "footer":{"text":"TennoReporter"},
                    "timestamp": datetime.utcnow().isoformat(),
                }, self._log)
                self.state[arrive_key] = {"ts": time.time()}

        for inv in invasions:
            oid = inv["_oid"]
            if oid and oid not in self.state:
                post_discord({
                    "title": "⚠️ 稀有入侵任务！",
                    "description": f"**{inv['node']}** — {inv['atk']} 进攻 {inv['def_']}",
                    "color": 0xE74C3C,
                    "fields": [
                        {"name":"⚔️ 进攻奖励","value":inv["atk_r"],"inline":True},
                        {"name":"🛡️ 防守奖励","value":inv["def_r"],"inline":True},
                        {"name":"📊 进度","value":f"{inv['progress']:.1f}%","inline":False},
                    ],
                    "footer":{"text":"TennoReporter"},
                    "timestamp": datetime.utcnow().isoformat(),
                }, self._log)
                self.state[oid] = {"ts": time.time()}

        # ────────────────────────────────────────────
        #  钢铁裂缝推送（升级版）
        # ────────────────────────────────────────────

        # Step 1：找出是否出现新的裂缝
        new_fissure_found = False
        for fs in fissures:
            oid = fs["_oid"]
            if oid and oid not in self.state:
                new_fissure_found = True

        # Step 2：如果没有新裂缝，则不推送旧裂缝
        if not new_fissure_found:
            # 仍旧按原逻辑，只推送新裂缝
            for fs in fissures:
                oid = fs["_oid"]
                if oid and oid not in self.state:
                    post_discord({
                        "title": "🌀 钢铁模式虚空裂缝",
                        "description": f"**{fs['node_label']}** — {fs['tier']} 裂缝",
                        "color": 0x8E44AD,
                        "fields": [
                            {"name":"🎯 任务","value":fs["mtype"],"inline":True},
                            {"name":"⌛ 剩余","value":fs["remain"],"inline":True},
                            {"name":"📅 到期","value":fs["expiry"],"inline":True},
                        ],
                        "footer":{"text":"TennoReporter"},
                        "timestamp": datetime.utcnow().isoformat(),
                    }, self._log)
                    self.state[oid] = {"ts": time.time()}
        else:
            # Step 3：有新裂缝出现 → 推送所有未结束的裂缝
            for fs in fissures:
                oid = fs["_oid"]
                post_discord({
                    "title": "🌀 钢铁模式虚空裂缝（更新）",
                    "description": f"**{fs['node_label']}** — {fs['tier']} 裂缝",
                    "color": 0x8E44AD,
                    "fields": [
                        {"name":"🎯 任务","value":fs["mtype"],"inline":True},
                        {"name":"⌛ 剩余","value":fs["remain"],"inline":True},
                        {"name":"📅 到期","value":fs["expiry"],"inline":True},
                    ],
                    "footer":{"text":"TennoReporter"},
                    "timestamp": datetime.utcnow().isoformat(),
                }, self._log)

                # 新裂缝添加标记，旧裂缝也更新标记（防重复）
                self.state[oid] = {"ts": time.time()}


        # ────────────────────────────────────────────
        #  天气推送（仅地球）
        # ────────────────────────────────────────────
        try:
            # 只保留地球昼/夜
            weather_list = [
                w for w in self._fetch_weather()
                if w["planet"] == "地球"
            ]
        except Exception as e:
            self._log(f"天气推送前获取失败: {e}", "err")
            weather_list = []

        for w in weather_list:
            # 构造天气唯一 key
            key = f"weather_{w['planet']}_{w['state']}_{w['expiry']}"
            if key in self.state:
                continue

            embed = {
                "title": f"🌦 地球天气更新",
                "color": 0x3A86FF,
                "fields": [
                    {"name": "当前状态", "value": w['state'], "inline": True},
                    {"name": "剩余时间", "value": w['remain'], "inline": True},
                    {"name": "切换时间", "value": w['expiry'], "inline": False},
                ],
                "footer": {"text": "TennoReporter · 天气推送"},
                "timestamp": datetime.utcnow().isoformat()
            }

            if w.get("next_state"):
                embed["fields"].append(
                    {"name": "下一状态", "value": w["next_state"], "inline": True}
                )

            post_discord(embed, self._log)
            self.state[key] = {"ts": time.time()}

    # ────────────────────────────────────────────
    #  轮询逻辑
    # ────────────────────────────────────────────
    def _fetch_and_update(self):
        self._set_status("● 正在请求...", C["accent"])
        self._log("轮询 API...", "info")
        try:
            r = requests.get(API_URL, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            self._log(f"API 请求失败: {e}", "err")
            self._set_status("● 请求失败", C["log_err"])
            return

        traders, invasions, fissures = self._process_data(data)
        self.last_data = data
        weather = self._fetch_weather()

        self.after(0, lambda t=traders:   self.render_trader(t))
        self.after(0, lambda i=invasions: self.render_invasions(i))
        self.after(0, lambda f=fissures:  self.render_fissures(f))
        self.after(0, lambda w=weather:   self.render_weather(w))

        self._log(
            f"刷新完成 — 商人:{len(traders)}  稀有入侵:{len(invasions)}  "
            f"钢铁裂缝:{len(fissures)}  天气:{len(weather)}",
            "ok"
        )
        self._set_status("● 运行中", C["green"])
        self._do_discord_notifications(traders, invasions, fissures)
        purge_old(self.state)
        save_state(self.state)

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
            self._log("手动刷新...", "info")
            threading.Thread(target=self._fetch_and_update, daemon=True).start()
        else:
            self.next_check = 0

    def _force_push(self):
        if not self.last_data:
            self._log("尚无数据，请先刷新", "warn")
            return
        self.btn_push.configure(state="disabled", text="推送中...")
        self._log("── 强制推送开始 ──", "accent")

        def _do():
            traders, invasions, fissures = self._process_data(self.last_data)
            sent = 0
            for t in traders:
                if t.get("_active"):
                    embed = {"title":"🛸 虚空商人已到达！",
                             "description":f"**{t['name']}** 现在在 **{t['node']}**！",
                             "color":0xFFD700,
                             "fields":[{"name":"⌛ 剩余","value":t["remain"],"inline":True},
                                       {"name":"📅 离开","value":t["expiry_str"],"inline":True}],
                             "footer":{"text":"TennoReporter · 手动推送"},
                             "timestamp":datetime.utcnow().isoformat()}
                else:
                    embed = {"title":"🛸 虚空商人即将到来",
                             "description":f"**{t['name']}** 将抵达 **{t['node']}**",
                             "color":0xFFA500,
                             "fields":[{"name":"📅 到达","value":t["arrive_str"],"inline":True},
                                       {"name":"⌛ 倒计时","value":t["arrive_remain"],"inline":True},
                                       {"name":"📅 离开","value":t["expiry_str"],"inline":True}],
                             "footer":{"text":"TennoReporter · 手动推送"},
                             "timestamp":datetime.utcnow().isoformat()}
                post_discord(embed, self._log); sent += 1

            for inv in invasions:
                post_discord({"title":"⚠️ 稀有入侵任务！",
                              "description":f"**{inv['node']}** — {inv['atk']} 进攻 {inv['def_']}",
                              "color":0xE74C3C,
                              "fields":[{"name":"⚔️ 进攻奖励","value":inv["atk_r"],"inline":True},
                                        {"name":"🛡️ 防守奖励","value":inv["def_r"],"inline":True},
                                        {"name":"📊 进度","value":f"{inv['progress']:.1f}%","inline":False}],
                              "footer":{"text":"TennoReporter · 手动推送"},
                              "timestamp":datetime.utcnow().isoformat()}, self._log)
                sent += 1

            for fs in fissures:
                post_discord({"title":"🌀 钢铁模式虚空裂缝",
                              "description":f"**{fs['node_label']}** — {fs['tier']} 裂缝",
                              "color":0x8E44AD,
                              "fields":[{"name":"🎯 任务","value":fs["mtype"],"inline":True},
                                        {"name":"⌛ 剩余","value":fs["remain"],"inline":True},
                                        {"name":"📅 到期","value":fs["expiry"],"inline":True}],
                              "footer":{"text":"TennoReporter · 手动推送"},
                              "timestamp":datetime.utcnow().isoformat()}, self._log)
                sent += 1

            msg = f"── 强制推送完成，共 {sent} 条 ──" if sent else "当前无可推送内容"
            self._log(msg, "ok" if sent else "warn")
            self.after(0, self.btn_push.configure,
                       {"state": "normal", "text": "📤 推送 Discord"})

        threading.Thread(target=_do, daemon=True).start()

    # ────────────────────────────────────────────
    #  时钟 & 状态栏
    # ────────────────────────────────────────────
    def _set_status(self, text, color):
        self.after(0, self.lbl_status.configure, {"text": text, "fg": color})

    def _start_clock(self):
        self._tick()

    def _tick(self):
        self.lbl_clock.configure(text=datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))
        if self.running and self.next_check > 0:
            secs = max(0, int(self.next_check - time.time()))
            self.lbl_next.configure(text=f"下次刷新  {secs//60:02d}:{secs%60:02d}",
                                     fg=C["subtext"])
        elif not self.running:
            self.lbl_next.configure(text="")
        self.after(1000, self._tick)

class HeadlessReporter:
    """
    云端无 GUI 版本：不创建窗口，不需要 tkinter。
    只执行世界状态轮询 + Discord 自动推送。
    """
    def __init__(self):
        self.state = load_state()
        self.last_data = None

    def log(self, msg):
        print("[CLOUD]", msg)

    def run_once(self):
        # 拉取 API
        try:
            r = requests.get(API_URL, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            self.log(f"API 请求失败: {e}")
            return

        # 调用 TennoReporter 内的数据处理逻辑（复用）
        traders, invasions, fissures = TennoReporter._process_data(self=TennoReporter, data=data)

        # 天气（只取地球）
        try:
            w_all = TennoReporter._fetch_weather(self=TennoReporter)
            weather_list = [w for w in w_all if w["planet"] == "地球"]
        except:
            weather_list = []

        self.log(f"刷新成功: 商人 {len(traders)}, 入侵 {len(invasions)}, 裂缝 {len(fissures)}, 天气 {len(weather_list)}")

        # 执行推送逻辑（使用 GUI 类中的运行函数）
        TennoReporter._do_discord_notifications(
            self=TennoReporter,
            traders=traders,
            invasions=invasions,
            fissures=fissures
        )

        purge_old(self.state)
        save_state(self.state)

    def loop_forever(self):
        self.log("云端模式已启动（无 GUI）")
        while True:
            self.run_once()
            time.sleep(CHECK_EVERY)

# ══════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════
if __name__ == "__main__":
    # 当 cloud_runner 调用时，不启动 GUI
    if "--headless" in sys.argv:
        bot = HeadlessReporter()
        bot.loop_forever()
    else:
        app = TennoReporter()
        app.mainloop()