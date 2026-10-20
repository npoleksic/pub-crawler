# pub-crawler: New Publication Alert System

pub-crawler is an automated research publication screening tool that queries the Europe PMC database and uses Google's Gemini LLM to rigorously screen, score, and summarize newly released abstracts (journals + preprints) based on user-defined criteria. Results are provided via email in an easy-to-read format.

## 1. Prerequisites

You must have Python installed on your computer. No manual package installation is required; the included batch script handles dependencies automatically.

## 2. Obtain Your Credentials

You need two free credentials to run this tool. The setup wizard will ask for these the first time you execute the script.

**A. Google Gemini API Key**

1. Go to [Google AI Studio](https://aistudio.google.com/).
2. Sign in with your Google account.
3. Click **Get API key** in the bottom left corner, then click **Create API key**.
4. Copy the generated string.

**B. Gmail App Password**
Standard password logins are disabled for automated scripts. You must generate a 16-character "App Password" to allow Python to send emails.

1. Go to your [Google Account Security page](https://myaccount.google.com/security).
2. Ensure **2-Step Verification** is turned **ON**.
3. In the search bar at the top, type **"App passwords"** and select it.
4. Enter a custom app name (e.g., "pub-crawler") and click **Create**.
5. Copy the 16-character password provided in the yellow box (do not include spaces when pasting it later).

## 3. Configure Your Search

Open `search_parameters.txt` in any basic text editor (like Notepad). Define your search terms and LLM screening criteria under the bracketed headers. Lines starting with `#` are ignored, allowing you to leave notes.

```text
[FIELD_OF_INTEREST]
Systems neuroscience

[DAYS_BACK]
7

[SEARCH]
frontal eye field
superior colliculus
active vision

[INCLUDE]
macaque
single-unit recording
microstimulation

[EXCLUDE]
fMRI
clinical trial
patients

```

## 4. First-Time Setup & Execution

Do not run the Python file directly.

1. Double-click **`run_alert.bat`**.
2. The script will automatically check for and install the necessary background packages (`google-genai`, `requests`, `python-dotenv`).
3. A setup wizard will appear in the command prompt window. Paste your Gemini API key, sender email, 16-character Gmail app password, and recipient email when prompted.
4. These credentials are saved permanently to a hidden `.env` file in the same folder.
5. The script will proceed with the search, display progress logs, send the email, and close.

For all future runs, simply double-click `run_alert.bat`. It will bypass the setup wizard and execute the search immediately.
