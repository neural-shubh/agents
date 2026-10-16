# Agents

A collection of AI agents I'm building to automate everyday work: pushing code to GitHub, handling email, and answering questions over local data with models that run on my own machine. Each agent lives in its own folder with its own dependencies.

> Status: personal projects, work in progress.

## Projects

| Project | What it is | Built with | Status |
|---|---|---|---|
| [github agent](github%20agent) | Pushes folders and files to GitHub, creates repos, opens issues and pull requests, with an approval step before anything changes | LangGraph, git, GitHub REST API | Working |
| [gmail agent](gmail%20agent) | Inbox triage, a digest of important emails, and drafting replies | Python, Gmail API | In progress |
| [Local-Ai-agent](Local-Ai-agent) | A question-answering agent over a CSV of support tickets that runs fully locally | LangChain, Ollama, Chroma | Experimental |
| [sysagent](sysagent) | Details coming soon | | In progress |

## The projects in a bit more detail

### github agent

A [LangGraph](https://langchain-ai.github.io/langgraph/) agent controlled from the command line. Tell it what you want in plain English:

```
python graph.py "push 'C:\path\to\project' to repo my-project"
python graph.py "create an issue in repo my-project titled 'Add tests'"
```

Before it pushes, it scans the files, skips virtual environments and secrets, audits the project (missing README, leaked keys, hardcoded paths), and pauses for your approval. New repos are created on request, and updates to existing repos go through a pull request instead of straight to `main`. Full details are in [its own README](github%20agent/README.md).

### gmail agent

Aims to take care of the inbox: sorting what matters from what doesn't, sending a nightly digest of the important mail, and drafting or sending replies. It needs your own Google API credentials, which stay on your machine and are never committed.

### Local-Ai-agent

A small retrieval-augmented agent. It loads a CSV of support tickets into a Chroma vector store and answers questions about them with a local model served by [Ollama](https://ollama.com), so no data leaves the machine.

### sysagent

Details coming soon.

## Getting started

Each project is independent, so set up the one you want to try:

1. Clone the repo and open the project folder.
2. Create and activate a virtual environment inside it:

   ```
   python -m venv venv
   .\venv\Scripts\Activate.ps1      # Windows PowerShell
   source venv/bin/activate         # macOS / Linux
   ```

3. Install that project's dependencies from its `requirements.txt`.
4. Create the project's `.env` file with the keys it needs (see its README).
5. For the local-model projects, install Ollama and pull a model first, for example `ollama pull llama3.2`.

Requirements: Python 3.10 or newer. The github agent also needs `git` on your PATH.

## Secrets

No API keys, tokens, or credential files are committed to this repo. Each project reads them from a local `.env` file or its own credentials file, and both are listed in `.gitignore`. If you fork this, create your own.

## Repo layout

```
agents/
  github agent/     GitHub automation (LangGraph)
  gmail agent/      Email triage and replies
  Local-Ai-agent/   Local question answering over support tickets
  sysagent/         Work in progress
```

## What's next

More agents are planned (LinkedIn and shopping), along with a layer that runs them together.

## Author

Shubh Sharma ([@neural-shubh](https://github.com/neural-shubh))
