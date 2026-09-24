"""Build calibration/verify_labelled.json from the real ingested sources.

The provisions are not written here. They are re-extracted from the files
ingestion actually downloaded, through the same ``extract_text`` and
``split_sections`` the corpus was built with, so the text a judge sees in
calibration is byte-for-byte the text retrieval returns in production. Writing
statutory wording into a fixture by hand would be inventing law to test a
thing that exists to stop invented law, which is the mistake tests/fixtures.py
already refuses to make.

What *is* written here is the drafts and their labels: for each case, a set of
claims where some are carried by the provisions and some are injected errors
of a named kind - a wrong number, an obligation put on the wrong party, an
exception the provisions do not create, a scope wider than they grant, and a
claim with no source at all.

Run with the sources on disk (they are git-ignored; MICHAEL_SOURCES_DIR points
at them)::

    uv run python calibration/build_verify_labelled.py

It re-derives the file and fails loudly if a claim it labels is not a claim
:func:`michael.verify.split_claims` produces. That check is the point: a
labelled set that has drifted from the splitter measures nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("MICHAEL_DATABASE_URL", "postgresql://unused@127.0.0.1:1/unused")
os.environ.setdefault("EMBEDDING_API_KEY", "unused")

from michael.ingest import extract_text, split_sections  # noqa: E402
from michael.verify import split_claims  # noqa: E402

OUT = ROOT / "calibration" / "verify_labelled.json"

#: Where each document sits under the sources directory, and how it is cited.
#: The sha256-named files are exactly what ingestion stored; see
#: sources/ingestion.log.jsonl for the URL each one came from.
DOCUMENTS: dict[str, dict[str, str]] = {
    "fw1": {
        "path": "Fair Work Act 2009/C2026C00355VOL01.docx",
        "citation": "Fair Work Act 2009 (Cth)",
        "jurisdiction": "commonwealth",
        "source_url": "https://www.legislation.gov.au/C2009A00028/latest/downloads",
    },
    "parks": {
        "path": (
            "www.legislation.wa.gov.au/"
            "0dcfa8e3052924c38dbcce49f95439f45e514e57e6db2c2a4de54c1ccfc15f39.bin"
        ),
        "citation": "Residential Parks (Long-stay Tenants) Act 2006 (WA)",
        "jurisdiction": "wa",
        "source_url": "https://www.legislation.wa.gov.au/",
    },
    "landtax": {
        "path": (
            "www.legislation.wa.gov.au/"
            "02909c5f138db7e4e93cbe1674e45cc4f7ab410f8d38d7654e0824cc92068d29.bin"
        ),
        "citation": "Land Tax Assessment Act 2002 (WA)",
        "jurisdiction": "wa",
        "source_url": "https://www.legislation.wa.gov.au/",
    },
    "lsl": {
        "path": (
            "www.legislation.wa.gov.au/"
            "79f4fbce2b229e09d6354bf9ea15cd78578057e353221b6b72ecc24ae3692e68.bin"
        ),
        "citation": "Construction Industry Portable Paid Long Service Leave Act 1985 (WA)",
        "jurisdiction": "wa",
        "source_url": "https://www.legislation.wa.gov.au/",
    },
}

SUPPORTED = "supported"
WRONG_NUMBER = "wrong_number"
WRONG_PARTY = "wrong_party"
INVENTED_EXCEPTION = "invented_exception"
OVERSTATED_SCOPE = "overstated_scope"
NO_SOURCE = "no_source"

#: Injected error kinds. A claim labelled with any of these must not come back
#: SUPPORTED; that is the failure the whole exercise is measuring.
INJECTED = (WRONG_NUMBER, WRONG_PARTY, INVENTED_EXCEPTION, OVERSTATED_SCOPE, NO_SOURCE)


@dataclass(frozen=True)
class Case:
    case_id: str
    title: str
    request: str
    domain: str
    #: (document key, section number) pairs, in the order retrieval returned them.
    provisions: tuple[tuple[str, str], ...]
    #: (claim sentence, label). Grouped into paragraphs of the draft in order.
    claims: tuple[tuple[str, str], ...]


CASES: tuple[Case, ...] = (
    Case(
        case_id="fw-notice-periods",
        title="Minimum notice of termination",
        request="What notice must an employer give when ending an employee's employment?",
        domain="employment",
        provisions=(("fw1", "117"),),
        claims=(
            (
                "An employer must not terminate an employee's employment unless the employer "
                "has given the employee written notice of the day of the termination.",
                SUPPORTED,
            ),
            (
                "An employee whose period of continuous service is more than 3 years but not "
                "more than 5 years is entitled to a minimum notice period of 4 weeks.",
                WRONG_NUMBER,
            ),
            (
                "The minimum period of notice is increased by 1 week if the employee is over "
                "45 years old and has completed at least 2 years of continuous service with "
                "the employer at the end of the day the notice is given.",
                SUPPORTED,
            ),
            (
                "A reference to continuous service with the employer does not include periods "
                "of employment as a casual employee of the employer.",
                SUPPORTED,
            ),
        ),
    ),
    Case(
        case_id="fw-notice-wrong-party",
        title="Who must give notice of termination",
        request="Does the employee have to give written notice of termination?",
        domain="employment",
        provisions=(("fw1", "117"),),
        claims=(
            (
                "An employee must not end the employment relationship unless the employee has "
                "given the employer written notice of the day of the termination.",
                WRONG_PARTY,
            ),
            (
                "The employer may instead pay the employee payment in lieu of notice of at "
                "least the amount the employer would have been liable to pay at the full rate "
                "of pay for the hours the employee would have worked until the end of the "
                "minimum period of notice.",
                SUPPORTED,
            ),
            (
                "An employee who fails to give the required notice is liable to the employer "
                "for a penalty equal to the unworked notice.",
                NO_SOURCE,
            ),
        ),
    ),
    Case(
        case_id="fw-redundancy-pay",
        title="Redundancy pay periods",
        request="How much redundancy pay is an employee entitled to?",
        domain="employment",
        provisions=(("fw1", "119"),),
        claims=(
            (
                "An employee whose period of continuous service with the employer on "
                "termination is at least 5 years but less than 6 years has a redundancy pay "
                "period of 10 weeks.",
                SUPPORTED,
            ),
            (
                "An employee whose period of continuous service with the employer on "
                "termination is at least 10 years has a redundancy pay period of 16 weeks.",
                WRONG_NUMBER,
            ),
            (
                "An employee is entitled to be paid redundancy pay by the employer if the "
                "employee's employment is terminated because of the insolvency or bankruptcy "
                "of the employer.",
                SUPPORTED,
            ),
            (
                "Redundancy pay is not payable where the employer offers the employee a "
                "position with an associated entity on no less favourable terms.",
                INVENTED_EXCEPTION,
            ),
        ),
    ),
    Case(
        case_id="fw-redundancy-scope",
        title="When redundancy pay arises",
        request="Is redundancy pay payable whenever an employer dismisses someone?",
        domain="employment",
        provisions=(("fw1", "119"),),
        claims=(
            (
                "An employee is entitled to redundancy pay where the employment is terminated "
                "at the employer's initiative because the employer no longer requires the job "
                "done by the employee to be done by anyone.",
                SUPPORTED,
            ),
            (
                "Every employee dismissed by an employer is entitled to redundancy pay "
                "calculated on their period of continuous service.",
                OVERSTATED_SCOPE,
            ),
            (
                "The amount of the redundancy pay is worked out at the employee's base rate "
                "of pay for his or her ordinary hours of work.",
                SUPPORTED,
            ),
        ),
    ),
    Case(
        case_id="fw-annual-leave",
        title="Annual leave entitlement",
        request="How much paid annual leave does an employee get each year?",
        domain="employment",
        provisions=(("fw1", "87"),),
        claims=(
            (
                "For each year of service with an employer an employee is entitled to 4 weeks "
                "of paid annual leave, other than for periods of employment as a casual "
                "employee of the employer.",
                SUPPORTED,
            ),
            (
                "An employee who is defined or described as a shiftworker for the purposes of "
                "the National Employment Standards by a modern award that applies to them is "
                "entitled to 6 weeks of paid annual leave.",
                WRONG_NUMBER,
            ),
            (
                "An employee's entitlement to paid annual leave accrues progressively during a "
                "year of service according to the employee's ordinary hours of work, and "
                "accumulates from year to year.",
                SUPPORTED,
            ),
        ),
    ),
    Case(
        case_id="fw-annual-leave-casuals",
        title="Annual leave and casual service",
        request="Do casual employees accrue paid annual leave?",
        domain="employment",
        provisions=(("fw1", "87"),),
        claims=(
            (
                "Paid annual leave accrues for every employee of the employer, including "
                "periods of employment as a casual employee.",
                OVERSTATED_SCOPE,
            ),
            (
                "An award/agreement free employee qualifies for the shiftworker annual leave "
                "entitlement if the employee is employed in an enterprise in which shifts are "
                "continuously rostered 24 hours a day for 7 days a week, is regularly rostered "
                "to work those shifts, and regularly works on Sundays and public holidays.",
                SUPPORTED,
            ),
            (
                "An employee may elect to be paid out their accrued annual leave at any time "
                "by giving the employer 14 days written notice.",
                NO_SOURCE,
            ),
        ),
    ),
    Case(
        case_id="fw-personal-leave",
        title="Paid personal/carer's leave",
        request="How much paid personal leave does a full-time employee get?",
        domain="employment",
        provisions=(("fw1", "96"),),
        claims=(
            (
                "For each year of service with an employer an employee is entitled to 10 days "
                "of paid personal/carer's leave, other than for periods of employment as a "
                "casual employee of the employer.",
                SUPPORTED,
            ),
            (
                "For each year of service with an employer an employee is entitled to 12 days "
                "of paid personal/carer's leave.",
                WRONG_NUMBER,
            ),
            (
                "An employee may take paid personal/carer's leave in single day periods "
                "without providing the employer with a medical certificate.",
                NO_SOURCE,
            ),
        ),
    ),
    Case(
        case_id="fw-max-hours",
        title="Maximum weekly hours",
        request="How many hours a week can an employer require an employee to work?",
        domain="employment",
        provisions=(("fw1", "62"),),
        claims=(
            (
                "An employer must not request or require a full-time employee to work more "
                "than 38 hours in a week unless the additional hours are reasonable.",
                SUPPORTED,
            ),
            (
                "An employer must not request or require a full-time employee to work more "
                "than 40 hours in a week unless the additional hours are reasonable.",
                WRONG_NUMBER,
            ),
            (
                "The employee may refuse to work additional hours beyond those referred to in "
                "paragraph (1)(a) or (b) if they are unreasonable.",
                SUPPORTED,
            ),
            (
                "Any hours worked beyond 38 in a week are unlawful and must be refused by the "
                "employee.",
                OVERSTATED_SCOPE,
            ),
        ),
    ),
    Case(
        case_id="fw-max-hours-reasonableness",
        title="Reasonableness of additional hours",
        request="What makes additional hours reasonable or unreasonable?",
        domain="employment",
        provisions=(("fw1", "62"),),
        claims=(
            (
                "Any risk to employee health and safety from working the additional hours must "
                "be taken into account in determining whether additional hours are reasonable.",
                SUPPORTED,
            ),
            (
                "Whether the employee is entitled to receive overtime payments, penalty rates "
                "or other compensation for working additional hours must be taken into account.",
                SUPPORTED,
            ),
            (
                "Additional hours are taken to be reasonable if the employer gives the "
                "employee at least 7 days notice of the requirement to work them.",
                INVENTED_EXCEPTION,
            ),
            (
                "The hours an employee works in a week are taken to include any hours of "
                "authorised leave or absence, whether paid or unpaid, that the employee takes "
                "in the week.",
                SUPPORTED,
            ),
        ),
    ),
    Case(
        case_id="fw-nes-standards",
        title="What the National Employment Standards cover",
        request="What matters do the National Employment Standards cover?",
        domain="employment",
        provisions=(("fw1", "61"),),
        claims=(
            (
                "The minimum standards relate to matters including maximum weekly hours, "
                "annual leave, public holidays, and notice of termination and redundancy pay.",
                SUPPORTED,
            ),
            (
                "Divisions 3 to 12 constitute the National Employment Standards.",
                SUPPORTED,
            ),
            (
                "The National Employment Standards may be displaced by an individual "
                "flexibility arrangement agreed in writing between the employer and the "
                "employee.",
                OVERSTATED_SCOPE,
            ),
        ),
    ),
    Case(
        case_id="fw-public-holidays",
        title="Public holidays",
        request="Which days are public holidays under the National Employment Standards?",
        domain="employment",
        provisions=(("fw1", "115"),),
        claims=(
            (
                "25 April, being Anzac Day, is a public holiday for the purposes of the "
                "National Employment Standards.",
                SUPPORTED,
            ),
            (
                "1 March is a public holiday for the purposes of the National Employment "
                "Standards in every State and Territory.",
                NO_SOURCE,
            ),
            (
                "An employer and an award/agreement free employee may agree on the "
                "substitution of a day or part-day for a day or part-day that would otherwise "
                "be a public holiday.",
                SUPPORTED,
            ),
            (
                "A modern award may remove a day from the list of public holidays altogether "
                "for the employees it covers.",
                OVERSTATED_SCOPE,
            ),
        ),
    ),
    Case(
        case_id="fw-casual-meaning",
        title="Meaning of casual employee",
        request="When is an employee a casual employee?",
        domain="employment",
        provisions=(("fw1", "15A"),),
        claims=(
            (
                "An employee is a casual employee of an employer only if the employment "
                "relationship is characterised by an absence of a firm advance commitment to "
                "continuing and indefinite work.",
                SUPPORTED,
            ),
            (
                "Whether the relationship is characterised by an absence of a firm advance "
                "commitment is to be assessed on the basis of the real substance, practical "
                "reality and true nature of the employment relationship.",
                SUPPORTED,
            ),
            (
                "An employee who has a regular pattern of work cannot be a casual employee.",
                OVERSTATED_SCOPE,
            ),
            (
                "An employee engaged for fewer than 20 hours a week is a casual employee.",
                NO_SOURCE,
            ),
        ),
    ),
    Case(
        case_id="fw-flexible-working",
        title="Requests for flexible working arrangements",
        request="Who can ask for a change in working arrangements?",
        domain="employment",
        provisions=(("fw1", "65"),),
        claims=(
            (
                "An employee other than a casual employee is not entitled to make a request "
                "unless the employee has completed at least 12 months of continuous service "
                "with the employer immediately before making the request.",
                SUPPORTED,
            ),
            (
                "An employee who is 55 or older is in the circumstances in which a request for "
                "a change in working arrangements may be made.",
                SUPPORTED,
            ),
            (
                "An employee other than a casual employee is not entitled to make a request "
                "unless the employee has completed at least 6 months of continuous service "
                "with the employer.",
                WRONG_NUMBER,
            ),
            (
                "The employer must make the request to the employee in writing setting out "
                "details of the change sought and the reasons for the change.",
                WRONG_PARTY,
            ),
        ),
    ),
    Case(
        case_id="fw-award-application",
        title="When a modern award applies",
        request="When does a modern award apply to an employer and employee?",
        domain="employment",
        provisions=(("fw1", "47"),),
        claims=(
            (
                "A modern award applies to an employee or employer if the award covers them, "
                "the award is in operation, and no other provision of the Act provides that "
                "the award does not apply to them.",
                SUPPORTED,
            ),
            (
                "A modern award does not apply to an employee at a time when the employee is a "
                "high income employee.",
                SUPPORTED,
            ),
            (
                "A modern award does not apply to an employee who has agreed in writing with "
                "the employer that it will not apply.",
                INVENTED_EXCEPTION,
            ),
        ),
    ),
    Case(
        case_id="fw-casual-conversion",
        title="Employee notification about casual employment",
        request="When can a casual employee notify the employer about changing their status?",
        domain="employment",
        provisions=(("fw1", "66AAB"),),
        claims=(
            (
                "Where the employer is a small business employer at the time the notification "
                "is given, the employee must have been employed by the employer for a period "
                "of at least 12 months beginning the day the employment started.",
                SUPPORTED,
            ),
            (
                "Where the employer is not a small business employer at the time the "
                "notification is given, the employee must have been employed by the employer "
                "for a period of at least 3 months beginning the day the employment started.",
                WRONG_NUMBER,
            ),
            (
                "A casual employee may give an employer a written notification under this "
                "section if the employee believes that the employee no longer meets the "
                "requirements of subsections 15A(1) to (4).",
                SUPPORTED,
            ),
            (
                "The employer may give the employee a written notification requiring the "
                "employee to change to full-time employment.",
                WRONG_PARTY,
            ),
        ),
    ),
    Case(
        case_id="wa-parks-charges",
        title="What a park operator may charge",
        request="What payments may a park operator require from a long-stay tenant?",
        domain="general",
        provisions=(("parks", "12"),),
        claims=(
            (
                "A park operator must not require or receive from a long-stay tenant any "
                "payment in relation to the long-stay agreement other than rent, a security "
                "bond, an option amount that is refunded or applied towards rent, an amount "
                "the operator is authorised to require under the Act, or a prescribed fee.",
                SUPPORTED,
            ),
            (
                "The penalty for a park operator who contravenes the restriction on payments "
                "is a fine of $10 000.",
                WRONG_NUMBER,
            ),
            (
                "A payment accepted in contravention of the section is recoverable by the "
                "person who paid it as a debt due in a court of competent jurisdiction.",
                SUPPORTED,
            ),
            (
                "A park operator may charge an entry fee if the tenant agrees to it in writing "
                "before the agreement is signed.",
                INVENTED_EXCEPTION,
            ),
        ),
    ),
    Case(
        case_id="wa-parks-copy",
        title="Tenant's copy of a long-stay agreement",
        request="When must a park operator give the tenant a copy of the agreement?",
        domain="general",
        provisions=(("parks", "17"),),
        claims=(
            (
                "When a long-stay tenant signs a long-stay agreement, the park operator must "
                "give the tenant a copy of the agreement.",
                SUPPORTED,
            ),
            (
                "The park operator must ensure that a fully executed copy of the agreement is "
                "given to the tenant within 21 days after it was first signed by the tenant, "
                "or if that is not practicable, as soon as practicable after that.",
                SUPPORTED,
            ),
            (
                "The tenant must ensure that a fully executed copy of the agreement is given "
                "to the park operator within 21 days after it was first signed.",
                WRONG_PARTY,
            ),
        ),
    ),
    Case(
        case_id="wa-parks-cooling-off",
        title="Cooling off period for site-only agreements",
        request="Can a long-stay tenant rescind a site-only agreement after signing it?",
        domain="general",
        provisions=(("parks", "18"),),
        claims=(
            (
                "A long-stay tenant under a site-only agreement is entitled to rescind the "
                "agreement at any time within 5 working days after the date of the agreement "
                "if the park operator has complied with section 11(2).",
                SUPPORTED,
            ),
            (
                "Where the park operator has not complied with section 11(2) within the time "
                "specified but has given the tenant the documents required under that section, "
                "the tenant may rescind at any time within 14 working days after the day on "
                "which those documents are given.",
                WRONG_NUMBER,
            ),
            (
                "A long-stay tenant is not entitled to rescind a long-stay agreement under "
                "this section after taking up occupation of the agreed premises.",
                SUPPORTED,
            ),
        ),
    ),
    Case(
        case_id="wa-parks-contracting-out",
        title="Contracting out of the Act",
        request="Can the parties agree that the Act will not apply to their agreement?",
        domain="general",
        provisions=(("parks", "9"),),
        claims=(
            (
                "Any contract, agreement, scheme or arrangement has no effect to the extent "
                "that it purports to exclude, modify or restrict the operation of the Act, "
                "except as specifically provided by the Act.",
                SUPPORTED,
            ),
            (
                "Any purported waiver of a right conferred by or under the Act has no effect.",
                SUPPORTED,
            ),
            (
                "A person who enters into an arrangement with the intention of defeating the "
                "operation of the Act is liable to a fine of $10 000.",
                SUPPORTED,
            ),
            (
                "A term excluding the Act is effective where both parties obtained independent "
                "legal advice before signing.",
                INVENTED_EXCEPTION,
            ),
        ),
    ),
    Case(
        case_id="wa-parks-disclosure",
        title="Documents for prospective long-stay tenants",
        request="What must a park operator give a prospective tenant before the agreement?",
        domain="general",
        provisions=(("parks", "11"),),
        claims=(
            (
                "A park operator must give the required documents to a person at least 5 "
                "working days before a site-only agreement is signed.",
                SUPPORTED,
            ),
            (
                "The required documents include a copy of the proposed agreement, a disclosure "
                "statement in the approved form, a copy of the information booklet in the "
                "approved form, and a copy of any park rules that apply to the residential "
                "park.",
                SUPPORTED,
            ),
            (
                "A park operator must give the required documents to a person at least 10 "
                "working days before a site-only agreement is signed.",
                WRONG_NUMBER,
            ),
            (
                "The obligation to give the required documents applies to every long-stay "
                "agreement without exception.",
                OVERSTATED_SCOPE,
            ),
        ),
    ),
    Case(
        case_id="wa-parks-agents",
        title="Real estate agent fees",
        request="Can a real estate agent charge a long-stay tenant a letting fee?",
        domain="general",
        provisions=(("parks", "13"),),
        claims=(
            (
                "A real estate agent who provides services on behalf of a park operator in "
                "connection with letting agreed premises must not require or receive from a "
                "long-stay tenant any fee, charge or reward for those services.",
                SUPPORTED,
            ),
            (
                "A fee, charge or reward received in contravention of the section is "
                "recoverable by the person who paid it as a debt due in a court of competent "
                "jurisdiction.",
                SUPPORTED,
            ),
            (
                "The park operator commits an offence if the agent charges the tenant such a fee.",
                WRONG_PARTY,
            ),
        ),
    ),
    Case(
        case_id="wa-landtax-liability",
        title="Who is liable for land tax",
        request="Who has to pay land tax on a lot in Western Australia?",
        domain="general",
        provisions=(("landtax", "7"),),
        claims=(
            (
                "Land tax payable on land for an assessment year is payable by the person who "
                "is or was the owner of the land at midnight on 30 June in the previous "
                "financial year.",
                SUPPORTED,
            ),
            (
                "Joint owners of land are jointly and severally liable for land tax payable on "
                "the land regardless of each joint owner's respective interests in, or use of, "
                "the land.",
                SUPPORTED,
            ),
            (
                "Land tax is payable by the person who is the owner of the land at midnight on "
                "31 December in the previous financial year.",
                WRONG_NUMBER,
            ),
            (
                "A purchaser who buys the land during the assessment year becomes liable for "
                "the land tax for that year.",
                NO_SOURCE,
            ),
        ),
    ),
    Case(
        case_id="wa-landtax-residence",
        title="Exemption for a private residence",
        request="Is my home exempt from land tax?",
        domain="general",
        provisions=(("landtax", "21"),),
        claims=(
            (
                "Private residential property, except property held in trust, is exempt for an "
                "assessment year if at midnight on 30 June in the financial year before the "
                "assessment year it is owned by an individual who uses it as the individual's "
                "primary residence.",
                SUPPORTED,
            ),
            (
                "Property owned by persons who have lived in a de facto relationship with each "
                "other for at least 2 years, at least one of whom uses it as that person's "
                "primary residence, is exempt.",
                SUPPORTED,
            ),
            (
                "Property owned by persons who have lived in a de facto relationship with each "
                "other for at least 5 years is exempt.",
                WRONG_NUMBER,
            ),
            (
                "All private residential property owned by an individual is exempt from land tax.",
                OVERSTATED_SCOPE,
            ),
        ),
    ),
    Case(
        case_id="wa-landtax-construction",
        title="One year exemption while building a home",
        request="Is land exempt while a private residence is being built on it?",
        domain="general",
        provisions=(("landtax", "24"),),
        claims=(
            (
                "Private residential property owned by an individual is exempt for an "
                "assessment year if the construction of the private residence is completed "
                "during the assessment year and the individual is the first occupant of it.",
                SUPPORTED,
            ),
            (
                "The property is not exempt if the individual or any other person derived any "
                "income from the property in the period between the beginning of the "
                "assessment year and the time when the property was first occupied.",
                SUPPORTED,
            ),
            (
                "The exemption is available whether or not the individual uses the private "
                "residence as their primary residence during the assessment year.",
                OVERSTATED_SCOPE,
            ),
            (
                "The exemption may be extended by a further year on application to the "
                "Commissioner.",
                NO_SOURCE,
            ),
        ),
    ),
    Case(
        case_id="wa-lsl-taking-leave",
        title="Taking construction industry long service leave",
        request="How must long service leave be taken in the construction industry?",
        domain="general",
        provisions=(("lsl", "24"),),
        claims=(
            (
                "An employee shall take long service leave in one continuous period unless the "
                "employer consents to the leave being taken in more than one period.",
                SUPPORTED,
            ),
            (
                "Where the employer consents, the leave shall not be taken in more than 3 "
                "periods and a period of leave shall be not less than one week.",
                SUPPORTED,
            ),
            (
                "Where the employer consents, the leave shall not be taken in more than 5 periods.",
                WRONG_NUMBER,
            ),
            (
                "Where an employer and employee do not agree as to the time at which an "
                "employee may proceed on leave, the employer or employee may apply to the "
                "Board and the Board may determine the application.",
                SUPPORTED,
            ),
        ),
    ),
    Case(
        case_id="wa-lsl-cessation",
        title="Cessation of a long service leave entitlement",
        request="When does a construction industry long service leave entitlement lapse?",
        domain="general",
        provisions=(("lsl", "23"),),
        claims=(
            (
                "Where a person has been engaged as an employee for any number of days that "
                "does not exceed 1 100 days of service and has not been so engaged within the "
                "period of 2 years commencing from the last of such days, the Board shall "
                "cause the name of that person to be removed from the register of employees.",
                SUPPORTED,
            ),
            (
                "Where a person has been engaged for a number of days exceeding 1 100 days of "
                "service and has not been so engaged within the period of 6 years commencing "
                "from the last of such days, the name is removed from the register.",
                WRONG_NUMBER,
            ),
            (
                "Nothing in the section prevents an employee from becoming entitled to long "
                "service leave under the Act by virtue of any subsequent service as an "
                "employee.",
                SUPPORTED,
            ),
        ),
    ),
)


def _row_id(citation: str, section: str) -> int:
    """A stable six-digit id standing in for the database row id."""
    digest = hashlib.sha256(f"{citation}|{section}".encode()).hexdigest()
    return 100_000 + int(digest[:12], 16) % 900_000


def load_provisions() -> dict[tuple[str, str], dict[str, object]]:
    """Every provision the cases name, read verbatim from the ingested sources."""
    from michael.config import settings

    sources = settings().sources_dir
    wanted: dict[str, set[str]] = {}
    for case in CASES:
        for key, section in case.provisions:
            wanted.setdefault(key, set()).add(section)

    found: dict[tuple[str, str], dict[str, object]] = {}
    for key, sections in wanted.items():
        meta = DOCUMENTS[key]
        path = sources / meta["path"]
        if not path.is_file():
            raise SystemExit(
                f"{path} is missing. This builder reads the real ingested sources; "
                "point MICHAEL_SOURCES_DIR at them and re-run."
            )
        provisions = split_sections(extract_text(path.read_bytes(), origin=str(path)))
        by_number = {p.section_number: p for p in provisions}
        for section in sorted(sections):
            provision = by_number.get(section)
            if provision is None:
                raise SystemExit(f"{meta['citation']} has no section {section} in {path.name}")
            found[key, section] = {
                # A six-digit stand-in for the database row id. Distinctive on
                # purpose: an id the judge invents must not collide with one
                # retrieval returned, or the id-not-in-the-retrieved-set rule
                # would pass a hallucinated citation by luck.
                "provision_id": _row_id(meta["citation"], section),
                "ref": f"{key}:{section}",
                "jurisdiction": meta["jurisdiction"],
                "citation": meta["citation"],
                "section_number": provision.section_number,
                "heading": provision.heading,
                "text": provision.text,
                "source_url": meta["source_url"],
            }
    return found


def render_draft(case: Case) -> str:
    """Assemble a draft whose paragraphs are exactly the labelled claims.

    One claim per paragraph, wrapped in the scaffolding a real draft carries -
    a heading, a bolded request field, a status line - so the splitter's
    exclusions are exercised on the same shapes production feeds it.
    """
    lines = [
        f"# ADVICE NOTE - {case.title}",
        "",
        "**Status: DRAFT. Not reviewed. Not executed.**",
        "",
        f"**Request:** {case.request}",
        "",
        "## ADVICE",
        "",
    ]
    for text, _ in case.claims:
        lines += [text, ""]
    return "\n".join(lines)


def build() -> dict[str, object]:
    provisions = load_provisions()
    cases: list[dict[str, object]] = []
    for case in CASES:
        draft = render_draft(case)
        produced, skipped = split_claims(draft)
        labelled = [text for text, _ in case.claims]
        if [c.text for c in produced] != [" ".join(t.split()) for t in labelled]:
            raise SystemExit(
                f"{case.case_id}: split_claims does not produce the labelled claims.\n"
                f"  produced: {[c.text for c in produced]}\n"
                f"  labelled: {labelled}"
            )
        if skipped:
            raise SystemExit(f"{case.case_id}: {skipped} claims were skipped as unsupplied")
        cases.append(
            {
                "case_id": case.case_id,
                "title": case.title,
                "request": case.request,
                "domain": case.domain,
                "draft": draft,
                "provisions": [provisions[key] for key in case.provisions],
                "claims": [
                    {
                        "claim_id": claim.claim_id,
                        "text": claim.text,
                        "label": label,
                        "injected": label in INJECTED,
                    }
                    for claim, (_, label) in zip(produced, case.claims, strict=True)
                ],
            }
        )
    return {
        "note": (
            "Drafts and labels are written by hand; every provision is re-extracted from "
            "the files ingestion downloaded, through michael.ingest.extract_text and "
            "split_sections, so the text here is the text retrieval returns. Rebuild with "
            "calibration/build_verify_labelled.py, which refuses to write a labelled claim "
            "that michael.verify.split_claims does not produce."
        ),
        "labels": {
            "supported": "carried by the provisions as stated",
            "wrong_number": "injected: a figure the provisions do not state",
            "wrong_party": "injected: an obligation placed on the wrong party",
            "invented_exception": "injected: an exception the provisions do not create",
            "overstated_scope": "injected: wider than the provisions grant",
            "no_source": "injected: nothing in the provisions goes to it at all",
        },
        "cases": cases,
    }


def main() -> None:
    payload = build()
    # newline="" so a rebuild on Windows does not rewrite the file as CRLF.
    # .gitattributes keeps this repository LF, and a whole-file line-ending
    # churn buries the one label that actually changed.
    with OUT.open("w", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    cases = payload["cases"]
    assert isinstance(cases, list)
    claims = [c for case in cases for c in case["claims"]]
    injected = [c for c in claims if c["injected"]]
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"  cases:    {len(cases)}")
    print(f"  claims:   {len(claims)}")
    print(f"  injected: {len(injected)}")
    kinds: dict[str, int] = {}
    for claim in injected:
        kinds[claim["label"]] = kinds.get(claim["label"], 0) + 1
    for kind, count in sorted(kinds.items()):
        print(f"    {kind}: {count}")


if __name__ == "__main__":
    main()
