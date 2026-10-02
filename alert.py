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
        "pageSize": 100
    }
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    results = data.get("resultList", {}).get("result", [])
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

def ask_gemini(prompt):
    MODEL_CHAIN = [
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash-lite",
        "gemini-3.8-flash",
        "gemini-3.7-flash"
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3-flash-preview",
        "gemini-2.5-flash",
    ]
    for model in MODEL_CHAIN:
        while True:
            try:
                chat = client.chats.create(model=model)
                response = chat.send_message(prompt)
                return response.text.strip()
            except errors.APIError as api_err:
                error_msg = str(api_err).lower()
                print(f"[ERROR][ask_gemini] API ERROR: {error_msg}")
                if "per day" in error_msg or "quota" in error_msg:
                    break
                time.sleep(10)
            except Exception as err:
                print(f"[ERROR][ask_gemini]: {str(err)}")

            time.sleep(5)
    return None


def filter_by_title(papers, include_topics, exclude_topics):
    title_list = "\n".join(f"{i}) {p.get("title", "No Title")}" for i, p in enumerate(papers))

    prompt = (
        "Given the numbered list of paper titles, determine if the paper is likely to include or exclude the following topics. "
        "Papers should be cover topics relevant to neuroscience. "
        "Provide only a comma separated list of the numbers that correspond to papers that VIOLATE this criteria. If there are none, "
        "respond with exactly: NULL.\n"
        f"INCLUDES AT LEAST ONE OF: {include_topics}\nEXCLUDE: {exclude_topics}\n\nTITLES:\n{title_list}"
    )

    response = ask_gemini(prompt)
    if response is None:
        return None
    elif response == "NULL":
        return papers
    else:
        try:
            filtered_idx = [int(x) for x in response.split(",")]
            indices_to_remove = set(filtered_idx)
            papers_filt = [paper for i, paper in enumerate(papers) if i not in indices_to_remove]
            return papers_filt
        except Exception as e:
            print(f"[ERROR][filter_by_title]: {str(e)}")
            return None

def check_and_summarize_abstract(abstract, include_topics, exclude_topics):
    if not abstract:
        return "No abstract available."

    prompt = (
        "Evaluate the provided scientific abstract while considering the following criteria:\n"
        "The research must be relevant to systems or behavioral neuroscience research and focus "
        "on studying awake, behaving non-human primates, humans, or other model organisms."
        f"The research should include one of the following topics: {include_topics}\n "
        f"The research should NOT include the following topics: {exclude_topics}\n"
        "If the abstract meets this criteria, summarize it in exactly 2 sentences "
        "focusing on the main findings and their significance.\n"
        "If the abstract does not meet this criteria, output the following word exactly "
        "as it is written: REJECT\n\n"
        f"Abstract:\n{abstract}"
    )

    response = ask_gemini(prompt)
    if response is None:
        return f"Unable to generate summary. Abstract preview: {abstract[:250]}"
    elif response == "REJECT":
        return None
    else:
        return response


def send_email(papers, include_topics, exclude_topics):
        
    msg = MIMEMultipart("alternative")
    msg["From"] = GMAIL_SENDER
    msg["To"] = GMAIL_RECIPIENT
    
    html_content = "<h2>Weekly Literature Alert</h2>"
    n_papers = 0
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
        abstract = p.get("abstractText", "")
        
        summary = check_and_summarize_abstract(abstract, include_topics, exclude_topics)
        
        time.sleep(5)
        
        if summary is None:
            continue

        n_papers+=1
        html_content += f"""
        <div style="margin-bottom: 20px; padding-bottom: 20px; border-bottom: 1px solid #ccc;">
            <h3 style="margin: 0 0 5px 0;"><a href="{link}">{title}</a></h3>
            <p style="margin: 0 0 5px 0; color: #555;"><strong>Authors:</strong> {authors}</p>
            <p style="margin: 0 0 10px 0; color: #555;"><strong>Source:</strong> {journal}</p>
            <p style="margin: 0;"><strong>Summary:</strong> {summary}</p>
        </div>
        """

    print(f"[LOG] {n_papers} paper(s) with relevant abstracts")
    if n_papers > 1:
        msg["Subject"] = f"Pub-monitor: {n_papers} new papers!"
    else:
        msg["Subject"] = f"Pub-monitor: {n_papers} new paper!"

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
    if len(new_papers) > 0:
        print(f"[LOG] {len(new_papers)} candidate paper(s) found")
        filtered_papers = filter_by_title(new_papers, including, excluding)
        if filtered_papers is not None and len(filtered_papers) > 0:
            print(f"[LOG] {len(filtered_papers)} paper(s) with relevant titles")
            send_email(filtered_papers, including, excluding)
        else:
            print(f"[LOG] No relevant papers remaining")
    else:
        print(f"[LOG] No candidate papers found")
