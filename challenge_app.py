# -*- coding: utf-8 -*-
"""挑战答题应用（wxPython）

功能说明：
1. 首页 4 个挑战入口按钮：数学 / 语文 / 英语 / 科学。
2. 点击按钮后旧页面销毁、进入对应挑战页，并向本地日志文件追加一条
   点击记录（点击时间 + 点击类型）。
3. 题型全部为选择题，每次从 sqlite 库对应表中随机抽取 20 题
   （筛选条件 continue_pass_count < 3）。
4. 逐题作答，不可返回上一题；全部答完显示正确率与明细列表，并向日志
   文件追加一条答题结果记录（点击类型 + 答题数量 + 正确率 + 获奖情况）。
5. 正确率超过 80% 显示「领取奖励」按钮，并按档位（<85% / <90% / <95% /
   <100% / =100%）显示不同的鼓励语；否则提示鼓励语。
6. 奖励页包含下拉选择框与兑换按钮，兑换时按正确率档位执行对应的
   启动指令（LAUNCH_COMMANDS 待定，先留空并给出提示）。
7. 每答一次题，对应题目的 answer_count（答题次数）加 1。
8. 应用启动即为全屏（按 F11 可切换），全局字号放大 1 倍。
9. 点击"下一题/交卷"按钮的时间间隔必须超过 5 秒才跳转。
10. 两次挑战之间的间隔须超过 30 分钟，冷却中点击会提示剩余等待时间。
"""

import datetime
import os
import platform
import random
import sqlite3
import subprocess
import threading
import time

import wx

# ----------------------------- 配置 -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "challenge.db")     # sqlite 数据库文件
LOG_PATH = os.path.join(BASE_DIR, "click_log.txt")   # 点击记录日志文件
QUESTION_COUNT = 20    # 每次答题数量
PASS_LIMIT = 3         # 出题条件: continue_pass_count < PASS_LIMIT
REWARD_RATE = 0.8      # 正确率超过 80% 才显示奖励按钮
NEXT_MIN_INTERVAL = 5  # "下一题/交卷"按钮点击的最小间隔（秒）
CHALLENGE_COOLDOWN = 30 * 60  # 两次挑战之间的冷却时间（秒），30 分钟

# 挑战冷却时间戳文件
COOLDOWN_PATH = os.path.join(BASE_DIR, "last_challenge.txt")

# 奖励档位: (正确率下限, 档位名, 鼓励语)，超过 80% 后按区间从上到下取档
REWARD_TIERS = [
    (0.80, "80%~85%", "很棒！已经答得很不错了，离满分只差一点点，继续保持哦！"),
    (0.85, "85%~90%", "非常优秀！奖励已经到手，再接再厉更上一层楼！"),
    (0.90, "90%~95%", "太厉害了！距离满分越来越近啦，加油冲刺！"),
    (0.95, "95%~100%", "接近完美！满分就在眼前，向你发起最后冲刺！"),
    (1.00, "100%", "满分！你太棒了，名副其实的答题小达人！"),
]

# 各档位兑换后执行的启动指令（待定），配置后点击兑换即启动对应应用，例如:
# LAUNCH_COMMANDS["100%"] = r"C:\SomeApp\start.exe"
LAUNCH_COMMANDS = {
    "80%~85%": "",
    "85%~90%": "",
    "90%~95%": "",
    "95%~100%": "",
    "100%": "",
}

# 挑战类型: (按钮文字, 数据表名)
CHALLENGES = [
    ("数学挑战", "tbl_math"),
    ("语文挑战", "tbl_literal"),
    ("英语挑战", "tbl_english"),
    ("科学挑战", "tbl_science"),
]

REWARD_OPTIONS = ["选项1", "选项2", "选项3", "选项4"]


# ----------------------------- 工具 -----------------------------
def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _title_font(ctrl, delta=4):
    font = ctrl.GetFont()
    font.PointSize += delta
    ctrl.SetFont(font.Bold())


def write_click_log(click_type):
    """向本地日志文件追加一条点击记录：点击时间 + 点击类型。"""
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{_now()}  {click_type}\n")


def write_result_log(click_type, total, correct, rate, result_text):
    """答题结束后追加一条结果记录：点击类型 + 答题数量 + 正确率 + 结果。"""
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(
            f"{_now()}  {click_type}  答题数量：{total}  "
            f"正确率：{rate:.0%}（答对{correct}）  结果：{result_text}\n"
        )


def get_reward_tier(rate):
    """按正确率返回所属奖励档位 (rate 下限, 档位名, 鼓励语)。

    仅在 rate > REWARD_RATE 时调用；rate >= 档位下限即落入该档，
    因此 100% 精确落入满分档。
    """
    tier = REWARD_TIERS[0]
    for t in REWARD_TIERS:
        if rate >= t[0]:
            tier = t
    return tier


def get_last_challenge_time():
    """读取上次挑战的时间戳，无记录返回 None。"""
    if not os.path.exists(COOLDOWN_PATH):
        return None
    try:
        with open(COOLDOWN_PATH, encoding="utf-8") as f:
            return float(f.read().strip())
    except (ValueError, OSError):
        return None


def write_last_challenge_time():
    """记录当前时间为上次挑战时间。"""
    with open(COOLDOWN_PATH, "w", encoding="utf-8") as f:
        f.write(str(time.time()))


_SPEAKER = None  # Windows SAPI 语音对象缓存
_SPEAK_LOCK = threading.Lock()  # 串行播放，避免多进程声音重叠

def speak_text(text):
    """调用系统 TTS 朗读文本（不阻塞 UI 主线程）。macOS 使用 say，Windows 使用 SAPI.SpVoice。"""
    def _do_speak():
        with _SPEAK_LOCK:
            try:
                if platform.system() == "Darwin":
                    # macOS: 用 run() 等待 say 完成，避免多个 say 进程声音重叠
                    subprocess.run(["say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    global _SPEAKER
                    if _SPEAKER is None:
                        import win32com.client
                        _SPEAKER = win32com.client.Dispatch("SAPI.SpVoice")
                    _SPEAKER.Speak(text)
            except Exception:
                _SPEAKER = None  # Windows 出错时重置，下次重建

    threading.Thread(target=_do_speak, daemon=True).start()


# ----------------------------- 数据库 -----------------------------
def fetch_questions(table):
    """从指定表随机抽取 QUESTION_COUNT 道题（continue_pass_count < PASS_LIMIT）。

    option 字段以 ; 分隔且第一项为正确答案；这里把答案单独存下，
    显示顺序随机打乱，避免正确答案固定出现在第一位。
    """
    sql = (
        f"SELECT id, question, option FROM {table} "
        f"WHERE continue_pass_count < ? ORDER BY RANDOM() LIMIT ?"
    )
    questions = []
    with sqlite3.connect(DB_PATH) as conn:
        for qid, question, option in conn.execute(sql, (PASS_LIMIT, QUESTION_COUNT)):
            options = [o.strip() for o in str(option).replace("；", ";").split(";") if o.strip()]
            if len(options) < 2:
                continue
            questions.append(
                {
                    "id": qid,
                    "question": question,
                    "answer": options[0],
                    "options": random.sample(options, k=len(options)),
                }
            )
    return questions


def update_question_stats(table, qid, is_correct):
    """作答后更新统计：答对 continue_pass_count +1，答错清零；
    answer_count（答题次数）每答一次 +1，并刷新 update_time。

    若不需要在答题时更新这些字段，删除 on_next 中对它的调用即可。
    """
    pass_expr = "continue_pass_count + 1" if is_correct else "0"
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            f"UPDATE {table} SET continue_pass_count = {pass_expr}, "
            f"answer_count = answer_count + 1, update_time = ? WHERE id = ?",
            (_now(), qid),
        )


# --------------------- 首次运行生成示例数据库 ---------------------
SAMPLE_QUESTIONS = {
    "tbl_math": [
        ("math", "3 + 5 × 2 = ?", ["13", "16", "11", "26"]),
        ("math", "一个正方形有几条对称轴？", ["4条", "2条", "1条", "8条"]),
        ("math", "12 × 12 = ?", ["144", "124", "132", "24"]),
        ("math", "一小时有多少秒？", ["3600", "360", "600", "6000"]),
        ("math", "一个三角形内角和是多少度？", ["180度", "90度", "270度", "360度"]),
        ("math", "0.5 用分数表示是多少？", ["1/2", "1/5", "2/3", "1/20"]),
    ],
    "tbl_literal": [
        ("literal", "“床前明月光”的下一句是？", ["疑是地上霜", "低头思故乡", "举头望明月", "夜半钟声到客船"]),
        ("literal", "《静夜思》的作者是谁？", ["李白", "杜甫", "白居易", "王维"]),
        ("literal", "成语“守株待兔”比喻什么？", ["不主动努力，妄想不劳而获", "做事坚持不懈", "善于抓住机会", "勤俭节约"]),
        ("literal", "“春眠不觉晓”的下一句是？", ["处处闻啼鸟", "花落知多少", "夜来风雨声", "红掌拨清波"]),
        ("literal", "“画蛇添足”中的“足”是指什么？", ["脚", "足够", "满足", "画笔"]),
        ("literal", "“三人行，必有我师焉”出自哪部著作？", ["《论语》", "《诗经》", "《道德经》", "《西游记》"]),
    ],
    "tbl_english": [
        ("english", "“apple”的中文意思是？", ["苹果", "香蕉", "橘子", "葡萄"]),
        ("english", "“狗”对应的英文单词是？", ["dog", "cat", "pig", "duck"]),
        ("english", "“book”的复数形式是？", ["books", "bookes", "book", "bookies"]),
        ("english", "I ___ a student. 空格处应填？", ["am", "is", "are", "be"]),
        ("english", "“Monday”是指星期几？", ["星期一", "星期二", "星期日", "星期五"]),
        ("english", "“red”表示什么颜色？", ["红色", "蓝色", "绿色", "黄色"]),
    ],
    "tbl_science": [
        ("science", "地球绕太阳公转一圈需要多长时间？", ["一年", "一天", "一个月", "一小时"]),
        ("science", "标准大气压下，水的沸点是多少摄氏度？", ["100", "90", "80", "120"]),
        ("science", "人体最大的器官是什么？", ["皮肤", "心脏", "肝脏", "大脑"]),
        ("science", "植物进行光合作用主要依靠什么？", ["叶绿体", "根系", "花瓣", "树皮"]),
        ("science", "声音在下列哪种介质中传播最快？", ["固体", "液体", "气体", "真空"]),
        ("science", "彩虹通常有几种颜色？", ["7种", "5种", "3种", "10种"]),
    ],
}


def init_db():
    """数据库文件不存在时，创建 4 张表并写入示例题目。

    若你已有数据库，把文件命名为 challenge.db 放在脚本同目录
    （或修改 DB_PATH），本函数会自动跳过。
    """
    if os.path.exists(DB_PATH):
        return
    now = _now()
    with sqlite3.connect(DB_PATH) as conn:
        for table, items in SAMPLE_QUESTIONS.items():
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {table} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        type TEXT,
                        question TEXT,
                        option TEXT,
                        continue_pass_count INTEGER DEFAULT 0,
                        answer_count INTEGER DEFAULT 0,
                        create_time TEXT,
                        update_time TEXT
                    )"""
            )
            conn.executemany(
                f"INSERT INTO {table} (type, question, option, continue_pass_count, "
                f"create_time, update_time) VALUES (?, ?, ?, 0, ?, ?)",
                [(t, q, ";".join(opts), now, now) for t, q, opts in items],
            )


def ensure_answer_count_column():
    """为 4 张表补齐 answer_count（答题次数）字段，兼容已有的旧库文件。"""
    with sqlite3.connect(DB_PATH) as conn:
        for _, table in CHALLENGES:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if cols and "answer_count" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN answer_count INTEGER DEFAULT 0")


class RoundPlayButton(wx.Panel):
    """圆形播放按钮，带点击反馈效果。"""

    def __init__(self, parent, text, size=36):
        super().__init__(parent, size=(size, size))
        self.SetMinSize((size, size))
        self.SetMaxSize((size, size))
        self.text = text
        self._size = size
        self._pressed = False
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_down)
        self.Bind(wx.EVT_LEFT_UP, self._on_up)
        self.Bind(wx.EVT_LEAVE_WINDOW, self._on_leave)

    def _on_paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        dc.Clear()
        w, h = self.GetSize()
        # 外圈底色
        colour = wx.Colour(70, 130, 200) if not self._pressed else wx.Colour(50, 100, 160)
        dc.SetBrush(wx.Brush(colour))
        dc.SetPen(wx.Pen(wx.Colour(40, 80, 140), 2))
        dc.DrawCircle(w // 2, h // 2, min(w, h) // 2 - 1)
        # 播放三角图标
        cx, cy, r = w // 2, h // 2, min(w, h) // 2 - 6
        points = [wx.Point(cx - r + 6, cy - r + 4), wx.Point(cx - r + 6, cy + r - 4), wx.Point(cx + r - 4, cy)]
        dc.SetBrush(wx.Brush(wx.WHITE))
        dc.SetPen(wx.Pen(wx.WHITE))
        dc.DrawPolygon(points)

    def _on_down(self, event):
        self._pressed = True
        self.Refresh()

    def _on_up(self, event):
        self._pressed = False
        self.Refresh()
        speak_text(self.text)

    def _on_leave(self, event):
        self._pressed = False
        self.Refresh()


# ----------------------------- 页面 -----------------------------
class StartPanel(wx.Panel):
    """首页：4 个挑战入口按钮。"""

    def __init__(self, parent):
        super().__init__(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        title = wx.StaticText(self, label="挑战答题乐园")
        _title_font(title, 8)
        sizer.Add(title, 0, wx.ALIGN_CENTER | wx.TOP, 50)

        grid = wx.GridSizer(2, 2, 20, 20)
        for label, table in CHALLENGES:
            btn = wx.Button(self, label=label)
            btn.SetMinSize((180, 70))
            btn.Bind(wx.EVT_BUTTON, lambda evt, l=label, t=table: self.GetParent().show_challenge(l, t))
            grid.Add(btn, 0, wx.EXPAND)
        sizer.Add(grid, 0, wx.ALIGN_CENTER | wx.TOP, 40)
        self.SetSizer(sizer)


class ChallengePanel(wx.Panel):
    """答题页：一次一题，作答后进入下一题，不可回退。"""

    def __init__(self, parent, label, table):
        super().__init__(parent)
        self.label = label
        self.table = table
        self.questions = fetch_questions(table)
        self.index = 0
        self.records = []   # 每题: {question, chosen, answer, correct}
        self.option_btns = []
        self.last_click_time = None   # 上次点击"下一题/交卷"的时间戳

        if not self.questions:
            wx.CallAfter(self._no_question)
            return

        sizer = wx.BoxSizer(wx.VERTICAL)
        self.progress = wx.StaticText(self, label="")
        sizer.Add(self.progress, 0, wx.ALIGN_CENTER | wx.TOP, 20)

        # 题目行：文本 + 可选播放按钮（英语挑战）
        self.question_row = wx.BoxSizer(wx.HORIZONTAL)
        self.question_text = wx.StaticText(self, label="")
        _title_font(self.question_text, 2)
        self.question_row.Add(self.question_text, 0, wx.ALIGN_CENTER_VERTICAL)
        self.question_play_btn = None
        sizer.Add(self.question_row, 0, wx.ALIGN_CENTER_HORIZONTAL | wx.TOP, 30)

        self.option_sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.option_sizer, 0, wx.ALIGN_CENTER_HORIZONTAL | wx.TOP, 50)

        self.next_btn = wx.Button(self, label="下一题")
        self.next_btn.SetMinSize((120, 36))
        self.next_btn.Bind(wx.EVT_BUTTON, self.on_next)
        sizer.Add(self.next_btn, 0, wx.ALIGN_CENTER | wx.TOP, 25)

        self.SetSizer(sizer)
        self.show_question()

    def _no_question(self):
        wx.MessageBox(
            f"「{self.label}」暂无可用题目（筛选条件 continue_pass_count < {PASS_LIMIT}）",
            "提示", wx.ICON_INFORMATION,
        )
        self.GetParent().show_start()

    def show_question(self):
        q = self.questions[self.index]
        self.progress.SetLabel(f"第 {self.index + 1} / {len(self.questions)} 题")
        self.question_text.SetLabel(f"（{self.index + 1}） {q['question']}")
        self.question_text.Wrap(self.GetParent().text_width())

        # 清理旧的播放按钮
        for btn in self.option_btns:
            self.option_sizer.Detach(btn)
            btn.Destroy()
        self.option_btns = []
        if self.question_play_btn:
            self.question_row.Detach(self.question_play_btn)
            self.question_play_btn.Destroy()
            self.question_play_btn = None
        for btn in getattr(self, "option_play_btns", []):
            btn.Destroy()
        self.option_play_btns = []

        # 英语挑战：在题目文本旁加播放按钮
        if self.table == "tbl_english":
            self.question_play_btn = RoundPlayButton(self, q["question"], size=40)
            self.question_row.Add(self.question_play_btn, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 10)

        for i, text in enumerate(q["options"]):
            row = wx.BoxSizer(wx.HORIZONTAL)
            rb = wx.RadioButton(self, label=text, style=wx.RB_GROUP if i == 0 else 0)
            # 英语挑战：选项被选中时自动播放
            if self.table == "tbl_english":
                rb.Bind(wx.EVT_RADIOBUTTON, lambda evt, t=text: speak_text(t))
            row.Add(rb, 0, wx.ALIGN_CENTER_VERTICAL)
            # 英语挑战：选项旁加播放按钮
            if self.table == "tbl_english":
                play_btn = RoundPlayButton(self, text, size=36)
                row.Add(play_btn, 0, wx.LEFT | wx.ALIGN_CENTER_VERTICAL, 8)
                self.option_play_btns.append(play_btn)
            self.option_sizer.Add(row, 0, wx.BOTTOM, 10)
            self.option_btns.append(rb)

        self.next_btn.SetLabel("交卷" if self.index == len(self.questions) - 1 else "下一题")
        self.Layout()

        # 英语挑战：进入题目后自动播放题目
        if self.table == "tbl_english":
            speak_text(q["question"])

    def on_next(self, event):
        q = self.questions[self.index]
        selected = next((b.GetLabel() for b in self.option_btns if b.GetValue()), None)
        if selected is None:
            wx.MessageBox("请先选择一个答案", "提示", wx.ICON_INFORMATION)
            return
        # 点击间隔必须超过 5 秒才允许跳转
        now = time.monotonic()
        if self.last_click_time is not None and now - self.last_click_time < NEXT_MIN_INTERVAL:
            remain = NEXT_MIN_INTERVAL - (now - self.last_click_time)
            wx.MessageBox(
                f"答题太快啦，请再思考一下\n（还需等待 {remain:.0f} 秒）",
                "提示", wx.ICON_INFORMATION,
            )
            return
        self.last_click_time = now
        is_correct = selected == q["answer"]
        self.records.append(
            {"question": q["question"], "chosen": selected, "answer": q["answer"], "correct": is_correct}
        )
        update_question_stats(self.table, q["id"], is_correct)

        self.index += 1
        if self.index < len(self.questions):
            self.show_question()
        else:
            self.GetParent().show_result(self.label, self.records)


class ResultPanel(wx.Panel):
    """结果页：正确率 + 卡片式答题明细（错题在前）+ 分档鼓励语 + 奖励按钮。"""

    def __init__(self, parent, label, records, tier):
        super().__init__(parent)
        total = len(records)
        correct = sum(1 for r in records if r["correct"])
        rate = correct / total if total else 0.0

        sizer = wx.BoxSizer(wx.VERTICAL)
        title = wx.StaticText(self, label=f"{label} 答题结束")
        _title_font(title)
        sizer.Add(title, 0, wx.ALIGN_CENTER | wx.TOP, 20)

        acc = wx.StaticText(self, label=f"正确率：{rate:.0%}（答对 {correct} / 共 {total} 题）")
        sizer.Add(acc, 0, wx.ALIGN_CENTER | wx.TOP, 10)

        if tier is not None:
            # 获得奖励：按档位显示对应的鼓励语
            tip = wx.StaticText(self, label=tier[2])
            sizer.Add(tip, 0, wx.ALIGN_CENTER | wx.TOP, 12)
            reward_btn = wx.Button(self, label="领取奖励")
            reward_btn.Bind(wx.EVT_BUTTON, lambda evt: self.GetParent().show_reward(tier[1]))
            sizer.Add(reward_btn, 0, wx.ALIGN_CENTER | wx.TOP, 12)
        else:
            tip = wx.StaticText(self, label="离奖励还差一点点，加油哦！")
            sizer.Add(tip, 0, wx.ALIGN_CENTER | wx.TOP, 12)

        # 卡片式明细：错题排在最前，组内保持作答顺序
        seq_records = [dict(r, seq=i) for i, r in enumerate(records, 1)]
        ordered = sorted(seq_records, key=lambda r: r["correct"])  # False(错) 在前

        self.cards_panel = wx.ScrolledWindow(self, style=wx.BORDER_SUNKEN)
        self.cards_panel.SetScrollRate(0, 10)
        cards_sizer = wx.BoxSizer(wx.VERTICAL)
        wrap_w = self.GetParent().text_width()
        for r in ordered:
            cards_sizer.Add(self._make_card(self.cards_panel, r, wrap_w), 0, wx.EXPAND | wx.ALL, 6)
        self.cards_panel.SetSizer(cards_sizer)
        cards_sizer.FitInside(self.cards_panel)   # 按内容计算虚拟高度，支持滚动
        sizer.Add(self.cards_panel, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 20)

        back = wx.Button(self, label="返回首页")
        back.Bind(wx.EVT_BUTTON, lambda evt: self.GetParent().show_start())
        sizer.Add(back, 0, wx.ALIGN_CENTER | wx.TOP | wx.BOTTOM, 15)

        self.SetSizer(sizer)

    def _make_card(self, parent, r, wrap_w):
        """生成单张答题明细卡片：题号/对错 + 题目 + 你的答案（错题附正确答案）。"""
        card = wx.Panel(parent)
        if r["correct"]:
            card.SetBackgroundColour(wx.Colour(232, 245, 233))    # 浅绿
            status_text, status_colour = "答对", wx.Colour(46, 125, 50)
        else:
            card.SetBackgroundColour(wx.Colour(253, 235, 236))    # 浅红
            status_text, status_colour = "答错", wx.Colour(198, 40, 40)

        sizer = wx.BoxSizer(wx.VERTICAL)

        head = wx.BoxSizer(wx.HORIZONTAL)
        no_text = wx.StaticText(card, label=f"第 {r['seq']} 题")
        _title_font(no_text, 1)
        status = wx.StaticText(card, label=status_text)
        status.SetForegroundColour(status_colour)
        _title_font(status, 1)
        head.Add(no_text, 0, wx.RIGHT, 10)
        head.AddStretchSpacer()
        head.Add(status, 0)
        sizer.Add(head, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        q_text = wx.StaticText(card, label=r["question"])
        q_text.Wrap(wrap_w)
        sizer.Add(q_text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        if r["correct"]:
            ans = wx.StaticText(card, label=f"你的答案：{r['chosen']}")
        else:
            ans = wx.StaticText(
                card, label=f"你的答案：{r['chosen']}    正确答案：{r['answer']}"
            )
            ans.SetForegroundColour(status_colour)
        sizer.Add(ans, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        card.SetSizer(sizer)
        return card


class RewardPanel(wx.Panel):
    """奖励兑换页：下拉选择 + 兑换按钮，按正确率档位执行对应启动指令。"""

    def __init__(self, parent, tier_key):
        super().__init__(parent)
        self.tier_key = tier_key
        sizer = wx.BoxSizer(wx.VERTICAL)
        title = wx.StaticText(self, label="恭喜你，挑战成功！")
        _title_font(title, 6)
        sizer.Add(title, 0, wx.ALIGN_CENTER | wx.TOP, 70)

        sizer.Add(wx.StaticText(self, label="请选择奖励："), 0, wx.ALIGN_CENTER | wx.TOP, 40)

        self.choice = wx.Choice(self, choices=REWARD_OPTIONS)
        self.choice.SetMinSize((180, 30))
        sizer.Add(self.choice, 0, wx.ALIGN_CENTER | wx.TOP, 12)

        btn = wx.Button(self, label="兑 换")
        btn.SetMinSize((120, 36))
        btn.Bind(wx.EVT_BUTTON, self.on_exchange)
        sizer.Add(btn, 0, wx.ALIGN_CENTER | wx.TOP, 30)

        back = wx.Button(self, label="返回首页")
        back.Bind(wx.EVT_BUTTON, lambda evt: self.GetParent().show_start())
        sizer.Add(back, 0, wx.ALIGN_CENTER | wx.TOP, 15)

        self.SetSizer(sizer)

    def on_exchange(self, event):
        sel = self.choice.GetSelection()
        if sel == wx.NOT_FOUND:
            wx.MessageBox("请先选择一个奖励", "提示", wx.ICON_INFORMATION)
            return
        reward = self.choice.GetString(sel)
        cmd = LAUNCH_COMMANDS.get(self.tier_key, "")
        if not cmd:
            # 启动指令待定：先提示兑换成功
            wx.MessageBox(
                f"兑换成功：「{reward}」\n（「{self.tier_key}」档位的启动指令待配置）",
                "提示", wx.ICON_INFORMATION,
            )
            return
        try:
            subprocess.Popen(cmd, shell=True)
            wx.MessageBox(f"兑换成功：「{reward}」已启动", "提示", wx.ICON_INFORMATION)
        except OSError as e:
            wx.MessageBox(f"启动失败：{e}", "错误", wx.ICON_ERROR)


# ----------------------------- 主窗口 -----------------------------
class MainFrame(wx.Frame):
    """主窗口：负责页面切换，旧页面销毁（退出）。"""

    def __init__(self):
        super().__init__(
            None,
            title="挑战答题",
            size=(760, 600),
            style=wx.DEFAULT_FRAME_STYLE & ~(wx.RESIZE_BORDER | wx.MAXIMIZE_BOX),
        )
        # 全局字号放大 1 倍（子控件创建时自动继承父窗口字体）
        font = self.GetFont()
        font.PointSize *= 2
        self.SetFont(font)

        self.panel = None
        self.sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self.sizer)
        self.show_start()
        self.Centre()
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char)   # F11 切换全屏

    def on_char(self, event):
        if event.GetKeyCode() == wx.WXK_F11:
            self.ShowFullScreen(not self.IsFullScreen())
        else:
            event.Skip()

    def text_width(self):
        """正文换行宽度：全屏时限制最大宽度，便于阅读。"""
        return min(max(self.GetClientSize().GetWidth() - 160, 400), 1000)

    def _swap(self, new_panel):
        if self.panel is not None:
            self.sizer.Detach(self.panel)
            self.panel.Destroy()
        self.panel = new_panel
        self.sizer.Add(new_panel, 1, wx.EXPAND)
        self.sizer.Layout()

    def show_start(self):
        self.SetTitle("挑战答题")
        self._swap(StartPanel(self))

    def show_challenge(self, label, table):
        # 冷却检查：两次挑战间隔须超过 CHALLENGE_COOLDOWN 秒
        last = get_last_challenge_time()
        if last is not None:
            elapsed = time.time() - last
            if elapsed < CHALLENGE_COOLDOWN:
                remain = int(CHALLENGE_COOLDOWN - elapsed)
                mins, secs = divmod(remain, 60)
                wx.MessageBox(
                    f"挑战冷却中，请稍后再试\n（还需等待 {mins} 分 {secs} 秒）",
                    "提示", wx.ICON_INFORMATION,
                )
                return
        write_click_log(label)  # 记录点击时间与点击类型
        write_last_challenge_time()  # 记录本次挑战时间，开始冷却
        try:
            panel = ChallengePanel(self, label, table)
        except sqlite3.Error as e:
            wx.MessageBox(f"读取题目失败：{e}", "错误", wx.ICON_ERROR)
            return
        self.SetTitle(f"挑战答题 - {label}")
        self._swap(panel)

    def show_result(self, label, records):
        total = len(records)
        correct = sum(1 for r in records if r["correct"])
        rate = correct / total if total else 0.0
        tier = get_reward_tier(rate) if rate > REWARD_RATE else None
        write_result_log(
            label, total, correct, rate,
            f"获得奖励（{tier[1]}档）" if tier else "未获得奖励",
        )
        self.SetTitle(f"挑战答题 - {label} 结果")
        self._swap(ResultPanel(self, label, records, tier))

    def show_reward(self, tier_key):
        self.SetTitle("挑战答题 - 领取奖励")
        self._swap(RewardPanel(self, tier_key))


def main():
    init_db()
    ensure_answer_count_column()
    app = wx.App(False)
    frame = MainFrame()
    frame.Show()
    frame.ShowFullScreen(True)   # 启动即全屏，F11 可切换
    app.MainLoop()


if __name__ == "__main__":
    main()
