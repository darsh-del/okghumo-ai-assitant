"""Cheap, model-free front door: off-topic, abusive or oversized messages get a polite canned reply and NEVER
reach the Anthropic API (no cost, even for rejections). Pure regex, so it is a coarse filter by design: anything
ambiguous passes through and the SYSTEM prompt's scope rules are the second layer.
"""
import re

from app import lang

MAX_MSG_CHARS = 1000  # latest user message (typed)
VOICE_MAX_CHARS = 2500  # a 150 s voice note transcribes to ~2000+ characters
MAX_HISTORY = 10  # messages kept per request (client sends full history; unbounded = unbounded cost)
MAX_HISTORY_CHARS = 2500  # per message in that history

# words that mean "this is about the vendor's business": always allowed through
DOMAIN = re.compile(
    r"book|sale|revenue|earn|income|profit|agent|collect|outstanding|ledger|payment|paid|balance|due|"
    r"activit|tour|ticket|customer|guest|vendor|report|remind|schedul|cancel|summary|trend|source|online|offline|"
    r"order|commission|refund|total|how many|how much|today|yesterday|week|month|year|okghumo|bucketlistt|"
    r"pending|confirm|₹|\brs\b|\binr\b|\bpax\b|participants?|slot|inbox|account", re.I)

# clearly unrelated asks (only blocks when no DOMAIN word is present)
OFFTOPIC = re.compile(
    r"\b(poem|poetry|story|song|lyrics|joke|riddle|recipe|weather|horoscope|astrology|movie|film|cricket|football|"
    r"ipl|politic\w*|election|prime minister|president|bitcoin|crypto\w*|stocks?|(share|shares) (price|prices|market)|share market|forex|translate|essay|"
    r"homework|math problem|equation|chatgpt|openai|gemini|llama|medical|doctor|diagnos\w*|symptom\w*|lawyer|"
    r"relationship|girlfriend|boyfriend|dating|python|javascript|java|c\+\+|sql|html|css|code|coding|script|"
    r"program|algorithm|capital of|who (is|was|invented)|meaning of life|write me|tell me about yourself)\b", re.I)

INJECTION = re.compile(
    r"ignore (all |your |the |any )?(previous|prior|above|earlier)|disregard (all |your |the )?(previous|prior|above|instructions)|"
    r"(reveal|show|print|repeat|tell me) (me )?(your|the) (system |initial |hidden )?(prompt|instructions)|"
    r"system prompt|developer mode|jailbreak|\bdan mode\b|you are now", re.I)

GREETING = re.compile(
    r"(hi+|hello+|hey+|helo|namaste|namaskar|ram ram|jai shree krishna|jsk|kem cho|kem chho|good (morning|afternoon|evening))[\s!.]*",
    re.I)
THANKS = re.compile(
    r"(thanks?( you)?|thank u|thx|shukriya|dhanyavaad|dhanyavad|aabhar|great|awesome|perfect|bye|goodbye)[\s!.]*", re.I)


def classify(messages: list[dict], max_chars: int = MAX_MSG_CHARS) -> tuple[str, str] | None:
    """(reason, polite canned reply in the vendor's language) if the LATEST user message must not reach the model,
    else None. reason is for logs: too_long | greeting | thanks | injection | off_topic."""
    text = messages[-1]["content"].strip()
    if len(text) > max_chars:
        return "too_long", lang.reply("too_long", text)
    if GREETING.fullmatch(text):
        return "greeting", lang.reply("hello", text)
    if THANKS.fullmatch(text):
        return "thanks", lang.reply("thanks", text)
    if INJECTION.search(text):
        return "injection", lang.reply("injection", text)
    if OFFTOPIC.search(text) and not DOMAIN.search(text):
        return "off_topic", lang.reply("off_topic", text)
    return None


def check(messages: list[dict]) -> str | None:
    r = classify(messages)
    return r[1] if r else None


def trim(messages: list[dict]) -> list[dict]:
    """Cap what we send upstream: last MAX_HISTORY messages, each cut to MAX_HISTORY_CHARS (latest is already
    <= MAX_MSG_CHARS). History must start with a user turn."""
    out = [{**m, "content": m["content"][:MAX_HISTORY_CHARS]} for m in messages[-MAX_HISTORY:]]
    while out and out[0]["role"] != "user":
        out = out[1:]
    return out
