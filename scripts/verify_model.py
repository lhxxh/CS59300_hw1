"""Make a tiny real model call using local .env; never print credentials."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import dotenv_values
from research_assistant.llm import LLM, LLMError


def main():
    values = {**dotenv_values(Path(__file__).resolve().parent.parent / '.env'), **os.environ}
    config = {name: values.get(name, default) for name, default in {
        'LLM_API_STYLE':'openai', 'LLM_BASE_URL':'https://api.openai.com/v1',
        'LLM_MODEL':'gpt-4.1-mini', 'LLM_API_KEY':'', 'LLM_MAX_OUTPUT_TOKENS':'12000',
        'LLM_REASONING_EFFORT':'',
    }.items()}
    model = LLM(config)
    print('Checking model:', model.model, '| API style:', model.style, flush=True)
    result = model.complete('Reply exactly: Folio connection OK.', system='You are testing an API connection. Reply briefly.')
    print('Model reply:', result[:200].replace(model.key, '[redacted]') if model.key else result[:200], flush=True)


if __name__ == '__main__':
    try: main()
    except LLMError as exc:
        print('Connection check failed:', str(exc), file=sys.stderr)
        sys.exit(1)
