import json
import os
import time
from deep_translator import GoogleTranslator

TARGET_LANGS = ["ja", "ko", "ar", "es", "fa", "fr", "pt-BR", "ru", "tr"]

# mapping to deep-translator lang codes
LANG_MAP = {
    "ja": "ja", "ko": "ko", "ar": "ar", "es": "es", "fa": "fa", 
    "fr": "fr", "pt-BR": "pt", "ru": "ru", "tr": "tr"
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCALES_DIR = os.path.join(BASE_DIR, "locales")
EN_DIR = os.path.join(LOCALES_DIR, "en")

# Already done manually
ALREADY_DONE = {
    ("ko", "nodeDefs.json")
}

def translate_text(text, target_lang):
    if not text or not isinstance(text, str):
        return text
    if text.strip() == "": return text
    
    for attempt in range(3):
        try:
            translator = GoogleTranslator(source='en', target=LANG_MAP[target_lang])
            return translator.translate(text)
        except Exception as e:
            time.sleep(2)
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
    if (target_lang, filename) in ALREADY_DONE:
        print(f"Skipping {filename} for {target_lang} (already done)")
        return
        
    en_path = os.path.join(EN_DIR, filename)
    target_dir = os.path.join(LOCALES_DIR, target_lang)
    target_path = os.path.join(target_dir, filename)
    
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

if __name__ == "__main__":
    main()
