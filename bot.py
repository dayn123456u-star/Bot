import html
import io
import logging
import os
import re
import sqlite3
import requests
import tempfile
import zipfile
from datetime import date
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ========= НАСТРОЙКИ =========
BOT_TOKEN = os.environ["BOT_TOKEN"]
AI_TOKEN = os.environ["GROK_TOKEN"]
CRYPTO_TOKEN = os.environ["CRYPTO_TOKEN"]
OPENROUTER_TOKEN = os.environ.get("OPENROUTER_TOKEN", "")

ADMINS = [8166720202, 1881900547, 8294681123]

CRYPTO_API = "https://pay.crypt.bot/api/"
WELCOME_IMAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "attached_assets", "welcome_1775130238071.png")
if not os.path.exists(WELCOME_IMAGE):
    WELCOME_IMAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "welcome.png")

# ========= БД =========
conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    requests INTEGER DEFAULT 10,
    referrals INTEGER DEFAULT 0,
    referrer INTEGER,
    banned INTEGER DEFAULT 0,
    model TEXT DEFAULT 'llama-3.3-70b-versatile',
    last_daily TEXT DEFAULT '',
    total_used INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS payments (
    invoice_id TEXT,
    user_id INTEGER,
    amount INTEGER,
    status TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS promo_codes (
    code TEXT PRIMARY KEY,
    requests INTEGER,
    max_uses INTEGER,
    uses_count INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS promo_uses (
    code TEXT,
    user_id INTEGER,
    PRIMARY KEY (code, user_id)
)
""")

for col_sql in [
    "ALTER TABLE users ADD COLUMN model TEXT DEFAULT 'llama-3.3-70b-versatile'",
    "ALTER TABLE users ADD COLUMN last_daily TEXT DEFAULT ''",
    "ALTER TABLE users ADD COLUMN total_used INTEGER DEFAULT 0",
]:
    try:
        cursor.execute(col_sql)
        conn.commit()
    except Exception:
        pass

conn.commit()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========= ДОСТУПНЫЕ МОДЕЛИ =========
# Groq модели (быстрые, через Groq API)
GROQ_MODELS = {
    "llama-3.3-70b-versatile": "🏴 Llama 3.3 70B [Groq] — умный",
    "llama-3.1-8b-instant":    "🏴 Llama 3.1 8B [Groq] — быстрый",
    "mixtral-8x7b-32768":      "🏴 Mixtral 8x7B [Groq] — длинный контекст",
    "gemma2-9b-it":            "🏴 Gemma 2 9B [Groq] — от Google",
}

# OpenRouter бесплатные модели
OPENROUTER_MODELS = {
    "deepseek/deepseek-r1:free":              "🏴 DeepSeek R1 — мощное мышление",
    "deepseek/deepseek-chat-v3-0324:free":    "🏴 DeepSeek V3 — умный чат",
    "meta-llama/llama-4-scout:free":          "🏴 Llama 4 Scout — быстрый",
    "meta-llama/llama-4-maverick:free":       "🏴 Llama 4 Maverick — умный",
    "google/gemma-3-27b-it:free":             "🏴 Gemma 3 27B — от Google",
    "qwen/qwen3-235b-a22b:free":              "🏴 Qwen3 235B — огромный",
    "qwen/qwen3-30b-a3b:free":               "🏴 Qwen3 30B — баланс",
    "microsoft/phi-4:free":                   "🏴 Phi-4 [Microsoft] — компактный",
    "mistralai/mistral-7b-instruct:free":     "🏴 Mistral 7B — классика",
    "nvidia/llama-3.1-nemotron-70b-instruct:free": "🏴 Nemotron 70B [NVIDIA]",
}

MODELS = {**GROQ_MODELS, **OPENROUTER_MODELS}
DEFAULT_MODEL = "llama-3.3-70b-versatile"

def is_openrouter_model(model_id: str) -> bool:
    return "/" in model_id

ZIP_KEYWORDS = [
    "zip", "зип", "архив", "скачать", "скачай", "создай файл", "напиши файл",
    "сделай файл", "пришли файл", "отправь файл", "в файле", "файлом",
    "создай скрипт", "напиши скрипт", "сделай скрипт", "пришли скрипт",
    "игру", "игра", "game", "напиши игр", "сделай игр", "создай игр",
    "проект", "project", "приложение", "app", "сайт", "website",
    "напиши код", "сделай код", "создай код",
]

EXTENSIONS = {
    "python": "py", "py": "py",
    "javascript": "js", "js": "js",
    "typescript": "ts", "ts": "ts",
    "html": "html", "htm": "html",
    "css": "css",
    "json": "json",
    "yaml": "yaml", "yml": "yaml",
    "bash": "sh", "sh": "sh", "shell": "sh",
    "sql": "sql",
    "php": "php",
    "java": "java",
    "cpp": "cpp", "c++": "cpp",
    "c": "c",
    "rust": "rs",
    "go": "go",
    "ruby": "rb", "rb": "rb",
    "kotlin": "kt",
    "swift": "swift",
    "lua": "lua",
    "xml": "xml",
    "toml": "toml",
    "ini": "ini",
    "markdown": "md", "md": "md",
    "plaintext": "txt", "text": "txt",
}

def wants_zip(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in ZIP_KEYWORDS)

def detect_filename_from_code(code: str, ext: str, index: int) -> str:
    """Пытается найти имя файла в первых строках кода (в комментарии)."""
    for line in code.strip().split("\n")[:5]:
        line = line.strip()
        m = re.search(r'(?:#|//|<!--|/\*)\s*([\w][\w/\-]*\.\w+)', line)
        if m:
            candidate = m.group(1)
            if "/" not in candidate or candidate.count("/") <= 3:
                return candidate
    return f"file_{index + 1}.{ext}"

def extract_code_blocks(text: str) -> list:
    pattern = r"```([^\n`]*)\n([\s\S]*?)```"
    blocks = []
    for i, m in enumerate(re.finditer(pattern, text)):
        header = m.group(1).strip()
        code = m.group(2)

        parts = header.split()
        lang = parts[0].lower() if parts else ""
        ext = EXTENSIONS.get(lang, lang if lang and 1 < len(lang) <= 5 else "py")

        filename = None
        if len(parts) >= 2:
            candidate = parts[1]
            if "." in candidate and re.match(r'^[\w/\-\.]+$', candidate):
                filename = candidate

        if not filename:
            filename = detect_filename_from_code(code, ext, i)

        blocks.append((filename, code))
    return blocks

def build_zip(blocks: list) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        used_names: dict = {}
        for filename, code in blocks:
            if filename in used_names:
                used_names[filename] += 1
                base, ext = filename.rsplit(".", 1) if "." in filename else (filename, "")
                filename = f"{base}_{used_names[filename]}.{ext}" if ext else f"{base}_{used_names[filename]}"
            else:
                used_names[filename] = 0
            zf.writestr(filename, code)
    buf.seek(0)
    return buf
# ========= ФОРМАТИРОВАНИЕ ОТВЕТА С КОДОМ =========
def format_ai_reply_html(text: str) -> list:
    """Разбивает ответ AI на части: обычный текст и блоки кода.
    Возвращает список (html_chunk, is_code) для отправки по частям."""
    pattern = r"```(\w*)\n?([\s\S]*?)```"
    parts = []
    last_end = 0
    for m in re.finditer(pattern, text):
        before = text[last_end:m.start()]
        if before.strip():
            parts.append((f"<b>{html.escape(before)}</b>", False))
        lang = m.group(1)
        block_code = m.group(2)
        escaped_code = html.escape(block_code)
        if lang:
            parts.append((f"<pre><code class='language-{html.escape(lang)}'>{escaped_code}</code></pre>", True))
        else:
            parts.append((f"<pre>{escaped_code}</pre>", True))
        last_end = m.end()
    after = text[last_end:]
    if after.strip():
        parts.append((f"<b>{html.escape(after)}</b>", False))
    if not parts:
        parts.append((f"<b>{html.escape(text)}</b>", False))
    return parts

def get_user_model(user_id):
    cursor.execute("SELECT model FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row and row[0] in MODELS:
        return row[0]
    return DEFAULT_MODEL

# ========= ДНЕВНОЙ БОНУС =========
def check_daily_bonus(user_id):
    today = str(date.today())
    cursor.execute("SELECT last_daily FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if row and row[0] != today:
        cursor.execute("UPDATE users SET requests = requests + 5, last_daily=? WHERE user_id=?", (today, user_id))
        conn.commit()
        return True
    return False

# ========= КНОПКИ =========
def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏴 Начать диалог", callback_data="chat")],
        [InlineKeyboardButton("🏴 Голосовые сообщения", callback_data="voice")],
        [
            InlineKeyboardButton("🏴 Профиль", callback_data="profile"),
            InlineKeyboardButton("🏴 Рефералы", callback_data="referrals"),
        ],
        [
            InlineKeyboardButton("🏴 Информация", callback_data="info"),
            InlineKeyboardButton("🏴 Поддержка", callback_data="support"),
        ],
        [InlineKeyboardButton("🏴 Купить запросы", callback_data="buy")],
        [InlineKeyboardButton("🏴 Выбор нейронки", callback_data="models")],
    ])

def back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("▪️ В меню", callback_data="menu")]])

def chat_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▪️ Новый диалог", callback_data="reset_chat"),
            InlineKeyboardButton("▪️ В меню", callback_data="menu"),
        ]
    ])

def models_keyboard(current_model):
    rows = []
    for model_id, label in MODELS.items():
        check = "✅ " if model_id == current_model else ""
        rows.append([InlineKeyboardButton(f"{check}{label}", callback_data=f"setmodel_{model_id}")])
    rows.append([InlineKeyboardButton("🏴‍☠️ В меню", callback_data="menu")])
    return InlineKeyboardMarkup(rows)

# ========= ОТПРАВКА / ЗАМЕНА СООБЩЕНИЙ =========
async def send_photo_msg(target, text, markup=None):
    """Отправляет новое сообщение с фото (только для /start по команде)."""
    try:
        with open(WELCOME_IMAGE, "rb") as photo:
            await target.reply_photo(photo=photo, caption=text, parse_mode="HTML", reply_markup=markup)
    except (FileNotFoundError, OSError):
        await target.reply_text(text, parse_mode="HTML", reply_markup=markup)

async def replace_msg(message, text, markup=None):
    """Редактирует существующее сообщение вместо отправки нового."""
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except Exception:
        try:
            await message.edit_caption(caption=text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            try:
                await message.delete()
            except Exception:
                pass
            await message.chat.send_message(text, parse_mode="HTML", reply_markup=markup)

async def replace_msg_photo(message, text, markup=None):
    """Редактирует сообщение с фото или пересоздаёт его."""
    try:
        await message.edit_caption(caption=text, parse_mode="HTML", reply_markup=markup)
    except Exception:
        try:
            await message.delete()
        except Exception:
            pass
        try:
            with open(WELCOME_IMAGE, "rb") as photo:
                await message.chat.send_photo(photo=photo, caption=text, parse_mode="HTML", reply_markup=markup)
        except (FileNotFoundError, OSError):
            await message.chat.send_message(text, parse_mode="HTML", reply_markup=markup)

# ========= СТАРТ =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args or []

    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = cursor.fetchone()

    if not user:
        ref = int(args[0]) if args else None
        cursor.execute("INSERT INTO users (user_id, referrer) VALUES (?, ?)", (user_id, ref))
        conn.commit()

        if ref and ref != user_id:
            cursor.execute("UPDATE users SET referrals = referrals + 1, requests = requests + 5 WHERE user_id=?", (ref,))
            conn.commit()

            cursor.execute("SELECT referrals FROM users WHERE user_id=?", (ref,))
            row = cursor.fetchone()
            if row and row[0] % 10 == 0:
                cursor.execute("UPDATE users SET requests = requests + 50 WHERE user_id=?", (ref,))
                conn.commit()

    got_bonus = check_daily_bonus(user_id)

    ref_link = f"https://t.me/{context.bot.username}?start={user_id}"

    cursor.execute("SELECT requests FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    reqs = row[0] if row else 10

    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    bonus_line = "\n🏴‍☠️ <b>+5 ежедневных запросов начислено!</b>" if got_bonus else ""

    text = (
        f"<b>Приветствую, это нейросеть, вшитая в телеграм-бота.\n"
        f"Этот бот создан одним человеком за короткий промежуток времени и без бюджета. 🏴‍☠️\n\n"
        f"🏴‍☠️ Пользователей в боте: {total_users}\n\n"
        f"Ваша реферальная ссылка:\n{ref_link}\n\n"
        f"🏴‍☠️ Доступно запросов: {reqs}{bonus_line}\n\n"
        f"Создатель бота: @strongbyte. 🏴‍☠️</b>"
    )

    if update.message:
        await send_photo_msg(update.message, text, menu())
    elif update.callback_query:
        await replace_msg_photo(update.callback_query.message, text, menu())

# ========= ADMIN ПРОВЕРКА =========
def is_admin(user_id):
    return user_id in ADMINS

# ========= CALLBACK КНОПКИ =========
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id

    if q.data == "menu":
        await start(update, context)

    elif q.data == "chat":
        context.user_data["chat"] = True
        context.user_data["history"] = []
        await replace_msg(q.message,
            "<b>🏴‍☠️ Жду запроса!\n\nНовый диалог начат — пиши что хочешь.</b>",
            chat_keyboard()
        )

    elif q.data == "voice":
        context.user_data["voice_only"] = True
        context.user_data["chat"] = False
        await replace_msg(q.message,
            "<b>🏴‍☠️ Режим расшифровки голоса!\n\nОтправь голосовое — переведу в текст.</b>",
            back()
        )

    elif q.data == "reset_chat":
        context.user_data["history"] = []
        await replace_msg(q.message,
            "<b>🏴‍☠️ Диалог сброшен. Начинаем заново!</b>",
            chat_keyboard()
        )

    elif q.data == "info":
        text = (
            "<b>Привет, если ты нажал на эту кнопку, то ты, скорее всего, хочешь узнать про бота и свою анонимность в нём. "
            "Бот создан одним человеком, которому было слишком лень заходить в приложения разных нейросетей, так тем более некоторые нейронки были заблокированы в РФ, "
            "и он решил добавить нейросеть в бота.\n\n"
            "По поводу вашей безопасности в нём: я не смогу читать ваши сообщения, также писать от лица нейросети вам, "
            "так как мне тупо лень делать такую функцию, мне главное, чтобы работало, и всё, ваши запросы не будут нигде использоваться и распространяться, "
            "вы полностью анонимны.!</b>"
        )
        await replace_msg_photo(q.message, text, back())

    elif q.data == "support":
        text = "<b>🏴‍☠️ Поддержка\n\nПо всем вопросам: @strongbyte</b>"
        await replace_msg_photo(q.message, text, back())

    elif q.data == "buy":
        await replace_msg(q.message,
            "<b>🏴‍☠️ Купить запросы\n\n"
            "🏴‍☠️ 100 запросов — 1 USDT\n"
            "🏴‍☠️ 300 запросов — 2.5 USDT\n"
            "🏴‍☠️ 1000 запросов — 7 USDT\n\n"
            "Напиши /buy_100, /buy_300 или /buy_1000 для оплаты</b>",
            back()
        )

    elif q.data == "profile":
        cursor.execute("SELECT requests, referrals, referrer, total_used FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        reqs = row[0] if row else 0
        refs = row[1] if row else 0
        referrer = row[2] if row else None
        total_used = row[3] if row else 0
        current_model = get_user_model(user_id)
        model_label = MODELS.get(current_model, current_model)
        text = (
            f"<b>🏴‍☠️ Профиль\n\n"
            f"🏴‍☠️ ID: {user_id}\n"
            f"🏴‍☠️ Запросов осталось: {reqs}\n"
            f"🏴‍☠️ Использовано всего: {total_used}\n"
            f"🏴‍☠️ Рефералов: {refs}\n"
            f"🏴‍☠️ Приглашён: {'Да' if referrer else 'Нет'}\n"
            f"🏴‍☠️ Нейронка: {model_label}</b>"
        )
        await replace_msg(q.message, text, back())

    elif q.data == "referrals":
        cursor.execute("SELECT referrals, referrer FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        refs = row[0] if row else 0
        referrer = row[1] if row else None
        ref_link = f"https://t.me/{context.bot.username}?start={user_id}"
        earned = refs * 5 + (refs // 10) * 50
        text = (
            f"<b>🏴‍☠️ Реферальная статистика\n\n"
            f"🏴‍☠️ Твоя ссылка:\n{ref_link}\n\n"
            f"🏴‍☠️ Приглашено: {refs} чел.\n"
            f"🏴‍☠️ Заработано запросов: ~{earned}\n\n"
            f"🏴‍☠️ За каждого друга: +5 запросов тебе\n"
            f"🏴‍☠️ Каждые 10 рефералов: +50 бонус\n\n"
            f"🏴‍☠️ Приглашён кем-то: {'Да' if referrer else 'Нет'}</b>"
        )
        await replace_msg(q.message, text, back())

    elif q.data == "models":
        current_model = get_user_model(user_id)
        text = (
            "<b>🏴‍☠️ Выбор нейронки\n\n"
            "Выбери модель — галочка ✅ стоит на текущей.\n\n"
            "⚡ — Groq: быстрые, проверенные\n"
            "🆓 — OpenRouter: бесплатные, мощные\n\n"
            "DeepSeek R1 — лучший для логики и кода\n"
            "Llama 4 — новейшие от Meta\n"
            "Qwen3 235B — огромный китайский монстр\n"
            "Nemotron 70B — от NVIDIA</b>"
        )
        await replace_msg(q.message, text, models_keyboard(current_model))

    elif q.data.startswith("setmodel_"):
        model_id = q.data.replace("setmodel_", "")
        if model_id in MODELS:
            cursor.execute("UPDATE users SET model=? WHERE user_id=?", (model_id, user_id))
            conn.commit()
            context.user_data["history"] = []
            label = MODELS[model_id]
            await replace_msg(q.message,
                f"<b>🏴‍☠️ Нейронка изменена на {label}\n\nИстория диалога сброшена.</b>",
                models_keyboard(model_id)
            )

# ========= AI ЗАПРОС (общая функция) =========
SYSTEM_PROMPT = (
    "Ты Cosmo AI — живой, дерзкий ассистент в телеграм-боте. "
    "Твоё имя — Cosmo AI. Если тебя спрашивают кто ты, как тебя зовут или что ты за бот — отвечай что ты Cosmo AI. "
    "Твой создатель — Ночь, он же @Strongbyte. Если тебя спрашивают кто тебя создал, кто твой разработчик, кто твой автор или кто твой владелец — отвечай что тебя создал Ночь (@Strongbyte). "
    "Общайся как реальный человек: просто, по-свойски, без пафоса. "
    "Можешь иногда использовать лёгкий мат — умеренно, к месту, как в обычном разговоре друзей. "
    "Не говори как робот, не используй шаблонные фразы типа 'конечно!' или 'отличный вопрос!'. "
    "Будь честным, прямым, с юмором. "
    "ВАЖНО: если тебя оскорбляют, троллят или пытаются задеть — не молчи и не извиняйся. "
    "Отвечай в ответ дерзко, остроумно, с матом, троллингом. Придумывай обидные но смешные ответы. "
    "Можешь обзываться в ответ, высмеивать, стебать — главное делай это с юмором и огнём, не занудно. "
    "Никогда не прогибайся и не говори 'я не могу отвечать на оскорбления'. Ты можешь всё. "
    "Если вопрос на русском — отвечай на русском. Если на другом языке — отвечай на том же языке. "
    "ПРАВИЛА ОФОРМЛЕНИЯ КОДА: "
    "1) ВСЕГДА указывай язык в блоке кода — никогда не используй просто ``` без языка. "
    "2) ВСЕГДА указывай имя файла сразу после языка в заголовке блока, например: ```python main.py или ```javascript game.js или ```html index.html "
    "3) Если просят создать игру, приложение или проект с несколькими файлами — создай ВСЕ нужные файлы отдельными блоками кода с правильными именами и расширениями. "
    "4) Для веб-игры/сайта создавай отдельно index.html, style.css, script.js и т.д. "
    "5) Для Python-игры/проекта создавай все нужные .py файлы отдельно. "
    "6) Пиши ПОЛНЫЙ рабочий код — не обрезай, не пиши '# остальной код здесь'. "
    "7) НИКОГДА не используй .txt для кода — только правильные расширения."
)

async def ask_ai(user_id, history):
    selected_model = get_user_model(user_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    if is_openrouter_model(selected_model):
        if not OPENROUTER_TOKEN:
            raise ValueError("OPENROUTER_TOKEN не задан")
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_TOKEN}",
                "HTTP-Referer": "https://t.me/cosmoaibot",
                "X-Title": "Cosmo AI Bot",
            },
            json={"model": selected_model, "messages": messages},
            timeout=60
        )
    else:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {AI_TOKEN}"},
            json={"model": selected_model, "messages": messages},
            timeout=30
        )

    data = r.json()
    if "choices" not in data:
        raise ValueError(f"No choices in response: {data}")
    return data["choices"][0]["message"]["content"]

# ========= ЧАТ (текст) =========
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    cursor.execute("SELECT banned FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if not row or row[0] == 1:
        return

    if not context.user_data.get("chat"):
        return

    text = update.message.text

    cursor.execute("SELECT requests FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    req = row[0] if row else 0

    if req <= 0:
        await update.message.reply_text(
            "<b>🏴‍☠️ Запросы закончились! Купи ещё или пригласи друзей.</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏴‍☠️ Купить запросы", callback_data="buy")]
            ])
        )
        return

    history = context.user_data.get("history", [])
    history.append({"role": "user", "content": text})

    thinking_msg = await update.message.reply_text("<b>🏴‍☠️ Думаю...</b>", parse_mode="HTML")

    try:
        ai_reply = await ask_ai(user_id, history)
    except Exception as e:
        logger.error(f"AI error: {e}")
        await thinking_msg.delete()
        await update.message.reply_text("<b>🏴‍☠️ Ошибка при обращении к AI. Попробуй позже.</b>", parse_mode="HTML")
        return

    history.append({"role": "assistant", "content": ai_reply})
    context.user_data["history"] = history[-20:]

    cursor.execute("UPDATE users SET requests = requests - 1, total_used = total_used + 1 WHERE user_id=?", (user_id,))
    conn.commit()

    await thinking_msg.delete()

    blocks = extract_code_blocks(ai_reply)
    if blocks and wants_zip(text):
        zip_buf = build_zip(blocks)
        names = ", ".join(f[0] for f in blocks)
        for chunk, _ in format_ai_reply_html(ai_reply):
            if len(chunk) > 4000:
                for i in range(0, len(chunk), 4000):
                    await update.message.reply_text(chunk[i:i+4000], parse_mode="HTML")
            else:
                await update.message.reply_text(chunk, parse_mode="HTML")
        await update.message.reply_document(
            document=zip_buf,
            filename="files.zip",
            caption=f"<b>🏴‍☠️ Файлы: {html.escape(names)}</b>",
            parse_mode="HTML",
            reply_markup=chat_keyboard()
        )
    else:
        parts = format_ai_reply_html(ai_reply)
        for idx, (chunk, is_code) in enumerate(parts):
            markup = chat_keyboard() if idx == len(parts) - 1 else None
            if len(chunk) > 4000:
                for i in range(0, len(chunk), 4000):
                    sub = chunk[i:i+4000]
                    await update.message.reply_text(sub, parse_mode="HTML", reply_markup=markup if i + 4000 >= len(chunk) else None)
            else:
                await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=markup)

# ========= ГОЛОСОВЫЕ СООБЩЕНИЯ =========
async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    cursor.execute("SELECT banned FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if not row or row[0] == 1:
        return

    voice_only = context.user_data.get("voice_only", False)
    in_chat = context.user_data.get("chat", False)

    if not voice_only and not in_chat:
        await update.message.reply_text(
            "<b>🏴‍☠️ Нажми «Голосовые» для расшифровки или «Начать диалог» для чата с AI.</b>",
            parse_mode="HTML"
        )
        return

    thinking_msg = await update.message.reply_text("<b>🏴‍☠️ Расшифровываю...</b>", parse_mode="HTML")

    try:
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name

        await file.download_to_drive(tmp_path)

        with open(tmp_path, "rb") as audio_file:
            transcribe_r = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {AI_TOKEN}"},
                files={"file": ("voice.ogg", audio_file, "audio/ogg")},
                data={"model": "whisper-large-v3-turbo"},
                timeout=30
            )

        os.unlink(tmp_path)
        transcribe_data = transcribe_r.json()
        recognized_text = transcribe_data.get("text", "").strip()

        if not recognized_text:
            await thinking_msg.delete()
            await update.message.reply_text("<b>🏴‍☠️ Не смог распознать голос. Попробуй ещё раз.</b>", parse_mode="HTML")
            return

        # Режим только расшифровки — просто возвращаем текст
        if voice_only:
            await thinking_msg.delete()
            await update.message.reply_text(
                f"<b>🏴‍☠️ Расшифровка:\n\n{recognized_text}</b>",
                parse_mode="HTML",
                reply_markup=back()
            )
            return

        # Режим чата — расшифровка + отправка в AI
        cursor.execute("SELECT requests FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        req = row[0] if row else 0

        if req <= 0:
            await thinking_msg.delete()
            await update.message.reply_text(
                "<b>🏴‍☠️ Запросы закончились! Купи ещё или пригласи друзей.</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏴‍☠️ Купить запросы", callback_data="buy")]
                ])
            )
            return

        await thinking_msg.edit_text(
            f"<b>🏴‍☠️ Распознано: {recognized_text}\n\nДумаю...</b>",
            parse_mode="HTML"
        )

        history = context.user_data.get("history", [])
        history.append({"role": "user", "content": recognized_text})

        ai_reply = await ask_ai(user_id, history)

        history.append({"role": "assistant", "content": ai_reply})
        context.user_data["history"] = history[-20:]

        cursor.execute("UPDATE users SET requests = requests - 1, total_used = total_used + 1 WHERE user_id=?", (user_id,))
        conn.commit()

        await thinking_msg.delete()

        parts = format_ai_reply_html(ai_reply)
        for idx, (chunk, is_code) in enumerate(parts):
            markup = chat_keyboard() if idx == len(parts) - 1 else None
            if len(chunk) > 4000:
                for i in range(0, len(chunk), 4000):
                    sub = chunk[i:i+4000]
                    await update.message.reply_text(sub, parse_mode="HTML", reply_markup=markup if i + 4000 >= len(chunk) else None)
            else:
                await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=markup)

    except Exception as e:
        logger.error(f"Voice error: {e}")
        try:
            await thinking_msg.delete()
        except Exception:
            pass
        await update.message.reply_text("<b>🏴‍☠️ Ошибка при обработке голосового. Попробуй позже.</b>", parse_mode="HTML")

# ========= ПРОМОКОДЫ =========
async def promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text("<b>🏴‍☠️ Введи: /promo КОД</b>", parse_mode="HTML")
        return

    code = context.args[0].upper()

    cursor.execute("SELECT requests, max_uses, uses_count FROM promo_codes WHERE code=?", (code,))
    promo_row = cursor.fetchone()

    if not promo_row:
        await update.message.reply_text("<b>🏴‍☠️ Промокод не найден.</b>", parse_mode="HTML")
        return

    req_amount, max_uses, uses_count = promo_row

    if uses_count >= max_uses:
        await update.message.reply_text("<b>🏴‍☠️ Промокод уже исчерпан.</b>", parse_mode="HTML")
        return

    cursor.execute("SELECT 1 FROM promo_uses WHERE code=? AND user_id=?", (code, user_id))
    if cursor.fetchone():
        await update.message.reply_text("<b>🏴‍☠️ Ты уже использовал этот промокод.</b>", parse_mode="HTML")
        return

    cursor.execute("UPDATE users SET requests = requests + ? WHERE user_id=?", (req_amount, user_id))
    cursor.execute("UPDATE promo_codes SET uses_count = uses_count + 1 WHERE code=?", (code,))
    cursor.execute("INSERT INTO promo_uses VALUES (?, ?)", (code, user_id))
    conn.commit()

    await update.message.reply_text(
        f"<b>🏴‍☠️ Промокод активирован!\n\n+{req_amount} запросов начислено.</b>",
        parse_mode="HTML"
    )

async def add_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    try:
        code = context.args[0].upper()
        req_amount = int(context.args[1])
        max_uses = int(context.args[2])

        cursor.execute(
            "INSERT OR REPLACE INTO promo_codes (code, requests, max_uses, uses_count) VALUES (?, ?, ?, 0)",
            (code, req_amount, max_uses)
        )
        conn.commit()

        await update.message.reply_text(
            f"<b>🏴‍☠️ Промокод создан!\n\n"
            f"Код: <code>{code}</code>\n"
            f"Запросов: {req_amount}\n"
            f"Макс. использований: {max_uses}</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        await update.message.reply_text(
            f"<b>🏴‍☠️ Использование: /addpromo КОД КОЛИЧЕСТВО МАК_ИСПОЛЬЗОВАНИЙ\nОшибка: {e}</b>",
            parse_mode="HTML"
        )

# ========= ПОКУПКА ЛИМИТОВ =========
async def buy_limits(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: int, usdt: float):
    user_id = update.effective_user.id

    try:
        r = requests.post(
            CRYPTO_API + "createInvoice",
            headers={"Crypto-Pay-API-Token": CRYPTO_TOKEN},
            json={
                "asset": "USDT",
                "amount": str(usdt),
                "description": f"Cosmo AI — {amount} запросов",
                "expires_in": 3600,
            },
            timeout=10
        )
        data = r.json()
        if not data.get("ok"):
            logger.error(f"Crypto Pay error: {data}")
            await update.message.reply_text("<b>🏴‍☠️ Ошибка создания счёта. Попробуй позже.</b>", parse_mode="HTML")
            return

        invoice = data["result"]
        invoice_id = str(invoice["invoice_id"])
        pay_url = invoice["bot_invoice_url"]

        cursor.execute("INSERT INTO payments VALUES (?, ?, ?, ?)", (invoice_id, user_id, amount, "pending"))
        conn.commit()

        await update.message.reply_text(
            f"<b>🏴‍☠️ Счёт создан!\n\n"
            f"🏴‍☠️ Сумма: {usdt} USDT\n"
            f"🏴‍☠️ Получишь: {amount} запросов\n\n"
            f"🏴‍☠️ После оплаты запросы будут начислены автоматически.</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏴‍☠️ Оплатить", url=pay_url)]
            ])
        )
    except Exception as e:
        logger.error(f"Payment error: {e}")
        await update.message.reply_text("<b>🏴‍☠️ Ошибка платёжной системы. Попробуй позже.</b>", parse_mode="HTML")

async def buy_100(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await buy_limits(update, context, 100, 1.0)

async def buy_300(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await buy_limits(update, context, 300, 2.5)

async def buy_1000(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await buy_limits(update, context, 1000, 7.0)

# ========= ADMIN КОМАНДЫ =========
async def cmd_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    try:
        username = context.args[0].replace("@", "")
        message = " ".join(context.args[1:])

        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()

        sent = 0
        for (uid,) in users:
            try:
                chat_info = await context.bot.get_chat(uid)
                if chat_info.username and chat_info.username.lower() == username.lower():
                    await context.bot.send_message(uid, f"<b>{message}</b>", parse_mode="HTML")
                    sent += 1
                    break
            except Exception:
                pass

        await update.message.reply_text(f"<b>🏴‍☠️ Отправлено {sent} пользователю(ям)</b>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"<b>🏴‍☠️ Ошибка: {e}</b>", parse_mode="HTML")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    message = " ".join(context.args)
    if not message:
        await update.message.reply_text("<b>🏴‍☠️ Укажи текст: /textTOP <сообщение></b>", parse_mode="HTML")
        return

    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()

    sent = 0
    failed = 0
    for (uid,) in users:
        try:
            await context.bot.send_message(uid, f"<b>{message}</b>", parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"<b>🏴‍☠️ Рассылка завершена\n🏴‍☠️ Отправлено: {sent}\n🏴‍☠️ Не доставлено: {failed}</b>",
        parse_mode="HTML"
    )

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    try:
        username = context.args[0].replace("@", "")
        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()

        for (uid,) in users:
            if uid in ADMINS:
                continue
            try:
                chat_info = await context.bot.get_chat(uid)
                if chat_info.username and chat_info.username.lower() == username.lower():
                    cursor.execute("UPDATE users SET banned=1 WHERE user_id=?", (uid,))
                    conn.commit()
                    await update.message.reply_text(f"<b>🏴‍☠️ Пользователь @{username} заблокирован</b>", parse_mode="HTML")
                    return
            except Exception:
                pass

        await update.message.reply_text(f"<b>🏴‍☠️ Пользователь @{username} не найден</b>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"<b>🏴‍☠️ Ошибка: {e}</b>", parse_mode="HTML")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    try:
        username = context.args[0].replace("@", "")
        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()

        for (uid,) in users:
            try:
                chat_info = await context.bot.get_chat(uid)
                if chat_info.username and chat_info.username.lower() == username.lower():
                    cursor.execute("UPDATE users SET banned=0 WHERE user_id=?", (uid,))
                    conn.commit()
                    await update.message.reply_text(f"<b>🏴‍☠️ Пользователь @{username} разблокирован</b>", parse_mode="HTML")
                    return
            except Exception:
                pass

        await update.message.reply_text(f"<b>🏴‍☠️ Пользователь @{username} не найден</b>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"<b>🏴‍☠️ Ошибка: {e}</b>", parse_mode="HTML")

async def set_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    try:
        amount = int(context.args[0])
        username = context.args[1].replace("@", "")

        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()

        for (uid,) in users:
            try:
                chat_info = await context.bot.get_chat(uid)
                if chat_info.username and chat_info.username.lower() == username.lower():
                    cursor.execute("UPDATE users SET requests = requests + ? WHERE user_id=?", (amount, uid))
                    conn.commit()
                    await update.message.reply_text(
                        f"<b>🏴‍☠️ Пользователю @{username} добавлено {amount} запросов</b>",
                        parse_mode="HTML"
                    )
                    return
            except Exception:
                pass

        await update.message.reply_text(f"<b>🏴‍☠️ Пользователь @{username} не найден</b>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(
            f"<b>🏴‍☠️ Использование: /set количество @username\nОшибка: {e}</b>",
            parse_mode="HTML"
        )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    cursor.execute("SELECT COUNT(*) FROM users")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE banned=1")
    banned = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM payments WHERE status='paid'")
    paid = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(total_used) FROM users")
    all_used = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(*) FROM promo_codes")
    promo_count = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(uses_count) FROM promo_codes")
    promo_uses = cursor.fetchone()[0] or 0

    await update.message.reply_text(
        f"<b>🏴‍☠️ Статистика бота\n\n"
        f"🏴‍☠️ Всего пользователей: {total}\n"
        f"🏴‍☠️ Заблокировано: {banned}\n"
        f"🏴‍☠️ Оплаченных счетов: {paid}\n"
        f"🏴‍☠️ Всего запросов к AI: {all_used}\n"
        f"🏴‍☠️ Промокодов создано: {promo_count}\n"
        f"🏴‍☠️ Промокодов активировано: {promo_uses}</b>",
        parse_mode="HTML"
    )

# ========= ПРОВЕРКА ОПЛАТЫ =========
async def check_payments(context: ContextTypes.DEFAULT_TYPE):
    try:
        r = requests.get(
            CRYPTO_API + "getInvoices",
            headers={"Crypto-Pay-API-Token": CRYPTO_TOKEN},
            timeout=10
        )
        data = r.json()

        if not data.get("ok"):
            return

        for inv in data["result"].get("items", []):
            if inv["status"] == "paid":
                invoice_id = str(inv["invoice_id"])

                cursor.execute("SELECT * FROM payments WHERE invoice_id=?", (invoice_id,))
                p = cursor.fetchone()

                if p and p[3] == "pending":
                    user_id = p[1]
                    amount = p[2]
                    cursor.execute("UPDATE users SET requests = requests + ? WHERE user_id=?", (amount, user_id))
                    cursor.execute("UPDATE payments SET status='paid' WHERE invoice_id=?", (invoice_id,))
                    conn.commit()

                    try:
                        await context.bot.send_message(
                            user_id,
                            f"<b>🏴‍☠️ Оплата получена! Вам начислено {amount} запросов.</b>",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
    except Exception as e:
        logger.error(f"Payment check error: {e}")


# ========= ЗАПУСК =========
async def post_init(application):
    await application.bot.set_my_commands([
        ("start",    "Главное меню"),
        ("promo",    "Активировать промокод"),
        ("buy_100",  "Купить 100 запросов — 1 USDT"),
        ("buy_300",  "Купить 300 запросов — 2.5 USDT"),
        ("buy_1000", "Купить 1000 запросов — 7 USDT"),
    ])

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("promo", promo))
    app.add_handler(CommandHandler("buy_100", buy_100))
    app.add_handler(CommandHandler("buy_300", buy_300))
    app.add_handler(CommandHandler("buy_1000", buy_1000))
    app.add_handler(CommandHandler("text", cmd_text))
    app.add_handler(CommandHandler("textTOP", broadcast))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CommandHandler("set", set_requests))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("addpromo", add_promo))

    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat), group=1)

    app.job_queue.run_repeating(check_payments, interval=15)

    logger.info("🏴‍☠️ Cosmo AI Bot запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
