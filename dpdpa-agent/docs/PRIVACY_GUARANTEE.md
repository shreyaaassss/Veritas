# Veritas Privacy Guarantee

**Plain-language version for compliance officers and legal teams**

---

## The short version

When you install Veritas, your data stays with you. We never see it. We never receive it. We never store it.

We sell you software — not a cloud service.

---

## What happens to your data

**Your logs stay in your building.**

Here is exactly what happens when Veritas monitors your systems:

1. Your application writes a log line on your server.
2. The Veritas Agent (a small program running on your server) reads that log line.
3. The Agent sends it to the Veritas Runtime — also running on your server.
4. The Runtime checks whether the log line contains personal data and whether that's a DPDPA concern.
5. The result (a compliance verdict) is stored in a database on your server.
6. Your compliance team sees the result on a dashboard — also running on your server.

**At no point does any of this leave your infrastructure.**

---

## What Veritas Technologies receives

Nothing.

- We do not receive your log files.
- We do not receive the personal data of your customers.
- We do not receive your compliance verdicts or audit reports.
- We cannot access your Veritas installation remotely.
- We have no "back door" or administrative access to your system.

We receive payment for the software license. That is all.

---

## The one external connection (and why it's safe)

Veritas has one optional feature that connects to the internet: **AI-powered breach investigation**.

When your auditor asks a question like *"What exactly happened with violation #3?"*, Veritas can use an AI language model (OpenAI) to generate an explanation.

**What gets sent to OpenAI:**
- The type of rule that was violated (e.g., "PII exposed in logs")
- The name of the data field involved (e.g., "phone")
- Relevant text from the DPDPA statute

**What does NOT get sent:**
- The actual log line
- Any customer's name, phone number, Aadhaar number, or any other personal data
- Any information that could identify your customers

**This feature is entirely optional.** If you do not provide an OpenAI API key, the feature is simply disabled. Every other part of Veritas — detection, rule evaluation, evidence storage, dashboard, PDF reports — works completely without internet access.

---

## Where your data is stored

Everything is stored on the machine where you installed Veritas:

| What | Where |
|------|-------|
| Compliance verdicts and audit trail | Your server's hard drive |
| Agent registration records | Your server's hard drive |
| Your organisation's compliance policies | Your server's hard drive |
| Your Veritas license file | Your server's hard drive |

None of these files are transmitted anywhere. They are yours. We have no copies.

---

## How we prevent data theft

**License security:** Your license is cryptographically signed with a key that only Veritas holds. It cannot be forged or copied to another machine without becoming invalid.

**Agent security:** Every Veritas Agent is issued a unique access token. Revoked agents are blocked immediately. An agent registered to Organisation A cannot submit data claiming to be Organisation B.

**Audit trail security:** Every compliance record is linked to the previous one with a cryptographic hash. If anyone tampers with, deletes, or modifies a record, the chain breaks and the dashboard shows `CHAIN BROKEN`. This is verifiable by your auditors at any time.

---

## What happens when your license expires

Your data stays yours. We do not delete anything when a license expires — we simply stop accepting new telemetry. Your historical audit trail remains intact and accessible.

---

## Summary for your compliance review

| Question | Answer |
|----------|--------|
| Does Veritas store our customer data on its servers? | No |
| Does Veritas receive copies of our logs or telemetry? | No |
| Does Veritas have remote access to our installation? | No |
| Is there any cross-border data transfer? | No (the optional AI explanation feature sends no personal data) |
| Can we verify these claims independently? | Yes — network monitoring, database inspection, and chain verification are all available |
| What happens to our data if we stop using Veritas? | It remains on your servers. We have no copies to delete. |

---

*For the technical architecture documentation, see `DATA_RESIDENCY.md`.*  
*For questions: support@veritas.io*
