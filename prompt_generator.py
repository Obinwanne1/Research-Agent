import os
import re
import subprocess
from datetime import datetime
import models
from config import Config


def call_claude(prompt):
    result = subprocess.run(
        ["claude", "-p"],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=Config.CLAUDE_TIMEOUT
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI error: {result.stderr[:300]}")
    output = result.stdout.strip()
    try:
        output = output.encode('cp1252').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return output


def make_slug(prefix, topic):
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug_part = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:50]
    return f"{date_str}_{prefix}_{slug_part}"


PROMPT_GEN_META = """You are a world-class prompt engineer. Your outputs are renowned for being token-efficient, action-first, and measurably outperforming generic AI outputs.

Task: Write a professional Claude Code prompt for:
{description}

Hard rules:
- Max 200 tokens total in the generated prompt
- Every word earns its place — cut all filler
- Action-first structure (verb at the start)
- No hedging, no pleasantries, no disclaimers
- Include concrete constraints and output format

Output this exact structure (markdown):

## Generated Prompt
```
[the prompt — max 200 tokens, ready to copy-paste]
```

## Token Estimate
~N tokens

## Why This Outperforms GPT-4 / Gemini Output
| Dimension | This Prompt | Typical GPT-4/Gemini Output |
|-----------|-------------|------------------------------|
| Token count | ~N | 400-600 (bloated) |
| Structure | Action-first | Polite preamble + restates goal |
| Specificity | Exact constraints | Vague guidance |
| Usability | Copy-paste ready | Requires editing |

**Key advantage:** [1-2 sentence specific reason]

## What a Generic AI Would Produce
```
[show the typical bloated/vague version — so user sees the contrast]
```"""


SKILL_GEN_META = """You are a Claude Code skill architect. Your skills are production-ready, trigger correctly, and execute reliably.

Task: Write a complete Claude Code skill (.md file) for:
{description}

Hard rules:
- Valid frontmatter: name (kebab-case), description (one line, ≤120 chars)
- Clear TRIGGER section: exact phrases that invoke the skill
- Numbered STEPS: concrete, executable, no vague instructions
- Handle the 1-2 most common edge cases
- Total skill ≤ 500 tokens

Output this exact structure:

## Generated Skill File
```markdown
---
name: skill-name-here
description: One-line description of what this skill does
---

[Full skill content here]
```

## Why This Beats GPT-4 / Gemini Skill Output
| Dimension | This Skill | Typical GPT-4/Gemini Output |
|-----------|------------|-----------------------------|
| Trigger precision | Exact phrases | Vague "when user asks about X" |
| Steps | Numbered, atomic | Paragraphs of prose |
| Edge cases | Handled inline | Missing or generic |
| Token efficiency | ≤500 tokens | 800-1200 (bloated) |

**Key advantage:** [1-2 sentence specific reason]"""


def run_prompt_gen_task(payload, user_id, job_id):
    description = payload["topic"]
    try:
        models.update_job(job_id, status="running", message="Generating prompt...")
        prompt = PROMPT_GEN_META.format(description=description)
        output = call_claude(prompt)

        models.update_job(job_id, message="Saving...")
        slug = make_slug("prompt", description)
        user_dir = os.path.join(Config.RESEARCH_BASE_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        file_path = os.path.join(user_dir, f"{slug}.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"# Prompt: {description}\n\n")
            f.write(output)

        word_count = len(output.split())
        models.create_article(
            user_id=user_id,
            job_id=job_id,
            title=f"[Prompt] {description}",
            slug=slug,
            file_path=os.path.join(str(user_id), f"{slug}.md"),
            topic=description,
            word_count=word_count,
        )
        models.update_job(job_id, status="done", message="Prompt generated!", result_slug=slug)

    except subprocess.TimeoutExpired:
        models.update_job(job_id, status="error", message="Claude CLI timed out.")
    except Exception as e:
        models.update_job(job_id, status="error", message=f"Error: {str(e)[:200]}")


def run_skill_gen_task(payload, user_id, job_id):
    description = payload["topic"]
    try:
        models.update_job(job_id, status="running", message="Generating skill...")
        prompt = SKILL_GEN_META.format(description=description)
        output = call_claude(prompt)

        models.update_job(job_id, message="Saving...")
        slug = make_slug("skill", description)
        user_dir = os.path.join(Config.RESEARCH_BASE_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        file_path = os.path.join(user_dir, f"{slug}.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"# Skill: {description}\n\n")
            f.write(output)

        word_count = len(output.split())
        models.create_article(
            user_id=user_id,
            job_id=job_id,
            title=f"[Skill] {description}",
            slug=slug,
            file_path=os.path.join(str(user_id), f"{slug}.md"),
            topic=description,
            word_count=word_count,
        )
        models.update_job(job_id, status="done", message="Skill generated!", result_slug=slug)

    except subprocess.TimeoutExpired:
        models.update_job(job_id, status="error", message="Claude CLI timed out.")
    except Exception as e:
        models.update_job(job_id, status="error", message=f"Error: {str(e)[:200]}")
