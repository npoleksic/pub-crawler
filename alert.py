import os
import requests
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from google import genai
from google.genai import errors
import time
import re
from difflib import SequenceMatcher
import json

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GMAIL_SENDER = os.environ.get("GMAIL_SENDER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
GMAIL_RECIPIENT = os.environ.get("GMAIL_RECIPIENT")

client = genai.Client(api_key=GEMINI_API_KEY)


def get_search_query():
    with open("search_terms.txt", "r") as file:
        search_terms = file.read().splitlines()
    return f"({' OR '.join(f'\"{term}\"' for term in search_terms)}) AND (SRC:MED OR SRC:PPR OR SRC:PMC)"


def find_papers(query, days_back=7):
    today = datetime.now()
    past_date = today - timedelta(days=days_back)
    date_str = f"[{past_date.strftime('%Y-%m-%d')} TO {today.strftime('%Y-%m-%d')}]"

    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    params = {
        "query": f'{query} AND FIRST_PDATE:{date_str}',
        "format": "json",
        "resultType": "core", # Required to retrieve full abstracts
        "pageSize": 200
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


def get_criteria():
    with open("include_categories.txt", "r") as file:
        include_list = file.read().splitlines()

    with open("exclude_categories.txt", "r") as file:
        exclude_list = file.read().splitlines()

    include_topics = ", ".join(include_list)
    exclude_topics = ", ".join(exclude_list)

    return include_topics, exclude_topics


def ask_gemini(prompt, expect_json=False):
    MODEL_CHAIN = [
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash-lite",
        "gemini-3.8-flash",
        "gemini-3.7-flash",  # Added missing comma here
        "gemini-3.6-flash",
        "gemini-3.5-flash"
    ]

    config = {"response_mime_type": "application/json"} if expect_json else None

    for model in MODEL_CHAIN:
        while True:
            try:
                # Direct generate_content is perfectly fine here, no need for chats.create
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config
                )
                return response.text.strip()
            except errors.APIError as api_err:
                error_msg = str(api_err).lower()
                if "per day" in error_msg or "quota" in error_msg:
                    break  # Advance to next model
                print(f"[WARNING] API Rate limit hit. Pausing 10s...")
                time.sleep(10)
            except Exception as err:
                print(f"[ERROR][ask_gemini]: {str(err)}")
                break
    return None


def batch_process_papers(papers, include_topics, exclude_topics, batch_size=10):
    approved_papers = []

    for i in range(0, len(papers), batch_size):
        batch = papers[i:i + batch_size]
        print(f"[LOG] Evaluating papers {i + 1} to {min(i + batch_size, len(papers))}...")

        # Construct a JSON-friendly payload for the LLM
        payload = []
        for idx, p in enumerate(batch):
            payload.append({
                "id": idx,
                "title": p.get("title", "No Title"),
                "abstract": p.get("abstractText", "No abstract available.")
            })

        prompt = (
            "You are a rigorous scientific screener. Review the following JSON array of research papers.\n"
            "Criteria:\n"
            "1. Must be relevant to systems or behavioral neuroscience studying awake, behaving non-human primates, humans, or model organisms.\n"
            f"2. MUST INCLUDE at least one topic: {include_topics}\n"
            f"3. MUST NOT INCLUDE any topic: {exclude_topics}\n\n"
            "For each paper, evaluate if it meets ALL criteria. If it does, write a 2-sentence summary of the main finding. If it fails, set summary to 'REJECT'.\n"
            "Respond ONLY with a valid JSON array of objects using this exact schema: [{\"id\": int, \"summary\": \"string\"}]\n\n"
            f"Papers:\n{json.dumps(payload)}"
        )

        response = ask_gemini(prompt, expect_json=True)

        if response:
            try:
                results = json.loads(response)
                for res in results:
                    summary = res.get("summary", "REJECT")
                    if summary != "REJECT" and "Unable to generate" not in summary:
                        # Attach the summary back to the original paper dictionary
                        paper = batch[res["id"]]
                        paper["llm_summary"] = summary
                        approved_papers.append(paper)
            except json.JSONDecodeError:
                print("[ERROR] Failed to parse JSON from Gemini. Skipping batch.")

        time.sleep(5)  # Throttle batches to respect RPM limits

    return approved_papers

def send_email(papers):
    msg = MIMEMultipart("alternative")
    msg["From"] = GMAIL_SENDER
    msg["To"] = GMAIL_RECIPIENT

    html_content = "<h2>Weekly Literature Alert</h2>"

    for p in papers:
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
        summary = p.get("llm_summary", "Summary missing.")

        html_content += f"""
        <div style="margin-bottom: 20px; padding-bottom: 20px; border-bottom: 1px solid #ccc;">
            <h3 style="margin: 0 0 5px 0;"><a href="{link}">{title}</a></h3>
            <p style="margin: 0 0 5px 0; color: #555;"><strong>Authors:</strong> {authors}</p>
            <p style="margin: 0 0 10px 0; color: #555;"><strong>Source:</strong> {journal}</p>
            <p style="margin: 0;"><strong>Summary:</strong> {summary}</p>
        </div>
        """

    n_papers = len(papers)
    print(f"[LOG] {n_papers} paper(s) passed screening.")
    msg["Subject"] = f"Pub-monitor: {n_papers} new paper{'s' if n_papers != 1 else ''}!"
    msg.attach(MIMEText(html_content, "html"))

    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
    server.send_message(msg)
    server.quit()


if __name__ == "__main__":
    search_query = get_search_query()
    including, excluding = get_criteria()

    new_papers = find_papers(search_query)
    if new_papers:
        print(f"[LOG] {len(new_papers)} candidate paper(s) pulled from database.")
        filtered_papers = batch_process_papers(new_papers, including, excluding)

        if filtered_papers:
            send_email(filtered_papers)
        else:
            print("[LOG] No relevant papers remaining after LLM screening.")
    else:
        print("[LOG] No candidate papers found in database.")
