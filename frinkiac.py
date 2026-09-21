import json
import os
from pathlib import Path
import random
import time
import sys

import requests
from playwright.sync_api import TimeoutError, sync_playwright


###
# INITIALIZATION
###

# Get parent folder of file

RANDOM_URL = r'https://frinkiac.com/api/random'
GIF_PATH = "GIFs"
GIF_JSON_PATH = "gifs.json"
GIF_DATA = json.load(open(GIF_JSON_PATH, 'r')) if os.path.exists(GIF_JSON_PATH) else {"gif_number": 0, "gif_list": []}

MONTHS = """
January
February
March
April
May
June
July
August
September
October
November
December
""".split('\n')[1:-1]


# Class used for persistent tracking of rate limits
class RateLimiter:
    def __init__(self, min_interval=2.0):
        self.min_interval = min_interval
        self.last = 0

    def wait(self):
        now = time.time()
        delta = now - self.last
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta + random.uniform(0, 0.5))
        self.last = time.time()

LIMITER = RateLimiter(min_interval=2.5)

# Return True if caption contains any blacklisted words
def blacklisted(caption, blacklist):
    for word in blacklist:
        if word.lower() in caption.lower():
            return True
    return False


def save_gif(gif):

    # Update json file with new gif information

    # Overwrite the oldest gif if the maximum number of gifs has been reached
    if len(GIF_DATA["gif_list"]) >= GIF_DATA["gif_max"]:
        gif["Filename"] = os.path.join(GIF_PATH, f"{GIF_DATA['gif_number']}.gif")
        GIF_DATA["gif_list"][GIF_DATA["gif_number"]] = gif
        GIF_DATA["gif_number"] = (GIF_DATA["gif_number"] + 1) % GIF_DATA["gif_max"]

    # Otherwise, append the new gif to the list
    else:
        gif["Filename"] = os.path.join(GIF_PATH, f"{len(GIF_DATA['gif_list'])}.gif")
        GIF_DATA["gif_list"].append(gif)

    # Save gif to folder
    gif_bytes = requests.get(gif["URL"]).content
    with open(gif["Filename"], "wb") as f:
        f.write(gif_bytes)

    # Write updated gif data to file
    with open(GIF_JSON_PATH, 'w') as f:
        f.write(json.dumps(GIF_DATA, indent=4))
    return gif


def generate_gif(blacklist=[], season_range=[], year_range=[1989, 2000]):
    regenerate = True
    while regenerate:
        # Retrieve raw json from RANDOM_URL
        try:
            r = requests.get(RANDOM_URL)
            r.raise_for_status()
            raw = r.json()
            regenerate = False
        except Exception as e:
            print(e)
            return
        
        # Extract caption to check if regeneration needed
        caption = " ".join([s["Content"] for s in raw["Subtitles"]])

        # Check if the caption is too short or contains blacklisted words
        if len(caption) < 1 or blacklisted(caption, blacklist):
            regenerate = True
            continue

        # Collect the rest of the useful episode information
        episode = raw["Frame"]["Episode"]
        timestamp = raw["Frame"]["Timestamp"]
        info = raw["Episode"]
        year, month, day = info["OriginalAirDate"].split("-")
        month = MONTHS[int(month) - 1]
    
        if episode == "Movie":
            description = (
                f"{info['Title']}, directed by {info['Director']}, "
                f"written by {info['Writer']}, "
                f"aired {month} {day}, {year}."
            )
        else:
            description = (
                f"Season {info['Season']} Episode {info['EpisodeNumber']}, "
                f"{info['Title']}, directed by {info['Director']}, "
                f"written by {info['Writer']}, "
                f"aired {month} {day}, {year}."
            )

        # Check if a season range is provided and if the episode's season falls within that range
        if season_range is not None and len(season_range) == 2 and season_range[0] is not None and season_range[1] is not None and \
                season_range[0] > 0 and season_range[1] > 0 and season_range[0] <= season_range[1]:
            if not (season_range[0] <= int(info["Season"]) <= season_range[1]):
                regenerate = True
                continue

        # Check if a year range is provided and if the episode's year falls within that range
        if year_range is not None and len(year_range) == 2 and year_range[0] is not None and year_range[1] is not None and \
                year_range[0] > 0 and year_range[1] > 0 and year_range[0] <= year_range[1]:
            if not (year_range[0] <= int(year) <= year_range[1]):
                regenerate = True
                continue

    # Launch playwright
    with sync_playwright() as p:

        browser = p.chromium.launch(headless=True)

        page = browser.new_page()

        # Visit the first website
        first_url = f"https://frinkiac.com/bettermaker/{episode}/{timestamp - 2500}/{timestamp + 2500}"     
        LIMITER.wait()
        page.goto(first_url)
        # Wait for JS to execute
        page.wait_for_load_state("networkidle")

        # Retrieve and visit the second website
        second_url = page.url.replace("bettermaker", "bettergif")
        LIMITER.wait()
        page.goto(second_url)

        # Attempt to generate the GIF for download
        try:
            # Click the Generate GIF button
            page.click("text=Generate GIF")
            # Wait for the Download GIF button to appear
            page.wait_for_selector("text=Download GIF")
        
        except TimeoutError:
            print("GIF generation failed or timed out")
            print(second_url)
            page.close()
            browser.close()
            return

        # Extract the download link's URL
        final_url = "https://frinkiac.com" + page.get_attribute("text=Download GIF", "href")
        
        gif = {"URL": final_url, "Description": description, "Caption": caption}
        gif = save_gif(gif)
        return gif


def load_gif_from_file():
    return random.choice(GIF_DATA["gif_list"]) if GIF_DATA["gif_list"] else None


def get_gif():
    try:
        gif = generate_gif()
    except Exception as e:
        print(e)
        gif = load_gif_from_file()
    return gif


def main():
    print("Generating gif.")
    gif = get_gif()
    print("Gif generated and saved successfully.")

if __name__ == "__main__":
    main()