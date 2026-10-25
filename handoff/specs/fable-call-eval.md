# Read-only review: the "Call a trusted person" feature (Google Meet + ntfy)

You are reviewing, not building. Do not edit any file and do not run Ally (`run.py`). Never read `.env` or `config.toml`.

## Background
- **Ally** is a Windows accessibility assistant for older adults, built for the Cal Hacks hackathon. A live demo for the judges is on Sunday morning.
- **The scam-protection feature**, which is ours, warns when a page looks like a scam.
- **The case that matters most:** a remote-control tool (AnyDesk, Quick Assist and so on) is running while a bank or card page is open.
  - On high warnings, the panel shows a third button, "Call <name>".
  - Pressing it opens the helper's *standing* Google Meet link in Chrome on the older person's PC.
  - It also sends an ntfy.sh push to the helper's phone, with a "Join the call" button.
  - The push text never contains the address or anything from the page.
  - Without a Meet link, the button is "Tell <name>" and only sends the push.

Read: `ally/risk/contact.py`, `ally/risk/wire.py` (`on_risk_warning`, `_tell`, `_make_contact`, `PAUSE_SECONDS`), `ally/overlay/overlay.py`
(the extra-action button and the pause), `tests/test_risk_contact.py`, `docs/RISK_HANDOFF.md`, and the `[risk]` section of `ally/default_config.toml`.

## Evaluate it from every angle, and be concrete
1. **The person.** An anxious older adult, probably still on the phone with the scammer. Is "Call Priya" the right action at that moment?
   Is the wording, timing and placement right? What happens when the Meet tab opens on their screen?
2. **The scammer.** Assume they are watching and controlling the screen through the remote tool:
   - They see the Meet link. Can they join the standing room later, or right now?
   - Can they press the buttons, close the call tab, talk over the helper, or pose as the helper?
   - What does a standing room leak over time?
3. **Google Meet itself:**
   - Does a guest who isn't signed in get "Ask to join" and wait for the host?
   - What happens to a standing room with no host present?
   - Camera and microphone permission prompts; is Chrome signed in or not; mobile app or browser on the helper's side.
   - Is another service in `CALL_HOSTS` better suited (Jitsi, Whereby, Zoom)?
4. **ntfy:**
   - Is it reliable on iOS and Android? Delivery delay, Do Not Disturb, topic privacy, and what happens when the helper doesn't answer.
5. **Security and privacy in the code:**
   - URL validation, what the push contains, and whether a page can influence the call link or the message.
   - Failure handling and anything that can raise.
6. **Live-demo risk on stage:**
   - Venue wifi, the permission prompts, what the judges see.
   - Is it believable that it calls a real helper? A fallback if something fails.
7. **Novelty and judge impact** compared with what exists: bank "trusted contact" features, Life360-style alerts, family-safety apps.
8. **Alternatives worth switching to before Sunday** (a real phone call with Twilio, a WhatsApp click-to-chat link, a tel: link shown on the
   helper's phone, an SMS), weighed honestly against the time left and the cost.

## Output (markdown, under 900 words)
- **Verdict:** keep as is, keep with changes, or replace. One paragraph.
- **The top 8 risks, ranked.** For each: what goes wrong, how likely it is, and the smallest fix (name the file and function where it is code).
- **Must-fix before the demo**, kept separate from **nice to have**.
- **A 45-second demo script** for this beat, plus the fallback line if the call doesn't connect.
- Mark every claim you couldn't verify from the code as "unverified".
