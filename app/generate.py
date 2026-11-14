"""Generate a test complaint for the Complaint Reviewer Panel.

Each letter mixes correct, misquoted and invented law, so one check shows every status.
Two hand-written presets per area load instantly; Gemini can also write a fresh letter, falling
back to a preset if it is unavailable. The demo is UK only; the country is a parameter so other
European countries can be added later.
"""
import random
import re
from datetime import date

from .config import settings
from .pipeline import ORG_NAME

COUNTRIES = {"UK": "United Kingdom"}
COUNCIL, BANK = ORG_NAME, "Northgate Bank plc"

AREAS = [
    {"id": "foi", "short": "FOI", "name": "Freedom of Information", "law": "Freedom of Information Act 2000",
     "about": "Asks a public body for information it holds.", "to": f"the Information Governance Officer, {COUNCIL}"},
    {"id": "eir", "short": "EIR", "name": "Environmental Information Regulations",
     "law": "Environmental Information Regulations 2004",
     "about": "Asks for information about the environment: air, water, waste, planning.",
     "to": f"the Information Governance Officer, {COUNCIL}"},
    {"id": "sar", "short": "SAR", "name": "Subject Access Request", "law": "UK GDPR and Data Protection Act 2018",
     "about": "Asks for a copy of the personal data an organisation holds about you.",
     "to": f"the Data Protection Officer, {COUNCIL}"},
    {"id": "council", "short": "LGA", "name": "Local Government Act", "law": "Local Government Acts 1972 and 1974",
     "about": "Complains about a council service, decision or meeting.", "to": f"the Complaints Team, {COUNCIL}"},
    {"id": "bank", "short": "FCA", "name": "Financial Conduct Authority",
     "law": "FCA complaint rules (DISP) and Payment Services Regulations 2017",
     "about": "Complains to a bank about a payment or service.", "to": f"the Complaints Department, {BANK}"},
]
_BY_ID = {a["id"]: a for a in AREAS}

PROMPT = """Write a realistic letter that a member of the public in the United Kingdom might send to {to}.
Area: {short} ({name}), under the {law}.

It is a test letter for a tool that checks every law a letter cites, so include exactly:
- two citations that are correct: a real section, regulation or article, and an accurate description of what it says;
- one citation of a real provision with one wrong detail, such as the wrong deadline or number of days;
- one section, regulation or Act that does not exist, cited confidently;
- one legal claim that names no law at all (for example "you must reply within two weeks").

Rules: 180 to 280 words. Plain text only: no markdown, no headings, no bullet points, no notes about which
citations are wrong. Invent the sender's name and the local details; never name a real person.
Address it to {to}. Sign off with the sender's name. Today is {today}."""

_FIRST = {
    "foi": """Dear Information Governance Officer,

I am writing to request all documents relating to the 2026 contract for street lighting maintenance awarded by {council}, including the tender scores and the evaluation panel's notes.

Under section 1 of the Freedom of Information Act 2000, I am entitled to be told whether the council holds this information and, if it does, to have it communicated to me.

Section 10 of the Freedom of Information Act 2000 requires you to respond within 10 working days of receiving this request, so I expect your reply by then.

I also note that section 64Z of the Freedom of Information Act 2000 prohibits councils from redacting the names of contractors in any published contract.

Please be aware that you cannot charge me any fee for answering this request.

If you decide to withhold anything, section 17 of the Freedom of Information Act 2000 requires you to issue a refusal notice stating the exemption you rely on and why it applies.

Yours faithfully,
Daniel Okafor""",
    "eir": """Dear Information Governance Officer,

I am requesting all air quality monitoring results recorded by {council} on Camden Road and Kentish Town Road between January and October 2026, together with any reports sent to councillors about them.

Under regulation 5 of the Environmental Information Regulations 2004, you must make environmental information available on request as soon as possible and no later than 20 working days after receiving the request.

Regulation 7 of the same Regulations only allows you to extend that deadline by a further 5 working days, even for complex requests.

I also understand that regulation 31 of the Environmental Information Regulations 2004 requires councils to publish all pollution readings online within 48 hours of recording them.

The council is not allowed to charge anything for environmental information.

If you refuse any part of this request, regulation 14 requires you to tell me in writing which exception applies and why.

Yours sincerely,
Priya Raman""",
    "sar": """Dear Data Protection Officer,

I am making a subject access request for all personal data {council} holds about me, including emails, call notes and case records from my housing application since 2024.

Under Article 15 of the UK GDPR, I have the right to obtain confirmation that you are processing my personal data and a copy of that data.

Article 12(3) of the UK GDPR requires you to respond within 14 days of receiving this request.

I am also aware that the Data Protection (Compensation) Act 2020 entitles me to £500 for every week your reply is late.

You must provide all of this free of charge and in an electronic format.

Please also tell me the purposes of the processing and who the data has been shared with, as Article 15(1) of the UK GDPR requires.

Yours sincerely,
Thomas Whitfield""",
    "council": """Dear Complaints Team,

I wish to make a formal complaint about the decision to close the Eastgate library, which was taken at a cabinet meeting on 14 October 2026 that residents were told was private.

Section 100A of the Local Government Act 1972 requires meetings of a principal council to be open to the public, unless confidential or exempt information is being discussed.

Under section 26B of the Local Government Act 1974, I have only 6 months from the decision to take my complaint to the Local Government and Social Care Ombudsman, so please treat this as urgent.

Section 100Z of the Local Government Act 1972 also gives every resident the right to veto the closure of a public library.

The council must respond to a formal complaint within 10 working days.

Under section 100B of the Local Government Act 1972, copies of the agenda and reports for the meeting should have been open to inspection by the public beforehand.

Yours faithfully,
Helen Marsh""",
    "bank": """To the Complaints Department, {bank},

I am complaining about two payments totalling £2,340 that left my current account on 2 November 2026 without my authorisation.

Under regulation 76 of the Payment Services Regulations 2017, you must refund an unauthorised payment within 24 hours of being told about it. I reported this on the same day and have still not been refunded.

Regulation 77 of the same Regulations says I can only be held liable for these losses if I acted fraudulently or with gross negligence, and I did neither.

The Banking Consumer Protection Act 2019 also requires you to reimburse all scam victims within 5 working days.

The bank must pay me compensation for the stress this has caused.

I understand that under the FCA's DISP rules you have eight weeks to send me a final response, after which I can take my complaint to the Financial Ombudsman Service.

Yours sincerely,
Graham Pell""",
}


_SECOND = {
    "foi": """Dear Information Governance Officer,

Please send me the total amount {council} spent on agency and interim staff in each month of the 2025/26 financial year, broken down by directorate, and the five highest day rates paid.

I am making this request in writing and have included my name and an address for correspondence, as section 8 of the Freedom of Information Act 2000 requires for a valid request.

Section 10 of the Freedom of Information Act 2000 gives you one calendar month to respond, so I will expect your answer within that time.

If you think the cost is too high, I understand that section 12 of the Freedom of Information Act 2000 only allows you to refuse where the cost of complying would exceed the appropriate limit, and that you should then help me narrow the request.

I also note that section 93 of the Freedom of Information Act 2000 obliges every council to publish all agency staff invoices within 7 days of payment.

You are not allowed to ask me why I want this information.

Yours faithfully,
Aisha Bello""",
    "eir": """Dear Information Governance Officer,

I am requesting the flood risk assessment and any drainage reports {council} considered before approving the new housing development next to the canal, together with the planning officer's notes.

Regulation 2(1) of the Environmental Information Regulations 2004 defines environmental information to include measures such as plans and activities that affect the state of the land and water, so this request falls under those Regulations.

Under regulation 5(2) of the Environmental Information Regulations 2004, you must make this information available no later than 10 working days after receiving my request.

Regulation 12(1) says you may only withhold information if an exception applies and, in all the circumstances, the public interest in keeping it back outweighs the public interest in disclosing it.

I also understand that regulation 27 of the Environmental Information Regulations 2004 requires flood risk assessments to be published before any planning decision is taken.

The council must publish the full flood risk assessment on its website.

Yours sincerely,
Martin Doyle""",
    "sar": """Dear Data Protection Officer,

I am making a subject access request for all CCTV footage showing me or my car, and all records linked to the parking penalty charge notice I received on Kentish Town Road on 3 October 2026.

Under Article 15(3) of the UK GDPR, you must provide me with a copy of the personal data you are processing about me.

Article 12(5) of the UK GDPR says this information must be provided free of charge.

Article 12(3) of the UK GDPR gives you 40 days from receiving this request to send me the data.

I am also aware that section 220 of the Data Protection Act 2018 requires councils to keep CCTV footage for at least two years once a request has been made.

You must keep the footage until my request has been fully answered.

Yours sincerely,
Chloe Brennan""",
    "council": """Dear Complaints Team,

I wish to complain about repeated delays to repairs at my council flat. A leaking roof reported in July 2026 has still not been fixed, and the matter was discussed at the housing committee in September.

Under section 100C of the Local Government Act 1972, the minutes of that committee meeting must be open to public inspection for six years, so please send me a copy.

Section 100B of the Local Government Act 1972 required the agenda for that meeting to be open to inspection at least two clear days before it took place, which did not happen.

Section 26A of the Local Government Act 1974 allows a member of the public who has suffered injustice to complain to the Local Government and Social Care Ombudsman, which I will do if this is not resolved.

I also note that section 100M of the Local Government Act 1972 requires every housing repair to be completed within 28 days of being reported.

The council must compensate me for the damage to my belongings.

Yours faithfully,
Samuel Adeyemi""",
    "bank": """To the Complaints Department, {bank},

I am complaining about a holiday booking of £1,480 that I paid for with my credit card in August 2026. The travel company has since gone into administration and the holiday will not take place.

Section 75 of the Consumer Credit Act 1974 makes you jointly liable with the supplier for a breach of contract where I paid between £100 and £30,000 by credit card.

Section 49 of the Consumer Rights Act 2015 requires a service to be performed with reasonable care and skill, and your handling of my claim so far has fallen short of that.

Under regulation 76 of the Payment Services Regulations 2017, you have 5 business days to refund a payment I did not authorise, and one of the deposits was taken twice without my consent.

The Card Payments Fairness Act 2021 also requires you to cancel all foreign transaction fees on cancelled bookings.

The bank must refund my foreign transaction fees.

Yours sincerely,
Ruth Calloway""",
}

_TITLES = {
    "foi": ["Street lighting contract", "Agency staff spending"],
    "eir": ["Air quality on two roads", "Flood risk and planning"],
    "sar": ["Housing application records", "CCTV and a parking fine"],
    "council": ["Library closure meeting", "Housing repairs"],
    "bank": ["Unauthorised payments", "Holiday refund by credit card"],
}
# Two hand-written presets per area: instant, and each mixes correct, misquoted and invented law.
PRESETS = {a: [{"id": f"{a}-{i + 1}", "title": t, "text": txt}
               for i, (t, txt) in enumerate(zip(_TITLES[a], (_FIRST[a], _SECOND[a])))] for a in _FIRST}


def _fill(text: str) -> str:
    return text.replace("{council}", COUNCIL).replace("{bank}", BANK)


def _gemini(area: dict) -> str:
    from openai import OpenAI
    kw = {"api_key": settings.ai_api_key, "timeout": 45, "max_retries": 1}
    if settings.ai_base_url:
        kw["base_url"] = settings.ai_base_url
    resp = OpenAI(**kw).chat.completions.create(
        model=settings.ai_model, temperature=1.0,
        messages=[{"role": "user", "content": PROMPT.format(today=f"{date.today().day} {date.today():%B %Y}", **area)}])
    text = (resp.choices[0].message.content or "").strip()
    text = re.sub(r"^```\w*\n|\n```$", "", text).replace("**", "").strip()
    if len(text) < 200:
        raise RuntimeError("letter too short")
    return text


def generate(area_id: str, country: str = "UK", preset: str | None = None) -> dict:
    """preset given: that hand-written letter, instantly. Otherwise Gemini writes a new one,
    falling back to a random preset if Gemini is unavailable."""
    if country not in COUNTRIES:
        raise ValueError("This demo covers the United Kingdom only.")
    area = _BY_ID.get(area_id)
    if not area:
        raise ValueError(f"Unknown area: {area_id}")
    out = {"area": area_id, "country": country}
    if preset:
        p = next((p for p in PRESETS[area_id] if p["id"] == preset), None)
        if not p:
            raise ValueError(f"Unknown preset: {preset}")
        return {**out, "text": _fill(p["text"]), "source": "preset", "title": p["title"]}
    if settings.ai_enabled:
        try:
            return {**out, "text": _gemini(area), "source": "gemini"}
        except Exception:  # fall back to a preset so the demo never stalls
            pass
    p = random.choice(PRESETS[area_id])
    return {**out, "text": _fill(p["text"]), "source": "fallback", "title": p["title"]}


def options() -> dict:
    return {"countries": [{"code": c, "name": n} for c, n in COUNTRIES.items()],
            "areas": [{**{k: a[k] for k in ("id", "short", "name", "law", "about")},
                       "presets": [{"id": p["id"], "title": p["title"]} for p in PRESETS[a["id"]]]} for a in AREAS]}
