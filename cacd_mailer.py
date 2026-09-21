from datetime import date, datetime
import json
from pathlib import Path
import os
import re
import requests
import sys

from io import BytesIO
import logging
from pypdf import PdfReader

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

CONFIG_PATH = "cacd_config.json"

CONFIG = {}
with open(CONFIG_PATH, 'r') as f:
    CONFIG = json.load(f)

# ============================================================
# CALENDAR DOWNLOAD HELPER FUNCTIONS
# ============================================================

CONFIG["last_alert"] = datetime.fromisoformat(CONFIG["last_alert"])

API_URL = r'https://apps.cacd.uscourts.gov/JpsApi/judge-list'
FILE_URL = r'https://apps.cacd.uscourts.gov/JpsApi/file/'
PDF_PATH = "PDFs"

RAW_FIELDS = """
initials
title
name
titleType
dirFirstName
dirLastName
division
courthouse
courtroom
floor
dailyCalendars
""".split('\n')[1:-1]

CALENDAR_TERMS = [
    "jury trial",
    "bench trial",
    "court trial",
    "claim construction"
]

CALENDAR_REGEXES = [term.replace(' ', r'\s+') for term in CALENDAR_TERMS]
CALENDAR_ITEMS = [CALENDAR_TERMS, CALENDAR_REGEXES]

DATE_PATTERN = r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b"

# Download a PDF to the specified folder with the specified file label
# Returns the path to the file and the pdf bytes
def download_pdf(url, save_dir, file_label):
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, f"{file_label}.pdf")
    pdf_bytes = requests.get(url).content
    if not os.path.isfile(save_path):
        with open(save_path, "wb") as f:
            f.write(pdf_bytes)
    return save_path, pdf_bytes

# Class used for processing judge information from API and
# downloading PDFs
class Judge:
    def __init__(self, raw, events=CALENDAR_ITEMS):
        # Assign all of the fields that require no processing
        for field in RAW_FIELDS:
            setattr(self, field, raw[field])
        self.download_calendars(events)
        return

    def download_calendars(self, events):
        # Check whether there are any calendars, and if so, download them
        for item in self.dailyCalendars:
            date = item["date"]
            calendar = item["calendar"]
            item["filePath"] = None
            item["events"] = set()
            if calendar is None or calendar["fileName"] is None:
                continue
            pdf_path, pdf_bytes = download_pdf(
                f"{FILE_URL}{calendar['id']}",
                PDF_PATH,
                f"{date}_{self.titleType}_{self.division}_{self.initials}"
            )
            item["filePath"] = pdf_path

            # Read PDFs to see if any specified events are on calendar
            reader = PdfReader(BytesIO(pdf_bytes))
            text = "\n".join([page.extract_text() or "" for page in reader.pages]).lower()

            for name, regex in zip(*events):
                if re.search(regex, text):
                    if not re.search(regex + r"\s*" + DATE_PATTERN, text):
                        item["events"].add(name)
        return

    # Return True if there is at least one calendar
    def has_calendars(self):
        for item in self.dailyCalendars:
            if item["filePath"] is not None:
                return True
        return False

    # Return calendar items that match the specified event,
    # otherwise return None 
    def has_events(self, events):
        return [
            item
            for item in self.dailyCalendars
            if any(event in item["events"] for event in events)
        ] or None

    def get_calendars(self):
        if not self.has_calendars():
            return None

        return [
            item
            for item in self.dailyCalendars
            if item["filePath"] is not None
        ]

# ============================================================
# CHECK PROCESSING HELPER FUNCTIONS
# ============================================================

class CheckResult:
    def __init__(self, matched, calendars=None):
        self.matched = matched
        self.calendars = calendars

def evaluate_check(judge, check):
    if "all" in check:
        results = [
            evaluate_check(judge, item)
            for item in check["all"]
        ]

        return CheckResult(
            matched=all(result.matched for result in results),
            calendars=[
                calendar
                for result in results
                if result.calendars
                for calendar in result.calendars
            ]
        )

    if "any" in check:
        results = [
            evaluate_check(judge, item)
            for item in check["any"]
        ]

        matched_results = [
            result
            for result in results
            if result.matched
        ]

        return CheckResult(
            matched=bool(matched_results),
            calendars=[
                calendar
                for result in matched_results
                if result.calendars
                for calendar in result.calendars
            ]
        )

    if "field" in check:
        value = getattr(judge, check["field"])

        match check["operator"]:
            case "equals":
                return CheckResult(
                    matched=value == check["value"]
                )

            case "in":
                return CheckResult(
                    matched=value in check["value"]
                )

            case "contains":
                return CheckResult(
                    matched=check["value"] in value
                )

    if "function" in check:
        func = getattr(judge, check["function"])
        result = func(*check.get("args", []))

        return CheckResult(
            matched=bool(result),
            calendars=result
        )

    raise ValueError(f"Unknown check: {check}")

def process_judges():
    r = requests.get(API_URL)
    r.raise_for_status()
    raw = r.json()
    judges = []
    for idx, judge in enumerate(raw):
        if (idx + 1) % (len(raw) / 10) == 0: 
            log.info(f"Processing judge {idx + 1} of {len(raw)}...")
        judges.append(Judge(judge))
    cal_judges = []
    for judge in judges:
        if judge.has_calendars():
            cal_judges.append(judge)
    log.info(f"Processing complete. {len(cal_judges)} judges with calendars found.")
    return cal_judges

def process_checks(judges: list[Judge], checks):
    processed = {}
    for name, check in checks.items():
        attachments = []
        text = []
        for judge in judges:
            result = evaluate_check(judge, check["criteria"])
            if result.matched:
                calendars = result.calendars or judge.get_calendars()

                links, dates = zip(
                    *[(item["filePath"], item["date"]) for item in calendars]
                )
                attachments += links
                text.append(
                    f"➡️ {judge.title} {judge.name} "
                    f"has a {check['term']} "
                    f"{' and '.join(dates)}."
                    )
        processed[name] = {"attachments": attachments, "text": text}
    log.info(f"Processed {len(processed)} checks.")
    return processed

def send_alerts(processed_checks):
    subscribers = CONFIG["subscribers"]
    gif = frinkiac.get_gif()
    for subscriber in subscribers:
        lines = []
        attachments = []
        for check in subscribers[subscriber]:
            new_lines = processed_checks[check]["text"]
            new_attachments = processed_checks[check]["attachments"]
            lines.append(f"🚨 {check} Calendar Alert 🚨")
            if len(new_lines) < 1:
                lines.append(f"No calendars today.\n")
                log.info(f"No calendars found for {subscriber}.")
                continue
            lines += new_lines + ["",]
            for attachment in new_attachments:
                if attachment not in attachments:
                    attachments.append(attachment)
        if len(attachments) < 1:
            lines = [""]

        emailer.send_email(
            f"Daily CACD Calendar - {date.today()}",
            f"CACD Calendar Notifier <{os.environ['EMAIL_USERNAME']}>",
            subscriber,
            "</p>\n<p>".join(lines),
            attachments,
            gif
        )
        log.info(f"Alert successfully sent to {subscriber}.")
    log.info(f"Alerts sent to {len(subscribers)} subscribers.")
    return


def main():
    log.info("Script launched successfully.")
    processed_judges = process_judges()
    processed_checks = process_checks(processed_judges, CONFIG["checks"])
    send_alerts(processed_checks)

    CONFIG["last_alert"] = datetime.now().isoformat()
    with open(CONFIG_PATH, 'w') as f:
        json.dump(CONFIG, f, indent=4)
    return

if __name__ == "__main__":
    main()
    sys.exit(0)