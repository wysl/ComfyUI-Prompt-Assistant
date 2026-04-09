import json
import os
import urllib.request
import urllib.parse
import time
import ssl
import sys

TARGET_LANGS = ["ja", "ko", "ar", "es", "fa", "fr", "pt-BR", "ru", "tr"]

LANG_MAP = {
    "ja": "ja", "ko": "ko", "ar": "ar", "es": "es", "fa": "fa", 
    "fr": "fr", "pt-BR": "pt", "ru": "ru", "tr": "tr"
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCALES_DIR = os.path.join(BASE_DIR, "locales")
EN_DIR = os.path.join(LOCALES_DIR, "en")

# Bypass SSL
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def translate_text(text, target_lang):
    if not text or not isinstance(text, str):
        return text
    if text.strip() == "": return text
    
    url = 'https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=' + LANG_MAP[target_lang] + '&dt=t&q=' + urllib.parse.quote(text)
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    for attempt in range(3):
        try:
            res = urllib.request.urlopen(req, context=ctx, timeout=10).read().decode('utf-8')
            data = json.loads(res)
            translated = "".join([part[0] for part in data[0] if part[0]])
            return translated
        except Exception as e:
            time.sleep(1)
    return text

def translate_dict(data, target_lang):
    if isinstance(data, dict):
        new_data = {}
        for k, v in data.items():
            if k in ["options"]: 
                new_data[k] = {ok: translate_text(ov, target_lang) if isinstance(ov, str) else ov for ok, ov in v.items()}
            else:
                new_data[k] = translate_dict(v, target_lang)
        return new_data
    elif isinstance(data, list):
        return [translate_dict(x, target_lang) for x in data]
    elif isinstance(data, str):
        return translate_text(data, target_lang)
    else:
        return data

def process_file(filename, target_lang):
    en_path = os.path.join(EN_DIR, filename)
    target_dir = os.path.join(LOCALES_DIR, target_lang)
    target_path = os.path.join(target_dir, filename)
    
    os.makedirs(target_dir, exist_ok=True)
    
    with open(en_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    print(f"Translating {filename} to {target_lang}...")
    translated_data = translate_dict(data, target_lang)
    
    with open(target_path, 'w', encoding='utf-8') as f:
        json.dump(translated_data, f, ensure_ascii=False, indent=4)
    print(f"Done {filename} for {target_lang}")

def main():
    files = ["nodeDefs.json", "ui.json"]
    for lang in TARGET_LANGS:
        for file in files:
            process_file(file, lang)
            time.sleep(0.5)

if __name__ == "__main__":
    main()
