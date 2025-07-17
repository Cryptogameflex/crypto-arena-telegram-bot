# Kripto Arēnas Premium Klubs - Telegram Bots

Telegram bots Kripto Arēnas Premium Kluba maksājumu apstrādei ar USDT TRC-20 tīklā.

## Funkcionalitāte

- 💰 Automātiska maksājumu verificēšana caur TronScan API
- 👥 Automātiska lietotāju pievienošana Premium grupai
- ⏰ Automātiska abonementu pārbaude katru stundu
- 📊 Admin panelis ar statistiku
- 🇱🇻 Pilnībā latviešu valodā

## Uzstādīšana

1. **Klonē projektu:**
\`\`\`bash
git clone <repository-url>
cd crypto-arena-bot
\`\`\`

2. **Instalē Python 3.13.5 vai jaunāku versiju**

3. **Instalē nepieciešamās bibliotēkas:**
\`\`\`bash
pip install -r requirements.txt
\`\`\`

4. **Uzstādi datubāzi:**
\`\`\`bash
python scripts/setup_database.py
\`\`\`

5. **Palaiž botu:**
\`\`\`bash
python main.py
\`\`\`

Vai izmanto start.sh skriptu:
\`\`\`bash
chmod +x start.sh
./start.sh
\`\`\`

## Konfigurācija

Bots izmanto šādus parametrus (main.py failā):

- `TELEGRAM_BOT_TOKEN` - Telegram bota tokens
- `ADMIN_USER_ID` - Admin lietotāja ID
- `GROUP_ID` - Premium grupas ID
- `TRONSCAN_API_KEY` - TronScan API atslēga
- `WALLET_ADDRESS` - USDT saņemšanas maka adrese
- `SUBSCRIPTION_PRICE` - Abonementa cena (25 USDT)
- `SUBSCRIPTION_DAYS` - Abonementa ilgums (30 dienas)

## Komandas

### Lietotāju komandas:
- `/start` - Sākuma ziņojums ar maksājuma instrukcijām
- `/status` - Abonementa statusa pārbaude

### Admin komandas:
- `/admin` - Admin panelis ar statistiku

## Admin rīki

Izmanto `scripts/admin_tools.py` papildu funkcijām:

\`\`\`bash
# Statistika
python scripts/admin_tools.py stats

# Aktīvo lietotāju saraksts
python scripts/admin_tools.py users

# Manuāli beigt lietotāja abonementu
python scripts/admin_tools.py expire <user_id>
\`\`\`

## Datubāzes struktūra

### subscriptions tabula:
- `user_id` - Telegram lietotāja ID
- `username` - Lietotāja username
- `first_name` - Lietotāja vārds
- `txid` - Transakcijas ID
- `start_date` - Abonementa sākuma datums
- `end_date` - Abonementa beigu datums
- `is_active` - Vai abonements ir aktīvs
- `created_at` - Izveidošanas datums

### transactions tabula:
- `txid` - Transakcijas ID
- `user_id` - Lietotāja ID
- `amount` - Maksājuma summa
- `verified_at` - Verificēšanas datums

## Drošība

- Visi TXID tiek pārbaudīti caur TronScan API
- Katrs TXID var tikt izmantots tikai vienu reizi
- Automātiska abonementu pārbaude katru stundu
- Tikai caur botu pievienotie lietotāji tiek pārvaldīti

## Atbalsts

Ja rodas problēmas, pārbaudi:
1. Python versiju (3.13.5+)
2. Vai visas bibliotēkas ir instalētas
3. Vai TronScan API atslēga ir derīga
4. Vai bota tokens ir pareizs

## Licenze

Šis projekts ir izveidots Kripto Arēnas Premium Kluba vajadzībām.
