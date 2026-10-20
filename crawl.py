import requests
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from google import genai
from google.genai import errors
import time
import re
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
from dotenv import load_dotenv, set_key
import sys

ENV_FILE = ".env"

def setup_credentials():
    if Path(ENV_FILE).exists():
        load_dotenv(ENV_FILE)

    required_keys = {
        "GEMINI_API_KEY": "Google Gemini API Key",
        "GMAIL_SENDER": "Sender Gmail Address",
        "GMAIL_APP_PASSWORD": "16-character Gmail App Password (no spaces)",
        "GMAIL_RECIPIENT": "Recipient Email Address"
    }

    missing_keys = [key for key in required_keys if not os.environ.get(key)]

    if missing_keys:
        if os.environ.get("GITHUB_ACTIONS") == "true" or not sys.stdout.isatty():
            print(f"[ERROR] Missing required credentials in CI/CD environment: {', '.join(missing_keys)}")
            print("Ensure these are set in GitHub Secrets and mapped in the workflow file.")
            sys.exit(1)

        print("\n" + "=" * 45)
        print(" FIRST-TIME SETUP: API KEYS & CREDENTIALS")
        print("=" * 45)
        print("Your keys will be safely stored in a local '.env' file.\n")

        if not Path(ENV_FILE).exists():
            Path(ENV_FILE).touch()

        for env_var in missing_keys:
            val = input(f"Paste your {required_keys[env_var]}: ").strip()
            set_key(ENV_FILE, env_var, val)
            os.environ[env_var] = val

        print("\n[LOG] Setup complete! Keys saved to .env file.")
        print("=" * 45 + "\n")

setup_credentials()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GMAIL_SENDER = os.environ.get("GMAIL_SENDER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
GMAIL_RECIPIENT = os.environ.get("GMAIL_RECIPIENT")

client = genai.Client(api_key=GEMINI_API_KEY)

MODEL_CHAIN = [
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3-flash-preview",
    ]

def load_search_params(filename="search_parameters.txt"):
    params = {
        "DAYS_BACK": [],
        "SEARCH": [],
        "INCLUDE": [],
        "EXCLUDE": [],
        "FIELD_OF_INTEREST": []
    }
    current_section = None

    try:
        with open(filename, "r") as f:
            for line in f:
                line = line.strip()

                if not line or line.startswith("#"):
                    continue

                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1]
                    if section in params:
                        current_section = section
                    continue

                if current_section:
                    params[current_section].append(line)

    except FileNotFoundError:
        print(f"[ERROR][load_search_params] Configuration file '{filename}' not found.")

    return params


def get_search_query(search_terms):
    formatted_terms = " OR ".join(f'TITLE_ABS:"{term}"' for term in search_terms)
    return f"({formatted_terms}) AND (SRC:MED OR SRC:PPR OR SRC:PMC)"


def find_papers(query, days_back=7):
    today = datetime.now()
    past_date = today - timedelta(days=days_back)
    date_str = f"[{past_date.strftime('%Y-%m-%d')} TO {today.strftime('%Y-%m-%d')}]"

    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    params = {
        "query": f'{query} AND FIRST_PDATE:{date_str}',
        "format": "json",
        "resultType": "core",
        "pageSize": 1000
    }
    attempts = 0
    results = []

    while attempts <= 5:
        try:
            attempts += 1
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            results = data.get("resultList", {}).get("result", [])
            break
        except requests.exceptions.HTTPError as http_err:
            if attempts > 5:
                print(f"[ERROR][find_papers] HTTP ERROR: {http_err}")
            else:
                print(f"[WARNING][find_papers] Pub. database API call failed. Reattempting ({attempts}/5)...")
                time.sleep(10)

    all_papers = [p for p in results if p.get("doi")]
    papers = remove_duplicate_papers(all_papers)
    return papers


def is_similar_title(title1, title2, threshold=0.85):
    norm1 = re.sub(r'[^a-z0-9]', '', title1.lower())
    norm2 = re.sub(r'[^a-z0-9]', '', title2.lower())
    if norm1 in norm2 or norm2 in norm1:
        return True
    return SequenceMatcher(None, norm1, norm2).ratio() > threshold


def remove_duplicate_papers(papers):
    unique_papers = []
    for p in papers:
        is_dup = False
        current_title = p.get("title", "")
        current_doi = p.get("doi", "")
        for up in unique_papers:
            if current_doi and current_doi == up.get("doi"):
                is_dup = True
                break
            if current_title and up.get("title"):
                if is_similar_title(current_title, up.get("title")):
                    is_dup = True
                    if p.get("source") in ["MED", "PMC"] and up.get("source") == "PPR":
                        unique_papers.remove(up)
                        unique_papers.append(p)
                    break
        if not is_dup:
            unique_papers.append(p)
    return unique_papers

def circular_shift(lst, n):
    if not lst:
        return lst
    n = n % len(lst)
    return lst[-n:] + lst[:-n]


def ask_gemini(prompt, expect_json=False):
    global MODEL_CHAIN
    high_demand_limit = 10
    config = {"response_mime_type": "application/json"} if expect_json else None

    for i, model in enumerate(MODEL_CHAIN):
        high_demand_counter = 0
        while True:
            try:
                chat = client.chats.create(model=model, config=config)
                response = chat.send_message(prompt)
                if i > 0:
                    MODEL_CHAIN = circular_shift(MODEL_CHAIN, -i)
                return response.text.strip()

            except errors.APIError as api_err:
                error_msg = str(api_err).lower()
                if "per day" in error_msg or "quota" in error_msg:
                    if i+1 < len(MODEL_CHAIN):
                        print(f"[WARNING][ask_gemini] Daily rate limit exceeded for model: {model}. Switching to model: {MODEL_CHAIN[i+1]}")
                    else:
                        print(f"[WARNING][ask_gemini] Daily rate limit exceeded for model: {model}. Unable to generate response.")
                    break
                elif "high demand" in error_msg:
                    high_demand_counter += 1
                    if high_demand_counter > high_demand_limit:
                        if i+1 < len(MODEL_CHAIN):
                            print(f"[WARNING][ask_gemini] Model: {model} is in high demand. Switching to model: {MODEL_CHAIN[i+1]}")
                        else:
                            print(f"[WARNING][ask_gemini] Model: {model} is in high demand. Unable to generate response.")
                        break
                elif "404" in error_msg:
                    if i+1 < len(MODEL_CHAIN):
                        print(f"[WARNING][ask_gemini] Model: {model} is no longer in use. Switching to model: {MODEL_CHAIN[i+1]}")
                    else:
                        print(f"[WARNING][ask_gemini] Model: {model} is no longer in use. Unable to generate response.")
                    break
                # print(f"[WARNING][ask_gemini] API Rate limit hit. Pausing 10s...")
                time.sleep(10)
            except Exception as err:
                print(f"[ERROR][ask_gemini]: {str(err)}")
                return None

    return None


def batch_process_papers(papers, params, batch_size=5):
    approved_papers = []
    include_topics = ", ".join(params["INCLUDE"])
    exclude_topics = ", ".join(params["EXCLUDE"])
    field_of_interest = params["FIELD_OF_INTEREST"][0]

    valid_papers = [p for p in papers if p.get("abstractText") and p.get("abstractText").strip()]
    dropped_count = len(papers) - len(valid_papers)
    if dropped_count > 0:
        print(f"[LOG] Dropped {dropped_count} paper(s) before screening due to missing abstracts.")


    for i in range(0, len(valid_papers), batch_size):
        print(f"[LOG] Evaluated {i} out of {len(valid_papers)} paper(s)...".ljust(30), end="\r", flush=True)
        batch = valid_papers[i:i + batch_size]

        payload = []
        for idx, p in enumerate(batch):
            journal = (
                    p.get("journalInfo", {}).get("journal", {}).get("title") or
                    p.get("journalTitle") or
                    p.get("bookOrReportDetails", {}).get("publisher") or
                    "Unknown Source"
            )
            payload.append({
                "id": idx,
                "title": p.get("title", "No Title"),
                "journal": journal,
                "abstract": p.get("abstractText")
            })

        prompt = (
            "You are a rigorous scientific screener. Review the following JSON array of research papers.\n"
            "Criteria:\n"
            f"1. Must be strictly primary {field_of_interest} research.\n"
            f"2. Must be a preprint or published in a reputable journal that is known for publishing high-level {field_of_interest} research.\n"
            f"3. MUST study, review, or theorize on at least one of the following topics: {include_topics}\n"
            f"4. MUST NOT cover any of the following topics: {exclude_topics}\n\n"
            "If it meets ALL criteria, write a 2-sentence summary of the significance and main findings. "
            "If it fails ANY criteria, the summary MUST be exactly the word: REJECT\n"
            "Respond ONLY with a valid JSON array using this exact schema: [{\"id\": int, \"summary\": \"string\"}]\n\n"
            f"Papers:\n{json.dumps(payload)}"
        )

        response = ask_gemini(prompt, expect_json=True)

        if response:
            try:
                results = json.loads(response)
                for res in results:
                    summary = res.get("summary", "REJECT").strip()
                    summary_lower = summary.lower()

                    is_rejected = (
                            "reject" in summary_lower or
                            "no abstract" in summary_lower or
                            len(summary) < 30
                    )

                    if not is_rejected:
                        paper = batch[res["id"]]
                        paper["llm_summary"] = summary
                        approved_papers.append(paper)
            except json.JSONDecodeError:
                print("[ERROR][batch_process_papers] Failed to parse JSON from LLM.".ljust(30))
                return None
            except Exception as err:
                print(f"[ERROR][batch_process_papers] {str(err)}".ljust(30))
                return None
        else:
            return None
        time.sleep(5)

    print(f"[LOG] Completed evaluation of {len(papers)} paper(s)!".ljust(30))
    if len(approved_papers) > 0:
        return approved_papers
    else:
        return "NULL"


def send_email(papers):
    if papers is None or GMAIL_SENDER is None or GMAIL_RECIPIENT is None or GMAIL_APP_PASSWORD is None:
        return

    print(f"[LOG] {len(papers)} paper(s) passed screening.")

    valid_papers = []
    for p in papers:
        summary = p.get("llm_summary", "Summary missing.")
        if summary is None or summary == "Summary missing." or summary == "":
            continue
        valid_papers.append(p)

    n_papers = len(valid_papers)

    msg = EmailMessage()
    msg['From'] = GMAIL_SENDER
    msg['To'] = GMAIL_RECIPIENT
    msg['Subject'] = f"pub-crawler: {n_papers} new paper{'s' if n_papers != 1 else ''}!"

    html_content = f"""
    <html>
    <head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        @media screen and (max-width: 600px) {{
            .tile {{
                display: block !important;
                width: 100% !important;
                box-sizing: border-box !important;
                margin-bottom: 10px !important;
            }}
            .empty-tile {{
                display: none !important;
            }}
        }}
    </style>
    </head>
    <body style="font-family: Arial, sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="10" border="0">
    """

    for i in range(0, n_papers, 2):
        html_content += "<tr>\n"
        for j in range(2):
            if i + j < n_papers:
                p = valid_papers[i + j]
                title = p.get("title", "No Title")
                journal = (
                        p.get("journalInfo", {}).get("journal", {}).get("title") or
                        p.get("journalTitle") or
                        p.get("bookOrReportDetails", {}).get("publisher") or
                        "Unknown Source"
                )
                authors = p.get("authorString", "Unknown Authors")
                doi = p.get("doi", "")
                link = f"https://doi.org/{doi}" if doi else f"https://europepmc.org/article/{p.get('source', '')}/{p.get('id', '')}"
                summary = p.get("llm_summary")

                html_content += f"""
                <td class="tile" width="50%" valign="top" style="border: 1px solid #ddd; border-radius: 8px; padding: 15px; background-color: #fafafa;">
                    <div style="font-size: 14px; font-weight: bold; margin-bottom: 8px; line-height: 1.3;">
                        <a href="{link}" style="color: #0b57d0; text-decoration: none;">{title}</a>
                    </div>
                    <div style="font-size: 11px; color: #555; margin-bottom: 8px; line-height: 1.3;">
                        <strong>{journal}</strong><br>
                        {authors}
                    </div>
                    <div style="font-size: 12px; color: #222; line-height: 1.5; margin: 0;">
                        {summary}
                    </div>
                </td>\n"""
            else:
                html_content += '                <td class="empty-tile" width="50%" style="border: none; background: transparent;"></td>\n'
        html_content += "</tr>\n"

    html_content += """
    </table>
    </body>
    </html>
    """

    print("[LOG] Composing email...")
    msg.set_content(html_content, subtype='html')

    print("[LOG] Accessing email server...")
    server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10)

    print("[LOG] Logging into email...")
    server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)

    print("[LOG] Sending email...")
    server.send_message(msg)
    server.quit()
    print("[LOG] Success!")


if __name__ == "__main__":
    params = load_search_params()

    search_query = get_search_query(params["SEARCH"])

    days_back = int(params["DAYS_BACK"][0])
    times_checked = 0
    while times_checked < 120:
        times_checked += 1
        new_papers = find_papers(search_query, days_back=days_back)
        if new_papers is not None:
            break
        time.sleep(60)

    if new_papers:
        print(f"[LOG] {len(new_papers)} candidate paper(s) pulled from database.")
        attempts = 1
        while attempts <= 5:
            filtered_papers = batch_process_papers(new_papers, params)
            attempts += 1
            if not filtered_papers and attempts <= 5:
                print(f"[LOG][WARNING] An error occurred while filtering publications. Reattempting ({attempts}/5).")
            elif not filtered_papers and attempts > 5:
                print(f"[LOG][ERROR] Failed to filter publications.")
            elif filtered_papers == "NULL":
                print(f"[LOG] No relevant papers remaining after LLM screening.")
            else:
                send_email(filtered_papers)
                break
    else:
        print("[LOG] No candidate papers found in database.")
