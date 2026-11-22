# WhatsApp scheduled reports: go-live after Meta approves the template

## Why this step exists
WhatsApp lets us send free text only within 24 hours of the vendor's last message. A report that fires at
8 am to a vendor who has not written for a day needs a Meta-approved template. Without it the report is
saved and delivered the next time the vendor sends any message.

Everything else (propose, YES, cancel, `/logout`, reports inside the 24h window) already works live.

## Current state
- Template: `report_ready`, Utility, English, body starts "Your {{report_type}} ..." (named variable). Status: In review.
- Code live on the server: commit `dc2cd8d` on `dev` (scheduling over WhatsApp, named-variable support, /logout).
- Server: `ec2-user@13.233.170.73`, code in `~/okghumo` (now a git clone tracking `origin/dev` over HTTPS; the repo is public, no key needed), secrets in `~/chat.env` (outside the repo, never committed).

## Step 1: confirm approval
WhatsApp Manager > Message templates: `report_ready` must show **Active**. If it is Rejected or was re-labelled
Marketing, edit the wording to something plain and factual (for example "Your {{report_type}} is ready. Reply with
any message to read it.") and resubmit. Do nothing on the server until it is Active.

## Step 2: add three lines to the server's chat.env
Use the exact template name, the language code shown in the Manager, and the variable name from the body.
```bash
ssh -i ~/Downloads/okghumo-chatbot.pem ec2-user@13.233.170.73
cd ~
printf '%s\n' 'WHATSAPP_REPORT_TEMPLATE=report_ready' 'WHATSAPP_TEMPLATE_LANG=en' 'WHATSAPP_TEMPLATE_PARAM=report_type' >> chat.env
tail -4 chat.env    # check the three lines; do NOT paste the file anywhere, it holds secrets
```
If the template used `{{1}}` instead of a name, leave out `WHATSAPP_TEMPLATE_PARAM`.

## Step 3: restart so the containers read the new values
```bash
cd ~/okghumo && docker compose up -d
docker compose ps        # chat, scheduler, caddy should be Up
curl -s localhost:8000/health   # {"ok":true}
```

## Step 4: test end to end
1. From a linked vendor phone send: "send me a daily report at 8 am", then reply YES.
2. Wait 24+ hours without messaging the bot (or schedule it and test the next day), or ask Meta's test tools.
   At the scheduled time you should get the template text, then the full report after any reply.
3. Logs: `docker logs --since 10m scheduler` and `docker logs --since 10m chat`. Look for `template send failed`
   (wrong name, language or variable) or `scheduled delivery failed`.

Common failures: template name or language code typo; variable name mismatch (named vs `{{1}}`); template not Active yet.

## Deploying future code changes (git on the server)
The server folder is a git clone of https://github.com/darsh-del/okghumo-ai-assitant (branch `dev`).

On your Mac: run tests, commit, push.
```bash
.venv/bin/python -m pytest -q
git push origin dev
```
On the server:
```bash
ssh -i ~/Downloads/okghumo-chatbot.pem ec2-user@13.233.170.73
cd ~/okghumo
git pull --ff-only origin dev
docker compose up -d --build
docker compose ps && curl -s localhost:8000/health
docker logs --since 2m chat 2>&1 | grep -iE "error|traceback"      # expect nothing
docker logs --since 2m scheduler 2>&1 | grep -iE "error|traceback" # expect nothing
```
`--ff-only` refuses to merge if someone edited files on the server, so it never silently overwrites anything.
Never edit files on the server by hand; change the repo and pull.

## Rollback
```bash
cd ~/okghumo && git log --oneline -5      # pick the last good commit
git checkout <commit> && docker compose up -d --build
# afterwards return to normal: git checkout dev
```
The database (volume `okghumo_appdata`) is not touched by code deploys. New columns are added automatically at start-up.

## Kill switches (edit ~/chat.env, then `cd ~/okghumo && docker compose up -d`)
- `SCHEDULING_VIA_CHAT=0` stops proposing and confirming new schedules (chat and WhatsApp).
- `SCHEDULER_ENABLED=0` stops all scheduled reminders and reports from firing.
- Remove `WHATSAPP_REPORT_TEMPLATE` to stop template sends (reports then wait for the vendor's next message).

## Costs and limits to know
- Meta bills per delivered template message; check the rate card in WhatsApp Manager.
- Each scheduled report is one model call with the read-only Okghumo tools (use `REPORT_MODEL` for a cheaper model).
- Limits per vendor: 10 schedules, 5 reports, a report at most once a day.

## Automatic deploys (GitHub Actions)
Every push to `dev` runs the tests, then SSHes to the server, runs `git merge --ff-only origin/dev`, rebuilds and
checks `/health`. One-time setup (repo owner, on GitHub):
1. Actions tab > enable workflows for the repo.
2. Settings > Secrets and variables > Actions > New repository secret:
   - `EC2_HOST` = `13.233.170.73`
   - `EC2_SSH_KEY` = the full contents of the `.pem` key file (paste it in the secret box only, never in chat or code)
3. The server's firewall (EC2 security group) must allow SSH (port 22) from GitHub's runners. They use changing
   IPs, so either open port 22 to the world with key-only login, or use a self-hosted runner / AWS SSM instead.
4. Run it once: Actions > CI/CD > Re-run, then confirm `deployed: healthy` in the log.
If the deploy fails with "not possible to fast-forward", someone edited files on the server: run `git status` there,
undo the hand edit, and re-run.
