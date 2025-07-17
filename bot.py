import os
import asyncio
import logging
from datetime import datetime, timedelta
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError
from dotenv import load_dotenv
from supabase import create_client, Client # Importējam Supabase klientu

# Ielādējam vides mainīgos no .env faila (tikai lokālai attīstībai)
load_dotenv()

# Konfigurācija - tagad lasām no vides mainīgajiem
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
GROUP_ID = int(os.getenv("GROUP_ID"))
TRONSCAN_API_KEY = os.getenv("TRONSCAN_API_KEY")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY") # Anon public key

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
        # Inicializējam Supabase klientu
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.setup_handlers()
        logger.info("✅ Supabase klients inicializēts.")

    # init_database funkcija vairs nav nepieciešama, jo Supabase tabulas tiek izveidotas atsevišķi
    # (izmantojot scripts/create_supabase_tables.sql)

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
        if await self.is_txid_used(txid): # Tagad asinhrona funkcija
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
                await self.save_subscription(user, txid) # Tagad asinhrona funkcija
                
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
                            await self.save_transaction(txid, user_id, float(transfer.get('amount_str', 0)) / 1000000) # Tagad asinhrona funkcija
                            return True
                    
                    return False
                    
        except Exception as e:
            logger.error(f"Error verifying transaction: {e}")
            return False

    async def is_txid_used(self, txid: str) -> bool:
        """Pārbauda vai TXID jau ir izmantots Supabase"""
        try:
            response = self.supabase.from_('transactions').select('txid').eq('txid', txid).execute()
            if response.data and len(response.data) > 0:
                logger.info(f"TXID {txid} jau ir izmantots.")
                return True
            logger.info(f"TXID {txid} nav izmantots.")
            return False
        except Exception as e:
            logger.error(f"Kļūda pārbaudot TXID Supabase: {e}")
            return True # Drošības nolūkos atgriežam True, ja ir kļūda

    async def save_transaction(self, txid: str, user_id: int, amount: float):
        """Saglabā transakciju Supabase"""
        try:
            data = {
                "txid": txid,
                "user_id": user_id,
                "amount": amount,
                "verified_at": datetime.now().isoformat() # Supabase automātiski apstrādās TIMESTAMP WITH TIME ZONE
            }
            # Izmantojam on_conflict='txid' un ignore_duplicates=True, lai nodrošinātu, ka TXID ir unikāls
            response = self.supabase.from_('transactions').insert(data, on_conflict='txid', ignore_duplicates=True).execute()
            if response.data:
                logger.info(f"Transakcija {txid} veiksmīgi saglabāta Supabase.")
            elif response.error:
                logger.error(f"Kļūda saglabājot transakciju {txid} Supabase: {response.error}")
        except Exception as e:
            logger.error(f"Kļūda saglabājot transakciju Supabase: {e}")

    async def save_subscription(self, user, txid: str):
        """Saglabā abonementu Supabase"""
        try:
            start_date = datetime.now()
            end_date = start_date + timedelta(days=SUBSCRIPTION_DAYS)
            
            data = {
                "user_id": user.id,
                "username": user.username or '',
                "first_name": user.first_name or '',
                "txid": txid,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "is_active": True,
                "reminder_sent_12h": False
            }
            # Izmantojam upsert, lai atjauninātu, ja lietotājs jau eksistē, vai ievietotu jaunu
            response = self.supabase.from_('subscriptions').upsert(data, on_conflict='user_id').execute()
            if response.data:
                logger.info(f"Abonements lietotājam {user.id} veiksmīgi saglabāts/atjaunināts Supabase.")
            elif response.error:
                logger.error(f"Kļūda saglabājot abonementu lietotājam {user.id} Supabase: {response.error}")
        except Exception as e:
            logger.error(f"Kļūda saglabājot abonementu Supabase: {e}")

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
        
        try:
            response = self.supabase.from_('subscriptions').select('*').eq('user_id', user.id).eq('is_active', True).execute()
            subscription = response.data[0] if response.data else None
            
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
        except Exception as e:
            logger.error(f"Kļūda iegūstot statusu no Supabase: {e}")
            await update.message.reply_text("❌ Kļūda iegūstot abonementa statusu. Lūdzu, mēģiniet vēlreiz vēlāk.")

    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin komandas"""
        if update.effective_user.id != ADMIN_USER_ID:
            await update.message.reply_text("❌ Nav atļaujas.")
            return
        
        try:
            # Aktīvie abonenti
            response_active = self.supabase.from_('subscriptions').select('count', count='exact').eq('is_active', True).execute()
            active_count = response_active.count if response_active.count is not None else 0
            
            # Kopējie abonenti
            response_total = self.supabase.from_('subscriptions').select('count', count='exact').execute()
            total_count = response_total.count if response_total.count is not None else 0
            
            # Šodienas ieņēmumi
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            response_today_revenue = self.supabase.from_('transactions').select('amount').gte('verified_at', today_start).execute()
            today_revenue = sum(item['amount'] for item in response_today_revenue.data) if response_today_revenue.data else 0
            
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
        except Exception as e:
            logger.error(f"Kļūda iegūstot admin datus no Supabase: {e}")
            await update.message.reply_text("❌ Kļūda iegūstot admin datus. Lūdzu, mēģiniet vēlreiz vēlāk.")

    async def notify_admin(self, message: str):
        """Nosūta ziņojumu adminam"""
        try:
            await self.app.bot.send_message(chat_id=ADMIN_USER_ID, text=message)
        except Exception as e:
            logger.error(f"Error notifying admin: {e}")

    async def send_subscription_reminders(self):
        """Nosūta atgādinājumus par beidzošiem abonementiem (12h pirms)"""
        now = datetime.now()
        twelve_hours_from_now = now + timedelta(hours=12)
        
        try:
            # Atrod aktīvos abonementus, kas beigsies nākamo 12 stundu laikā un kuriem atgādinājums vēl nav nosūtīts
            response = self.supabase.from_('subscriptions').select('user_id, first_name, end_date').eq('is_active', True).eq('reminder_sent_12h', False).gte('end_date', now.isoformat()).lte('end_date', twelve_hours_from_now.isoformat()).execute()
            users_to_remind = response.data
            
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
                    self.supabase.from_('subscriptions').update({'reminder_sent_12h': True}).eq('user_id', user_id).execute()
                    logger.info(f"Nosūtīts 12h atgādinājums lietotājam: {user_id}")
                    
                except Exception as e:
                    logger.error(f"Kļūda sūtot atgādinājumu lietotājam {user_id}: {e}")
        except Exception as e:
            logger.error(f"Kļūda iegūstot atgādinājumu lietotājus no Supabase: {e}")

    async def check_expired_subscriptions(self):
        """Pārbauda beidzošos abonementus"""
        now = datetime.now()
        
        try:
            # Atrod beidzošos abonementus
            response = self.supabase.from_('subscriptions').select('user_id, username, first_name, end_date').eq('is_active', True).lte('end_date', now.isoformat()).execute()
            expired_users = response.data
            
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
                    self.supabase.from_('subscriptions').update({'is_active': False}).eq('user_id', user_id).execute()
                    
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
        except Exception as e:
            logger.error(f"Kļūda pārbaudot beidzošos abonementus no Supabase: {e}")

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
