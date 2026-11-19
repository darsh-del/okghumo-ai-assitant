# Okghumo Assistant — Tester Guide

Thanks for helping test. The assistant answers questions about **your own** OkGhumo business data
(sales, bookings, collections, agent balances). It can only **read** data. It cannot change anything.

## What you need (you will be sent the first one)
1. **An access token** — a long text from the person running the test. It expires (usually in 24 hours).
2. **Your OkGhumo login token** — for the OkGhumo account whose data you want to ask about.
3. **The link:** http://13.233.170.73  (type the full `http://` at the start)
4. A normal web browser. Nothing to install.

> The page does not use HTTPS yet, so there is no padlock. Only use an OkGhumo account you are allowed
> to test with, and do not share your login token with anyone else.

## Steps
1. Open the link.
2. Click **Settings** (top right).
3. Paste the **access token** into the first box.
4. Paste your **OkGhumo login token** into the second box and click **Connect**.
   You should see "Connected." The second box clears itself; it is not kept in your browser.
5. Click **Settings** again to close it, then type a question (Enter sends, Shift+Enter adds a new line).

The page remembers your access token, so you only repeat this when it expires or you clear your browser data.

## Things to try
- How were my sales this month?
- Who owes me money?
- Show bookings by source (online, agent, offline).
- What were my collections last week, cash vs online?
- Show my latest confirmed bookings.

Under each answer you will see small tags listing which tools the assistant used.

## What to tell us
- Anything that looks wrong: numbers that don't match your dashboard, wrong date ranges, odd answers.
- Questions it could not answer.
- Anything slow (answers can take up to a minute or two) or confusing.
- **The exact red message**, if you see one, plus roughly what time it happened.

## If you see a red message
| Message | What to do |
|---|---|
| Your access token is missing, invalid or expired | Ask for a new access token and paste it in Settings. |
| No OkGhumo account is connected yet | Settings → paste your OkGhumo login token → Connect. |
| OkGhumo sign-in failed | Your login token has probably expired. Get a fresh one and Connect again. |
| This account's role is not allowed | Ask the test organiser to change your access level. |
| Could not reach the server | Check your internet; if it persists, tell the organiser. |
| OkGhumo rejected that token (when pressing Connect) | The login token is wrong or expired, or the account has no business assigned. |

If the page won't load at all: make sure the link starts with `http://` (not `https://`), try a private/
incognito window or another browser, and tell the organiser if it still fails (your network address may
need to be allowed).

## Good to know
- It only shows data for the account you connected.
- It will not show customers' phone numbers, emails or payment images.
- **New chat** clears the conversation (the assistant has no memory between chats).
- Scheduled reminders exist in the backend but have no screen yet; you can ignore them for now.

---

## For the test organiser (delete before sending)
Make a 24-hour access token on the server for each tester (give each a different `sub` name):
```bash
cd ~/okghumo && docker compose exec chat python -c "import os,jwt,time;print(jwt.encode({'sub':'tester-NAME','role':'viewer','exp':int(time.time())+86400},os.environ['JWT_SECRET'],algorithm='HS256'))"
```
- `role`: `viewer` = analytics only, `staff` = analytics + bookings, `admin` = all read tools.
- Send the token privately. Open port 80 in the security group to each tester's IP.
- To cancel everyone's tokens at once, change `JWT_SECRET` in `~/chat.env` and run `docker compose up -d`.
