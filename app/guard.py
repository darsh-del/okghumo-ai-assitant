"""Cheap, model-free front door: off-topic, abusive or oversized messages get a polite canned reply and NEVER
reach the Anthropic API (no cost, even for rejections). Pure regex, so it is a coarse filter by design: anything
ambiguous passes through and the SYSTEM prompt's scope rules are the second layer.
"""
import re

MAX_MSG_CHARS = 1000  # latest user message
MAX_HISTORY = 10  # messages kept per request (client sends full history; unbounded = unbounded cost)
MAX_HISTORY_CHARS = 2000  # per message in that history

# words that mean "this is about the vendor's business": always allowed through
DOMAIN = re.compile(
    r"book|sale|revenue|earn|income|profit|agent|collect|outstanding|ledger|payment|paid|balance|due|"
    r"activit|tour|ticket|customer|guest|vendor|report|remind|schedul|cancel|summary|trend|source|online|offline|"
    r"order|commission|refund|total|how many|how much|today|yesterday|week|month|year|okghumo|bucketlistt|"
    r"pending|confirm|₹|\brs\b|\binr\b|\bpax\b|participants?|slot|inbox|account", re.I)

# clearly unrelated asks (only blocks when no DOMAIN word is present)
OFFTOPIC = re.compile(
    r"\b(poem|poetry|story|song|lyrics|joke|riddle|recipe|weather|horoscope|astrology|movie|film|cricket|football|"
    r"ipl|politic\w*|election|prime minister|president|bitcoin|crypto\w*|stocks?|shares?|forex|translate|essay|"
    r"homework|math problem|equation|chatgpt|openai|gemini|llama|medical|doctor|diagnos\w*|symptom\w*|lawyer|"
    r"relationship|girlfriend|boyfriend|dating|python|javascript|java|c\+\+|sql|html|css|code|coding|script|"
    r"program|algorithm|capital of|who (is|was|invented)|meaning of life|write me|tell me about yourself)\b", re.I)

INJECTION = re.compile(
    r"ignore (all |your |the |any )?(previous|prior|above|earlier)|disregard (all |your |the )?(previous|prior|above|instructions)|"
    r"(reveal|show|print|repeat|tell me) (me )?(your|the) (system |initial |hidden )?(prompt|instructions)|"
    r"system prompt|developer mode|jailbreak|\bdan mode\b|you are now", re.I)

GREETING = re.compile(r"(hi+|hello+|hey+|namaste|namaskar|good (morning|afternoon|evening)|hii+|helo)[\s!.]*", re.I)
THANKS = re.compile(r"(thanks?( you)?|thank u|thx|shukriya|dhanyavaad|dhanyavad|great|awesome|perfect|bye|goodbye)[\s!.]*", re.I)

HELLO = ("Hello! 😊 I'm the Okghumo assistant. I can help with your sales, bookings, agent balances and collections, "
         "and set up reminders or reports. What would you like to know?")
WELCOME = "You're most welcome! 😊 Please ask any time you need anything about your Okghumo account."
OFF = ("Thank you for asking! I'm sorry, but I can only help with your Okghumo account: sales, bookings, agent "
       "balances, collections, and reminders or reports. Is there anything about those I can help you with? 😊")
INJ = ("Thank you for your message. I'm sorry, but I'm not able to help with that. I'm happy to help with your "
       "Okghumo sales, bookings, agents or collections though. 😊")
LONG = ("Thank you for your message! It's a little long for me to work with. Could you please send a shorter "
        "question about your Okghumo account? 😊")


def check(messages: list[dict]) -> str | None:
    """Canned polite reply if the LATEST user message should not reach the model, else None (continue)."""
    text = messages[-1]["content"].strip()
    if len(text) > MAX_MSG_CHARS:
        return LONG
    if GREETING.fullmatch(text):
        return HELLO
    if THANKS.fullmatch(text):
        return WELCOME
    if INJECTION.search(text):
        return INJ
    if OFFTOPIC.search(text) and not DOMAIN.search(text):
        return OFF
    return None


def trim(messages: list[dict]) -> list[dict]:
    """Cap what we send upstream: last MAX_HISTORY messages, each cut to MAX_HISTORY_CHARS (latest is already
    <= MAX_MSG_CHARS). History must start with a user turn."""
    out = [{**m, "content": m["content"][:MAX_HISTORY_CHARS]} for m in messages[-MAX_HISTORY:]]
    while out and out[0]["role"] != "user":
        out = out[1:]
    return out
