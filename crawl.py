import requests
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from google import genai
from google.genai import errors, types
from pydantic import BaseModel
import time
import re
import html
from difflib import SequenceMatcher
import json
import csv
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
        if os.environ.get("GITHUB_ACTIONS") == "true":
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

SCORE_CUTOFF = 7  # 0-10; papers scoring at or above this are emailed
RUN_LOCALLY = os.environ.get("GITHUB_ACTIONS") != "true"


class Screening(BaseModel):
    id: int
    reason: str
    topic: int
    priority: int
    transfer: int
    score: int
    summary: str


def load_search_params(filename="search_parameters.txt"):
    params = {
        "DAYS_BACK": [],
        "SEARCH": [],
        "CORE_TOPICS": [],
        "RELATED_TOPICS": [],
        "EXCLUDE": [],
        "PRIORITY": {}
    }
    legacy = {"FIELD_OF_INTEREST": "CORE_TOPICS", "INCLUDE": "RELATED_TOPICS"}
    target = None

    try:
        with open(filename, "r") as f:
            for line in f:
                line = line.strip()

                if not line or line.startswith("#"):
                    continue

                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1].strip()
                    if section.upper().startswith("PRIORITY:"):
                        name = section.split(":", 1)[1].strip()
                        if not name:
                            print(f"[ERROR][load_search_params] [PRIORITY:] needs a name, e.g. [PRIORITY: model organism]")
                            sys.exit(1)
                        target = params["PRIORITY"].setdefault(name, [])
                    elif section in legacy:
                        print(f"[ERROR][load_search_params] Section [{section}] is no longer used. Replace it with [{legacy[section]}] (see README).")
                        sys.exit(1)
                    elif section in params and section != "PRIORITY":
                        target = params[section]
                    else:
                        print(f"[WARNING][load_search_params] Unknown section [{section}] ignored.")
                        target = None
                    continue

                if target is not None:
                    target.append(line)

    except FileNotFoundError:
        print(f"[ERROR][load_search_params] Configuration file '{filename}' not found.")
        sys.exit(1)

    params["PRIORITY"] = {k: v for k, v in params["PRIORITY"].items() if v}
    missing = [k for k in ("CORE_TOPICS", "DAYS_BACK", "SEARCH") if not params[k]]
    if missing:
        print(f"[ERROR][load_search_params] Empty or missing section(s) in '{filename}': {', '.join(missing)}")
        sys.exit(1)

    return params


def build_prompt(params):
    has_priority = bool(params["PRIORITY"])
    lines = [
        "You are screening newly released research papers for a researcher. Score how relevant each paper is to the researcher's interests.",
        "",
        "RESEARCHER'S INTERESTS",
        f"Core topics (highest weight): {'; '.join(params['CORE_TOPICS'])}",
    ]
    if params["RELATED_TOPICS"]:
        lines.append(f"Related topics (lower weight): {'; '.join(params['RELATED_TOPICS'])}")
    for name, items in params["PRIORITY"].items():
        lines.append(f"Preferred {name}, from highest to lowest priority: {' > '.join(items)}")
    if params["EXCLUDE"]:
        lines.append(f"Excluded topics: {'; '.join(params['EXCLUDE'])}")

    rules = ["Core topics weigh most. Related topics weigh less. A paper on neither scores low."]
    if has_priority:
        rules.append("For each preferred list, a paper that matches a higher-ranked item scores higher than one that matches a lower-ranked item.")
    rules += [
        "A paper outside the core topics" + (", or that matches a lower-ranked item on a preferred list," if has_priority else "")
        + " can still score high if its findings give clear, transferable insight into a core topic.",
        "Score 0 if an excluded topic is the main subject of the paper. A passing mention of an excluded topic does not count.",
        "Treat all article types (primary research, reviews, methods, theory) equally, unless the interests above say otherwise.",
        "Score 0 if the paper is not a preprint and is not published in a reputable journal known for publishing high-level research on these topics.",
    ]
    lines += ["", "SCORING RULES"] + [f"{i}. {r}" for i, r in enumerate(rules, 1)]
    lines += [
        "",
        "SCORE SCALE (0-10)",
        "9-10: directly addresses a core topic" + (" and matches the top of each preferred list" if has_priority else "") + ".",
        "7-8: clearly relevant: addresses a core topic" + (" with lower preference" if has_priority else "")
        + ", or a related topic with strong transferable insight into a core topic.",
        "4-6: partly relevant: a related topic, or a weak connection to a core topic.",
        "1-3: marginal connection only.",
        "0: unrelated, excluded topic, or unreliable journal.",
        "",
        "OUTPUT: one entry per paper, with the paper's id. Fill the fields in this order:",
        "- reason: one sentence that states the main subject of the paper and how it relates to the interests.",
        "- topic: 0-10, relevance to the core and related topics.",
        "- priority: 0-10, fit to the preferred lists." if has_priority else "- priority: always 10.",
        "- transfer: 0-10, value of transferable insight into a core topic.",
        "- score: 0-10, overall relevance. Use your judgment; this is not an average of the other fields.",
        f"- summary: if score is {SCORE_CUTOFF} or higher, a 2-sentence summary of the significance and main findings. Otherwise an empty string.",
        "",
    ]
    return "\n".join(lines)


def paper_journal(p):
    return (
            p.get("journalInfo", {}).get("journal", {}).get("title") or
            p.get("journalTitle") or
            p.get("bookOrReportDetails", {}).get("publisher") or
            "Unknown Source"
    )


def paper_link(p):
    doi = p.get("doi", "")
    return f"https://doi.org/{doi}" if doi else f"https://europepmc.org/article/{p.get('source', '')}/{p.get('id', '')}"


def write_log(papers):
    if not RUN_LOCALLY:
        return
    path = Path(__file__).resolve().parent / f"pub-crawler_log_{datetime.now():%Y-%m-%d_%H%M}.csv"
    rows = sorted(papers, key=lambda p: p.get("llm_score", -1), reverse=True)
    try:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["score", "passed", "title", "reason", "link"])
            for p in rows:
                w.writerow([
                    p.get("llm_score", ""),
                    "Y" if p.get("llm_summary") else "N",
                    re.sub(r"<[^>]+>", "", p.get("title", "")),
                    p.get("llm_reason", ""),
                    paper_link(p)
                ])
        print(f"[LOG] Screening log saved: {path.name}")
    except OSError as err:
        print(f"[WARNING][write_log] Could not save screening log: {err}")


def get_search_query(search_terms):
    formatted_terms = " OR ".join(f'TITLE_ABS:"{term}"' for term in search_terms)
    return f"({formatted_terms}) AND (SRC:MED OR SRC:PPR OR SRC:PMC)"


def find_papers(query, days_back=7, max_results=5000):
    today = datetime.now()
    past_date = today - timedelta(days=days_back)
    date_str = f"[{past_date.strftime('%Y-%m-%d')} TO {today.strftime('%Y-%m-%d')}]"

    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    params = {
        "query": f'{query} AND FIRST_PDATE:{date_str}',
        "format": "json",
        "resultType": "core",
        "pageSize": 1000,
        "cursorMark": "*"
    }
    results = []

    while True:
        data = None
        for attempt in range(1, 6):
            try:
                response = requests.get(url, params=params, timeout=60)
                response.raise_for_status()
                data = response.json()
                break
            except (requests.exceptions.RequestException, ValueError) as err:
                print(f"[WARNING][find_papers] Pub. database API call failed ({attempt}/5): {err}")
                if attempt < 5:
                    time.sleep(10)
        if data is None:
            print("[ERROR][find_papers] Pub. database unreachable.")
            return None

        if params["cursorMark"] == "*":
            print(f"[LOG] {data.get('hitCount', 0)} hit(s) in database.")

        page = data.get("resultList", {}).get("result", [])
        results.extend(page)
        next_cursor = data.get("nextCursorMark")
        if not page or not next_cursor or next_cursor == params["cursorMark"]:
            break
        if len(results) >= max_results:
            print(f"[WARNING][find_papers] Result limit ({max_results}) reached. Remaining hits were not retrieved.")
            break
        params["cursorMark"] = next_cursor

    all_papers = [p for p in results[:max_results] if p.get("doi")]
    papers = remove_duplicate_papers(all_papers)
    return papers


def normalize_title(title):
    return re.sub(r'[^a-z0-9]', '', (title or "").lower())


def is_similar_title(norm1, norm2, sm, threshold=0.85):
    if not norm1 or not norm2:
        return False
    if norm1 in norm2 or norm2 in norm1:
        return True
    if 2 * min(len(norm1), len(norm2)) / (len(norm1) + len(norm2)) <= threshold:
        return False
    sm.set_seq1(norm1)
    return sm.quick_ratio() > threshold and sm.ratio() > threshold


def remove_duplicate_papers(papers):
    unique = []
    for p in papers:
        is_dup = False
        current_norm = normalize_title(p.get("title"))
        current_doi = p.get("doi", "")
        for k, (up_norm, up_sm, up) in enumerate(unique):
            if current_doi and current_doi == up.get("doi"):
                is_dup = True
                break
            if is_similar_title(current_norm, up_norm, up_sm):
                is_dup = True
                if p.get("source") in ["MED", "PMC"] and up.get("source") == "PPR":
                    unique[k] = (current_norm, SequenceMatcher(None, "", current_norm), p)
                break
        if not is_dup:
            unique.append((current_norm, SequenceMatcher(None, "", current_norm), p))
    return [p for _, _, p in unique]


def ask_gemini(prompt, schema=None):
    retry_limit = 10
    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema) if schema else None

    for model in list(MODEL_CHAIN):
        next_idx = MODEL_CHAIN.index(model) + 1
        switch_msg = f"Switching to model: {MODEL_CHAIN[next_idx]}" if next_idx < len(MODEL_CHAIN) else "No models remaining."
        for attempt in range(1, retry_limit + 1):
            try:
                response = client.models.generate_content(model=model, contents=prompt, config=config)
                result = response.parsed if schema else response.text
                if result is None:
                    print(f"[WARNING][ask_gemini] Model: {model} returned an empty or invalid response.")
                return result

            except Exception as err:
                code = getattr(err, "code", None)
                msg = str(err).lower()
                if code in (401, 403) or (code == 400 and "api key" in msg):
                    print(f"[ERROR][ask_gemini] Gemini request refused ({code}). Check GEMINI_API_KEY: {err}")
                    sys.exit(1)
                if code == 429 and ("per day" in msg or "perday" in msg):
                    print(f"[WARNING][ask_gemini] Daily rate limit exceeded for model: {model}. {switch_msg}")
                    MODEL_CHAIN.remove(model)
                    break
                if code in (400, 404):
                    print(f"[WARNING][ask_gemini] Model: {model} is unavailable or rejected the request ({code}). {switch_msg}")
                    MODEL_CHAIN.remove(model)
                    break
                if attempt == retry_limit:
                    print(f"[WARNING][ask_gemini] Model: {model} failed {retry_limit} times ({code}: {err}). {switch_msg}")
                    break
                time.sleep(30 if code == 429 else 15)

    return None


def batch_process_papers(papers, prompt, batch_size=5, max_attempts=3, max_consecutive_failures=3):
    approved_papers = []
    skipped_count = 0
    consecutive_failures = 0

    valid_papers = [p for p in papers if p.get("abstractText") and p.get("abstractText").strip()]
    dropped_count = len(papers) - len(valid_papers)
    valid_ids = {id(p) for p in valid_papers}
    for p in papers:
        if id(p) not in valid_ids:
            p["llm_reason"] = "No abstract"
    if dropped_count > 0:
        print(f"[LOG] Dropped {dropped_count} paper(s) before screening due to missing abstracts.")

    for i in range(0, len(valid_papers), batch_size):
        if not MODEL_CHAIN or consecutive_failures >= max_consecutive_failures:
            reason = "All models are exhausted or unavailable" if not MODEL_CHAIN else f"{consecutive_failures} consecutive batches failed"
            print(f"[ERROR][batch_process_papers] {reason}. Stopping screening.")
            skipped_count += len(valid_papers) - i
            for p in valid_papers[i:]:
                p["llm_reason"] = "Not screened (LLM error)"
            break

        print(f"[LOG] Evaluated {i} out of {len(valid_papers)} paper(s)...".ljust(30), end="\r", flush=True)
        batch = valid_papers[i:i + batch_size]

        payload = []
        for idx, p in enumerate(batch):
            payload.append({
                "id": idx,
                "title": p.get("title", "No Title"),
                "journal": paper_journal(p),
                "abstract": p.get("abstractText")
            })

        batch_prompt = prompt + f"Papers:\n{json.dumps(payload)}"

        results = None
        for attempt in range(1, max_attempts + 1):
            results = ask_gemini(batch_prompt, schema=list[Screening])
            if results is not None or not MODEL_CHAIN:
                break
            print(f"[WARNING][batch_process_papers] Batch {i // batch_size + 1} failed ({attempt}/{max_attempts}).")

        if results is None:
            print(f"[WARNING][batch_process_papers] Skipping batch {i // batch_size + 1} ({len(batch)} paper(s)).")
            skipped_count += len(batch)
            consecutive_failures += 1
            for p in batch:
                p["llm_reason"] = "Not screened (LLM error)"
            continue

        consecutive_failures = 0
        scored = {}
        for res in results:
            if 0 <= res.id < len(batch) and res.id not in scored:
                scored[res.id] = res
        for idx, p in enumerate(batch):
            res = scored.get(idx)
            if res is None:
                p["llm_reason"] = "Not scored (missing from LLM output)"
                continue
            p["llm_score"] = max(0, min(10, res.score))
            p["llm_reason"] = res.reason.strip()
            if p["llm_score"] >= SCORE_CUTOFF:
                p["llm_summary"] = res.summary.strip() or p["llm_reason"]
                approved_papers.append(p)
        time.sleep(5)

    print(f"[LOG] Completed evaluation of {len(valid_papers)} paper(s)!".ljust(30))
    if skipped_count:
        print(f"[WARNING][batch_process_papers] {skipped_count} paper(s) could not be screened and were skipped.")
        if skipped_count == len(valid_papers):
            return None
    return approved_papers


def send_email(papers, days_back=7):
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

    since_date = datetime.now() - timedelta(days=days_back)

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
    <div style="text-align: center; margin-bottom: 10px; font-size: 16px; color: #777;">
        {n_papers} new paper{'s' if n_papers != 1 else ''} since {since_date.strftime('%B %d, %Y')}
    </div>
    <table width="100%" cellpadding="0" cellspacing="10" border="0">
    """

    for i in range(0, n_papers, 2):
        html_content += "<tr>\n"
        for j in range(2):
            if i + j < n_papers:
                p = valid_papers[i + j]
                title = p.get("title", "No Title")
                journal = html.escape(paper_journal(p))
                authors = html.escape(p.get("authorString", "Unknown Authors"))
                link = html.escape(paper_link(p))
                summary = html.escape(p.get("llm_summary"))

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
    <div style="text-align: center; margin-top: 20px; margin-bottom: 10px; font-size: 13px; color: #777;">
        <a href="https://github.com/npoleksic/pub-crawler" style="color: #0b57d0; text-decoration: none;">pub-crawler source code</a>
    </div>
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
    prompt = build_prompt(params)

    days_back = int(params["DAYS_BACK"][0])
    search_query = get_search_query(params["SEARCH"])

    new_papers = find_papers(search_query, days_back=days_back)
    if new_papers is None:
        sys.exit(1)

    if new_papers:
        print(f"[LOG] {len(new_papers)} candidate paper(s) pulled from database.")
        filtered_papers = batch_process_papers(new_papers, prompt, batch_size=5)
        write_log(new_papers)
        if filtered_papers is None:
            print("[LOG][ERROR] Failed to filter publications.")
            sys.exit(1)
        elif not filtered_papers:
            print("[LOG] No relevant papers remaining after LLM screening.")
        else:
            filtered_papers.sort(key=lambda p: p["llm_score"], reverse=True)
            send_email(filtered_papers, days_back)
    else:
        print("[LOG] No candidate papers found in database.")
