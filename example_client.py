#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import urllib.request

URL = "http://127.0.0.1:8080/translate"


def translate(text, source="auto", target="vi", mode="advanced"):
    payload = {
        "text": text,
        "from": source,
        "to": target,
        "mode": mode,
    }

    req = urllib.request.Request(
        URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


print("ADVANCED:")
print(translate("hello world", "en", "vi", "advanced"))

print()
print("CLASSIC:")
print(translate("hello world", "en", "vi", "classic"))
