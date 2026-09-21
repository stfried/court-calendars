import html
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import logging
import requests
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Table,
    TableStyle,
)

import emailer
import frinkiac

# ============================================================
# CONFIGURATION
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers = [
        logging.FileHandler(
            f"{Path(__file__).stem}.log",
            mode="w",
        ),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

OUTPUT_PDF = "Sacramento_Judge_Calendar.pdf"

CONFIG_PATH = "caed_config.json"

CONFIG = {}
with open(CONFIG_PATH, 'r') as f:
    CONFIG = json.load(f)

CONFIG["last_alert"] = datetime.fromisoformat(CONFIG["last_alert"])

PUBLIC_INFORMATION_URL = (
    "https://www.caed.uscourts.gov/"
    "caednew/index.cfm/public-information/"
)

API_URL = (
    "https://www.caed.uscourts.gov/"
    "caedapiclient/getCalendarForJudgeDate.aspx"
)

NUMBER_OF_DAYS = 7

SACRAMENTO_JUDGES = {
    "Brennan": 5027,
    "Calabretta": 5090,
    "Claire": 5062,
    "Coggins": 5096,
    "Delaney": 5055,
    "Drozd": 5088,
    "Kim": 5091,
    "Mendez": 5037,
    "Nunley": 5067,
    "Peterson": 5085,
    "Riordan": 5095,
    "Shubb": 5005,
}

# ============================================================
# EMAIL
# ============================================================


def send_email(recipient, pdf_path, gif):

    subject = f"Weekly EDCA Calendar - {date.today()}"
    sender = f"EDCA Calendar Notifier <{os.environ['EMAIL_USERNAME']}>"
    content = "Attached is the Sacramento Judge Calendar for the next week."
    emailer.send_email(subject, sender, recipient, content, [pdf_path], gif)


def send_calendar_emails():

    gif = frinkiac.get_gif()

    for recipient in CONFIG["recipients"]:

        send_email(
            recipient,
            OUTPUT_PDF,
            gif
        )

        log.info(
            f"Calendar successfully sent to "
            f"{recipient}."
        )


# ============================================================
# HTTP SESSION
# ============================================================

def create_session():

    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "application/json, text/javascript, */*; q=0.01"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    })

    return session


def establish_session(session):

    log.info("Opening CAED public-information page...")

    response = session.get(
        PUBLIC_INFORMATION_URL,
        headers={
            "Referer": (
                "https://www.caed.uscourts.gov/"
            )
        },
        timeout=30,
    )

    response.raise_for_status()

    log.info(
        f"  Connected successfully "
        f"({response.status_code})"
    )


# ============================================================
# CALENDAR API
# ============================================================

def get_calendar(
    session,
    judge_name,
    judge_id,
    calendar_date,
):

    date_string = calendar_date.strftime(
        "%m/%d/%Y"
    )

    params = {
        "1": "1",
        "date": date_string,
        "JudgeID": str(judge_id),
        "Start": "undefined",
        "End": "undefined",
    }

    headers = {
        "Referer": PUBLIC_INFORMATION_URL,
        "X-Requested-With": "XMLHttpRequest",
    }

    log.info(
        f"  {date_string} | "
        f"{judge_name:<12} | "
        f"JudgeID {judge_id} ...",
    )

    try:

        response = session.get(
            API_URL,
            params=params,
            headers=headers,
            timeout=30,
        )

        response.raise_for_status()

        data = response.json()

        return data

    except requests.RequestException as exc:

        log.error(f"ERROR: {exc}")

        return None

    except ValueError as exc:

        log.error(f"ERROR: Invalid JSON response: {exc}")

        return None


# ============================================================
# DATA CLEANING
# ============================================================

def strip_html(value):

    if value is None:
        return ""

    value = html.unescape(
        str(value)
    )

    value = re.sub(
        r"<br\s*/?>",
        " ",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"<[^>]+>",
        "",
        value,
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def clean_party_name(value):

    value = strip_html(value)

    value = re.sub(
        r"\s*\(attorney\)\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )

    return value.strip()


def clean_courtroom(value):
    """
    Normalize courtroom descriptions to just
    the courtroom number.

    Examples:

        "Courtroom 4"      -> "4"
        "Courtroom No. 4"  -> "4"
        "No. 4"            -> "4"
        "NO 4"             -> "4"
    """

    value = strip_html(value)

    value = re.sub(
        r"(?i)\bcourtroom\b\.?\s*",
        "",
        value,
    )

    value = re.sub(
        r"(?i)\bno\.?\s*",
        "",
        value,
    )

    return value.strip()


def get_case_name(case):

    plaintiff = clean_party_name(
        case.get("Plaintiff", "")
    )

    defendant = clean_party_name(
        case.get("Defendent", "")
    )

    if plaintiff and defendant:
        return (
            f"{plaintiff} v. {defendant}"
        )

    if plaintiff:
        return plaintiff

    if defendant:
        return defendant

    return ""


def format_session_time(value):

    if not value:
        return ""

    value = str(value).strip()

    formats = [
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %H:%M",
    ]

    for date_format in formats:

        try:

            parsed = datetime.strptime(
                value,
                date_format,
            )

            return parsed.strftime(
                "%I:%M %p"
            ).lstrip("0")

        except ValueError:
            continue

    return value


def get_sort_datetime(value):

    if not value:
        return datetime.max

    value = str(value).strip()

    formats = [
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %H:%M",
    ]

    for date_format in formats:

        try:

            return datetime.strptime(
                value,
                date_format,
            )

        except ValueError:
            continue

    return datetime.max


def is_asterisk_case_number(case_number):

    if not case_number:
        return False

    return bool(
        re.fullmatch(
            r"\*+",
            case_number.strip(),
        )
    )


# ============================================================
# COLLECT CALENDAR DATA
# ============================================================

def collect_calendars(session):

    all_matters = []

    start_date = datetime.now().date()

    log.info("=" * 70)
    log.info("RETRIEVING SACRAMENTO JUDGE CALENDARS")
    log.info("=" * 70)
    for day_offset in range(
        NUMBER_OF_DAYS
    ):

        calendar_date = (
            start_date
            + timedelta(days=day_offset)
        )

        # Skip Saturdays and Sundays.
        if calendar_date.weekday() >= 5:

            log.info("SKIPPING WEEKEND:")
            log.info(f"{calendar_date.strftime('%A, %B %d, %Y')}")

            continue

        log.info(
            f"DATE: "
            f"{calendar_date.strftime('%A, %B %d, %Y')}"
        )
        log.info("-" * 70)

        for judge_name, judge_id in (
            SACRAMENTO_JUDGES.items()
        ):

            data = get_calendar(
                session,
                judge_name,
                judge_id,
                calendar_date,
            )

            if not data:
                continue

            day_info = data.get("Day") or {}

            cases = data.get("Cases") or []

            courtroom = clean_courtroom(
                day_info.get(
                    "Courtroom",
                    "",
                )
            )

            floor = strip_html(
                day_info.get(
                    "Floor",
                    "",
                )
            )

            location = strip_html(
                day_info.get(
                    "Location",
                    "",
                )
            )

            for case in cases:

                case_number = strip_html(
                    case.get(
                        "CaseNumber",
                        "",
                    )
                )

                # Skip pseudo-case.
                if (
                    case_number.upper()
                    == "PUBLIC ACCESS INFORMATION"
                ):
                    continue

                # Skip case numbers consisting
                # entirely of asterisks.
                if is_asterisk_case_number(
                    case_number
                ):
                    continue

                session_time = strip_html(
                    case.get(
                        "SessionTime",
                        "",
                    )
                )

                if not session_time:
                    continue

                case_name = get_case_name(
                    case
                )

                proceeding = strip_html(
                    case.get(
                        "Proceeding",
                        "",
                    )
                )

                if "vacated" in proceeding.lower():
                    continue

                if "moved to" in proceeding.lower():
                    continue

                if "volun dismissal" in proceeding.lower():
                    continue

                if "case settled" in proceeding.lower():
                    continue

                if "case dismissed" in proceeding.lower():
                    continue

                if "advanced to" in proceeding.lower():
                    continue


                public_note = strip_html(
                    case.get(
                        "PublicNote",
                        "",
                    )
                )

                session_location = strip_html(
                    case.get(
                        "SessionLocation",
                        "",
                    )
                )

                all_matters.append({
                    "date": calendar_date,

                    "date_display": (
                        calendar_date.strftime(
                            "%A, %B %d, %Y"
                        )
                    ),

                    "judge": judge_name,

                    "judge_id": judge_id,

                    "courtroom": courtroom,

                    "floor": floor,

                    "location": location,

                    "time": format_session_time(
                        session_time
                    ),

                    "sort_time": get_sort_datetime(
                        session_time
                    ),

                    "case_number": case_number,

                    "case_name": case_name,

                    "hearing_type": proceeding,

                    "public_note": public_note,

                    "session_location": (
                        session_location
                    ),
                })

    all_matters.sort(
        key=lambda item: (
            item["date"],
            item["sort_time"],
            item["judge"],
        )
    )

    return all_matters


# ============================================================
# PDF
# ============================================================

def create_pdf(matters):

    log.info("=" * 70)
    log.info("CREATING PDF")
    log.info("=" * 70)

    doc = SimpleDocTemplate(
        str(OUTPUT_PDF),
        pagesize=landscape(letter),
        rightMargin=0.35 * inch,
        leftMargin=0.35 * inch,
        topMargin=0.35 * inch,
        bottomMargin=0.35 * inch,
        title="Sacramento Judge Calendar",
        author="CAED Calendar Monitor",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "CalendarTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=21,
        alignment=TA_CENTER,
        spaceAfter=4,
    )

    subtitle_style = ParagraphStyle(
        "CalendarSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        alignment=TA_CENTER,
        spaceAfter=12,
    )

    date_style = ParagraphStyle(
        "DateHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=15,
        spaceBefore=7,
        spaceAfter=6,
    )

    table_header_style = ParagraphStyle(
        "TableHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=9,
        alignment=TA_LEFT,
    )

    table_body_style = ParagraphStyle(
        "TableBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=9,
        alignment=TA_LEFT,
    )

    table_body_center_style = ParagraphStyle(
        "TableBodyCenter",
        parent=table_body_style,
        alignment=TA_CENTER,
    )

    note_style = ParagraphStyle(
        "Note",
        parent=table_body_style,
        fontName="Helvetica-Oblique",
        fontSize=6.5,
        leading=7.5,
        spaceBefore=2,
    )

    story = []

    start_date = datetime.now().date()

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    if matters:

        first_date = min(
            item["date"]
            for item in matters
        )

        last_date = max(
            item["date"]
            for item in matters
        )

        date_range = (
            f"{first_date.strftime('%B %d, %Y')} – "
            f"{last_date.strftime('%B %d, %Y')}"
        )

    else:

        date_range = (
            "No calendar matters found"
        )

    story.append(
        Paragraph(
            "U.S. District Court — "
            "Eastern District of California",
            title_style,
        )
    )

    story.append(
        Paragraph(
            f"Sacramento Judge Calendar"
            f"<br/>{date_range}",
            subtitle_style,
        )
    )

    # --------------------------------------------------------
    # GROUP BY DATE
    # --------------------------------------------------------

    matters_by_date = {}

    for matter in matters:

        matters_by_date.setdefault(
            matter["date"],
            [],
        ).append(matter)

    # Include weekdays with no matters.
    for day_offset in range(
        NUMBER_OF_DAYS
    ):

        calendar_date = (
            start_date
            + timedelta(days=day_offset)
        )

        if calendar_date.weekday() >= 5:
            continue

        matters_by_date.setdefault(
            calendar_date,
            [],
        )

    # --------------------------------------------------------
    # DAILY TABLES
    # --------------------------------------------------------

    first_date_on_page = True

    for calendar_date in sorted(
        matters_by_date
    ):

        # Every date after the first starts
        # on a new page.
        if not first_date_on_page:

            story.append(
                PageBreak()
            )

        first_date_on_page = False

        day_matters = matters_by_date[
            calendar_date
        ]

        story.append(
            Paragraph(
                calendar_date.strftime(
                    "%A, %B %d, %Y"
                ),
                date_style,
            )
        )

        if not day_matters:

            story.append(
                Paragraph(
                    "No calendar matters returned.",
                    table_body_style,
                )
            )

            continue

        table_data = [
            [
                Paragraph(
                    "Time",
                    table_header_style,
                ),
                Paragraph(
                    "Judge",
                    table_header_style,
                ),
                Paragraph(
                    "Courtroom",
                    table_header_style,
                ),
                Paragraph(
                    "Floor",
                    table_header_style,
                ),
                Paragraph(
                    "Case No.",
                    table_header_style,
                ),
                Paragraph(
                    "Case",
                    table_header_style,
                ),
                Paragraph(
                    "Hearing / Proceeding",
                    table_header_style,
                ),
            ]
        ]

        for matter in day_matters:

            case_cell = [
                Paragraph(
                    html.escape(
                        matter["case_name"]
                    ),
                    table_body_style,
                )
            ]

            if matter["public_note"]:

                case_cell.append(
                    Paragraph(
                        html.escape(
                            matter["public_note"]
                        ),
                        note_style,
                    )
                )

            table_data.append([
                Paragraph(
                    html.escape(
                        matter["time"]
                    ),
                    table_body_center_style,
                ),

                Paragraph(
                    html.escape(
                        matter["judge"]
                    ),
                    table_body_style,
                ),

                Paragraph(
                    html.escape(
                        matter["courtroom"]
                    ),
                    table_body_center_style,
                ),

                Paragraph(
                    html.escape(
                        matter["floor"]
                    ),
                    table_body_center_style,
                ),

                Paragraph(
                    html.escape(
                        matter["case_number"]
                    ),
                    table_body_style,
                ),

                case_cell,

                Paragraph(
                    html.escape(
                        matter["hearing_type"]
                    ),
                    table_body_style,
                ),
            ])

        table = Table(
            table_data,
            colWidths=[
                0.65 * inch,   # Time
                0.85 * inch,   # Judge
                0.70 * inch,   # Courtroom
                0.55 * inch,   # Floor
                1.00 * inch,   # Case number
                3.50 * inch,   # Case
                3.25 * inch,   # Hearing
            ],
            repeatRows=1,
            hAlign="LEFT",
        )

        table.setStyle(
            TableStyle([
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor(
                        "#D9E2F3"
                    ),
                ),
                (
                    "TEXTCOLOR",
                    (0, 0),
                    (-1, 0),
                    colors.black,
                ),
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.35,
                    colors.HexColor(
                        "#A6A6A6"
                    ),
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "TOP",
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    4,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    4,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    3,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    3,
                ),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [
                        colors.white,
                        colors.HexColor(
                            "#F7F7F7"
                        ),
                    ],
                ),
            ])
        )

        story.append(table)

    # --------------------------------------------------------
    # BUILD PDF
    # --------------------------------------------------------

    doc.build(story)

    log.info("PDF created:")
    log.info(f"  {OUTPUT_PDF}")
    log.info(
        f"  Matters: {len(matters)}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    log.info("=" * 70)
    log.info("CAED SACRAMENTO JUDGE CALENDAR")
    log.info("=" * 70)

    session = create_session()

    try:

        establish_session(session)

        matters = collect_calendars(
            session
        )

    finally:

        session.close()

    log.info(
        f"Total calendar matters found: "
        f"{len(matters)}"
    )

    create_pdf(matters)

    log.info("=" * 70)
    log.info("SENDING CALENDAR EMAILS")
    log.info("=" * 70)

    send_calendar_emails()

    log.info("=" * 70)
    log.info("DONE")
    log.info("=" * 70)

    # Update the last alert timestamp
    CONFIG["last_alert"] = datetime.now().isoformat()

    # Write the updated CONFIG dictionary back to JSON
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(CONFIG, f, indent=4)


if __name__ == "__main__":
    main()
    sys.exit(0)