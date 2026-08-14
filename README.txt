GOOGLE TRANSLATE LOCAL API V2
=============================

BAN CHOT
--------
Upstream:
  https://translate.google.com.vn/_/TranslateWebserverUi/data/batchexecute?rpcids=MkEWBc

Request upstream toi thieu:
  Content-Type: application/x-www-form-urlencoded
  f.req=<payload>

Mode da xac nhan bang HAR:
  advanced = flag 1
  classic  = flag 2

Mac dinh:
  advanced

Khong dung trong duong dich chinh:
  Cookie
  bootstrap GET
  f.sid
  bl
  at
  _reqid
  Chrome
  Scrapling
  browser fingerprint

Bao ve tai:
  - 1 persistent requests.Session
  - concurrency upstream = 1
  - local clients co the den song song, nhung MkEWBc duoc xep hang
  - khong retry loop
  - khong tu doi host/IP
  - 302 -> /sorry, 403, 429 se kich hoat cooldown local

Luu y:
  Day la RPC web noi bo/khong chinh thuc cua Google Translate.
  Google co the thay endpoint, RPC ID, payload hoac response ma khong bao truoc.


CAI DAT
-------
Windows CMD:

  py -m pip install -r requirements.txt


CHAY
----
  START_API.cmd

Mac dinh:
  http://127.0.0.1:8080


HEALTH
------
  http://127.0.0.1:8080/health


POST /translate - ADVANCED MAC DINH
-----------------------------------
curl -X POST "http://127.0.0.1:8080/translate" ^
  -H "Content-Type: application/json" ^
  -d "{\"text\":\"hello world\",\"from\":\"en\",\"to\":\"vi\"}"

Hoac ghi mode ro rang:

curl -X POST "http://127.0.0.1:8080/translate" ^
  -H "Content-Type: application/json" ^
  -d "{\"text\":\"hello world\",\"from\":\"en\",\"to\":\"vi\",\"mode\":\"advanced\"}"


POST /translate - CLASSIC
-------------------------
curl -X POST "http://127.0.0.1:8080/translate" ^
  -H "Content-Type: application/json" ^
  -d "{\"text\":\"hello world\",\"from\":\"en\",\"to\":\"vi\",\"mode\":\"classic\"}"


AUTO DETECT
-----------
curl -X POST "http://127.0.0.1:8080/translate" ^
  -H "Content-Type: application/json" ^
  -d "{\"text\":\"xin chao\",\"from\":\"auto\",\"to\":\"en\",\"mode\":\"advanced\"}"


GET TEST NHANH
--------------
Advanced:
  http://127.0.0.1:8080/translate?text=hello%20world&from=en&to=vi&mode=advanced

Classic:
  http://127.0.0.1:8080/translate?text=hello%20world&from=en&to=vi&mode=classic


RESPONSE THANH CONG
-------------------
{
  "ok": true,
  "translation": "xin chào thế giới",
  "source": "en",
  "target": "vi",
  "detected_source": "en",
  "mode": "advanced",
  "upstream_ms": 1234.56,
  "total_ms": 1235.10
}


LOI ANTI-ABUSE / RATE LIMIT
---------------------------
API KHONG co bypass.

Neu Google tra 302 -> /sorry, 403 hoac 429:
- khong follow /sorry
- khong retry lien tuc
- khong doi sang host khac
- bat cooldown local
- client nhan Retry-After

Vi du:
{
  "ok": false,
  "error": "upstream_rate_limited",
  "upstream_status": 429,
  "retry_after_seconds": 60
}

Cooldown mac dinh:
  60 giay

Doi:
  py google_translate_api.py --cooldown 120


MO CHO LAN
----------
  py google_translate_api.py --host 0.0.0.0 --port 8080

Nen bat API key:
  py google_translate_api.py --host 0.0.0.0 --port 8080 --api-key "YOUR_SECRET"

Client gui:
  X-API-Key: YOUR_SECRET


FILE
----
google_translate_api.py
  Server + adapter MkEWBc.

START_API.cmd
  Cai requests neu thieu va khoi dong 127.0.0.1:8080.

TEST_API.cmd
  Test health, Advanced, Classic.

example_client.py
  Vi du Python.

requirements.txt
  Dependency.

README.txt
  Tai lieu nay.
