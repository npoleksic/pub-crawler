import os
import requests
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
import time
# Configuration: Targets PubMed (SRC:MED) and Preprints like bioRxiv (SRC:PPR)
SEARCH_QUERY = (
    '( '
    '("frontal eye field" OR "frontal eye fields") '
    'OR '
    '(("active vision" OR "natural gaze" OR "3D gaze" OR "gaze control" OR "gaze behavior" OR "eye movement" OR "eye movements" OR "saccade" OR "saccades" OR "saccadic" OR "oculomotor" OR "superior colliculus") '
    'AND ("macaque" OR "macaques" OR "primate" OR "primates" OR "NHP" OR "marmoset" OR "electrophysiology" OR "neural" OR "cortex" OR "cerebellum")) '
    'OR '
    '(("freely moving" OR "freely behaving" OR "head-unrestrained" OR "head-free" OR "eye and head" OR "naturalistic behavior") '
    'AND ("electrophysiology" OR "neural recording" OR "single-unit" OR "telemetry" OR "macaque" OR "macaques" OR "primate" OR "primates" OR "NHP")) '
    ') '
    'AND NOT ("patients" OR "clinical trial" OR "case report") '
    'AND (SRC:MED OR SRC:PPR OR SRC:PMC)'
)
DAYS_BACK = 30

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
genai.configure(api_key=GEMINI_API_KEY)

def get_recent_papers():
    today = datetime.now()
    past_date = today - timedelta(days=DAYS_BACK)
    date_str = f"[{past_date.strftime('%Y-%m-%d')} TO {today.strftime('%Y-%m-%d')}]"
    
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    params = {
        "query": f'{SEARCH_QUERY} AND FIRST_PDATE:{date_str}',
        "format": "json",
        "resultType": "core", # Required to retrieve full abstracts
        "pageSize": 3
    }
    
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    
    return data.get("resultList", {}).get("result", [])

def summarize_abstract(abstract):
    if not abstract:
        return "No abstract available."
    
    prompt = f"Summarize the following scientific abstract in exactly 2 sentences, focusing on the main finding and its significance:\n\n{abstract}"

    for model_name in MODEL_CHAIN:
        model = genai.GenerativeModel(model_name)
        
        while True:
            try:
                response = model.generate_content(prompt)
                return response.text.strip()
                
            except ResourceExhausted as e:
                error_msg = str(e).lower()
                if "per day" in error_msg:
                    break 
                    
                time.sleep(10)
                
            except Exception:
                # Break to fallback model on any other unexpected API errors (e.g., 500)
                break
                    
    return f"Unable to generate summary. Abstract preview: {abstract[:250]}..."

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
        journal = p.get("journalTitle", "Preprint / Unknown Source")
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
    papers = get_recent_papers()
    send_email(papers)
