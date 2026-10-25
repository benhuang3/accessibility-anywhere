## Verdict: keep with changes

Keep the trusted-person escalation, but describe it as **requesting help**, not placing a connected call. For remote-access warnings, prioritize ending the scammer’s phone call and closing the remote tool; opening Meet leaves remote control active. Keep Meet for a rehearsed Sunday demo, with a normal phone callback as fallback. **Unverified judgment:** this is a compelling intervention for an anxious person, provided the interface makes the next step clear.

*Read-only review; no files changed, Ally not started, tests not run. “Unverified” marks claims beyond repository code, including documented service behavior. All likelihood estimates are unverified.*

## Top 8 risks, ranked

1. **The scammer still controls the rescue screen.** `_tell()` neither closes nor suspends remote access. **Likelihood: high under the stated threat model (unverified).** The attacker could press buttons, close Meet, mute audio, talk over the helper, or impersonate them through the interface (unverified). Smallest fix: in `ally/risk/wire.py::on_risk_warning`, prioritize “Hang up, then close the remote tool”; preserve access to the contact action afterward.

2. **The standing room becomes a persistent target.** Its URL appears on the compromised screen. Reuse could expose future conversations, participant identities and routines (unverified). Knowing the link permits an entry attempt now or later; admission depends on settings (unverified). Smallest fix: use a dedicated, restricted room, admit only recognized identities, and replace exposed links. [Google access settings](https://support.google.com/a/answer/13722774)

3. **Call/Tell can freeze the panel and hide its result.** `ally/overlay/overlay.py::_extra_row` runs networking synchronously; bridge waiting allows eight seconds, followed by ntfy’s four-second timeout. `_render()` caches unchanged panel content, so `_extra_done` may never appear and the button remains actionable. **Likelihood: high for unchanged panels (unverified).** Smallest fix: disable immediately, run work off-thread, then queue a forced panel rebuild with success/failure and retry.

4. **Meet opens a joining workflow, not a call.** Unsigned guests request admission; an absent host can block entry when host-first is enabled (unverified). A standing link does not guarantee an attended room. **Likelihood: medium without rehearsal (unverified).** Smallest fix: `ally/risk/wire.py::_tell` should say “Call page opened; join when ready.” Rehearse signed-in and signed-out Chrome, host absence, and the helper’s actual app/browser route. [Google joining guidance](https://support.google.com/meet/answer/9303069)

5. **The alert overstates safety and delivery.** `contact.py::message_for` asserts “nothing has been paid” without payment evidence. `_tell()` treats HTTP success as helper receipt and says “Calling.” **Likelihood: certain wording exposure when used (unverified).** Smallest fix: say “Ally noticed a possible scam. Please contact them,” and “Alert submitted; waiting for your helper.”

6. **Push delivery or the helper fails.** There is no acknowledgement, escalation or unanswered timer. Android can delay notifications without instant delivery; iOS depends on push infrastructure; high priority does not establish a DND override (unverified). **Likelihood: medium (unverified).** Smallest fix: test the locked phone and notification settings; provide a persistent callback fallback in `_tell()`. [Android delivery/settings](https://docs.ntfy.sh/subscribe/phone/), [iOS delivery](https://docs.ntfy.sh/config/#ios-instant-notifications)

7. **Topic secrecy is the only configured access control.** The sender supplies no authentication; validation permits predictable eight-character topics. Anyone knowing an unprotected topic can subscribe or publish (unverified). **Likelihood: low with strong randomness, higher with shared names (unverified).** Smallest fix: require a generated random topic during setup; later add authenticated publishing. The push also exposes the standing link. [ntfy security](https://docs.ntfy.sh/faq/)

8. **Validation accepts malformed call links.** `contact.py::valid_call_url` checks HTTPS and hostname, but accepts credentials, arbitrary ports and control characters. `ntfy_sender.send` builds `Request` outside its exception handler; `_tell` catches sender errors, but the sender’s “never raises” promise is incomplete. **Likelihood: low with curated configuration (unverified).** Reject those components and move request construction inside `try`. Positive: page text/URL cannot choose the configured link or enter the fixed alert body.

## Must-fix before the demo

- Fix risks 3 and 5; clarify remote-control safety sequencing.
- Correct `docs/RISK_HANDOFF.md`: the overlay delays **No**, not **Yes**; closing works immediately.
- Rehearse venue Wi-Fi, Chrome identity, host admission, microphone permissions, camera-off operation and phone delivery. Permission prompts are a real prerequisite (unverified). [Google permissions](https://support.google.com/meet/answer/7317473)
- Show judges the helper’s physical phone receiving the alert; prepare a clearly labeled recording and ordinary callback.

## Nice to have / alternatives

**Unverified assessment:** avoid a service migration overnight. [Whereby](https://support.whereby.com/en/articles/4515266) still needs its owner; [Zoom](https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0082131) retains host/waiting-room friction; [Jitsi](https://jitsi.org/security/) adds room-creation authentication.

**Unverified assessment:** a phone-side `tel:` callback is the smallest useful alternative; current validation rejects it. [WhatsApp click-to-chat](https://faq.whatsapp.com/5913398998672934) opens a chat, requiring another action. SMS adds sending integration and delivery uncertainty. Twilio offers real ringing but adds account setup and call bridging; trials restrict recipients (unverified). Published US rates are $0.014/outbound minute plus $1.15/month for a local number, before additional legs/features (unverified). [Trial](https://www.twilio.com/docs/usage/trials/try-out-voice), [pricing](https://www.twilio.com/en-us/voice/pricing/us)

**Unverified judge assessment:** trusted contacts and [family SOS alerts](https://investors.life360.com/node/8031/pdf) already exist. Ally’s strongest distinction is connecting the browser/process warning directly to human help; uniqueness is unverified. [Trusted-contact comparison](https://www.finra.org/sites/default/files/2021-09/trusted-contact-infographic.pdf)

## 45-second demo script

**0–15s:** “This is a simulated remote-control scam beside a bank page. Ally flags that combination. Closing the remote tool is available immediately.”

**15–30s:** “This button requests Priya’s help.” Tap; show her phone receiving the alert. “It includes the risk category and meeting link, without the bank address or page contents.”

**30–45s:** Close the simulated remote tool; join the rehearsed room. Priya: “I’m here. Let’s stop and check together.”

**Fallback:** “The meeting hasn’t connected. Ally cannot confirm someone answered; our fallback is a normal phone callback.”