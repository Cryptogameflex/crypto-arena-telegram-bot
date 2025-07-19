import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import aiohttp
import json
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError
from dotenv import load_dotenv
from supabase import create_client, Client # Importējam Supabase

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

# Logging konfigurācija
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class CryptoArenaBot:
    def __init__(self):
        # Pārbaudām, vai visi nepieciešamie vides mainīgie ir iestatīti
        if not all([TELEGRAM_BOT_TOKEN, ADMIN_USER_ID, GROUP_ID, TRONSCAN_API_KEY, WALLET_ADDRESS, SUPABASE_URL, SUPABASE_KEY]):
            logger.error("Trūkst viens vai vairāki nepieciešamie vides mainīgie. Lūdzu, pārbaudiet .env failu vai servera konfigurāciju.")
            raise ValueError("Trūkst vides mainīgie.")

        self.app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
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
        if await self.is_txid_used(txid):
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
                await self.save_subscription(user, txid)
                
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

    async def sendtx_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Apstrādā /sendtx komandu ar TXID, izmantojot handle_txid loģiku."""
        if not context.args:
            await update.message.reply_text(
                "Lūdzu, ieraksti TXID:\n`/sendtx <TXID>`",
                parse_mode='Markdown'
            )
            return

        txid = context.args[0].strip()
        
        # Pārbauda vai ziņojums ir privātā sarunā
        if update.message.chat.type != 'private':
            await update.message.reply_text("Šo komandu var izmantot tikai privātā sarunā ar botu.")
            return

        # Izveido pagaidu Update objektu, lai atkārtoti izmantotu handle_txid loģiku
        # Tas nodrošina, ka TXID tiek pārbaudīts caur TronScan API
        class DummyMessage:
            def __init__(self, text, chat):
                self.text = text
                self.chat = chat
            
        class DummyUpdate:
            def __init__(self, message, effective_user):
                self.message = message
                self.effective_user = effective_user

        dummy_message = DummyMessage(txid, update.message.chat)
        dummy_update = DummyUpdate(dummy_message, update.effective_user)

        await self.handle_txid(dummy_update, context)

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
                            await self.save_transaction(txid, user_id, float(transfer.get('amount_str', 0)) / 1000000)
                            return True
                
                return False
                
        except Exception as e:
            logger.error(f"Error verifying transaction: {e}")
            return False

    async def is_txid_used(self, txid: str) -> bool:
        """Pārbauda vai TXID jau ir izmantots Supabase datubāzē"""
        try:
            response = self.supabase.table("transactions").select("txid").eq("txid", txid).execute()
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Error checking if TXID is used in Supabase: {e}")
            return False

    async def save_transaction(self, txid: str, user_id: int, amount: float):
        """Saglabā transakciju Supabase datubāzē"""
        try:
            response = self.supabase.table("transactions").insert({
                "txid": txid,
                "user_id": user_id,
                "amount": amount,
                "verified_at": datetime.now().isoformat()
            }).execute()
            if response.status_code not in (200, 201):
                logger.error(f"Error saving transaction to Supabase: {response.status_code} - {response.data}")
        except Exception as e:
            logger.error(f"Error saving transaction to Supabase: {e}")

    async def save_subscription(self, user, txid: str):
        """Saglabā abonementu Supabase datubāzē (vai atjaunina, ja lietotājs jau eksistē)"""
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
            if response.status_code not in (200, 201):
                logger.error(f"Error saving subscription to Supabase: {response.status_code} - {response.data}")
        except Exception as e:
            logger.error(f"Error saving subscription to Supabase: {e}")

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
        """Parāda lietotāja abonements statusu no Supabase"""
        user = update.effective_user
        
        try:
            response = self.supabase.table("subscriptions").select("*").eq("user_id", user.id).eq("is_active", True).execute()
            subscription = response.data[0] if response.data else None
        except Exception as e:
            logger.error(f"Error fetching subscription status from Supabase: {e}")
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

    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin komandas no Supabase"""
        if update.effective_user.id != ADMIN_USER_ID:
            await update.message.reply_text("❌ Nav atļaujas.")
            return
        
        try:
            # Aktīvie abonenti
            active_count_resp = self.supabase.table("subscriptions").select("count", count="exact").eq("is_active", True).execute()
            active_count = active_count_resp.count if active_count_resp.count is not None else 0
            
            # Kopējie abonenti
            total_count_resp = self.supabase.table("subscriptions").select("count", count="exact").execute()
            total_count = total_count_resp.count if total_count_resp.count is not None else 0
            
            # Šodienas ieņēmumi
            today = datetime.now().date()
            today_revenue_resp = self.supabase.table("transactions").select("amount").gte("verified_at", today.isoformat()).lt("verified_at", (today + timedelta(days=1)).isoformat()).execute()
            today_revenue = sum(item['amount'] for item in today_revenue_resp.data) if today_revenue_resp.data else 0
            
        except Exception as e:
            logger.error(f"Error fetching admin data from Supabase: {e}")
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

    async def notify_admin(self, message: str):
        """Nosūta ziņojumu adminam"""
        try:
            await self.app.bot.send_message(chat_id=ADMIN_USER_ID, text=message)
        except Exception as e:
            logger.error(f"Error notifying admin: {e}")

    async def send_subscription_reminders(self):
        """Nosūta atgādinājumus par beidzošiem abonementiem (12h pirms) no Supabase"""
        now = datetime.now()
        twelve_hours_from_now = now + timedelta(hours=12)
        
        try:
            response = self.supabase.table("subscriptions").select("user_id, first_name, end_date").eq("is_active", True).gte("end_date", now.isoformat()).lte("end_date", twelve_hours_from_now.isoformat()).eq("reminder_sent_12h", False).execute()
            users_to_remind = response.data
        except Exception as e:
            logger.error(f"Error fetching users for reminders from Supabase: {e}")
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
                
                # Atzīmē, ka atgādinājums ir nosūtīts
                update_resp = self.supabase.table("subscriptions").update({"reminder_sent_12h": True}).eq("user_id", user_id).execute()
                if update_resp.status_code not in (200, 204):
                    logger.error(f"Error updating reminder_sent_12h for {user_id}: {update_resp.status_code} - {update_resp.data}")
                logger.info(f"Nosūtīts 12h atgādinājums lietotājam: {user_id}")
                
            except Exception as e:
                logger.error(f"Kļūda sūtot atgādinājumu lietotājam {user_id}: {e}")

    async def check_expired_subscriptions(self):
        """Pārbauda beidzošos abonementus no Supabase"""
        now = datetime.now()
        
        try:
            response = self.supabase.table("subscriptions").select("user_id, username, first_name, end_date").eq("is_active", True).lte("end_date", now.isoformat()).execute()
            expired_users = response.data
        except Exception as e:
            logger.error(f"Error fetching expired users from Supabase: {e}")
            expired_users = []
        
        for user_data in expired_users:
            user_id = user_data['user_id']
            username = user_data['username']
            first_name = user_data['first_name']
            end_date = user_data['end_date']
            
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
                update_resp = self.supabase.table("subscriptions").update({"is_active": False}).eq("user_id", user_id).execute()
                if update_resp.status_code not in (200, 204):
                    logger.error(f"Error deactivating subscription for {user_id}: {update_resp.status_code} - {update_resp.data}")
                
                # Paziņo lietotājam
                await self.app.bot.send_message(
                    chat_id=user_id,
                    text="⏰ Tavs Premium abonemets ir beidzies.\n"
                         "Lai turpinātu, izmanto /start lai iegādātos jaunu abonementu."
                )
                
                logger.info(f"Removed expired user: {user_id}")
                
            except Exception as e:
                logger.error(f"Error removing expired user {user_id}: {e}")
        
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
