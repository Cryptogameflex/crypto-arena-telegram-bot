# test_supabase.py

import os
from dotenv import load_dotenv
from supabase import create_client

# 1) Ielādē mainīgos no .env
load_dotenv()

# 2) Inicializē Supabase klientu
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def test_insert():
    user = "local_test_user"
    txid = "TEST_TXID_123"

    try:
        # 3) Ievieto ierakstu
        resp = (
            supabase
            .table("transactions")
            .insert({"user_id": user, "txid": txid})
            .execute()
        )

        # 4) Izvada visu atbildi
        print("RAW RESPONSE:", resp)

        # 5) Ja ir .data atribūts, parāda to
        if hasattr(resp, "data"):
            print("✅ INSERTED:", resp.data)
        else:
            # Ja nav data, tad izvada resp pazīmes, lai redzētu, kas iekšā
            print("⚠️ Nav resp.data atribūta, resp pazīmes:", dir(resp))

    except Exception as e:
        print("❌ INSERT ERROR:", e)

if __name__ == "__main__":
    test_insert()
