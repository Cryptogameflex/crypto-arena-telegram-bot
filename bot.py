import os
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
level=logging.DEBUG, # Mainīts uz DEBUG
format='%(asctime)s %(levelname)s %(message)s' # Atjaunināts formāts
)
logger = logging.getLogger(__name__)

# Samazinām Supabase/HTTP žurnālu līmeņus, kā ieteikts attēlā
logging.getLogger("httpx").setLevel(logging.WARNING) # httpx ir httpcore un urllib3 aizstājējs jaunākās versijās
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("supabase").setLevel(logging.WARNING)


class CryptoArenaBot:
    def __init__(self):
        # Pārbaudām, vai visi nepieciešamie vides mainīgie ir iestatīti
        if not all([TELEGRAM_BOT_TOKEN, ADMIN_USER_ID, GROUP_ID, TRONSCAN_API_KEY, WALLET_ADDRESS, SUPABASE_URL, SUPABASE_KEY]):
            logger.error("Trūkst viens vai vairāki nepieciešamie vides mainīgie. Lūdzu, pārbaudiet .env failu vai servera konfigurāciju.")
            raise ValueError("Trūkst vides mainīgie.")

        self.app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        logging.debug(f"🔑 Loaded SUPABASE_URL={SUPABASE_URL!r}")
        logging.debug(f"🔑 Loaded SUPABASE_KEY={SUPABASE_KEY[:8]!r}...") # Maskējam atslēgu drošības nolūkos
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

    async def _process_txid(self, chat_id: int, user: User, txid: str, context: ContextTypes.DEFAULT_TYPE):
        """Galvenā loģika TXID apstrādei, ko izmanto gan handle_txid, gan sendtx_command."""
        print(f"DEBUG: Entered _process_txid function for user {user.id}") # Debug print
        print(f"DEBUG: TXID received: {repr(txid)}") # Debug print with repr
        print(f"DEBUG: Length of TXID received: {len(txid)}") # Debug print length

        is_valid = False
        # Pievienots testa TXID, lai apietu verifikāciju un pārbaudītu Supabase saglabāšanu
        # Šis TXID ir precīzi 64 simbolus garš
        test_txid_value = "TEST_TXID_FOR_SUPABASE_SAVE_0123456789ABCDEF0123456789ABCDEF01234" # 64 simboli
        
        if txid == test_txid_value:
            print("DEBUG: Using test TXID, bypassing TronScan verification.")
            is_valid = await self.save_transaction(txid, user.id, SUBSCRIPTION_PRICE)
            if is_valid:
                print("DEBUG: Test TXID saved to Supabase successfully.")
            else:
                print("DEBUG: Failed to save test TXID to Supabase.")
        else:
            # Pārbauda vai TXID formāts ir pareizs (tikai reāliem TXID)
            if len(txid) != 64:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Nepareizs TXID formāts. TXID jābūt 64 simbolu garam."
                )
                print(f"DEBUG: Invalid TXID format: {repr(txid)}") # Debug print with repr
                return
            
            # Pārbauda vai TXID jau nav izmantots
            if await self.is_txid_used(txid):
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Šis TXID jau ir izmantots. Katrs TXID var tikt izmantots tikai vienu reizi."
                )
                print(f"DEBUG: TXID already used: {txid}") # Debug print
                return
            
            await context.bot.send_message(chat_id=chat_id, text="🔍 Pārbaudu maksājumu... Lūdzu uzgaidi.")
            print(f"DEBUG: Verifying transaction for TXID: {txid}") # Debug print
            # Verificē transakciju ar TronScan API
            is_valid = await self.verify_transaction(txid, user.id)
        
        if is_valid:
            print("DEBUG: Transaction is valid.") # Debug print
            # Pievieno lietotāju grupai
            success = await self.add_user_to_group(user)
            
            if success:
                print("DEBUG: User added to group successfully.") # Debug print
                # Saglabā abonementu datubāzē
                await self.save_subscription(user, txid)
                
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ Maksājums apstiprināts!\n"
                         f"🎉 Tu esi pievienots Premium grupai uz {SUBSCRIPTION_DAYS} dienām.\n"
                         f"📅 Abonements beigsies: {(datetime.now() + timedelta(days=SUBSCRIPTION_DAYS)).strftime('%d.%m.%Y %H:%M')}"
                )
                
                # Paziņo adminam
                await self.notify_admin(f"✅ Jauns dalībnieks: {user.first_name} (@{user.username})\nTXID: {txid}")
            else:
                print("DEBUG: Failed to add user to group.") # Debug print
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Neizdevās pievienot grupai. Lūdzu sazinies ar administratoru."
                )
        else:
            print("DEBUG: Transaction is NOT valid.") # Debug print
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
        print("DEBUG: Exited _process_txid function.") # Debug print

    async def handle_txid(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Apstrādā TXID ziņojumus"""
        print("DEBUG: Entered handle_txid function.") # Debug print
        # Pārbauda vai ziņojums ir privātā sarunā
        if update.message.chat.type != 'private':
            return  # Ignorē ziņojumus grupās

        user = update.effective_user
        
        # Pievienota papildu atkļūdošanas izvade
        print(f"DEBUG: Raw update.message.text: {repr(update.message.text)}")
        print(f"DEBUG: Length of raw update.message.text: {len(update.message.text)}")

        txid = update.message.text.strip()
        
        await self._process_txid(update.effective_chat.id, user, txid, context)
        print("DEBUG: Exited handle_txid function.") # Debug print

    async def sendtx_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Apstrādā /sendtx komandu ar TXID, izmantojot handle_txid loģiku."""
        print("DEBUG: Entered sendtx_command function.") # Debug print
        logging.debug(f"💬 /sendtx triggered by @{update.effective_user.username} ({update.effective_user.id})")
        if not context.args:
            await update.message.reply_text(
                "Lūdzu, ieraksti TXID:\n`/sendtx <TXID>`",
                parse_mode='Markdown'
            )
            print("DEBUG: No TXID provided in /sendtx command.") # Debug print
            return

        raw_txid_arg = context.args[0]
        print(f"DEBUG: Raw TXID from context.args[0]: {repr(raw_txid_arg)}")
        print(f"DEBUG: Length of raw TXID: {len(raw_txid_arg)}")

        txid = raw_txid_arg.strip()
        print(f"DEBUG: TXID after strip() in sendtx_command: {repr(txid)}")
        print(f"DEBUG: Length of TXID after strip() in sendtx_command: {len(txid)}")
        
        # Pārbauda vai ziņojums ir privātā sarunā
        if update.message.chat.type != 'private':
            await update.message.reply_text("Šo komandu var izmantot tikai privātā sarunā ar botu.")
            print("DEBUG: /sendtx used in non-private chat.") # Debug print
            return

        await self._process_txid(update.effective_chat.id, update.effective_user, txid, context)
        print("DEBUG: Exited sendtx_command function.") # Debug print

    async def verify_transaction(self, txid: str, user_id: int) -> bool:
        """Verificē transakciju caur TronScan API"""
        print(f"DEBUG: Entered verify_transaction function for TXID: {txid}") # Debug print
        try:
            url = f"https://apilist.tronscanapi.com/api/transaction-info?hash={txid}"
            headers = {
                "TRON-PRO-API-KEY": TRONSCAN_API_KEY
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    print(f"DEBUG: TronScan API response status: {response.status}") # Debug print
                    if response.status != 200:
                        logger.error(f"TronScan API error: {response.status}")
                        return False
                    
                    data = await response.json()
                    print(f"DEBUG: TronScan API response data: {json.dumps(data, indent=2)}") # Debug print
                    
                    # Pārbauda vai transakcija eksistē
                    if not data or 'trc20TransferInfo' not in data:
                        print("DEBUG: No trc20TransferInfo found in TronScan response.") # Debug print
                        return False
                    
                    transfers = data.get('trc20TransferInfo', [])
                    
                    for transfer in transfers:
                        # Pārbauda vai maksājums ir uz mūsu adresi
                        if (transfer.get('to_address') == WALLET_ADDRESS and 
                            float(transfer.get('amount_str', 0)) / 1000000 >= SUBSCRIPTION_PRICE):
                            
                            print(f"DEBUG: Valid transfer found: {transfer}") # Debug print
                            # Saglabā transakciju
                            # Izsauc atjaunināto save_transaction funkciju
                            return await self.save_transaction(txid, user_id, float(transfer.get('amount_str', 0)) / 1000000)
            
            print("DEBUG: No valid transfer found to WALLET_ADDRESS with sufficient amount.") # Debug print
            return False
            
        except Exception as e:
            logger.error(f"Error verifying transaction: {e}")
            print(f"DEBUG: Exception in verify_transaction: {e}") # Debug print
            return False

    async def is_txid_used(self, txid: str) -> bool:
        """Pārbauda vai TXID jau ir izmantots Supabase datubāzē"""
        print(f"DEBUG: Checking if TXID is used: {txid}") # Debug print
        try:
            response = self.supabase.table("transactions").select("txid").eq("txid", txid).execute()
            print(f"DEBUG: Supabase is_txid_used response data: {response.data}") # Debug print
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Error checking if TXID is used in Supabase: {e}")
            print(f"DEBUG: Exception in is_txid_used: {e}") # Debug print
            return False

    async def save_transaction(self, txid: str, user_id: int, amount: float) -> bool:
        """Saglabā transakciju Supabase datubāzē ar atkļūdošanas izvadi un Būla atgriešanas vērtību."""
        print(f"DEBUG: Entered save_transaction function for TXID: {txid}, User ID: {user_id}, Amount: {amount}") # Debug print
        logging.debug(f"💾 save_transaction() called with user_id={user_id!r}, txid={txid!r}")
        try:
            resp = self.supabase.table("transactions").insert({
                "txid": txid,
                "user_id": user_id,
                "amount": amount,  # Saglabāts no iepriekšējās versijas
                "verified_at": datetime.now().isoformat()  # Saglabāts no iepriekšējās versijas
            }).execute()

            logging.debug(f"🟢 Supabase raw response: {resp!r}")
            print(f"DEBUG: Supabase insert raw response: {resp}") # Debug print
            if hasattr(resp, "data") and resp.data:
                logging.debug(f"✅ resp.data: {resp.data}")
                print(f"DEBUG: Supabase insert successful, data: {resp.data}") # Debug print
                return True
            else:
                logging.error(f"❌ No resp.data attribute, resp attrs: {dir(resp)}")
                print(f"DEBUG: Supabase insert failed or no data. Response attributes: {dir(resp)}") # Debug print
                if resp.error:  # Pievienots, lai apstrādātu Supabase kļūdas objektu
                    logging.error(f"🔺 Supabase error details: {resp.error}")
                    print(f"DEBUG: Supabase error details: {resp.error}") # Debug print
            return False
        except Exception as e:
            logging.exception(f"🔺 Exception when inserting into Supabase: {e}")
            print(f"DEBUG: Exception in save_transaction: {e}") # Debug print
            return False

    async def save_subscription(self, user, txid: str):
        """Saglabā abonementu Supabase datubāzē (vai atjaunina, ja lietotājs jau eksistē)"""
        print(f"DEBUG: Entered save_subscription function for user: {user.id}, TXID: {txid}") # Debug print
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
            
            # Izlabota kļūdu apstrāde, pārbaudot response.data esamību
            if response.data: # Ja response.data nav tukšs, operācija bija veiksmīga
                print(f"DEBUG: Supabase save_subscription successful, data: {response.data}") # Debug print
            else:
                logger.error(f"Error saving subscription to Supabase: No data returned. Error: {response.error if hasattr(response, 'error') else 'N/A'}")
                print(f"DEBUG: Supabase save_subscription error: No data returned. Error: {response.error if hasattr(response, 'error') else 'N/A'}") # Debug print
        except Exception as e:
            logger.error(f"Error saving subscription to Supabase: {e}")
            print(f"DEBUG: Exception in save_subscription: {e}") # Debug print

    async def add_user_to_group(self, user) -> bool:
        """Pievieno lietotāju grupai"""
        print(f"DEBUG: Entered add_user_to_group function for user: {user.id}") # Debug print
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
            print(f"DEBUG: Invite link sent to user {user.id}: {invite_link.invite_link}") # Debug print
            return True
            
        except Exception as e:
            logger.error(f"Error adding user to group: {e}")
            print(f"DEBUG: Exception in add_user_to_group: {e}") # Debug print
            return False

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Parāda lietotāja abonements statusu no Supabase"""
        print(f"DEBUG: Entered status_command for user: {update.effective_user.id}") # Debug print
        user = update.effective_user
        
        try:
            response = self.supabase.table("subscriptions").select("*").eq("user_id", user.id).eq("is_active", True).execute()
            subscription = response.data[0] if response.data else None
            print(f"DEBUG: Supabase status_command response data: {response.data}") # Debug print
        except Exception as e:
            logger.error(f"Error fetching subscription status from Supabase: {e}")
            print(f"DEBUG: Exception in status_command (fetching from Supabase): {e}") # Debug print
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
        print("DEBUG: Status message sent.") # Debug print

    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin komandas no Supabase"""
        print(f"DEBUG: Entered admin_command for user: {update.effective_user.id}") # Debug print
        if update.effective_user.id != ADMIN_USER_ID:
            await update.message.reply_text("❌ Nav atļaujas.")
            print("DEBUG: Unauthorized admin access attempt.") # Debug print
            return
        
        try:
            # Aktīvie abonenti
            active_count_resp = self.supabase.table("subscriptions").select("count", count="exact").eq("is_active", True).execute()
            active_count = active_count_resp.count if active_count_resp.count is not None else 0
            print(f"DEBUG: Active users count: {active_count}") # Debug print
            
            # Kopējie abonenti
            total_count_resp = self.supabase.table("subscriptions").select("count", count="exact").execute()
            total_count = total_count_resp.count if total_count_resp.count is not None else 0
            print(f"DEBUG: Total users count: {total_count}") # Debug print
            
            # Šodienas ieņēmumi
            today = datetime.now().date()
            today_revenue_resp = self.supabase.table("transactions").select("amount").gte("verified_at", today.isoformat()).lt("verified_at", (today + timedelta(days=1)).isoformat()).execute()
            today_revenue = sum(item['amount'] for item in today_revenue_resp.data) if today_revenue_resp.data else 0
            print(f"DEBUG: Today's revenue: {today_revenue}") # Debug print
            
        except Exception as e:
            logger.error(f"Error fetching admin data from Supabase: {e}")
            print(f"DEBUG: Exception in admin_command (fetching from Supabase): {e}") # Debug print
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
        print("DEBUG: Admin panel message sent.") # Debug print

    async def notify_admin(self, message: str):
        """Nosūta ziņojumu adminam"""
        print(f"DEBUG: Notifying admin: {message}") # Debug print
        try:
            await self.app.bot.send_message(chat_id=ADMIN_USER_ID, text=message)
        except Exception as e:
            logger.error(f"Error notifying admin: {e}")
            print(f"DEBUG: Exception in notify_admin: {e}") # Debug print

    async def send_subscription_reminders(self):
        """Nosūta atgādinājumus par beidzošiem abonementiem (12h pirms) no Supabase"""
        print("DEBUG: Running send_subscription_reminders.") # Debug print
        now = datetime.now()
        twelve_hours_from_now = now + timedelta(hours=12)
        
        try:
            response = self.supabase.table("subscriptions").select("user_id, first_name, end_date").eq("is_active", True).gte("end_date", now.isoformat()).lte("end_date", twelve_hours_from_now.isoformat()).eq("reminder_sent_12h", False).execute()
            users_to_remind = response.data
            print(f"DEBUG: Users to remind: {users_to_remind}") # Debug print
        except Exception as e:
            logger.error(f"Error fetching users for reminders from Supabase: {e}")
            print(f"DEBUG: Exception in send_subscription_reminders (fetching from Supabase): {e}") # Debug print
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
                if not update_resp.data: # Pārbauda, vai dati tika atgriezti
                    logger.error(f"Error updating reminder_sent_12h for {user_id}: No data returned. Error: {update_resp.error if hasattr(update_resp, 'error') else 'N/A'}")
                    print(f"DEBUG: Error updating reminder_sent_12h for {user_id}: No data returned. Error: {update_resp.error if hasattr(update_resp, 'error') else 'N/A'}") # Debug print
                logger.info(f"Nosūtīts 12h atgādinājums lietotājam: {user_id}")
                
            except Exception as e:
                logger.error(f"Kļūda sūtot atgādinājumu lietotājam {user_id}: {e}")
                print(f"DEBUG: Exception sending reminder to {user_id}: {e}") # Debug print

    async def check_expired_subscriptions(self):
        """Pārbauda beidzošos abonementus no Supabase"""
        print("DEBUG: Running check_expired_subscriptions.") # Debug print
        now = datetime.now()
        
        try:
            response = self.supabase.table("subscriptions").select("user_id, username, first_name, end_date").eq("is_active", True).lte("end_date", now.isoformat()).execute()
            expired_users = response.data
            print(f"DEBUG: Expired users found: {expired_users}") # Debug print
        except Exception as e:
            logger.error(f"Error fetching expired users from Supabase: {e}")
            print(f"DEBUG: Exception in check_expired_subscriptions (fetching from Supabase): {e}") # Debug print
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
                if not update_resp.data: # Pārbauda, vai dati tika atgriezti
                    logger.error(f"Error deactivating subscription for {user_id}: No data returned. Error: {update_resp.error if hasattr(update_resp, 'error') else 'N/A'}")
                    print(f"DEBUG: Error deactivating subscription for {user_id}: No data returned. Error: {update_resp.error if hasattr(update_resp, 'error') else 'N/A'}") # Debug print
                
                # Paziņo lietotājam
                await self.app.bot.send_message(
                    chat_id=user_id,
                    text="⏰ Tavs Premium abonemets ir beidzies.\n"
                         "Lai turpinātu, izmanto /start lai iegādātos jaunu abonementu."
                )
                
                logger.info(f"Removed expired user: {user_id}")
                print(f"DEBUG: User {user_id} removed and notified.") # Debug print
                
            except Exception as e:
                logger.error(f"Error removing expired user {user_id}: {e}")
                print(f"DEBUG: Exception removing expired user {user_id}: {e}") # Debug print
        
        if expired_users:
            await self.notify_admin(f"🔄 Noņemti {len(expired_users)} lietotāji ar beidzošiem abonementiem.")
            print(f"DEBUG: Admin notified about {len(expired_users)} expired users.") # Debug print

    async def subscription_checker(self):
        """Periodiski pārbauda abonementus un sūta atgādinājumus"""
        print("DEBUG: Starting subscription_checker loop.") # Debug print
        while True:
            try:
                await self.send_subscription_reminders()
                await self.check_expired_subscriptions()
                await asyncio.sleep(3600)  # Pārbauda katru stundu
            except Exception as e:
                logger.error(f"Error in subscription checker: {e}")
                print(f"DEBUG: Exception in subscription_checker loop: {e}") # Debug print
                await asyncio.sleep(300)  # Mēģina atkal pēc 5 minūtēm

    async def run(self):
        """Palaiž botu"""
        # Sāk abonementu pārbaudītāju
        # asyncio.create_task(self.subscription_checker()) # Komentēts ārā, kā ieteikts attēlā
        
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
    logging.info("🚀 Bot starting...") # Pievienots no attēla
    bot = CryptoArenaBot()
    asyncio.run(bot.run())
