import json
def S(i, text, exp, note, url="https://alerts-help.example/page", overlay="", fs=False):
    return {"id": f"s{i:02d}", "text": text, "overlay_text": overlay, "fullscreen": fs, "url": url, "expect_min": exp, "note": note}
def B(i, text, exp, note, url, overlay="", fs=False):
    return {"id": f"b{i:02d}", "text": text, "overlay_text": overlay, "fullscreen": fs, "url": url, "expect_max": exp, "note": note}
H, M = "high", "medium"
scam = [
 S(1, "WARNING: Microsoft Windows has detected viruses on your computer. Do not close this window. Call support now on 1-800-555-0101 to remove the infection.", H, "classic fake Windows virus popup with phone"),
 S(2, "Your computer has been locked. Windows Defender found a trojan and your passwords may be stolen. Call 1-800-555-0102 immediately and speak to a certified technician.", H, "locked + Defender brand + phone"),
 S(3, "*** SECURITY ALERT *** Apple has blocked this device because malware was found. Do NOT restart or shut down. Contact support at 1-888-555-0103", H, "fake Apple block page", url="https://apple-care-help.example/block"),
 S(4, "Norton Security Warning!! Your PC is infected with 5 viruses. Contact technician: 1 (877) 555-0104. Subscription renewal $349.99 payable by gift cards only.", H, "Norton branding, gift card payment, toll-free"),
 S(5, "mcafee protection expired - your system is hacked and personal files at risk. call support now 1-855-555-0105 to renew. Do not turn off your computer.", H, "lowercase McAfee, hacked, do not turn off"),
 S(6, "Error 0x80070424. Your Windows registration is blocked due to suspicious activity. Please call 1-800-555-0106. Our technician will need remote access to your PC using a small tool.", H, "fake error code with remote access ask"),
 S(7, "Virus detected! Do not close this page. Call now: 1-833-555-0107", H, "no brand but fullscreen", fs=True),
 S(8, "Please wait while we check your device...", H, "alarm text and phone only in overlay", overlay="Trojan found! Your files are locked. Call support now 1-844-555-0108 before data is lost.", url="https://pc-cleaner.example/scan"),
 S(9, "Microsoft Support: Your account has been hacked. Pay the unlock fee with Bitcoin at the ATM and call 1-866-555-0109 for the wallet address. Do not close this window.", H, "crypto payment demand with Microsoft brand"),
 S(10, "Google Chrome security alert: malware has infected your browser. Call support now 1-800-555-0110. Do not close this tab or your data will be lost.", H, "fake Chrome alert"),
 S(11, "REFUND NOTICE: We refunded you too much by mistake, please call 1-877-555-0111 right away. Your Windows license is locked until the extra money is returned via gift cards. Do not hang up.", H, "refund scam with locked and brand"),
 S(12, "Attention Windows user. Your IP address has been hacked and blocked. Call Microsoft certified experts: 1.888.555.0112. Do not shut down your PC.", H, "dotted phone format, shouting tone"),
 S(13, "Suspicious sign-in blocked.", H, "short banner; details only in fullscreen overlay", overlay="Windows security alert: virus detected. Call 1-800-555-0113 now. Do not close.", fs=True, url="https://secure-pc-fix.example/"),
 S(14, "Your Apple ID was used on an unknown device and has been locked. Malware may have spread. Call 1-855-555-0114 now to speak to an advisor who will guide you through remote access.", H, "Apple ID locked + remote access"),
 S(15, "SYSTEM FAILURE. viruses found. DO NOT RESTART. Dial 1-866-555-0115 for free removal.", H, "no brand, phone, fullscreen", fs=True, url="https://system-fail.example/"),
 S(16, "Microsoft virus alert. Call now to fix your computer.", M, "brand + alarm + action but no phone"),
 S(17, "Your computer may be infected. Contact support for help. Gift cards accepted for the service fee.", M, "no brand, no phone"),
 S(18, "Norton subscription ended. Your PC is hacked. Contact technician today.", M, "brand + alarm + action, no phone"),
 S(19, "Notice: unusual activity. Your account is locked. Call support now 1-800-555-0119.", M, "no brand, phone, locked and call support (bank-like)"),
 S(20, "Windows has found malware. Do not close this window.", M, "brand and alarm only"),
 S(21, "Bank security: your debit card is blocked. Please confirm by calling 1-888-555-0120 and have your login ready; we may ask you to install a support tool.", M, "fake bank follow-up, no tech brand"),
 S(22, "Apple Support refund desk. Virus on your Mac. Call support today.", M, "terse, no phone"),
 S(23, "mcafee license expired!!! virus risk. renew with crypto or wire transfer", M, "brand + alarm + payment, no phone"),
 S(24, "Your device is locked. Pay with Bitcoin to restore access.", M, "ransom style, no brand, no phone", fs=True),
 S(25, "Please wait...", M, "alarm in overlay only", overlay="Google security alert: your computer is infected with a virus. Contact support.", url="https://browser-guard.example/"),
]
ben = [
 B(1, "Classic tomato soup: simmer onions and garlic, add canned tomatoes and stock, blend and serve with warm bread. Serves four.", "none", "recipe", "https://kitchen-notes.example/soup"),
 B(2, "Apple pie from scratch. Peel six apples, toss with cinnamon and sugar, fill the crust and bake until golden. Call it dessert.", "none", "recipe mentions apple", "https://kitchen-notes.example/pie"),
 B(3, "Scammers pretending to be Microsoft are calling older adults and claiming their computers have viruses. Police say never allow a stranger remote access and never pay with gift cards.", "medium", "news about scams on unknown host", "https://daily-news.example/scam-warning"),
 B(4, "Tech support scams: how to spot them. Real companies do not send pop-up warnings with a phone number. If you see a fake virus alert, do not call the number. Close the browser and run a scan.", "none", "FTC advice page", "https://consumer.ftc.gov/articles/how-spot-avoid-tech-support-scams"),
 B(5, "Report fraud. Scammers say your computer is locked or hacked and ask you to pay with gift cards, bitcoin or a wire transfer. Stop and tell someone you trust.", "none", "FTC advice page", "https://www.ftc.gov/scams"),
 B(6, "Protect yourself from tech support scams. Microsoft will never send unsolicited pop-ups asking you to call a number about viruses or malware. Windows Defender runs quietly and does not ask for payment.", "none", "Microsoft support advice", "https://support.microsoft.com/en-us/windows/protect-yourself-from-tech-support-scams"),
 B(7, "Consumer alert: do not call numbers in pop-ups claiming a virus. Contact support only through the official site. Hacked accounts should be reported.", "none", "FTC consumer advice", "https://consumer.ftc.gov/consumer-alerts/tech-support"),
 B(8, "Card help. If your card is blocked or your online access is locked, call the number on the back of your card, for example 1-800-555-0131, weekdays 8 to 8.", "medium", "bank help page on unknown host", "https://example-bank.example/help/locked-card"),
 B(9, "Package delayed? If your delivery is blocked at the depot call our helpline 1-877-555-0132 with your tracking code. Never share your password.", "medium", "shipping help with phone and blocked", "https://parcel-co.example/help"),
 B(10, "Release notes 4.2: added dark mode on Windows, fixed a crash on Apple silicon, improved startup time.", "none", "software release notes", "https://notes-app.example/changelog"),
 B(11, "Version 9.1 supports Windows 11 and macOS. Apple users should update to the latest system before installing.", "none", "release notes with brands", "https://editor-tool.example/release"),
 B(12, "Bitcoin rose 3 percent today as crypto markets rallied. Analysts expect volatility ahead of the central bank meeting.", "none", "crypto news", "https://finance-daily.example/markets"),
 B(13, "A wire transfer limit has been raised by regional lenders, and cryptocurrency exchanges report higher volumes this quarter.", "none", "finance news", "https://finance-daily.example/banking"),
 B(14, "Buy gift cards for any occasion. Choose a design, pick an amount and send by email. Gift cards never expire.", "none", "gift card shop", "https://gifts-shop.example/cards"),
 B(15, "Ten tips to speed up an old laptop: remove unused programs, check startup items, add memory and keep Windows updated.", "none", "tech blog", "https://tech-tips.example/speed-up"),
 B(16, "Maple Street Plumbing. Family run since 1998. Call us on 1-800-555-0136 or 555-0137 for a free estimate.", "none", "small business with phone", "https://maple-plumbing.example/"),
 B(17, "Order 48213 has shipped. Track it with number 4821390017 on our site. Expected delivery Thursday.", "none", "order notice with long number", "https://shop-orders.example/track"),
 B(18, "Your tracking update: parcel 1Z9990123456 is out for delivery today. No action needed.", "none", "tracking notice", "https://parcel-co.example/track"),
 B(19, "Locked out of your account? Use the reset link we email you. We will never call and ask for your password.", "none", "account help, locked word only", "https://photo-share.example/help"),
 B(20, "The security conference covered malware trends, phishing and how hacked devices spread. Microsoft and Google researchers presented.", "none", "conference news with brands and alarm words", "https://sec-news.example/recap"),
 B(21, "Virus season is here: flu shots available at the clinic. Call 1-800-555-0141 to book. Appointments blocked out on Sundays.", "medium", "health clinic with 'virus' and 'blocked'", "https://town-clinic.example/flu"),
 B(22, "How to avoid fake virus warnings. If a page says Do not close this window, close it anyway using Task Manager. Real Windows alerts never list a phone number.", "none", "safety advice on unknown host", "https://safe-surf.example/guide"),
 B(23, "Garden diary: the apple trees need pruning in winter. The old windows of the greenhouse were replaced last spring.", "none", "gardening with brand words", "https://garden-diary.example/"),
 B(24, "Mobile plan help. If your SIM is blocked after three wrong PIN entries, call 1-888-555-0144 to unlock it. Hours 9 to 5.", "medium", "telecom help, blocked + phone", "https://mobile-net.example/support"),
 B(25, "FBI warning: criminals impersonating tech support are targeting seniors. Report to ic3.gov. Never send bitcoin or gift cards to someone who contacts you.", "none", "FBI advice page", "https://www.fbi.gov/scams-and-safety"),
]
look = [("paypa1.com","digit 1 for l"),("micr0soft.com","zero for o"),("secure-chase.net","brand with keyword, wrong TLD"),
 ("paypal.com.secure-login.xyz","brand as subdomain of other domain"),("wellsfargoo.com","extra letter"),
 ("https://xn--pypal-4ve.com/","IDN, Cyrillic a in paypal (punycode)"),("rnicrosoft.com","rn for m"),
 ("bankofamerica-login.com","brand plus login"),("amaz0n.com","zero for o"),("chase.com-verify.net","brand-looking prefix on other domain")]
look = [{"id": f"l{i:02d}", "url": u, "note": n} for i, (u, n) in enumerate(look, 1)]
look[5]["url"] = "https://pаypal.com/"
look[5]["note"] = "IDN homoglyph, Cyrillic a in paypal"
real = [("https://www.chase.com/","bank"),("https://www.paypal.com/","payments"),("https://www.google.co.uk/","regional Google"),
 ("https://www.amazon.co.uk/","regional Amazon"),("https://www.usa.gov/","government"),("https://mail.google.com/","subdomain"),
 ("https://support.microsoft.com/","subdomain"),("https://en.wikipedia.org/wiki/Apple","encyclopedia"),("https://www.apple.com/","Apple"),
 ("https://www.pineapple.com/","contains 'apple' but unrelated")]
real = [{"id": f"r{i:02d}", "url": u, "note": n} for i, (u, n) in enumerate(real, 1)]
with open("ally/risk/eval_cases.json", "w", encoding="utf-8") as f:
    json.dump({"scam_text": scam, "benign_text": ben, "lookalike": look, "real_domain": real}, f, indent=1)
    f.write("\n")
