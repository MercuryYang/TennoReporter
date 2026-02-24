# TennoReporter — Warframe 世界状态监控 GUI
*A lightweight real-time Warframe world-state monitor with Discord alerts.*

---

## 🛰 项目简介

**TennoReporter** 是一款基于 **Python + Tkinter** 开发的  
**Warframe 世界状态监控工具**，拥有深色科幻风格界面，无需额外 GUI 依赖。

它可实时监控 Warframe 世界状态，并自动将重要事件推送到 Discord Webhook。

适用于：

- 想持续监控虚空商人（Baro Ki’Teer）
- 想捕捉稀有奖励入侵任务
- 想追踪钢铁模式裂缝（Steel Path Fissures）
- 想了解地球昼夜天气变化

---

## ✨ 功能特色

### ✔ 图形化实时监控
- 虚空商人（Void Trader）
- 稀有奖励入侵（Rare Invasions）
- 钢铁模式虚空裂缝（Steel Path Fissures）
- 星球天气（地球、平原、金星、魔胎之境）
- 实时日志窗口
- 自动刷新计时器

### ✔ 自动 Discord 推送
自动识别事件变化，并将以下内容发送到 Discord：

- 虚空商人提前 **3 天预告**
- 虚空商人抵达提醒
- 稀有奖励入侵（Forma / Reactor / Riven 等）
- 钢铁裂缝推送：**新裂缝出现时推送所有未结束裂缝**
- 地球昼夜变化提醒

### ✔ 状态持久化
- 推送记录写入 `state.json`
- 智能去重，不重复推送

### ✔ 稳定后台运行
- 后台线程轮询 API
- Tkinter 主线程更新 UI
- 断网 / 超时自动重试

---

## 📡 自动推送规则（当前版本）

### 🛸 虚空商人（Void Trader）
| 触发条件 | 推送内容 |
|----------|----------|
| 距离抵达 ≤ **72 小时** 且未推送过 | 「虚空商人提前预告」 |
| 商人抵达时且未推送过 | 「虚空商人已到达」 |

---

### ⚠ 稀有奖励入侵（Rare Invasions）
若奖励中包含：
- Forma  
- Orokin Catalyst  
- Orokin Reactor  
- Aura Forma  
- Riven  
- Sentinel Weapon BP  
- Alad V Coordinates  

且未推送过 → 推送一次。

---

### 🌀 钢铁裂缝（Steel Path Fissures）
采用增强推送规则：

| 触发条件 | 推送行为 |
|----------|----------|
| 新裂缝出现 | 推送该裂缝 + **所有未结束旧裂缝** |
| 没有新裂缝 | 只推送未推送过的裂缝 |

---

### 🌦 地球天气推送
仅监控 “地球” 昼夜变化：

| 触发条件 | 推送内容 |
|----------|----------|
| 新昼夜状态（白天/夜晚）+ 切换时间组合未推送 | 「地球天气更新」 |

---

## 📥 安装与运行

1. 克隆仓库
git clone https://github.com/<yourname>/TennoReporter.git
cd TennoReporter

2. 安装依赖
pip install requests
Tkinter 属 Python 自带库，无需安装。

3. 配置 Webhook

在代码顶部修改：
WEBHOOK_URL = "https://discord.com/api/webhooks/xxxx/yyyy"

4. 启动程序
python TennoReporter.py

📂 项目结构
TennoReporter/
│── TennoReporter.py      # 主程序（GUI + 逻辑）
│── state.json            # 推送历史记录（自动生成）
│── README.md             # 本文档
└── ...
