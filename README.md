# pub-crawler: New Publication Alert System

pub-crawler finds newly released research papers (journal articles and preprints) and emails you the ones that match your research interests. It searches Europe PMC and, when needed, OpenAlex. Google's Gemini LLM screens each paper against the topics you list, and the script scores the papers with fixed rules. Each emailed paper has a short summary and a link.

**How a run works**

1. Your search terms query Europe PMC (life sciences and biomedicine). If you have an OpenAlex key, the script checks whether Europe PMC finds most of the papers that OpenAlex finds for your terms. If Europe PMC misses 10% or more, OpenAlex (all fields) is searched too.
2. Duplicates, papers without a DOI, and repository entries (datasets, code, posters on figshare or Zenodo) are removed.
3. Gemini reads the title and abstract of each paper, in batches of 20, and reports which of your listed items the paper matches. The script calculates a score from 0 to 10.
4. Papers that score close to the cutoff are checked a second time, and the email summaries are written.
5. Papers that score 7 or higher are emailed to you. If no paper passes, no email is sent.

## 1. Prerequisites

You need Python 3 (the GitHub workflow uses Python 3.11). You do not have to install packages yourself. The launch scripts do this for you.

## 2. Obtain Your Credentials

The setup wizard asks for these the first time you run the tool.

**A. Google Gemini API Key (required)**

1. Go to [Google AI Studio](https://aistudio.google.com/).
2. Sign in with your Google account.
3. Click **Get API key** in the bottom left corner, then click **Create API key**.
4. Copy the generated string.

**B. Gmail App Password (required)**

Gmail does not accept your normal password from scripts. You must make a 16-character "App Password".

1. Go to your [Google Account Security page](https://myaccount.google.com/security).
2. Make sure that **2-Step Verification** is **ON**.
3. In the search bar at the top, type **"App passwords"** and select it.
4. Enter an app name (for example, "pub-crawler") and click **Create**.
5. Copy the 16-character password. Do not include the spaces when you paste it.

**C. OpenAlex API Key (optional, recommended outside the life sciences)**

Europe PMC covers the life sciences and biomedicine. For other fields (for example physics, engineering, computer science, economics or the social sciences), the tool also needs OpenAlex. Without a key, the tool uses Europe PMC only.

1. Make a free account at [openalex.org](https://openalex.org/).
2. Copy your key from [openalex.org/settings/api](https://openalex.org/settings/api).

The free key gives a daily budget of $1, which is about 1,000 search calls. A typical run uses a few to a few tens of calls. When the budget is used up, OpenAlex refuses further calls for that day. It does not charge you.

## 3. Configure Your Search

Edit `search_parameters.txt` in a plain text editor (for example, Notepad or TextEdit). If you are a new user, start with `search_parameters_template.txt`: copy its contents into `search_parameters.txt`, then replace the example items with your own.

Put each item on its own line under the bracketed headers. Lines starting with `#` are ignored. Each line is one item: commas and "or" inside a line do not split it. The comments in the file explain how your settings affect the number of papers and Gemini requests.

| Section | Required | Purpose |
|---|---|---|
| `[DAYS_BACK]` | Yes | How many days back to search. Use the interval between your runs (for example, 7 for a weekly run). |
| `[SEARCH]` | Yes | Terms for the database query, matched as exact phrases in the title or abstract. The search only needs to find every relevant paper; the screening removes the rest. |
| `[CORE_TOPICS]` | Yes | Your main research topics. These have the most effect on the score. |
| `[RELATED_TOPICS]` | No | Topics of secondary interest. These have less effect on the score. |
| `[PRIORITY: <name>]` | No | A ranked list, first line = highest priority. You choose the name. Add as many lists as you need, or none. |
| `[EXCLUDE]` | No | Topics you do not want. A paper scores 0 if one of these applies to its subject, its study population or model, its methods, or its study design. A passing mention does not count. |

Write topics as specific phrases. Qualifiers count: "neural mechanisms of eye movements" does not match a behavioral study of eye movements. Keep `[EXCLUDE]` short: each item adds a chance of a wrong exclusion. Add only topics that you see often in your results.

**How papers are scored**

Gemini does not rate the papers. It only reports facts: which core and related topics each paper matches (as its main subject or as a substantial secondary part), the rank of the paper on each priority list, and which excluded items apply. The script then calculates the score with fixed rules:

* The score is 0 if an excluded item applies, if the entry is not a paper (for example, a poster, a dataset or a thesis), or if the paper is in a journal that is not known for research on your topics. Preprints are never scored 0 for their venue.
* **Topic part (40%):** the best topic match counts. A core topic as the main subject counts most. A core topic as a secondary part, or a related topic as the main subject, counts less. Each additional matched topic adds a small bonus.
* **Priority part (60%):** rank 1 gives full points, and lower ranks give fewer points. A paper that matches no item on a list gets no points for that list. With more than one list, the lists are averaged. If you have no priority lists, the score uses the topic part only.

Papers that score 6 or higher are checked a second time, in a separate and stricter pass. This pass also writes the email summaries. Papers that score 7 or higher after this check are emailed. All article types (primary research, reviews, methods) are treated equally unless your lists say otherwise (for example, add `review articles` to `[EXCLUDE]`). The weights, the cutoff and the other scoring settings are constants at the top of `crawl.py`.

**Example: systems neuroscience**

```text
[DAYS_BACK]
7

[SEARCH]
superior colliculus
eye movements

[CORE_TOPICS]
neural mechanisms of oculomotor and gaze control
neural mechanisms of head and neck orienting movements

[RELATED_TOPICS]
motor control
multisensory integration

[PRIORITY: model organism]
non-human primates
humans
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

**Screening log**

When you run the tool on your computer, it saves a screening log (`pub-crawler_log_<date>.csv`) in the same folder. The log lists every paper that was found, sorted by score. For each paper, it shows a one-line reason, the items Gemini matched (topics, priority rank, excluded items) and the changes made by the second check. Papers without an abstract are not screened, but they are in the log. Use the log to find papers that just missed the cutoff or were excluded by mistake, and adjust your lists. No log is saved when the tool runs on GitHub.

## 4. Run on Your Computer

Do not run `crawl.py` directly. Use the launch script for your system. It installs the necessary packages (`google-genai`, `requests`, `python-dotenv`) and then starts the tool.

**Windows:** double-click **`run_crawl.bat`**. Python must be on your PATH (select **Add python.exe to PATH** when you install Python).

**macOS or Linux:** open a terminal in the pub-crawler folder and type:

```bash
bash run_crawl.sh
```

The first time, the script makes a virtual environment in a `.venv` folder and installs the packages there. This prevents the "externally-managed-environment" error that pip shows on Homebrew Python and on recent Debian and Ubuntu versions. On Debian or Ubuntu, you may have to install `python3-venv` first (`sudo apt install python3-venv`).

**First run:**

1. A setup wizard appears. Paste your Gemini API key, sender Gmail address, 16-character app password and recipient email address when prompted.
2. The wizard then asks for your OpenAlex key. Press Enter to skip it.
3. The credentials are saved in a hidden `.env` file in the same folder. To change a credential later, edit or delete this file.
4. The tool searches, screens the papers, shows progress and sends the email.

For all later runs, start the launch script again. The wizard is not shown again.

## 5. Run on GitHub Actions

You can also run the tool on GitHub's servers, so that your computer does not have to be on.

1. Fork this repository to your GitHub account.
2. In your fork, edit `search_parameters.txt` and commit the change. The GitHub run uses the file in the repository.
3. Go to **Settings → Secrets and variables → Actions** and click **New repository secret**. Add these secrets:

   | Secret | Required |
   |---|---|
   | `GEMINI_API_KEY` | Yes |
   | `GMAIL_SENDER` | Yes |
   | `GMAIL_APP_PASSWORD` | Yes |
   | `GMAIL_RECIPIENT` | Yes |
   | `OPENALEX_API_KEY` | No |

4. Open the **Actions** tab. GitHub turns off workflows in new forks, so click the button to enable them.
5. Select the **start crawling** workflow and click **Run workflow**.

The GitHub run does not show the setup wizard and does not save a log. If a required secret is missing, the run stops with an error.
