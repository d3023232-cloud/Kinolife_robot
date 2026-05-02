import asyncio
import sqlite3
import uuid
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    InlineQueryResultArticle, InputTextMessageContent, InlineKeyboardMarkup,
    InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
)
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ==============================================
# ⚙️ НАСТРОЙКИ
# ==============================================
BOT_TOKEN = "8738511395:AAF2BtIXebNnttWN1cpyFM9sD0nfHa4DLqA"
STARS_TOKEN = "ТВОЙ_ТОКЕН_ДЛЯ_STARS"
YUKASSA_TOKEN = "ТВОЙ_ТОКЕН_ДЛЯ_ЮКАССЫ"
ADMIN_IDS = [5975768248, 8319217707, 6403805365]

SPONSOR_CHANNELS = [
    {"id": "@TMD300", "name": "Спонсор 1"},
    {"id": "@TMD033", "name": "Спонсор 2"},
]

# ========== СОСТОЯНИЯ FSM ==========
class AddMovieStates(StatesGroup):
    waiting_for_video = State()
    waiting_for_title = State()
    waiting_for_year = State()
    waiting_for_country = State()
    waiting_for_genres = State()
    waiting_for_keywords = State()
    waiting_for_rating_kp = State()
    waiting_for_rating_imdb = State()
    waiting_for_description = State()

class DeleteMovieStates(StatesGroup):
    waiting_for_id = State()

class AdVideoStates(StatesGroup):
    waiting_for_video = State()

class WatchAdStates(StatesGroup):
    waiting_for_movie = State()

# ========== ИНИЦИАЛИЗАЦИЯ ==========
logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ========== БАЗА ДАННЫХ (ЛОКАЛЬНАЯ) ==========
DATABASE_PATH = "cinema.db"

def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS movies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT, year TEXT, rating_kp REAL, rating_imdb REAL,
        country TEXT, genres TEXT, keywords TEXT,
        description TEXT, video_file_id TEXT, created_at TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
        user_id INTEGER PRIMARY KEY,
        plan_type TEXT,
        start_date TIMESTAMP,
        end_date TIMESTAMP,
        active BOOLEAN
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS payments (
        payment_id TEXT PRIMARY KEY,
        user_id INTEGER, amount INTEGER, currency TEXT,
        status TEXT, created_at TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inviter_id INTEGER,
        invited_id INTEGER,
        created_at TIMESTAMP,
        rewarded_for_purchase BOOLEAN DEFAULT 0,
        rewarded_for_3 BOOLEAN DEFAULT 0,
        UNIQUE(invited_id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS ad_settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    conn.commit()
    conn.close()
    
def extend_subscription(user_id: int, days: int, plan_type: str = "bonus"):
    conn = get_db_connection()
    c = conn.cursor()
    now = datetime.now()
    c.execute("SELECT end_date, active FROM subscriptions WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if row and row[1]:
        end_date = datetime.fromisoformat(row[0])
        new_end = end_date + timedelta(days=days)
        c.execute("""UPDATE subscriptions SET end_date = ?, plan_type = ? WHERE user_id = ?""",
                  (new_end.isoformat(), plan_type, user_id))
    else:
        new_end = now + timedelta(days=days)
        c.execute("""INSERT INTO subscriptions (user_id, plan_type, start_date, end_date, active)
                     VALUES (?, ?, ?, ?, 1)""",
                  (user_id, plan_type, now.isoformat(), new_end.isoformat()))
    conn.commit()
    conn.close()

def get_ad_video() -> str | None:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM ad_settings WHERE key = 'ad_video_file_id'")
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def set_ad_video(file_id: str):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO ad_settings (key, value) VALUES ('ad_video_file_id', ?)", (file_id,))
    conn.commit()
    conn.close()

def get_inviter_id(invited_id: int) -> int | None:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT inviter_id FROM referrals WHERE invited_id = ?", (invited_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def add_referral(inviter_id: int, invited_id: int):
    if inviter_id == invited_id:
        return False
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO referrals (inviter_id, invited_id, created_at) VALUES (?, ?, ?)",
                  (inviter_id, invited_id, datetime.now().isoformat()))
        conn.commit()
        c.execute("SELECT COUNT(*) FROM referrals WHERE inviter_id = ?", (inviter_id,))
        count = c.fetchone()[0]
        if count >= 3:
            c.execute("SELECT rewarded_for_3 FROM referrals WHERE inviter_id = ? AND rewarded_for_3 = 1 LIMIT 1", (inviter_id,))
            if not c.fetchone():
                extend_subscription(inviter_id, 1, "bonus_3invites")
                c.execute("UPDATE referrals SET rewarded_for_3 = 1 WHERE inviter_id = ?", (inviter_id,))
                conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def reward_inviter_on_purchase(user_id: int):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT inviter_id, rewarded_for_purchase FROM referrals WHERE invited_id = ?", (user_id,))
    row = c.fetchone()
    if row and not row[1]:
        inviter_id = row[0]
        extend_subscription(inviter_id, 30, "bonus_referral")
        c.execute("UPDATE referrals SET rewarded_for_purchase = 1 WHERE invited_id = ?", (user_id,))
        conn.commit()
    conn.close()

def count_invites(user_id: int):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM referrals WHERE inviter_id = ?", (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def get_subscription_info(user_id: int):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT plan_type, end_date FROM subscriptions WHERE user_id = ? AND active = 1 AND end_date > ?",
              (user_id, datetime.now().isoformat()))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0], datetime.fromisoformat(row[1])
    return None, None

def search_movies(query: str):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""SELECT id, title, year, rating_kp, rating_imdb, country, genres
                 FROM movies WHERE title LIKE ? OR keywords LIKE ? LIMIT 10""",
              (f"%{query}%", f"%{query}%"))
    rows = c.fetchall()
    conn.close()
    return rows

def get_movie_by_id(movie_id: int):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM movies WHERE id = ?", (movie_id,))
    row = c.fetchone()
    conn.close()
    return row

def get_all_movies():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, title, year FROM movies ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def delete_movie_by_id(movie_id: int):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM movies WHERE id = ?", (movie_id,))
    conn.commit()
    conn.close()

def get_movies_count():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM movies")
    count = c.fetchone()[0]
    conn.close()
    return count

def get_active_subscriptions_count():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM subscriptions WHERE active = 1 AND end_date > ?", (datetime.now().isoformat(),))
    count = c.fetchone()[0]
    conn.close()
    return count

# ========== КЛАВИАТУРЫ ==========
def get_tariffs_keyboard(movie_id: int = None):
    movie_param = f"_{movie_id}" if movie_id else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 месяц — 99₽", callback_data=f"tariff_1m{movie_param}")],
        [InlineKeyboardButton(text="3 месяца — 199₽", callback_data=f"tariff_3m{movie_param}")],
        [InlineKeyboardButton(text="12 месяцев — 499₽", callback_data=f"tariff_12m{movie_param}")]
    ])

def get_payment_methods_keyboard(plan_type: str, movie_id: int = None):
    movie_param = f"_{movie_id}" if movie_id else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data=f"pay_stars_{plan_type}{movie_param}")],
        [InlineKeyboardButton(text="💳 Банковская карта / СБП", callback_data=f"pay_yk_{plan_type}{movie_param}")],
        [InlineKeyboardButton(text="🔙 Назад к тарифам", callback_data="back_to_tariffs")]
    ])

def get_admin_keyboard():
    # ВАЖНО: обычная кнопка с callback, без ссылки на второго бота
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить фильм", callback_data="admin_add_movie")],
        [InlineKeyboardButton(text="📋 Список фильмов", callback_data="admin_list_movies")],
        [InlineKeyboardButton(text="🗑️ Удалить фильм", callback_data="admin_delete_movie")],
        [InlineKeyboardButton(text="📢 Загрузить рекламное видео", callback_data="admin_upload_ad")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")]
    ])

def get_main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Начать поиск", switch_inline_query_current_chat="")],
        [InlineKeyboardButton(text="💎 Купить VIP", callback_data="show_tariffs")],
        [InlineKeyboardButton(text="🤝 Партнёрская программа", callback_data="partner_info")]
    ])

def get_partner_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Начать поиск", switch_inline_query_current_chat="")],
        [InlineKeyboardButton(text="💎 Купить VIP", callback_data="show_tariffs")],
        [InlineKeyboardButton(text="🤝 Партнёрская программа", callback_data="partner_info")]
    ])
    
# ========== ПРОВЕРКА ПОДПИСКИ НА СПОНСОРОВ ==========
async def is_subscribed_to_sponsors(user_id: int) -> bool:
    for ch in SPONSOR_CHANNELS:
        channel_id = ch["id"]
        try:
            member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if member.status not in ("member", "creator", "administrator"):
                return False
        except Exception as e:
            logging.error(f"Ошибка проверки {channel_id}: {e}")
            return False
    return True

async def show_sponsors_check(message: types.Message):
    text = "🔒 *Чтобы пользоваться ботом, подпишитесь на наших спонсоров:*\n\n"
    keyboard = []
    for ch in SPONSOR_CHANNELS:
        text += f"• {ch['name']}: [Подписаться](https://t.me/{ch['id'].lstrip('@')})\n"
        keyboard.append([InlineKeyboardButton(text=f"📢 {ch['name']}", url=f"https://t.me/{ch['id'].lstrip('@')}")])
    keyboard.append([InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sponsors")])
    await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))

# ========== INLINE ПОИСК ==========
@dp.inline_query()
async def inline_search(inline_query: types.InlineQuery):
    query = inline_query.query.strip()
    if len(query) < 2:
        return
    movies = search_movies(query)
    results = []
    for movie in movies:
        movie_id, title, year, kp, imdb, country, genres = movie
        results.append(InlineQueryResultArticle(
            id=str(movie_id),
            title=title,
            description=f"{year} | КП:{kp or '?'} | IMDB:{imdb or '?'} | {country}",
            input_message_content=InputTextMessageContent(
                message_text=f"🎬 *{title}* ({year})\n⭐ КП: {kp or '?'} | IMDB: {imdb or '?'}\n🌍 {country}\n🎭 {genres}",
                parse_mode=ParseMode.MARKDOWN
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎬 Смотреть", callback_data=f"watch_{movie_id}")],
                [InlineKeyboardButton(text="💎 Купить подписку", callback_data=f"buy_subscription_{movie_id}")]
            ])
        ))
    await inline_query.answer(results, cache_time=1)

# ========== ПРОСМОТР ФИЛЬМА С РЕКЛАМОЙ ==========
@dp.callback_query(lambda c: c.data.startswith("watch_"))
async def watch_movie(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    movie_id = int(callback.data.split("_")[1])
    movie = get_movie_by_id(movie_id)
    if not movie:
        await callback.message.answer("❌ Фильм не найден")
        await callback.answer()
        return

    video_file_id = movie[8]
    plan, end_date = get_subscription_info(user_id)
    if plan:
        await bot.send_video(chat_id=user_id, video=video_file_id,
                             caption=f"🎬 *{movie[1]}*\n🍿 Приятного просмотра!", parse_mode=ParseMode.MARKDOWN)
        await callback.answer()
        return

    ad_video = get_ad_video()
    if not ad_video:
        await callback.message.answer(
            "🔒 *У вас нет подписки.*\nОформите доступ к фильмам:",
            reply_markup=get_tariffs_keyboard(movie_id),
            parse_mode=ParseMode.MARKDOWN
        )
        await callback.answer()
        return

    await state.set_state(WatchAdStates.waiting_for_movie)
    await state.update_data(movie_id=movie_id, video_file_id=video_file_id, movie_title=movie[1])

    await bot.send_video(
        chat_id=user_id,
        video=ad_video,
        caption="📢 *Посмотрите рекламу, чтобы начать просмотр фильма*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Я посмотрел рекламу", callback_data="ad_watched")]
        ])
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "ad_watched")
async def ad_watched(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    current_state = await state.get_state()
    if current_state != WatchAdStates.waiting_for_movie.state:
        await callback.answer("❓ Сначала запросите фильм", show_alert=True)
        return
    data = await state.get_data()
    movie_id = data.get("movie_id")
    video_file_id = data.get("video_file_id")
    movie_title = data.get("movie_title")
    if not movie_id:
        await callback.answer("Ошибка, попробуйте снова", show_alert=True)
        await state.clear()
        return
    await bot.send_video(chat_id=user_id, video=video_file_id,
                         caption=f"🎬 *{movie_title}*\n🍿 Приятного просмотра!", parse_mode=ParseMode.MARKDOWN)
    await state.clear()
    await callback.answer("Приятного просмотра!", show_alert=False)

# ========== ПОКУПКА ПОДПИСКИ ==========
@dp.callback_query(lambda c: c.data.startswith("buy_subscription_"))
async def buy_subscription(callback: types.CallbackQuery):
    movie_id = int(callback.data.split("_")[2])
    await callback.message.edit_text(
        "💳 *Магазин подписок*\n\nВыберите тариф на 1, 3 или 12 месяцев:",
        reply_markup=get_tariffs_keyboard(movie_id),
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("tariff_"))
async def handle_tariff_selection(callback: types.CallbackQuery):
    data = callback.data.split("_")
    plan_type = data[1]
    movie_id = int(data[2]) if len(data) > 2 else None
    prices = {"1m": "99₽", "3m": "199₽", "12m": "499₽"}
    await callback.message.edit_text(
        f"💰 *Тариф: {plan_type[:1] if plan_type=='1m' else plan_type[:1] if plan_type=='3m' else '12'} месяц(а)*\n\n"
        f"Сумма: {prices[plan_type]}\n\nВыберите способ оплаты:",
        reply_markup=get_payment_methods_keyboard(plan_type, movie_id),
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "show_tariffs")
async def show_tariffs(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "💳 *Магазин подписок*\n\nВыберите тариф:",
        reply_markup=get_tariffs_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("pay_stars_"))
async def pay_with_stars(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    plan_type = parts[2]
    movie_id = int(parts[3]) if len(parts) > 3 else None
    user_id = callback.from_user.id
    prices = {"1m": 99, "3m": 199, "12m": 499}
    months = {"1m": 1, "3m": 3, "12m": 12}
    payment_id = str(uuid.uuid4())
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?)",
              (payment_id, user_id, prices[plan_type], "STARS", "pending", datetime.now().isoformat()))
    conn.commit()
    conn.close()
    await bot.send_invoice(
        chat_id=user_id,
        title=f"Подписка на {months[plan_type]} месяц(а)",
        description=f"Доступ ко всем фильмам на {months[plan_type]} месяц(а)",
        payload=f"stars_{plan_type}_{user_id}_{payment_id}",
        provider_token=STARS_TOKEN,
        currency="XTR",
        prices=[LabeledPrice(label="Подписка", amount=prices[plan_type])],
        start_parameter="cinema_subscription"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("pay_yk_"))
async def pay_with_yukassa(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    plan_type = parts[2]
    movie_id = int(parts[3]) if len(parts) > 3 else None
    user_id = callback.from_user.id
    prices = {"1m": 99, "3m": 199, "12m": 499}
    months = {"1m": 1, "3m": 3, "12m": 12}
    payment_id = str(uuid.uuid4())
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?)",
              (payment_id, user_id, prices[plan_type], "RUB", "pending", datetime.now().isoformat()))
    conn.commit()
    conn.close()
    await bot.send_invoice(
        chat_id=user_id,
        title=f"Подписка на {months[plan_type]} месяц(а)",
        description=f"Доступ ко всем фильмам на {months[plan_type]} месяц(а)",
        payload=f"yukassa_{plan_type}_{user_id}_{payment_id}",
        provider_token=YUKASSA_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label="Подписка", amount=prices[plan_type] * 100)],
        start_parameter="cinema_subscription"
    )
    await callback.answer()

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout: PreCheckoutQuery):
    await pre_checkout.answer(ok=True)

@dp.message(lambda msg: msg.successful_payment)
async def process_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    parts = payload.split("_")
    payment_type = parts[0]
    plan_type = parts[1]
    user_id = int(parts[2])
    payment_id = parts[3]
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE payments SET status = ? WHERE payment_id = ?", ("completed", payment_id))
    conn.commit()
    conn.close()
    days_map = {"1m": 30, "3m": 90, "12m": 365}
    days = days_map.get(plan_type, 30)
    extend_subscription(user_id, days, plan_type)
    reward_inviter_on_purchase(user_id)
    method = "Telegram Stars" if payment_type == "stars" else "ЮKassa"
    await message.answer(
        f"✅ *Оплата через {method} прошла успешно!*\n\n"
        f"Подписка на {days} дней активирована.\n\n"
        f"🍿 *Приятного просмотра!*\n\n"
        f"👇 Нажмите кнопку, чтобы начать поиск:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Найти фильм", switch_inline_query_current_chat="")],
            [InlineKeyboardButton(text="📊 Проверить статус", callback_data="check_subscription")]
        ])
    )

# ========== ПАРТНЁРСКАЯ ПРОГРАММА ==========
@dp.callback_query(lambda c: c.data == "partner_info")
async def partner_info(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    bot_username = (await bot.get_me()).username
    invite_link = f"https://t.me/{bot_username}?start=ref{user_id}"
    invites_count = count_invites(user_id)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT rewarded_for_3 FROM referrals WHERE inviter_id = ? AND rewarded_for_3 = 1 LIMIT 1", (user_id,))
    bonus_3 = c.fetchone() is not None
    conn.close()
    bonus_text = "✅ Бонус за 3 приглашения получен" if bonus_3 else "⏳ Бонус ещё не получен"
    text = (
        f"🎁 *Партнёрская программа*\n\n"
        f"Пригласи 3 друзей – получи 1 день VIP бесплатно!\n"
        f"Если хотя бы один из приглашённых купит подписку – ты получишь месяц VIP в подарок.\n\n"
        f"Твоя реферальная ссылка:\n`{invite_link}`\n\n"
        f"Количество приглашённых: {invites_count}/3\n"
        f"Статус бонуса: {bonus_text}"
    )
    await callback.message.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_partner_keyboard())
    await callback.answer()

@dp.message(Command("referral"))
async def cmd_referral(message: types.Message):
    user_id = message.from_user.id
    bot_username = (await bot.get_me()).username
    invite_link = f"https://t.me/{bot_username}?start=ref{user_id}"
    invites_count = count_invites(user_id)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT rewarded_for_3 FROM referrals WHERE inviter_id = ? AND rewarded_for_3 = 1 LIMIT 1", (user_id,))
    bonus_3 = c.fetchone() is not None
    conn.close()
    bonus_text = "✅ Бонус за 3 приглашения получен" if bonus_3 else "⏳ Бонус ещё не получен"
    text = (
        f"🎁 *Партнёрская программа*\n\n"
        f"Пригласи 3 друзей – получи 1 день VIP бесплатно!\n"
        f"Если хотя бы один из приглашённых купит подписку – ты получишь месяц VIP в подарок.\n\n"
        f"Твоя реферальная ссылка:\n`{invite_link}`\n\n"
        f"Количество приглашённых: {invites_count}/3\n"
        f"Статус бонуса: {bonus_text}"
    )
    await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_partner_keyboard())

# ========== ПРОВЕРКА ПОДПИСКИ НА СПОНСОРОВ (callback) ==========
@dp.callback_query(lambda c: c.data == "check_sponsors")
async def check_sponsors_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_subscribed_to_sponsors(user_id):
        await show_main_menu(callback.message, user_id)
        await callback.message.delete()
    else:
        await callback.answer("❌ Вы не подписаны на всех спонсоров. Подпишитесь и нажмите снова.", show_alert=True)

# ========== ГЛАВНОЕ МЕНЮ ==========
async def show_main_menu(message: types.Message, user_id: int):
    name = message.from_user.first_name
    text = (
        f"Привет, {name}! 🎬\n"
        "Этот бот — твой личный кинотеатр.\n"
        "Чтобы найти фильм, сериал или кино, используй кнопку ниже.\n"
        "Нажми «🔍 Начать поиск», введи название и выбирай из результатов."
    )
    await message.answer(text, reply_markup=get_main_menu_keyboard())

# ========== СТАТУС ПОДПИСКИ ==========
@dp.callback_query(lambda c: c.data == "check_subscription")
async def check_subscription_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    plan, end_date = get_subscription_info(user_id)
    if plan:
        days_left = (end_date - datetime.now()).days
        await callback.message.answer(
            f"✅ *Подписка активна*\n\n📅 Тариф: {plan}\n⏰ Осталось дней: {days_left}\n📆 Действует до: {end_date.strftime('%d.%m.%Y')}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Продлить подписку", callback_data="show_tariffs")]
            ])
        )
    else:
        await callback.message.answer(
            "❌ *У вас нет активной подписки*",
            reply_markup=get_tariffs_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )
    await callback.answer()

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    user_id = message.from_user.id
    plan, end_date = get_subscription_info(user_id)
    if plan:
        days_left = (end_date - datetime.now()).days
        await message.answer(
            f"✅ *Подписка активна*\n\n📅 Тариф: {plan}\n⏰ Осталось дней: {days_left}\n📆 Действует до: {end_date.strftime('%d.%m.%Y')}",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await message.answer(
            "❌ *У вас нет активной подписки*",
            reply_markup=get_tariffs_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )

# ========== АДМИН-ПАНЕЛЬ ==========
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа к админ-панели")
        return
    await message.answer(
        "🔐 *Админ-панель*\n\nВыберите действие:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_admin_keyboard()
    )

# === ИСПРАВЛЕННЫЙ ОБРАБОТЧИК admin_add_movie (размещён здесь, до остальных) ===
@dp.callback_query(lambda c: c.data == "admin_add_movie")
async def admin_add_movie(callback: types.CallbackQuery, state: FSMContext):
    print("=== admin_add_movie CALLBACK RECEIVED ===")  # отладочный вывод
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    # Отвечаем на колбэк, чтобы кнопка перестала "висеть"
    await callback.answer()
    # Сбрасываем предыдущее состояние
    await state.clear()
    # Редактируем сообщение – заменяем админ-панель на первый шаг добавления
    await callback.message.edit_text(
        "🎬 *Добавление нового фильма*\n\n"
        "📹 *Шаг 1/9:* Отправьте видео файлом.\n\n"
        "Видео будет сохранено в Telegram, я получу его file_id.",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(AddMovieStates.waiting_for_video)
    print(f"Состояние установлено: {await state.get_state()}")

# ========== ОСТАЛЬНЫЕ ОБРАБОТЧИКИ АДМИНКИ (без изменений) ==========
@dp.callback_query(lambda c: c.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    movies_count = get_movies_count()
    subs_count = get_active_subscriptions_count()
    await callback.message.edit_text(
        f"📊 *Статистика*\n\n🎬 Фильмов в базе: {movies_count}\n👥 Активных подписок: {subs_count}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")]
        ])
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_list_movies")
async def admin_list_movies(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    movies = get_all_movies()
    if not movies:
        await callback.message.edit_text(
            "📭 В базе пока нет фильмов",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")]
            ])
        )
        await callback.answer()
        return
    text = "📋 *Список фильмов:*\n\n"
    for movie in movies[:20]:
        text += f"🎬 ID: `{movie[0]}` | {movie[1]} ({movie[2]})\n"
    if len(movies) > 20:
        text += f"\n...и ещё {len(movies) - 20} фильмов"
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Удалить фильм по ID", callback_data="admin_delete_movie")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")]
        ])
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_delete_movie")
async def admin_delete_movie_prompt(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "🗑️ *Удаление фильма*\n\nВведите ID фильма, который нужно удалить.\n"
        "Список фильмов с ID можно посмотреть в «📋 Список фильмов»",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_admin")]
        ])
    )
    await state.set_state(DeleteMovieStates.waiting_for_id)
    await callback.answer()

@dp.message(DeleteMovieStates.waiting_for_id)
async def process_delete_movie(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    if not message.text.isdigit():
        await message.answer("❌ Введите числовой ID фильма")
        return
    movie_id = int(message.text)
    movie = get_movie_by_id(movie_id)
    if not movie:
        await message.answer("❌ Фильм с таким ID не найден")
        await state.clear()
        return
    delete_movie_by_id(movie_id)
    await message.answer(f"✅ Фильм *{movie[1]}* (ID: {movie_id}) удалён", parse_mode=ParseMode.MARKDOWN)
    await state.clear()

# ========== ЗАГРУЗКА РЕКЛАМНОГО ВИДЕО (АДМИН) ==========
@dp.callback_query(lambda c: c.data == "admin_upload_ad")
async def admin_upload_ad(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "📢 *Загрузка рекламного видео*\n\nОтправьте видео файлом. Это видео будет показываться всем пользователям без подписки перед просмотром.",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(AdVideoStates.waiting_for_video)
    await callback.answer()

@dp.message(AdVideoStates.waiting_for_video)
async def process_ad_video(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    if not message.video:
        await message.answer("❌ Отправьте видео файлом")
        return
    file_id = message.video.file_id
    set_ad_video(file_id)
    await message.answer(f"✅ Рекламное видео сохранено!\n`{file_id}`", parse_mode=ParseMode.MARKDOWN)
    await state.clear()

@dp.callback_query(lambda c: c.data == "back_to_admin")
async def back_to_admin(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "🔐 *Админ-панель*\n\nВыберите действие:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_admin_keyboard()
    )
    await callback.answer()

# ========== ОБРАБОТЧИК /start ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    args = command.args
    if args and args.startswith("ref"):
        try:
            inviter_id = int(args[3:])
            if inviter_id != user_id:
                add_referral(inviter_id, user_id)
        except:
            pass
    if await is_subscribed_to_sponsors(user_id):
        await show_main_menu(message, user_id)
    else:
        await show_sponsors_check(message)

# ========== ОСТАЛЬНЫЕ ОБРАБОТЧИКИ ДЛЯ ДОБАВЛЕНИЯ ФИЛЬМА (шаги 1-9) ==========
@dp.message(AddMovieStates.waiting_for_video)
async def add_movie_video(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Нет доступа")
        await state.clear()
        return
    if not message.video:
        await message.answer("❌ Пожалуйста, отправьте видео файлом")
        return
    file_id = message.video.file_id
    await state.update_data(video_file_id=file_id)
    await message.answer(
        f"✅ Видео получено!\n`{file_id}`\n\n📝 *Шаг 2/9:* Введите название фильма:",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(AddMovieStates.waiting_for_title)

@dp.message(AddMovieStates.waiting_for_title)
async def add_movie_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await message.answer("📅 *Шаг 3/9:* Введите год выпуска (например: 2023):", parse_mode=ParseMode.MARKDOWN)
    await state.set_state(AddMovieStates.waiting_for_year)

@dp.message(AddMovieStates.waiting_for_year)
async def add_movie_year(message: types.Message, state: FSMContext):
    year = message.text.strip()
    if not year.isdigit() or len(year) != 4:
        await message.answer("❌ Введите корректный год (4 цифры, например: 2023)")
        return
    await state.update_data(year=year)
    await message.answer("🌍 *Шаг 4/9:* Введите страну производства (например: США, Россия):", parse_mode=ParseMode.MARKDOWN)
    await state.set_state(AddMovieStates.waiting_for_country)

@dp.message(AddMovieStates.waiting_for_country)
async def add_movie_country(message: types.Message, state: FSMContext):
    await state.update_data(country=message.text.strip())
    await message.answer("🎭 *Шаг 5/9:* Введите жанры через запятую (например: драма, комедия, боевик):", parse_mode=ParseMode.MARKDOWN)
    await state.set_state(AddMovieStates.waiting_for_genres)

@dp.message(AddMovieStates.waiting_for_genres)
async def add_movie_genres(message: types.Message, state: FSMContext):
    await state.update_data(genres=message.text.strip())
    await message.answer(
        "🔑 *Шаг 6/9:* Введите *ключевые слова* через запятую\n\n"
        "Пример: `форсаж, вин дизель, гонки, тюнинг, машины`\n\n"
        "⚠️ По этим словам пользователи смогут найти фильм!",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(AddMovieStates.waiting_for_keywords)

@dp.message(AddMovieStates.waiting_for_keywords)
async def add_movie_keywords(message: types.Message, state: FSMContext):
    keywords = message.text.strip().lower()
    await state.update_data(keywords=keywords)
    await message.answer("⭐ *Шаг 7/9:* Введите рейтинг Кинопоиска (например: 7.5 или 0 если нет):", parse_mode=ParseMode.MARKDOWN)
    await state.set_state(AddMovieStates.waiting_for_rating_kp)

@dp.message(AddMovieStates.waiting_for_rating_kp)
async def add_movie_rating_kp(message: types.Message, state: FSMContext):
    try:
        val = float(message.text.strip().replace(',', '.'))
        await state.update_data(rating_kp=val if val > 0 else None)
    except:
        await state.update_data(rating_kp=None)
    await message.answer("🎬 *Шаг 8/9:* Введите рейтинг IMDb (например: 8.2 или 0 если нет):", parse_mode=ParseMode.MARKDOWN)
    await state.set_state(AddMovieStates.waiting_for_rating_imdb)

@dp.message(AddMovieStates.waiting_for_rating_imdb)
async def add_movie_rating_imdb(message: types.Message, state: FSMContext):
    try:
        val = float(message.text.strip().replace(',', '.'))
        await state.update_data(rating_imdb=val if val > 0 else None)
    except:
        await state.update_data(rating_imdb=None)
    await message.answer(
        "📝 *Шаг 9/9:* Введите описание фильма.\n\n"
        "Напишите описание фильма: краткий сюжет, главные герои, интересные особенности. "
        "Это описание увидят пользователи при поиске.",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(AddMovieStates.waiting_for_description)

@dp.message(AddMovieStates.waiting_for_description)
async def add_movie_description(message: types.Message, state: FSMContext):
    data = await state.update_data(description=message.text.strip())
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO movies (title, year, rating_kp, rating_imdb, country, genres, keywords, description, video_file_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (data['title'], data['year'], data['rating_kp'], data['rating_imdb'],
          data['country'], data['genres'], data['keywords'], data['description'],
          data['video_file_id'], datetime.now().isoformat()))
    conn.commit()
    movie_id = c.lastrowid
    conn.close()
    await message.answer(
        f"✅ *Фильм успешно добавлен!*\n\n🎬 Название: {data['title']}\n🆔 ID: {movie_id}\n"
        f"🔑 Ключевые слова: {data['keywords']}\n\nТеперь пользователи смогут найти его через поиск!",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.clear()

# ========== ЗАПУСК ==========
async def main():
    init_db()
    bot_info = await bot.get_me()
    print(f"🤖 Бот запущен: @{bot_info.username}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
