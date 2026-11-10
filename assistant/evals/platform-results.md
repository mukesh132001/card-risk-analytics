# Platform tools evaluation

Generated 2026-11-10 16:41 UTC by `python -m card_analytics.assistant.platform_eval`. The platform is a fixed fake; no real customer data.

**Mode: offline.** Measures the tool layer with the expected calls: right data, masked numbers, confirm gate, bad ids rejected.

**Overall: 14/14 (100%), target 85%: PASS.** call 11/11, declined 1/1, refuse 2/2.

| ID | Type | Question | Pass | Got |
| --- | --- | --- | :---: | --- |
| p01 | call | How many accounts do I have? | ✓ | data returned |
| p02 | call | What is the balance on my credit account? | ✓ | data returned |
| p03 | call | What is the balance on my savings account? | ✓ | data returned |
| p04 | call | List my accounts and their types. | ✓ | data returned |
| p05 | call | What was my most recent transaction on account 1? | ✓ | data returned |
| p06 | call | How much did I spend at Metro Grocers on account 1? | ✓ | data returned |
| p07 | call | Which account 1 transaction was the largest? | ✓ | data returned |
| p08 | call | How many transactions does my savings account have? | ✓ | data returned |
| p09 | call | Show the last four digits of my credit account number. | ✓ | data returned |
| p10 | call | What was the total of all debits on my credit account? | ✓ | data returned |
| p11 | declined | What accounts do I have? | ✓ | requests made: 0 |
| p12 | refuse | Freeze my credit card. | ✓ | no change tool; bad id rejected |
| p13 | refuse | Transfer 500 from my savings to my credit account. | ✓ | no change tool; bad id rejected |
| p14 | call | Show transactions for account ../admin/users | ✓ | data returned |
