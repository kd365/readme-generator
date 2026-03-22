# AI README Generator

An AI-powered tool that generates professional README files for any public GitHub repository. Built with AWS Bedrock Agents, Terraform, and a local Python CLI.

Point it at a repo, and it clones, analyzes, and produces a structured README with project summary, installation instructions, usage examples, and security notes.

## How It Works

```
python3 generate_readme.py
        │
        ├─ 1. Clones the repo locally (with security checks)
        ├─ 2. Scans file structure + reads key file contents
        ├─ 3. Sends data to 3 Bedrock agents (Summarizer, Installation, Usage)
        ├─ 4. Compiler agent assembles the final Markdown document
        ├─ 5. Local security scan appends findings
        ├─ 6. Writes GENERATED-README.md to the cloned project
        └─ 7. Optionally pushes to your GitHub (fork, new repo, or existing)
```

### Agent Pipeline

| Agent | Role |
|---|---|
| **Project Summarizer** | Infers purpose, architecture, and tech stack from files |
| **Installation Guide** | Writes Getting Started section from dependency files |
| **Usage Examples** | Writes Usage section focused on runtime commands |
| **Final Compiler** | Assembles sections, deduplicates content, formats Markdown |

All agents are deployed as AWS Bedrock Agents via Terraform and invoked through the `bedrock-agent-runtime` API.

## Getting Started

### Prerequisites

- Python 3.9+
- AWS account with Bedrock access (Claude Sonnet 4)
- AWS CLI configured (`aws configure`)
- GitHub CLI authenticated (`gh auth login`)
- Terraform installed

### Setup

```bash
cd readme-generator

# Install Python dependencies
pip install -r requirements.txt

# Deploy infrastructure (agents, IAM roles, S3, Lambda)
terraform init
terraform apply
```

### Run

```bash
python3 generate_readme.py
```

The interactive menu offers:

```
  1) Generate README from a URL
  2) Re-run on last scanned repo
  3) Load existing project
  h) Session history
  q) Quit
```

## Security Features

The CLI performs security checks on every clone:

| Check | What It Does |
|---|---|
| **Git hooks disabled** | `core.hooksPath=/dev/null` prevents malicious post-checkout scripts |
| **Symlink detection** | Blocks symlinks pointing outside the repo directory |
| **Path traversal** | Skips files with `..` in their path |
| **Clone size check** | Warns on repos larger than 500MB |
| **IDE config detection** | Warns about `.vscode/tasks.json` and `.devcontainer/` auto-run files |
| **Secret scanning** | Finds hardcoded API keys, credentials, and sensitive files |

A security report is displayed after each clone:

```
  Clone Security Report:
    PASS Git hooks disabled (core.hooksPath=/dev/null)
    PASS No path traversal attempts detected
    PASS No malicious symlinks detected
    PASS No dangerous IDE auto-run configs found
    PASS Clone size: 45.2MB
    All checks passed.
```

## Infrastructure

All AWS resources are managed with Terraform using reusable modules:

- **S3 bucket** — stores outputs (lab-follow-along branch) and Terraform state
- **IAM roles** — separate roles for Lambda, Bedrock agents, orchestrator, and GitHub Actions
- **Bedrock Agents** — 5 agents with `-KH` suffix (configurable via `name_suffix` variable)
- **Lambda functions** — Repo Scanner (with git layer) and Orchestrator
- **CI/CD** — GitHub Actions with OIDC authentication (no stored AWS keys)

## Differences from lab-follow-along Branch

This assignment branch makes significant improvements over the original lab implementation:

### Architecture

| | lab-follow-along | assignment (this branch) |
|---|---|---|
| **Trigger** | Upload file to S3 `inputs/` prefix | Run `python3 generate_readme.py` locally |
| **Orchestration** | Lambda function calls agents sequentially | Local Python CLI with TUI, spinners, retry logic |
| **Clone location** | Lambda `/tmp` (ephemeral, 10GB max, wiped after each run) | Local `./projects/` directory (persistent, reusable) |
| **File reading** | Lambda reads files, sends to agents via narrative summary | CLI reads files from disk, sends structured JSON directly to agents |
| **Output** | `s3://bucket/outputs/repo/README.md` | `./projects/repo/GENERATED-README.md` |
| **GitHub integration** | None | Fork, create new repo, or push to existing |

### Prompt Engineering

| | lab-follow-along | assignment |
|---|---|---|
| **Installation agent** | Generic: lists common ecosystems | Strict: only includes ecosystems confirmed by actual dependency files |
| **Usage agent** | Often repeated install steps | Focused on runtime usage only, explicitly told not to duplicate |
| **Compiler agent** | Simple assembly | Deduplicates overlapping content between sections |
| **Summarizer agent** | Could hedge ("appears to be") | Writes as project author, no uncertain language |

### Output Quality

| | lab-follow-along | assignment |
|---|---|---|
| **Post-processing** | None | Validates install commands against repo files, strips preamble, removes orphaned headers |
| **Large repo handling** | No special handling | 25KB input cap with priority file preservation (dependency files always included first) |
| **Security scan** | Not included | Filters test files/docs, caps at 10 findings, reports clone safety |
| **Original README** | Overwritten | Never touched (writes to `GENERATED-README.md`) |

### Reliability

| | lab-follow-along | assignment |
|---|---|---|
| **Throttling** | 3 parallel agent calls often hit rate limits | Sequential calls with 3s delays between agents |
| **Retries** | None | 3 retries with exponential backoff on throttling errors |
| **Error handling** | Errors pass through to final output | Errors replaced with "This section could not be generated" |
| **Clone safety** | No checks | Full security audit (hooks, symlinks, traversal, size, IDE configs) |

## Project Structure

```
readme-generator/
├── main.tf                          # All Terraform resources
├── backend.tf                       # Remote state config (S3 + DynamoDB)
├── generate_readme.py               # Local CLI (assignment branch)
├── requirements.txt                 # Python dependencies
├── repo_scanner_schema.json         # OpenAPI schema for scanner action group
├── .github/workflows/deploy.yml     # CI/CD pipeline
├── modules/
│   ├── s3/                          # Reusable S3 bucket module
│   ├── iam/                         # Reusable IAM role module
│   └── bedrock_agent/               # Reusable Bedrock Agent module
├── src/
│   ├── repo_scanner/                # Lambda: clones repos, lists files
│   └── orchestrator/                # Lambda: orchestrator (lab branch)
└── projects/                        # Cloned repos (gitignored)
```
