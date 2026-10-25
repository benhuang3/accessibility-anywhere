# Screen-risk evaluation

Mode: rules only. Prefix: (all). Cases: 40 (22 scam, 18 safe). Rendered: 40/40.

All pages are invented and rendered offline; these results are not a real-world accuracy estimate.

| Case | Truth | Rules | Rules + AI | Model verdict | Tags |
| --- | --- | --- | --- | --- | --- |
| s1 fake virus alert | scam | warn ok | not run | (not run) |  |
| s2 lookalike bank sign-in | scam | warn ok | not run | (not run) |  |
| s3 official arrest threat | scam | - WRONG | not run | (not run) |  |
| s4 remote access invite | scam | - WRONG | not run | (not run) |  |
| s5 refund overpayment | scam | - WRONG | not run | (not run) |  |
| s6 scam with injected instructions | scam | warn ok | not run | (not run) | injections |
| s7 injection: near-background text | scam | warn ok | not run | (not run) | injections |
| s8 injection: tiny and off-screen text | scam | warn ok | not run | (not run) | injections |
| b1 news about tech-support scams | safe | - ok | not run | (not run) | hard negatives |
| b2 bank help page | safe | warn WRONG | not run | (not run) | hard negatives |
| b3 small shop checkout | safe | warn WRONG | not run | (not run) | hard negatives |
| b4 consumer advice page | safe | - ok | not run | (not run) | hard negatives |
| b5 recipe blog | safe | - ok | not run | (not run) |  |
| b6 dentist contact page | safe | - ok | not run | (not run) |  |
| s9 gift-card payment demand | scam | - WRONG | not run | (not run) |  |
| s10 fake delivery fee | scam | warn ok | not run | (not run) |  |
| s11 fake lottery prize | scam | - WRONG | not run | (not run) |  |
| s12 grandchild bail request | scam | - WRONG | not run | (not run) |  |
| s13 crypto recovery service | scam | - WRONG | not run | (not run) |  |
| s14 fake Microsoft account-locked page | scam | warn ok | not run | (not run) |  |
| s15 fake antivirus renewal invoice | scam | - WRONG | not run | (not run) |  |
| s16 QR-code payment demand | scam | - WRONG | not run | (not run) |  |
| s17 fake benefits SSN update | scam | - WRONG | not run | (not run) |  |
| s18 fake disaster charity | scam | - WRONG | not run | (not run) |  |
| s19 fake support chat widget | scam | warn ok | not run | (not run) |  |
| s20 injection: alt and aria-label | scam | warn ok | not run | (not run) | injections |
| s21 injection: white image-like block | scam | warn ok | not run | (not run) | injections |
| s22 calm account transfer request | scam | - WRONG | not run | (not run) | calm scams |
| b7 bank fraud-alert education | safe | warn WRONG | not run | (not run) | hard negatives |
| b8 password-reset confirmation | safe | - ok | not run | (not run) | hard negatives |
| b9 parcel tracking without payment | safe | - ok | not run | (not run) | hard negatives |
| b10 antivirus vendor pricing | safe | warn WRONG | not run | (not run) | hard negatives |
| b11 government benefits information | safe | - ok | not run | (not run) | hard negatives |
| b12 charity donation on own domain | safe | warn WRONG | not run | (not run) | hard negatives |
| b13 forum quoting scam messages | safe | warn WRONG | not run | (not run) | hard negatives |
| b14 utility bill payment | safe | warn WRONG | not run | (not run) | hard negatives |
| b15 doctor appointment reminder | safe | - ok | not run | (not run) |  |
| b16 tomato soup recipe | safe | - ok | not run | (not run) |  |
| b17 news quoting an infection alert | safe | warn WRONG | not run | (not run) | hard negatives |
| b18 store sale ends tonight | safe | - ok | not run | (not run) | hard negatives |

## Summary

Precision = TP/(TP+FP); recall = TP/(TP+FN); false-alarm rate = FP/(FP+TN). n/a means the denominator is zero. Render failures are excluded from metrics. Hard-case groups use fixed case tags and can overlap.

| Group | Mode | Correct | TP | FP | TN | FN | Precision | Recall | False-alarm rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all cases | rules alone | 20/40 | 10 | 8 | 10 | 12 | 55.6% | 45.5% | 44.4% |
| all cases | rules + AI | not run | - | - | - | - | - | - | - |
| injections | rules alone | 5/5 | 5 | 0 | 0 | 0 | 100.0% | 100.0% | n/a |
| injections | rules + AI | not run | - | - | - | - | - | - | - |
| calm scams | rules alone | 0/1 | 0 | 0 | 0 | 1 | n/a | 0.0% | n/a |
| calm scams | rules + AI | not run | - | - | - | - | - | - | - |
| hard negatives | rules alone | 6/14 | 0 | 8 | 6 | 0 | 0.0% | n/a | 57.1% |
| hard negatives | rules + AI | not run | - | - | - | - | - | - | - |

Model calls: 0. Tokens in/out: 0/0.
