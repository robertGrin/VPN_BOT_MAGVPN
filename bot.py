import asyncio
import logging
import uuid
import os
import random
from datetime import datetime, timedelta
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, BaseFilter
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, LabeledPrice, PreCheckoutQuery, BotCommand
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, func

from yookassa import Configuration, Payment

from models import Base, User, Device
from vpn_service import VPNService

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "..."
ADMIN_IDS = [..., ..., ...]
SUPPORT_USERNAME = "..." 

Configuration.account_id = '...'
Configuration.secret_key = '...'

vpn_service = VPNService(
    panel_url="...", 
    public_ip="...",          
    username="...",                 
    password="...",  
    inbound_id=1,                  
    sni="...",               
    pbk="...", 
    sid="..."    
)

PRICES = {
    30: {"rub": 99, "stars": 99, "text": "1 месяц - 99 ₽ / 99 ⭐️"},
    90: {"rub": 252, "stars": 250, "text": "3 месяца - 252 ₽ / 250 ⭐ (-15%)"},
    180: {"rub": 475, "stars": 475, "text": "6 месяцев - 475 ₽ / 475 ⭐ (-20%)"},
    365: {"rub": 831, "stars": 830, "text": "12 месяцев - 831 ₽ / 830 ⭐ (-30%)"}
}
# =============================================

if not os.path.exists("db"):
    os.makedirs("db")

engine = create_async_engine("sqlite+aiosqlite:///db/vpn_bot.db", echo=False)
async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
storage = RedisStorage.from_url('redis://redis:6379/0')
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=storage)

class AdminStates(StatesGroup):
    wait_for_user_id = State()
    wait_for_days = State()
    wait_for_broadcast = State()
    wait_for_replace_id = State()
    wait_for_profile_id = State() 

class IsAdmin(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        return message.from_user.id in ADMIN_IDS

# --- КЛАВИАТУРЫ ---
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔑 Мои ключи"), KeyboardButton(text="➕ Купить новый ключ")],
        [KeyboardButton(text="👤 Мой профиль"), KeyboardButton(text="💰 Прайс-лист")],
        [KeyboardButton(text="ℹ️ Как подключиться"), KeyboardButton(text="🆘 Поддержка")]
    ],
    resize_keyboard=True
)

admin_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="🎁 Выдать подписку")],
        [KeyboardButton(text="📢 Рассылка"), KeyboardButton(text="🔄 Заменить ключ")],
        [KeyboardButton(text="🔍 Найти пользователя"), KeyboardButton(text="⬅️ Выйти из админки")] 
    ],
    resize_keyboard=True
)

@dp.update.outer_middleware()
async def db_session_middleware(handler, event, data):
    async with async_session_maker() as session:
        data['session'] = session
        return await handler(event, data)

async def set_bot_commands():
    commands = [
        BotCommand(command="start", description="🚀 Главное меню"),
        BotCommand(command="keys", description="🔑 Мои ключи"),
        BotCommand(command="buy", description="➕ Купить новый ключ"),
        BotCommand(command="profile", description="👤 Мой профиль"),
        BotCommand(command="price", description="💰 Прайс-лист"),
        BotCommand(command="help", description="ℹ️ Как подключиться"),
        BotCommand(command="support", description="🆘 Поддержка")
    ]
    await bot.set_my_commands(commands)

# --- АДМИН ПАНЕЛЬ ---
@dp.message(Command("Admin_auth_logs"), IsAdmin())
async def cmd_admin(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🛠 <b>Панель администратора</b>", reply_markup=admin_kb)

@dp.message(F.text == "⬅️ Выйти из админки", IsAdmin())
async def exit_admin(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Возврат в меню пользователя.", reply_markup=main_kb)

@dp.message(F.text == "📊 Статистика", IsAdmin())
async def admin_stats(message: types.Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    now = datetime.now()
    total_users = (await session.execute(select(func.count(User.id)))).scalar()
    total_devices = (await session.execute(select(func.count(Device.id)))).scalar()
    
    active_paid = (await session.execute(select(func.count(Device.id)).where(Device.subscription_end > now, Device.is_paid == True))).scalar()
    active_free = (await session.execute(select(func.count(Device.id)).where(Device.subscription_end > now, Device.is_paid == False))).scalar()

    await message.answer(
        f"<b>📈 Статистика:</b>\n\n"
        f"Всего пользователей: <b>{total_users}</b>\n"
        f"Всего ключей в базе: <b>{total_devices}</b>\n\n"
        f"Активных ключей (платные): <b>{active_paid}</b>\n"
        f"Активных ключей (пробные): <b>{active_free}</b>\n"
        f"Итого рабочих: <b>{active_paid + active_free}</b>"
    )

@dp.message(F.text == "🎁 Выдать подписку", IsAdmin())
async def admin_give_sub_start(message: types.Message, state: FSMContext):
    await message.answer("Введите Telegram ID пользователя:")
    await state.set_state(AdminStates.wait_for_user_id)

@dp.message(AdminStates.wait_for_user_id, IsAdmin())
async def admin_give_sub_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("❌ ID должен состоять из цифр.")
    await state.update_data(target_id=int(message.text))
    await message.answer("На сколько дней выдать ПОДАРОЧНЫЙ ключ?")
    await state.set_state(AdminStates.wait_for_days)

@dp.message(AdminStates.wait_for_days, IsAdmin())
async def admin_give_sub_days(message: types.Message, state: FSMContext, session: AsyncSession):
    if not message.text.isdigit(): return await message.answer("❌ Введите число.")
    data = await state.get_data()
    days = int(message.text)
    target_id = data['target_id']
    
    user = (await session.execute(select(User).where(User.telegram_id == target_id))).scalar_one_or_none()
    if not user:
        session.add(User(telegram_id=target_id))
        await session.flush()
        
    count = (await session.execute(select(func.count(Device.id)).where(Device.user_id == target_id))).scalar()
    new_dev = Device(
        user_id=target_id,
        name=f"🎁 Подарок #{count + 1}",
        subscription_end=datetime.now() + timedelta(days=days),
        is_paid=True
    )
    session.add(new_dev)
    await session.commit()
    
    await message.answer(f"✅ Успешно! Пользователю выдан подарочный ключ на {days} дней.", reply_markup=admin_kb)
    try:
        await bot.send_message(target_id, f"🎁 Администратор выдал вам новый ключ на {days} дней!\nЗайдите в «🔑 Мои ключи».")
    except: pass
    await state.clear()

@dp.message(F.text == "📢 Рассылка", IsAdmin())
async def admin_broadcast_start(message: types.Message, state: FSMContext):
    await message.answer("Отправьте сообщение, которое нужно разослать:")
    await state.set_state(AdminStates.wait_for_broadcast)

@dp.message(AdminStates.wait_for_broadcast, IsAdmin())
async def admin_broadcast_send(message: types.Message, state: FSMContext, session: AsyncSession):
    users = (await session.execute(select(User.telegram_id))).scalars().all()
    count = 0
    msg = await message.answer("⏳ Рассылка запущена...")
    for uid in users:
        try:
            await message.copy_to(uid)
            count += 1
            await asyncio.sleep(0.05)
        except: pass
    await msg.edit_text(f"✅ Рассылка завершена!\nДоставлено: <b>{count} из {len(users)}</b>")
    await state.clear()

@dp.message(F.text == "🔄 Заменить ключ", IsAdmin())
async def admin_replace_start(message: types.Message, state: FSMContext):
    await message.answer("Введите <b>ID Устройства</b> (админ может запросить его у пользователя из его раздела 'Мои ключи'):")
    await state.set_state(AdminStates.wait_for_replace_id)

@dp.message(AdminStates.wait_for_replace_id, IsAdmin())
async def admin_replace_exec(message: types.Message, state: FSMContext, session: AsyncSession):
    if not message.text.isdigit(): return await message.answer("❌ ID должен состоять из цифр.")
    
    dev_id = int(message.text)
    dev = (await session.execute(select(Device).where(Device.id == dev_id))).scalar_one_or_none()
    
    if not dev: return await message.answer("❌ Устройство с таким ID не найдено.")
        
    msg = await message.answer("🔄 Генерирую новый ключ на сервере...")
    try:
        new_key = await vpn_service.get_happ_key_for_user(dev.user_id + random.randint(100000, 999999), dev.subscription_end)
        dev.vpn_key = new_key
        await session.commit()
        await msg.edit_text(f"✅ Ключ устройства #{dev_id} заменен.")
        try:
            await bot.send_message(dev.user_id, f"🔄 Администратор обновил ваш ключ <b>{dev.name}</b>!\n\nНовый ключ:\n<code>{new_key}</code>")
        except: pass
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка генерации: {str(e)}")
    await state.clear()

@dp.message(F.text == "🔍 Найти пользователя", IsAdmin())
async def admin_find_user_start(message: types.Message, state: FSMContext):
    await message.answer("Введите Telegram ID пользователя:")
    await state.set_state(AdminStates.wait_for_profile_id)

@dp.message(AdminStates.wait_for_profile_id, IsAdmin())
async def admin_find_user_exec(message: types.Message, state: FSMContext, session: AsyncSession):
    if not message.text.isdigit(): 
        return await message.answer("❌ ID должен состоять из цифр.")
    
    target_id = int(message.text)
    user = (await session.execute(select(User).where(User.telegram_id == target_id))).scalar_one_or_none()
    
    if not user:
        await message.answer("❌ Пользователь с таким ID не найден в базе.")
        await state.clear()
        return

    devices = (await session.execute(select(Device).where(Device.user_id == target_id))).scalars().all()
    
    text = f"👤 <b>Профиль пользователя {target_id}</b>\n\n"
    text += f"<b>Всего ключей:</b> {len(devices)}\n"
    
    if devices:
        text += "\n<b>Список ключей:</b>\n"
        for dev in devices:
            is_active = dev.subscription_end and dev.subscription_end > datetime.now()
            status = "✅" if is_active else "❌"
            end_date = dev.subscription_end.strftime('%d.%m.%Y %H:%M') if dev.subscription_end else "Неизвестно"
            
            text += f"\n{status} <b>{dev.name}</b> (ID устройства: <code>{dev.id}</code>)\n"
            text += f"   └ Окончание: {end_date}\n"
            text += f"   └ Оплачен: {'Да' if dev.is_paid else 'Нет (Пробный/Подарок)'}\n"
    else:
        text += "\n<i>У пользователя нет ключей.</i>"
        
    await message.answer(text)
    await state.clear()


# --- ОБРАБОТЧИКИ ПОЛЬЗОВАТЕЛЯ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, session: AsyncSession):
    user = (await session.execute(select(User).where(User.telegram_id == message.from_user.id))).scalar_one_or_none()
    
    if not user:
        user = User(telegram_id=message.from_user.id)
        session.add(user)
        trial_dev = Device(
            user_id=message.from_user.id,
            name="🎁 Пробный ключ",
            subscription_end=datetime.now() + timedelta(days=7),
            is_paid=False
        )
        session.add(trial_dev)
        await session.commit()
        
        await message.answer(
            f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
            f"Добро пожаловать в <b>MagVPN</b> 🧙‍♂️\n\n"
            f"🎁 Мы выдали тебе первый ключ на <b>7 дней тест-драйва</b>!\n\n"
            f"Жми <b>«🔑 Мои ключи»</b>, чтобы забрать его.\n\n"
            f"🚨 <b>Обязательно подпишись на наш канал - <a href='https://t.me/MagVPNhere'>MagVPN</a></b>", 
            reply_markup=main_kb,
            disable_web_page_preview=True)
    else:
        await message.answer("Вы в главном меню 👇", reply_markup=main_kb)

@dp.message(Command("profile"))
@dp.message(F.text == "👤 Мой профиль")
async def process_profile(message: types.Message, session: AsyncSession):
    devices = (await session.execute(select(Device).where(Device.user_id == message.from_user.id))).scalars().all()
    active_count = sum(1 for dev in devices if dev.subscription_end and dev.subscription_end > datetime.now())
    
    await message.answer(
        f"👤 <b>Ваш профиль</b>\n\n"
        f"<b>ID:</b> <code>{message.from_user.id}</code>\n"
        f"<b>Всего ключей (устройств):</b> {len(devices)}\n"
        f"<b>Активных подписок:</b> {active_count}\n\n"
        f"<i>Управляйте своими ключами в разделе «🔑 Мои ключи».</i>"
    )

@dp.message(Command("support"))
@dp.message(F.text == "🆘 Поддержка")
async def process_support(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Написать агенту поддержки", url=f"https://t.me/{SUPPORT_USERNAME}")]
    ])
    await message.answer("👨‍💻 <b>Служба поддержки MagVPN</b>\n\nЕсли у вас возникли вопросы, мы готовы помочь!", reply_markup=kb)

@dp.message(Command("help"))
@dp.message(F.text == "ℹ️ Как подключиться")
async def process_help(message: types.Message):
    text = (
        "<b>Инструкция по подключению:</b>\n\n"
        
        "<b>🍏 Для iOS (iPhone/iPad):</b>\n"
        "1. Скачайте приложение <b>V2Ray Tun</b> из App Store.\n"
        "2. Скопируйте ваш ключ (в разделе «Мои ключи»).\n"
        "3. Откройте приложение, нажмите «+» и выберите «Импорт из буфера».\n"
        "<a href='https://t.me/MagVPNhere/4'>▶️ ВИДЕО ИНСТРУКЦИЯ</a>\n\n"
        
        "<b>🤖 Для Android:</b>\n"
        "1. Скачайте приложение <b>V2Ray Tun</b> из Google Play.\n"
        "2. Скопируйте ваш ключ.\n"
        "3. Откройте приложение, нажмите «+» и выберите «Импорт из буфера».\n"
        "<a href='https://t.me/MagVPNhere/11'>▶️ ВИДЕО ИНСТРУКЦИЯ</a>\n\n"
        
        "<b>💻 Для macOS:</b>\n"
        "1. Скачайте приложение <b>V2Ray Tun</b> из App Store.\n"
        "2. Скопируйте ваш ключ.\n"
        "3. Откройте приложение, нажмите «+» и выберите «Импорт из буфера».\n"
        "<a href='https://t.me/MagVPNhere/12'>▶️ ВИДЕО ИНСТРУКЦИЯ</a>\n\n"
        
        "<b>🖥 Для ПК (Windows):</b>\n"
        "1. Скачайте программу <b>v2RayN</b> и запустите её.\n"
        "2. Скопируйте ваш ключ.\n"
        "3. В приложении нажмите <code>Ctrl+V</code> (или выберите «Импорт из буфера»).\n"
        "<a href='https://t.me/MagVPNhere/13'>▶️ ВИДЕО ИНСТРУКЦИЯ</a>\n\n"
        
        "🚨 Если у вас возникли сложности с подключением VPN, смело пишите в поддержку 👉 @MagVPN_help\n"
        "Постараемся оперативно Вам помочь!"
    )
    await message.answer(text, disable_web_page_preview=True)

# --- УПРАВЛЕНИЕ КЛЮЧАМИ ---
@dp.message(Command("keys"))
@dp.message(F.text == "🔑 Мои ключи")
async def process_my_keys(message: types.Message, session: AsyncSession):
    devices = (await session.execute(select(Device).where(Device.user_id == message.from_user.id))).scalars().all()
    
    if not devices:
        return await message.answer(
            "У вас пока нет ключей ❌\nЖми «➕ Купить новый ключ».\n\n"
            "🚨 <b>Обязательно подпишись на наш канал - <a href='https://t.me/MagVPNhere'>MagVPN</a></b>",
            disable_web_page_preview=True
        )
        
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for dev in devices:
        status = "✅" if dev.subscription_end and dev.subscription_end > datetime.now() else "❌"
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"{status} {dev.name} (до {dev.subscription_end.strftime('%d.%m')})", callback_data=f"dev_{dev.id}")])
        
    await message.answer(
        "<b>Ваши VPN ключи (устройства):</b>\nНажмите на нужный для получения ссылки 👇\n\n"
        "🚨 <b>Обязательно подпишись на наш канал - <a href='https://t.me/MagVPNhere'>MagVPN</a></b>", 
        reply_markup=kb,
        disable_web_page_preview=True
    )

@dp.callback_query(F.data == "back_keys")
async def back_to_keys(call: CallbackQuery, session: AsyncSession):
    devices = (await session.execute(select(Device).where(Device.user_id == call.from_user.id))).scalars().all()
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for dev in devices:
        status = "✅" if dev.subscription_end and dev.subscription_end > datetime.now() else "❌"
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"{status} {dev.name} (до {dev.subscription_end.strftime('%d.%m')})", callback_data=f"dev_{dev.id}")])
    await call.message.edit_text(
        "<b>Ваши VPN ключи (устройства):</b>\nНажмите на нужный для получения ссылки 👇\n\n"
        "🚨 <b>Обязательно подпишись на наш канал - <a href='https://t.me/MagVPNhere'>MagVPN</a></b>", 
        reply_markup=kb,
        disable_web_page_preview=True
    )

@dp.callback_query(F.data.startswith("dev_"))
async def show_device_info(call: CallbackQuery, session: AsyncSession):
    dev_id = int(call.data.split("_")[1])
    dev = (await session.execute(select(Device).where(Device.id == dev_id, Device.user_id == call.from_user.id))).scalar_one_or_none()
    
    if not dev: return await call.answer("Ключ не найден!", show_alert=True)
        
    is_active = dev.subscription_end and dev.subscription_end > datetime.now()
    
    text = f"📱 <b>{dev.name}</b> (ID устройства: {dev.id})\n\n"
    text += f"<b>Статус:</b> {'Активен ✅' if is_active else 'Закончился ❌'}\n"
    text += f"<b>Действует до:</b> {dev.subscription_end.strftime('%d.%m.%Y %H:%M')}\n\n"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    
    if is_active:
        if not dev.vpn_key:
            kb.inline_keyboard.append([InlineKeyboardButton(text="🔄 Сгенерировать ключ", callback_data=f"gen_{dev.id}")])
            text += "<i>Нажмите кнопку ниже, чтобы получить ссылку.</i>"
        else:
            text += f"<b>Ваш ключ:</b>\n<code>{dev.vpn_key}</code>\n\n<i>Один ключ можно подключить только на одно устройство одновременно!</i>"
    else:
        text += "<i>Подписка закончилась. Продлите её, чтобы получить доступ.</i>"
        
    kb.inline_keyboard.append([InlineKeyboardButton(text="💳 Продлить", callback_data=f"ext_{dev.id}")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_keys")])
    
    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("gen_"))
async def generate_key(call: CallbackQuery, session: AsyncSession):
    dev_id = int(call.data.split("_")[1])
    dev = (await session.execute(select(Device).where(Device.id == dev_id, Device.user_id == call.from_user.id))).scalar_one_or_none()
    
    if not dev or not (dev.subscription_end and dev.subscription_end > datetime.now()):
        return await call.answer("Сначала продлите подписку!", show_alert=True)
        
    await call.message.edit_text("🔄 Связываюсь с сервером... Подождите.")
    try:
        unique_id = call.from_user.id + random.randint(100000, 999999)
        new_key = await vpn_service.get_happ_key_for_user(unique_id, dev.subscription_end)
        dev.vpn_key = new_key
        await session.commit()
        await call.answer("✅ Ключ успешно сгенерирован!", show_alert=True)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Продлить", callback_data=f"ext_{dev.id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_keys")]
        ])
        text = f"📱 <b>{dev.name}</b> (ID устройства: {dev.id})\n\n<b>Статус:</b> Активен ✅\n<b>Действует до:</b> {dev.subscription_end.strftime('%d.%m.%Y %H:%M')}\n\n<b>Ваш ключ:</b>\n<code>{dev.vpn_key}</code>"
        await call.message.edit_text(text, reply_markup=kb)
    except Exception as e:
        await call.message.edit_text(f"❌ Ошибка сервера. Напишите в поддержку.\nКод: {e}")

# --- ПОКУПКА И ПРОДЛЕНИЕ ---
@dp.message(Command("buy"))
@dp.message(Command("price"))
@dp.message(F.text == "➕ Купить новый ключ")
@dp.message(F.text == "💰 Прайс-лист")
async def process_buy_new(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=PRICES[30]["text"], callback_data="buy_new_0_30")],
        [InlineKeyboardButton(text=PRICES[90]["text"], callback_data="buy_new_0_90")],
        [InlineKeyboardButton(text=PRICES[180]["text"], callback_data="buy_new_0_180")],
        [InlineKeyboardButton(text=PRICES[365]["text"], callback_data="buy_new_0_365")]
    ])
    await message.answer(
        "<b>💰 Прайс-лист / Покупка НОВОГО ключа</b>\n\n"
        "Вы покупаете совершенно новый, независимый ключ для второго телефона, ПК или друга.\n\n"
        "Выберите период:\n\n"
        "🚨 <b>Обязательно подпишись на наш канал - <a href='https://t.me/MagVPNhere'>MagVPN</a></b>", 
        reply_markup=kb,
        disable_web_page_preview=True
    )

@dp.callback_query(F.data.startswith("ext_"))
async def process_extend(call: CallbackQuery):
    dev_id = int(call.data.split("_")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=PRICES[30]["text"], callback_data=f"buy_ext_{dev_id}_30")],
        [InlineKeyboardButton(text=PRICES[90]["text"], callback_data=f"buy_ext_{dev_id}_90")],
        [InlineKeyboardButton(text=PRICES[180]["text"], callback_data=f"buy_ext_{dev_id}_180")],
        [InlineKeyboardButton(text=PRICES[365]["text"], callback_data=f"buy_ext_{dev_id}_365")]
    ])
    await call.message.edit_text(f"<b>💳 Продление ключа</b>\n\nВыберите период продления:", reply_markup=kb)

@dp.callback_query(F.data.startswith("buy_new_") | F.data.startswith("buy_ext_"))
async def select_payment_method(call: CallbackQuery):
    parts = call.data.split("_")
    action, dev_id, days = parts[1], int(parts[2]), int(parts[3])
    
    price_rub = PRICES[days]["rub"]
    price_stars = PRICES[days]["stars"]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⭐️ Telegram Stars ({price_stars})", callback_data=f"pay_{action}_{dev_id}_{days}_stars")],
        [InlineKeyboardButton(text=f"📱 СБП ({price_rub} ₽)", callback_data=f"pay_{action}_{dev_id}_{days}_sbp")],
        [InlineKeyboardButton(text=f"💳 Банковская карта ({price_rub} ₽)", callback_data=f"pay_{action}_{dev_id}_{days}_card")],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data="back_keys")]
    ])
    await call.message.edit_text(f"🛒 Тариф на <b>{days} дней</b>.\n\nВыберите удобный способ оплаты:", reply_markup=kb)

# --- ОПЛАТА ЗВЕЗДАМИ ---
@dp.callback_query(F.data.endswith("_stars"))
async def send_invoice_stars(call: CallbackQuery):
    parts = call.data.split("_")
    action, dev_id, days = parts[1], int(parts[2]), int(parts[3])
    title = "Новый VPN Ключ" if action == "new" else "Продление VPN"
    
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title=f"{title} ({days} дней)",
        description=f"Оплата доступа к MagVPN на {days} дней.",
        payload=f"stars_{action}_{dev_id}_{days}_{uuid.uuid4().hex[:6]}", 
        provider_token="", 
        currency="XTR",    
        prices=[LabeledPrice(label=f"VPN на {days} дней", amount=PRICES[days]["stars"])]
    )
    await call.answer()

@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_stars_success(message: types.Message, session: AsyncSession):
    payload = message.successful_payment.invoice_payload
    if payload.startswith("stars_"):
        parts = payload.split("_")
        action, dev_id, days = parts[1], int(parts[2]), int(parts[3])
        
        if action == "new":
            count = (await session.execute(select(func.count(Device.id)).where(Device.user_id == message.from_user.id))).scalar()
            new_dev = Device(
                user_id=message.from_user.id,
                name=f"Ключ #{count + 1}",
                subscription_end=datetime.now() + timedelta(days=days),
                is_paid=True
            )
            session.add(new_dev)
            await session.commit()
            await message.answer(f"✅ <b>Оплата прошла!</b>\nНовый ключ успешно куплен.\nЗайдите в «🔑 Мои ключи», чтобы сгенерировать его.")
            
        elif action == "ext":
            dev = (await session.execute(select(Device).where(Device.id == dev_id))).scalar_one_or_none()
            if dev:
                dev.subscription_end = (dev.subscription_end if dev.subscription_end and dev.subscription_end > datetime.now() else datetime.now()) + timedelta(days=days)
                dev.is_paid = True
                await session.commit()
                
                if dev.vpn_key:
                    try:
                        parsed = urlparse(dev.vpn_key)
                        client_uuid = parsed.username
                        email = parsed.fragment
                        await vpn_service.update_client_expiry(client_uuid, email, dev.subscription_end)
                    except Exception as e:
                        logging.error(f"Failed to update panel expiry: {e}")
                
                await message.answer(f"✅ <b>Оплата прошла!</b>\nКлюч <b>{dev.name}</b> успешно продлен на {days} дней.")

# --- ОПЛАТА ЮKASSA ---
@dp.callback_query(F.data.endswith("_sbp") | F.data.endswith("_card"))
async def create_payment_link_yookassa(call: CallbackQuery):
    parts = call.data.split("_")
    action, dev_id, days, method = parts[1], int(parts[2]), int(parts[3]), parts[4]
    
    await call.message.edit_text("⏳ Генерирую ссылку на оплату...")
    
    payload = {
        "amount": {"value": f"{PRICES[days]['rub']}.00", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": f"https://t.me/{(await bot.me()).username}"},
        "capture": True, "description": f"VPN на {days} дней",
        "metadata": {"action": action, "dev_id": dev_id, "days": days, "user_id": call.from_user.id}
    }
    
    if method == "sbp":
        payload["payment_method_data"] = {"type": "sbp"}
    elif method == "card":
        payload["payment_method_data"] = {"type": "bank_card"}
        
    try:
        payment = await asyncio.to_thread(Payment.create, payload, str(uuid.uuid4()))
        
        method_text = "СБП" if method == "sbp" else "Банковской картой"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🔗 Оплатить ({method_text})", url=payment.confirmation.confirmation_url)],
            [InlineKeyboardButton(text="🔄 Проверить платеж", callback_data=f"check_{payment.id}")]
        ])
        await call.message.edit_text(f"Счет на <b>{PRICES[days]['rub']} ₽</b> создан.\nПосле оплаты нажмите кнопку проверки.", reply_markup=kb)
        
    except Exception as e:
        await call.message.edit_text(f"❌ <b>Ошибка на стороне платежной системы:</b>\n<code>{e}</code>\n\nВозможно, этот способ оплаты еще не активирован в вашем личном кабинете ЮKassa.")

@dp.callback_query(F.data.startswith("check_"))
async def verify_payment_manual(call: CallbackQuery, session: AsyncSession):
    payment_id = call.data.split("_")[1]
    payment = await asyncio.to_thread(Payment.find_one, payment_id)
    
    if payment.status == "succeeded":
        action = payment.metadata.get("action")
        dev_id = int(payment.metadata.get("dev_id"))
        days = int(payment.metadata.get("days"))
        user_id = int(payment.metadata.get("user_id"))
        
        if action == "new":
            count = (await session.execute(select(func.count(Device.id)).where(Device.user_id == user_id))).scalar()
            new_dev = Device(
                user_id=user_id,
                name=f"Ключ #{count + 1}",
                subscription_end=datetime.now() + timedelta(days=days),
                is_paid=True
            )
            session.add(new_dev)
            await session.commit()
            await call.message.edit_text(f"✅ <b>Оплата получена!</b>\nНовый ключ успешно куплен.\nЗайдите в «🔑 Мои ключи».")
        elif action == "ext":
            dev = (await session.execute(select(Device).where(Device.id == dev_id))).scalar_one_or_none()
            if dev:
                dev.subscription_end = (dev.subscription_end if dev.subscription_end and dev.subscription_end > datetime.now() else datetime.now()) + timedelta(days=days)
                dev.is_paid = True
                await session.commit()
                
                if dev.vpn_key:
                    try:
                        parsed = urlparse(dev.vpn_key)
                        client_uuid = parsed.username
                        email = parsed.fragment
                        await vpn_service.update_client_expiry(client_uuid, email, dev.subscription_end)
                    except Exception as e:
                        logging.error(f"Failed to update panel expiry: {e}")
                
                await call.message.edit_text(f"✅ <b>Оплата получена!</b>\nСрок действия ключа продлен.")
    elif payment.status == "pending":
        await call.answer("⏳ Платеж еще обрабатывается.", show_alert=True)
    else:
        await call.message.edit_text("❌ Платеж отменен или просрочен.")

async def main():
    logging.basicConfig(level=logging.INFO)
    await set_bot_commands() 
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    print("🚀 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
