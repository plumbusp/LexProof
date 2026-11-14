"""Generate a test complaint for the Complaint Reviewer Panel.

Each letter mixes correct, misquoted and invented law, so one check shows every status.
Gemini writes a fresh letter when an AI key is set; otherwise (or if it fails) a built-in
letter for the area is used. The demo is UK only; the country is a parameter so other
European countries can be added later.
"""
import re
from datetime import date

from .config import settings

COUNTRIES = {"UK": "United Kingdom"}
COUNCIL, BANK = "Northgate Borough Council", "Northgate Bank plc"

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

TEMPLATES = {
    "foi": """Dear Information Governance Officer,

I am writing to request all documents relating to the 2026 contract for street lighting maintenance awarded by Northgate Borough Council, including the tender scores and the evaluation panel's notes.

Under section 1 of the Freedom of Information Act 2000, I am entitled to be told whether the council holds this information and, if it does, to have it communicated to me.

Section 10 of the Freedom of Information Act 2000 requires you to respond within 10 working days of receiving this request, so I expect your reply by then.

I also note that section 64Z of the Freedom of Information Act 2000 prohibits councils from redacting the names of contractors in any published contract.

Please be aware that you cannot charge me any fee for answering this request.

If you decide to withhold anything, section 17 of the Freedom of Information Act 2000 requires you to issue a refusal notice stating the exemption you rely on and why it applies.

Yours faithfully,
Daniel Okafor""",
    "eir": """Dear Information Governance Officer,

I am requesting all air quality monitoring results recorded by Northgate Borough Council on Mill Lane and Station Road between January and October 2026, together with any reports sent to councillors about them.

Under regulation 5 of the Environmental Information Regulations 2004, you must make environmental information available on request as soon as possible and no later than 20 working days after receiving the request.

Regulation 7 of the same Regulations only allows you to extend that deadline by a further 5 working days, even for complex requests.

I also understand that regulation 31 of the Environmental Information Regulations 2004 requires councils to publish all pollution readings online within 48 hours of recording them.

The council is not allowed to charge anything for environmental information.

If you refuse any part of this request, regulation 14 requires you to tell me in writing which exception applies and why.

Yours sincerely,
Priya Raman""",
    "sar": """Dear Data Protection Officer,

I am making a subject access request for all personal data Northgate Borough Council holds about me, including emails, call notes and case records from my housing application since 2024.

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
    "bank": """To the Complaints Department, Northgate Bank plc,

I am complaining about two payments totalling £2,340 that left my current account on 2 November 2026 without my authorisation.

Under regulation 76 of the Payment Services Regulations 2017, you must refund an unauthorised payment within 24 hours of being told about it. I reported this on the same day and have still not been refunded.

Regulation 77 of the same Regulations says I can only be held liable for these losses if I acted fraudulently or with gross negligence, and I did neither.

The Banking Consumer Protection Act 2019 also requires you to reimburse all scam victims within 5 working days.

The bank must pay me compensation for the stress this has caused.

I understand that under the FCA's DISP rules you have eight weeks to send me a final response, after which I can take my complaint to the Financial Ombudsman Service.

Yours sincerely,
Graham Pell""",
}


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


def generate(area_id: str, country: str = "UK") -> dict:
    if country not in COUNTRIES:
        raise ValueError("This demo covers the United Kingdom only.")
    area = _BY_ID.get(area_id)
    if not area:
        raise ValueError(f"Unknown area: {area_id}")
    if settings.ai_enabled:
        try:
            return {"text": _gemini(area), "source": "gemini", "area": area_id, "country": country}
        except Exception:  # fall back to the built-in letter so the demo never stalls
            pass
    return {"text": TEMPLATES[area_id], "source": "template", "area": area_id, "country": country}


def options() -> dict:
    return {"countries": [{"code": c, "name": n} for c, n in COUNTRIES.items()],
            "areas": [{k: a[k] for k in ("id", "short", "name", "law", "about")} for a in AREAS]}
