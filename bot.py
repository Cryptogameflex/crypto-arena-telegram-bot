import os
import asyncio
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import aiohttp
import json
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError
from dotenv import load_dotenv # Importējam dotenv

# Ielādējam vides mainīgos no .env faila (tikai lokālai attīstībai)
load_dotenv()

# Konfigurācija - tagad lasām no vides mainīgajiem
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
GROUP_ID = int(os.getenv("GROUP_ID"))
TRONSCAN_API_KEY = os.getenv("TRONSCAN_API_KEY")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")

# Pārējā konfigurācija paliek nemainīga
SUBSCRIPTION_PRICE = 25  # USDT
SUBSCRIPTION_DAYS = 30

# Logging konfigurācija
logging.basicConfig(
format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
level=logging.INFO
)
logger = logging.getLogger(__name__)

class CryptoArenaBot:
def __init__(self):
    # Pārbaudām, vai visi nepieciešamie vides mainīgie ir iestatīti
    if not all([TELEGRAM_BOT_TOKEN, ADMIN_USER_ID, GROUP_ID, TRONSCAN_API_KEY, WALLET_ADDRESS]):
        logger.error("Trūkst viens vai vairāki nepieciešamie vides mainīgie. Lūdzu, pārbaudiet .env failu vai servera konfigurāciju.")
        raise ValueError("Trūkst vides mainīgie.")

    self.app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    self.init_database()
    self.setup_handlers()

def init_database(self):
    """Inicializē SQLite datubāzi"""
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            txid TEXT,
            start_date TEXT,
            end_date TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            reminder_sent_12h INTEGER DEFAULT 0
        )
    ''')
    
    # Pievieno jaunu kolonnu, ja tā neeksistē (lai atjauninātu esošās datubāzes)
    try:
        cursor.execute("ALTER TABLE subscriptions ADD COLUMN reminder_sent_12h INTEGER DEFAULT 0")
        logger.info("Pievienota kolonna 'reminder_sent_12h' tabulai 'subscriptions'.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            logger.info("Kolonna 'reminder_sent_12h' jau eksistē.")
        else:
            logger.error(f"Kļūda pievienojot kolonnu: {e}")
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            txid TEXT PRIMARY KEY,
            user_id INTEGER,
            amount REAL,
            verified_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def setup_handlers(self):
    """Uzstāda bot handlerus"""
    self.app.add_handler(CommandHandler("start", self.start_command))
    self.app.add_handler(CommandHandler("status", self.status_command))
    self.app.add_handler(CommandHandler("admin", self.admin_command))
    self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_txid))
    
    logger.info("✅ Visi handleri ir reģistrēti")

async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sākuma komanda ar maksājuma instrukcijām"""
    user = update.effective_user
    
    welcome_text = f"""
🎯 **KRIPTO ARĒNA PREMIUM KLUBA APMAKSA**

Lai pievienotos Premium klubam, tev jāveic maksājums:

💰 **Cena:** {SUBSCRIPTION_PRICE} USDT
⏰ **Periods:** {SUBSCRIPTION_DAYS} dienas
🌐 **Tīkls:** TRC-20 (Tron)

📍 **Maksājuma adrese:**
`{WALLET_ADDRESS}`

**Instrukcijas:**
1. Nosūti {SUBSCRIPTION_PRICE} USDT uz augstāk norādīto adresi
2. Pēc maksājuma nosūti man transaction ID (TXID)
3. Es pārbaudīšu maksājumu un pievienošu tevi grupai

⚠️ **Svarīgi:** Maksājums jāveic TRC-20 tīklā!

Nosūti man TXID pēc maksājuma veikšanas. (Sagaidi kamēr visi bloki ir apstiprināti).
"""
    
    await update.message.reply_text(
        welcome_text, 
        parse_mode='Markdown'
    )

async def handle_txid(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Apstrādā TXID ziņojumus"""
    # Pārbauda vai ziņojums ir privātā sarunā
    if update.message.chat.type != 'private':
        return  # Ignorē ziņojumus grupās

    user = update.effective_user
    txid = update.message.text.strip()
    
    # Pārbauda vai TXID formāts ir pareizs
    if len(txid) != 64:
        await update.message.reply_text(
            "❌ Nepareizs TXID formāts. TXID jābūt 64 simbolu garam."
        )
        return
    
    # Pārbauda vai TXID jau nav izmantots
    if self.is_txid_used(txid):
        await update.message.reply_text(
            "❌ Šis TXID jau ir izmantots. Katrs TXID var tikt izmantots tikai vienu reizi."
        )
        return
    
    await update.message.reply_text("🔍 Pārbaudu maksājumu... Lūdzu uzgaidi.")
    
    # Verificē transakciju
    is_valid = await self.verify_transaction(txid, user.id)
    
    if is_valid:
        # Pievieno lietotāju grupai
        success = await self.add_user_to_group(user)
        
        if success:
            # Saglabā abonementu datubāzē
            self.save_subscription(user, txid)
            
            await update.message.reply_text(
                f"✅ Maksājums apstiprināts!\n"
                f"🎉 Tu esi pievienots Premium grupai uz {SUBSCRIPTION_DAYS} dienām.\n"
                f"📅 Abonements beigsies: {(datetime.now() + timedelta(days=SUBSCRIPTION_DAYS)).strftime('%d.%m.%Y %H:%M')}"
            )
            
            # Paziņo adminam
            await self.notify_admin(f"✅ Jauns dalībnieks: {user.first_name} (@{user.username})\nTXID: {txid}")
        else:
            await update.message.reply_text(
                "❌ Neizdevās pievienot grupai. Lūdzu sazinies ar administratoru."
            )
    else:
        await update.message.reply_text(
            "❌ Maksājums nav atrasts vai nav derīgs.\n"
            "Pārbaudi vai:\n"
            "• TXID ir pareizs\n"
            "• Maksājums ir 25 USDT\n"
            "• Izmantots TRC-20 tīkls\n"
            "• Maksājums nosūtīts uz pareizo adresi\n"
            "• Sazināties ar atbalstu @arenasupport"
        )

async def verify_transaction(self, txid: str, user_id: int) -> bool:
    """Verificē transakciju caur TronScan API"""
    try:
        url = f"https://apilist.tronscanapi.com/api/transaction-info?hash={txid}"
        headers = {
            "TRON-PRO-API-KEY": TRONSCAN_API_KEY
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    logger.error(f"TronScan API error: {response.status}")
                    return False
                
                data = await response.json()
                
                # Pārbauda vai transakcija eksistē
                if not data or 'trc20TransferInfo' not in data:
                    return False
                
                transfers = data.get('trc20TransferInfo', [])
                
                for transfer in transfers:
                    # Pārbauda vai maksājums ir uz mūsu adresi
                    if (transfer.get('to_address') == WALLET_ADDRESS and 
                        float(transfer.get('amount_str', 0)) / 1000000 >= SUBSCRIPTION_PRICE):
                        
                        # Saglabā transakciju
                        self.save_transaction(txid, user_id, float(transfer.get('amount_str', 0)) / 1000000)
                        return True
                
                return False
                
    except Exception as e:
        logger.error(f"Error verifying transaction: {e}")
        return False

def is_txid_used(self, txid: str) -> bool:
    """Pārbauda vai TXID jau ir izmantots"""
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM transactions WHERE txid = ?", (txid,))
    count = cursor.fetchone()[0]
    
    conn.close()
    return count > 0

def save_transaction(self, txid: str, user_id: int, amount: float):
    """Saglabā transakciju datubāzē"""
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    cursor.execute(
        "INSERT OR REPLACE INTO transactions (txid, user_id, amount) VALUES (?, ?, ?)",
        (txid, user_id, amount)
    )
    
    conn.commit()
    conn.close()

def save_subscription(self, user, txid: str):
    """Saglabā abonementu datubāzē"""
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    start_date = datetime.now()
    end_date = start_date + timedelta(days=SUBSCRIPTION_DAYS)
    
    cursor.execute('''
        INSERT OR REPLACE INTO subscriptions 
        (user_id, username, first_name, txid, start_date, end_date, is_active, reminder_sent_12h)
        VALUES (?, ?, ?, ?, ?, ?, 1, 0)
    ''', (
        user.id,
        user.username or '',
        user.first_name or '',
        txid,
        start_date.isoformat(),
        end_date.isoformat()
    ))
    
    conn.commit()
    conn.close()

async def add_user_to_group(self, user) -> bool:
    """Pievieno lietotāju grupai"""
    try:
        # Ģenerē invite link
        invite_link = await self.app.bot.create_chat_invite_link(
            chat_id=GROUP_ID,
            member_limit=1,
            expire_date=datetime.now() + timedelta(hours=1)
        )
        
        # Nosūta invite link lietotājam
        await self.app.bot.send_message(
            chat_id=user.id,
            text=f"🔗 Tavs personīgais uzaicinājuma links:\n{invite_link.invite_link}\n\n"
                 f"⏰ Links derīgs 1 stundu."
        )
        
        return True
        
    except Exception as e:
        logger.error(f"Error adding user to group: {e}")
        return False

async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Parāda lietotāja abonements statusu"""
    user = update.effective_user
    
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT * FROM subscriptions WHERE user_id = ? AND is_active = 1",
        (user.id,)
    )
    subscription = cursor.fetchone()
    conn.close()
    
    if subscription:
        end_date = datetime.fromisoformat(subscription[5])
        days_left = (end_date - datetime.now()).days
        
        status_text = f"""
📊 **Tavs abonements:**

✅ Status: Aktīvs
📅 Beigas: {end_date.strftime('%d.%m.%Y %H:%M')}
⏰ Atlikušās dienas: {days_left}
💳 TXID: `{subscription[3]}`
        """
    else:
        status_text = "❌ Tev nav aktīva abonementa. Izmanto /start lai iegādātos."
    
    await update.message.reply_text(status_text, parse_mode='Markdown')

async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin komandas"""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ Nav atļaujas.")
        return
    
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    # Aktīvie abonenti
    cursor.execute("SELECT COUNT(*) FROM subscriptions WHERE is_active = 1")
    active_count = cursor.fetchone()[0]
    
    # Kopējie abonenti
    cursor.execute("SELECT COUNT(*) FROM subscriptions")
    total_count = cursor.fetchone()[0]
    
    # Šodienas ieņēmumi
    today = datetime.now().date()
    cursor.execute(
        "SELECT SUM(amount) FROM transactions WHERE DATE(verified_at) = ?",
        (today,)
    )
    today_revenue = cursor.fetchone()[0] or 0
    
    conn.close()
    
    admin_text = f"""
👑 **Admin panelis:**

📊 Aktīvie abonenti: {active_count}
👥 Kopējie abonenti: {total_count}
💰 Šodienas ieņēmumi: {today_revenue:.2f} USDT

Komandas:
/start - Sākuma ziņojums
/status - Abonementa status
/admin - Admin panelis
    """
    
    await update.message.reply_text(admin_text, parse_mode='Markdown')

async def notify_admin(self, message: str):
    """Nosūta ziņojumu adminam"""
    try:
        await self.app.bot.send_message(chat_id=ADMIN_USER_ID, text=message)
    except Exception as e:
        logger.error(f"Error notifying admin: {e}")

async def send_subscription_reminders(self):
    """Nosūta atgādinājumus par beidzošiem abonementiem (12h pirms)"""
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    now = datetime.now()
    twelve_hours_from_now = now + timedelta(hours=12)
    
    # Atrod aktīvos abonementus, kas beigsies nākamo 12 stundu laikā un kuriem atgādinājums vēl nav nosūtīts
    cursor.execute('''
        SELECT user_id, first_name, end_date 
        FROM subscriptions 
        WHERE is_active = 1 
        AND datetime(end_date) > datetime(?) 
        AND datetime(end_date) <= datetime(?)
        AND reminder_sent_12h = 0
    ''', (now.isoformat(), twelve_hours_from_now.isoformat()))
    
    users_to_remind = cursor.fetchall()
    
    for user_data in users_to_remind:
        user_id, first_name, end_date_str = user_data
        
        try:
            await self.app.bot.send_message(
                chat_id=user_id,
                text="Vēlos Tevi informēt, ka šodien ir tava pēdējā Premium Kluba izmantošanas diena. Lai turpinātu baudīt Premium Kluba priekšrocības, aicinu veikt maksājumu!"
            )
            
            # Atzīmē, ka atgādinājums ir nosūtīts
            cursor.execute(
                "UPDATE subscriptions SET reminder_sent_12h = 1 WHERE user_id = ?",
                (user_id,)
            )
            logger.info(f"Nosūtīts 12h atgādinājums lietotājam: {user_id}")
            
        except Exception as e:
            logger.error(f"Kļūda sūtot atgādinājumu lietotājam {user_id}: {e}")
    
    conn.commit()
    conn.close()

async def check_expired_subscriptions(self):
    """Pārbauda beidzošos abonementus"""
    conn = sqlite3.connect('subscriptions.db')
    cursor = conn.cursor()
    
    # Atrod beidzošos abonementus
    cursor.execute('''
        SELECT user_id, username, first_name, end_date 
        FROM subscriptions 
        WHERE is_active = 1 AND datetime(end_date) <= datetime('now')
    ''')
    
    expired_users = cursor.fetchall()
    
    for user_data in expired_users:
        user_id, username, first_name, end_date = user_data
        
        try:
            # Izmet no grupas
            await self.app.bot.ban_chat_member(
                chat_id=GROUP_ID,
                user_id=user_id
            )
            
            # Atceļ banu (lai var atgriezties ar jaunu abonementu)
            await self.app.bot.unban_chat_member(
                chat_id=GROUP_ID,
                user_id=user_id
            )
            
            # Deaktivizē abonementu
            cursor.execute(
                "UPDATE subscriptions SET is_active = 0 WHERE user_id = ?",
                (user_id,)
            )
            
            # Paziņo lietotājam
            await self.app.bot.send_message(
                chat_id=user_id,
                text="⏰ Tavs Premium abonemets ir beidzies.\n"
                     "Lai turpinātu, izmanto /start lai iegādātos jaunu abonementu."
            )
            
            logger.info(f"Removed expired user: {user_id}")
            
        except Exception as e:
            logger.error(f"Error removing expired user {user_id}: {e}")
    
    conn.commit()
    conn.close()
    
    if expired_users:
        await self.notify_admin(f"🔄 Noņemti {len(expired_users)} lietotāji ar beidzošiem abonementiem.")

async def subscription_checker(self):
    """Periodiski pārbauda abonementus un sūta atgādinājumus"""
    while True:
        try:
            await self.send_subscription_reminders()
            await self.check_expired_subscriptions()
            await asyncio.sleep(3600)  # Pārbauda katru stundu
        except Exception as e:
            logger.error(f"Error in subscription checker: {e}")
            await asyncio.sleep(300)  # Mēģina atkal pēc 5 minūtēm

async def run(self):
    """Palaiž botu"""
    # Sāk abonementu pārbaudītāju
    asyncio.create_task(self.subscription_checker())
    
    # Sāk botu
    await self.app.initialize()
    await self.app.start()
    await self.app.updater.start_polling()
    
    logger.info("🤖 Kripto Arēnas bots ir palaists!")
    logger.info("📋 Handlers registered:")
    for handler in self.app.handlers[0]:
        logger.info(f"  - {type(handler).__name__}")
    
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Apstāju botu...")
    finally:
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()

if __name__ == "__main__":
bot = CryptoArenaBot()
asyncio.run(bot.run())
