import os
import sys
import locale
import asyncio
import logging
import json

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import aiohttp
from telegram import Update, User, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.error import TelegramError
from dotenv import load_dotenv
from supabase import create_client, Client

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

# Ielādējam vides mainīgos no .env faila
load_dotenv()

# Konfigurācija - tagad lasām no vides mainīgajiem
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID")) if os.getenv("ADMIN_USER_ID") else None
GROUP_ID = int(os.getenv("GROUP_ID")) if os.getenv("GROUP_ID") else None
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


class CryptoArenaBot:
  def __init__(self):
      # Pārbaudām, vai visi nepieciešamie vides mainīgie ir iestatīti
      self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
      self.admin_user_id = int(os.getenv("ADMIN_USER_ID")) if os.getenv("ADMIN_USER_ID") else None
      self.group_id = int(os.getenv("GROUP_ID")) if os.getenv("GROUP_ID") else None
      self.tronscan_api_key = os.getenv("TRONSCAN_API_KEY")
      self.wallet_address = os.getenv("WALLET_ADDRESS")
      self.supabase_url = os.getenv("SUPABASE_URL")
      self.supabase_key = os.getenv("SUPABASE_KEY")
      self.bot_username = None # Tiks iestatīts run() funkcijā

      if not all([self.telegram_bot_token, self.admin_user_id is not None, self.group_id is not None, self.tronscan_api_key, self.wallet_address, self.supabase_url, self.supabase_key]):
          logger.error("Trūkst viens vai vairāki nepieciešamie vides mainīgie. Lūdzu, pārbaudiet .env failu vai servera konfigurāciju.")
          raise ValueError("Trūkst vides mainīgie.")

      self.app = Application.builder().token(self.telegram_bot_token).build()
      
      # Pievienots error handling Supabase klientam
      try:
          # Noņemam pielāgoto httpx.Client un ļaujam supabase-py pārvaldīt savu
          self.supabase: Client = create_client(self.supabase_url, self.supabase_key)
          logger.debug(f"🔑 Loaded SUPABASE_URL='{self.supabase_url}'")
          logger.debug(f"🔑 Loaded SUPABASE_KEY='{self.supabase_key[:8]}'...")
          
          # Testējam Supabase savienojumu
          test_response = self.supabase.table("transactions").select("count", count="exact").limit(0).execute()
          logger.info("✅ Supabase savienojums veiksmīgs")
          
      except Exception as e:
          error_message_safe = str(e).encode('ascii', 'replace').decode('ascii')
          logger.error(f"❌ Supabase savienojuma kļūda: {error_message_safe}")
          raise
          
      self.setup_handlers()

  def setup_handlers(self):
      """Uzstāda bot handlerus"""
      self.app.add_handler(CommandHandler("start", self.start_command))
      self.app.add_handler(CommandHandler("status", self.status_command))
      self.app.add_handler(CommandHandler("admin", self.admin_command))
      self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_txid))
      self.app.add_handler(CommandHandler("sendtx", self.sendtx_command))
      # handle_payment_choice vairs netiek izmantots tieši ar callback_data, jo USDT poga tagad izmanto deep link
      # Ja nākotnē būs citas callback_data pogas, šis handleris būs jāatjauno
      # self.app.add_handler(CallbackQueryHandler(self.handle_payment_choice)) 
      
      logger.info("✅ Visi handleri ir reģistrēti")

  async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
      """Sākuma komanda ar maksājuma izvēles iespējām vai tiešu USDT instrukciju sūtīšanu"""
      user = update.effective_user
      
      if context.args and context.args[0] == 'pay_usdt':
          logger.debug("✅ /start with 'pay_usdt' argument detected. Sending USDT instructions directly.")
          await self.send_usdt_instructions(update.message.chat.id, context)
          return

      # Combine the main title and welcome text into a single caption
      full_caption = f"""
🎯 **KRIPTO ARĒNA PREMIUM KLUBA APMAKSA**

Lūdzu, izvēlies apmaksas veidu:
"""
      
      keyboard = InlineKeyboardMarkup([
          [InlineKeyboardButton("Apmaksāt ar USDT", url=f"https://t.me/{self.bot_username}?start=pay_usdt")],
          [InlineKeyboardButton("Apmaksāt ar bankas karti", url="https://t.me/tribute/app?startapp=siSV")]
      ])
      
      # Send the image with the combined caption and the inline keyboard
      await update.message.reply_photo(
          photo="https://hebbkx1anhila5yf.public.blob.vercel-storage.com/55.jpg-3bgmbJskU9V3VVxg5GKvxeaScpkixp.jpeg", # Source URL of the provided image
          caption=full_caption,
          parse_mode='Markdown',
          reply_markup=keyboard # Attach the keyboard directly to the photo message
      )
      logger.debug("✅ /start fired with payment choices!")

  # handle_payment_choice funkcija vairs netiek izmantota, jo USDT poga tagad izmanto deep link
  # Ja nākotnē būs citas callback_data pogas, šī funkcija būs jāatjauno
  async def handle_payment_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
      """Apstrādā maksājuma izvēles pogas (šobrīd netiek izmantots 'pay_usdt' callback)"""
      logger.debug(f"--- Entering handle_payment_choice ---")
      query = update.callback_query
      logger.debug(f"Callback query received from user {query.from_user.id}. Raw data: '{query.data}'")
      
      try:
          await query.answer()
          logger.debug(f"Callback query answered successfully for '{query.data}'")
      except Exception as e:
          error_message_safe = str(e).encode('ascii', 'replace').decode('ascii')
          logger.error(f"Error answering callback query for '{query.data}': {error_message_safe}")

      # Šis bloks vairs netiks izpildīts 'pay_usdt' gadījumā, jo tas tagad tiek apstrādāts start_command
      if query.data == 'pay_usdt':
          logger.debug(f"Matched 'pay_usdt' callback. Proceeding to send instructions for chat ID: {query.message.chat.id}")
          try:
              await self.send_usdt_instructions(query.message.chat.id, context)
              logger.debug("USDT instructions sent successfully.")
          except Exception as e:
              error_message_safe = str(e).encode('ascii', 'replace').decode('ascii')
              logger.error(f"Error sending USDT instructions for chat ID {query.message.chat.id}: {error_message_safe}")
              await context.bot.send_message(
                  chat_id=query.message.chat.id,
                  text="Radās kļūda, sūtot USDT instrukcijas. Lūdzu, mēģiniet vēlreiz vai sazinieties ar atbalstu."
              )
      else:
          logger.warning(f"Unhandled callback data received: '{query.data}'")
      logger.debug(f"--- Exiting handle_payment_choice for '{query.data}' ---")

  async def send_usdt_instructions(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
      """Nosūta detalizētas USDT apmaksas instrukcijas"""
      usdt_instructions_text = f"""
🎯 **KRIPTO ARĒNA PREMIUM KLUBA APMAKSA (USDT)**

Lai pievienotos Premium klubam, tev jāveic maksājums:

💰 **Cena:** {SUBSCRIPTION_PRICE} USDT
⏰ **Periods:** {SUBSCRIPTION_DAYS} dienas
🌐 **Tīkls:** TRC-20 (Tron)

📍 **Maksājuma adrese:**
`{self.wallet_address}`

**Instrukcijas:**
1. Nosūti {SUBSCRIPTION_PRICE} USDT uz augstāk norādīto adresi
2. Pēc maksājuma nosūti man transaction ID (TXID)
3. Es pārbaudīšu maksājumu un pievienošu tevi grupai

⚠️ **Svarīgi:** Maksājums jāveic TRC-20 tīklā!

Nosūti man TXID pēc maksājuma veikšanas. (Sagaidi kamēr visi bloki ir apstiprināti).
"""
      await context.bot.send_message(
          chat_id=chat_id,
          text=usdt_instructions_text,
          parse_mode='Markdown'
      )
      logger.debug("✅ USDT instructions sent.")

  async def is_txid_used(self, txid: str) -> bool:
      """Pārbauda vai TXID jau ir izmantots Supabase datubāzē"""
      logger.debug(f"Checking if TXID is used: {txid}")
      try:
          response = self.supabase.table("transactions").select("txid").eq("txid", txid).execute()
          logger.debug(f"Supabase is_txid_used response data: {response.data}")
          
          if len(response.data) > 0:
              logger.warning(f"TXID {txid} jau ir izmantots")
              return True
          else:
              logger.info(f"TXID {txid} nav izmantots - var turpināt")
              return False
              
      except Exception as e:
          error_message_safe = str(e).encode('ascii', 'replace').decode('ascii')
          logger.error(f"Error checking if TXID is used in Supabase: {error_message_safe}")
          return False

  async def _process_txid(self, chat_id: int, user: User, txid: str, context: ContextTypes.DEFAULT_TYPE):
      """Galvenā loģika TXID apstrādei"""
      logger.debug(f"Entered _process_txid function for user {user.id}")
      logger.debug(f"TXID received: {repr(txid)}")
      logger.debug(f"Length of TXID received: {len(txid)}")

      is_valid = False
      
      # Pārbauda vai TXID formāts ir pareizs
      if len(txid) != 64:
          await context.bot.send_message(
              chat_id=chat_id,
              text="❌ Nepareizs TXID formāts. TXID jābūt 64 simbolu garam."
          )
          logger.debug(f"Invalid TXID format: {repr(txid)}")
          return
      
      if await self.is_txid_used(txid):
          await context.bot.send_message(
              chat_id=chat_id,
              text="❌ Šis TXID jau ir izmantots. Katrs TXID var tikt izmantots tikai vienu reizi."
          )
          logger.debug(f"TXID already used: {txid}")
          return
      
      await context.bot.send_message(chat_id=chat_id, text="🔍 Pārbaudu maksājumu... Lūdzu uzgaidi.")
      logger.debug(f"Verifying transaction for TXID: {txid}")
      is_valid = await self.verify_transaction(txid, user.id)
      
      if is_valid:
          logger.debug("Transaction is valid.")
          success = await self.add_user_to_group(user)
          
          if success:
              logger.debug("User added to group successfully.")
              await self.save_subscription(user, txid)
              
              await context.bot.send_message(
                  chat_id=chat_id,
                  text=f"✅ Maksājums apstiprināts!\n"
                       f"🎉 Tu esi pievienots Premium grupai uz {SUBSCRIPTION_DAYS} dienām.\n"
                       f"📅 Abonements beigsies: {(datetime.now(timezone.utc) + timedelta(days=SUBSCRIPTION_DAYS)).strftime('%d.%m.%Y %H:%M')}"
              )
              
              await self.notify_admin(f"✅ Jauns dalībnieks: {user.first_name} (@{user.username})\nTXID: {txid}")
          else:
              logger.debug("Failed to add user to group.")
              await context.bot.send_message(
                  chat_id=chat_id,
                  text="❌ Neizdevās pievienot grupai. Lūdzu sazinies ar administratoru."
              )
      else:
          logger.debug("Transaction is NOT valid.")
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
      logger.debug("Exited _process_txid function.")

  async def handle_txid(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
      """Apstrādā TXID ziņojumus"""
      logger.debug("Entered handle_txid function.")
      if update.message.chat.type != 'private':
          return

      user = update.effective_user
      logger.debug(f"Raw update.message.text: {repr(update.message.text)}")
      logger.debug(f"Length of raw update.message.text: {len(update.message.text)}")

      txid = update.message.text.strip()
      await self._process_txid(update.effective_chat.id, user, txid, context)
      logger.debug("Exited handle_txid function.")

  async def sendtx_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
      """Apstrādā /sendtx komandu ar TXID"""
      logger.debug("Entered sendtx_command function.")
      logger.debug(f"💬 /sendtx triggered by @{update.effective_user.username} ({update.effective_user.id})")
      
      if not context.args:
          await update.message.reply_text(
              "Lūdzu, ieraksti TXID:\n`/sendtx <TXID>`",
              parse_mode='Markdown'
          )
          logger.debug("No TXID provided in /sendtx command.")
          return

      raw_txid_arg = context.args[0]
      logger.debug(f"Raw TXID from context.args[0]: {repr(raw_txid_arg)}")
      logger.debug(f"Length of raw TXID: {len(raw_txid_arg)}")

      txid = raw_txid_arg.strip()
      logger.debug(f"TXID after strip() in sendtx_command: {repr(txid)}")
      logger.debug(f"Length of TXID after strip() in sendtx_command: {len(txid)}")
      
      if update.message.chat.type != 'private':
          await update.message.reply_text("Šo komandu var izmantot tikai privātā sarunā ar botu.")
          logger.debug("/sendtx used in non-private chat.")
          return

      await self._process_txid(update.effective_chat.id, update.effective_user, txid, context)
      logger.debug("Exited sendtx_command function.")

  async def verify_transaction(self, txid: str, user_id: int) -> bool:
      """Verificē transakciju caur TronScan API"""
      logger.debug(f"Entered verify_transaction function for TXID: {txid}")
      try:
          url = f"https://apilist.tronscanapi.com/api/transaction-info?hash={txid}"
          headers = {
              "TRON-PRO-API-KEY": self.tronscan_api_key
          }
          
          async with aiohttp.ClientSession() as session:
              async with session.get(url, headers=headers) as response:
                  logger.debug(f"TronScan API response status: {response.status}")
                  if response.status != 200:
                      logger.error(f"TronScan API error: {response.status}")
                      return False
                  
                  data = await response.json()
                  logger.debug(f"TronScan API response data: {json.dumps(data, indent=2)}")
                  
                  if not data or 'trc20TransferInfo' not in data:
                      logger.debug("No trc20TransferInfo found in TronScan response.")
                      return False
                  
                  transfers = data.get('trc20TransferInfo', [])
                  
                  for transfer in transfers:
                      if (transfer.get('to_address') == self.wallet_address and 
                          float(transfer.get('amount_str', 0)) / 1000000 >= SUBSCRIPTION_PRICE):
                          
                          logger.debug(f"Valid transfer found: {transfer}")
                          return await self.save_transaction(txid, user_id, float(transfer.get('amount_str', 0)) / 1000000)
          
          logger.debug("No valid transfer found to WALLET_ADDRESS with sufficient amount.")
          return False
          
      except Exception as e:
          error_message_safe = str(e).encode('ascii', 'replace').decode('ascii')
          logger.error(f"Error verifying transaction: {error_message_safe}")
          return False

  async def save_transaction(self, txid: str, user_id: int, amount: float) -> bool:
      """Saglabā transakciju Supabase datubāzē"""
      logger.debug(f"Entered save_transaction function for TXID: {txid}, User ID: {user_id}, Amount: {amount}")
      logger.debug(f"💾 save_transaction() called with user_id={user_id!r}, txid={txid!r}") # Changed to logger.debug
      
      try:
          resp = self.supabase.table("transactions").insert({
              "txid": txid,
              "user_id": str(user_id), # Pārliecināmies, ka user_id tiek saglabāts kā string
              "amount": amount,
              "verified_at": datetime.now(timezone.utc).isoformat() # Labojums: izmanto timezone.utc
          }).execute()

          logger.debug(f"🟢 Supabase raw response: {resp!r}") # Changed to logger.debug
          
          if hasattr(resp, "data") and resp.data:
              logger.debug(f"✅ resp.data: {resp.data}") # Changed to logger.debug
              return True
          else:
              logger.error(f"❌ No resp.data attribute, resp attrs: {dir(resp)}") # Changed to logger.error
              if hasattr(resp, 'error') and resp.error:
                  logger.error(f"🔺 Supabase error details: {resp.error}") # Changed to logger.error
          return False
          
      except Exception as e:
          error_message_safe = str(e).encode('ascii', 'replace').decode('ascii')
          logger.exception(f"🔺 Exception when inserting into Supabase: {error_message_safe}") # Changed to logger.exception
          return False

  async def save_subscription(self, user, txid: str):
      """Saglabā abonementu Supabase datubāzē"""
      logger.debug(f"Entered save_subscription function for user: {user.id}, TXID: {txid}")
      start_date = datetime.now(timezone.utc) # Labojums: izmanto timezone.utc
      end_date = start_date + timedelta(days=SUBSCRIPTION_DAYS)
      
      try:
          response = self.supabase.table("subscriptions").upsert({
              "user_id": str(user.id), # Pārliecināmies, ka user_id tiek saglabāts kā string
              "username": user.username or '',
              "first_name": user.first_name or '',
              "txid": txid,
              "start_date": start_date.isoformat(),
              "end_date": end_date.isoformat(),
              "is_active": True,
              "reminder_sent_12h": False,
              "created_at": datetime.now(timezone.utc).isoformat() # Pievienota created_at kolonna, izmanto timezone.utc
          }).execute()
          
          if response.data:
              logger.debug(f"Supabase save_subscription successful, data: {response.data}")
          else:
              logger.error(f"Error saving subscription to Supabase: No data returned. Error: {response.error if hasattr(response, 'error') else 'N/A'}")
              
      except Exception as e:
          error_message_safe = str(e).encode('ascii', 'replace').decode('ascii')
          logger.error(f"Error saving subscription to Supabase: {error_message_safe}")

  async def add_user_to_group(self, user) -> bool:
      """Pievieno lietotāju grupai"""
      logger.debug(f"Entered add_user_to_group function for user: {user.id}")
      try:
          invite_link = await self.app.bot.create_chat_invite_link(
              chat_id=self.group_id,
              member_limit=1,
              expire_date=datetime.now(timezone.utc) + timedelta(hours=1) # Labojums: izmanto timezone.utc
          )
          
          await self.app.bot.send_message(
              chat_id=user.id,
              text=f"🔗 Tavs personīgais uzaicinājuma links:\n{invite_link.invite_link}\n\n"
                   f"⏰ Links derīgs 1 stundu."
          )
          logger.debug(f"Invite link sent to user {user.id}: {invite_link.invite_link}")
          return True
          
      except Exception as e:
          error_message_safe = str(e).encode('ascii', 'replace').decode('ascii')
          logger.error(f"Error adding user to group: {error_message_safe}")
          return False

  async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
      """Parāda lietotāja abonements statusu"""
      logger.debug(f"Entered status_command for user: {update.effective_user.id}")
      user = update.effective_user
      
      try:
          response = self.supabase.table("subscriptions").select("*").eq("user_id", str(user.id)).eq("is_active", True).execute()
          subscription = response.data[0] if response.data else None
          logger.debug(f"Supabase status_command response data: {response.data}")
      except Exception as e:
          error_message_safe = str(e).encode('ascii', 'replace').decode('ascii')
          logger.error(f"Error fetching subscription status from Supabase: {error_message_safe}")
          subscription = None
      
      if subscription:
          end_date = datetime.fromisoformat(subscription['end_date'])
          days_left = (end_date - datetime.now(timezone.utc)).days # Labojums: izmanto timezone.utc
          
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
      logger.debug("Status message sent.")

  async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
      """Admin komandas"""
      logger.debug(f"Entered admin_command for user: {update.effective_user.id}")
      if update.effective_user.id != self.admin_user_id:
          await update.message.reply_text("❌ Nav atļaujas.")
          logger.debug("Unauthorized admin access attempt.")
          return
      
      try:
          active_count_resp = self.supabase.table("subscriptions").select("count", count="exact").eq("is_active", True).execute()
          active_count = active_count_resp.count if active_count_resp.count is not None else 0
          logger.debug(f"Active users count: {active_count}")
          
          total_count_resp = self.supabase.table("subscriptions").select("count", count="exact").execute()
          total_count = total_count_resp.count if total_count_resp.count is not None else 0
          logger.debug(f"Total users count: {total_count}")
          
          today = datetime.now(timezone.utc).date() # Labojums: izmanto timezone.utc
          today_revenue_resp = self.supabase.table("transactions").select("amount").gte("verified_at", today.isoformat()).lt("verified_at", (today + timedelta(days=1)).isoformat()).execute()
          today_revenue = sum(item['amount'] for item in today_revenue_resp.data) if today_revenue_resp.data else 0
          logger.debug(f"Today's revenue: {today_revenue}")
          
      except Exception as e:
          error_message_safe = str(e).encode('ascii', 'replace').decode('ascii')
          logger.error(f"Error fetching admin data from Supabase: {error_message_safe}")
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
      logger.debug("Admin panel message sent.")

  async def notify_admin(self, message: str):
      """Nosūta ziņojumu adminam"""
      logger.debug(f"Notifying admin: {message}")
      try:
          await self.app.bot.send_message(chat_id=self.admin_user_id, text=message)
      except Exception as e:
          error_message_safe = str(e).encode('ascii', 'replace').decode('ascii')
          logger.error(f"Error notifying admin: {error_message_safe}")

  async def send_subscription_reminders(self):
      """Nosūta atgādinājumus par beidzošiem abonementiem"""
      logger.debug("Running send_subscription_reminders.")
      now = datetime.now(timezone.utc) # Labojums: izmanto timezone.utc
      twelve_hours_from_now = now + timedelta(hours=12)
      
      try:
          response = self.supabase.table("subscriptions").select("user_id, first_name, end_date").eq("is_active", True).gte("end_date", now.isoformat()).lte("end_date", twelve_hours_from_now.isoformat()).eq("reminder_sent_12h", False).execute()
          users_to_remind = response.data
          logger.debug(f"Users to remind: {users_to_remind}")
      except Exception as e:
          error_message_safe = str(e).encode('ascii', 'replace').decode('ascii')
          logger.error(f"Error fetching users for reminders from Supabase: {error_message_safe}")
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
              logger.info(f"Nosūtīts 12h atgādinājums lietotājam: {user_id}")
              
          except Exception as e:
              error_message_safe = str(e).encode('ascii', 'replace').decode('ascii')
              logger.error(f"Kļūda sūtot atgādinājumu lietotājam {user_id}: {error_message_safe}")

  async def check_expired_subscriptions(self):
      """Pārbauda beidzošos abonementus"""
      logger.debug("Running check_expired_subscriptions.")
      now = datetime.now(timezone.utc) # Labojums: izmanto timezone.utc
      
      try:
          response = self.supabase.table("subscriptions").select("user_id, username, first_name, end_date").eq("is_active", True).lte("end_date", now.isoformat()).execute()
          expired_users = response.data
          logger.debug(f"Expired users found: {expired_users}")
      except Exception as e:
          error_message_safe = str(e).encode('ascii', 'replace').decode('ascii')
          logger.error(f"Error fetching expired users from Supabase: {error_message_safe}")
          expired_users = []
      
      for user_data in expired_users:
          user_id = user_data['user_id']
          username = user_data['username']
          first_name = user_data['first_name']
          end_date = user_data['end_date']
          
          try:
              await self.app.bot.ban_chat_member(
                  chat_id=self.group_id,
                  user_id=user_id
              )
              
              await self.app.bot.unban_chat_member(
                  chat_id=self.group_id,
                  user_id=user_id
              )
              
              update_resp = self.supabase.table("subscriptions").update({"is_active": False}).eq("user_id", user_id).execute()
              if not update_resp.data:
                  logger.error(f"Error deactivating subscription for {user_id}: No data returned. Error: {update_resp.error if hasattr(update_resp, 'error') else 'N/A'}")
              
              await self.app.bot.send_message(
                  chat_id=user_id,
                  text="⏰ Tavs Premium abonemets ir beidzies.\n"
                       "Lai turpinātu, izmanto /start lai iegādātos jaunu abonementu."
              )
              
              logger.info(f"Removed expired user: {user_id}")
              
          except Exception as e:
              error_message_safe = str(e).encode('ascii', 'replace').decode('ascii')
              logger.error(f"Error removing expired user {user_id}: {error_message_safe}")
      
      if expired_users:
          await self.notify_admin(f"🔄 Noņemti {len(expired_users)} lietotāji ar beidzošiem abonementiem.")

  async def subscription_checker(self):
      """Periodiski pārbauda abonementus un sūta atgādinājumus"""
      logger.debug("Starting subscription_checker loop.")
      while True:
          try:
              await self.send_subscription_reminders()
              await self.check_expired_subscriptions()
              await asyncio.sleep(3600)  # Pārbauda katru stundu
          except Exception as e:
              error_message_safe = str(e).encode('ascii', 'replace').decode('ascii')
              logger.error(f"Error in subscription checker: {error_message_safe}")
              await asyncio.sleep(300)  # Mēģina atkal pēc 5 minūtēm

  async def run(self):
      """Palaiž botu"""
      # Iegūstam bota lietotājvārdu pirms handleri tiek izsaukti
      bot_info = await self.app.bot.get_me()
      self.bot_username = bot_info.username
      logger.info(f"Bot username: @{self.bot_username}")

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
  logger.info("🚀 Bot starting...") # Changed to logger.info
  bot = CryptoArenaBot()
  asyncio.run(bot.run())
