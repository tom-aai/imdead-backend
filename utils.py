import os
import logging

logger = logging.getLogger(__name__)

def load_prompt(filename):
    """Load a prompt from a text file."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    prompt_path = os.path.join(script_dir, 'prompts', filename)
    
    try:
        with open(prompt_path, 'r', encoding='utf-8') as file:
            return file.read().strip()
    except FileNotFoundError as e:
        logger.error(f"Error loading prompt file {filename}: {e}")
        return ""

