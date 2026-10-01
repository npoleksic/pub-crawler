import os
import requests
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from google import genai
from google.genai import errors
# from google.api_core.exceptions import ResourceExhausted
import time
import re
from difflib import SequenceMatcher
# Configuration: Targets PubMed (SRC:MED) and (SRC:PMC) and Preprints like bioRxiv (SRC:PPR)
SEARCH_QUERY = (
    '( '
    '("frontal eye field" OR "frontal eye fields") '
    'OR '
    '(("active vision" OR "natural gaze" OR "3D gaze" OR "gaze control" OR "gaze behavior" '
    'OR "eye movement" OR "eye movements" OR "saccade" OR "saccades" OR "saccadic" OR "vergence" OR "saccade-vergence" OR "disjunctive saccades" OR "disconjugate saccades" OR "binocular" '
    'OR "oculomotor" OR "superior colliculus") '
    'AND ("macaque" OR "macaques" OR "primate" OR "primates" OR "NHP" OR "marmoset" OR "electrophysiology" OR "neural" OR "cortex" OR "cerebellum")) '
    'OR '
    '(("freely moving" OR "freely behaving" OR "head-unrestrained" OR "head-free" OR "eye and head" OR "naturalistic behavior") '
    'AND ("electrophysiology" OR "neural recording" OR "single-unit" OR "telemetry" OR "macaque" OR "macaques" OR "primate" OR "primates" OR "NHP")) '
    ') '
    'AND NOT ("patients" OR "clinical trial" OR "case report") '
    'AND (SRC:MED OR SRC:PPR OR SRC:PMC)'
)
DAYS_BACK = 7

# Load environment variables (pulled from GitHub Secrets)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GMAIL_SENDER = os.environ.get("GMAIL_SENDER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
GMAIL_RECIPIENT = os.environ.get("GMAIL_RECIPIENT")

MODEL_CHAIN = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite"
]

# Initialize Gemini
client = genai.Client(api_key=GEMINI_API_KEY)

def get_recent_papers():
    today = datetime.now()
    past_date = today - timedelta(days=DAYS_BACK)
    date_str = f"[{past_date.strftime('%Y-%m-%d')} TO {today.strftime('%Y-%m-%d')}]"
    
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    params = {
        "query": f'{SEARCH_QUERY} AND FIRST_PDATE:{date_str}',
        "format": "json",
        "resultType": "core", # Required to retrieve full abstracts
        "pageSize": 50
    }
    
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    results = data.get("resultList", {}).get("result", [])
    return [p for p in results if p.get("doi")]
    
def summarize_abstract(abstract):
    if not abstract:
        return "No abstract available."
    
    prompt = f"Summarize the following scientific abstract in exactly 2 sentences, focusing on the main finding and its significance:\n\n{abstract}"

    for model_name in MODEL_CHAIN:
        model = genai.GenerativeModel(model_name)
        
        while True:
            try:
                # response = model.generate_content(prompt)
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
                return response.text.strip()
            except errors.APIError as e:
            # except ResourceExhausted as e:
                error_msg = str(e).lower()
                if "per day" in error_msg or "quota" in error_msg:
                    break 
                    
                time.sleep(10)
                
            except Exception:
                # Break to fallback model on any other unexpected API errors (e.g., 500)
                break
                    
    return f"Unable to generate summary. Abstract preview: {abstract[:250]}..."

def is_similar_title(title1, title2, threshold=0.85):
    # Strip non-alphanumeric characters and convert to lowercase
    norm1 = re.sub(r'[^a-z0-9]', '', title1.lower())
    norm2 = re.sub(r'[^a-z0-9]', '', title2.lower())
    
    # Catch cases where one title is just a slightly truncated version of the other
    if norm1 in norm2 or norm2 in norm1:
        return True
        
    # Calculate similarity ratio (0.0 to 1.0)
    return SequenceMatcher(None, norm1, norm2).ratio() > threshold

def remove_duplicate_papers(papers):
    unique_papers = []
    
    for p in papers:
        is_dup = False
        current_title = p.get("title", "")
        current_doi = p.get("doi", "")
        
        for up in unique_papers:
            # 1. Fast exact check: if both have DOIs and they match
            if current_doi and current_doi == up.get("doi"):
                is_dup = True
                break
            
            # 2. Fuzzy check: compare titles
            if current_title and up.get("title"):
                if is_similar_title(current_title, up.get("title")):
                    is_dup = True
                    # If the new duplicate is from a peer-reviewed source (MED/PMC), 
                    # replace the stored preprint version with it.
                    if p.get("source") in ["MED", "PMC"] and up.get("source") == "PPR":
                        unique_papers.remove(up)
                        unique_papers.append(p)
                    break
                    
        if not is_dup:
            unique_papers.append(p)
            
    return unique_papers

def send_email(papers):
    if not papers:
        print("No matching papers found this week.")
        return
        
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Pub-monitor: {len(papers)} new papers!"
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
        abstract = p.get("abstractText", "")
        
        summary = summarize_abstract(abstract)
        time.sleep(5)
        html_content += f"""
        <div style="margin-bottom: 20px; padding-bottom: 20px; border-bottom: 1px solid #ccc;">
            <h3 style="margin: 0 0 5px 0;"><a href="{link}">{title}</a></h3>
            <p style="margin: 0 0 5px 0; color: #555;"><strong>Authors:</strong> {authors}</p>
            <p style="margin: 0 0 10px 0; color: #555;"><strong>Source:</strong> {journal}</p>
            <p style="margin: 0;"><strong>Summary:</strong> {summary}</p>
        </div>
        """
    msg.attach(MIMEText(html_content, "html"))
    
    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
    server.send_message(msg)
    server.quit()

if __name__ == "__main__":
    all_papers = get_recent_papers()
    papers_final = remove_duplicate_papers(all_papers)
    send_email(papers_final)
