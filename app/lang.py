"""Tiny language sniffing so canned replies can match the vendor: English, Hindi or Gujarati, ALWAYS in Roman
letters (Hinglish-style), never Devanagari/Gujarati script. Heuristic on purpose: a wrong guess only means one
canned line in the other language; the model itself follows the LANGUAGE rule in the system prompt.
"""
import re

_HI = set("kya hai hain mera meri mere kitna kitni kitne aaj kal batao bata dikhao dikha chahiye nahi nahin mujhe "
          "aap tum kaise kyun kyu hua hue abhi wala wali paisa kaha kahan kab kaun sabse pichle pichla bhai "
          "sir ji haan accha achha theek thik karo kar do dena dijiye bataiye".split())
_GU = set("chhe chho che shu su kem kemcho mane tamaru tamara tamne tame maru mari mara ketla ketli ketlu joiye "
          "nathi aaje kaale batavo aapo saru majama kya kyare kon pachhla pachhli".split())
_GU_STRONG = {"chhe", "chho", "shu", "kem", "mane", "tamaru", "tamara", "tamne", "ketla", "ketli", "ketlu", "joiye",
              "nathi", "aaje", "kaale", "batavo", "aapo", "majama", "kemcho"}
_HI_STRONG = {"kya", "hai", "hain", "mera", "meri", "mere", "kitna", "kitni", "kitne", "batao", "bata", "dikhao",
              "chahiye", "nahi", "mujhe", "kaise", "abhi", "paisa", "bataiye", "dijiye"}


def detect(text: str) -> str:
    """'en' | 'hi' | 'gu'"""
    if re.search("[઀-૿]", text):
        return "gu"
    if re.search("[ऀ-ॿ]", text):
        return "hi"
    words = re.findall(r"[a-z]+", text.lower())
    gu = sum(w in _GU_STRONG for w in words)
    hi = sum(w in _HI_STRONG for w in words)
    if gu and gu >= hi:
        return "gu"
    return "hi" if hi else "en"


# Canned replies (Roman script). At most one emoji, and only on the friendliest ones.
REPLIES = {
    "hello": {
        "en": "Hello! 😊 I'm the Okghumo assistant. I can help with your sales, bookings, agent balances and "
              "collections, and set up reminders or reports. What would you like to know?",
        "hi": "Namaste! 😊 Main Okghumo assistant hoon. Aapki sales, bookings, agent balance aur collections mein "
              "madad kar sakta hoon, aur reminder ya report bhi set kar sakta hoon. Bataiye, kya jaanna hai?",
        "gu": "Kem cho! 😊 Hu Okghumo assistant chhu. Tamari sales, bookings, agent balance ane collections ma "
              "madad kari shaku chhu, ane reminder ke report pan set kari shaku chhu. Batavo, shu jaanvu chhe?",
    },
    "thanks": {
        "en": "You're most welcome! 😊 Ask me any time you need anything about your Okghumo account.",
        "hi": "Aapka bahut shukriya! 😊 Okghumo account ke baare mein kabhi bhi poochh sakte hain.",
        "gu": "Tamaro khub aabhar! 😊 Okghumo account vishe kyarey pan puchhi shako chho.",
    },
    "off_topic": {
        "en": "Thank you for asking! I'm sorry, but I can only help with your Okghumo account: sales, bookings, "
              "agent balances, collections, and reminders or reports. Is there anything about those I can help with?",
        "hi": "Poochhne ke liye shukriya! Maaf kijiye, main sirf aapke Okghumo account mein madad kar sakta hoon: "
              "sales, bookings, agent balance, collections, aur reminder ya report. Inmein se kuch poochhna hai?",
        "gu": "Puchhva badal aabhar! Maaf karjo, hu fakt tamara Okghumo account ma madad kari shaku chhu: sales, "
              "bookings, agent balance, collections, ane reminder ke report. Ema thi kain puchhvu chhe?",
    },
    "injection": {
        "en": "Thank you for your message. I'm sorry, but I'm not able to help with that. I'm happy to help with "
              "your Okghumo sales, bookings, agents or collections though.",
        "hi": "Aapke message ke liye shukriya. Maaf kijiye, main isme madad nahi kar sakta. Aapki Okghumo sales, "
              "bookings, agents ya collections mein madad karke mujhe khushi hogi.",
        "gu": "Tamara message badal aabhar. Maaf karjo, hu ema madad nahi kari shaku. Tamari Okghumo sales, "
              "bookings, agents ke collections ma madad karvama mane khushi thase.",
    },
    "rate_limited": {
        "en": "You're sending messages a bit fast. Please wait a minute and try again.",
        "hi": "Aap thoda tezi se message bhej rahe hain. Kripya ek minute ruk kar phir koshish kijiye.",
        "gu": "Tame thodi jhadpe message moki rahya chho. Krupa kari ek minute rahi ne fari prayas karo.",
    },
    "too_long": {
        "en": "Thank you for your message! It's a little long for me to work with. Could you please send a "
              "shorter question about your Okghumo account?",
        "hi": "Message ke liye shukriya! Yeh mere liye thoda lamba hai. Kya aap Okghumo account ke baare mein "
              "chhota sawaal bhej sakte hain?",
        "gu": "Message badal aabhar! Aa mara mate thodu lambu chhe. Shu tame Okghumo account vishe nano prashn "
              "moki shako?",
    },
}


def reply(kind: str, text: str) -> str:
    return REPLIES[kind][detect(text)]
