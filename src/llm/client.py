"""
LLM client for local inference via Ollama.
Handles communication with the Ollama API running on localhost.
"""
from __future__ import annotations
import json
import urllib.request
import urllib.error


DEFAULT_MODEL = "qwen2.5:14b"
DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaClient:
    """Client for Ollama local LLM inference."""

    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL):
        self.model = model
        self.base_url = base_url

    def generate(self, prompt: str, system: str = "", temperature: float = 0.1) -> str:
        """Send a prompt to Ollama and return the response text.

        Args:
            prompt: The user prompt
            system: Optional system prompt
            temperature: Lower = more deterministic (good for extraction)

        Returns:
            The model's response as a string
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "temperature": temperature,
            "stream": False,
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result.get("response", "")
        except urllib.error.URLError as e:
            raise ConnectionError(
                f"Não foi possível conectar ao Ollama em {self.base_url}. "
                f"Verifique se o Ollama está rodando. Erro: {e}"
            )

    def extract_json(self, prompt: str, system: str = "", temperature: float = 0.1) -> dict | list | None:
        """Send a prompt and parse the response as JSON.

        Handles common issues: markdown code fences, trailing text after JSON, etc.

        Returns:
            Parsed JSON as dict/list, or None if parsing fails
        """
        raw = self.generate(prompt, system, temperature)
        return self._parse_json_response(raw)

    def _parse_json_response(self, raw: str) -> dict | list | None:
        """Try to extract valid JSON from a model response.

        Models often wrap JSON in ```json ... ``` or add explanation text.
        This handles those cases.
        """
        text = raw.strip()

        # Remove markdown code fences
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start) if "```" in text[start:] else len(text)
            text = text[start:end].strip()
        elif "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start) if "```" in text[start:] else len(text)
            text = text[start:end].strip()

        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON object or array in the text
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start = text.find(start_char)
            if start == -1:
                continue
            # Find matching closing bracket
            depth = 0
            for i in range(start, len(text)):
                if text[i] == start_char:
                    depth += 1
                elif text[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break
        return None

    def is_available(self) -> bool:
        """Check if Ollama is running and the model is available."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name", "") for m in data.get("models", [])]
                return any(self.model in m for m in models)
        except (urllib.error.URLError, Exception):
            return False