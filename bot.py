import os
import sys
import locale

# PIEVIENOJIET ŠO KODA SĀKUMĀ - pirms citiem importiem
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['LC_ALL'] = 'en_US.UTF-8'
os.environ['LANG'] = 'en_US.UTF-8'

# Iestatīt UTF-8 kodējumu
try:
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, 'C.UTF-8')
    except locale.Error:
        pass  # Turpināt bez locale iestatījumiem

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import aiohttp
import json
from telegram import Update, User
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError
from dotenv import load_dotenv
from supabase import create_client, Client

# Ielādējam vides mainīgos no .env faila
load_dotenv()

# Konfigurācija - tagad lasām no vides mainīgajiem
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
GROUP_ID = int(os.getenv("GROUP_ID"))
TRONSCAN_API_KEY = os.getenv("TRONSCAN_API_KEY")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Pārējā konfigurācija paliek nemainīga
SUBSCRIPTION_PRICE = 25  # USDT
SUBSCRIPTION_DAYS = 30

# Logging konfigurācija ar UTF-8 atbalstu
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Iestatīt UTF-8 kodējumu logging handleram
for handler in logging.root.handlers:
    if hasattr(handler, 'stream') and hasattr(handler.stream, 'reconfigure'):
        handler.stream.reconfigure(encoding='utf-8')

logger = logging.getLogger(__name__)

# Samazinām Supabase/HTTP žurnālu līmeņus
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("supabase").setLevel(logging.WARNING)


class CryptoArenaBot:
    def __init__(self):
        # Pārbaudām, vai visi nepieciešamie vides mainīgie ir iestatīti
        if not all([TELEGRAM_BOT_TOKEN, ADMIN_USER_ID, GROUP_ID, TRONSCAN_API_KEY, WALLET_ADDRESS, SUPABASE_URL, SUPABASE_KEY]):
            logger.error("Trūkst viens vai vairāki nepieciešamie vides mainīgie. Lūdzu, pārbaudiet .env failu vai servera konfigurāciju.")
            raise ValueError("Trūkst vides mainīgie.")

        self.app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Pievienots error handling Supabase klientam
        try:
            self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
            logging.debug(f"🔑 Loaded SUPABASE_URL='{SUPABASE_URL}'")
            logging.debug(f"🔑 Loaded SUPABASE_KEY='{SUPABASE_KEY[:8]}'...")
            
            # Testējam Supabase savienojumu
            test_response = self.supabase.table("transactions").select("count", count="exact").limit(0).execute()
            logging.info("✅ Supabase savienojums veiksmīgs")
            
        except Exception as e:
            logging.error(f"❌ Supabase savienojuma kļūda: {e}")
            raise
            
        self.setup_handlers()

    def setup_handlers(self):
        """Uzstāda bot handlerus"""
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        self.app.add_handler(CommandHandler("admin", self.admin_command))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_txid))
        self.app.add_handler(CommandHandler("sendtx", self.sendtx_command))
        
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
        logging.debug("✅ /start fired!")

    async def is_txid_used(self, txid: str) -> bool:
        """Pārbauda vai TXID jau ir izmantots Supabase datubāzē"""
        print(f"DEBUG: Checking if TXID is used: {txid}")
        try:
            response = self.supabase.table("transactions").select("txid").eq("txid", txid).execute()
            print(f"DEBUG: Supabase is_txid_used response data: {response.data}")
            
            # LABOJUMS: Pareiza kļūdas ziņojuma loģika
            if len(response.data) > 0:
                logging.warning(f"TXID {txid} jau ir izmantots")
                return True
            else:
                logging.info(f"TXID {txid} nav izmantots - var turpināt")
                return False
                
        except Exception as e:
            logger.error(f"Error checking if TXID is used in Supabase: {e}")
            print(f"DEBUG: Exception in is_txid_used: {e}")
            return False

    async def _process_txid(self, chat_id: int, user: User, txid: str, context: ContextTypes.DEFAULT_TYPE):
        """Galvenā loģika TXID apstrādei"""
        print(f"DEBUG: Entered _process_txid function for user {user.id}")
        print(f"DEBUG: TXID received: {repr(txid)}")
        print(f"DEBUG: Length of TXID received: {len(txid)}")

        is_valid = False
        test_txid_value = "TEST_TXID_FOR_SUPABASE_SAVE_0123456789ABCDEF0123456789ABCDEF01234"
        
        if txid == test_txid_value:
            print("DEBUG: Using test TXID, bypassing TronScan verification.")
            is_valid = await self.save_transaction(txid, user.id, SUBSCRIPTION_PRICE)
            if is_valid:
                print("DEBUG: Test TXID saved to Supabase successfully.")
            else:
                print("DEBUG: Failed to save test TXID to Supabase.")
        else:
            # Pārbauda vai TXID formāts ir pareizs
            if len(txid) != 64:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Nepareizs TXID formāts. TXID jābūt 64 simbolu garam."
                )
                print(f"DEBUG: Invalid TXID format: {repr(txid)}")
                return
            
            # LABOJUMS: Pareiza kļūdas ziņojuma loģika
            if await self.is_txid_used(txid):
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Šis TXID jau ir izmantots. Katrs TXID var tikt izmantots tikai vienu reizi."
                )
                print(f"DEBUG: TXID already used: {txid}")
                return
            
            await context.bot.send_message(chat_id=chat_id, text="🔍 Pārbaudu maksājumu... Lūdzu uzgaidi.")
            print(f"DEBUG: Verifying transaction for TXID: {txid}")
            is_valid = await self.verify_transaction(txid, user.id)
        
        if is_valid:
            print("DEBUG: Transaction is valid.")
            success = await self.add_user_to_group(user)
            
            if success:
                print("DEBUG: User added to group successfully.")
                await self.save_subscription(user, txid)
                
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ Maksājums apstiprināts!\n"
                         f"🎉 Tu esi pievienots Premium grupai uz {SUBSCRIPTION_DAYS} dienām.\n"
                         f"📅 Abonements beigsies: {(datetime.now() + timedelta(days=SUBSCRIPTION_DAYS)).strftime('%d.%m.%Y %H:%M')}"
                )
                
                await self.notify_admin(f"✅ Jauns dalībnieks: {user.first_name} (@{user.username})\nTXID: {txid}")
            else:
                print("DEBUG: Failed to add user to group.")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Neizdevās pievienot grupai. Lūdzu sazinies ar administratoru."
                )
        else:
            print("DEBUG: Transaction is NOT valid.")
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Maksājums nav atrasts vai nav derīgs.\n"
                     "Pārbaudi vai:\n"
                     "• TXID ir pareizs\n"
                     "• Maksājums ir 25 USDT\n"
                     "• Izmantots TRC-20 tīkls\n"
                     "• Maksājums nosūtīts uz pareizo adresi\n"
                     "• Sazināties ar atbalstu @arenasupport"
            )
        print("DEBUG: Exited _process_txid function.")

    async def handle_txid(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Apstrādā TXID ziņojumus"""
        print("DEBUG: Entered handle_txid function.")
        if update.message.chat.type != 'private':
            return

        user = update.effective_user
        print(f"DEBUG: Raw update.message.text: {repr(update.message.text)}")
        print(f"DEBUG: Length of raw update.message.text: {len(update.message.text)}")

        txid = update.message.text.strip()
        await self._process_txid(update.effective_chat.id, user, txid, context)
        print("DEBUG: Exited handle_txid function.")

    async def sendtx_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Apstrādā /sendtx komandu ar TXID"""
        print("DEBUG: Entered sendtx_command function.")
        logging.debug(f"💬 /sendtx triggered by @{update.effective_user.username} ({update.effective_user.id})")
        
        if not context.args:
            await update.message.reply_text(
                "Lūdzu, ieraksti TXID:\n`/sendtx <TXID>`",
                parse_mode='Markdown'
            )
            print("DEBUG: No TXID provided in /sendtx command.")
            return

        raw_txid_arg = context.args[0]
        print(f"DEBUG: Raw TXID from context.args[0]: {repr(raw_txid_arg)}")
        print(f"DEBUG: Length of raw TXID: {len(raw_txid_arg)}")

        txid = raw_txid_arg.strip()
        print(f"DEBUG: TXID after strip() in sendtx_command: {repr(txid)}")
        print(f"DEBUG: Length of TXID after strip() in sendtx_command: {len(txid)}")
        
        if update.message.chat.type != 'private':
            await update.message.reply_text("Šo komandu var izmantot tikai privātā sarunā ar botu.")
            print("DEBUG: /sendtx used in non-private chat.")
            return

        await self._process_txid(update.effective_chat.id, update.effective_user, txid, context)
        print("DEBUG: Exited sendtx_command function.")

    async def verify_transaction(self, txid: str, user_id: int) -> bool:
        """Verificē transakciju caur TronScan API"""
        print(f"DEBUG: Entered verify_transaction function for TXID: {txid}")
        try:
            url = f"https://apilist.tronscanapi.com/api/transaction-info?hash={txid}"
            headers = {
                "TRON-PRO-API-KEY": TRONSCAN_API_KEY
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    print(f"DEBUG: TronScan API response status: {response.status}")
                    if response.status != 200:
                        logger.error(f"TronScan API error: {response.status}")
                        return False
                    
                    data = await response.json()
                    print(f"DEBUG: TronScan API response data: {json.dumps(data, indent=2)}")
                    
                    if not data or 'trc20TransferInfo' not in data:
                        print("DEBUG: No trc20TransferInfo found in TronScan response.")
                        return False
                    
                    transfers = data.get('trc20TransferInfo', [])
                    
                    for transfer in transfers:
                        if (transfer.get('to_address') == WALLET_ADDRESS and 
                            float(transfer.get('amount_str', 0)) / 1000000 >= SUBSCRIPTION_PRICE):
                            
                            print(f"DEBUG: Valid transfer found: {transfer}")
                            return await self.save_transaction(txid, user_id, float(transfer.get('amount_str', 0)) / 1000000)
            
            print("DEBUG: No valid transfer found to WALLET_ADDRESS with sufficient amount.")
            return False
            
        except Exception as e:
            logger.error(f"Error verifying transaction: {e}")
            print(f"DEBUG: Exception in verify_transaction: {e}")
            return False

    async def save_transaction(self, txid: str, user_id: int, amount: float) -> bool:
        """Saglabā transakciju Supabase datubāzē"""
        print(f"DEBUG: Entered save_transaction function for TXID: {txid}, User ID: {user_id}, Amount: {amount}")
        logging.debug(f"💾 save_transaction() called with user_id={user_id!r}, txid={txid!r}")
        
        try:
            resp = self.supabase.table("transactions").insert({
                "txid": txid,
                "user_id": user_id,
                "amount": amount,
                "verified_at": datetime.now().isoformat()
            }).execute()

            logging.debug(f"🟢 Supabase raw response: {resp!r}")
            print(f"DEBUG: Supabase insert raw response: {resp}")
            
            if hasattr(resp, "data") and resp.data:
                logging.debug(f"✅ resp.data: {resp.data}")
                print(f"DEBUG: Supabase insert successful, data: {resp.data}")
                return True
            else:
                logging.error(f"❌ No resp.data attribute, resp attrs: {dir(resp)}")
                print(f"DEBUG: Supabase insert failed or no data. Response attributes: {dir(resp)}")
                if hasattr(resp, 'error') and resp.error:
                    logging.error(f"🔺 Supabase error details: {resp.error}")
                    print(f"DEBUG: Supabase error details: {resp.error}")
            return False
            
        except Exception as e:
            logging.exception(f"🔺 Exception when inserting into Supabase: {e}")
            print(f"DEBUG: Exception in save_transaction: {e}")
            return False

    async def save_subscription(self, user, txid: str):
        """Saglabā abonementu Supabase datubāzē"""
        print(f"DEBUG: Entered save_subscription function for user: {user.id}, TXID: {txid}")
        start_date = datetime.now()
        end_date = start_date + timedelta(days=SUBSCRIPTION_DAYS)
        
        try:
            response = self.supabase.table("subscriptions").upsert({
                "user_id": user.id,
                "username": user.username or '',
                "first_name": user.first_name or '',
                "txid": txid,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "is_active": True,
                "reminder_sent_12h": False
            }).execute()
            
            if response.data:
                print(f"DEBUG: Supabase save_subscription successful, data: {response.data}")
            else:
                logger.error(f"Error saving subscription to Supabase: No data returned. Error: {response.error if hasattr(response, 'error') else 'N/A'}")
                print(f"DEBUG: Supabase save_subscription error: No data returned. Error: {response.error if hasattr(response, 'error') else 'N/A'}")
                
        except Exception as e:
            logger.error(f"Error saving subscription to Supabase: {e}")
            print(f"DEBUG: Exception in save_subscription: {e}")

    async def add_user_to_group(self, user) -> bool:
        """Pievieno lietotāju grupai"""
        print(f"DEBUG: Entered add_user_to_group function for user: {user.id}")
        try:
            invite_link = await self.app.bot.create_chat_invite_link(
                chat_id=GROUP_ID,
                member_limit=1,
                expire_date=datetime.now() + timedelta(hours=1)
            )
            
            await self.app.bot.send_message(
                chat_id=user.id,
                text=f"🔗 Tavs personīgais uzaicinājuma links:\n{invite_link.invite_link}\n\n"
                     f"⏰ Links derīgs 1 stundu."
            )
            print(f"DEBUG: Invite link sent to user {user.id}: {invite_link.invite_link}")
            return True
            
        except Exception as e:
            logger.error(f"Error adding user to group: {e}")
            print(f"DEBUG: Exception in add_user_to_group: {e}")
            return False

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Parāda lietotāja abonements statusu"""
        print(f"DEBUG: Entered status_command for user: {update.effective_user.id}")
        user = update.effective_user
        
        try:
            response = self.supabase.table("subscriptions").select("*").eq("user_id", user.id).eq("is_active", True).execute()
            subscription = response.data[0] if response.data else None
            print(f"DEBUG: Supabase status_command response data: {response.data}")
        except Exception as e:
            logger.error(f"Error fetching subscription status from Supabase: {e}")
            print(f"DEBUG: Exception in status_command (fetching from Supabase): {e}")
            subscription = None
        
        if subscription:
            end_date = datetime.fromisoformat(subscription['end_date'])
            days_left = (end_date - datetime.now()).days
            
            status_text = f"""
📊 **Tavs abonements:**

✅ Status: Aktīvs
📅 Beigas: {end_date.strftime('%d.%m.%Y %H:%M')}
⏰ Atlikušās dienas: {days_left}
💳 TXID: `{subscription['txid']}`
        """
        else:
            status_text = "❌ Tev nav aktīva abonementa. Izmanto /start lai iegādātos."
        
        await update.message.reply_text(status_text, parse_mode='Markdown')
        print("DEBUG: Status message sent.")

    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin komandas"""
        print(f"DEBUG: Entered admin_command for user: {update.effective_user.id}")
        if update.effective_user.id != ADMIN_USER_ID:
            await update.message.reply_text("❌ Nav atļaujas.")
            print("DEBUG: Unauthorized admin access attempt.")
            return
        
        try:
            active_count_resp = self.supabase.table("subscriptions").select("count", count="exact").eq("is_active", True).execute()
            active_count = active_count_resp.count if active_count_resp.count is not None else 0
            print(f"DEBUG: Active users count: {active_count}")
            
            total_count_resp = self.supabase.table("subscriptions").select("count", count="exact").execute()
            total_count = total_count_resp.count if total_count_resp.count is not None else 0
            print(f"DEBUG: Total users count: {total_count}")
            
            today = datetime.now().date()
            today_revenue_resp = self.supabase.table("transactions").select("amount").gte("verified_at", today.isoformat()).lt("verified_at", (today + timedelta(days=1)).isoformat()).execute()
            today_revenue = sum(item['amount'] for item in today_revenue_resp.data) if today_revenue_resp.data else 0
            print(f"DEBUG: Today's revenue: {today_revenue}")
            
        except Exception as e:
            logger.error(f"Error fetching admin data from Supabase: {e}")
            print(f"DEBUG: Exception in admin_command (fetching from Supabase): {e}")
            active_count = 0
            total_count = 0
            today_revenue = 0
        
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
        print("DEBUG: Admin panel message sent.")

    async def notify_admin(self, message: str):
        """Nosūta ziņojumu adminam"""
        print(f"DEBUG: Notifying admin: {message}")
        try:
            await self.app.bot.send_message(chat_id=ADMIN_USER_ID, text=message)
        except Exception as e:
            logger.error(f"Error notifying admin: {e}")
            print(f"DEBUG: Exception in notify_admin: {e}")

    async def send_subscription_reminders(self):
        """Nosūta atgādinājumus par beidzošiem abonementiem"""
        print("DEBUG: Running send_subscription_reminders.")
        now = datetime.now()
        twelve_hours_from_now = now + timedelta(hours=12)
        
        try:
            response = self.supabase.table("subscriptions").select("user_id, first_name, end_date").eq("is_active", True).gte("end_date", now.isoformat()).lte("end_date", twelve_hours_from_now.isoformat()).eq("reminder_sent_12h", False).execute()
            users_to_remind = response.data
            print(f"DEBUG: Users to remind: {users_to_remind}")
        except Exception as e:
            logger.error(f"Error fetching users for reminders from Supabase: {e}")
            print(f"DEBUG: Exception in send_subscription_reminders (fetching from Supabase): {e}")
            users_to_remind = []
        
        for user_data in users_to_remind:
            user_id = user_data['user_id']
            first_name = user_data['first_name']
            end_date_str = user_data['end_date']
            
            try:
                await self.app.bot.send_message(
                    chat_id=user_id,
                    text="Vēlos Tevi informēt, ka šodien ir tava pēdējā Premium Kluba izmantošanas diena. Lai turpinātu baudīt Premium Kluba priekšrocības, aicinu veikt maksājumu!"
                )
                
                update_resp = self.supabase.table("subscriptions").update({"reminder_sent_12h": True}).eq("user_id", user_id).execute()
                if not update_resp.data:
                    logger.error(f"Error updating reminder_sent_12h for {user_id}: No data returned. Error: {update_resp.error if hasattr(update_resp, 'error') else 'N/A'}")
                    print(f"DEBUG: Error updating reminder_sent_12h for {user_id}: No data returned. Error: {update_resp.error if hasattr(update_resp, 'error') else 'N/A'}")
                logger.info(f"Nosūtīts 12h atgādinājums lietotājam: {user_id}")
                
            except Exception as e:
                logger.error(f"Kļūda sūtot atgādinājumu lietotājam {user_id}: {e}")
                print(f"DEBUG: Exception sending reminder to {user_id}: {e}")

    async def check_expired_subscriptions(self):
        """Pārbauda beidzošos abonementus"""
        print("DEBUG: Running check_expired_subscriptions.")
        now = datetime.now()
        
        try:
            response = self.supabase.table("subscriptions").select("user_id, username, first_name, end_date").eq("is_active", True).lte("end_date", now.isoformat()).execute()
            expired_users = response.data
            print(f"DEBUG: Expired users found: {expired_users}")
        except Exception as e:
            logger.error(f"Error fetching expired users from Supabase: {e}")
            print(f"DEBUG: Exception in check_expired_subscriptions (fetching from Supabase): {e}")
            expired_users = []
        
        for user_data in expired_users:
            user_id = user_data['user_id']
            username = user_data['username']
            first_name = user_data['first_name']
            end_date = user_data['end_date']
            
            try:
                await self.app.bot.ban_chat_member(
                    chat_id=GROUP_ID,
                    user_id=user_id
                )
                
                await self.app.bot.unban_chat_member(
                    chat_id=GROUP_ID,
                    user_id=user_id
                )
                
                update_resp = self.supabase.table("subscriptions").update({"is_active": False}).eq("user_id", user_id).execute()
                if not update_resp.data:
                    logger.error(f"Error deactivating subscription for {user_id}: No data returned. Error: {update_resp.error if hasattr(update_resp, 'error') else 'N/A'}")
                    print(f"DEBUG: Error deactivating subscription for {user_id}: No data returned. Error: {update_resp.error if hasattr(update_resp, 'error') else 'N/A'}")
                
                await self.app.bot.send_message(
                    chat_id=user_id,
                    text="⏰ Tavs Premium abonemets ir beidzies.\n"
                         "Lai turpinātu, izmanto /start lai iegādātos jaunu abonementu."
                )
                
                logger.info(f"Removed expired user: {user_id}")
                print(f"DEBUG: User {user_id} removed and notified.")
                
            except Exception as e:
                logger.error(f"Error removing expired user {user_id}: {e}")
                print(f"DEBUG: Exception removing expired user {user_id}: {e}")
        
        if expired_users:
            await self.notify_admin(f"🔄 Noņemti {len(expired_users)} lietotāji ar beidzošiem abonementiem.")
            print(f"DEBUG: Admin notified about {len(expired_users)} expired users.")

    async def subscription_checker(self):
        """Periodiski pārbauda abonementus un sūta atgādinājumus"""
        print("DEBUG: Starting subscription_checker loop.")
        while True:
            try:
                await self.send_subscription_reminders()
                await self.check_expired_subscriptions()
                await asyncio.sleep(3600)  # Pārbauda katru stundu
            except Exception as e:
                logger.error(f"Error in subscription checker: {e}")
                print(f"DEBUG: Exception in subscription_checker loop: {e}")
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
    logging.info("🚀 Bot starting...")
    bot = CryptoArenaBot()
    asyncio.run(bot.run())
