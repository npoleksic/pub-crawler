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

Open `search_parameters.txt` in any basic text editor (like Notepad). Put each item on its own line under the bracketed headers. Lines starting with `#` are ignored, allowing you to leave notes.

| Section | Required | Purpose |
|---|---|---|
| `[DAYS_BACK]` | Yes | How many days back to search. |
| `[SEARCH]` | Yes | Terms for the database query (matched in title or abstract). Keep these broad; the LLM does the fine filtering. |
| `[CORE_TOPICS]` | Yes | Your main research topics. These weigh most in the LLM score. |
| `[RELATED_TOPICS]` | No | Topics of secondary interest. These weigh less. |
| `[PRIORITY: <name>]` | No | A ranked list, first line = highest priority. You choose the name. Add as many lists as you need, or none. |
| `[EXCLUDE]` | No | Topics you never want. A paper is dropped only if one of these is its *main* subject. |

The LLM scores each paper from 0 to 10. Papers that score 7 or higher are emailed. A paper outside your core topics, or low on a priority list, can still score high if its findings give clear insight into a core topic. All article types (primary research, reviews, methods) are treated equally unless your lists say otherwise (for example, add `review articles` to `[EXCLUDE]`).

**Example: systems neuroscience**

```text
[DAYS_BACK]
7

[SEARCH]
superior colliculus
eye movements

[CORE_TOPICS]
oculomotor and gaze control mechanisms
head and neck orienting movements

[RELATED_TOPICS]
motor control
multisensory integration

[PRIORITY: model organism]
non-human primate
human
non-primate mammals
invertebrates

[EXCLUDE]
clinical trials
```

**Example: cancer biology**

```text
[CORE_TOPICS]
mRNA stability and decay in cancer
translational control in tumor cells

[PRIORITY: evidence type]
in vivo models
patient samples
cell lines
in silico analysis
```

**Example: sports medicine**

```text
[CORE_TOPICS]
anterior cruciate ligament injury
return-to-sport outcomes

[PRIORITY: population]
elite athletes
recreational athletes
general population

[EXCLUDE]
case studies
```

When you run the tool locally, it also saves a screening log (`pub-crawler_log_<date>.csv`) in the same folder. The log lists every paper it found, with its score and a one-line reason, sorted by score. Use it to see which papers just missed the cut and to adjust your topics. No log is saved when the tool runs on GitHub.

## 4. First-Time Setup & Execution

Do not run the Python file directly.

1. Double-click **`run_crawl.bat`**.
2. The script will automatically check for and install the necessary background packages (`google-genai`, `requests`, `python-dotenv`).
3. A setup wizard will appear in the command prompt window. Paste your Gemini API key, sender email, 16-character Gmail app password, and recipient email when prompted.
4. These credentials are saved permanently to a hidden `.env` file in the same folder.
5. The script will proceed with the search, display progress logs, send the email, and close.

For all future runs, simply double-click `run_crawl.bat`. It will bypass the setup wizard and execute the search immediately.
